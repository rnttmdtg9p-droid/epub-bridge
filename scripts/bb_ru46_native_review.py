"""Actual complete AI review of immutable original Russian; no translated-text round trip."""
import base64,concurrent.futures,datetime,gzip,hashlib,json,os,subprocess,threading,time,urllib.request,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'results/ru46-native-review';OUT.mkdir(parents=True,exist_ok=True);PACKED=base64.b64decode(''.join(p.read_text() for p in sorted((ROOT/'inputs').glob('ru46-review-input.part*.b64'))));assert hashlib.sha256(PACKED).hexdigest()=='1d403c0ba88c8edf31ce2b356bda1c3e8618f0e800d7629ae8ca5ff39502693f';SOURCE=json.loads(gzip.decompress(PACKED))['source'];MODEL='claude-sonnet-5-5';KEY=os.environ.get('ANTHROPIC_API_KEY','').strip();LOCK=threading.Lock();H=lambda s:hashlib.sha256(s.encode()).hexdigest()
SYSTEM='You are the authorized complete source-aligned literary reviewer for Anton Chekhov, Three Sisters, ORIGINAL RUSSIAN, not a translation. The selected 1901-based native copytext is authoritative. Review every supplied heading, dialogue, stage direction, cast entry and preserved editorial note, including inline emphasis and [[FN:n]] anchors, against its source and its complete-act context. Assess fidelity and literary integrity. Preserve authentic Chekhov wording, punctuation, repetitions, foreign-language mistakes, character voices and paragraph boundaries. Do not improve or rewrite an original author. XML namespace spelling, attribute order, whitespace serialization and NBSP presentation are not literary corrections. Twelve explanatory notes belong to the preserved Wikisource editorial layer, not to Chekhov; their source wording is preserved. Report any genuine source-to-target corruption or unresolved editorial finding honestly. Return assessments only; do not serialize or rewrite the source text. Each assessment must use source_id, verdict PASS or SOURCE_CORRECTION_REQUIRED, and a concise specific reason. If correction is required include suggested_target and source-based rationale. JSON only.'
def save(p,d):
 p.parent.mkdir(parents=True,exist_ok=True);q=p.with_suffix(p.suffix+'.tmp');q.write_text(json.dumps(d,ensure_ascii=False,indent=2));q.replace(p)
def commit():
 with LOCK:
  subprocess.run(['git','add','results/ru46-native-review'],cwd=ROOT,check=True)
  if subprocess.run(['git','diff','--cached','--quiet'],cwd=ROOT).returncode:
   subprocess.run(['git','commit','-m','Checkpoint actual original-Russian complete literary review and independent comparison'],cwd=ROOT,check=True)
   for attempt in range(3):
    if subprocess.run(['git','push','origin','HEAD'],cwd=ROOT).returncode==0:break
    if attempt==2:raise RuntimeError('CHECKPOINT_PUSH_FAILED')
    time.sleep(2*(attempt+1))
def call(payload,path):
 body={'model':MODEL,'max_tokens':16384,'output_config':{'effort':'medium'},'system':SYSTEM,'messages':[{'role':'user','content':json.dumps(payload,ensure_ascii=False)}]};save(Path(str(path)+'.request.json'),body);headers={'x-api-key':KEY,'anthropic-version':'2023-06-01','content-type':'application/json'}
 if os.environ.get('ANTHROPIC_WORKSPACE_ID','').strip():headers['anthropic-workspace-id']=os.environ['ANTHROPIC_WORKSPACE_ID'].strip()
 for attempt in range(4):
  try:
   req=urllib.request.Request('https://api.anthropic.com/v1/messages',data=json.dumps(body,ensure_ascii=False).encode(),headers=headers)
   with urllib.request.urlopen(req,timeout=240) as f:raw=json.load(f)
   save(Path(str(path)+'.response.json'),raw);assert raw['stop_reason']=='end_turn','INCOMPLETE_PROVIDER_RESPONSE';text='\n'.join(x['text'] for x in raw['content'] if x.get('type')=='text');result=json.JSONDecoder().raw_decode(text[text.index('{'):])[0];return result,{'response_id':raw['id'],'actual_model':raw['model'],'usage':raw.get('usage')}
  except urllib.error.HTTPError as e:
   if e.code in [429,500,502,503,529] and attempt<3:time.sleep(5*(attempt+1));continue
   raise RuntimeError('ANTHROPIC_HTTP_'+str(e.code)) from None
 raise RuntimeError('API_RETRIES_EXHAUSTED')
def checked(payload,path,rows):
 errors=[]
 for attempt in range(3):
  q=dict(payload)
  if errors:q['format_retry']='Prior response failed: '+errors[-1]+'. Return exactly one assessment for every requested source_id in order.'
  try:
   result,api=call(q,Path(str(path)+f'.attempt{attempt:02}'));assess=result['assessments'];assert [x['source_id'] for x in assess]==[r['id'] for r in rows],'ASSESSMENT_COVERAGE';assert all(x['verdict'] in ['PASS','SOURCE_CORRECTION_REQUIRED'] and x.get('reason') for x in assess),'INVALID_VERDICT';return assess,api
  except (AssertionError,KeyError,ValueError,TypeError) as e:errors.append(str(e));save(Path(str(path)+f'.attempt{attempt:02}.format-error.json'),{'error':str(e)})
 raise RuntimeError('FORMAT_RETRIES_EXHAUSTED '+str(errors))
def review_unit(u):
 n=u['unit'];D=OUT/f'{n:03}';D.mkdir(exist_ok=True);heading={'id':u['heading_id'],'text':u['heading'],'sha256':H(u['heading']),'tag':'heading'};allrows=[heading]+u['rows'];batches=[];batch=[];chars=0
 for r in allrows:
  assert H(r['text'])==r['sha256']
  if batch and (len(batch)>=40 or chars+len(r['text'])>16000):batches.append(batch);batch=[];chars=0
  batch.append(r);chars+=len(r['text'])
 if batch:batches.append(batch)
 for k,rows in enumerate(batches):
  final=D/f'batch{k:03}.verified.json'
  if final.exists():continue
  context={'book':'Три сестры, Антон Чехов, original Russian1901-based selected copytext','unit':n,'heading':u['heading'],'complete_unit_source':[r['text'] for r in u['rows']]};base={'context':context,'source_rows':rows,'final_rows':[{'source_id':r['id'],'target':r['text']} for r in rows]};review,api=checked({**base,'task':'Review EVERY supplied source/final row completely. Return {"assessments":[{"source_id":"exact id","verdict":"PASS or SOURCE_CORRECTION_REQUIRED","reason":"specific source-based literary assessment"}]}. The final text is the source-native original; preserve authentic wording. Report actual defects, not taste-based rewrites.'},D/f'batch{k:03}.review',rows)
  comparison,api2=checked({**base,'prior_review_findings':review,'task':'Independently compare EVERY final row against frozen source, including any prior findings. Confirm original-author fidelity, speaker and stage directions, notes, markup semantics and literary integrity. Resolve proposed rewrites by preserving authenticated original wording; confirm a genuine unresolved source mismatch when present. Return {"assessments":[{"source_id":"exact id","verdict":"PASS or SOURCE_CORRECTION_REQUIRED","reason":"specific source-bound final decision"}]} for every supplied row, in order.'},D/f'batch{k:03}.comparison',rows)
  assert all(x['verdict']=='PASS' for x in comparison),'UNRESOLVED_SOURCE_FINDING_'+json.dumps([x for x in comparison if x['verdict']!='PASS'],ensure_ascii=False)
  save(final,{'state':'PASS_ACTUAL_FULL_SOURCE_NATIVE_REVIEW_AND_SEPARATE_COMPARISON','unit':n,'batch':k,'source_ids':[r['id'] for r in rows],'source_hashes':{r['id']:r['sha256'] for r in rows},'review':review,'review_api':api,'comparison':comparison,'comparison_api':api2,'target_policy':'EXACT_FROZEN_NATIVE_SOURCE_BYTES;HOST_ASSEMBLY_NOT_MODEL_TRANSLATION','immutable_targets':[{'source_id':r['id'],'source_sha256':r['sha256'],'target':r['text'],'target_sha256':r['sha256'],'tag':r.get('tag')} for r in rows]});commit();print('UNIT',n,'BATCH',k+1,'OF',len(batches),'ACTUAL_FULL_REVIEW_AND_COMPARISON_PASS',flush=True)
 from bb_ru46_native_verify import qualify_unit
 accepted,proof=qualify_unit(ROOT,SOURCE,n);save(OUT/'qualified'/f'unit-{n:03}.json',accepted);save(OUT/'qualified'/f'unit-{n:03}.evidence.json',proof);commit();return n
if __name__=='__main__':
 assert KEY and len(SOURCE)==5 and sum(len(u['rows']) for u in SOURCE)==1058;errors=[];save(OUT/'run-status.json',{'state':'RUNNING_COMPLETE_ORIGINAL_RUSSIAN_LITERARY_REVIEW','run_id':os.environ.get('GITHUB_RUN_ID'),'source_rows':1058,'source_headings':5,'checkpoint_is_stop_condition':False});commit()
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
  jobs={pool.submit(review_unit,u):u['unit'] for u in SOURCE}
  for job in concurrent.futures.as_completed(jobs):
   try:job.result()
   except Exception as e:errors.append({'unit':jobs[job],'error':str(e)});print('UNIT_FAILED',jobs[job],str(e),flush=True)
 save(OUT/'run-status.json',{'state':'PARTIAL_FAILURE' if errors else 'COMPLETE_ACTUAL_NATIVE_REVIEW_PENDING_BOOK_QA','errors':errors,'source_rows':1058,'source_headings':5,'run_id':os.environ.get('GITHUB_RUN_ID'),'model':MODEL,'checkpoint_is_stop_condition':False});commit()
 if errors:raise SystemExit(1)
