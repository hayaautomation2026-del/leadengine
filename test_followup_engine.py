import contextlib
import io
import unittest

import sdr_worker


class FollowupEngineTests(unittest.TestCase):
    def setUp(self):
        self.original_sb = sdr_worker.sb
        self.original_ai = sdr_worker.openai_text
        self.original_send = sdr_worker.send_email
        self.original_dry_run = sdr_worker.DRY_RUN

    def tearDown(self):
        sdr_worker.sb = self.original_sb
        sdr_worker.openai_text = self.original_ai
        sdr_worker.send_email = self.original_send
        sdr_worker.DRY_RUN = self.original_dry_run

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


if __name__ == "__main__":
    unittest.main()
