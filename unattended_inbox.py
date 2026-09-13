"""Bounded unattended inbox polling; every check writes private evidence."""
import os
import time
from datetime import datetime, timezone
import inbox_conversation as flow
from response_audit import record

def main():
    check_id = os.environ['INBOX_CHECK_ID']
    deadline = time.monotonic() + 570
    record(check_id, 'worker_started', poll_seconds=30)
    while time.monotonic() < deadline:
        config = flow.read_config(check_id)
        settings = flow.worker.load_settings()
        if not flow.allowed(config, settings):
            record(check_id, 'worker_blocked', status=config.get('status'), owner=config.get('owner'),
                   enabled=config.get('enabled'), replies_sent=config.get('replies_sent'),
                   reply_cap=config.get('reply_cap'), expires_at=config.get('expires_at'))
            return
        if config['status'] != 'active':
            record(check_id, 'worker_blocked', status=config['status'])
            return
        try:
            flow.run_test_conversation(check_id, audit=lambda event, **data: record(check_id,event,**data))
        except Exception as exc:
            record(check_id, 'worker_error', error_type=type(exc).__name__)
            raise RuntimeError('Unattended inbox failed; inspect private events') from None
        time.sleep(30)
    record(check_id, 'worker_finished')

if __name__ == '__main__': main()
