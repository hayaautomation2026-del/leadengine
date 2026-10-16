"""Offline regression checks: retries/fallback never send mail or use live credentials."""
import unittest
from unittest.mock import Mock, patch, call

import requests
import sdr_worker_gemini as adapter


def response(status):
    r = Mock()
    r.status_code = status
    r.ok = status == 200
    r.text = "temporary error" if status != 200 else ""
    r.json.return_value = {"candidates": [{"content": {"parts": [{"text": "Hello"}]}}]}
    return r


class GeminiResilienceTests(unittest.TestCase):
    def setUp(self):
        self.key = patch.object(adapter, "GEMINI_API_KEY", "test-api-key")
        self.primary = patch.object(adapter, "GEMINI_MODEL", "gemini-3.8-flash")
        self.backup = patch.object(adapter, "GEMINI_FALLBACK_MODEL", "gemini-3.6-flash")
        self.jitter = patch.object(adapter.random, "uniform", return_value=0)
        self.sleep = patch.object(adapter.time, "sleep")
        for p in (self.key, self.primary, self.backup, self.jitter, self.sleep):
            p.start()
            self.addCleanup(p.stop)

    @patch.object(adapter.requests, "post")
    def test_retry_primary_before_fallback(self, post):
        post.side_effect = [response(503), response(503), response(200)]
        self.assertEqual(adapter.gemini_text("hello"), "Hello")
        self.assertEqual(post.call_count, 3)
        self.assertTrue(all("gemini-3.8-flash" in c.args[0] for c in post.call_args_list))
        self.assertEqual(adapter.time.sleep.call_args_list, [call(1), call(2)])

    @patch.object(adapter.requests, "post")
    def test_fallback_after_exhausting_503(self, post):
        post.side_effect = [response(503)] * 3 + [response(200)]
        self.assertEqual(adapter.gemini_text("hello"), "Hello")
        self.assertEqual(post.call_count, 4)
        self.assertIn("gemini-3.6-flash", post.call_args_list[-1].args[0])
        self.assertEqual(adapter.time.sleep.call_args_list, [call(1), call(2)])

    @patch.object(adapter.requests, "post")
    def test_503_on_both_models_fails_closed(self, post):
        post.side_effect = [response(503)] * 6
        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            adapter.gemini_text("hello")
        self.assertEqual(post.call_count, 6)
        self.assertEqual(adapter.time.sleep.call_count, 4)

    @patch.object(adapter.requests, "post")
    def test_bad_credentials_do_not_retry_or_fallback(self, post):
        post.return_value = response(401)
        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            adapter.gemini_text("hello")
        self.assertEqual(post.call_count, 1)
        adapter.time.sleep.assert_not_called()

    @patch.object(adapter.requests, "post")
    def test_transport_failure_can_use_backup(self, post):
        post.side_effect = [requests.Timeout()] * 3 + [response(200)]
        self.assertEqual(adapter.gemini_text("hello"), "Hello")
        self.assertIn("gemini-3.6-flash", post.call_args_list[-1].args[0])

    @patch.object(adapter.requests, "post")
    def test_empty_ai_answer_fails_closed(self, post):
        bad = response(200)
        bad.json.return_value = {"candidates": []}
        post.return_value = bad
        with self.assertRaisesRegex(RuntimeError, "no text"):
            adapter.gemini_text("hello")
        self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
