import unittest
from conversation_engine import advance, new_conversation, extraction_prompt
class MultiAnswerTests(unittest.TestCase):
    def setUp(self):
        self.offer = {'approved_answers':{'delivery':'Timing needs confirmation.', 'inputs':'We need your logo.'}}
    def test_combines_without_qualification(self):
        _, d = advance(new_conversation(), 'm', 'When and what do you need?',
            {'intent':'details','intent_evidence':'When and what do you need?', 'answer_keys':['delivery','inputs','delivery']}, self.offer)
        self.assertEqual(d['body'], 'Timing needs confirmation.\n\nWe need your logo.')
    def test_no_key_does_not_invent_generic_answer(self):
        for keys in ([], ['missing'], 'delivery'):
            _, d = advance(new_conversation(), 'm', 'When?', {'intent':'details','intent_evidence':'When?', 'answer_keys':keys}, self.offer)
            self.assertEqual(d['action'], 'handoff')
            self.assertEqual(d['body'], '')
    def test_unsupported_wins(self):
        _, d = advance(new_conversation(), 'm', 'Guarantee delivery', {'intent':'unsupported','intent_evidence':'Guarantee delivery', 'answer_keys':['delivery']}, self.offer)
        self.assertEqual(d['action'], 'handoff')
    def test_schema_includes_keys(self):
        self.assertIn('"answer_keys":', extraction_prompt('When?', [], self.offer))
