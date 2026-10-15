import base64
import contextlib
import io
import unittest

import sdr_worker


class SDRWorkerTests(unittest.TestCase):
    def setUp(self):
        self.original_sb = sdr_worker.sb
        self.original_ai = sdr_worker.openai_text
        self.original_send = sdr_worker.send_email
        self.original_gmail_api = sdr_worker.gmail_api
        self.original_dry_run = sdr_worker.DRY_RUN
        self.original_handoff_email = sdr_worker.HANDOFF_EMAIL

    def tearDown(self):
        sdr_worker.sb = self.original_sb
        sdr_worker.openai_text = self.original_ai
        sdr_worker.send_email = self.original_send
        sdr_worker.gmail_api = self.original_gmail_api
        sdr_worker.DRY_RUN = self.original_dry_run
        sdr_worker.HANDOFF_EMAIL = self.original_handoff_email


    def test_validation_blocks_placeholders_and_short_messages(self):
        invalid = [
            ("owner@example.com", "Hey", "This is a sufficiently long valid body for testing."),
            ("owner@example.com", "Good subject", "short"),
            ("owner@example.com", "Hi [Name]", "This is a sufficiently long valid body for testing."),
            ("owner@example.com", "Good subject", "```json\\nThis is a long body with unrendered text."),
            ("missing-at.example.com", "Good subject", "This is a sufficiently long valid body for testing."),
        ]
        for email, subject, body in invalid:
            with self.subTest(subject=subject, body=body):
                with self.assertRaises(ValueError):
                    sdr_worker.validate_outbound_email(email, subject, body)

    def test_invalid_send_never_calls_gmail(self):
        sdr_worker.gmail_api = lambda *a, **k: self.fail("Gmail must not run")
        with self.assertRaises(ValueError):
            sdr_worker.send_email("token", "owner@example.com", "Bad", "Too short")

    def test_outbox_claim_precedes_send_and_persistence_retries(self):
        calls = []
        failures = [1]
        original_sleep = sdr_worker.time.sleep
        sdr_worker.time.sleep = lambda _: None
        def fake_sb(method, path, *, params=None, body=None, prefer=None):
            calls.append((method, path, body, params))
            if method == "PATCH" and path == "leads" and body.get("outreach_status") == "sending":
                return [{"id": "lead-1"}]
            if method == "POST" and path == "sdr_email_messages":
                return [{"id": "outbox-1"}]
            if method == "PATCH" and path == "sdr_email_messages":
                if failures:
                    failures.pop()
                    raise RuntimeError("temporary database outage")
                return [{"id": "outbox-1"}]
            if method == "PATCH" and path == "leads":
                return [{"id": "lead-1"}]
            return []
        try:
            sdr_worker.sb = fake_sb
            lead = {"id": "lead-1", "outreach_status": "pending"}
            outbox = sdr_worker.reserve_outbound(lead, "owner@example.com", "Question", "This body is over 25 characters.")
            self.assertEqual(outbox, "outbox-1")
            self.assertEqual([(x[0], x[1]) for x in calls[:2]],
                             [("PATCH", "leads"), ("POST", "sdr_email_messages")])
            sdr_worker.finalize_outbound("lead-1", outbox, {"id": "gmail-1", "threadId": "thread-1"}, {
                "outreach_status": "contacted", "last_contacted_at": sdr_worker.now(),
                "followup_due_at": None,
            })
            self.assertEqual(len([x for x in calls if x[0] == "PATCH" and x[1] == "sdr_email_messages"]), 2)
            self.assertEqual(calls[-1][2]["outreach_status"], "contacted")
        finally:
            sdr_worker.time.sleep = original_sleep

    def test_failed_outbox_record_never_calls_gmail(self):
        sdr_worker.sb = lambda method, path, **kwargs: (
            [{"id": "lead-1"}] if method == "PATCH" and path == "leads" else []
        )
        sdr_worker.gmail_api = lambda *a, **k: self.fail("Gmail must never run")
        with self.assertRaises(RuntimeError):
            sdr_worker.reserve_outbound(
                {"id": "lead-1", "outreach_status": "pending"},
                "owner@example.com", "Question", "This body is over 25 characters."
            )

    def test_due_lead_generates_dry_followup_without_sending(self):
        sdr_worker.DRY_RUN = True

        def fake_sb(method, path, *, params=None, body=None, prefer=None):
            if path == "sdr_settings":
                return [{"sending_enabled": False, "daily_send_cap": 30}]
            if path == "sdr_suppressions":
                return []
            if path == "leads":
                return [{
                    "id": "lead-1",
                    "business_name": "Example Business",
                    "email": "owner@example.com",
                    "followup_due_at": "2026-09-01T00:00:00+00:00",
                    "outreach_status": "contacted",
                }]
            if path == "sdr_email_messages" and params and params.get("sent_at"):
                return []
            if path == "sdr_email_messages" and params and params.get("direction") == "eq.inbound":
                return []
            if path == "sdr_email_messages" and params and params.get("direction") == "eq.outbound":
                return [{
                    "id": "msg-1",
                    "subject": "Quick question",
                    "body_text": "Initial outreach message",
                    "sent_at": "2026-09-01T00:00:00+00:00",
                }]
            return []

        sdr_worker.sb = fake_sb
        sdr_worker.openai_text = lambda prompt: '{"body":"Just checking whether this is worth a quick look. Happy to share a short example if useful."}'
        sdr_worker.send_email = lambda *args, **kwargs: self.fail("Dry-run must never call Gmail send")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            sdr_worker.process_followups("fake-token")

        text = output.getvalue()
        self.assertIn("DRY FOLLOW-UP #1", text)
        self.assertIn("owner@example.com", text)
        self.assertIn("SDR follow-ups processed: 1", text)

    def test_sequence_stops_after_three_total_touches(self):
        sdr_worker.DRY_RUN = False
        patches = []

        def fake_sb(method, path, *, params=None, body=None, prefer=None):
            if path == "sdr_settings":
                return [{"sending_enabled": False, "daily_send_cap": 30}]
            if path == "sdr_suppressions":
                return []
            if path == "leads" and method == "PATCH":
                patches.append(body)
                return None
            if path == "leads":
                return [{
                    "id": "lead-2",
                    "business_name": "Example Business",
                    "email": "owner@example.com",
                    "followup_due_at": "2026-09-01T00:00:00+00:00",
                    "outreach_status": "contacted",
                }]
            if path == "sdr_email_messages" and params and params.get("sent_at"):
                return []
            if path == "sdr_email_messages" and params and params.get("direction") == "eq.inbound":
                return []
            if path == "sdr_email_messages" and params and params.get("direction") == "eq.outbound":
                return [
                    {"id": "m3", "subject": "Subject", "body_text": "Third", "sent_at": "2026-09-03T00:00:00+00:00"},
                    {"id": "m2", "subject": "Subject", "body_text": "Second", "sent_at": "2026-09-02T00:00:00+00:00"},
                    {"id": "m1", "subject": "Subject", "body_text": "First", "sent_at": "2026-09-01T00:00:00+00:00"},
                ]
            return []

        sdr_worker.sb = fake_sb
        sdr_worker.send_email = lambda *args, **kwargs: self.fail("Sequence-complete lead must not send")
        sdr_worker.process_followups("fake-token")

        self.assertTrue(any(p.get("outreach_status") == "stopped" and p.get("followup_due_at") is None for p in patches))

    def test_hot_reply_creates_dry_handoff_and_marks_lead_hot(self):
        sdr_worker.DRY_RUN = True
        sdr_worker.HANDOFF_EMAIL = "closer@example.com"
        posts = []
        patches = []

        reply_text = "Yes, please send pricing and let's arrange a call tomorrow."
        encoded_reply = base64.urlsafe_b64encode(reply_text.encode()).decode().rstrip("=")

        def fake_gmail_api(token, method, path, *, params=None, body=None):
            if method == "GET" and path == "messages":
                return {"messages": [{"id": "gmail-hot-1"}]}
            if method == "GET" and path == "messages/gmail-hot-1":
                return {
                    "threadId": "thread-hot-1",
                    "payload": {
                        "mimeType": "text/plain",
                        "headers": [
                            {"name": "From", "value": "Owner <owner@example.com>"},
                            {"name": "Subject", "value": "Re: Automation"},
                        ],
                        "body": {"data": encoded_reply},
                    },
                }
            if method == "POST" and path == "messages/gmail-hot-1/modify":
                return {}
            raise AssertionError(f"Unexpected Gmail call: {method} {path}")

        def fake_sb(method, path, *, params=None, body=None, prefer=None):
            if path == "sdr_email_messages" and method == "GET":
                return []
            if path == "leads" and method == "GET":
                return [{
                    "id": "lead-hot-1",
                    "business_name": "Hot Prospect LLC",
                    "email": "owner@example.com",
                    "phone_number": "+971500000000",
                    "whatsapp_number": "+971500000000",
                    "city": "Abu Dhabi",
                    "country": "UAE",
                    "website_url": "https://example.com",
                }]
            if path == "sdr_email_messages" and method == "POST":
                posts.append(body)
                return None
            if path == "leads" and method == "PATCH":
                patches.append(body)
                return None
            if path == "sdr_suppressions":
                return []
            return []

        sdr_worker.gmail_api = fake_gmail_api
        sdr_worker.sb = fake_sb
        sdr_worker.openai_text = lambda prompt: '{"classification":"hot","buying_intent":92,"summary":"Prospect asked for pricing and a call."}'
        sdr_worker.send_email = lambda *args, **kwargs: self.fail("Dry HOT handoff must not send a real email")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            sdr_worker.process_replies("fake-token")

        text = output.getvalue()
        self.assertIn("DRY HOT HANDOFF", text)
        self.assertIn("Hot Prospect LLC", text)
        self.assertIn("92/100", text)
        self.assertIn("Prospect asked for pricing and a call.", text)
        self.assertIn(reply_text, text)
        self.assertIn("SDR replies processed: 1", text)
        self.assertEqual(posts[0]["ai_classification"], "hot")
        self.assertTrue(any(p.get("outreach_status") == "hot" and p.get("followup_due_at") is None for p in patches))

    def test_live_handoff_uses_configured_handoff_email(self):
        sdr_worker.DRY_RUN = False
        sdr_worker.HANDOFF_EMAIL = "closer@example.com"
        sent = []

        def fake_send(token, to_email, subject, body):
            sent.append((token, to_email, subject, body))
            return {"id": "internal-alert-1"}

        sdr_worker.send_email = fake_send
        lead = {
            "business_name": "Hot Prospect LLC",
            "email": "owner@example.com",
            "phone_number": "+971500000000",
            "whatsapp_number": "+971500000000",
            "city": "Abu Dhabi",
            "country": "UAE",
        }
        cls = {"buying_intent": 88, "summary": "Prospect wants a demo."}

        result = sdr_worker.notify_hot_handoff("token-1", lead, "Re: Demo", "Can we see a demo?", cls)

        self.assertEqual(result["id"], "internal-alert-1")
        self.assertEqual(sent[0][1], "closer@example.com")
        self.assertIn("HOT LEAD: Hot Prospect LLC | 88/100", sent[0][2])
        self.assertIn("Reply personally now", sent[0][3])


if __name__ == "__main__":
    unittest.main()
