import base64, concurrent.futures, datetime, gzip, hashlib, json, os, subprocess, threading, time, urllib.request, urllib.error
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/ru86-claude'; OUT.mkdir(parents=True,exist_ok=True)
PACKED=base64.b64decode(''.join(p.read_text() for p in sorted((ROOT/'inputs').glob('ru86-source.part*.b64'))))
assert hashlib.sha256(PACKED).hexdigest()=='873d6b368f226a138a28ee93339a6a0b56854afe52eb6724a62a15357ba0fe8b'
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
        subprocess.run(['git','add','results/ru86-claude'],cwd=ROOT,check=True)
        if subprocess.run(['git','diff','--cached','--quiet'],cwd=ROOT).returncode:
            subprocess.run(['git','commit','-m','Checkpoint rank86 initial Claude translation only'],cwd=ROOT,check=True)
            subprocess.run(['git','push','origin','HEAD'],cwd=ROOT,check=True)


SYSTEM="You are Boundary Bay's authorized INITIAL translator of Victor Hugo, Notre-Dame de Paris—1482, from the supplied complete public-domain FRENCH source into modern literary Russian. The immutable PG19657 source includes the1831preface,1832definitive-edition note,11books59chapters,two manuscript-note sections and131 authentic footnotes in135 note paragraphs. There are64 source units and4018 rows, including11 parent book headings and authentic source title matter. Produce a NEW complete translation solely from the supplied French; never consult, copy, paraphrase or imitate existing Russian translations, including Pomerantseva, Suvorin, Sytin or modern translations. INITIAL TRANSLATION ONLY; never literary review, QA, validation or release certification. Translate every supplied row completely and preserve exact order and identifiers, all facts, speakers, agents, negation, ambiguity, irony, humour, dramatic violence, religious and social attitudes, the narrative voice, purposeful repetition and architectural or historical digressions. Do not abridge, summarize, merge, reorder or omit paragraphs. Translate genuine French headings, historical prose, letters, verse, signatures, datelines, source manuscript notes and footnotes. Parent LIVRE headings remain book labels, not invented chapter titles. Preserve authenticated Roman chapter numerals exactly, without adding the word CHAPTER or numbering unnumbered prefaces. Preserve every exact [[I]]...[[/I]], [[B]]...[[/B]], [[SC]]...[[/SC]] emphasis marker and every opaque [[Nnumber]] note-reference marker exactly once in its source row; four note references occur in genuine chapter names and must not be dropped. For rows with source_lines preserve the exact line count and blank stanza boundaries. Translate French verse with its meaning, imagery and rhythm; keep genuine Latin, Spanish, Greek and other foreign-language quotations/expressions foreign when the French original presents them as foreign, preserving their quoted wording rather than modernizing them into Russian. Translate the supplied French explanations of those expressions in the authentic source footnotes. Do not invent translator notes or explanations. Preserve all numbers, dates, currencies, measurements and historical calendar references without conversion. Use natural modern Russian orthography and em-dash dialogue with coherent speaker changes, consistent names and historically appropriate registers. Fixed Russian title Собор Парижской Богоматери; retain1482 in the authentic source work heading and1831 in its source date. Character names glossary: Victor Hugo=Виктор Гюго; Quasimodo=Квазимодо; Esmeralda/La Esmeralda=Эсмеральда; Claude Frollo=Клод Фролло; Jehan Frollo=Жеан Фролло; Pierre Gringoire=Пьер Гренгуар; Phœbus/Phoebus de Châteaupers=Феб де Шатопер; Fleur-de-Lys=Флёр-де-Лис; Djali=Джали (female goat); Clopin Trouillefou=Клопен Труйльфу; Paquette=Пакетта; Gudule=Гудула; la Sachette=Затворница; Louis XI=Людовик XI; Tristan l'Hermite=Тристан Лермит; Olivier le Daim=Оливье ле Дэн; Jacques Coppenole=Жак Коппеноль; Robert d'Estouteville=Робер д'Эстутвиль; Mahiette=Маэтта; Oudarde=Ударда; Gervaise=Жервеза. Places/terms: Notre-Dame=собор Парижской Богоматери (in the fixed title use Собор Парижской Богоматери); la Grève=Гревская площадь; Cour des Miracles=Двор чудес; Grand'Salle=Большой зал; Palais de Justice=Дворец правосудия; Montfaucon=Монфокон; Tour-Roland=Роландова башня; la Bastille=Бастилия; prévôt=прево; bailli=бальи; archidiacre=архидиакон. Preserve meaningful distinctions of professional and civic titles. The source unit and role metadata are transport context, not extra reader prose. Return only the requested JSON."
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
    allrows=unit['rows'];batches=[];batch=[];chars=0
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
            context={'title':"Notre-Dame de Paris",'unit':number,'heading':unit['heading'],'previous_rows':final[-2:],'source_language':'French','part':unit.get('part'),'book':unit.get('book'),'chapter':unit.get('chapter'),'role':unit.get('role')}
            request={'task':'Translate EVERY supplied row into Russian. Return JSON only: {"rows":[{"source_id":"exact supplied id","target":"complete Russian translation"}]}. Keep every row in order. Preserve all [[I]], [[B]], [[SC]] emphasis markers, [[Nnumber]] note markers and explicit source line counts. Do not return hashes; the host binds immutable hashes.','context':context,'source_rows':rows}
            result,meta=checked_call(SYSTEM,request,folder/f'batch{index:03}.translation',rows)
            draft=result['rows']
            item={'unit':number,'batch':index,'state':'INITIAL_TRANSLATION_ONLY_PENDING_INDEPENDENT_REVIEW','rows':draft,'translation_api':meta,'source_row_ids':[x['id'] for x in rows]}
            save(done,item)
        final.extend(draft);receipts.append(meta)
        save(folder/'chapter.progress.json',{'unit':number,'accepted_batches':index+1,'total_batches':len(batches),'accepted_rows':len(final),'state':'INITIAL_TRANSLATION_ONLY_REVIEW_PENDING','utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
        commit()
        print('RANK86 UNIT',number,'BATCH',index+1,'OF',len(batches),'TRANSLATED',len(draft),'ROWS; REVIEW_PENDING',flush=True)
    assert len(final)==len(allrows)
    save(folder/'chapter.translated.json',{'unit':number,'heading':next(r for r in final if r['source_id']==unit['heading_id']),'rows':final,'source_rows':len(unit['rows']),'state':'INITIAL_TRANSLATION_ONLY_PENDING_INDEPENDENT_REVIEW','translation_batches':len(batches),'provider_receipts':receipts,'master':'5.0.70'})
    save(OUT/'progress.json',{'rank':86,'completed_unit':number,'source_units':64,'source_rows':4018,'state':'INITIAL_TRANSLATION_RUNNING_REVIEW_NOT_RUN','utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    commit()
    print('UNIT_CHECKPOINT',number,'ROWS',len(unit['rows']),flush=True)

if __name__=='__main__':
    assert len(SOURCE)==64 and sum(len(u['rows']) for u in SOURCE)==4018
    if not KEY:raise SystemExit('MISSING_ANTHROPIC_API_KEY')
    errors=[]
    for unit in SOURCE:
        try:translate(unit)
        except Exception as error:
            errors.append({'unit':unit['unit'],'error':str(error)})
            print('UNIT_FAILED',unit['unit'],str(error),flush=True)
            break
    save(OUT/'run-status.json',{'state':'PARTIAL_FAILURE' if errors else 'INITIAL_TRANSLATION_COMPLETE_PENDING_INDEPENDENT_REVIEW_AND_BOOK_QA','errors':errors,'source_units':64,'source_rows':4018,'claude_role':'INITIAL_TRANSLATION_ONLY','utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    commit()
    if errors:raise SystemExit(1)
