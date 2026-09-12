"""Send one explicitly queued owner inbox check; never run the prospect worker.

Private recipients and message content stay in the database, not the public repo.
Atomic queued->claimed update permits one send attempt only. A timeout or crash
requires manual review; reruns never blindly resend an uncertain message.
"""
import os
import re
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


if __name__ == "__main__":
    run_check(os.environ["INBOX_CHECK_ID"])
