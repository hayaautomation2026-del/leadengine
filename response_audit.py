"""Private append-only response and timing events."""
import os
import sdr_worker as worker

def record(check_id, event, **data):
    result = worker.sb('POST', 'sdr_response_events', body={
        'check_id':check_id, 'event':event, 'run_id':os.environ.get('GITHUB_RUN_ID'),
        'data':data}, prefer='return=representation')
    if not result:
        raise RuntimeError('Response audit write failed')
