"""Private model preview. No Gmail calls, no conversation reset, no sending."""
from copy import deepcopy
import inbox_conversation as f
from conversation_engine import assess, advance

CHECK = '07dfa044-4beb-48ec-82e9-b66dd8b7e909'
def main():
    c = f.read_config(CHECK)
    history = c['state']['history']
    index = max(i for i, e in enumerate(history) if e['role'] == 'prospect')
    message = history[index]['text']
    state = deepcopy(c['state'])
    state.update(owner='sdr', status='active', history=history[:index], processed=[], asked=[], turns=0)
    assessment = assess(message, state['history'], f.model_reader, c['offer'])
    _, decision = advance(state, 'preview-only', message, assessment, c['offer'])
    last = deepcopy(c.get('last_decision') or {})
    last['preview'] = {'assessment':assessment, 'decision':decision, 'sent':False}
    saved = f.worker.sb('PATCH', f.TABLE, params={'check_id':'eq.'+CHECK,'version':'eq.'+str(c['version'])},
                       body={'last_decision':last}, prefer='return=representation')
    if not saved:
        raise RuntimeError('Preview not saved: conversation changed')
    print('BUYER_PREVIEW_SAVED sent=false')
if __name__ == '__main__': main()
