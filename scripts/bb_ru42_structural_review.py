"""Actual source comparison of the five structural labels; no name batches repeated."""
from pathlib import Path
import json,hashlib,gzip,base64,os
import bb_ru42_original as api
R=api.ROOT;O=R/'results/ru42-structural-final';O.mkdir(parents=True,exist_ok=True);p=R/'inputs/ru42-name-consistency.json'
if not p.exists():
 packed=json.loads((R/'inputs/ru42-name-consistency.packed.json').read_text());raw=gzip.decompress(base64.b64decode(packed['gzip_base64']));assert hashlib.sha256(raw).hexdigest()==packed['uncompressed_sha256'];p.write_bytes(raw)
data=json.loads(p.read_text());assert data['complete70input'];items=data['structural_review_rows'];sources=[x['source'] for x in items];current=api.validate([{'source_id':x['source']['id'],'target':x['target']} for x in items],sources);folder=O/'structural';folder.mkdir(exist_ok=True);turns=[]
for phase in range(6):
 payload={'task':'Compare EVERY Russian structural label with its French source. Preserve the exact Russian bytes if faithful. Return JSON ONLY using exactly these two top-level arrays: {"rows":[{"source_id":"exact id","target":"complete final Russian label"}],"review":[{"source_id":"exact id","verdict":"PASS or CORRECTED","reason":"concise source-based assessment"}]}. Every source_id exactly once in both arrays, in supplied order. Do not use alternative response keys. The labels are the work title, subtitle and three authenticated part labels.','source_rows':sources,'final_rows':current,'phase':'INDEPENDENT_FINAL_COMPARISON' if phase else 'STRUCTURAL_TRANSLATION_REVIEW'}
 result,meta=api.checked_call(api.SYSTEM+' Independently verify the structural translations against French.',payload,folder/f'phase{phase:02}',sources,True);changes=[{'source_id':a['source_id'],'before':a['target'],'after':b['target']} for a,b in zip(current,result['rows']) if a['target']!=b['target']];turns.append({'phase':phase,'api':meta,'review':result['review'],'corrections':changes});current=result['rows']
 if phase>=1 and not changes:
  assert all(x['verdict']=='PASS' for x in result['review']);assert [x['target'] for x in current]==[x['target'] for x in items],'STRUCTURAL_CORRECTION_REQUIRES_BUILDER_ALIGNMENT';api.save(O/'structural.verified.json',{'state':'ACTUAL_STRUCTURAL_TRANSLATIONS_REVIEWED_AND_STABLE','source_items':items,'rows':current,'turns':turns,'primary_source_witness_sha256':data['structural_source_witness_sha256'],'run_id':os.environ.get('GITHUB_RUN_ID'),'input_sha256':hashlib.sha256(p.read_bytes()).hexdigest()});print('FIVE_STRUCTURAL_LABELS_ACTUAL_REVIEW_AND_SEPARATE_STABLE_COMPARISON_PASS',flush=True);break
else:raise RuntimeError('STRUCTURAL_COMPARISON_NOT_STABLE')
