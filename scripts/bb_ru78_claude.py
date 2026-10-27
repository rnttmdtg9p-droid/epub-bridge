import base64, concurrent.futures, datetime, gzip, hashlib, json, os, subprocess, threading, time, urllib.request, urllib.error
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/ru78-claude'; OUT.mkdir(parents=True,exist_ok=True)
PACKED=base64.b64decode((ROOT/'inputs/ru78-source.json.gz.b64').read_text())
assert hashlib.sha256(PACKED).hexdigest()=='f763c3965dd0249e467aa611dd6593b893c0666e95322dbc426fc7166e7d0783'
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
        subprocess.run(['git','add','results/ru78-claude'],cwd=ROOT,check=True)
        if subprocess.run(['git','diff','--cached','--quiet'],cwd=ROOT).returncode:
            subprocess.run(['git','commit','-m','Checkpoint rank78 initial Claude translation only'],cwd=ROOT,check=True)
            subprocess.run(['git','push','origin','HEAD'],cwd=ROOT,check=True)


SYSTEM='You are the authorized Boundary Bay INITIAL literary translator for Jack London, Hearts of Three, original English to Russian. Source is the complete selected public-domain Mills & Boon witness preserved in Project Gutenberg54068. Create a NEW complete fluent literary Russian translation only from supplied English. Never consult or reproduce any published Russian translation, including D’Aktil or Kudryavtseva. Preserve all facts, agency, negation, ambiguity, humour, narrative voice, dialogue turns, paragraph boundaries, headings, signatures, datelines, genuine paratext and every [[I]]...[[/I]] emphasis marker. Do not modernize away historical social attitudes, slurs, deliberate character speech or Spanish/Latin words where their foreignness is meaningful. Translate the foreword and the entire George Sterling pirate song. For verse_group rows preserve the exact explicit source line count and stanza grouping, and seek natural Russian rhythm without changing facts or images. Display_group/source_display rows include letter salutations, signatures and dates; preserve their roles. Never summarize, omit, merge rows or add explanatory notes. CHAPTER headings have a number only; do not invent chapter names. Return JSON only. This is INITIAL TRANSLATION ONLY, never literary review or release qualification. Fixed title: Сердца трёх. Names glossary: Francis Morgan=Фрэнсис Морган; Henry Morgan=Генри Морган; Sir Henry Morgan=сэр Генри Морган; Leoncia=Леонсия; Thomas Regan=Томас Риган; Alvarez Torres=Альварес Торрес; Bascom=Бэском; Solano=Солано; Enrico=Энрико; Parker=Паркер; Charles Goddard=Чарлз Годдард; George Sterling=Джордж Стерлинг; Bocas del Toro=Бокас-дель-Торо; Chiriqui Lagoon=лагуна Чирики; Mosquito Coast=Москитовый берег; Tampico Petroleum=Тампико петролеум. Keep names, currency, geography, treasures and relationships consistent across chapters. Internal transport metadata must never become reader content.'
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
        assert row['target'].count('[[I]]')==src.get('emphasis_spans',0) and row['target'].count('[[/I]]')==src.get('emphasis_spans',0),'EMPHASIS_MARKER_COUNT'
        row.update(target_id='ru-'+src['id'],tag=src['tag'],target_sha256=sha(row['target']))
    return rows

def checked_call(system,payload,path,source):
    last=None
    for attempt in range(3):
        request=dict(payload)
        if last:request['format_retry_instruction']='Previous response failed strict validation: '+last+'. Return only the requested JSON, with every source_id in supplied order. Do not invent or copy hashes; the host binds exact immutable source hashes.'
        try:
            result,meta=call(system,request,Path(str(path)+f'.attempt{attempt:02}'))
            result['rows']=validate(result['rows'],source)
            return result,meta
        except (AssertionError,KeyError,ValueError,TypeError) as error:
            last=type(error).__name__+': '+str(error)
            save(Path(str(path)+f'.attempt{attempt:02}.validation-error.json'),{'error':last})
    raise RuntimeError('STRICT_FORMAT_RETRIES_EXHAUSTED '+str(last))


def translate(unit):
    number=unit['unit'];folder=OUT/f'{number:03}';folder.mkdir(exist_ok=True)
    heading={'id':unit['heading_id'],'tag':'heading','text':unit['heading'],'sha256':sha(unit['heading']),'emphasis_spans':0}
    allrows=[heading]+unit['rows'];batches=[];batch=[];chars=0
    for row in allrows:
        assert sha(row['text'])==row['sha256']
        if batch and (len(batch)>=16 or chars+len(row['text'])>8000):batches.append(batch);batch=[];chars=0
        batch.append(row);chars+=len(row['text'])
    if batch:batches.append(batch)
    final=[];receipts=[]
    for index,rows in enumerate(batches):
        done=folder/f'batch{index:03}.translated.json'
        if done.exists():
            item=json.loads(done.read_text());draft=validate(item['rows'],rows);meta=item['translation_api']
            assert item['state']=='INITIAL_TRANSLATION_ONLY_PENDING_INDEPENDENT_REVIEW'
        else:
            context={'title':"Hearts of Three",'unit':number,'heading':unit['heading'],'previous_rows':final[-2:]}
            request={'task':'Translate EVERY supplied row into Russian. Return JSON only: {"rows":[{"source_id":"exact supplied id","target":"complete Russian translation"}]}. Keep every row in order. Preserve [[I]] markers and explicit display line counts. Do not return hashes; the host binds immutable hashes.','context':context,'source_rows':rows}
            result,meta=checked_call(SYSTEM,request,folder/f'batch{index:03}.translation',rows)
            draft=result['rows']
            item={'unit':number,'batch':index,'state':'INITIAL_TRANSLATION_ONLY_PENDING_INDEPENDENT_REVIEW','rows':draft,'translation_api':meta,'source_row_ids':[x['id'] for x in rows]}
            save(done,item)
        final.extend(draft);receipts.append(meta)
        save(folder/'chapter.progress.json',{'unit':number,'accepted_batches':index+1,'total_batches':len(batches),'accepted_rows':len(final),'state':'INITIAL_TRANSLATION_ONLY_REVIEW_PENDING','utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
        commit()
        print('RANK78 UNIT',number,'BATCH',index+1,'OF',len(batches),'TRANSLATED',len(draft),'ROWS; REVIEW_PENDING',flush=True)
    assert len(final)==len(allrows)
    save(folder/'chapter.translated.json',{'unit':number,'heading':final[0],'rows':final[1:],'source_rows':len(unit['rows']),'state':'INITIAL_TRANSLATION_ONLY_PENDING_INDEPENDENT_REVIEW','translation_batches':len(batches),'provider_receipts':receipts,'master':'5.0.70'})
    save(OUT/'progress.json',{'rank':78,'completed_unit':number,'source_units':31,'source_rows':2742,'state':'INITIAL_TRANSLATION_RUNNING_REVIEW_NOT_RUN','utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    commit()
    print('UNIT_CHECKPOINT',number,'ROWS',len(unit['rows']),flush=True)

if __name__=='__main__':
    assert len(SOURCE)==31 and sum(len(u['rows']) for u in SOURCE)==2742
    if not KEY:raise SystemExit('MISSING_ANTHROPIC_API_KEY')
    errors=[]
    for unit in SOURCE:
        try:translate(unit)
        except Exception as error:
            errors.append({'unit':unit['unit'],'error':str(error)})
            print('UNIT_FAILED',unit['unit'],str(error),flush=True)
            break
    save(OUT/'run-status.json',{'state':'PARTIAL_FAILURE' if errors else 'INITIAL_TRANSLATION_COMPLETE_PENDING_INDEPENDENT_REVIEW_AND_BOOK_QA','errors':errors,'source_units':31,'source_rows':2742,'claude_role':'INITIAL_TRANSLATION_ONLY','utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    commit()
    if errors:raise SystemExit(1)

