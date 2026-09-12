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
