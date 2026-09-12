# Controlled AI email reply test

The dedicated inbox conversation worker reads the single owner-authorized Gmail
thread recorded in `sdr_inbox_checks`, interprets the customer's current reply
using Gemini, and sends a response selected by `conversation_engine` from the
configured test offer. It preserves Gmail thread ID, In-Reply-To and References.

The real scheduled SDR invokes this worker only when INBOX_TEST_CHECK_ID is set.
The private configuration permits at most five replies and expires 24 hours after
activation. It requires normal prospect sending to stay OFF. The public dashboard
is still mock-only and its buttons do not control this private test.

Conversation history, quotes supporting qualification, decisions and outgoing
message IDs persist in an RLS-protected table accessible only to the service role.
A conditional version/status update claims work before generation. Another check
before sending verifies owner control, test expiry and new thread messages. Send
uncertainty disables the test for review; it is not automatically retried.

Stop, handoff and delay requests disable further test replies. Owner takeover can
also set owner=human or enabled=false in the private test configuration. A manual
sender message newer than the pending customer reply is treated as takeover.

The offer is explicitly an example service: posts and captions. No quantity,
price, guarantee or delivery commitment is invented. These tests do not establish
production inbox placement or qualification accuracy across real prospects.

Validation: 13 controlled-reply tests plus existing conversation, inbox, follow-up
and owner-control suites (51 tests total). The dedicated CI job runs the 13 tests
before attempting an authorized real reply.
