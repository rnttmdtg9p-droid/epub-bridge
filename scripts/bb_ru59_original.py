import base64, concurrent.futures, datetime, gzip, hashlib, json, os, subprocess, threading, time, urllib.request, urllib.error
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/ru59-original'; OUT.mkdir(parents=True,exist_ok=True)
PACKED=base64.b64decode(''.join(p.read_text() for p in sorted((ROOT/'inputs').glob('ru59-source.part*.b64'))))
assert hashlib.sha256(PACKED).hexdigest()=='98a9f07c09b708413823be347d0c80bb74bdb96c5e18eb989bc6b8cc2ec8d95e'
SOURCE=json.loads(gzip.decompress(PACKED))
KEY=os.environ.get('ANTHROPIC_API_KEY','').strip()
MODEL='claude-sonnet-5-5'
LOCK=threading.Lock()
def sha(s): return hashlib.sha256(s.encode()).hexdigest()
def save(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    t=p.with_suffix(p.suffix+'.tmp'); t.write_text(json.dumps(v,ensure_ascii=False,indent=2));t.replace(p)
def commit():
    # Read-only execution: provider evidence is uploaded as per-unit artifacts, never pushed into Git.
    return

GLOSSARY='Sherlock Holmes — Шерлок Холмс; Watson — Ватсон; James Mortimer — Джеймс Мортимер; Henry Baskerville — Генри Баскервиль; Charles Baskerville — Чарльз Баскервиль; Stapleton — Стэплтон; Beryl — Берил; Barrymore — Бэрримор; Selden — Селден; Laura Lyons — Лора Лайонс; Grimpen Mire — Гримпенская трясина; Baskerville Hall — Баскервиль-холл; Dartmoor — Дартмур; Robinson — Робинсон. Preserve Watson first-person narration, Holmes precise deductions, ambiguity and period setting.'
SYSTEM='You are the authorized Boundary Bay literary translator and source-aligned reviewer of Arthur Conan Doyle, The Hound of the Baskervilles (1902), from the complete frozen English original into fluent Russian. Translate only supplied English; do not consult or reproduce an existing Russian translation. Preserve every fact, agency, speaker, negation, number, dialogue turn, paragraph boundary, ambiguity, letter, signature, dateline, dedication and italic emphasis. No summary, omission, censorship, added explanation, or modernization of facts. Apply all fidelity and literary corrections automatically. Preserve meaningful source line breaks and inline italics with <i>, superscripts/subscripts if present; no other generated HTML or Markdown. Chapter headings require faithful sentence-case Russian, retaining original numbering. The dedication heading is editorial metadata; retain the complete authentic dedication text separately. '+GLOSSARY+' Return JSON only.'

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
            elif tag in ('sup','sub'):self.parts.append('<'+tag+'>')
            elif tag=='br':self.parts.append('<br/>')
        def handle_endtag(self,tag):
            if tag in ('i','em'):self.parts.append('</i>')
            elif tag in ('sup','sub'):self.parts.append('</'+tag+'>')
        def handle_data(self,data):self.parts.append(escape(data))
    texts=[]
    selected=[row for row in rows if row.get('translate',True)]
    for row in selected:
        c=Clean();c.feed(row.get('source_html') or escape(row['text']));texts.append('<p>'+''.join(c.parts)+'</p>')
    host='https://api-free.deepl.com' if key.endswith(':fx') else 'https://api.deepl.com'
    payload={'text':texts,'source_lang':'EN','target_lang':'RU','model_type':'quality_optimized','tag_handling':'html','context':'New complete literary Russian translation of Jules Verne, Michel Strogoff (Hetzel1905;first published1876), directly from English. Preserve every paragraph, fact, name, historical peculiarity and italic span. '+json.dumps(context,ensure_ascii=False)}
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
    payload={'text':[lines[i] for i in positions],'source_lang':'EN','target_lang':'RU','model_type':'quality_optimized','context':'Literary translation of Jules Verne, Michel Strogoff, English to Russian. These are ordered individual lines of ONE authentic source verse/document row; do not add or omit content. Complete source row for context: '+row['text']+' Context: '+json.dumps(context,ensure_ascii=False)}
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
        row.update(target_id='ru-'+src['id'],tag=src['tag'],target_sha256=sha(row['target']))
    return rows

def checked_call(system,payload,path,source,review=False):
    # Native Roman labels, ciphers, Latin and runic documents are not new translations.
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
                assert all(r.get('verdict') in ['PASS','CORRECTED'] for r in result['review']),'REVIEW_VERDICT_SCHEMA'
            if native:
                byid={r['source_id']:r for r in result['rows']}
                for r in native:byid[r['id']]=validate([{'source_id':r['id'],'target':r['text']}],[r])[0]
                result['rows']=[byid[r['id']] for r in source]
                if review:
                    verdicts={r['source_id']:r for r in result['review']}
                    for r in native:verdicts[r['id']]={'source_id':r['id'],'verdict':'NOT_APPLICABLE_SOURCE_NATIVE_FOREIGN','reason':'Exact unmodified original structural label, literal cipher, Latin or documentary token; no AI translation. Independent source/hash/lineation comparison, not an invented provider verdict.'}
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
        request={'task':'Compare EVERY final Russian row against its English source. Verify all prior corrections in the supplied ledger. Check meaning, agency, negation, omissions, idiom, register, italics and original verse line count. Return JSON only: {"rows":[{"source_id":"exact source id","target":"complete corrected Russian"}],"review":[{"source_id":"exact source id","verdict":"PASS or CORRECTED","reason":"specific source-based assessment"}]}. Return every row in both arrays, in source order. Do not return source hashes; host binds immutable hashes. If already faithful and idiomatic, preserve the exact target bytes.','context':context,'source_rows':rows,'final_rows':current,'prior_corrections':item.get('corrections',[])+[c for previous in comparison for c in previous['corrections']]}
        result,meta=checked_call(SYSTEM+' You are the final source-aligned AI comparison reviewer, checking corrected text against English independently of prior verdicts.',request,folder/f'batch{index:03}.comparison{turn:02}',rows,True)
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
    heading={'id':unit['heading_id'],'tag':'heading','text':unit['heading'],'sha256':sha(unit['heading']),'translate':True,'role':'SOURCE_AUTHENTIC_HEADING'}
    allrows=[heading]+unit['rows'];final=[];reviews=[];batches=[];batch=[];chars=0
    for row in allrows:
        assert sha(row['text'])==row['sha256']
        if batch and (len(batch)>=20 or chars+len(row['text'])>11000):batches.append(batch);batch=[];chars=0
        batch.append(row);chars+=len(row['text'])
    if batch:batches.append(batch)
    for index,rows in enumerate(batches):
        verified=folder/f'batch{index:03}.comparison_verified.json'
        if verified.exists():
            item=json.loads(verified.read_text())
            expected_native={r['id'] for r in rows if not r.get('translate',True)}
            last_review=item.get('comparison',[{}])[-1].get('review',[])
            valid_schema=len(last_review)==len(rows) and all(r.get('verdict') in (['NOT_APPLICABLE_SOURCE_NATIVE_FOREIGN'] if r.get('source_id') in expected_native else ['PASS','CORRECTED']) and r.get('reason') for r in last_review)
            if valid_schema:
                final.extend(validate(item['rows'],rows));reviews.append(item);continue
            # Preserve malformed actual exchange; repeat source comparison from the saved draft.
            verified.rename(verified.with_name(verified.name+'.superseded-invalid-verdict-schema.json'))
            save(folder/f'batch{index:03}.cache-rejection.json',{'state':'RECHECK_REQUIRED','reason':'Final actual review lacks exact PASS/CORRECTED verdict field; do not infer or repair provider verdicts locally.'})
        context={'book':'Michel Strogoff','chapter':unit['heading'],'part':unit['part'],'chapter_name':next(r['text'] for r in unit['rows'] if r['role']=='SOURCE_CHAPTER_NAME'),'unit':number,'complete_chapter_french':[r['text'] for r in unit['rows'] if r['tag']!='illustration_caption'],'previous_french':[r['text'] for r in allrows[max(0,allrows.index(rows[0])-3):allrows.index(rows[0])]],'previous_reviewed_russian':[r['target'] for r in final[-3:]]}
        done=folder/f'batch{index:03}.reviewed.json'
        if done.exists():
            item=json.loads(done.read_text());validate(item['rows'],rows)
            assert [r['source_id'] for r in item['review']]==[r['id'] for r in rows]
        else:
            # Reuse an old review request's complete draft if a former response was malformed.
            legacy=folder/f'batch{index:03}.request.json';original=None;meta=None
            if legacy.exists():
                try:
                    body=json.loads(legacy.read_text());payload=json.loads(body['messages'][0]['content'])
                    if payload.get('draft_rows'):
                        original=validate(payload['draft_rows'],rows);meta={'state':'LEGACY_DRAFT_RECOVERED_FROM_REVIEW_REQUEST','original_http_response':'OVERWRITTEN_BY_V1_SUFFIX_BUG','evidence_file':str(legacy.relative_to(ROOT))}
                except (ValueError,KeyError,TypeError,AssertionError):pass
            if original is None:
                try:
                    draft,meta=deepl_translate(rows,folder/f'batch{index:03}.translation',context)
                except RuntimeError as service_error:
                    switch={'state':'AUTHORIZED_DEEPL_TO_CLAUDE_FALLBACK','reason':str(service_error)}
                    save(folder/f'batch{index:03}.provider-switch.json',switch)
                    draft,meta=checked_call(SYSTEM,{'task':'Translate EVERY editable English source row directly into complete fluent Russian. Return JSON only with rows:[{source_id,target}], in supplied order; preserve all source tokens and structure.','context':context,'source_rows':rows},folder/f'batch{index:03}.translation.claude',rows)
                    meta.update(provider='Claude',switch=switch)
                original=validate(draft['rows'],rows)
            request={'task':'Read EVERY English and Russian row and correct fidelity, omissions, agency, negation, idiom, register, names, italics and verse. Return JSON only: {"rows":[{"source_id":"exact source id","target":"complete final Russian"}],"review":[{"source_id":"exact source id","verdict":"PASS or CORRECTED","reason":"specific finding or source-based assessment"}]}. Every row in both arrays, in source order. Do not return source hashes; host binds immutable hashes.','context':context,'source_rows':rows,'draft_rows':original}
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
    from bb_ru59_verify import qualify_unit
    try:
        accepted,qualification=qualify_unit(ROOT,SOURCE,number,output_name="ru59-original")
        save(OUT/'qualified'/f'unit-{number:03}.json',accepted)
        save(OUT/'qualified'/f'unit-{number:03}.evidence.json',qualification)
        (OUT/'qualified'/f'unit-{number:03}.blocked.json').unlink(missing_ok=True)
    except Exception as error:
        (OUT/'qualified'/f'unit-{number:03}.json').unlink(missing_ok=True)
        (OUT/'qualified'/f'unit-{number:03}.evidence.json').unlink(missing_ok=True)
        save(OUT/'qualified'/f'unit-{number:03}.blocked.json',{'unit':number,'state':'REVIEW_REQUIRED','error':type(error).__name__+': '+str(error),'book_release':False})
        print('INDEPENDENT_RECONCILIATION_BLOCKED',number,str(error),flush=True)
        raise RuntimeError('INDEPENDENT_UNIT_RECONCILIATION_REQUIRED '+str(error)) from None
    commit();print('UNIT_CHECKPOINT',number,'ROWS',len(unit['rows']),flush=True)
    return number

if __name__=='__main__':
    assert len(SOURCE)==16 and sum(len(u['rows']) for u in SOURCE)==1463
    if not KEY:raise SystemExit('MISSING_ANTHROPIC_API_KEY')
    errors=[]
    save(OUT/'run-status.json',{'state':'RUNNING_CONTINUOUS_TRANSLATION_REVIEW','run_id':os.environ.get('GITHUB_RUN_ID'),'source_units':16,'source_rows':1463,'checkpoint_is_stop_condition':False,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    commit()
    selected=int(os.environ['BB_UNIT']) if os.environ.get('BB_UNIT') else None
    for unit in SOURCE:
        if selected is not None and unit['unit']!=selected:continue
        try:translate(unit)
        except Exception as error:
            errors.append({'unit':unit['unit'],'error':str(error)});print('UNIT_FAILED',unit['unit'],str(error),flush=True);break
    save(OUT/'run-status.json',{'state':'PARTIAL_FAILURE' if errors else 'SELECTED_UNIT_TRANSLATION_REVIEW_COMPLETE_PENDING_BOOK_QA' if selected is not None else 'API_TRANSLATION_REVIEW_COMPLETE_PENDING_BOOK_QA','errors':errors,'selected_unit':selected,'source_units':16,'source_rows':1463,'translation_provider':'DeepL quality_optimized','review_provider':MODEL,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    commit()
    if errors:raise SystemExit(1)
    if errors:raise SystemExit(1)
