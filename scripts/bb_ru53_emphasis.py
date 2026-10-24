"""Source-bound italic repair of five actual qualified dispatch paragraphs; no Russian text in Git."""
from pathlib import Path
import json,hashlib,re,os
from bb_ru53_original import ROOT,SOURCE,call,SYSTEM,save,sha
from bb_ru53_verify import qualify_unit
OUT=ROOT/'results/ru53-emphasis';OUT.mkdir(parents=True,exist_ok=True)
chapter,report=qualify_unit(ROOT,SOURCE,17)
targets={r['source_id']:r for r in chapter['rows']};source={r['id']:r for u in SOURCE for r in u['rows']}
ids=['053-017-0060','053-017-0062','053-017-0069','053-017-0076','053-017-0103'];rows=[]
for sid in ids:
 before=targets[sid]['target'];plain=re.sub(r'</?i>','',before)
 if sid.endswith('0060'):
  line,body=before.split('\n');place,date=line.rsplit(', ',1);proposal='<i>'+place+',</i> <i>'+date+'</i>\n<i>'+body+'</i>'
 elif sid.endswith('0103'):
  start=plain.index('«',1);end=plain.index('»',start)+1;proposal='<i>'+plain[:start]+'</i>'+plain[start:end]+'<i>'+plain[end:]+'</i>'
 else:proposal='<i>'+before+'</i>'
 assert re.sub(r'</?i>','',proposal)==plain and proposal.count('\n')==before.count('\n')
 src=source[sid];assert len(re.findall(r'<i(?:\s[^>]*)?>',src.get('source_html') or src['text']))==proposal.count('<i>')
 rows.append(dict(unit=17,source_id=sid,source=src['text'],source_sha256=src['sha256'],source_html=src['source_html'],source_html_sha256=sha(src['source_html']),before=before,before_target_sha256=sha(before),proposal=proposal))
inp=dict(state='ACTUAL_INITIAL_UNIT17_FULL_AI_REVIEW_VERIFIED_NARROW_MARKUP_REPAIR',frozen_source_sha256='6ee415d89050dc6875ea96b1b3850d528e881503fa926b3361bebc6ff9135099',initial_unit_qualifications=[dict(unit=17,initial_qualified_target_sha256=report['qualified_target_sha256'])],rows=rows,book_release=False)
save(OUT/'input.json',inp)
accepted=[];proof=[]
for stage in range(2):
 task=('Review every complete French source paragraph against the Russian translation and proposed minimal italic-tag correction. Restore all source emphasis including distinct spans in dispatch heading and Genesis quotations; leave the newspaper title unitalic when source does. Every visible character and logical newline must remain unchanged. Return the proposed targets exactly only when valid.' if stage==0 else 'Independently compare every corrected full Russian paragraph against its French source. Verify source emphasis, distinct span boundaries, newspaper title exception, unchanged visible characters and logical lines. Return each fully verified target exactly without another edit.')
 payload={'task':task+' Return JSON only with rows:[{source_id,target}] and review:[{source_id,verdict,reason}]. Return every supplied ID in source order; verdict CORRECTED for the first correction pass and PASS for the final unchanged comparison. Give a specific source-based reason.','frozen_source_sha256':inp['frozen_source_sha256'],'source_rows':[{'id':r['source_id'],'text':r['source'],'sha256':r['source_sha256'],'source_html':r['source_html'],'source_html_sha256':r['source_html_sha256']} for r in rows],'current_rows':[{'source_id':r['source_id'],'target':r['before'] if stage==0 else r['proposal']} for r in rows],'proposed_rows':[{'source_id':r['source_id'],'target':r['proposal']} for r in rows],'prior_corrections':[] if stage==0 else accepted}
 last=None
 for attempt in range(3):
  request=dict(payload)
  if last:request['strict_retry_instruction']='Previous output failed strict acceptance: '+last+'. Preserve all requested IDs and exact approved proposed targets; report a specific valid verdict and reason.'
  path=OUT/f'stage{stage:02}.attempt{attempt:02}'
  try:
   result,meta=call(SYSTEM+' This is a narrowly source-bound italic markup correction, not a new translation.',request,path,max_tokens=16384)
   assert meta['actual_model']==meta['requested_model']=='claude-sonnet-5-5';assert [x.get('source_id',x.get('id')) for x in result['rows']]==[r['source_id'] for r in rows];assert [x['target'] for x in result['rows']]==[r['proposal'] for r in rows],'ONLY_EXACT_SOURCE_ITALIC_CORRECTION_ALLOWED';assert [x.get('source_id',x.get('id')) for x in result['review']]==[r['source_id'] for r in rows];assert all(x.get('verdict')==('CORRECTED' if stage==0 else 'PASS') and x.get('reason') for x in result['review']),'COMPLETE_EXPLICIT_REVIEW_VERDICTS_REQUIRED'
   proof.append({'stage':stage,'api':meta,'request_file':str(path.relative_to(ROOT))+'.request.json','response_file':str(path.relative_to(ROOT))+'.response.json','review':result['review']});break
  except (AssertionError,KeyError,ValueError,TypeError) as error:
   last=type(error).__name__+': '+str(error);save(Path(str(path)+'.validation-error.json'),{'error':last})
 else:raise RuntimeError('CONSISTENCY_REVIEW_NOT_ACCEPTED '+str(last))
 if stage==0:accepted=[{'source_id':r['source_id'],'unit':r['unit'],'source_sha256':r['source_sha256'],'before_target_sha256':r['before_target_sha256'],'target_sha256':sha(r['proposal']),'before':r['before'],'target':r['proposal'],'scope':'SOURCE_ITALIC_TAGS_ONLY_ALL_VISIBLE_CHARACTERS_AND_LINES_UNCHANGED'} for r in rows]
save(OUT/'accepted-overrides.json',{'state':'ACTUAL_AI_EMPHASIS_CORRECTION_AND_UNCHANGED_SOURCE_COMPARISON_PASS_PENDING_INDEPENDENT_BINDING','workflow_run_id':os.environ.get('GITHUB_RUN_ID'),'execution_commit':os.environ.get('GITHUB_SHA'),'input_sha256':hashlib.sha256((OUT/'input.json').read_bytes()).hexdigest(),'frozen_source_sha256':inp['frozen_source_sha256'],'rows':accepted,'actual_review_stages':proof,'book_release':False});print('ACTUAL_EMPHASIS_CORRECTIONS_AND_SOURCE_COMPARISON_PASS',len(accepted))
