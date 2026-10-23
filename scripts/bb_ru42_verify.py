"""Independent frozen-source reconciliation of real retained Claude exchanges.
No network calls, translation requests, literary sampling or release claims.
"""
from pathlib import Path
import json,hashlib

def sha(s):return hashlib.sha256(s.encode()).hexdigest()
def qualify_unit(root,source,unit):
 root=Path(root);u=next(u for u in source if u['unit']==unit);folder=root/f'results/ru42-complete-original/{unit:03}';chapter=json.loads((folder/'chapter.reviewed.json').read_text());assert 'CORRECTION_COMPARISON_COMPLETE' in chapter['state'],'INCOMPLETE_COMPARISON'
 expected={r['id']:r for r in u['rows']};expected[u['heading_id']]={'id':u['heading_id'],'text':u['heading'],'sha256':sha(u['heading'])};adjfile=root/'inputs/ru42-controlling-adjudications.json';adj=json.loads(adjfile.read_text()) if adjfile.exists() else {};final=[];batches=[]
 def verdict(r,rows):
  if r['verdict']=='PASS':return bool(r.get('reason'))
  target=next(x['target'] for x in rows if x['source_id']==r['source_id'])
  history=batch.get('corrections',[])+[c for turn in batch.get('comparison',[]) for c in turn.get('corrections',[])]
  if r['verdict']=='CORRECTED' and r.get('reason') and any(c['source_id']==r['source_id'] and c['after']==target for c in history):return True
  a=adj.get(r['source_id']);return bool(a and a['final_verdict']=='PASS' and a['source']==expected[r['source_id']]['text'] and a['before']==next(x['target'] for x in rows if x['source_id']==r['source_id']))
 for file in sorted(folder.glob('batch*.comparison_verified.json')):
  batch=json.loads(file.read_text());rows=batch['rows'];ids=[r['source_id'] for r in rows];assert [r['source_id'] for r in batch['review']]==ids,'REVIEW_ORDER';last=batch['comparison'][-1];assert not last['corrections'],'UNVERIFIED_CORRECTIONS';assert [r['source_id'] for r in last['review']]==ids and all(verdict(r,rows) for r in last['review']),'FINAL_REVIEW_VERDICTS'
  number=int(file.name[5:8]);turn=len(batch['comparison'])-1;requests=sorted(folder.glob(f'batch{number:03}.comparison{turn:02}.attempt*.request.json'));assert requests,'MISSING_RETAINED_REQUEST';request=requests[-1];body=json.loads(request.read_text());payload=json.loads(body['messages'][0]['content']);frozen=payload['source_rows'];assert [r['target'] for r in payload['final_rows']]==[r['target'] for r in rows],'REQUEST_TARGET_MISMATCH'
  response=json.loads(request.with_name(request.name.replace('.request.json','.response.json')).read_text());assert response['model']==last['api']['actual_model']=='claude-sonnet-5-5','MODEL_EVIDENCE';assert response['id']==last['api']['response_id'] and response['stop_reason']=='end_turn','RESPONSE_EVIDENCE';text='\n'.join(c['text'] for c in response['content'] if c.get('type')=='text');raw=json.JSONDecoder().raw_decode(text[text.index('{'):])[0];assert [r.get('source_id',r.get('id')) for r in raw['rows']]==ids,'RESPONSE_ID_ORDER';assert [r['target'] for r in raw['rows']]==[r['target'] for r in rows],'RESPONSE_TARGET_MISMATCH'
  for r in raw['review']:r['source_id']=r.get('source_id',r.get('id'))
  assert [r['source_id'] for r in raw['review']]==ids and all(verdict(r,rows) for r in raw['review']),'ACTUAL_API_REVIEW_VERDICTS';assert len(frozen)==len(rows)
  for src,row in zip(frozen,rows):
   bound=expected[row['source_id']];assert src['id']==row['source_id'] and src['text']==bound['text'],'FROZEN_SOURCE_TEXT';assert row['source_sha256']==src['sha256']==bound['sha256']==sha(src['text']),'FROZEN_SOURCE_HASH';assert row['target_sha256']==sha(row['target']),'TARGET_HASH'
   import re
   assert re.findall(r'\[\[FN:\d+\]\]',row['target'])==re.findall(r'\[\[FN:\d+\]\]',bound['text']),'NOTE_TOKEN_PRESERVATION'
   if not bound.get('translate',True):assert row['target']==bound['text'],'FOREIGN_DOCUMENT_VERBATIM'
   if bound.get('source_lines'):assert src['source_lines']==bound['source_lines'] and len(row['target'].splitlines())==len(bound['source_lines']),'AUTHENTIC_VERSE_LINEATION'
  final.extend(rows);batches.append({'batch':number,'rows':len(rows),'response_id':response['id'],'source_order_hashes':'PASS','actual_request_response':'PASS','final_comparison':'PASS'})
 assert len(final)==len(u['rows'])+1 and chapter['heading']==final[0] and chapter['rows']==final[1:],'CHAPTER_BATCH_RECONCILIATION';assert [r['source_id'] for r in chapter['rows']]==[r['id'] for r in u['rows']],'COMPLETE_SOURCE_ORDER';applied=[]
 for row in [chapter['heading']]+chapter['rows']:
  sid=row['source_id']
  if sid in adj:
   a=adj[sid];assert a['unit']==unit and a['source']==expected[sid]['text'] and a['before']==row['target'] and a['final_verdict']=='PASS','CONTROLLING_CORRECTION_SOURCE';row.update(target=a['after'],target_sha256=sha(a['after']));applied.append(a)
 if applied:chapter['controlling_ai_correction_adjudications']=applied
 report={'unit':unit,'state':'INDEPENDENT_SOURCE_AND_ACTUAL_AI_EVIDENCE_RECONCILED_PENDING_BOOK_QA','source_rows':len(u['rows']),'batch_evidence':batches,'controlling_corrections':len(applied),'qualified_target_sha256':sha(json.dumps(chapter,ensure_ascii=False,sort_keys=True)),'book_release':False}
 return chapter,report
