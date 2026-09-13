import unittest
from unittest.mock import patch

import unattended_inbox as ui


def msg(mid, thread, when, sender, to):
    return {
        "id": mid,
        "threadId": thread,
        "internalDate": str(when),
        "payload": {"headers": [
            {"name": "From", "value": sender},
            {"name": "To", "value": to},
        ]},
    }


class UnattendedInboxTests(unittest.TestCase):
    def test_transient_supabase_errors_only(self):
        self.assertTrue(ui.is_transient(RuntimeError("Supabase GET failed 504: Gateway Timeout")))
        self.assertTrue(ui.is_transient(RuntimeError("Supabase GET failed 429: rate limited")))
        self.assertFalse(ui.is_transient(RuntimeError("Supabase GET failed 400: bad request")))
        self.assertFalse(ui.is_transient(RuntimeError("gmail failed 504")))

    def test_robust_customer_messages_accepts_rewritten_recipient_headers(self):
        thread = {"messages": [
            msg("good", "t1", 2000, "Ameer <mtiameer4@gmail.com>", "AI <aiagentsutomations01@gmail.com>"),
            msg("wrong-from", "t1", 3000, "other@example.com", "aiagentsutomations01@gmail.com"),
            msg("wrong-to", "t1", 4000, "mtiameer4@gmail.com", "other@example.com"),
        ]}
        check = {"recipient": "mtiameer4@gmail.com", "expected_sender": "aiagentsutomations01@gmail.com"}
        self.assertEqual([m["id"] for m in ui.robust_customer_messages(thread, check)], ["good"])

    def test_relink_uses_only_unprocessed_post_send_messages(self):
        check = {
            "id": "check",
            "recipient": "mtiameer4@gmail.com",
            "expected_sender": "aiagentsutomations01@gmail.com",
            "subject": "Test",
            "gmail_thread_id": "old-thread",
            "sent_at": "2026-09-13T23:00:00+00:00",
        }
        old = msg("old", "old-thread", 1757804300000, "mtiameer4@gmail.com", "aiagentsutomations01@gmail.com")
        processed = msg("done", "x", 1757804500000, "mtiameer4@gmail.com", "aiagentsutomations01@gmail.com")
        good = msg("new", "new-thread", 1757804600000, "mtiameer4@gmail.com", "aiagentsutomations01@gmail.com")
        wrong = msg("wrong", "bad-thread", 1757804700000, "other@example.com", "aiagentsutomations01@gmail.com")
        by_id = {m["id"]: m for m in [old, processed, good, wrong]}
        patches = []

        def sb(method, table, params=None, body=None, prefer=None):
            if method == "GET":
                return [check]
            patches.append((table, body))
            return []

        def gmail_api(token, method, path, params=None, body=None):
            if path == "messages":
                return {"messages": [{"id": k} for k in by_id]}
            return by_id[path.split("/")[-1]]

        config = {"state": {"processed": ["done"]}}
        with patch.object(ui.flow.worker, "sb", side_effect=sb), \
             patch.object(ui.flow.worker, "gmail_token", return_value="token"), \
             patch.object(ui.flow.worker, "gmail_api", side_effect=gmail_api), \
             patch.object(ui, "record"):
            ui.relink_controlled_thread("check", config)

        self.assertEqual(patches, [("sdr_inbox_checks", {"gmail_thread_id": "new-thread"})])


if __name__ == "__main__":
    unittest.main()
