import base64, concurrent.futures, datetime, gzip, hashlib, json, os, subprocess, threading, time, urllib.request, urllib.error
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/ru46-review'; OUT.mkdir(parents=True,exist_ok=True)
PACKED=base64.b64decode(''.join(p.read_text() for p in sorted((ROOT/'inputs').glob('ru46-review-input.part*.b64'))))
assert hashlib.sha256(PACKED).hexdigest()=='6f4c60c93af17582babc51d94d7e3339bfba558cf58baaa56d6057f3dfb3bd40'
INPUT=json.loads(gzip.decompress(PACKED));SOURCE=INPUT['source'];DRAFT={u['unit']:u for u in INPUT['draft']}
KEY=os.environ.get('ANTHROPIC_API_KEY','').strip()
MODEL='claude-sonnet-5-5'
LOCK=threading.Lock()
def sha(s): return hashlib.sha256(s.encode()).hexdigest()
def save(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    t=p.with_suffix(p.suffix+'.tmp'); t.write_text(json.dumps(v,ensure_ascii=False,indent=2));t.replace(p)
def commit():
    with LOCK:
        subprocess.run(['git','add','results/ru46-review'],cwd=ROOT,check=True)
        if subprocess.run(['git','diff','--cached','--quiet'],cwd=ROOT).returncode:
            subprocess.run(['git','commit','-m','Checkpoint rank46 complete source-aligned literary review and comparison'],cwd=ROOT,check=True)
            
            for push_attempt in range(3):
                pushed=subprocess.run(['git','push','origin','HEAD'],cwd=ROOT)
                if pushed.returncode==0:break
                if push_attempt==2:raise RuntimeError('CHECKPOINT_PUSH_FAILED_AFTER_SAFE_RETRIES')
                time.sleep(2*(push_attempt+1))

SYSTEM='You are the authorized Boundary Bay complete source-aligned literary reviewer for Anton Chekhov, Three Sisters (1901), ORIGINAL RUSSIAN, not a translation. Review EVERY source paragraph, heading, cast entry, stage direction and editorial note. The authenticated Russian text is the authority: preserve each word, speaker, paragraph, punctuation, inline HTML, foreign-language phrase, and [[FN:number]] note token EXACTLY. Do not rewrite Chekhov into modern prose or correct his deliberate grammar, repeated words, ellipses, foreign-language mistakes or character voices. Review fidelity and literary integrity, and use PASS with a specific assessment when text matches. If any accidental target corruption exists, restore the EXACT supplied source bytes. The 12 preserved explanatory notes are Wikisource editorial glosses, not Chekhov authorial notes; do not invent attribution. There is no new translation and no need to improve already authentic literary Russian. Return all requested rows and all review assessments in order, JSON only.'

def call(system,payload,path,max_tokens=32768):
    body={'model':MODEL,'max_tokens':max_tokens,'output_config':{'effort':'medium'},'system':system+' Keep each per-row assessment concise (at most40words) while checking every source row completely.','messages':[{'role':'user','content':json.dumps(payload,ensure_ascii=False)}]}
    save(Path(str(path)+'.request.json'),body)
    headers={'x-api-key':KEY,'anthropic-version':'2023-06-01','content-type':'application/json'}
    workspace=os.environ.get('ANTHROPIC_WORKSPACE_ID','').strip()
    if workspace:headers['anthropic-workspace-id']=workspace
    for attempt in range(4):
        try:
            req=urllib.request.Request('https://api.anthropic.com/v1/messages',data=json.dumps(body,ensure_ascii=False).encode(),headers=headers)
            with urllib.request.urlopen(req,timeout=240) as r: raw=json.load(r)
            save(Path(str(path)+'.response.json'),raw)
            if raw.get('stop_reason')!='end_turn':raise ValueError('INCOMPLETE_RESPONSE_'+str(raw.get('stop_reason')))
            text='\n'.join(x['text'] for x in raw['content'] if x.get('type')=='text').strip()
            if text.startswith('```'):text=text.split('\n',1)[1].rsplit('```',1)[0]
            return json.JSONDecoder().raw_decode(text[text.index('{'):])[0],{'requested_model':MODEL,'actual_model':raw.get('model'),'response_id':raw.get('id'),'usage':raw.get('usage')}
        except urllib.error.HTTPError as e:
            if e.code in (429,500,502,503,529) and attempt<3:time.sleep(5*(attempt+1));continue
            raise RuntimeError('CLAUDE_HTTP_'+str(e.code)) from None
    raise RuntimeError('RETRY_EXHAUSTED')

def deepl_translate(rows,path,context):
    from html.parser import HTMLParser
    from html import escape
    key=os.environ.get('DEEPL_AUTH_KEY','').strip()
    if not key:raise RuntimeError('MISSING_DEEPL_AUTH_KEY')
    class Clean(HTMLParser):
        def __init__(self):super().__init__();self.parts=[]
        def handle_starttag(self,tag,attrs):
            if tag in ('i','em'):self.parts.append('<i>')
            elif tag=='br':self.parts.append('<br/>')
        def handle_endtag(self,tag):
            if tag in ('i','em'):self.parts.append('</i>')
        def handle_data(self,data):self.parts.append(escape(data))
    texts=[]
    selected=[row for row in rows if row.get('translate',True)]
    for row in selected:
        c=Clean();c.feed(row.get('source_html') or escape(row['text']));texts.append('<p>'+''.join(c.parts)+'</p>')
    host='https://api-free.deepl.com' if key.endswith(':fx') else 'https://api.deepl.com'
    payload={'text':texts,'source_lang':'EN','target_lang':'RU','model_type':'quality_optimized','tag_handling':'html','context':'New complete literary Russian translation of Jack London, The Call of the Wild (1903), directly from English. Preserve every paragraph, fact, name, historical peculiarity and italic span. '+json.dumps(context,ensure_ascii=False)}
    request_file=Path(str(path)+'.deepl.request.json')
    if request_file.exists():assert json.loads(request_file.read_text())==payload,'EXISTING_DEEPL_REQUEST_MISMATCH'
    else:save(request_file,payload)
    for attempt in range(4):
        try:
            response_file=Path(str(path)+f'.deepl.attempt{attempt:02}.response.json')
            if response_file.exists():raw=json.loads(response_file.read_text())
            else:
                req=urllib.request.Request(host+'/v2/translate',data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Authorization':'DeepL-Auth-Key '+key,'Content-Type':'application/json'})
                with urllib.request.urlopen(req,timeout=180) as response:raw=json.load(response)
                save(response_file,raw)
            targets=raw.get('translations',[]);assert len(targets)==len(selected),'DEEPL_ROW_COUNT'
            result=[]
            for row,target in zip(selected,targets):
                assert target.get('model_type_used')=='quality_optimized','DEEPL_MODEL_NOT_QUALITY_OPTIMIZED'
                c=Clean();c.feed(target['text']);text=''.join(c.parts).strip().replace('<br/>','\n');assert text
                result.append({'source_id':row['id'],'target':text})
            byid={item['source_id']:item for item in result};line_recoveries=[]
            for src in selected:
                if not src.get('source_lines'):continue
                prior=byid[src['id']]['target'];parts=prior.split('\n');valid_shape=len(parts)==len(src['source_lines']) and [not x.strip() for x in parts]==[not x.strip() for x in src['source_lines']]
                if valid_shape:continue
                rejection={'source_id':src['id'],'source_sha256':src['sha256'],'prior_bulk_target':prior,'prior_bulk_target_sha256':sha(prior),'reason':'AUTHENTIC_SOURCE_LINE_OR_STANZA_PATTERN_CHANGED','source_line_count':len(src['source_lines']),'actual_bulk_line_count':len(parts),'bulk_response_file':str(response_file.relative_to(ROOT))}
                save(Path(str(path)+'.'+src['id']+'.rejected-lineation.json'),rejection)
                fixed,receipt=deepl_source_line_recovery(src,Path(str(path)+'.'+src['id']),context);byid[src['id']]['target']=fixed;line_recoveries.append(receipt)
            result=[byid[row['id']] if row.get('translate',True) else {'source_id':row['id'],'target':row['text'],'source_native_verbatim':True} for row in rows]
            return {'rows':result},{'provider':'DeepL','requested_model':'quality_optimized','actual_model':'quality_optimized','response_file':str(response_file.relative_to(ROOT)),'source_line_recoveries':line_recoveries}
        except urllib.error.HTTPError as error:
            save(Path(str(path)+f'.deepl.attempt{attempt:02}.error.json'),{'http_status':error.code})
            if error.code in [429,500,502,503] and attempt<3:time.sleep(5*(attempt+1));continue
            raise RuntimeError('DEEPL_HTTP_'+str(error.code)) from None
    raise RuntimeError('DEEPL_RETRY_EXHAUSTED')

def deepl_source_line_recovery(row,path,context):
    """Translate only a rejected lineated row, retaining raw rejected bulk output."""
    from html import unescape
    key=os.environ.get('DEEPL_AUTH_KEY','').strip()
    if not key:raise RuntimeError('MISSING_DEEPL_AUTH_KEY')
    lines=row['source_lines'];positions=[i for i,t in enumerate(lines) if t.strip()]
    assert positions and len(positions)<=50
    host='https://api-free.deepl.com' if key.endswith(':fx') else 'https://api.deepl.com'
    payload={'text':[lines[i] for i in positions],'source_lang':'EN','target_lang':'RU','model_type':'quality_optimized','context':'Literary translation of Jack London, The Call of the Wild, English to Russian. These are ordered individual lines of ONE authentic source verse/document row; do not add or omit content. Complete source row for context: '+row['text']+' Context: '+json.dumps(context,ensure_ascii=False)}
    reqpath=Path(str(path)+'.source-lines.deepl.request.json')
    if reqpath.exists():assert json.loads(reqpath.read_text())==payload,'LINE_RECOVERY_REQUEST_MISMATCH'
    else:save(reqpath,payload)
    for attempt in range(4):
        responsepath=Path(str(path)+f'.source-lines.deepl.attempt{attempt:02}.response.json')
        try:
            if responsepath.exists():raw=json.loads(responsepath.read_text())
            else:
                req=urllib.request.Request(host+'/v2/translate',data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Authorization':'DeepL-Auth-Key '+key,'Content-Type':'application/json'})
                with urllib.request.urlopen(req,timeout=180) as response:raw=json.load(response)
                save(responsepath,raw)
            ts=raw['translations'];assert len(ts)==len(positions),'LINE_RECOVERY_COUNT';result=['']*len(lines)
            for position,t in zip(positions,ts):
                assert t.get('model_type_used')=='quality_optimized','LINE_RECOVERY_MODEL'
                target=t['text'].strip();assert target and '\n' not in target and '\r' not in target,'LINE_RECOVERY_NEWLINE'
                result[position]=target
            return '\n'.join(result),{'source_id':row['id'],'source_sha256':row['sha256'],'source_line_positions':positions,'source_line_count':len(lines),'request_file':str(reqpath.relative_to(ROOT)),'response_file':str(responsepath.relative_to(ROOT)),'requested_model':'quality_optimized','actual_model':'quality_optimized','state':'ACTUAL_ORDERED_LINE_TRANSLATION_ASSEMBLED_FOR_FULL_AI_REVIEW','target_sha256':sha('\n'.join(result))}
        except urllib.error.HTTPError as error:
            save(Path(str(path)+f'.source-lines.deepl.attempt{attempt:02}.error.json'),{'http_status':error.code})
            if error.code in [429,500,502,503] and attempt<3:time.sleep(5*(attempt+1));continue
            raise RuntimeError('DEEPL_LINE_RECOVERY_HTTP_'+str(error.code)) from None
    raise RuntimeError('DEEPL_LINE_RECOVERY_RETRY_EXHAUSTED')

def validate(rows,original):
    assert isinstance(rows,list) and len(rows)==len(original),'ROW_COUNT'
    for row,src in zip(rows,original):
        row['source_id']=row.get('source_id',row.get('id'))
        assert row['source_id']==src['id'],'SOURCE_ID_ALIGNMENT'
        assert row.get('source_sha256',src['sha256'])==src['sha256'],'SOURCE_HASH_ALIGNMENT'
        row['source_sha256']=src['sha256']
        assert isinstance(row['target'],str) and row['target'].strip(),'EMPTY_TARGET'
        import re
        assert re.findall(r'\[\[FN:\d+\]\]',row['target'])==re.findall(r'\[\[FN:\d+\]\]',src['text']),'NOTE_TOKEN_PRESERVATION'
        if not src.get('translate',True):assert row['target']==src['text'],'AUTHENTIC_FOREIGN_SOURCE_VERBATIM'
        if src.get('source_lines'):
            assert len(row['target'].split('\n'))==len(src['source_lines']),'SOURCE_DISPLAY_LINE_COUNT'
            assert [not x.strip() for x in row['target'].split('\n')]==[not x.strip() for x in src['source_lines']],'SOURCE_STANZA_BREAKS'
        if src.get('tag')=='section_heading':assert row['target']==src['text'],'ORIGINAL_SUBSECTION_NUMERAL_CHANGED'
        if src.get('preserve_original_verbatim'):assert row['target']==src['text'],'AUTHENTIC_ORIGINAL_RUSSIAN_CHANGED'
        row.update(target_id='ru-'+src['id'],tag=src['tag'],target_sha256=sha(row['target']))
    return rows

def checked_call(system,payload,path,source,review=False):
    # Original English/German/English facsimiles and Maori verse are not new translations.
    # Keep exact source-native bytes; show them as protected context, never editable rows.
    native=[r for r in source if not r.get('translate',True)]
    editable=[r for r in source if r.get('translate',True)]
    native_ids={r['id'] for r in native};last=None
    existing=list(path.parent.glob(path.name+'.attempt*.request.json'))
    import re
    start=max([int(re.search(r'\.attempt(\d+)\.request',q.name)[1])+1 for q in existing] or [0])
    for attempt in range(start,start+3):
        request=dict(payload)
        request['source_rows']=[{k:v for k,v in r.items() if k not in ['source_fragments','source_child_indices']} for r in editable]
        if native:
            request['source_rows']=[{k:v for k,v in r.items() if k not in ['source_fragments','source_child_indices']} for r in editable]
            request['protected_source_native_rows']=native
            request['protected_source_instruction']='Read these authentic foreign-language documents/verse as context. They are copied exactly from source and are NOT editable or translated rows. Return rows and review arrays only for source_rows. Do not add protected IDs to your returned arrays.'
            for key in ['draft_rows','final_rows']:
                if key in request:request[key]=[r for r in request[key] if r['source_id'] not in native_ids]
        if last:request['format_retry_instruction']='Previous response failed strict validation: '+last+'. Return only requested JSON with every editable source_id in supplied order.'
        try:
            budget=65536 if last and 'INCOMPLETE_RESPONSE_max_tokens' in last else 32768
            result,meta=call(system,request,Path(str(path)+f'.attempt{attempt:02}'),max_tokens=budget)
            result['rows']=validate(result['rows'],editable)
            if review:
                for r in result.get('review',[]):r['source_id']=r.get('source_id',r.get('id'))
                assert [r['source_id'] for r in result['review']]==[r['id'] for r in editable],'REVIEW_COVERAGE'
                assert all(r.get('reason') for r in result['review']),'REVIEW_REASON'
            if native:
                byid={r['source_id']:r for r in result['rows']}
                for r in native:byid[r['id']]=validate([{'source_id':r['id'],'target':r['text']}],[r])[0]
                result['rows']=[byid[r['id']] for r in source]
                if review:
                    verdicts={r['source_id']:r for r in result['review']}
                    for r in native:verdicts[r['id']]={'source_id':r['id'],'verdict':'NOT_APPLICABLE_SOURCE_NATIVE_FOREIGN','reason':'Exact unmodified original foreign document or verse; no AI translation. Independent source/hash/lineation comparison, not an invented provider verdict.'}
                    result['review']=[verdicts[r['id']] for r in source]
                meta['protected_source_native_ids']=[r['id'] for r in native]
                meta['actual_provider_review_ids']=[r['id'] for r in editable]
            return result,meta
        except (AssertionError,KeyError,ValueError,TypeError) as error:
            last=type(error).__name__+': '+str(error)
            save(Path(str(path)+f'.attempt{attempt:02}.validation-error.json'),{'error':last})
    raise RuntimeError('STRICT_FORMAT_RETRIES_EXHAUSTED '+str(last))

def compare_final(unit,index,rows,item,folder,context):
    current=validate(item['rows'],rows)
    comparison=[]
    pending=folder/f'batch{index:03}.comparison_pending.json'
    if pending.exists():
        recovered=json.loads(pending.read_text())
        current=validate(recovered['rows'],rows)
        comparison=recovered['comparison']
        assert comparison and comparison[-1]['corrections'], 'PENDING_COMPARISON_LEDGER'
    # Continue from applied corrections and retain every earlier API exchange.
    # A nonstable result remains pending; extra passes do not relax acceptance.
    start_turn=len(comparison)
    for turn in range(start_turn,start_turn+6):
        request={'task':'Compare EVERY final Russian row against its original Russian source. Verify all prior corrections in the supplied ledger. Check meaning, agency, negation, omissions, idiom, register, italics and original verse line count. Return JSON only: {"rows":[{"source_id":"exact source id","target":"complete corrected Russian"}],"review":[{"source_id":"exact source id","verdict":"PASS or CORRECTED","reason":"specific source-based assessment"}]}. Return every row in both arrays, in source order. Do not return source hashes; host binds immutable hashes. If already faithful and idiomatic, preserve the exact target bytes.','context':context,'source_rows':rows,'final_rows':current,'prior_corrections':item.get('corrections',[])+[c for previous in comparison for c in previous['corrections']]}
        result,meta=checked_call(SYSTEM+' You are the final source-aligned AI comparison reviewer, checking corrected text against original Russian independently of prior verdicts.',request,folder/f'batch{index:03}.comparison{turn:02}',rows,True)
        revised=result['rows']
        changes=[{'source_id':a['source_id'],'before':a['target'],'after':b['target']} for a,b in zip(current,revised) if a['target']!=b['target']]
        comparison.append({'api':meta,'review':result['review'],'corrections':changes})
        current=revised
        if not changes:
            final=dict(item);final.update(rows=current,comparison=comparison,state='SOURCE_ALIGNED_AI_REVIEW_AND_CORRECTION_COMPARISON_COMPLETE_PENDING_BOOK_QA')
            save(folder/f'batch{index:03}.comparison_verified.json',final)
            return final
    save(folder/f'batch{index:03}.comparison_pending.json',{'rows':current,'comparison':comparison,'state':'CORRECTIONS_APPLIED_RECHECK_STILL_REQUIRED'})
    raise RuntimeError('COMPARISON_NOT_STABLE_AFTER_RESUMED_PASSES')

def translate(unit):
    number=unit['unit'];folder=OUT/f'{number:03}';folder.mkdir(exist_ok=True)
    heading={'id':unit['heading_id'],'tag':'heading','text':unit['heading'],'sha256':sha(unit['heading'])}
    allrows=[heading]+unit['rows'];final=[];reviews=[];batches=[];batch=[];chars=0
    for row in allrows:
        assert sha(row['text'])==row['sha256']
        if batch and (len(batch)>=32 or chars+len(row['text'])>12500):batches.append(batch);batch=[];chars=0
        batch.append(row);chars+=len(row['text'])
    if batch:batches.append(batch)
    for index,rows in enumerate(batches):
        verified=folder/f'batch{index:03}.comparison_verified.json'
        if verified.exists():
            item=json.loads(verified.read_text());final.extend(validate(item['rows'],rows));reviews.append(item);continue
        context={'book':'Три сестры — Антон Чехов (1901), original Russian','chapter':unit['heading'],'unit':number,'complete_act_original_russian':[r['text'] for r in unit['rows'] if r['tag']!='illustration_caption'],'previous_source_russian':[r['text'] for r in allrows[max(0,allrows.index(rows[0])-3):allrows.index(rows[0])]],'previous_reviewed_russian':[r['target'] for r in final[-3:]]}
        done=folder/f'batch{index:03}.reviewed.json'
        if done.exists():
            item=json.loads(done.read_text());validate(item['rows'],rows)
            assert [r['source_id'] for r in item['review']]==[r['id'] for r in rows]
        else:
            prior=DRAFT[number];byid={r['source_id']:r for r in [prior['heading']]+prior['rows']};original=validate([{'source_id':r['id'],'target':byid[r['id']]['target']} for r in rows],rows);meta={'state':'AUTHENTIC_ORIGINAL_RUSSIAN_VERBATIM_NO_TRANSLATION_API_USED','input_packed_sha256':hashlib.sha256(PACKED).hexdigest(),'prior_prepared_drafts_embedded_in_frozen_input':True}
            request={'task':'Read EVERY original Russian source and target row. Verify exact fidelity, speaker and stage-direction integrity, punctuation, inline markup and notes. Restore only accidental target corruption from the exact source; never rewrite authenticated Russian. Return JSON only: {"rows":[{"source_id":"exact source id","target":"complete final Russian"}],"review":[{"source_id":"exact source id","verdict":"PASS or CORRECTED","reason":"specific finding or source-based assessment"}]}. Every row in both arrays, in source order. Do not return source hashes; host binds immutable hashes.','context':context,'source_rows':rows,'draft_rows':original}
            result,review_meta=checked_call(SYSTEM+' You are an independent source-aligned AI literary reviewer. Apply all corrections directly.',request,folder/f'batch{index:03}.review',rows,True)
            corrected=result['rows'];changes=[{'source_id':a['source_id'],'before':a['target'],'after':b['target']} for a,b in zip(original,corrected) if a['target']!=b['target']]
            item={'unit':number,'batch':index,'state':'SOURCE_ALIGNED_AI_REVIEWED_PENDING_COMPARISON','rows':corrected,'review':result['review'],'corrections':changes,'translation_api':meta,'review_api':review_meta};save(done,item)
        item=compare_final(number,index,rows,item,folder,context)
        final.extend(item['rows']);reviews.append(item)
        print('UNIT',number,'BATCH',index+1,'OF',len(batches),'COMPARISON_VERIFIED',flush=True)
        save(OUT/'run-status.json',{'state':'RUNNING_CONTINUOUS_TRANSLATION_REVIEW','unit':number,'last_complete_batch':index,'run_id':os.environ.get('GITHUB_RUN_ID'),'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'checkpoint_is_stop_condition':False})
        commit()
    assert len(final)==len(allrows)
    save(folder/'chapter.reviewed.json',{'unit':number,'heading':final[0],'rows':final[1:],'source_rows':len(unit['rows']),'state':'SOURCE_ALIGNED_AI_REVIEW_AND_CORRECTION_COMPARISON_COMPLETE_PENDING_BOOK_QA','review_batches':len(reviews),'master':'5.0.70'})
    # Persist real provider exchanges before independent acceptance checks.
    commit()
    from bb_ru46_verify import qualify_unit
    try:
        accepted,qualification=qualify_unit(ROOT,SOURCE,number,output_name="ru46-review")
        save(OUT/'qualified'/f'unit-{number:03}.json',accepted)
        save(OUT/'qualified'/f'unit-{number:03}.evidence.json',qualification)
        (OUT/'qualified'/f'unit-{number:03}.blocked.json').unlink(missing_ok=True)
    except Exception as error:
        (OUT/'qualified'/f'unit-{number:03}.json').unlink(missing_ok=True)
        (OUT/'qualified'/f'unit-{number:03}.evidence.json').unlink(missing_ok=True)
        save(OUT/'qualified'/f'unit-{number:03}.blocked.json',{'unit':number,'state':'REVIEW_REQUIRED','error':type(error).__name__+': '+str(error),'book_release':False})
        print('INDEPENDENT_RECONCILIATION_BLOCKED',number,str(error),flush=True)
    commit();print('UNIT_CHECKPOINT',number,'ROWS',len(unit['rows']),flush=True)
    return number

if __name__=='__main__':
    assert len(SOURCE)==5 and sum(len(u['rows']) for u in SOURCE)==1061
    if not KEY:raise SystemExit('MISSING_ANTHROPIC_API_KEY')
    errors=[]
    save(OUT/'run-status.json',{'state':'RUNNING_CONTINUOUS_COMPLETE_SOURCE_REVIEW','run_id':os.environ.get('GITHUB_RUN_ID'),'source_units':5,'source_rows':1061,'checkpoint_is_stop_condition':False,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()});commit()
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        jobs={pool.submit(translate,u):u['unit'] for u in SOURCE}
        for job in concurrent.futures.as_completed(jobs):
            try:job.result()
            except Exception as error:errors.append({'unit':jobs[job],'error':str(error)});print('UNIT_FAILED',jobs[job],str(error),flush=True)
    save(OUT/'run-status.json',{'state':'PARTIAL_FAILURE' if errors else 'API_TRANSLATION_REVIEW_COMPLETE_PENDING_BOOK_QA','errors':errors,'source_units':5,'source_rows':1061,'translation_provider':'NOT_APPLICABLE_ORIGINAL_RUSSIAN','review_provider':MODEL,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()});commit()
    if errors:raise SystemExit(1)
