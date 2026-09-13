"""Bounded synthetic generation probe. No Gmail or database access."""
import json
import os
import re
import requests

BASE = 'https://generativelanguage.googleapis.com/v1beta/'

def main():
    key = os.environ.get('GEMINI_API_KEY')
    if not key:
        print('PROBE missing_credentials')
        return 1
    headers = {'x-goog-api-key': key}
    try:
        listing = requests.get(BASE + 'models', headers=headers, timeout=20)
        print(f'PROBE list_status={listing.status_code}')
        if not listing.ok:
            return 1
        models = [m['name'] for m in listing.json().get('models', [])
                  if 'generateContent' in m.get('supportedGenerationMethods', [])
                  and re.fullmatch(r'models/gemini-[a-zA-Z0-9.\-]+', m.get('name', ''))
                  and 'flash' in m['name'] and not any(x in m['name'] for x in ('image', 'audio', 'live', 'tts'))]
        configured = ['models/' + os.environ.get('GEMINI_MODEL', 'gemini-3.8-flash'), 'models/gemini-flash-lite-latest']
        candidates = list(dict.fromkeys(configured + models))[:6]
        for model in candidates:
            try:
                response = requests.post(BASE + model + ':generateContent', headers=headers,
                    json={'contents':[{'parts':[{'text':'Return exactly this JSON object: {"probe":"ok"}'}]}],
                          'generationConfig':{'responseMimeType':'application/json', 'temperature':0}}, timeout=20)
                print(f'PROBE model={model} status={response.status_code}')
                if not response.ok:
                    continue
                parts = response.json().get('candidates', [{}])[0].get('content', {}).get('parts', [])
                value = json.loads(''.join(p.get('text', '') for p in parts))
                if value == {'probe':'ok'}:
                    print(f'PROBE_GENERATION_VERIFIED model={model}')
                    return 0
            except (requests.RequestException, ValueError, IndexError, TypeError):
                print(f'PROBE model={model} invalid_or_unavailable=true')
    except (requests.RequestException, ValueError, KeyError, TypeError):
        print('PROBE discovery_failed')
    return 1

if __name__ == '__main__':
    raise SystemExit(main())
