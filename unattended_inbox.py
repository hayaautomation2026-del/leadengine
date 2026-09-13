"""Bounded unattended inbox polling; every check writes private evidence."""
import os
import re
import time
from datetime import datetime, timezone
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


def main():
    check_id = os.environ['INBOX_CHECK_ID']
    deadline = time.monotonic() + 18000
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
