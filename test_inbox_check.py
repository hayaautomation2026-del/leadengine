import unittest
from unittest.mock import patch
import inbox_check

CHECK_ID = "d2d77c87-ec8a-4878-a5dd-e6e9509a257b"


class InboxCheckTests(unittest.TestCase):
    def setUp(self):
        self.row = {"id": CHECK_ID, "recipient": "owner@example.com", "expected_sender": "sdr@example.com",
                    "subject": "Delivery check", "body_text": "Please reply", "status": "queued"}
        self.status = "queued"
        self.calls = []
        def db(method, table, *, params=None, body=None, prefer=None):
            self.calls.append(table)
            self.assertEqual(table, "sdr_inbox_checks")
            if params.get("status") == "eq.queued":
                if self.status != "queued": return []
                self.status = "claimed"
                return [self.row]
            self.status = body["status"]
            return None
        self.patches = [patch.object(inbox_check.worker, "sb", side_effect=db),
            patch.object(inbox_check.worker, "load_settings", return_value={"sending_enabled": False, "kill_switch": False}),
            patch.object(inbox_check.worker, "gmail_token", return_value="fake-token"),
            patch.object(inbox_check.worker, "gmail_api", return_value={"emailAddress": "sdr@example.com"}),
            patch.object(inbox_check.worker, "GMAIL_FROM_EMAIL", "sdr@example.com"),
            patch.object(inbox_check.worker, "send_email", return_value={"id": "m1", "threadId": "t1"})]
        self.mocks = [p.start() for p in self.patches]
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])

    def test_single_send_and_rerun_does_nothing(self):
        inbox_check.run_check(CHECK_ID)
        inbox_check.run_check(CHECK_ID)
        self.mocks[-1].assert_called_once_with("fake-token", "owner@example.com", "Delivery check", "Please reply")
        self.assertEqual(self.status, "sent")

    def test_wrong_sender_does_not_send(self):
        self.row["expected_sender"] = "other@example.com"
        with self.assertRaises(RuntimeError): inbox_check.run_check(CHECK_ID)
        self.mocks[-1].assert_not_called()

    def test_timeout_does_not_resend(self):
        self.mocks[-1].side_effect = TimeoutError()
        with self.assertRaises(RuntimeError): inbox_check.run_check(CHECK_ID)
        inbox_check.run_check(CHECK_ID)
        self.assertEqual(self.mocks[-1].call_count, 1)
        self.assertEqual(self.status, "review")

    def test_recipient_header_injection_is_rejected(self):
        self.row["recipient"] = "owner@example.com\r\nBcc: other@example.com"
        with self.assertRaises(RuntimeError): inbox_check.run_check(CHECK_ID)
        self.mocks[-1].assert_not_called()

    def test_emergency_stop_blocks_test(self):
        self.mocks[1].return_value = {"sending_enabled": False, "kill_switch": True}
        with self.assertRaises(RuntimeError): inbox_check.run_check(CHECK_ID)
        self.mocks[-1].assert_not_called()

    def test_prospect_sending_is_never_enabled(self):
        self.mocks[1].return_value = {"sending_enabled": True, "kill_switch": False}
        with self.assertRaises(RuntimeError): inbox_check.run_check(CHECK_ID)
        self.mocks[-1].assert_not_called()


class InspectionTests(unittest.TestCase):
    def test_inspection_uses_only_get_and_never_sends(self):
        row = {"gmail_message_id": "m1", "recipient": "owner@example.com",
               "expected_sender": "sdr@example.com", "subject": "Test"}
        def db(method, *args, **kwargs):
            self.assertEqual(method, "GET")
            return [row]
        def gmail(token, method, path, **kwargs):
            self.assertEqual(method, "GET")
            if path == "messages/m1":
                return {"id": "m1", "labelIds": ["SENT"], "payload": {"headers": [
                    {"name": "To", "value": "owner@example.com"},
                    {"name": "From", "value": "sdr@example.com"},
                    {"name": "Subject", "value": "Test"}]}}
            return {"messages": [{"id": "reply1", "payload": {"mimeType": "text/plain", "body": {"data": "SW50ZXJlc3RlZA"}, "headers": [
                {"name": "From", "value": "owner@example.com"}, {"name": "To", "value": "sdr@example.com"}]}}]}
        with patch.object(inbox_check.worker, "sb", side_effect=db), \
             patch.object(inbox_check.worker, "gmail_api", side_effect=gmail), \
             patch.object(inbox_check.worker, "gmail_token", return_value="fake"), \
             patch.object(inbox_check.worker, "send_email", side_effect=AssertionError("Must not send")):
            inbox_check.inspect_check(CHECK_ID)


if __name__ == "__main__": unittest.main()
