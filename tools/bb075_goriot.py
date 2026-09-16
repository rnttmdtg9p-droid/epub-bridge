import requests,json,re,time,hashlib
from pathlib import Path
S=requests.Session();S.headers['User-Agent']='BoundaryBayClassics/5.0.14 exact-work illustration research';A='https://commons.wikimedia.org/w/api.php';R=Path('goriot');R.mkdir();q=S.get(A,params={'action':'query','format':'json','list':'search','srsearch':'intitle:"Illustrations de Le Père Goriot"','srnamespace':6,'srlimit':20},timeout=60).json();titles=[x['title'] for x in q['query']['search']];out=[]
for title in titles:
 d=S.get(A,params={'action':'query','format':'json','prop':'imageinfo','iiprop':'url|extmetadata|size','iiurlwidth':1400,'titles':title},timeout=60).json();pg=next(iter(d['query']['pages'].values()));info=pg['imageinfo'][0];u=info.get('thumburl') or info['url'];b=None
 for i in range(6):
  rr=S.get(u,timeout=90)
  if rr.ok and rr.headers.get('content-type','').startswith('image/'):b=rr.content;break
  time.sleep(3+i*2)
 if b:
  fn=re.sub(r'[^A-Za-z0-9._-]+','_',title.replace('File:',''));(R/fn).write_bytes(b);out.append({'title':title,'file':fn,'url':u,'sha256':hashlib.sha256(b).hexdigest(),'info':info})
 time.sleep(1.2)
(R/'records.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))