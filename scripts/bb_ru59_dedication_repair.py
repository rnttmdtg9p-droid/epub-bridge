from pathlib import Path
import json,re
from bb_ru59_original import ROOT,SOURCE,OUT,SYSTEM,checked_call,validate,save,sha
from bb_ru59_verify import qualify_unit
original,proof=qualify_unit(ROOT,SOURCE,1)
source=[r for r in SOURCE[0]['rows'] if r['id']=='059-en-dedication-02'];assert len(source)==1
before=[r for r in original['rows'] if r['source_id']==source[0]['id']];assert len(before)==1
folder=ROOT/'results/ru59-dedication-repair';folder.mkdir(parents=True,exist_ok=True)
save(folder/'initial-qualified.json',{'chapter':original,'proof':proof,'repair_reason':'Mixed Cyrillic/Latin Хindhead found by complete-target script scan.'})
current=before;history=[]
for turn in range(6):
 payload={'task':'Correct this original authorial dateline only. Translate both English place names idiomatically into Russian. Fix the mixed-script Хindhead. Preserve the two source places in order and the two-line dateline form. Return JSON only with rows:[{source_id,target}] and review:[{source_id,verdict:PASS or CORRECTED,reason}]. On subsequent comparison, preserve exact target bytes if correct.','source_rows':source,'draft_rows':current,'prior_corrections':history}
 result,meta=checked_call(SYSTEM+' Verify the corrected dedication dateline directly against its English source.',payload,folder/f'pass{turn:02}',source,True)
 target=result['rows'][0]['target'];assert len(target.splitlines())==2,'TWO_SOURCE_PLACE_LINES_REQUIRED'
 assert not any(re.search('[A-Za-z]',w) and re.search('[А-Яа-яЁё]',w) for w in re.findall(r'[^\W\d_]+',target)),'MIXED_SCRIPT_TOKEN'
 changes=[{'source_id':a['source_id'],'before':a['target'],'after':b['target']} for a,b in zip(current,result['rows']) if a['target']!=b['target']]
 history.append(dict(api=meta,review=result['review'],corrections=changes));current=result['rows']
 if not changes:
  save(folder/'repair.verified.json',dict(state='ACTUAL_SOURCE_BOUND_CORRECTION_AND_UNCHANGED_COMPARISON_COMPLETE',source_rows=source,initial=before,rows=current,history=history,book_release=False));print('DEDICATION_DATELINE_CORRECTION_COMPARISON_VERIFIED',flush=True);break
else:raise RuntimeError('CORRECTION_COMPARISON_NOT_STABLE')
