from pathlib import Path
from lxml import html
import urllib.request,urllib.error,json,os,time,re,hashlib,unicodedata
from sklearn.feature_extraction.text import TfidfVectorizer
OUT=Path('results/ru22-art');OUT.mkdir(parents=True,exist_ok=True)
url='https://www.gutenberg.org/cache/epub/1184/pg1184-images.html'
raw=urllib.request.urlopen(url,timeout=90).read();root=html.fromstring(raw);rows=[]
for img in root.xpath('//img'):
 anc=img.xpath('ancestor::div[@class="chapter"][h3]')
 if not anc:continue
 c=anc[0];ch=int(c.xpath('./h3')[0].text_content().strip().split('.')[0].split()[-1]);prev=img.getparent().getprevious()
 while prev is not None and prev.tag!='p':prev=prev.getprevious()
 anchor=prev.text_content().strip() if prev is not None else ''
 ps=c.xpath('./p');fraction=(ps.index(prev)+1)/len(ps) if prev in ps else 0
 rows.append({'asset':Path(img.get('src')).stem,'chapter':ch,'anchor_english':anchor,'source_fraction':fraction})
key=os.environ['DEEPL_AUTH_KEY'];host='https://api-free.deepl.com' if key.endswith(':fx') else 'https://api.deepl.com'
for start in range(0,len(rows),20):
 batch=rows[start:start+20];payload={'text':[x['anchor_english'] or 'Illustration' for x in batch],'source_lang':'EN','target_lang':'FR','model_type':'quality_optimized','context':'Alexandre Dumas, Le Comte de Monte-Cristo. Translate these passages to match the original French novel.'}
 for attempt in range(3):
  try:
   req=urllib.request.Request(host+'/v2/translate',data=json.dumps(payload).encode(),headers={'Authorization':'DeepL-Auth-Key '+key,'Content-Type':'application/json'})
   with urllib.request.urlopen(req,timeout=120) as r:result=json.load(r)
   assert len(result['translations'])==len(batch)
   break
  except Exception:
   if attempt==2:raise
   time.sleep(3*(attempt+1))
 for a,b in zip(batch,result['translations']):a['anchor_french']=b['text']
 (OUT/'anchors.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2));print(start+len(batch),flush=True)
for ch in range(1,118):
 selected=[x for x in rows if x['chapter']==ch]
 if not selected:continue
 unit=json.loads((Path('results/ru22-deepl')/f'{ch:03}.json').read_text());ps=[x for x in unit['paragraphs'] if x['role']=='body']
 texts=[x['source'] for x in ps]+[a['anchor_french'] for a in selected]
 m=TfidfVectorizer(analyzer='char_wb',ngram_range=(3,5),strip_accents='unicode').fit_transform(texts)
 scores=(m[len(ps):]@m[:len(ps)].T).toarray()
 for a,sc in zip(selected,scores):
  ranked=sorted(range(len(ps)),key=lambda j:float(sc[j])-.12*abs((j+1)/len(ps)-a['source_fraction']),reverse=True)
  ix=ranked[0];a.update(after_id=ps[ix]['id'],source_anchor_french=ps[ix]['source'],target_anchor_russian=html.fromstring(ps[ix]['target_html']).text_content(),similarity=float(sc[ix]),candidates=[{'id':ps[j]['id'],'score':float(sc[j]),'text':ps[j]['source']} for j in ranked[:3]])
(OUT/'alignment.json').write_text(json.dumps({'source_url':url,'source_sha256':hashlib.sha256(raw).hexdigest(),'method':'DeepL English-to-French anchor translation then French char-ngram TF-IDF with weak sequence prior','images':rows},ensure_ascii=False,indent=2))
