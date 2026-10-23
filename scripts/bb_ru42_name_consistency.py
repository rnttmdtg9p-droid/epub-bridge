"""Real final source-aligned review of explicit spelling-policy proposals."""
import json,re,datetime,hashlib,os
import bb_ru42_original as api
R=api.ROOT;O=R/'results/ru42-name-consistency';O.mkdir(parents=True,exist_ok=True);p=R/'inputs/ru42-name-consistency.json'
if not p.exists():
 import base64,gzip
 packed=json.loads((R/'inputs/ru42-name-consistency.packed.json').read_text());raw=gzip.decompress(base64.b64decode(packed['gzip_base64']));assert hashlib.sha256(raw).hexdigest()==packed['uncompressed_sha256'];p.write_bytes(raw)
data=json.loads(p.read_text());assert data['complete70input'] and data['available_units']==list(range(1,71)),'COMPLETE_BOOK_INPUT_REQUIRED';H=lambda s:hashlib.sha256(s.encode()).hexdigest()
def canonical(s):
 for pattern,replacement in data['replacement_rules']:s=re.sub(pattern,lambda m:replacement.upper() if m.group().isupper() else replacement,s,flags=re.I)
 return s
batches=[];batch=[];chars=0
for item in data['rows']:
 size=len(item['source']['text'])+len(item['proposed_target'])
 if batch and (len(batch)>=12 or chars+size>16000):batches.append(batch);batch=[];chars=0
 batch.append(item);chars+=size
if batch:batches.append(batch)
allfinal=[]
def review_batch(index,items):
 folder=O/f'{index:03}';folder.mkdir(exist_ok=True);sources=[x['source'] for x in items];current=[{'source_id':x['source']['id'],'target':x['proposed_target']} for x in items];current=api.validate(current,sources);ledger=[{'origin':'HOST_EDITORIAL_POLICY','source_id':x['source']['id'],'before':x['base_reviewed_target']['target'],'after':x['proposed_target']} for x in items];turns=[]
 for phase in range(8):
  payload={'task':'Perform a complete French-source comparison of EVERY proposed final Russian row. These rows were translated by DeepL and already received full literary review; explicit publisher spelling consistency edits now require independent verification. Verify all meaning, source facts, agency, negation, syntax, italics, note markers and every editorial correction. Follow the canonical name spelling policy. Preserve the exact current target bytes if faithful and idiomatic. Return JSON only: {"rows":[{"source_id":"exact id","target":"complete corrected Russian"}],"review":[{"source_id":"exact id","verdict":"PASS or CORRECTED","reason":"source-based assessment"}]}. Every source row exactly once in both arrays.','canonical_spelling_policy':data['canonical_spelling_policy'],'source_rows':sources,'final_rows':current,'prior_corrections':ledger,'review_phase':'INDEPENDENT_COMPARISON' if phase else 'EDITORIAL_CORRECTION_REVIEW'}
  result,meta=api.checked_call(api.SYSTEM+' You are verifying final editorial name corrections against the complete French source, independently of preceding approvals.',payload,folder/f'phase{phase:02}',sources,True);provider=result['rows'];changes=[{'origin':'ACTUAL_PROVIDER_CORRECTION','source_id':a['source_id'],'before':a['target'],'after':b['target']} for a,b in zip(current,provider) if a['target']!=b['target']];host=[];nextrows=[]
  for row in provider:
   new=canonical(row['target']);nextrows.append({**row,'target':new,'target_sha256':H(new)})
   if new!=row['target']:host.append({'origin':'HOST_EDITORIAL_POLICY_REAPPLICATION_REQUIRES_NEXT_REAL_REVIEW','source_id':row['source_id'],'before':row['target'],'after':new})
  ledger.extend(changes+host);turns.append({'phase':phase,'api':meta,'review':result['review'],'provider_corrections':changes,'host_policy_reapplications':host});current=nextrows
  if phase>=1 and not changes and not host:
   assert all(v['verdict']=='PASS' for v in result['review']);api.save(folder/'batch.verified.json',{'state':'ACTUAL_SOURCE_COMPARISON_STABLE_AFTER_EDITORIAL_CORRECTIONS','rows':current,'input_items':items,'turns':turns,'correction_ledger':ledger});print('NAME_CORRECTIONS_SOURCE_VERIFIED',index+1,'OF',len(batches),'ROWS',len(current),flush=True);return current
 else:raise RuntimeError('NAME_CORRECTIONS_NOT_STABLE')
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=3) as pool:batch_results=list(pool.map(lambda pair:review_batch(*pair),enumerate(batches)))
allfinal=[row for batch in batch_results for row in batch]
structural=data['structural_review_rows'];sources=[x['source'] for x in structural];current=api.validate([{'source_id':x['source']['id'],'target':x['target']} for x in structural],sources);folder=O/'structural';folder.mkdir(exist_ok=True);turns=[]
for phase in range(6):
 payload={'task':'Compare EVERY structural title label against its French source. These are the work title, subtitle and authenticated three part labels. Preserve exact current Russian bytes when faithful. Return JSON only with complete rows and per-row review verdict PASS or CORRECTED and a concise source-based reason. Every source_id exactly once in supplied order.','source_rows':sources,'final_rows':current,'phase':'INDEPENDENT_FINAL_COMPARISON' if phase else 'STRUCTURAL_TRANSLATION_REVIEW'}
 result,meta=api.checked_call(api.SYSTEM+' Independently verify structural heading translations against French.',payload,folder/f'phase{phase:02}',sources,True);changes=[{'source_id':a['source_id'],'before':a['target'],'after':b['target']} for a,b in zip(current,result['rows']) if a['target']!=b['target']];turns.append({'phase':phase,'api':meta,'review':result['review'],'corrections':changes});current=result['rows']
 if phase>=1 and not changes:
  assert all(x['verdict']=='PASS' for x in result['review']);assert [x['target'] for x in current]==[x['target'] for x in structural],'STRUCTURAL_CORRECTION_REQUIRES_BUILDER_ALIGNMENT';api.save(O/'structural.verified.json',{'state':'ACTUAL_STRUCTURAL_TRANSLATIONS_REVIEWED_AND_STABLE','source_items':structural,'rows':current,'turns':turns,'primary_source_witness_sha256':data['structural_source_witness_sha256']});break
else:raise RuntimeError('STRUCTURAL_COMPARISON_NOT_STABLE')
assert len(allfinal)==len(data['rows']);api.save(O/'all-rows.verified.json',{'state':'ALL_NAME_POLICY_CORRECTIONS_HAVE_REAL_REVIEW_AND_SEPARATE_STABLE_SOURCE_COMPARISON','rows':allfinal,'input_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'batches':len(batches),'source_book_units':70,'run_id':os.environ.get('GITHUB_RUN_ID'),'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'release_claimed':False})
