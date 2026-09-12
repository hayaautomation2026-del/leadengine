import json
import unittest
from unittest.mock import patch
import requests
import sdr_worker
from conversation_engine import new_conversation, set_owner


class PreviewAdapterTests(unittest.TestCase):
    def test_preview_uses_reader_without_sending_or_database(self):
        with patch.object(requests.sessions.Session, "request", side_effect=AssertionError("Network forbidden")), \
             patch.object(sdr_worker, "openai_text", return_value=json.dumps({
                 "intent": "price", "intent_evidence": "How much?", "facts": {}})):
            state, decision = sdr_worker.preview_conversation_reply(
                new_conversation(), "m1", "How much?", {"price_text": "USD 350"})
            self.assertEqual(decision["action"], "draft")
            self.assertIn("USD 350", decision["body"])

    def test_takeover_never_calls_model(self):
        with patch.object(sdr_worker, "openai_text", side_effect=AssertionError("No model needed")):
            _, decision = sdr_worker.preview_conversation_reply(
                set_owner(new_conversation(), "human"), "m1", "Hi", {})
            self.assertEqual(decision["action"], "handoff")


if __name__ == "__main__":
    unittest.main()
