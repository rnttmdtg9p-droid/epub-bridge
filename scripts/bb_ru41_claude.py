import base64, concurrent.futures, datetime, gzip, hashlib, json, os, subprocess, threading, time, urllib.request, urllib.error
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/ru41-claude'; OUT.mkdir(parents=True,exist_ok=True)
PACKED=base64.b64decode((ROOT/'inputs/ru41-source.json.gz.b64').read_text())
assert hashlib.sha256(PACKED).hexdigest()=='c56b7e2fcf5a1172033f7523484b2341a43cbe9b725a272a6d03ac4c925e918d'
SOURCE=json.loads(gzip.decompress(PACKED))
KEY=os.environ.get('ANTHROPIC_API_KEY','').strip()
MODEL='claude-sonnet-5-5'
LOCK=threading.Lock()
def sha(s): return hashlib.sha256(s.encode()).hexdigest()
def save(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    t=p.with_suffix(p.suffix+'.tmp'); t.write_text(json.dumps(v,ensure_ascii=False,indent=2));t.replace(p)
def commit():
    with LOCK:
        subprocess.run(['git','add','results/ru41-claude'],cwd=ROOT,check=True)
        if subprocess.run(['git','diff','--cached','--quiet'],cwd=ROOT).returncode:
            subprocess.run(['git','commit','-m','Checkpoint rank 41 Claude translation and review'],cwd=ROOT,check=True)
            subprocess.run(['git','push','origin','HEAD'],cwd=ROOT,check=True)

GLOSSARY='Мазарини; Ришелье; д’Артаньян; Бофор; Пале-Рояль; Бернуин; Брусель; Рошфор; Тревиль; Гито; Коменж; Вилькье; Башомон; Бенсерад; Рамбуйе; дю Трамбле; Бассомпьер; Лильбон; д’Аркур; Фонтрай; де Рие; Шале; Планше; Атос; Портос; Арамис; Мёнг; Шатле; Венсен.'
SYSTEM='You are the authorized Boundary Bay literary translator for Alexandre Dumas and Auguste Maquet, Vingt ans après (1845), French to Russian. Create a NEW complete fluent literary Russian translation directly from the supplied French. Never consult or reproduce a published Russian translation. Preserve every fact, agency, negation, tense, humour, ambiguity, dialogue turn and paragraph boundary. Preserve historical anomalies faithfully. Use Russian dialogue punctuation and idiomatic prose. Retain authentic source verse line counts exactly, and italic spans semantically. Do not merge, omit, summarize, modernize facts, or add explanations. Tag h5 denotes source letter/signature headings, not a new chapter. Names already established: '+GLOSSARY+' Return JSON only.'

def call(system,payload,path):
    body={'model':MODEL,'max_tokens':16000,'system':system,'messages':[{'role':'user','content':json.dumps(payload,ensure_ascii=False)}]}
    save(Path(str(path)+'.request.json'),body)
    headers={'x-api-key':KEY,'anthropic-version':'2023-06-01','content-type':'application/json'}
    workspace=os.environ.get('ANTHROPIC_WORKSPACE_ID','').strip()
    if workspace:headers['anthropic-workspace-id']=workspace
    for attempt in range(4):
        try:
            req=urllib.request.Request('https://api.anthropic.com/v1/messages',data=json.dumps(body,ensure_ascii=False).encode(),headers=headers)
            with urllib.request.urlopen(req,timeout=240) as r: raw=json.load(r)
            save(Path(str(path)+'.response.json'),raw)
            if raw.get('stop_reason')!='end_turn':raise RuntimeError('INCOMPLETE_RESPONSE_'+str(raw.get('stop_reason')))
            text='\n'.join(x['text'] for x in raw['content'] if x.get('type')=='text').strip()
            if text.startswith('```'):text=text.split('\n',1)[1].rsplit('```',1)[0]
            return json.JSONDecoder().raw_decode(text[text.index('{'):])[0],{'requested_model':MODEL,'actual_model':raw.get('model'),'response_id':raw.get('id'),'usage':raw.get('usage')}
        except urllib.error.HTTPError as e:
            if e.code in (429,500,502,503,529) and attempt<3:time.sleep(5*(attempt+1));continue
            raise RuntimeError('CLAUDE_HTTP_'+str(e.code)) from None
    raise RuntimeError('RETRY_EXHAUSTED')

def validate(rows,original):
    assert isinstance(rows,list) and len(rows)==len(original),'ROW_COUNT'
    for row,src in zip(rows,original):
        row['source_id']=row.get('source_id',row.get('id'))
        assert row['source_id']==src['id'],'SOURCE_ID_ALIGNMENT'
        assert row.get('source_sha256',src['sha256'])==src['sha256'],'SOURCE_HASH_ALIGNMENT'
        row['source_sha256']=src['sha256']
        assert isinstance(row['target'],str) and row['target'].strip(),'EMPTY_TARGET'
        if src.get('source_lines'):assert len(row['target'].splitlines())==len(src['source_lines']),'VERSE_LINE_COUNT'
        row.update(target_id='ru-'+src['id'],tag=src['tag'],target_sha256=sha(row['target']))
    return rows

def checked_call(system,payload,path,source,review=False):
    last=None
    for attempt in range(3):
        request=dict(payload)
        if last:request['format_retry_instruction']='Previous response failed strict validation: '+last+'. Return only the requested JSON, with every source_id in supplied order. Do not invent or copy hashes; the host binds exact immutable source hashes.'
        try:
            result,meta=call(system,request,Path(str(path)+f'.attempt{attempt:02}'))
            result['rows']=validate(result['rows'],source)
            if review:
                for r in result.get('review',[]):r['source_id']=r.get('source_id',r.get('id'))
                assert [r['source_id'] for r in result['review']]==[r['id'] for r in source],'REVIEW_COVERAGE'
                assert all(r.get('reason') for r in result['review']),'REVIEW_REASON'
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
        request={'task':'Compare EVERY final Russian row against its French source. Verify all prior corrections in the supplied ledger. Check meaning, agency, negation, omissions, idiom, register, italics and original verse line count. Return JSON only: {"rows":[{"source_id":"exact source id","target":"complete corrected Russian"}],"review":[{"source_id":"exact source id","verdict":"PASS or CORRECTED","reason":"specific source-based assessment"}]}. Return every row in both arrays, in source order. Do not return source hashes; host binds immutable hashes. If already faithful and idiomatic, preserve the exact target bytes.','context':context,'source_rows':rows,'final_rows':current,'prior_corrections':item.get('corrections',[])+[c for previous in comparison for c in previous['corrections']]}
        result,meta=checked_call(SYSTEM+' You are the final source-aligned AI comparison reviewer, checking corrected text against French independently of prior verdicts.',request,folder/f'batch{index:03}.comparison{turn:02}',rows,True)
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
        if batch and (len(batch)>=20 or chars+len(row['text'])>11000):batches.append(batch);batch=[];chars=0
        batch.append(row);chars+=len(row['text'])
    if batch:batches.append(batch)
    for index,rows in enumerate(batches):
        verified=folder/f'batch{index:03}.comparison_verified.json'
        if verified.exists():
            item=json.loads(verified.read_text());final.extend(validate(item['rows'],rows));reviews.append(item);continue
        context={'book':'Vingt ans après','chapter':unit['heading'],'unit':number,'previous_french':[r['text'] for r in allrows[max(0,allrows.index(rows[0])-3):allrows.index(rows[0])]],'previous_reviewed_russian':[r['target'] for r in final[-3:]]}
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
                request={'task':'Translate every row. Return JSON only: {"rows":[{"source_id":"exact source id","target":"complete Russian"}]}. Include every row in source order. Source_html conveys emphasis: retain semantically using <i> tags, no other HTML. source_lines requires the identical number of target lines. Do not return source hashes; host binds immutable hashes.','context':context,'source_rows':rows}
                draft,meta=checked_call(SYSTEM,request,folder/f'batch{index:03}.translation',rows)
                original=draft['rows']
            request={'task':'Read EVERY French and Russian row and correct fidelity, omissions, agency, negation, idiom, register, names, italics and verse. Return JSON only: {"rows":[{"source_id":"exact source id","target":"complete final Russian"}],"review":[{"source_id":"exact source id","verdict":"PASS or CORRECTED","reason":"specific finding or source-based assessment"}]}. Every row in both arrays, in source order. Do not return source hashes; host binds immutable hashes.','context':context,'source_rows':rows,'draft_rows':original}
            result,review_meta=checked_call(SYSTEM+' You are an independent source-aligned AI literary reviewer. Apply all corrections directly.',request,folder/f'batch{index:03}.review',rows,True)
            corrected=result['rows'];changes=[{'source_id':a['source_id'],'before':a['target'],'after':b['target']} for a,b in zip(original,corrected) if a['target']!=b['target']]
            item={'unit':number,'batch':index,'state':'SOURCE_ALIGNED_AI_REVIEWED_PENDING_COMPARISON','rows':corrected,'review':result['review'],'corrections':changes,'translation_api':meta,'review_api':review_meta};save(done,item)
        item=compare_final(number,index,rows,item,folder,context)
        final.extend(item['rows']);reviews.append(item)
        print('UNIT',number,'BATCH',index+1,'OF',len(batches),'COMPARISON_VERIFIED',flush=True)
    assert len(final)==len(allrows)
    save(folder/'chapter.reviewed.json',{'unit':number,'heading':final[0],'rows':final[1:],'source_rows':len(unit['rows']),'state':'SOURCE_ALIGNED_AI_REVIEW_AND_CORRECTION_COMPARISON_COMPLETE_PENDING_BOOK_QA','review_batches':len(reviews),'master':'5.0.70'})
    commit();print('UNIT_CHECKPOINT',number,'ROWS',len(unit['rows']),flush=True)
    return number

if __name__=='__main__':
    assert len(SOURCE)==99 and sum(len(u['rows']) for u in SOURCE)==12324
    if not KEY:raise SystemExit('MISSING_ANTHROPIC_API_KEY')
    errors=[]
    # Units I-III are already accepted and immutable; IV-VI browser drafts were never accepted.
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        jobs={pool.submit(translate,u):u['unit'] for u in SOURCE if u['unit']>=4}
        for job in concurrent.futures.as_completed(jobs):
            try:job.result()
            except Exception as error:
                errors.append({'unit':jobs[job],'error':str(error)});print('UNIT_FAILED',jobs[job],str(error),flush=True)
                # Authentication/model access failure must not fan out repeated requests.
                if any(s in str(error) for s in ['HTTP_401','HTTP_403','HTTP_404']):
                    for pending in jobs:pending.cancel()
                    break
    save(OUT/'run-status.json',{'state':'PARTIAL_FAILURE' if errors else 'API_TRANSLATION_REVIEW_COMPLETE_PENDING_BOOK_QA','errors':errors,'accepted_seed_units':[1,2,3],'source_units':99,'source_rows':12324,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    commit()
    if errors:raise SystemExit(1)

