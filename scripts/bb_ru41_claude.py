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
    save(path.with_suffix('.request.json'),body)
    headers={'x-api-key':KEY,'anthropic-version':'2023-06-01','content-type':'application/json'}
    workspace=os.environ.get('ANTHROPIC_WORKSPACE_ID','').strip()
    if workspace:headers['anthropic-workspace-id']=workspace
    for attempt in range(4):
        try:
            req=urllib.request.Request('https://api.anthropic.com/v1/messages',data=json.dumps(body,ensure_ascii=False).encode(),headers=headers)
            with urllib.request.urlopen(req,timeout=240) as r: raw=json.load(r)
            save(path.with_suffix('.response.json'),raw)
            if raw.get('stop_reason')!='end_turn':raise RuntimeError('INCOMPLETE_RESPONSE_'+str(raw.get('stop_reason')))
            text='\n'.join(x['text'] for x in raw['content'] if x.get('type')=='text').strip()
            if text.startswith('```'):text=text.split('\n',1)[1].rsplit('```',1)[0]
            return json.loads(text),{'requested_model':MODEL,'actual_model':raw.get('model'),'response_id':raw.get('id'),'usage':raw.get('usage')}
        except urllib.error.HTTPError as e:
            if e.code in (429,500,502,503,529) and attempt<3:time.sleep(5*(attempt+1));continue
            raise RuntimeError('CLAUDE_HTTP_'+str(e.code)) from None
    raise RuntimeError('RETRY_EXHAUSTED')

def validate(rows,original):
    assert isinstance(rows,list) and len(rows)==len(original),'ROW_COUNT'
    for row,src in zip(rows,original):
        assert row['source_id']==src['id'] and row['source_sha256']==src['sha256'],'SOURCE_ALIGNMENT'
        assert isinstance(row['target'],str) and row['target'].strip(),'EMPTY_TARGET'
        if src.get('source_lines'):assert len(row['target'].splitlines())==len(src['source_lines']),'VERSE_LINE_COUNT'
        row.update(target_id='ru-'+src['id'],tag=src['tag'],target_sha256=sha(row['target']))
    return rows

def translate(unit):
    number=unit['unit']; folder=OUT/f'{number:03}';folder.mkdir(exist_ok=True)
    heading={'id':unit['heading_id'],'tag':'heading','text':unit['heading'],'sha256':sha(unit['heading'])}
    allrows=[heading]+unit['rows'];final=[];reviews=[]
    batches=[];batch=[];chars=0
    for row in allrows:
        assert sha(row['text'])==row['sha256']
        if batch and (len(batch)>=20 or chars+len(row['text'])>11000):batches.append(batch);batch=[];chars=0
        batch.append(row);chars+=len(row['text'])
    if batch:batches.append(batch)
    for index,rows in enumerate(batches):
        done=folder/f'batch{index:03}.reviewed.json'
        if done.exists():
            item=json.loads(done.read_text());final.extend(validate(item['rows'],rows));reviews.append(item);continue
        context={'book':'Vingt ans après','chapter':unit['heading'],'unit':number,'previous_french':[r['text'] for r in allrows[max(0,allrows.index(rows[0])-3):allrows.index(rows[0])]],'previous_reviewed_russian':[r['target'] for r in final[-3:]]}
        request={'task':'Translate every row. Return {"rows":[{"source_id":"...","source_sha256":"...","target":"..."}]} in exact supplied order. Source_html conveys emphasis; retain italics with <i> tags, no other HTML. source_lines requires identical target line count.','context':context,'source_rows':rows}
        draft,meta=call(SYSTEM,request,folder/f'batch{index:03}.translation')
        original=validate(draft['rows'],rows)
        review_system=SYSTEM+' You are now an independent source-aligned AI literary reviewer of a supplied draft. Read EVERY French and Russian row. Correct meaning, omissions, additions, agency, negation, idiom, register, names, punctuation, italics and verse. Return final corrected targets for every row and a complete per-row assessment. No human approval step.'
        request={'task':'Return {"rows":[{"source_id":"...","source_sha256":"...","target":"..."}],"review":[{"source_id":"...","verdict":"PASS or CORRECTED","reason":"specific finding or faithful/idiomatic"}]}. Every source row must appear in both arrays, in order. Correct errors directly.','context':context,'source_rows':rows,'draft_rows':original}
        reviewed,review_meta=call(review_system,request,folder/f'batch{index:03}.review')
        corrected=validate(reviewed['rows'],rows)
        assert [r['source_id'] for r in reviewed['review']]==[r['id'] for r in rows],'REVIEW_COVERAGE'
        changes=[{'source_id':a['source_id'],'before':a['target'],'after':b['target']} for a,b in zip(original,corrected) if a['target']!=b['target']]
        item={'unit':number,'batch':index,'state':'SOURCE_ALIGNED_AI_REVIEWED_PENDING_BOOK_RECONCILIATION','rows':corrected,'review':reviewed['review'],'corrections':changes,'translation_api':meta,'review_api':review_meta}
        save(done,item);final.extend(corrected);reviews.append(item)
        print('UNIT',number,'BATCH',index+1,'OF',len(batches),'REVIEWED',flush=True)
    assert len(final)==len(allrows)
    save(folder/'chapter.reviewed.json',{'unit':number,'heading':final[0],'rows':final[1:],'source_rows':len(unit['rows']),'state':'SOURCE_ALIGNED_AI_REVIEWED_PENDING_BOOK_RECONCILIATION','review_batches':len(reviews),'master':'5.0.70'})
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
