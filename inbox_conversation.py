"""Bounded real-AI test replies for one privately configured owner email thread.

Does not enable prospect sending or connect the public demo to real data.
Interrupted/ambiguous sends stop for review, never automatically resend.
"""
import base64
import json
import os
import re
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.utils import parseaddr, formataddr
from uuid import UUID

import requests
import sdr_worker as worker
from conversation_engine import advance, assess, new_conversation, STOP
from inbox_check import valid_email

TABLE = "sdr_test_conversations"


def read_config(check_id):
    rows = worker.sb("GET", TABLE, params={"check_id": f"eq.{check_id}", "limit": "1"}) or []
    return rows[0] if rows else None


def allowed(config, settings):
    if not config:
        return False
    return (config.get("enabled") is True and config.get("owner") == "sdr"
            and config.get("status") in {"active", "processing"}
            and config["replies_sent"] < config["reply_cap"]
            and datetime.fromisoformat(config["expires_at"].replace("Z", "+00:00")) > datetime.now(timezone.utc)
            and settings.get("sending_enabled") is False and settings.get("kill_switch") is False)


def latest_text(message):
    text = worker.extract_text(message.get("payload", {})).strip()
    # Trim common quoted history before sending the current reply to the model.
    text = re.split(r"(?im)^On .{0,500}wrote:\s*$|^-{2,}\s*Original Message\s*-{2,}$", text, maxsplit=1)[0]
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))[:6000].strip()


def customer_messages(thread, check):
    matches = []
    for msg in thread.get("messages", []):
        headers = msg.get("payload", {}).get("headers", [])
        if (parseaddr(worker.header_value(headers, "From"))[1].lower() == check["recipient"].lower()
                and parseaddr(worker.header_value(headers, "To"))[1].lower() == check["expected_sender"].lower()):
            matches.append(msg)
    return sorted(matches, key=lambda m: int(m.get("internalDate", 0)))


def model_reader(prompt):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("AI credentials missing")
    model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
    response = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key}, json={"contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}, timeout=60)
    if not response.ok:
        raise RuntimeError(f"AI HTTP {response.status_code}")
    return "".join(part.get("text", "") for candidate in response.json().get("candidates", [])
                   for part in candidate.get("content", {}).get("parts", []))


def reply_payload(check, message, body):
    headers = message.get("payload", {}).get("headers", [])
    parent_id = worker.header_value(headers, "Message-ID")
    subject = worker.header_value(headers, "Subject")
    refs = worker.header_value(headers, "References")
    if not re.fullmatch(r"<[^<>\s]+>", parent_id) or any(c in subject + refs for c in "\r\n"):
        raise ValueError("Missing or unsafe reply threading headers")
    mime = MIMEText(body, "plain", "utf-8")
    mime["To"] = check["recipient"]
    mime["From"] = formataddr((worker.FROM_NAME, check["expected_sender"]))
    mime["Subject"] = subject
    mime["In-Reply-To"] = parent_id
    mime["References"] = (refs + " " + parent_id).strip()
    mime["Message-ID"] = f"<leadengine-test-{check['id']}-{message['id']}@gmail.com>"
    return {"threadId": check["gmail_thread_id"], "raw": base64.urlsafe_b64encode(mime.as_bytes()).decode().rstrip("=")}


def run_test_conversation(check_id, token=None):
    UUID(check_id)
    config = read_config(check_id)
    if not allowed(config, worker.load_settings()) or config["status"] != "active":
        return
    checks = worker.sb("GET", "sdr_inbox_checks", params={"id": f"eq.{check_id}", "status": "eq.sent", "limit": "1"}) or []
    if not checks:
        return
    check = checks[0]
    if not valid_email(check["recipient"]) or not valid_email(check["expected_sender"]) or check["recipient"].lower() == check["expected_sender"].lower():
        raise ValueError("Invalid test inbox configuration")
    token = token or worker.gmail_token()
    profile = worker.gmail_api(token, "GET", "profile")
    if str(profile.get("emailAddress") or "").lower() != check["expected_sender"].lower():
        raise RuntimeError("Wrong sender account")
    thread = worker.gmail_api(token, "GET", "threads/" + check["gmail_thread_id"], params={"format": "full"})
    state = config.get("state") or new_conversation()
    pending = [m for m in customer_messages(thread, check) if m["id"] not in state["processed"]]
    if not pending:
        return
    newest = pending[-1]
    # A manual sender reply after the customer message means the owner took over.
    later = [m for m in thread.get("messages", []) if int(m.get("internalDate", 0)) > int(newest.get("internalDate", 0))]
    if later:
        worker.sb("PATCH", TABLE, params={"check_id": f"eq.{check_id}", "version": f"eq.{config['version']}"},
                  body={"owner": "human", "enabled": False, "updated_at": worker.now()})
        return
    version = config["version"] + 1
    claimed = worker.sb("PATCH", TABLE, params={"check_id": f"eq.{check_id}", "version": f"eq.{config['version']}",
        "status": "eq.active", "enabled": "eq.true", "owner": "eq.sdr"},
        body={"status": "processing", "version": version, "updated_at": worker.now()}, prefer="return=representation") or []
    if not claimed:
        return
    def save(body, status=None):
        params = {"check_id": f"eq.{check_id}", "version": f"eq.{version}"}
        if status:
            params["status"] = "eq." + status
        return worker.sb("PATCH", TABLE, params=params, body={**body, "updated_at": worker.now()}, prefer="return=representation") or []
    try:
        message = "\n".join(latest_text(m) for m in pending)
        assessment = {} if STOP.search(message) else assess(message, state["history"], model_reader)
        next_state, decision = advance(state, newest["id"], message, assessment, config["offer"])
        next_state["processed"] = list(dict.fromkeys(next_state["processed"] + [m["id"] for m in pending]))
        if decision["action"] != "draft":
            save({"state": next_state, "last_decision": decision, "enabled": False,
                  "owner": "human" if decision["action"] == "handoff" else "sdr",
                  "status": "stopped" if decision["action"] == "stop" else "review"}, "processing")
            print("TEST_CONVERSATION " + json.dumps({"action": decision["action"], "sent": False}))
            return
        payload = reply_payload(check, newest, decision["body"])
        fresh = read_config(check_id)
        if not allowed(fresh, worker.load_settings()) or fresh["version"] != version:
            save({"status": "review", "enabled": False}, "processing")
            return
        # Do not send a stale draft if another reply arrived while AI was thinking.
        latest = worker.gmail_api(token, "GET", "threads/" + check["gmail_thread_id"], params={"format": "minimal"})
        if {m["id"] for m in latest.get("messages", [])} != {m["id"] for m in thread.get("messages", [])}:
            save({"status": "active"}, "processing")
            return
        reserved = worker.sb("PATCH", TABLE, params={"check_id": f"eq.{check_id}", "version": f"eq.{version}",
            "status": "eq.processing", "enabled": "eq.true", "owner": "eq.sdr", "expires_at": f"gt.{worker.now()}"},
            body={"status": "sending", "state": next_state, "last_decision": decision}, prefer="return=representation") or []
        if not reserved:
            return
        sent = worker.gmail_api(token, "POST", "messages/send", body=payload)
        if not sent.get("id") or sent.get("threadId") != check["gmail_thread_id"]:
            raise RuntimeError("Reply threading not confirmed")
        count = config["replies_sent"] + 1
        recorded = save({"status": "active", "replies_sent": count, "last_output_id": sent["id"],
                         "enabled": count < config["reply_cap"]}, "sending")
        if not recorded:
            raise RuntimeError("Send completed but recording needs review")
        print("TEST_CONVERSATION " + json.dumps({"action": "replied", "thread_matches": True, "replies_sent": count}))
    except Exception:
        save({"status": "review", "enabled": False})
        raise RuntimeError("Test conversation needs review; no automatic resend") from None


if __name__ == "__main__":
    run_test_conversation(os.environ["INBOX_CHECK_ID"])
