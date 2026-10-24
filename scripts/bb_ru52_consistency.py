"""Read-only final terminology correction and independent source comparison; no retranslations."""
from pathlib import Path
import json,hashlib,re
from bb_ru52_original import ROOT,SOURCE,call,SYSTEM,save,sha
OUT=ROOT/'results/ru52-consistency';OUT.mkdir(parents=True,exist_ok=True)
inp=json.loads((ROOT/'inputs/ru52-consistency.json').read_text());assert inp['complete_source_units']==45 and inp['frozen_source_sha256']=='6ebe811a5993d5aa2cb25c7c219d1383502ee422ed121d691113d14f581ba093';rows=inp['rows'];assert 1<=len(rows)<=40
source={r['id']:r for u in SOURCE for r in u['rows']}
for r in rows:
 s=source[r['source_id']];assert s.get('translate',True) and r['source']==s['text'] and r['source_sha256']==s['sha256']==sha(r['source']);assert sha(r['before'])==r['before_target_sha256'];assert r['proposal']==re.sub(r'Сакнуссем(?!м)','Сакнуссемм',r['before']) and r['proposal']!=r['before'];assert re.findall(r'\[\[FN:\d+\]\]',r['proposal'])==re.findall(r'\[\[FN:\d+\]\]',r['before'])
accepted=[];proof=[]
for stage in range(2):
 task=('Review these complete French source paragraphs against the existing Russian translation and the proposed minimal proper-name spelling correction. Canonical Russian name is Арне Сакнуссемм, genitive Сакнуссемма, instrumental Сакнуссеммом. French narrative Saknussemm is the same character. The proposed correction changes only the missing second м; do not change any other wording, punctuation, italics, note tokens or historical native Latin documents. Check source meaning and grammatical case; return the proposal exactly only when valid.' if stage==0 else 'Independently compare every corrected complete Russian paragraph to the French source. Verify the canonical Saknussemm spelling and grammatical ending, and verify that all other wording, meaning, punctuation, italics and note tokens are unchanged. Return each fully verified corrected target exactly. Do not introduce another edit.')
 payload={'task':task+' Return JSON only with rows:[{source_id,target}] and review:[{source_id,verdict,reason}]. Return every supplied ID in source order; verdict CORRECTED for the first correction pass and PASS for the final unchanged comparison. Give a specific source-based reason.','frozen_source_sha256':inp['frozen_source_sha256'],'source_rows':[{'id':r['source_id'],'text':r['source'],'sha256':r['source_sha256']} for r in rows],'current_rows':[{'source_id':r['source_id'],'target':r['before'] if stage==0 else r['proposal']} for r in rows],'proposed_rows':[{'source_id':r['source_id'],'target':r['proposal']} for r in rows],'prior_corrections':[] if stage==0 else accepted}
 last=None
 for attempt in range(3):
  request=dict(payload)
  if last:request['strict_retry_instruction']='Previous output failed strict acceptance: '+last+'. Preserve all requested IDs and exact approved proposed targets; report a specific valid verdict and reason.'
  path=OUT/f'stage{stage:02}.attempt{attempt:02}'
  try:
   result,meta=call(SYSTEM+' This is the complete-book cross-chapter terminology consistency review, not a new translation.',request,path,max_tokens=16384)
   assert meta['actual_model']==meta['requested_model']=='claude-sonnet-5-5';assert [x.get('source_id',x.get('id')) for x in result['rows']]==[r['source_id'] for r in rows];assert [x['target'] for x in result['rows']]==[r['proposal'] for r in rows],'ONLY_EXACT_CANONICAL_NAME_CORRECTION_ALLOWED';assert [x.get('source_id',x.get('id')) for x in result['review']]==[r['source_id'] for r in rows];assert all(x.get('verdict')==('CORRECTED' if stage==0 else 'PASS') and x.get('reason') for x in result['review']),'COMPLETE_EXPLICIT_REVIEW_VERDICTS_REQUIRED'
   proof.append({'stage':stage,'api':meta,'request_file':str(path.relative_to(ROOT))+'.request.json','response_file':str(path.relative_to(ROOT))+'.response.json','review':result['review']});break
  except (AssertionError,KeyError,ValueError,TypeError) as error:
   last=type(error).__name__+': '+str(error);save(Path(str(path)+'.validation-error.json'),{'error':last})
 else:raise RuntimeError('CONSISTENCY_REVIEW_NOT_ACCEPTED '+str(last))
 if stage==0:accepted=[{'source_id':r['source_id'],'unit':r['unit'],'source_sha256':r['source_sha256'],'before_target_sha256':r['before_target_sha256'],'target_sha256':sha(r['proposal']),'before':r['before'],'target':r['proposal'],'scope':'MISSING_SECOND_M_IN_CANONICAL_RUSSIAN_SAKNUSSEMM_NAME_ONLY'} for r in rows]
save(OUT/'accepted-overrides.json',{'state':'ACTUAL_AI_TERMINOLOGY_CORRECTION_AND_UNCHANGED_SOURCE_COMPARISON_PASS_PENDING_INDEPENDENT_BINDING','input_sha256':hashlib.sha256((ROOT/'inputs/ru52-consistency.json').read_bytes()).hexdigest(),'frozen_source_sha256':inp['frozen_source_sha256'],'rows':accepted,'actual_review_stages':proof,'book_release':False});print('ACTUAL_CONSISTENCY_CORRECTIONS_AND_SOURCE_COMPARISON_PASS',len(accepted))
