import unittest
from conversation_engine import advance, new_conversation

class WhitespaceTests(unittest.TestCase):
    def test_multiline_intent(self):
        _, decision = advance(new_conversation(), 'm', 'WHAT DETAILS\nDO YOU NEED',
            {'intent':'details','intent_evidence':'WHAT DETAILS DO YOU NEED','facts':{}},
            {'details_text':'Approved details'})
        self.assertEqual(decision['action'], 'draft')

    def test_fact_whitespace_and_retraction(self):
        state, _ = advance(new_conversation(), 'm', 'I approve\nthis myself',
            {'intent':'interested','intent_evidence':'I approve this myself',
             'facts':{'authority':'I approve this myself'}}, {})
        self.assertIn('authority', state['facts'])
        state, _ = advance(state, 'n', 'Actually I\ncannot approve',
            {'intent':'interested','intent_evidence':'Actually I cannot approve',
             'unknown_fields':['authority'], 'unknown_evidence':'I cannot approve'}, {})
        self.assertNotIn('authority', state['facts'])

    def test_changed_words_are_still_rejected(self):
        _, decision = advance(new_conversation(), 'm', 'What details?',
            {'intent':'ready','intent_evidence':'I accept the price','facts':{}}, {})
        self.assertEqual(decision['action'], 'handoff')
