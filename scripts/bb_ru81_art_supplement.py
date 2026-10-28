import datetime, hashlib, json, os, subprocess, time, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results/ru81-art-supplement-20261028'
OUT.mkdir(parents=True, exist_ok=True)
source = json.loads((ROOT / 'inputs/ru81-art-supplement-20261028.json').read_text())
H = lambda s: hashlib.sha256(s.encode()).hexdigest()
for row in source['rows']:
    row['sha256'] = H(row['text'])

def save(name, value):
    p = OUT / name
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2))

system = 'You are the authorized Boundary Bay INITIAL French-to-Russian translator. This is NEW short artwork text from the public-domain historical illustrations for Victor Hugo Les Miserables, NOT literary review. The main novel has already been translated: do not repeat it. Translate every supplied row, preserving row order, numbers, names and meaning. Use modern natural Russian suitable for captions/transcriptions. Never consult an existing Russian translation. Jean Valjean = Жан Вальжан. Keep uncertainty and ellipsis for partly obscured lettering; never invent unseen text or expand TAMINET to a complete guessed word. A partly visible foreign fragment may remain literal within the Russian transcription. Context fields are instructions, not reader text. Return JSON only: {"rows":[{"source_id":"exact id","target":"translation"}]}. Do not return hashes.'
payload = {'model': 'claude-sonnet-5-5', 'max_tokens': 5000, 'system': system,
           'messages': [{'role': 'user', 'content': json.dumps(source, ensure_ascii=False)}]}
save('source.bound.json', source)
save('translation.request.json', payload)
headers = {'x-api-key': os.environ['ANTHROPIC_API_KEY'].strip(), 'anthropic-version': '2023-06-01', 'content-type': 'application/json'}
if os.environ.get('ANTHROPIC_WORKSPACE_ID', '').strip():
    headers['anthropic-workspace-id'] = os.environ['ANTHROPIC_WORKSPACE_ID'].strip()
for attempt in range(4):
    try:
        req = urllib.request.Request('https://api.anthropic.com/v1/messages', data=json.dumps(payload, ensure_ascii=False).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=240) as response:
            raw = json.load(response)
        save('translation.response.json', raw)
        assert raw.get('stop_reason') == 'end_turn', 'INCOMPLETE_PROVIDER_RESPONSE'
        text = '\n'.join(x['text'] for x in raw['content'] if x.get('type') == 'text').strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0]
        translated = json.JSONDecoder().raw_decode(text[text.index('{'):])[0]['rows']
        assert len(translated) == len(source['rows']), 'ROW_COUNT'
        for src, dst in zip(source['rows'], translated):
            assert dst['source_id'] == src['id'], 'ROW_ORDER'
            assert isinstance(dst['target'], str) and dst['target'].strip(), 'EMPTY_TARGET'
            dst.update(source_sha256=src['sha256'], target_sha256=H(dst['target']))
        save('translated.initial.json', {'state': 'INITIAL_CLAUDE_TRANSLATION_ONLY_PENDING_COMPLETE_INDEPENDENT_REVIEW', 'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'rows': translated, 'translation_api': {'requested_model': payload['model'], 'actual_model': raw.get('model'), 'response_id': raw.get('id'), 'usage': raw.get('usage')}})
        subprocess.run(['git', 'add', 'results/ru81-art-supplement-20261028'], cwd=ROOT, check=True)
        if subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=ROOT).returncode:
            subprocess.run(['git', 'commit', '-m', 'Preserve14newrank81arttextClaudeinitialtranslationsandrawprovenance'], cwd=ROOT, check=True)
            subprocess.run(['git', 'push', 'origin', 'HEAD'], cwd=ROOT, check=True)
        print('INITIAL_TRANSLATION_COMPLETE', len(translated), raw.get('model'), 'INDEPENDENT_REVIEW_PENDING')
        break
    except urllib.error.HTTPError as error:
        save('attempt%02d.error.json' % attempt, {'http_status': error.code, 'state': 'INITIAL_TRANSLATION_FAILED_NO_REVIEW_CLAIM'})
        if error.code not in (429, 500, 502, 503, 529) or attempt == 3:
            raise RuntimeError('CLAUDE_HTTP_' + str(error.code)) from None
        time.sleep(5 * (attempt + 1))
