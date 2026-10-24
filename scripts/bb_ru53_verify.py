"""Independent actual-exchange verification, with exact native-document pass-through."""
from pathlib import Path
import json,hashlib,re

def sha(s):return hashlib.sha256(s.encode()).hexdigest()
def decoded(response):
 text='\n'.join(c['text'] for c in response['content'] if c.get('type')=='text');return json.JSONDecoder().raw_decode(text[text.index('{'):])[0]
def sid(row):return row.get('source_id',row.get('id'))
def qualify_unit(root,source,unit,output_name="ru53-original"):
 root=Path(root);u=next(x for x in source if x['unit']==unit);folder=root/f'results/{output_name}/{unit:03}';chapter=json.loads((folder/'chapter.reviewed.json').read_text());assert 'CORRECTION_COMPARISON_COMPLETE' in chapter['state']
 if chapter.get('reused_prior_source_unit'):
  from bb_ru53_seed_verify import qualify_seeded_unit
  accepted,report=qualify_seeded_unit(root,source,unit);assert chapter['heading']==accepted['heading'] and chapter['rows']==accepted['rows'];return chapter,report
 expected={r['id']:r for r in u['rows']};expected[u['heading_id']]={'id':u['heading_id'],'text':u['heading'],'sha256':sha(u['heading']),'translate':False};final=[];batches=[]
 for file in sorted(folder.glob('batch*.comparison_verified.json')):
  batch=json.loads(file.read_text());rows=batch['rows'];ids=[r['source_id'] for r in rows];native={i for i in ids if not expected[i].get('translate',True)};active=[i for i in ids if i not in native];assert [r['source_id'] for r in batch['review']]==ids
  last=batch['comparison'][-1];assert not last['corrections'],'UNVERIFIED_FINAL_CHANGES';assert [r['source_id'] for r in last['review']]==ids
  number=int(file.name[5:8]);actual_changes=set()
  for request in folder.glob(f'batch{number:03}.*.attempt*.request.json'):
   response=request.with_name(request.name.replace('.request.json','.response.json'))
   if not response.exists():continue
   body=json.loads(request.read_text());payload=json.loads(body['messages'][0]['content']);before=payload.get('draft_rows',payload.get('final_rows',[]))
   if not before:continue
   try:after=decoded(json.loads(response.read_text())).get('rows',[])
   except (KeyError,ValueError,TypeError):continue
   before={sid(r):r['target'] for r in before}
   for r in after:
    i=sid(r)
    if i in before and before[i]!=r.get('target'):actual_changes.add((i,before[i],r.get('target')))
  history=batch.get('corrections',[])+[c for turn in batch['comparison'] for c in turn['corrections']]
  for c in history:assert (c['source_id'],c['before'],c['after']) in actual_changes,'CORRECTION_NOT_FOUND_IN_REAL_EXCHANGE'
  def verdict(r):
   i=sid(r);target=next(x['target'] for x in rows if x['source_id']==i)
   if i in native:return r.get('verdict')=='NOT_APPLICABLE_SOURCE_NATIVE_FOREIGN' and target==expected[i]['text'] and bool(r.get('reason'))
   if r.get('verdict')=='PASS':return bool(r.get('reason'))
   return r.get('verdict')=='CORRECTED' and bool(r.get('reason')) and any(c['source_id']==i and c['after']==target for c in history)
  assert all(verdict(r) for r in last['review']),'FINAL_FULL_REVIEW_DISPOSITIONS'
  turn=len(batch['comparison'])-1;requests=sorted(folder.glob(f'batch{number:03}.comparison{turn:02}.attempt*.request.json'));assert requests
  request=requests[-1];body=json.loads(request.read_text());payload=json.loads(body['messages'][0]['content']);frozen=payload['source_rows'];protected=payload.get('protected_source_native_rows',[])
  assert [r['id'] for r in frozen]==active and [r['id'] for r in protected]==[i for i in ids if i in native],'ACTUAL_EDITABLE_AND_NATIVE_SOURCE_COVERAGE'
  assert [r['target'] for r in payload['final_rows']]==[r['target'] for r in rows if r['source_id'] not in native],'ACTUAL_REQUEST_TARGETS'
  response=json.loads(request.with_name(request.name.replace('.request.json','.response.json')).read_text());assert response['model']==last['api']['actual_model']=='claude-sonnet-5-5';assert response['id']==last['api']['response_id'] and response['stop_reason']=='end_turn';raw=decoded(response)
  assert [sid(r) for r in raw['rows']]==active and [r['target'] for r in raw['rows']]==[r['target'] for r in rows if r['source_id'] not in native],'ACTUAL_RESPONSE_TARGETS'
  assert [sid(r) for r in raw['review']]==active and all(verdict(r) for r in raw['review']),'ACTUAL_PROVIDER_FINAL_VERDICTS'
  supplied={r['id']:r for r in frozen+protected};assert set(supplied)==set(ids)
  for row in rows:
   i=row['source_id'];src=supplied[i];bound=expected[i];assert src['text']==bound['text'] and row['source_sha256']==src['sha256']==bound['sha256']==sha(src['text']);assert row['target_sha256']==sha(row['target'])
   assert re.findall(r'\[\[FN:\d+\]\]',row['target'])==re.findall(r'\[\[FN:\d+\]\]',bound['text']),'NOTE_TOKEN_PRESERVATION'
   if i in native:assert row['target']==bound['text'] and not src.get('translate',True),'EXACT_NATIVE_SOURCE'
   if bound.get('source_lines'):
    if i in native:assert row['target'].split('\n')==bound['source_lines'],'SOURCE_NATIVE_VERSE_LINES'
    else:
     assert len(row['target'].split('\n'))==len(bound['source_lines']),'TRANSLATED_SOURCE_LINE_COUNT'
     assert [not x.strip() for x in row['target'].split('\n')]==[not x.strip() for x in bound['source_lines']],'TRANSLATED_STANZA_BREAKS'
  final.extend(rows);batches.append({'batch':number,'rows':len(rows),'actual_provider_review_rows':len(active),'source_native_untranslated_rows':len(native),'response_id':response['id'],'actual_request_response':'PASS','final_source_comparison':'PASS'})
 assert len(final)==len(u['rows'])+1 and chapter['heading']==final[0] and chapter['rows']==final[1:];assert [r['source_id'] for r in chapter['rows']]==[r['id'] for r in u['rows']]
 report={'unit':unit,'state':'INDEPENDENT_SOURCE_AND_ACTUAL_AI_EVIDENCE_RECONCILED_PENDING_BOOK_QA','source_rows':len(u['rows']),'batch_evidence':batches,'source_native_policy':'Untranslated original foreign documents/verse copied exactly and independently compared; not represented as model-translated or provider-reviewed output','qualified_target_sha256':sha(json.dumps(chapter,ensure_ascii=False,sort_keys=True)),'book_release':False};return chapter,report
