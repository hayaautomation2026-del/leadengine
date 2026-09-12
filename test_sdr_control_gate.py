import unittest

import sdr_worker_gemini as adapter


class SDRControlGateTests(unittest.TestCase):
    def setUp(self):
        self.original = adapter.ORIGINAL_SB
        self.calls = []

    def tearDown(self):
        adapter.ORIGINAL_SB = self.original

    def fake_with(self, settings):
        def fake(method, path, *, params=None, body=None, prefer=None):
            if path == "sdr_settings":
                return [settings]
            self.calls.append((method, path, dict(params or {})))
            return [{"id": "lead-1"}]
        adapter.ORIGINAL_SB = fake

    def lead_query(self):
        return adapter.controlled_sb(
            "GET",
            "leads",
            params={
                "select": "id,email,pain_score",
                "email": "not.is.null",
                "outreach_status": "eq.pending",
                "limit": "10",
            },
        )

    def test_manual_mode_only_queries_approved(self):
        self.fake_with({"sending_enabled": True, "kill_switch": False, "approval_mode": "manual", "min_pain_score": 50})
        self.lead_query()
        self.assertEqual(self.calls[-1][2]["outreach_status"], "eq.approved")
        self.assertEqual(self.calls[-1][2]["pain_score"], "gte.50")

    def test_auto_mode_allows_pending_or_approved(self):
        self.fake_with({"sending_enabled": True, "kill_switch": False, "approval_mode": "auto", "min_pain_score": 50})
        self.lead_query()
        self.assertEqual(self.calls[-1][2]["outreach_status"], "in.(pending,approved)")

    def test_stop_returns_no_outbound_candidates(self):
        self.fake_with({"sending_enabled": False, "kill_switch": False, "approval_mode": "manual", "min_pain_score": 50})
        self.assertEqual(self.lead_query(), [])
        self.assertEqual(self.calls, [])

    def test_kill_returns_no_outbound_candidates(self):
        self.fake_with({"sending_enabled": True, "kill_switch": True, "approval_mode": "auto", "min_pain_score": 50})
        self.assertEqual(self.lead_query(), [])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
