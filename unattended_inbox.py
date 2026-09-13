"""Bounded unattended inbox polling; every check writes private evidence."""
import os
import re
import time
from datetime import datetime, timezone
from email.utils import parseaddr, getaddresses

import inbox_conversation as flow
from response_audit import record

POLL_SECONDS = 30
TRANSIENT_RETRY_SECONDS = (5, 15, 30)


def is_transient(exc):
    """Retry only infrastructure-style failures; let code/config bugs fail loudly."""
    text = str(exc)
    if "Supabase" not in text:
        return False
    match = re.search(r"failed\s+(\d{3})", text)
    if not match:
        return False
    status = int(match.group(1))
    return status == 429 or 500 <= status <= 599


def with_transient_retry(check_id, operation, fn):
    """Run one external operation with short bounded backoff."""
    attempts = 1 + len(TRANSIENT_RETRY_SECONDS)
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if not is_transient(exc) or attempt == attempts:
                raise
            delay = TRANSIENT_RETRY_SECONDS[attempt - 1]
            record(
                check_id,
                "worker_retry",
                operation=operation,
                attempt=attempt,
                delay_seconds=delay,
                error_type=type(exc).__name__,
                error=str(exc)[:300],
            )
            time.sleep(delay)


def recipient_addresses(headers):
    values = []
    for name in ("To", "Cc", "Delivered-To", "X-Original-To"):
        value = flow.worker.header_value(headers, name)
        if value:
            values.append(value)
    return {addr.lower() for _, addr in getaddresses(values) if addr}


def robust_customer_messages(thread, check):
    """Accept the exact test sender when Gmail rewrites recipient headers."""
    matches = []
    expected_from = check["recipient"].lower()
    expected_to = check["expected_sender"].lower()
    for msg in thread.get("messages", []):
        headers = msg.get("payload", {}).get("headers", [])
        sender = parseaddr(flow.worker.header_value(headers, "From"))[1].lower()
        recipients = recipient_addresses(headers)
        if sender == expected_from and expected_to in recipients:
            matches.append(msg)
    return sorted(matches, key=lambda m: int(m.get("internalDate", 0)))


def relink_controlled_thread(check_id, config):
    """Recover only the controlled test if Gmail places a valid reply in another thread."""
    checks = flow.worker.sb("GET", "sdr_inbox_checks", params={
        "select": "id,recipient,expected_sender,subject,gmail_thread_id,sent_at",
        "id": f"eq.{check_id}",
        "status": "eq.sent",
        "limit": "1",
    }) or []
    if not checks:
        return
    check = checks[0]
    if not all(check.get(k) for k in ("recipient", "expected_sender", "gmail_thread_id", "sent_at")):
        return

    token = flow.worker.gmail_token()
    query = f'from:{check["recipient"]} newer_than:2d'
    found = flow.worker.gmail_api(token, "GET", "messages", params={"q": query, "maxResults": 25}) or {}
    processed = set((config.get("state") or {}).get("processed") or [])
    sent_cutoff_ms = int(datetime.fromisoformat(str(check["sent_at"]).replace("Z", "+00:00")).timestamp() * 1000)
    candidates = []

    for item in found.get("messages", []):
        if item.get("id") in processed:
            continue
        msg = flow.worker.gmail_api(token, "GET", f'messages/{item["id"]}', params={"format": "metadata"})
        if int(msg.get("internalDate", 0)) <= sent_cutoff_ms:
            continue
        headers = msg.get("payload", {}).get("headers", [])
        sender = parseaddr(flow.worker.header_value(headers, "From"))[1].lower()
        recipients = recipient_addresses(headers)
        if sender != check["recipient"].lower():
            continue
        if check["expected_sender"].lower() not in recipients:
            continue
        candidates.append(msg)

    record(check_id, "gmail_search", found=len(found.get("messages", [])), candidates=len(candidates))
    if not candidates:
        return

    newest = max(candidates, key=lambda m: int(m.get("internalDate", 0)))
    new_thread_id = newest.get("threadId")
    if not new_thread_id:
        return
    if new_thread_id != check["gmail_thread_id"]:
        flow.worker.sb("PATCH", "sdr_inbox_checks", params={"id": f"eq.{check_id}"}, body={
            "gmail_thread_id": new_thread_id,
        }, prefer="return=minimal")
        record(check_id, "thread_relinked", old_thread_id=check["gmail_thread_id"],
               new_thread_id=new_thread_id, message_id=newest.get("id"))


def main():
    check_id = os.environ['INBOX_CHECK_ID']
    deadline = time.monotonic() + 18000
    flow.customer_messages = robust_customer_messages
    record(check_id, 'worker_started', poll_seconds=POLL_SECONDS)
    while time.monotonic() < deadline:
        try:
            config = with_transient_retry(check_id, 'read_config', lambda: flow.read_config(check_id))
            settings = with_transient_retry(check_id, 'load_settings', flow.worker.load_settings)

            if not flow.allowed(config, settings):
                record(check_id, 'worker_blocked', status=config.get('status'), owner=config.get('owner'),
                       enabled=config.get('enabled'), replies_sent=config.get('replies_sent'),
                       reply_cap=config.get('reply_cap'), expires_at=config.get('expires_at'))
                if (datetime.fromisoformat(config['expires_at'].replace('Z','+00:00')) > datetime.now(timezone.utc)
                        and config['replies_sent'] < config['reply_cap'] and config['status'] != 'stopped'):
                    time.sleep(POLL_SECONDS)
                    continue
                return

            if config['status'] != 'active':
                record(check_id, 'worker_blocked', status=config['status'])
                return

            with_transient_retry(check_id, 'relink_controlled_thread',
                                 lambda: relink_controlled_thread(check_id, config))
            with_transient_retry(
                check_id,
                'run_test_conversation',
                lambda: flow.run_test_conversation(
                    check_id,
                    audit=lambda event, **data: record(check_id, event, **data),
                ),
            )
        except Exception as exc:
            record(
                check_id,
                'worker_error',
                error_type=type(exc).__name__,
                error=str(exc)[:300],
            )
            raise RuntimeError('Unattended inbox failed; inspect private events') from None

        time.sleep(POLL_SECONDS)
    record(check_id, 'worker_finished')


if __name__ == '__main__':
    main()
