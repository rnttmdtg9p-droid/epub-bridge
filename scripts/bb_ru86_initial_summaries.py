"""Claude initial authoring ONLY. Literary review remains independent OpenAI foreground work."""
import base64,gzip,hashlib,json,os,time,urllib.request,urllib.error,datetime
from pathlib import Path
R=Path(__file__).resolve().parents[1];O=R/'results/ru86-initial-summaries';O.mkdir(parents=True,exist_ok=True)
def save(n,j):(O/n).write_text(json.dumps(j,ensure_ascii=False,indent=2))
def run():
 key=os.environ.get('ANTHROPIC_API_KEY','').strip();assert key,'ANTHROPIC_API_KEY_NOT_CONFIGURED'
 packed=base64.b64decode(''.join(p.read_text()for p in sorted((R/'inputs').glob('ru86-source.part*.b64'))));assert hashlib.sha256(packed).hexdigest()=='873d6b368f226a138a28ee93339a6a0b56854afe52eb6724a62a15357ba0fe8b'
 source=json.loads(gzip.decompress(packed));byid={a['id']:a for u in source for a in u['rows']};spec=json.load(open(R/'inputs/ru86-abridged-summaries.json'));items=[]
 for a in spec['rows']:
  assert [byid[i]['sha256']for i in a['source_ids']]==a['source_sha256']
  items.append(dict(id=a['id'],source_ids=a['source_ids'],source=[byid[i]['text']for i in a['source_ids']]))
 system='You are the INITIAL Russian editorial-summary author for a user-authorised, clearly labelled abridged edition of Victor Hugo Notre-Dame de Paris. INITIAL AUTHORING ONLY: do not review, validate, certify or sign off any translation or editorial work. Some supplied original French passages concern minors. Do not translate or reproduce sexual descriptions, anatomy, sexualised bodily reactions or erotic detail. Instead write a brief NON-GRAPHIC, neutral Russian summary of plot-relevant events, emotions and causality for each supplied group. Do not invent events, ages, motives or dialogue. Keep names Клод, Эсмеральда, Феб, Жеан and chronology faithful. In summary bb86-summary-10 preserve the nonsexual Latin quotations Non cuiquam datum est habere nasum with marker [[N123]] and Dimidiam domum in paradiso, using [[I]]...[[/I]] emphasis; compress surrounding events non-graphically. Return only a JSON object with rows, each containing id and target. Retain every supplied id exactly once and in exact order. A separate OpenAI reviewer will check all summaries independently.'
 body=dict(model='claude-sonnet-5-5',max_tokens=12000,system=system,messages=[dict(role='user',content=json.dumps(dict(rows=items),ensure_ascii=False))]);headers={'x-api-key':key,'anthropic-version':'2023-06-01','content-type':'application/json'}
 workspace=os.environ.get('ANTHROPIC_WORKSPACE_ID','').strip()
 if workspace:headers['anthropic-workspace-id']=workspace
 for attempt in range(3):
  try:
   q=urllib.request.Request('https://api.anthropic.com/v1/messages',data=json.dumps(body,ensure_ascii=False).encode(),headers=headers)
   with urllib.request.urlopen(q,timeout=240)as r:response=json.load(r)
   break
  except urllib.error.HTTPError as e:
   if e.code in [429,500,502,503,529]and attempt<2:time.sleep(5*(attempt+1));continue
   raise RuntimeError('CLAUDE_HTTP_'+str(e.code))from None
 assert response['stop_reason']=='end_turn','INCOMPLETE_RESPONSE';s='\n'.join(x['text']for x in response['content']if x['type']=='text').strip()
 if s.startswith('```'):s=s.split('\n',1)[1].rsplit('```',1)[0]
 answer=json.loads(s);assert [a['id']for a in answer['rows']]==[a['id']for a in spec['rows']]
 for a in answer['rows']:assert isinstance(a['target'],str)and a['target'].strip()
 save('initial.json',dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),state='CLAUDE_INITIAL_SUMMARIES_COMPLETE_NOT_REVIEWED_NOT_ACCEPTED',actual_model=response.get('model'),response_id=response.get('id'),usage=response.get('usage'),request_sha256=hashlib.sha256(json.dumps(body,ensure_ascii=False).encode()).hexdigest(),source_packed_sha256=hashlib.sha256(packed).hexdigest(),rows=answer['rows']))
 print('INITIAL_SUMMARIES_COMPLETE',len(answer['rows']),'actual_model',response.get('model'))
try:run()
except Exception as e:
 save('failure.json',dict(state='FAILED_NO_INITIAL_COMPLETION_OR_REVIEW_PASS',exception=type(e).__name__,failure=str(e)[:500]));raise
