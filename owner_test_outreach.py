"""One-time owner-only AI outreach verification.

Uses the existing atomic inbox_check send gate and reply inspector.
No prospect queries or global sending controls are changed.
Rerunning after Gmail acceptance is inspection-only; it cannot resend.
"""
from __future__ import annotations

import hashlib
import os
from uuid import UUID

import inbox_check
import sdr_worker as worker
from sdr_worker_gemini import gemini_text

EXPECTED_SENDER = "aiagentsutomations01@gmail.com"
EXPECTED_RECIPIENT_SHA256 = "bc87adf653bc80056bad41f88549414fd42791e504e52a8affe3e99e7f0c639d"


def check_authorized_row(row):
    """Pin test to the owner's configured address, not an arbitrary DB recipient."""
    if row.get("expected_sender", "").lower() != EXPECTED_SENDER:
        raise RuntimeError("Test sender mismatch")
    if worker.GMAIL_FROM_EMAIL.lower() != EXPECTED_SENDER:
        raise RuntimeError("Gmail sender configuration mismatch")
    recipient = str(row.get("recipient") or "").strip().lower()
    digest = hashlib.sha256(recipient.encode("utf-8")).hexdigest()
    if digest != EXPECTED_RECIPIENT_SHA256:
        raise RuntimeError("Unapproved test recipient")
    if worker.DRY_RUN is not True:
        raise RuntimeError("Prospect worker must remain in dry-run mode")
    settings = worker.load_settings()
    if settings.get("sending_enabled") is not False or settings.get("kill_switch") is not False:
        raise RuntimeError("Prospect sending must be OFF and emergency stop disengaged")


def generate_owner_draft(recipient):
    """AI-generated formatting sample, never impersonating a verified real prospect."""
    prompt = (
        "Generate a demonstration FIRST-TOUCH sales outreach email for the owner to inspect. "
        "The entire business example is HYPOTHETICAL, not verified research. "
        "Scenario: a fictional small salon has an online inquiry form; our offer is social-media "
        "creative packages (posts and captions) and separately agreed inquiry-follow-up automation. "
        "The email must be plain-text, peer-to-peer, under 55 words, and finish with ONE easy question. "
        "No invented results, times, prices, guarantees, personal names, links, attachments, "
        "or generic cold-email clichés. Do not claim you saw the recipient's real website. "
        "Return JSON ONLY with string keys subject and body, and no Markdown.\n\n"
        + worker.load_playbook()
    )
    obj = worker.extract_json(gemini_text(prompt))
    if not isinstance(obj, dict):
        raise ValueError("Gemini draft was not a JSON object")
    subject = obj.get("subject")
    body = obj.get("body")
    if not isinstance(subject, str) or not isinstance(body, str):
        raise ValueError("Gemini did not return subject and body strings")
    subject = "TEST - " + subject.strip().removeprefix("TEST - ")
    body = (
        "TEST ONLY - Sample outreach for formatting and reply tracking.\n"
        "Hypothetical scenario; no real business was researched.\n\n"
        + body.strip()
    )
    worker.validate_outbound_email(recipient, subject, body, first_touch=True)
    if body.count("?") != 1:
        raise ValueError("Sample outreach must contain exactly one question")
    print("OWNER_TEST_AI_DRAFT_VALIDATED; NO_PROSPECT_DATA_USED", flush=True)
    return subject, body


def main():
    check_id = os.environ["INBOX_CHECK_ID"]
    UUID(check_id)
    rows = worker.sb(
        "GET", "sdr_inbox_checks",
        params={"id": f"eq.{check_id}", "limit": "1"},
    ) or []
    if len(rows) != 1:
        raise RuntimeError("Expected exactly one registered owner test")
    row = rows[0]
    check_authorized_row(row)

    if row["status"] == "sent":
        print("OWNER_TEST_ALREADY_SENT; NO_RESEND; INSPECTION_ONLY", flush=True)
        inbox_check.inspect_check(check_id)
        return
    if row["status"] != "queued":
        raise RuntimeError("Test already claimed or requires review; will not resend")

    subject, body = generate_owner_draft(row["recipient"])
    updated = worker.sb(
        "PATCH", "sdr_inbox_checks",
        params={"id": f"eq.{check_id}", "status": "eq.queued"},
        body={"subject": subject, "body_text": body},
        prefer="return=representation",
    ) or []
    if len(updated) != 1:
        raise RuntimeError("Test row changed before draft update; no send")

    # Existing queued -> claimed -> sent gate prevents any second send on reruns.
    inbox_check.run_check(check_id)
    print("OWNER_TEST_SEND_ATTEMPT_COMPLETED", flush=True)
    inbox_check.inspect_check(check_id)


if __name__ == "__main__":
    main()
