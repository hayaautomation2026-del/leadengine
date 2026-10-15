"""Gemini adapter must read the owner's sales playbook at runtime."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sdr_worker_gemini as gemini
import sdr_worker


class GeminiPlaybookTests(unittest.TestCase):
    def test_dynamic_playbook_and_missing_file_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "playbook.md"
            with patch.object(sdr_worker, "PLAYBOOK_PATH", path):
                self.assertEqual(gemini.load_playbook(), "")
                path.write_text("## Approved Buyer Q&A\n- pricing: Scope-based.\n", encoding="utf-8")
                self.assertIn("Scope-based.", gemini.load_playbook())
                self.assertEqual(sdr_worker.playbook_offer()["price_text"], "Scope-based.")

    def test_gemini_reader_and_decider_use_same_engine(self):
        question = "What does it cost?"
        offer = {"approved_answers": {"pricing": "Please ask us for a quote."}}
        response = '{"intent":"price","intent_evidence":"What does it cost?","answer_keys":["pricing"],"facts":{}}'
        with patch.object(gemini, "gemini_text", return_value=response):
            extracted = gemini.assess(question, [], offer=offer)
        from conversation_engine import new_conversation
        state, decision = gemini.advance(new_conversation(), "msg1", question, extracted, offer)
        self.assertEqual(decision["action"], "draft")
        self.assertEqual(decision["body"], "Please ask us for a quote.")


if __name__ == "__main__":
    unittest.main()
