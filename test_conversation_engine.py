import json
import unittest

from conversation_engine import advance, assess, new_conversation, set_owner

OFFER = {"price_text": "The sample package is USD 350.",
         "details_text": "The sample package includes posts and captions."}


class ConversationTests(unittest.TestCase):
    def step(self, text="Interested", intent="interested", facts=None, state=None, **kw):
        return advance(state or new_conversation(), kw.pop("message_id", "m1"), text,
                       {"intent": intent, "intent_evidence": text, "facts": facts or {}},
                       kw.pop("offer", OFFER), **kw)

    def test_price_answer_uses_approved_copy_and_asks_one_question(self):
        s, d = self.step("How much?", "price")
        self.assertIn(OFFER["price_text"], d["body"])
        self.assertEqual(d["body"].count("?"), 1)
        self.assertNotIn("budget", s["facts"])

    def test_no_approved_price_hands_off(self):
        _, d = self.step("How much?", "price", offer={})
        self.assertEqual(d["action"], "handoff")
        self.assertEqual(d["body"], "")

    def test_details_use_approved_copy(self):
        _, d = self.step("What is included?", "details")
        self.assertIn(OFFER["details_text"], d["body"])

    def test_multiple_messages_reach_qualified_handoff(self):
        s = new_conversation()
        for i, (key, quote) in enumerate([
            ("need", "We need regular posts"), ("budget", "Our budget is USD 350"),
            ("timing", "Start next month"), ("authority", "I approve purchases")]):
            s, d = self.step(quote, facts={key: quote}, state=s, message_id=str(i))
            self.assertEqual(d["action"], "draft")
        s, d = self.step("Please proceed", "ready", state=s, message_id="ready")
        self.assertEqual(d["action"], "handoff")
        self.assertEqual(len(d["facts"]), 4)
        self.assertEqual(d["payment_status"], "not_verified")

    def test_interest_is_not_qualified_buyer(self):
        _, d = self.step("Send invoice", "ready")
        self.assertEqual(d["action"], "draft")
        self.assertEqual(d["facts"], {})

    def test_fabricated_fact_is_discarded(self):
        s, _ = self.step(facts={"budget": "USD 900"})
        self.assertNotIn("budget", s["facts"])

    def test_explicit_unknown_budget_is_not_confirmed(self):
        s, _ = self.step("No budget yet", facts={"budget": "No budget yet"})
        self.assertNotIn("budget", s["facts"])

    def test_unknown_budget_can_retract_old_value(self):
        s, _ = self.step("USD 350", facts={"budget": "USD 350"})
        s, _ = advance(s, "m2", "Budget is unknown now", {
            "intent": "interested", "intent_evidence": "Budget is unknown now",
            "unknown_fields": ["budget"], "unknown_evidence": "Budget is unknown now"}, OFFER)
        self.assertNotIn("budget", s["facts"])

    def test_objection_does_not_offer_discount(self):
        _, d = self.step("Too expensive", "objection")
        self.assertEqual(d["action"], "draft")
        self.assertNotIn("discount", d["body"])

    def test_delay_waits_without_inventing_schedule(self):
        s, d = self.step("Contact me next month", "later")
        self.assertEqual(d["action"], "wait")
        self.assertEqual(s["defer_until"], "Contact me next month")
        self.assertEqual(d["body"], "")

    def test_stop_overrides_incorrect_ai_classification(self):
        s, d = self.step("Please unsubscribe", "interested")
        self.assertEqual(d["action"], "stop")
        s = set_owner(s, "sdr")
        _, d = self.step(state=s, message_id="m2")
        self.assertEqual(d["action"], "stop")

    def test_human_takeover_prevents_further_drafts(self):
        s = set_owner(new_conversation(), "human")
        _, d = self.step(state=s)
        self.assertEqual(d["action"], "handoff")
        self.assertEqual(d["body"], "")

    def test_owner_can_return_to_sdr(self):
        s = set_owner(set_owner(new_conversation(), "human"), "sdr")
        _, d = self.step(state=s)
        self.assertEqual(d["action"], "draft")

    def test_duplicate_message_does_not_generate_second_draft(self):
        s, _ = self.step()
        new, d = self.step(state=s)
        self.assertEqual(d["action"], "ignore")
        self.assertEqual(new, s)

    def test_pause_does_not_consume_message(self):
        s = new_conversation()
        new, d = self.step(state=s, paused=True)
        self.assertEqual(s, new)
        self.assertEqual(d["action"], "wait")

    def test_unsupported_and_human_requests_handoff(self):
        for intent in ("human", "unsupported", "unclear"):
            with self.subTest(intent=intent):
                _, d = self.step("Can you guarantee sales?", intent)
                self.assertEqual(d["action"], "handoff")

    def test_missing_or_malformed_ai_output_hands_off(self):
        for a in ({}, None, [], {"intent": "ready", "intent_evidence": "made up"}):
            _, d = advance(new_conversation(), "m1", "Hello", a, OFFER)
            self.assertEqual(d["action"], "handoff")

    def test_reader_failure_is_handled(self):
        self.assertEqual(assess("Hi", [], lambda _: "not json"), {})
        self.assertEqual(assess("Hi", [], lambda _: "[]"), {})

    def test_no_endless_repetition(self):
        s = new_conversation()
        for i in range(3):
            s, d = self.step(state=s, message_id=str(i))
        self.assertEqual(d["action"], "handoff")

    def test_saved_state_roundtrip(self):
        s, _ = self.step("Need posts", facts={"need": "Need posts"})
        s = json.loads(json.dumps(s))
        s, d = self.step("USD 350", facts={"budget": "USD 350"}, state=s, message_id="m2")
        self.assertIn("need", s["facts"])
        self.assertIn("When would you want", d["body"])

    def test_input_state_not_mutated(self):
        s = new_conversation()
        self.step(state=s)
        self.assertEqual(s, new_conversation())


if __name__ == "__main__":
    unittest.main()
