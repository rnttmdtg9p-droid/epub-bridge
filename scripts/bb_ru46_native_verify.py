"""Independent raw provider-exchange replay of complete original Russian review."""
from pathlib import Path
import json,hashlib
H=lambda s:hashlib.sha256(s.encode()).hexdigest()
def decode(d):
 t='\n'.join(x['text'] for x in d['content'] if x.get('type')=='text');return json.JSONDecoder().raw_decode(t[t.index('{'):])[0]
def qualify_unit(root,source,number,output_name='ru46-native-review'):
 root=Path(root);u=next(x for x in source if x['unit']==number);expected=[{'id':u['heading_id'],'text':u['heading'],'sha256':H(u['heading']),'tag':'heading'}]+u['rows'];folder=root/'results'/output_name/f'{number:03}';alltargets=[];evidence=[]
 for file in sorted(folder.glob('batch*.verified.json')):
  b=json.loads(file.read_text());ids=b['source_ids'];rows=expected[len(alltargets):len(alltargets)+len(ids)];assert ids==[r['id'] for r in rows] and b['source_hashes']=={r['id']:r['sha256'] for r in rows};assert b['state']=='PASS_ACTUAL_FULL_SOURCE_NATIVE_REVIEW_AND_SEPARATE_COMPARISON';k=b['batch'];responses=[]
  for phase in ['review','comparison']:
   reqs=sorted(folder.glob(f'batch{k:03}.{phase}.attempt*.request.json'));assert reqs;req=reqs[-1];request=json.loads(req.read_text());payload=json.loads(request['messages'][0]['content']);responsefile=req.with_name(req.name.replace('.request.json','.response.json'));raw=json.loads(responsefile.read_text());parsed=decode(raw);a=parsed['assessments'];assert raw['model']=='claude-sonnet-5-5' and raw['stop_reason']=='end_turn' and raw['id']==b[phase+'_api']['response_id'];assert a==b[phase] and [x['source_id'] for x in a]==ids;assert [r['id'] for r in payload['source_rows']]==ids
   assert [(r['text'],r['sha256']) for r in payload['source_rows']]==[(r['text'],r['sha256']) for r in rows];assert [x['target'] for x in payload['final_rows']]==[r['text'] for r in rows];assert all(x['verdict']=='PASS' and x.get('reason') for x in a) if phase=='comparison' else all(x.get('reason') for x in a)
   if phase=='comparison':assert payload['prior_review_findings']==b['review']
   responses.append({'phase':phase,'response_id':raw['id'],'request_sha256':hashlib.sha256(req.read_bytes()).hexdigest(),'response_sha256':hashlib.sha256(responsefile.read_bytes()).hexdigest()})
  targets=b['immutable_targets'];assert len(targets)==len(rows)
  for t,r in zip(targets,rows):assert t['source_id']==r['id'] and t['target']==r['text'] and t['source_sha256']==t['target_sha256']==r['sha256']==H(r['text'])
  alltargets.extend(targets);evidence.append({'batch':k,'rows':len(rows),'actual_exchanges':responses})
 assert len(alltargets)==len(expected),'INCOMPLETE_NATIVE_REVIEW';accepted={'unit':number,'heading':alltargets[0],'rows':alltargets[1:],'state':'SOURCE_ALIGNED_AI_REVIEW_AND_CORRECTION_COMPARISON_COMPLETE_PENDING_BOOK_QA','source_rows':len(u['rows']),'review_batches':len(evidence),'master':'5.0.70','method':'AI_LITERARY_REVIEW_COMPLETE_NATIVE_ORIGINAL;NO_TRANSLATION'};proof={'unit':number,'state':'PASS_INDEPENDENT_ACTUAL_NATIVE_REVIEW_REPLAY','source_rows':len(u['rows']),'headings':1,'evidence':evidence,'all_source_bytes_preserved':True,'book_release':False};return accepted,proof
