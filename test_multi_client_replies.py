"""Offline regression tests for central inbox processing of many customer replies.

No Gmail sends, real accounts, Supabase writes, or AI calls.
"""
import base64
import unittest
from unittest.mock import patch
import sdr_worker as worker


def mail(mid, sender, thread, to="aiagentsutomations01@gmail.com", labels=None):
    text = "I would like a quote and more information please."
    return {
        "id": mid,
        "threadId": thread,
        "labelIds": labels or [],
        "payload": {
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(text.encode()).decode()},
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": to},
                {"name": "Subject", "value": "Re: Your offer"},
                {"name": "Message-ID", "value": f"<{mid}@example.com>"},
            ],
        },
    }


class MultiClientReplyTests(unittest.TestCase):
    def setUp(self):
        self.messages = [
            mail(f"m{i}", f"client{i}@example.com", f"thread{i}",
                 labels=["SPAM"] if i % 10 == 0 else ([] if i % 7 == 0 else ["INBOX", "UNREAD"]))
            for i in range(50)
        ]
        self.processed_ids = set()
        self.handled = []
        self.read_modifications = []
        self.fail_id = None
        self.mail_index = {m["id"]: m for m in self.messages}
        self.mail_calls = []

        def gmail(token, method, path, *, params=None, body=None):
            if method == "GET" and path == "messages":
                self.mail_calls.append(dict(params))
                start = int(params.get("pageToken", "0"))
                end = min(start + 50, len(self.messages))
                return {
                    "messages": [{"id": m["id"]} for m in self.messages[start:end]],
                    **({"nextPageToken": str(end)} if end < len(self.messages) else {}),
                }
            if method == "GET" and path.startswith("messages/"):
                return self.mail_index[path.split("/")[-1]]
            if method == "POST" and path.endswith("/modify"):
                self.read_modifications.append(path.split("/")[1])
                return {}
            raise AssertionError((method, path, params, body))

        def sb(method, table, *, params=None, body=None, prefer=None):
            if method == "GET" and table == "sdr_email_messages":
                msg_id = params.get("gmail_message_id", "")[3:]
                if params.get("direction") == "eq.inbound":
                    return [{"id": "record"}] if msg_id in self.processed_ids else []
                if params.get("direction") == "eq.outbound":
                    lead_id = params.get("lead_id", "")[3:]
                    thread_id = params.get("gmail_thread_id", "")[3:]
                    return [{"id": "sent"}] if thread_id == lead_id.replace("lead", "thread") else []
            if method == "GET" and table == "leads":
                email = params.get("email", "")[3:]
                return [{"id": "lead" + email.split("@")[0].replace("client", ""),
                         "email": email, "outreach_status": "contacted"}] if email.startswith("client") else []
            raise AssertionError((method, table, params))

        def handler(token, lead, sender, subject, body, mid, thread, headers):
            if mid == self.fail_id:
                raise RuntimeError("temporary AI 503")
            self.handled.append((lead["id"], sender, mid, thread))
            self.processed_ids.add(mid)

        self.patches = [
            patch.object(worker, "gmail_api", side_effect=gmail),
            patch.object(worker, "sb", side_effect=sb),
            patch.object(worker, "handle_prospect_reply", side_effect=handler),
            patch.object(worker, "is_delivery_failure", return_value=False),
            patch.object(worker, "GMAIL_FROM_EMAIL", "aiagentsutomations01@gmail.com"),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])

    def test_50_independent_clients_including_spam_and_read(self):
        worker.process_replies("not-a-real-token")
        self.assertEqual(len(self.handled), 50)
        self.assertEqual(len(set(mid for _, _, mid, _ in self.handled)), 50)
        self.assertEqual(len(self.read_modifications), 50)
        self.assertEqual(self.mail_calls[0]["maxResults"], 50)
        query = self.mail_calls[0]["q"]
        self.assertIn("in:anywhere", query)
        self.assertIn("-in:trash", query)
        self.assertNotIn("is:unread", query)
        self.assertNotIn("in:inbox", query)
        worker.process_replies("not-a-real-token")
        self.assertEqual(len(self.handled), 50, "Already processed replies must not be handled twice")

    def test_page_two_is_not_dropped(self):
        self.messages.extend([
            mail(f"m{i}", f"client{i}@example.com", f"thread{i}")
            for i in range(50, 77)
        ])
        self.mail_index = {m["id"]: m for m in self.messages}
        worker.process_replies("not-a-real-token")
        self.assertEqual(len(self.handled), 77)
        self.assertEqual(len(self.mail_calls), 2)

    def test_one_model_failure_does_not_stop_others(self):
        self.fail_id = "m12"
        worker.process_replies("not-a-real-token")
        self.assertEqual(len(self.handled), 49)
        self.assertNotIn("m12", self.read_modifications)
        self.assertNotIn("m12", self.processed_ids)
        self.fail_id = None
        worker.process_replies("not-a-real-token")
        self.assertEqual(len(self.handled), 50)
        self.assertEqual(len(self.processed_ids), 50)

    def test_wrong_thread_and_wrong_destination_are_not_answered(self):
        self.messages[1]["threadId"] = "someone-elses-thread"
        self.messages[2]["payload"]["headers"][1]["value"] = "someone-else@example.com"
        worker.process_replies("not-a-real-token")
        self.assertEqual(len(self.handled), 48)
        self.assertNotIn("m1", self.processed_ids)
        self.assertNotIn("m2", self.processed_ids)


if __name__ == "__main__":
    unittest.main()
