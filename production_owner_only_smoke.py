"""One-run, owner-email-only production first-touch proof. No prospect batch access."""
import os
import sys
import traceback

import sdr_worker_gemini as adapter

worker = adapter.sdr_worker
RAW_SB = adapter.ORIGINAL_SB
TEST_EMAIL = "hayaautomation2026@gmail.com"
MARKER = "OWNER_ONLY_PRODUCTION_OUTREACH_20261017"
lead_id = None
sending_armed = False
send_calls = 0

def sb(method, path, **kwargs):
    return RAW_SB(method, path, **kwargs)

def preflight():
    settings = worker.load_settings()
    assert settings.get("sending_enabled") is False, "ABORT: send already enabled"
    assert settings.get("kill_switch") is False, "ABORT: owner kill switch on"
    assert settings.get("approval_mode") == "manual", "ABORT: manual mode required"
    assert not worker.DRY_RUN, "ABORT: production dry run still enabled"
    other = sb("GET", "leads", params={
        "select": "id,outreach_status,contact_status,last_checked_at,updated_at,notes,followup_due_at,last_contacted_at",
        "limit": "1000",
    }) or []
    real = [r for r in other if r["outreach_status"] == "pending"]
    assert len(real) == 29 and len(other) == 30, "ABORT: expected 29 pending leads and one approved test lead"
    tests = sb("GET", "leads", params={"select": "id,source,outreach_status,notes",
             "email": "eq." + TEST_EMAIL, "limit": "2"}) or []
    assert (len(tests) == 1 and tests[0]["source"] == "controlled_production_test"
            and tests[0]["outreach_status"] == "approved"
            and MARKER in (tests[0].get("notes") or "")), "ABORT: test lead not isolated"
    assert not (sb("GET", "sdr_email_messages", params={
        "select": "id", "lead_id": "eq." + tests[0]["id"], "limit": "1"}) or []), (
        "ABORT: test lead already has outbound history"
    )
    print("SAFE_PRECHECK_PASS existing_pending_leads=29 manual_mode=true sending_enabled=false", flush=True)
    return {r["id"]: r for r in real}, tests[0]["id"]

def safeguard_send(token, to_email, subject, body, **kwargs):
    global send_calls
    if str(to_email).strip().lower() != TEST_EMAIL:
        raise RuntimeError("BLOCKED_NON_OWNER_GMAIL_RECIPIENT")
    if send_calls:
        raise RuntimeError("BLOCKED_SECOND_GMAIL_SEND")
    send_calls += 1
    return original_send(token, to_email, subject, body, **kwargs)

def guarded_sb(method, path, *, params=None, body=None, prefer=None):
    if path == "leads":
        if method == "GET" and params and params.get("outreach_status") == "eq.pending":
            params = {**params, "id": "eq." + lead_id, "limit": "1"}
        if method in ("PATCH", "DELETE"):
            if not params or params.get("id") != "eq." + lead_id:
                raise RuntimeError("BLOCKED_NON_TEST_LEAD_WRITE")
        if method == "POST":
            raise RuntimeError("BLOCKED_UNEXPECTED_LEAD_INSERT")
    if path == "sdr_email_messages" and method == "POST":
        if not body or body.get("lead_id") != lead_id or body.get("direction") != "outbound":
            raise RuntimeError("BLOCKED_UNEXPECTED_OUTBOX_WRITE")
    return original_controlled_sb(method, path, params=params, body=body, prefer=prefer)

def assert_old_unchanged(snapshot):
    rows = sb("GET", "leads", params={
        "select": "id,outreach_status,contact_status,last_checked_at,updated_at,notes,followup_due_at,last_contacted_at",
        "limit": "1000",
    }) or []
    comparison = {r["id"]: r for r in rows if r["id"] in snapshot}
    assert comparison == snapshot, "REAL_PENDING_LEADS_MODIFIED"
    print("REAL_LEADS_UNTOUCHED count=29", flush=True)

def main():
    global lead_id, sending_armed, original_send, original_controlled_sb
    snapshot, lead_id = preflight()
    try:
        print("TEST_LEAD_APPROVED_FOR_ONE_OWNER_SEND lead_id=" + lead_id, flush=True)
        original_send = worker.send_email
        original_controlled_sb = worker.sb
        worker.send_email = safeguard_send
        worker.sb = guarded_sb

        # The live owner switch and approval-mode filtering are exercised for real.
        sb("PATCH", "sdr_settings", params={"id": "eq.true"},
           body={"sending_enabled": True})
        sending_armed = True
        assert worker.load_settings().get("sending_enabled") is True, "SENDING_GATE_NOT_ENABLED"
        print("OWNER_ONLY_SEND_GATE_ARMED", flush=True)

        token = worker.gmail_token()
        worker.send_batch(token)  # Actual production qualify -> Gmail send -> outbox finalize.
        records = sb("GET", "sdr_email_messages", params={
            "select": "id,lead_id,direction,status,to_email,gmail_message_id,gmail_thread_id,sent_at,subject",
            "lead_id": "eq." + lead_id,
            "limit": "5",
        }) or []
        assert send_calls == 1, "EXPECTED_EXACTLY_ONE_REAL_GMAIL_SEND"
        assert len(records) == 1, "EXPECTED_ONE_OUTBOUND_DB_RECORD"
        r = records[0]
        assert (r["direction"] == "outbound" and r["status"] == "sent"
                and r["to_email"].lower() == TEST_EMAIL and r.get("gmail_message_id")
                and r.get("gmail_thread_id") and r.get("sent_at")), "OUTBOUND_LOG_INCOMPLETE"
        print("PRODUCTION_OUTREACH_PASS qualification=true gmail_send=true table_log=true", flush=True)
        print("PRODUCTION_EMAIL_MESSAGE_ID=" + str(r["gmail_message_id"]), flush=True)
        print("PRODUCTION_EMAIL_THREAD_ID=" + str(r["gmail_thread_id"]), flush=True)
    finally:
        # Disable immediately even if model, Gmail, or DB logging fails.
        try:
            sb("PATCH", "sdr_settings", params={"id": "eq.true"},
               body={"sending_enabled": False})
            assert worker.load_settings().get("sending_enabled") is False, "OUTBOUND_DISABLE_NOT_CONFIRMED"
            sending_armed = False
            print("OUTBOUND_SENDING_DISABLED_CONFIRMED", flush=True)
        finally:
            if lead_id:
                # Prevent any owner-only test follow-up; preserve the sent email audit.
                sb("PATCH", "leads", params={"id": "eq." + lead_id},
                   body={"outreach_status": "stopped", "followup_due_at": None})
            assert_old_unchanged(snapshot)

if __name__ == "__main__":
    main()
