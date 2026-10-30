"""INITIAL Claude caption translation only; never literary review."""
from pathlib import Path
import os,json,urllib.request,hashlib,datetime
R=Path(__file__).resolve().parents[1];O=R/'results/ru87-initial-captions';O.mkdir(parents=True,exist_ok=True)
ROWS=[{'id': 'ru87-caption-0012', 'source': 'WITH ESTELLA AFTER ALL.'}, {'id': 'ru87-caption-0037', 'source': 'IN THE CHURCHYARD.'}, {'id': 'ru87-caption-0072', 'source': 'PIP’S FIRST LETTER.'}, {'id': 'ru87-caption-0082', 'source': 'PIP WAITS ON MISS HAVISHAM.'}, {'id': 'ru87-caption-0132', 'source': 'OLD ORLICK AMONG THE CINDERS.'}, {'id': 'ru87-caption-0189', 'source': 'LECTURING ON CAPITAL.'}, {'id': 'ru87-caption-0223', 'source': 'JOE PERPLEXED.'}, {'id': 'ru87-caption-0242', 'source': 'A RUBBER AT MISS HAVISHAM’S.'}, {'id': 'ru87-caption-0245', 'source': 'THE TRICKS OF TRABB’S BOY.'}, {'id': 'ru87-caption-0279', 'source': 'TAKING LEAVE OF JOE.'}, {'id': 'ru87-caption-0295', 'source': 'ESTELLA DEFENDS HERSELF.'}, {'id': 'ru87-caption-0335', 'source': 'UNCLE PROVIS RECITES HIS WRONGS.'}, {'id': 'ru87-caption-0348', 'source': '“DON’T GO HOME!”'}, {'id': 'ru87-caption-0393', 'source': 'THE MAN THAT MADE PIP.'}, {'id': 'ru87-caption-0399', 'source': 'A PERILOUS PASS.'}]
try:
 key=os.environ.get('ANTHROPIC_API_KEY','').strip();assert key,'ANTHROPIC_API_KEY_NOT_CONFIGURED'
 system='Translate these15historical illustration captions into literary Russian. INITIAL TRANSLATION ONLY. Do not review,validate,certify or sign off. Keep exact IDs and order. Names: Пип,Эстелла,Джо,мисс Гевишам,Орлик,Траб,Провис. A rubber is a game of cards; capital means financial capital. For The man that made Pip,the speaker Pumblechook claims credit for Pip’s advancement. Return JSON object rows,each id,target. Separate OpenAI independent review follows.'
 body=dict(model='claude-sonnet-5-5',max_tokens=5000,system=system,messages=[dict(role='user',content=json.dumps(dict(rows=ROWS),ensure_ascii=False))]);headers={'x-api-key':key,'anthropic-version':'2023-06-01','content-type':'application/json'}
 workspace=os.environ.get('ANTHROPIC_WORKSPACE_ID','').strip()
 if workspace:headers['anthropic-workspace-id']=workspace
 req=urllib.request.Request('https://api.anthropic.com/v1/messages',data=json.dumps(body,ensure_ascii=False).encode(),headers=headers)
 with urllib.request.urlopen(req,timeout=240)as response:answer=json.load(response)
 assert answer['stop_reason']=='end_turn';s='\n'.join(a['text']for a in answer['content']if a['type']=='text').strip()
 if s.startswith('```'):s=s.split('\n',1)[1].rsplit('```',1)[0]
 d=json.loads(s);assert[a['id']for a in d['rows']]==[a['id']for a in ROWS]
 out=dict(state='INITIAL15_CAPTIONS_COMPLETE_NOT_REVIEWED',utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),actual_model=answer['model'],response_id=answer['id'],usage=answer['usage'],request_sha256=hashlib.sha256(json.dumps(body,ensure_ascii=False).encode()).hexdigest(),source_rows=ROWS,rows=d['rows'])
 (O/'initial.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print('INITIAL15_CAPTIONS_COMPLETE',answer['model'],answer['id'])
except Exception as e:
 (O/'failure.json').write_text(json.dumps(dict(state='FAILED_NO_INITIAL_COMPLETION_OR_REVIEW_PASS',exception=type(e).__name__,failure=str(e)[:500])));raise
