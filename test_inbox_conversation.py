import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
import json
import unittest
from unittest.mock import patch

import inbox_conversation as flow
from conversation_engine import new_conversation

CHECK = "d2d77c87-ec8a-4878-a5dd-e6e9509a257b"


def message(body, sender="customer@example.com", msg_id="in1", date=1000):
    return {"id": msg_id, "internalDate": str(date), "payload": {"mimeType": "text/plain",
        "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}, "headers": [
            {"name": "From", "value": sender}, {"name": "To", "value": "sender@example.com"},
            {"name": "Subject", "value": "Re: Inbox test"}, {"name": "Message-ID", "value": f"<{msg_id}@example.com>"}]}}


class ControlledReplyTests(unittest.TestCase):
    def setUp(self):
        self.config = {"enabled": True, "owner": "sdr", "status": "active", "version": 0,
            "replies_sent": 0, "reply_cap": 5,
            "expires_at": (datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),
            "state": new_conversation(), "offer": {"details_text": "This test offer covers posts and captions."}}
        self.check = {"id": CHECK, "recipient": "customer@example.com", "expected_sender": "sender@example.com",
                      "gmail_thread_id": "thread1"}
        self.thread = {"messages": [message("What is included?")]}
        self.settings = {"sending_enabled": False, "kill_switch": False}
        self.sent = []
        self.status_reads = 0
        self.on_second_settings = None
        self.ai_failure = False
        self.send_failure = False
        self.new_reply = False
        self.claim_failure = False
        def db(method, table, *, params=None, body=None, prefer=None):
            self.assertIn(table, {flow.TABLE, "sdr_inbox_checks"})
            if table == "sdr_inbox_checks":
                self.assertEqual(method, "GET")
                return [deepcopy(self.check)]
            if method == "GET": return [deepcopy(self.config)]
            self.assertEqual(method, "PATCH")
            if self.claim_failure and params.get("status") == "eq.active": return []
            for key in ("version", "status", "owner", "enabled"):
                if key in params:
                    expected = params[key][3:]
                    actual = str(self.config[key]).lower() if isinstance(self.config[key], bool) else str(self.config[key])
                    if expected != actual: return []
            self.config.update(deepcopy(body))
            return [deepcopy(self.config)]
        def settings():
            self.status_reads += 1
            if self.status_reads >= 2 and self.on_second_settings:
                self.on_second_settings()
            return deepcopy(self.settings)
        def gmail(token, method, path, *, params=None, body=None):
            if method == "POST":
                self.assertEqual(path, "messages/send")
                self.sent.append(body)
                if self.send_failure: raise TimeoutError()
                return {"id": "out1", "threadId": "thread1"}
            self.assertEqual(method, "GET")
            if path == "profile": return {"emailAddress": "sender@example.com"}
            self.assertEqual(path, "threads/thread1")
            result = deepcopy(self.thread)
            if params.get("format") == "minimal" and self.new_reply:
                result["messages"].append(message("Wait", msg_id="new", date=2000))
            return result
        def ai(prompt):
            if self.ai_failure: raise RuntimeError("AI failed")
            return json.dumps({"intent": "details", "intent_evidence": "What is included?", "facts": {}})
        self.patches = [patch.object(flow.worker, "sb", side_effect=db),
            patch.object(flow.worker, "load_settings", side_effect=settings),
            patch.object(flow.worker, "gmail_api", side_effect=gmail),
            patch.object(flow.worker, "gmail_token", return_value="token"),
            patch.object(flow, "model_reader", side_effect=ai)]
        self.mocks = [p.start() for p in self.patches]
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])

    def run_flow(self): flow.run_test_conversation(CHECK)

    def test_reply_is_threaded_and_state_is_persisted(self):
        self.run_flow()
        self.assertEqual(len(self.sent), 1)
        data = self.sent[0]
        self.assertEqual(data["threadId"], "thread1")
        mime = message_from_bytes(base64.urlsafe_b64decode(data["raw"] + "=" * (-len(data["raw"]) % 4)))
        self.assertEqual(mime["To"], self.check["recipient"])
        self.assertEqual(mime["In-Reply-To"], "<in1@example.com>")
        self.assertIn("<in1@example.com>", mime["References"])
        self.assertIn("posts and captions", mime.get_payload(decode=True).decode())
        self.assertEqual(self.config["replies_sent"], 1)
        self.assertIn("in1", self.config["state"]["processed"])

    def test_duplicate_is_not_answered_again(self):
        self.run_flow(); self.run_flow()
        self.assertEqual(len(self.sent), 1)

    def test_unknown_sender_is_ignored(self):
        self.thread["messages"] = [message("What is included?", sender="stranger@example.com")]
        self.run_flow()
        self.assertEqual(self.sent, [])
        self.mocks[-1].assert_not_called()

    def test_no_reply_after_cap(self):
        self.config["replies_sent"] = 5
        self.run_flow()
        self.assertEqual(self.sent, [])

    def test_expired_test_stops(self):
        self.config["expires_at"] = "2020-01-01T00:00:00+00:00"
        self.run_flow()
        self.assertEqual(self.sent, [])

    def test_human_ownership_stops(self):
        self.config["owner"] = "human"
        self.run_flow()
        self.assertEqual(self.sent, [])

    def test_stop_request_does_not_call_model_or_send(self):
        self.thread["messages"] = [message("Stop contacting me")]
        self.run_flow()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.config["status"], "stopped")
        self.mocks[-1].assert_not_called()

    def test_timeout_disables_automatic_resend(self):
        self.send_failure = True
        with self.assertRaises(RuntimeError): self.run_flow()
        self.run_flow()
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.config["enabled"])

    def test_emergency_stop_during_ai_blocks_send(self):
        self.on_second_settings = lambda: self.settings.update(kill_switch=True)
        self.run_flow()
        self.assertEqual(self.sent, [])

    def test_atomic_claim_prevents_second_worker(self):
        self.claim_failure = True
        self.run_flow()
        self.assertEqual(self.sent, [])
        self.mocks[-1].assert_not_called()

    def test_new_message_during_ai_does_not_send_stale_draft(self):
        self.new_reply = True
        self.run_flow()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.config["status"], "active")

    def test_model_failure_does_not_send_fabricated_response(self):
        self.ai_failure = True
        self.run_flow()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.config["owner"], "human")

    def test_quoted_original_is_removed(self):
        m = message("What is included?\n\nOn Saturday, Ameer wrote:\n> Old content")
        self.assertEqual(flow.latest_text(m), "What is included?")


if __name__ == "__main__": unittest.main()
