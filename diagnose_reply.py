"""Inspect current private test interpretation without sending or changing ownership."""
import os
import inbox_conversation as f
from conversation_engine import assess

def main():
    check_id = os.environ['INBOX_CHECK_ID']
    c = f.read_config(check_id)
    if c['enabled'] or c['status'] != 'review':
        return
    history = c['state']['history']
    if not history or history[-1]['role'] != 'prospect':
        return
    message = history[-1]['text']
    a = assess(message, history[:-1], f.model_reader, c['offer'])
    decision = dict(c.get('last_decision') or {})
    decision['private_assessment'] = a
    f.worker.sb('PATCH', f.TABLE, params={'check_id':'eq.'+check_id,'version':'eq.'+str(c['version']),'enabled':'eq.false'},body={'last_decision':decision})
    print('PRIVATE_DIAGNOSIS_SAVED no_email_sent=true')

if __name__ == '__main__':
    main()
