from pathlib import Path
from lxml import html,etree
from copy import deepcopy
import urllib.request,urllib.error,json,hashlib,os,time,concurrent.futures,threading
OUT=Path('results/ru22-deepl');OUT.mkdir(parents=True,exist_ok=True)
HASHES=['48e1da16e9740ab420e40b8ecc70b45e7ec698e04171c7466c0eeddeff6c6e0a','700fcb6bcd69debf776685ce286462b3800e002aa789ee71dd533bbd5b3ce59e','d8ef06e5cf1b0e4cfe81e8c366eaeec68a7c8836c65c493e7fa2a66e3d75ac25','c6b440afe977961a906f8d619bea626d2eddc5cc2ed83e93aca731ae6414ca13']
def sha(s):return hashlib.sha256(s.encode()).hexdigest()
def atomic(path,obj):
 tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2));tmp.replace(path)
books=[]
for n,expected in zip(range(17989,17993),HASHES):
 url=f'https://www.gutenberg.org/files/{n}/{n}-h/{n}-h.htm';raw=urllib.request.urlopen(url,timeout=90).read();assert hashlib.sha256(raw).hexdigest()==expected,'SOURCE_CHANGED'
 root=html.fromstring(raw)
 for chapter in root.xpath('//div[@class="chapter"][h2]'):
  seq=len(books)+1;heads=chapter.xpath('./h2');number=heads[0].text_content().strip();paras=[]
  for node in chapter:
   if node.tag in ['h2','hr']:continue
   assert node.tag in ['h3','p','div'],node.tag
   if not node.text_content().strip():continue
   node=deepcopy(node)
   for x in node.iter():
    if not isinstance(x.tag,str):continue
    for a in list(x.attrib):
     if a not in ['class','id','href']:del x.attrib[a]
   markup=etree.tostring(node,encoding='unicode',method='html',with_tail=False)
   paras.append({'id':f'{seq:03}-{len(paras):04}','role':'heading' if node.tag=='h3' else 'body','source_html':markup,'source':node.text_content(),'source_sha256':sha(markup)})
  books.append({'chapter':seq,'number':number,'source_url':url,'source_document_sha256':expected,'paragraphs':paras,'engine':'DeepL','requested_model':'quality_optimized','source_language':'FR','target_language':'RU'})
assert len(books)==117,len(books)
for d in books:
 p=OUT/f"{d['chapter']:03}.json"
 if p.exists():
  old=json.loads(p.read_text());assert len(old['paragraphs'])==len(d['paragraphs'])
  for a,b in zip(old['paragraphs'],d['paragraphs']):assert a['id']==b['id'] and a['source_sha256']==b['source_sha256']
  d['paragraphs']=old['paragraphs']
manifest={'rank':22,'master':'5.0.51','authorization':'Standing automatic full-work DeepL fallback after three completed existing-source searches','chapters':117,'paragraphs':sum(len(d['paragraphs']) for d in books),'state':'IN_PROGRESS','units':{}}
lock=threading.Lock()
def save():atomic(OUT/'manifest.json',manifest)
key=os.environ.get('DEEPL_AUTH_KEY','').strip()
if not key:manifest.update(state='BLOCKED',reason='MISSING_DEEPL_AUTH_KEY');save();raise SystemExit(2)
host='https://api-free.deepl.com' if key.endswith(':fx') else 'https://api.deepl.com'
def call(path,payload=None):
 for attempt in range(3):
  req=urllib.request.Request(host+path,data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None,headers={'Authorization':'DeepL-Auth-Key '+key,'Content-Type':'application/json'})
  try:
   with urllib.request.urlopen(req,timeout=120) as r:return json.load(r)
  except urllib.error.HTTPError as e:
   if (e.code==429 or e.code>=500) and attempt<2:time.sleep(3*(attempt+1));continue
   raise RuntimeError('DEEPL_HTTP_'+str(e.code)) from None
  except Exception as e:raise RuntimeError('DEEPL_NETWORK_'+type(e).__name__) from None
needed=sum(len(p['source_html']) for d in books for p in d['paragraphs'] if not p.get('target_html'));manifest['remaining_characters_upper_bound']=needed
try:
 usage=call('/v2/usage');manifest['usage_before']=usage
 if usage.get('character_limit') is not None and usage.get('character_count',0)+needed>usage['character_limit']:
  manifest.update(state='BLOCKED_QUOTA',reason='Insufficient existing account quota; no limits changed');save();raise SystemExit(2)
except Exception as e:manifest.update(state='BLOCKED',reason=str(e));save();raise
save()
def translate(d):
 ps=d['paragraphs'];ch=d['chapter'];path=OUT/f'{ch:03}.json'
 for start in range(0,len(ps),20):
  batch=[p for p in ps[start:start+20] if not p.get('target_html')]
  if not batch:continue
  context='Alexandre Dumas, Le comte de Monte-Cristo. French adventure novel. Chapter '+d['number']+'. Names: Edmond Dantès — Эдмон Дантес; Monte-Cristo — Монте-Кристо; Mercédès — Мерседес; Fernand — Фернан; Danglars — Данглар; Villefort — Вильфор; Faria — Фариа; Morrel — Моррель; Caderousse — Кадрусс; Haydée — Гайде. Surrounding original text:\n'+'\n\n'.join(p['source'] for p in ps[max(0,start-3):min(len(ps),start+23)])
  payload={'text':[p['source_html'] for p in batch],'source_lang':'FR','target_lang':'RU','model_type':'quality_optimized','tag_handling':'html','context':context}
  assert len(json.dumps(payload).encode())<128*1024,'REQUEST_TOO_LARGE'
  result=call('/v2/translate',payload);rows=result.get('translations',[]);assert len(rows)==len(batch),'PARAGRAPH_COUNT_MISMATCH'
  for p,row in zip(batch,rows):
   target=row.get('text','').strip();assert target,'EMPTY_TARGET';assert row.get('model_type_used')=='quality_optimized','UNEXPECTED_MODEL'
   p.update(target_html=target,target_sha256=sha(target),model_type_used=row['model_type_used'])
  atomic(path,d)
  with lock:
   manifest['units'][str(ch)]={'translated':sum(bool(p.get('target_html')) for p in ps),'total':len(ps)};save()
  print('CHAPTER',ch,'PARAGRAPHS',sum(bool(p.get('target_html')) for p in ps),'OF',len(ps),flush=True)
 return ch
errors=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
 jobs={pool.submit(translate,d):d['chapter'] for d in books}
 for future in concurrent.futures.as_completed(jobs):
  try:print('COMPLETE',future.result(),flush=True)
  except Exception as e:errors.append({'chapter':jobs[future],'error':str(e)});print('FAILED',jobs[future],str(e),flush=True)
manifest.update(state='PARTIAL_FAILURE' if errors else 'TRANSLATION_COMPLETE',errors=errors);save()
if errors:raise SystemExit(1)
