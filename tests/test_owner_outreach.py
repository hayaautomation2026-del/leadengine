"""Offline safety checks for one-shot owner outreach; no Gmail or Supabase calls."""
import unittest
from unittest.mock import patch

import owner_test_outreach as target


TEST_ROW = {
    "recipient": "hayaautomation2026@gmail.com",
    "expected_sender": "aiagentsutomations01@gmail.com",
    "status": "queued",
}


class OwnerOnlyEmailTests(unittest.TestCase):
    def setUp(self):
        patches = [
            patch.object(target.worker, "GMAIL_FROM_EMAIL", "aiagentsutomations01@gmail.com"),
            patch.object(target.worker, "DRY_RUN", True),
            patch.object(target.worker, "load_settings", return_value={
                "sending_enabled": False, "kill_switch": False,
            }),
            patch.dict(target.os.environ, {"INBOX_CHECK_ID": "61bfd601-e25c-4d91-878b-b1c1b1dcf7a7"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_authorized_owner_and_disabled_prospects(self):
        target.check_authorized_row(TEST_ROW)

    def test_any_other_recipient_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Unapproved test recipient"):
            target.check_authorized_row({**TEST_ROW, "recipient": "someone@example.com"})

    def test_global_sending_enabled_is_rejected(self):
        with patch.object(target.worker, "load_settings", return_value={
            "sending_enabled": True, "kill_switch": False,
        }):
            with self.assertRaisesRegex(RuntimeError, "Prospect sending must be OFF"):
                target.check_authorized_row(TEST_ROW)

    @patch.object(target.inbox_check, "inspect_check")
    @patch.object(target.inbox_check, "run_check")
    @patch.object(target.worker, "sb")
    def test_rerunning_sent_row_never_resends(self, sb, send, inspect):
        sb.return_value = [{**TEST_ROW, "status": "sent"}]
        target.main()
        send.assert_not_called()
        inspect.assert_called_once()

    @patch.object(target.inbox_check, "inspect_check")
    @patch.object(target.inbox_check, "run_check")
    @patch.object(target.worker, "sb")
    @patch.object(target, "generate_owner_draft", return_value=("TEST - Sample", "TEST ONLY - Hypothetical sample text for owner inbox check."))
    def test_queued_row_is_sent_once(self, draft, sb, send, inspect):
        sb.side_effect = [[TEST_ROW], [TEST_ROW]]
        target.main()
        send.assert_called_once()
        inspect.assert_called_once()

    @patch.object(target.inbox_check, "run_check")
    @patch.object(target.worker, "sb")
    def test_claimed_row_never_resends(self, sb, send):
        sb.return_value = [{**TEST_ROW, "status": "claimed"}]
        with self.assertRaisesRegex(RuntimeError, "will not resend"):
            target.main()
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
