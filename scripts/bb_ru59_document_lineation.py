from pathlib import Path
import json,re
from bb_ru59_original import ROOT,SOURCE,SYSTEM,checked_call,save,sha
from bb_ru59_verify import qualify_unit
original,proof=qualify_unit(ROOT,SOURCE,3);rows=json.loads((ROOT/'inputs/ru59-document-lineation.json').read_text());by={r['source_id']:r for r in original['rows']};before=[by[r['id']] for r in rows];current=before;folder=ROOT/'results/ru59-document-lineation';folder.mkdir(parents=True,exist_ok=True);history=[]
norm=lambda s:' '.join(s.split())
save(folder/'initial.json',dict(source_rows=rows,before=before,initial_qualification=proof))
for turn in range(6):
 request={'task':'Restore only the exact source-authenticated paragraph breaks inside these two quoted prose documents. Preserve every translated visible character, word, punctuation and italic span unchanged. The source_lines list gives the exact sequence of source subparagraphs: return one target line for each source line, in that order; no added blank lines. Only whitespace/newline changes are authorized. Return a single JSON object: rows:[{source_id,target}] and review:[{source_id,verdict:PASS or CORRECTED,reason}]. Compare the corrected placement with every English source segment. On a later comparison preserve exact target bytes when correct.','source_rows':rows,'draft_rows':current,'prior_corrections':history}
 result,meta=checked_call(SYSTEM+' This is a source-bound layout/lineation correction, not a new translation.',request,folder/f'pass{turn:02}',rows,True)
 for a,b in zip(before,result['rows']):assert norm(a['target'])==norm(b['target']),'VISIBLE_TEXT_CHANGED_IN_LINEATION_ONLY_REPAIR'
 changes=[dict(source_id=a['source_id'],before=a['target'],after=b['target']) for a,b in zip(current,result['rows']) if a['target']!=b['target']];history.append(dict(api=meta,review=result['review'],corrections=changes));current=result['rows']
 if not changes:
  save(folder/'repair.verified.json',dict(state='ACTUAL_SOURCE_BREAKS_CORRECTED_AND_STABLE_COMPARISON_COMPLETE',source_rows=rows,before=before,rows=current,history=history,book_release=False));print('DOCUMENT_LINEATION_COMPARISON_VERIFIED',flush=True);break
else:raise RuntimeError('LINEATION_COMPARISON_NOT_STABLE')
