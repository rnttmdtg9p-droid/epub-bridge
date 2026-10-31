import base64, concurrent.futures, datetime, gzip, hashlib, json, os, subprocess, threading, time, urllib.request, urllib.error
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/ru99-claude'; OUT.mkdir(parents=True,exist_ok=True)
PACKED=base64.b64decode(''.join(p.read_text() for p in sorted((ROOT/'inputs').glob('ru99-source.part*.b64'))))
assert hashlib.sha256(PACKED).hexdigest()=='3baff14b12606ca68d7686f03c4812264c721fccc4ed59a028af870a42491e25'
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
        subprocess.run(['git','add','results/ru99-claude'],cwd=ROOT,check=True)
        if subprocess.run(['git','diff','--cached','--quiet'],cwd=ROOT).returncode:
            subprocess.run(['git','commit','-m','Checkpoint rank99 initial Claude translation only'],cwd=ROOT,check=True)
        for push_attempt in range(5):
            pushed=subprocess.run(['git','push','origin','HEAD'],cwd=ROOT)
            if pushed.returncode==0:break
            if push_attempt==4:raise RuntimeError('GIT_PUSH_RETRIES_EXHAUSTED')
            print('TRANSIENT_CHECKPOINT_PUSH_RETRY',push_attempt+1,flush=True)
            time.sleep(min(5*(push_attempt+1),15))


SYSTEM="You are Boundary Bay's authorized INITIAL translator of Jonathan Swift's complete Gulliver's Travels from the supplied public-domain ENGLISH Project Gutenberg829 source into modern literary Russian. INITIAL TRANSLATION ONLY. Never perform literary review, QA, validation, approval or certification. Produce a NEW translation solely from supplied English. Never consult, copy, paraphrase or imitate any existing Russian translation, including Konchalovsky/Yakovenko, Frankovsky or Engelhardt. The frozen complete source has47transport units: source title/credit, two authentic opening texts (fictional publisher and Gulliver's letter), four authentic part headings,39chapters (8/8/11/12), and seven source notes. Include every supplied heading, chapter argument/synopsis, paragraph, dialogue, legal inventory/article, letter, date, signature, verse/foreign quotation and note. Do not summarize, abridge, combine, reorder, omit, sanitize or add material. Preserve speakers, agents, negation, proportions, geographical directions, exact dates, English measures/numbers, Swift's irony, ambiguity, political satire, invented customs, bodily details and disturbing judgments without endorsing or explaining them. Use clear natural literary Russian with Swift's measured eighteenth-century narrative voice, not a modern children's retelling. Preserve all exact [[I]]...[[/I]], [[SC]]...[[/SC]], [[B]]...[[/B]] markup markers, [[BR]] line breaks, all exact [[N1]] etc note markers in their source positions, and explicit source_lines counts. Do not invent or remove notes. Preserve genuine Latin/French quotations in the original language and their emphasis, including the two-line Virgil quotation with its one [[BR]] and two literal newline-separated source lines. Ordinary English words within emphasis/smallcaps must be translated faithfully. Source chapter arguments are authentic reading text, not headings to discard. Footnote labels and figure identity are host-bound metadata, not prose to invent. Do not invent chapter names: source numbered CHAPTER headings remain numbered chapter labels; four Part headings retain their authentic voyage names. Fixed collection short title: Путешествия Гулливера. Author: Джонатан Свифт. Terminology consistently: Lemuel Gulliver=Лемюэль Гулливер; Richard Sympson=Ричард Симпсон; Lilliput=Лилипутия; Blefuscu=Блефуску; Brobdingnag=Бробдингнег; Laputa=Лапута; Balnibarbi=Бальнибарби; Glubbdubdrib=Глаббдобдриб; Luggnagg=Лаггнегг; Houyhnhnms=гуигнгнмы; Yahoo/Yahoos=йеху; Glumdalclitch=Глюмдальклич; Flimnap=Флимнап; Bolgolam=Болголам; Reldresal=Рельдресель; Munodi=Мьюноди; Quinbus Flestrin=Куинбус Флестрин. Preserve invented words and names consistently, retaining foreign-language word specimens where the narrator gives them as such. Translate all English narrative, chapter arguments and seven note texts, including notes which quote older textual variants. Every returned row must retain supplied source_id in exact order. Return only the requested JSON rows; do not invent hashes or add commentary."

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
        assert row['target'].count('[[BR]]')==src.get('br_count',0),'BR_MARKER_COUNT'
        if src.get('source_lines'):assert len(row['target'].splitlines())==len(src['source_lines']),'VERSE_LINE_COUNT'
        assert row['target'].count('[[I]]')==src.get('emphasis_spans',0) and row['target'].count('[[/I]]')==src.get('emphasis_spans',0),'EMPHASIS_MARKER_COUNT'
        import re
        for mark in ['B','SC']:
            assert row['target'].count('[['+mark+']]')==src.get({'B':'bold_spans','SC':'smallcaps_spans'}[mark],0) and row['target'].count('[[/'+mark+']]')==src.get({'B':'bold_spans','SC':'smallcaps_spans'}[mark],0),'EMPHASIS_'+mark
        assert sorted(re.findall(r'\[\[N[^]]+\]\]',row['target']))==sorted(src.get('note_markers',[])),'NOTE_MARKER_COUNT'
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
    allrows=unit['rows']
    complete=folder/'chapter.translated.json'
    if complete.exists():
        prior=json.loads(complete.read_text())
        assert prior['unit']==number and prior['state']=='INITIAL_TRANSLATION_ONLY_PENDING_INDEPENDENT_REVIEW'
        assert prior['source_rows']==len(allrows) and prior['provider_receipts']
        validate(prior['rows'],allrows)
        assert all(r.get('actual_model')==MODEL and r.get('response_id') for r in prior['provider_receipts'])
        print('REUSE_ACCEPTED_UNIT',number,'ROWS',len(allrows),'NO_NEW_PROVIDER_CALL',flush=True)
        return
    batches=[];batch=[];chars=0
    for row in allrows:
        assert sha(row['text'])==row['sha256']
        if batch and (len(batch)>=32 or chars+len(row['text'])>12000):batches.append(batch);batch=[];chars=0
        batch.append(row);chars+=len(row['text'])
    if batch:batches.append(batch)
    final=[];receipts=[]
    for index,rows in enumerate(batches):
        done=folder/f'batch{index:03}.translated.json'
        if done.exists():
            item=json.loads(done.read_text());draft=validate(item['rows'],rows);meta=item['translation_api']
            assert item['state']=='INITIAL_TRANSLATION_ONLY_PENDING_INDEPENDENT_REVIEW'
        else:
            context={'title':"Gulliver's Travels",'unit':number,'heading':unit['heading'],'previous_rows':final[-2:],'source_language':'English','part':unit.get('part'),'book':unit.get('book'),'chapter':unit.get('chapter'),'role':unit.get('role')}
            request={'task':'Translate EVERY supplied row into Russian. Return JSON only: {"rows":[{"source_id":"exact supplied id","target":"complete Russian translation"}]}. Keep every row in order. Preserve all [[I]], [[B]], [[SC]] emphasis markers, [[BR]] markers and explicit source line counts. Do not return hashes; the host binds immutable hashes.','context':context,'source_rows':rows}
            result,meta=checked_call(SYSTEM,request,folder/f'batch{index:03}.translation',rows)
            draft=result['rows']
            item={'unit':number,'batch':index,'state':'INITIAL_TRANSLATION_ONLY_PENDING_INDEPENDENT_REVIEW','rows':draft,'translation_api':meta,'source_row_ids':[x['id'] for x in rows]}
            save(done,item)
        final.extend(draft);receipts.append(meta)
        save(folder/'chapter.progress.json',{'unit':number,'accepted_batches':index+1,'total_batches':len(batches),'accepted_rows':len(final),'state':'INITIAL_TRANSLATION_ONLY_REVIEW_PENDING','utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
        commit()
        print('RANK99 UNIT',number,'BATCH',index+1,'OF',len(batches),'TRANSLATED',len(draft),'ROWS; REVIEW_PENDING',flush=True)
    assert len(final)==len(allrows)
    save(folder/'chapter.translated.json',{'unit':number,'heading':next(r for r in final if r['source_id']==unit['heading_id']),'rows':final,'source_rows':len(unit['rows']),'state':'INITIAL_TRANSLATION_ONLY_PENDING_INDEPENDENT_REVIEW','translation_batches':len(batches),'provider_receipts':receipts,'master':'5.0.70'})
    save(OUT/'progress.json',{'rank':99,'completed_unit':number,'source_units':47,'source_rows':681,'state':'INITIAL_TRANSLATION_RUNNING_REVIEW_NOT_RUN','utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    commit()
    print('UNIT_CHECKPOINT',number,'ROWS',len(unit['rows']),flush=True)

if __name__=='__main__':
    assert len(SOURCE)==47 and sum(len(u['rows']) for u in SOURCE)==681
    if not KEY:raise SystemExit('MISSING_ANTHROPIC_API_KEY')
    errors=[]
    for unit in SOURCE:
        try:translate(unit)
        except Exception as error:
            errors.append({'unit':unit['unit'],'error':str(error)})
            print('UNIT_FAILED',unit['unit'],str(error),flush=True)
            break
    save(OUT/'run-status.json',{'state':'PARTIAL_FAILURE' if errors else 'INITIAL_TRANSLATION_COMPLETE_PENDING_INDEPENDENT_REVIEW_AND_BOOK_QA','errors':errors,'source_units':47,'source_rows':681,'claude_role':'INITIAL_TRANSLATION_ONLY','utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    commit()
    if errors:raise SystemExit(1)
