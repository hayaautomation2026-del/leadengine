"""Send one explicitly queued owner inbox check; never run the prospect worker.

Private recipients and message content stay in the database, not the public repo.
Atomic queued->claimed update permits one send attempt only. A timeout or crash
requires manual review; reruns never blindly resend an uncertain message.
"""
import os
import re
import json
import sys
from email.utils import parseaddr
from uuid import UUID

import sdr_worker as worker


def valid_email(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", value) is not None


def run_check(check_id):
    UUID(check_id)
    rows = worker.sb("PATCH", "sdr_inbox_checks",
        params={"id": f"eq.{check_id}", "status": "eq.queued"},
        body={"status": "claimed", "claimed_at": worker.now()},
        prefer="return=representation") or []
    if not rows:
        print("No queued check. Nothing sent.")
        return
    row = rows[0]
    try:
        if len(rows) != 1 or not valid_email(row["recipient"]) or not valid_email(row["expected_sender"]):
            raise ValueError("Invalid inbox check")
        if row["recipient"].lower() == row["expected_sender"].lower():
            raise ValueError("Use a separate recipient inbox")
        if not row["subject"].strip() or not row["body_text"].strip() or any(x in row["subject"] for x in "\r\n"):
            raise ValueError("Invalid message")
        # This narrow check is allowed while prospect sending is OFF; never
        # switch it on. Respect the owner's emergency stop as well.
        settings = worker.load_settings()
        if settings.get("sending_enabled") is not False or settings.get("kill_switch") is not False:
            raise RuntimeError("Inbox check requires prospect sending OFF and emergency stop disengaged")
        token = worker.gmail_token()
        profile = worker.gmail_api(token, "GET", "profile")
        sender = str(profile.get("emailAddress") or "").lower()
        if sender != row["expected_sender"].lower() or sender != worker.GMAIL_FROM_EMAIL.lower():
            raise RuntimeError("Connected sender does not match the approved sender")
        result = worker.send_email(token, row["recipient"], row["subject"], row["body_text"])
        if not result.get("id") or not result.get("threadId"):
            raise RuntimeError("Gmail did not return message identifiers")
        worker.sb("PATCH", "sdr_inbox_checks", params={"id": f"eq.{check_id}"}, body={
            "status": "sent", "gmail_message_id": result["id"],
            "gmail_thread_id": result["threadId"], "sent_at": worker.now()})
        print("Gmail accepted the single test email. Inbox placement needs recipient confirmation.")
    except Exception:
        # Do not log private message contents, addresses, tokens or response bodies.
        try:
            worker.sb("PATCH", "sdr_inbox_checks", params={"id": f"eq.{check_id}"}, body={"status": "review"})
        except Exception:
            pass  # claimed remains non-retryable if even the review write failed
        raise RuntimeError("Inbox check needs review; automatic resend is disabled") from None


def inspect_check(check_id):
    """Read the exact sent message and targeted delivery failures; never send."""
    UUID(check_id)
    rows = worker.sb("GET", "sdr_inbox_checks", params={"id": f"eq.{check_id}", "limit": "1"}) or []
    if not rows or not rows[0].get("gmail_message_id"):
        print("No recorded Gmail message to inspect.")
        return
    row = rows[0]
    if not valid_email(row["recipient"]):
        raise ValueError("Invalid test recipient")
    token = worker.gmail_token()
    msg = worker.gmail_api(token, "GET", "messages/" + row["gmail_message_id"],
        params={"format": "metadata", "metadataHeaders": ["To", "From", "Subject"]})
    headers = msg.get("payload", {}).get("headers", [])
    report = {"message_found": msg.get("id") == row["gmail_message_id"],
              "in_sent": "SENT" in msg.get("labelIds", []),
              "recipient_matches": parseaddr(worker.header_value(headers, "To"))[1].lower() == row["recipient"].lower(),
              "sender_matches": parseaddr(worker.header_value(headers, "From"))[1].lower() == row["expected_sender"].lower(),
              "subject_matches": worker.header_value(headers, "Subject") == row["subject"]}
    thread = worker.gmail_api(token, "GET", "threads/" + (row.get("gmail_thread_id") or row["gmail_message_id"]), params={"format": "full"})
    replies = []
    for reply in thread.get("messages", []):
        reply_headers = reply.get("payload", {}).get("headers", [])
        from_email = parseaddr(worker.header_value(reply_headers, "From"))[1].lower()
        to_email = parseaddr(worker.header_value(reply_headers, "To"))[1].lower()
        if from_email == row["recipient"].lower() and to_email == row["expected_sender"].lower():
            replies.append({"message_id": reply["id"],
                            "readable_body": bool(worker.extract_text(reply.get("payload", {})).strip())})
    report["matched_customer_replies"] = len(replies)
    report["reply_details"] = replies
    print("INBOX_CHECK_DIAGNOSTIC " + json.dumps(report))


if __name__ == "__main__":
    if sys.argv[1:] == ["--inspect"]:
        inspect_check(os.environ["INBOX_CHECK_ID"])
    elif not sys.argv[1:]:
        run_check(os.environ["INBOX_CHECK_ID"])
    else:
        raise SystemExit("Unknown command")
