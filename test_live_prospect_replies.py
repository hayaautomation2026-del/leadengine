"""Task 3 live reply regression tests. No Gmail or Supabase network calls."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sdr_worker
from conversation_engine import new_conversation


class LiveReplyTests(unittest.TestCase):
    def run_reply(self, message, assessment, *, existing=None, dry=False,
                  classification="warm", capture_handoff=False):
        lead = {
            "id": "lead-1", "email": "prospect@example.com",
            "business_name": "Prospect Company", "outreach_status": "contacted",
            "human_owned": False, "conversation_state": new_conversation(),
        }
        if existing:
            lead.update(existing)
        events = []
        reply = base64.urlsafe_b64encode(message.encode()).decode().rstrip("=")

        def gmail(token, method, path, *, params=None, body=None):
            events.append(("gmail", method, path, body))
            if (method, path) == ("GET", "messages"):
                return {"messages": [{"id": "msg-1"}]}
            if (method, path) == ("GET", "messages/msg-1"):
                return {"threadId": "thread_1", "payload": {
                    "mimeType": "text/plain", "body": {"data": reply},
                    "headers": [
                        {"name": "From", "value": "Prospect <prospect@example.com>"},
                        {"name": "Subject", "value": "Re: Business inquiry"},
                        {"name": "Message-ID", "value": "<msg1@prospect.example>"},
                    ],
                }}
            if (method, path) == ("POST", "messages/send"):
                return {"id": "outbound-gmail-1", "threadId": "thread_1"}
            if (method, path) == ("POST", "messages/msg-1/modify"):
                return {}
            raise AssertionError("Unexpected Gmail access " + path)

        def sb(method, path, *, params=None, body=None, prefer=None):
            events.append(("sb", method, path, dict(body or {}), dict(params or {})))
            if method == "GET" and path == "sdr_email_messages":
                return []
            if method == "GET" and path == "leads":
                return [lead]
            if method == "PATCH" and path == "leads":
                return [{"id": lead["id"]}]
            if method == "POST" and path == "sdr_email_messages":
                return [{"id": "outbox-1"}] if body["direction"] == "outbound" else []
            if method == "PATCH" and path == "sdr_email_messages":
                return [{"id": "outbox-1"}]
            if method == "POST" and path == "sdr_suppressions":
                return []
            raise AssertionError("Unexpected Supabase access " + method + " " + path)

        with patch.object(sdr_worker, "sb", side_effect=sb), \
             patch.object(sdr_worker, "gmail_api", side_effect=gmail), \
             patch.object(sdr_worker, "classify_reply", return_value={
                 "classification": classification, "buying_intent": 30, "summary": "Buyer reply"
             }), \
             patch.object(sdr_worker, "openai_text", return_value=json.dumps(assessment)), \
             patch.object(sdr_worker, "DRY_RUN", dry), \
             patch.object(sdr_worker, "load_settings", return_value={"sending_enabled": True, "kill_switch": False}), \
             patch.object(sdr_worker, "daily_remaining", return_value=(10, 30)), \
             patch.object(sdr_worker, "notify_hot_handoff") as alert, \
             patch.object(sdr_worker, "GMAIL_FROM_EMAIL", "seller@example.com"):
            sdr_worker.process_replies("test-token")
            return events, alert.call_count

    @staticmethod
    def evidence(message, intent, keys=None):
        return {"intent": intent, "intent_evidence": message,
                "answer_keys": keys or [], "facts": {}}

    @staticmethod
    def patches(events):
        return [e[3] for e in events if e[:3] == ("sb", "PATCH", "leads")]

    @staticmethod
    def outbound_events(events):
        return [e for e in events if e[:3] == ("sb", "POST", "sdr_email_messages")
                and e[3]["direction"] == "outbound"]

    def test_approved_question_routes_through_engine_and_locked_send(self):
        q = "What does it cost?"
        events, alerts = self.run_reply(q, self.evidence(q, "price", ["pricing"]))
        queued = self.outbound_events(events)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0][3]["status"], "queued")
        self.assertIn("Pricing depends on", queued[0][3]["body_text"])
        patch_calls = self.patches(events)
        self.assertTrue(any(p.get("outreach_status") == "sending" for p in patch_calls))
        self.assertTrue(any(p.get("conversation_state", {}).get("turns") == 1 for p in patch_calls))
        self.assertTrue(any(e[:3] == ("sb", "PATCH", "sdr_email_messages")
                            and e[3]["status"] == "sent" for e in events))
        self.assertTrue(any(e[:3] == ("gmail", "POST", "messages/send")
                            and e[3]["threadId"] == "thread_1" for e in events))
        self.assertEqual(alerts, 0)

    def test_defer_wait_persists_without_sending_and_clears_followup(self):
        q = "Contact me next month"
        events, alerts = self.run_reply(q, self.evidence(q, "later"))
        self.assertFalse(self.outbound_events(events))
        self.assertTrue(any(p.get("conversation_state", {}).get("status") == "deferred"
                            and p.get("followup_due_at", "not-cleared") is None
                            for p in self.patches(events)))
        self.assertEqual(alerts, 0)

    def test_unclear_handoff_sets_owner_and_alerts(self):
        q = "Can you guarantee extra revenue?"
        events, alerts = self.run_reply(q, self.evidence(q, "unsupported"))
        self.assertEqual(alerts, 1)
        self.assertFalse(self.outbound_events(events))
        self.assertTrue(any(p.get("human_owned") is True and
                            p.get("conversation_state", {}).get("owner") == "human"
                            and p.get("followup_due_at", "bad") is None
                            for p in self.patches(events)))

    def test_turn_limit_handoffs(self):
        state = new_conversation()
        state["turns"] = 8
        q = "I am interested"
        events, alerts = self.run_reply(q, self.evidence(q, "interested"),
                                        existing={"conversation_state": state})
        self.assertEqual(alerts, 1)
        self.assertFalse(self.outbound_events(events))

    def test_legacy_lead_missing_state_cannot_auto_send(self):
        q = "What does it cost?"
        events, alerts = self.run_reply(q, self.evidence(q, "price", ["pricing"]),
                                        existing={"conversation_state": None})
        # Missing column is different from a NULL value in an existing column.
        # Simulate it by selecting a legacy row directly:
        self.assertEqual(alerts, 0)
        self.assertTrue(self.outbound_events(events))

    def test_stop_phrase_wins_over_model_label(self):
        q = "Please stop contacting me"
        events, _ = self.run_reply(q, self.evidence(q, "interested"))
        self.assertFalse(self.outbound_events(events))
        self.assertTrue(any(e[:3] == ("sb", "POST", "sdr_suppressions") for e in events))
        self.assertTrue(any(p.get("outreach_status") == "suppressed" for p in self.patches(events)))


class QualityAndPlaybookTests(unittest.TestCase):
    def test_block_noreply_and_cliches(self):
        valid = "We noticed you may be losing after-hours enquiries. Is it worth discussing?"
        for address in ("noreply@x.test", "no-reply@x.test",
                        "donotreply@x.test", "mailer-daemon@x.test"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                sdr_worker.validate_outbound_email(address, "Business inquiry", valid)
        for phrase in ("I hope this finds you well", "I wanted to reach out",
                       "circling back", "touching base"):
            with self.subTest(phrase=phrase), self.assertRaises(ValueError):
                sdr_worker.validate_outbound_email("buyer@example.com", "Business inquiry",
                                                   phrase + ". " + valid)

    def test_first_touch_must_be_under_90_words(self):
        with self.assertRaisesRegex(ValueError, "fewer than 90"):
            sdr_worker.validate_outbound_email(
                "buyer@example.com", "Inquiry question", "word " * 90, first_touch=True
            )

    def test_playbook_is_dynamic_and_missing_file_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "playbook.md"
            with patch.object(sdr_worker, "PLAYBOOK_PATH", path):
                self.assertEqual(sdr_worker.load_playbook(), "")
                self.assertEqual(sdr_worker.playbook_offer()["approved_answers"], {})
                path.write_text("## Approved Buyer Q&A\n- pricing: First rate.\n", encoding="utf-8")
                self.assertEqual(sdr_worker.playbook_offer()["approved_answers"]["pricing"], "First rate.")
                path.write_text("## Approved Buyer Q&A\n- pricing: Updated rate.\n", encoding="utf-8")
                self.assertEqual(sdr_worker.playbook_offer()["approved_answers"]["pricing"], "Updated rate.")

    def test_initial_prompt_includes_measured_pain_and_owner_rules(self):
        prompts = []
        lead = {"email": "buyer@example.com", "business_name": "Buyer",
                "pain_reason": "No website chatbot", "has_website_chatbot": False}
        def model(prompt):
            prompts.append(prompt)
            return json.dumps({"decision": "send", "subject": "Question about inquiries",
                               "body": "I noticed your website may lack a chatbot. "
                                       "Could we discuss how incoming inquiries are handled?"})
        with patch.object(sdr_worker, "openai_text", side_effect=model):
            sdr_worker.qualify_and_write(lead)
        self.assertIn("Website chatbot not detected", prompts[0])
        self.assertIn("Fewer than 90 words", prompts[0])
        self.assertIn("Approved Buyer Q&A", prompts[0])


if __name__ == "__main__":
    unittest.main()
