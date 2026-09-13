import base64
from email import message_from_bytes
import unittest
from unittest.mock import patch

import unattended_inbox as ui
from conversation_engine import new_conversation


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
        old = msg("old", "old-thread", 1789340390000, "mtiameer4@gmail.com", "aiagentsutomations01@gmail.com")
        processed = msg("done", "x", 1789340500000, "mtiameer4@gmail.com", "aiagentsutomations01@gmail.com")
        good = msg("new", "new-thread", 1789340600000, "mtiameer4@gmail.com", "aiagentsutomations01@gmail.com")
        wrong = msg("wrong", "bad-thread", 1789340700000, "other@example.com", "aiagentsutomations01@gmail.com")
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

    def test_gmail_native_reply_lets_gmail_generate_message_id(self):
        inbound = {
            "id": "in1",
            "payload": {"headers": [
                {"name": "Subject", "value": "Inbox test"},
                {"name": "Message-ID", "value": "<in1@example.com>"},
                {"name": "References", "value": ""},
            ]},
        }
        check = {
            "recipient": "mtiameer4@gmail.com",
            "expected_sender": "aiagentsutomations01@gmail.com",
            "gmail_thread_id": "thread1",
        }
        payload = ui.gmail_native_reply_payload(check, inbound, "hello")
        mime = message_from_bytes(base64.urlsafe_b64decode(payload["raw"] + "=" * (-len(payload["raw"]) % 4)))
        self.assertEqual(payload["threadId"], "thread1")
        self.assertEqual(mime["To"], "mtiameer4@gmail.com")
        self.assertEqual(mime["Subject"], "Re: Inbox test")
        self.assertEqual(mime["In-Reply-To"], "<in1@example.com>")
        self.assertIsNone(mime["Message-ID"])

    def test_explicit_bullet_request_is_formatted_as_bullets(self):
        message = "give me the same details in bullet points"
        assessment = {
            "intent": "details",
            "intent_evidence": message,
            "answer_keys": ["delivery", "inputs", "inclusions"],
            "facts": {},
            "unknown_fields": [],
        }
        offer = {"approved_answers": {
            "delivery": "Delivery timing is confirmed before payment.",
            "inputs": "We need your logo and business details.",
            "inclusions": "The package includes posts and captions.",
        }}
        _, decision = ui.format_aware_advance(
            new_conversation(), "m1", message, assessment, offer
        )
        self.assertEqual(decision["action"], "draft")
        self.assertTrue(all(line.startswith("- ") for line in decision["body"].splitlines()))
        self.assertIn("Delivery timing", decision["body"])
        self.assertIn("logo and business details", decision["body"])
        self.assertIn("posts and captions", decision["body"])


if __name__ == "__main__":
    unittest.main()
