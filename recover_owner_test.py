"""One-time CAS recovery of the exact unsent provider-failed owner test.
Never extends expiry or reply limits. Never sends email itself.
"""
import os
from copy import deepcopy
from datetime import datetime, timezone
import inbox_conversation as flow

CHECK = 'eaeb8f84-e86f-470a-bae3-ac124d86c2c3'
MESSAGE = '1a098df16745f66b'
TEXT = 'Thank you, I will check it out.'

def main():
    if os.environ.get('INBOX_CHECK_ID') != CHECK:
        raise RuntimeError('Recovery scope mismatch')
    c = flow.read_config(CHECK)
    if not c or c['version'] != 1:
        print('RECOVERY not_needed')
        return
    settings = flow.worker.load_settings()
    if (c['enabled'] or c['owner'] != 'human' or c['status'] != 'review'
            or c['replies_sent'] != 0 or c['reply_cap'] != 3 or c.get('last_output_id')
            or datetime.fromisoformat(c['expires_at'].replace('Z', '+00:00')) <= datetime.now(timezone.utc)
            or settings.get('sending_enabled') is not False or settings.get('kill_switch') is not False):
        raise RuntimeError('Recovery guards failed')
    if c.get('last_decision', {}).get('reason') != 'Could not reliably understand the reply':
        raise RuntimeError('Recovery reason changed')
    check = flow.worker.sb('GET', 'sdr_inbox_checks', params={'id': 'eq.'+CHECK})[0]
    if (check['recipient'] != 'mtiameer4@gmail.com' or check['expected_sender'] != 'aiagentsutomations01@gmail.com'
            or check['status'] != 'sent' or check['gmail_thread_id'] != '1a098de5f27a8cc6'):
        raise RuntimeError('Recovery identity mismatch')
    token = flow.worker.gmail_token()
    profile = flow.worker.gmail_api(token, 'GET', 'profile')
    if profile.get('emailAddress', '').lower() != check['expected_sender']:
        raise RuntimeError('Recovery sender mismatch')
    thread = flow.worker.gmail_api(token, 'GET', 'threads/'+check['gmail_thread_id'], params={'format':'full'})
    if {m['id'] for m in thread.get('messages', [])} != {check['gmail_message_id'], MESSAGE}:
        raise RuntimeError('Thread changed; manual review required')
    replies = flow.customer_messages(thread, check)
    if len(replies) != 1 or replies[0]['id'] != MESSAGE or flow.latest_text(replies[0]) != TEXT:
        raise RuntimeError('Recovery reply mismatch')
    state = deepcopy(c['state'])
    entry = {'role':'prospect', 'text':TEXT}
    if state['processed'] != [MESSAGE] or state['history'].count(entry) != 1 or state['history'][-1] != entry:
        raise RuntimeError('Recovery history changed')
    state['processed'].remove(MESSAGE)
    state['history'].pop()
    state.update(owner='sdr', status='active')
    result = flow.worker.sb('PATCH', flow.TABLE,
        params={'check_id':'eq.'+CHECK, 'version':'eq.1', 'enabled':'eq.false', 'status':'eq.review',
                'owner':'eq.human', 'expires_at':'gt.'+flow.worker.now()},
        body={'state':state, 'owner':'sdr', 'status':'active', 'enabled':True, 'version':2,
              'updated_at':flow.worker.now()}, prefer='return=representation')
    if not result:
        raise RuntimeError('Recovery concurrent change; no retry')
    print('RECOVERY verified_no_reply_sent=true restored_exact_message=true limits_preserved=true')

if __name__ == '__main__':
    main()
