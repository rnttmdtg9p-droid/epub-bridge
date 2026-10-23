"""Reconcile unchanged prior units through real original exchanges, never a bare PASS flag."""
from pathlib import Path
import json,hashlib,gzip,base64,copy,zipfile,io
H=lambda s:hashlib.sha256(s.encode()).hexdigest()
def qualify_seeded_unit(root,current,number):
 from bb_ru42_verify import qualify_unit
 root=Path(root);packed=base64.b64decode((root/'inputs/ru42-source.json.gz.b64').read_text());assert hashlib.sha256(packed).hexdigest()=='85acf8afe9c3576a79c1b3c8739c939e531e31cbbb0d7a50b39e31880fc3277b';prior=json.loads(gzip.decompress(packed));old=prior[number-1];new=current[number-1];assert number in [1,2,4] and old['rows']==new['rows'],'PRIOR_SOURCE_ROWS_CHANGED';chapter,oldproof=qualify_unit(root,prior,number,output_name='ru42-complete-original');heading_proof=None
 if number==1:
  folder=root/'inputs/ru42-heading001-final-actual'
  if not folder.exists():
   packed_heading=base64.b64decode((root/'inputs/ru42-heading001-final-actual.zip.b64').read_text());assert hashlib.sha256(packed_heading).hexdigest()=='6579f2981823f1f554cfaf4df6d6acda98cf5c56e0a79ace20f7817d5e7fe4b5'
   with zipfile.ZipFile(io.BytesIO(packed_heading)) as archive:
    assert archive.testzip() is None and all('/' not in n and '\\' not in n and n.endswith('.json') for n in archive.namelist());archive.extractall(folder)
  final=json.loads((folder/'heading.verified.json').read_text());assert final['source']==new['heading'] and final['source_sha256']==H(new['heading']) and final['target_sha256']==H(final['target']);assert final['source_id']==new['heading_id'];turn=final['review_turn'];assert turn>=1;records=[]
  for i in range(turn+1):
   request=json.loads((folder/f'review{i:02}.request.json').read_text());response=json.loads((folder/f'review{i:02}.response.json').read_text());payload=json.loads(request['messages'][0]['content']);assert payload['source']==new['heading'] and payload['source_sha256']==H(new['heading']) and payload['target']==final['target'];assert request['model']==response['model']=='claude-sonnet-5-5' and response['stop_reason']=='end_turn';text='\n'.join(x['text'] for x in response['content'] if x.get('type')=='text');decoded=json.JSONDecoder().raw_decode(text[text.index('{'):])[0];assert decoded['target']==final['target'] and decoded['verdict']=='PASS' and decoded['reason'];records.append({'response_id':response['id'],'verdict':'PASS','actual_model':response['model']})
  assert response['id']==final['response_id'];chapter['heading'].update(source_sha256=H(new['heading']),target=final['target'],target_sha256=H(final['target']));heading_proof={'state':'PASS_ACTUAL_CORRECTED_SOURCE_HEADING_EXCHANGES','actual':records,'source_sha256':H(new['heading']),'target_sha256':H(final['target'])}
 else:assert old['heading']==new['heading'],'UNVERIFIED_HEADING_CHANGE'
 chapter['reused_prior_source_unit']=number;chapter['state']='SOURCE_ALIGNED_AI_REVIEW_AND_CORRECTION_COMPARISON_COMPLETE_PENDING_BOOK_QA';proof={'unit':number,'state':'INDEPENDENT_PRIOR_ACTUAL_EXCHANGES_AND_EFFECTIVE_SOURCE_RECONCILED_PENDING_BOOK_QA','source_rows':len(new['rows']),'prior_complete_actual_qualification':oldproof,'heading_source_correction':heading_proof,'all_retained_source_body_rows_exactly_unchanged':True,'source_effective_unit_sha256':H(json.dumps(new,ensure_ascii=False,sort_keys=True)),'book_release':False};return chapter,proof
