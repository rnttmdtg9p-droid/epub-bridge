"""Translate four complete source paragraphs; preserve actual API correction evidence."""
import json,datetime
import bb_ru42_original as api
R=api.ROOT;O=R/'results/ru42-boundary-repairs';O.mkdir(parents=True,exist_ok=True)
data=json.loads((R/'inputs/ru42-boundary-repairs.json').read_text())
for record in data['rows']:
 n=record['unit'];folder=O/f'{n:03}';folder.mkdir(exist_ok=True);row=record['source'];rows=[row];context={'book':'Les Enfants du capitaine Grant','chapter':record['heading'],'unit':n,'paragraph_recovery':'Translate the COMPLETE authored paragraph as one coherent passage, not separate page fragments. Source exact words unchanged.'}
 translated,meta=api.deepl_translate(rows,folder/'translation',context);draft=api.validate(translated['rows'],rows)
 payload={'task':'Review the complete French paragraph and new Russian translation. Correct meaning, syntax, all facts, agency, negation, style, names and italics. Return JSON only with rows [{source_id,target}] and review [{source_id,verdict: PASS or CORRECTED,reason}]. Every supplied row exactly once.','context':context,'source_rows':rows,'draft_rows':draft}
 result,review=api.checked_call(api.SYSTEM+' Apply independent full source-aligned literary review.',payload,folder/'review',rows,True)
 changes=[{'source_id':a['source_id'],'before':a['target'],'after':b['target']} for a,b in zip(draft,result['rows']) if a['target']!=b['target']]
 item={'unit':n,'rows':result['rows'],'review':result['review'],'corrections':changes,'translation_api':meta,'review_api':review}
 final=api.compare_final(n,0,rows,item,folder,context);final['joined_source_ids']=record['joined_source_ids'];final['source']=row;api.save(folder/'paragraph.verified.json',final);print('FULL_PARAGRAPH_TRANSLATED_REVIEWED_STABLE',n,flush=True)
api.save(O/'run-status.json',{'state':'FOUR_COMPLETE_PARAGRAPHS_TRANSLATED_REVIEWED_STABLE_PENDING_INDEPENDENT_REPLAY','units':[r['unit'] for r in data['rows']],'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'checkpoint_is_stop_condition':False})
