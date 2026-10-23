"""Wait for complete actual source reviews, then scope name corrections and compare again."""
from pathlib import Path
import json,hashlib,os,subprocess,tarfile,io,time,re,urllib.request,zipfile
import bb_ru44_original as api
from bb_ru44_verify import qualify_unit
R=api.ROOT;O=R/'results/ru44-names';O.mkdir(parents=True,exist_ok=True);branch='bb-ru44-original-v5070-20261023';main='results/ru44-original'
for poll in range(240):
 subprocess.run(['git','fetch','origin',branch],cwd=R,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 state=subprocess.run(['git','show',f'origin/{branch}:{main}/run-status.json'],cwd=R,capture_output=True,text=True)
 status=json.loads(state.stdout) if state.returncode==0 else {}
 if status.get('state')=='API_TRANSLATION_REVIEW_COMPLETE_PENDING_BOOK_QA':break
 if status.get('state')=='PARTIAL_FAILURE':raise RuntimeError('MAIN_ACTUAL_TRANSLATION_FAILED_REPAIR_REQUIRED')
 print('WAIT_COMPLETE7_SOURCE_UNITS',status.get('unit'),status.get('last_complete_batch'),flush=True);time.sleep(30)
else:raise RuntimeError('WAIT_FOR_COMPLETE_SOURCE_LIMIT')
head=subprocess.check_output(['git','rev-parse',f'origin/{branch}'],cwd=R,text=True).strip();raw=subprocess.check_output(['git','archive',head,main],cwd=R)
with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
 assert all(not x.name.startswith('/') and '..' not in Path(x.name).parts for x in tf.getmembers());tf.extractall(R,filter='data')
seed=urllib.request.Request('https://api.github.com/repos/rnttmdtg9p-droid/epub-bridge/actions/artifacts/12519692363/zip',headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json'})
seedbytes=urllib.request.urlopen(seed).read();assert hashlib.sha256(seedbytes).hexdigest()=='a488813fe58a06bd4e50d1efe0d7438b8bcc23014489a601a90846b7b932636f'
with zipfile.ZipFile(io.BytesIO(seedbytes)) as z:
 assert z.testzip() is None and all(not n.startswith('/') and '..' not in Path(n).parts for n in z.namelist());z.extractall(O)
# Preserve failed actual exchanges. Correct the independently confirmed speaker/addressee defect separately.
for f in list(O.glob('batch004.phase*')): f.rename(f.with_name('rejected-'+f.name))
items=[];allunits=[];rx=re.compile(r'\bБак(а|у|ом|е|и|ов|ам|ами|ах)?\b')
for u in api.SOURCE:
 c,e=qualify_unit(R,api.SOURCE,u['unit'],output_name='ru44-original');allunits.append({'unit':u['unit'],'source_rows':len(u['rows']),'chapter_sha256':hashlib.sha256(json.dumps(c,ensure_ascii=False,sort_keys=True).encode()).hexdigest()});byid={r['id']:r for r in u['rows']}
 for t in c['rows']:
  if not rx.search(t['target']):continue
  src=byid[t['source_id']];assert re.search(r'\bBuck\b',src['text']),('NAME_CANDIDATE_WITHOUT_BUCK_SOURCE',src['id']);expected=rx.sub(lambda m:'Бэк'+(m.group(1) or ''),t['target']);items.append({'unit':u['unit'],'source':src,'prior_target':t['target'],'expected_target':expected})
assert items,'NO_VARIANTS_OVERLAY_NOT_NEEDED';input={'rank':44,'actual_main_run_id':39988950472,'actual_main_checkpoint_head':head,'actual_qualified_units':allunits,'items':items,'scope':'OnlypropernameBuckБак→Бэкincludinggrammaticalendings; retainallotherreviewedbytes'};api.save(O/'input.json',input);batches=[]
for index in range(0,len(items),8):
 batch=items[index:index+8];number=index//8
 if number<4:
  cached=json.loads((O/f'batch{number:03}.verified.json').read_text());assert cached['items']==batch;batches.append(cached);print('REUSED_VERIFIED_BATCH',number,flush=True);continue
 sources=[x['source'] for x in batch];current=api.validate([{'source_id':x['source']['id'],'target':x['prior_target']} for x in batch],sources);turns=[]
 for phase in range(5):
  payload={'task':'Make and verify ONLY this source-grounded proper-name consistency correction: English dog Buck must consistently be Russian Бэк (Бэка,Бэку,Бэком,Бэке). The prior reviewed text inconsistently uses Бак forms. The expected_target is the proposed exact narrow correction; preserve every other byte, meaning, punctuation, italics, line count and paragraph. Independently verify against every complete English source paragraph. Do not rewrite already reviewed wording or add explanations. Return JSON only with rows(source_id,target) and review(source_id,verdict PASS or CORRECTED,reason). In the first phase apply the correction; in later phase compare corrected whole paragraphs separately to the English and preserve correct bytes. Every source_id exactly once in order. If a supplied candidate has an actual source contradiction, explain it in the reason and do not invent a pass.','source_rows':sources,'final_rows':current,'proposed_narrow_targets':[{'source_id':x['source']['id'],'target':x['expected_target']} for x in batch],'phase':'INDEPENDENT_SOURCE_COMPARISON' if phase else 'SOURCE_ALIGNED_NAME_CORRECTION'}
  
  payload['task']+=' One separately confirmed source defect in 044-002-0013 must also be corrected: François is the dog-driver who speaks TO Perrault. Replace only крикнул погонщик Перро with крикнул погонщик, обращаясь к Перро in that paragraph. This is an explicitly scoped source correction. Independently compare it again. All other changes remain forbidden.'
  result,meta=api.checked_call(api.SYSTEM,payload,O/f'batch{number:03}.phase{phase:02}',sources,True);changes=[{'source_id':a['source_id'],'before':a['target'],'after':b['target']} for a,b in zip(current,result['rows']) if a['target']!=b['target']];turns.append({'phase':phase,'api':meta,'review':result['review'],'corrections':changes});current=result['rows'];assert [r['target'] for r in current]==[x['expected_target'].replace('крикнул погонщик Перро','крикнул погонщик, обращаясь к Перро') if x['source']['id']=='044-002-0013' else x['expected_target'] for x in batch],'UNSCOPED_NAME_CORRECTION_OR_REVIEW_REQUIRED'
  if phase>=1 and not changes and all(r['verdict']=='PASS' for r in result['review']):break
 else:raise RuntimeError('NAME_SOURCE_COMPARISON_NOT_STABLE')
 b={'batch':number,'items':batch,'rows':current,'turns':turns};batches.append(b);api.save(O/f'batch{number:03}.verified.json',b);print('NAME_BATCH_VERIFIED',number,'ROWS',len(batch),flush=True)
api.save(O/'names.verified.json',{'state':'ACTUAL_NAME_ONLY_CORRECTIONS_AND_SEPARATE_SOURCE_COMPARISONS','run_id':os.environ.get('GITHUB_RUN_ID'),'seed_run_id':39993022778,'seed_artifact_id':12519692363,'seed_batches':[0,1,2,3],'separate_source_correction':{'source_id':'044-002-0013','before':'крикнул погонщик Перро','after':'крикнул погонщик, обращаясь к Перро','source_fact':'François the dog-driver cried to Perrault'},'main_run_id':39988950472,'main_checkpoint_head':head,'input_sha256':hashlib.sha256((O/'input.json').read_bytes()).hexdigest(),'batches':batches,'rows':[r for b in batches for r in b['rows']],'book_release':False});print('ALL_NAME_CORRECTIONS_VERIFIED',len(items),flush=True)
