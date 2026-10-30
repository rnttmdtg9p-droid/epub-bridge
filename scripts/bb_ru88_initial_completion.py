"""Claude INITIAL German→Russian completion only. No review authority."""
from pathlib import Path
import os,json,urllib.request,urllib.error,hashlib,datetime,time
R=Path(__file__).resolve().parents[1];O=R/'results/ru88-initial-completion';O.mkdir(parents=True,exist_ok=True)
source=json.loads((R/'sources/ru88-missing12.json').read_text());key=os.environ.get('ANTHROPIC_API_KEY','').strip();assert key,'ANTHROPIC_API_KEY_NOT_CONFIGURED'
headers={'x-api-key':key,'anthropic-version':'2023-06-01','content-type':'application/json'}
workspace=os.environ.get('ANTHROPIC_WORKSPACE_ID','').strip()
if workspace:headers['anthropic-workspace-id']=workspace
system='You perform INITIAL literary translation from authentic1857 Brothers Grimm German (including regional dialect) into fluent, faithful Russian. This is NOT literary review, certification or validation. A separate independent OpenAI reviewer will review every row. Translate all supplied rows completely, preserving IDs/order, all narrative details, repetition, irony, religious references, dialect character without caricature, parenthetic authorial glosses, and verse line breaks. Use modern Russian orthography, literary folk-tale idiom compatible with historical Polevoy prose. Do not censor, abridge, explain, modernize the events, add notes, or invent content. Translate the genuine title. A verse row must remain verse. Do not copy a modern published Russian translation. Return ONLY JSON object with rows:[{id,target}] preserving source IDs and order. No other prose.'
failed=[];completed=[]
for unit in source['units']:
 out=O/(unit['id']+'.json')
 try:
  body=dict(model='claude-sonnet-5-5',max_tokens=14000,system=system,messages=[dict(role='user',content=json.dumps(unit,ensure_ascii=False))]);encoded=json.dumps(body,ensure_ascii=False).encode();answer=None
  for attempt in range(4):
   try:
    with urllib.request.urlopen(urllib.request.Request('https://api.anthropic.com/v1/messages',data=encoded,headers=headers),timeout=300)as response:answer=json.load(response)
    break
   except urllib.error.HTTPError as e:
    if e.code not in (429,500,502,503,504,529) or attempt==3:raise
    time.sleep(min(45,8*(attempt+1)))
  assert answer and answer['stop_reason']=='end_turn', 'INCOMPLETE_PROVIDER_OUTPUT'
  s='\n'.join(a['text']for a in answer['content']if a['type']=='text').strip()
  if s.startswith('```'):s=s.split('\n',1)[1].rsplit('```',1)[0]
  d=json.loads(s);assert[a['id']for a in d['rows']]==[a['id']for a in unit['rows']]
  assert all(isinstance(a['target'],str)and a['target'].strip()for a in d['rows'])
  data=dict(state='INITIAL_TRANSLATION_COMPLETE_INDEPENDENT_REVIEW_PENDING',utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),actual_model=answer['model'],response_id=answer['id'],usage=answer['usage'],request_sha256=hashlib.sha256(encoded).hexdigest(),source_unit=unit,rows=d['rows'])
  out.write_text(json.dumps(data,ensure_ascii=False,indent=2));completed.append(unit['id']);print('INITIAL_COMPLETE',unit['id'],len(d['rows']),answer['model'],answer['id'],flush=True)
 except Exception as e:
  failure=dict(state='FAILED_NO_REVIEW_OR_RELEASE_PASS',unit=unit['id'],utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),exception=type(e).__name__,failure=str(e)[:500]);(O/(unit['id']+'-failure.json')).write_text(json.dumps(failure,ensure_ascii=False,indent=2));failed.append(failure);print('INITIAL_FAILED',unit['id'],type(e).__name__,flush=True)
(O/'summary.json').write_text(json.dumps(dict(state='INITIAL_COMPLETION_REVIEW_PENDING'if not failed else'PARTIAL_INITIAL_FAILURE',completed=completed,failed=failed,initial_worker='Claude via Anthropic Messages API; never literary review'),ensure_ascii=False,indent=2))
if failed:raise SystemExit(1)
