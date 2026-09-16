import requests,zipfile,io,json,hashlib,re,time
from pathlib import Path
from bs4 import BeautifulSoup
from PIL import Image
S=requests.Session();S.headers['User-Agent']='BoundaryBayClassics/5.0.14 exact-work illustration research';OUT=Path('pg_assets');OUT.mkdir()
def get(u):
 for i in range(5):
  r=S.get(u,timeout=60)
  if r.status_code in (429,500,502,503,504):time.sleep(2**i);continue
  r.raise_for_status();return r
 raise RuntimeError(u)
def safe(s):return re.sub(r'[^A-Za-z0-9._-]+','_',s)
def sha(b):return hashlib.sha256(b).hexdigest()
def one(book,eids):
 root=OUT/book;root.mkdir(parents=True);R=[]
 for eid in eids:
  got=None
  for u in [f'https://www.gutenberg.org/cache/epub/{eid}/pg{eid}-h.zip',f'https://www.gutenberg.org/files/{eid}/{eid}-h.zip',f'https://www.gutenberg.org/cache/epub/{eid}/pg{eid}-images.html']:
   try:
    r=get(u)
    if len(r.content)>1000:got=(u,r.content);break
   except:pass
  if not got:R.append({'id':eid,'status':'FAILED'});continue
  u,b=got;d=root/f'pg{eid}';d.mkdir();htmls=[]
  if b[:2]==b'PK':
   with zipfile.ZipFile(io.BytesIO(b)) as z:
    for n in z.namelist():
     if n.lower().endswith(('.htm','.html')):
      p=d/safe(Path(n).name);p.write_bytes(z.read(n));htmls.append(p)
     elif n.lower().endswith(('.jpg','.jpeg','.png','.gif')):(d/safe(Path(n).name)).write_bytes(z.read(n))
  else:
   p=d/'source.html';p.write_bytes(b);htmls=[p]
  ims=[];seen=set();order=0
  for hp in htmls:
   soup=BeautifulSoup(hp.read_bytes(),'html.parser')
   for im in soup.find_all('img'):
    src=im.get('src');key=Path(src or '').name
    if not key or key in seen:continue
    seen.add(key);order+=1;rp=d/safe(key)
    if not rp.exists():
     from urllib.parse import urljoin
     try:rp.write_bytes(get(urljoin(u,src)).content)
     except:continue
    prev=im.find_previous(['h1','h2','h3','h4','h5','h6']);cap=' '.join(im.parent.stripped_strings) if im.parent else ''
    try:
     with Image.open(rp) as ii:wh=list(ii.size)
    except:wh=None
    ims.append({'order':order,'file':rp.name,'sha256':sha(rp.read_bytes()),'size':wh,'caption':cap[:1000] or im.get('alt',''),'heading':' '.join(prev.stripped_strings) if prev else None})
  R.append({'id':eid,'url':u,'status':'DOWNLOADED','images':ims})
 (root/'records.json').write_text(json.dumps(R,ensure_ascii=False,indent=2))
for b,ids in {'044_Frankenstein':[42324],'046_Ragione_e_sentimento':[21839],'054_Sherlock_Holmes':[48320],'059_Canterville':[14522],'070_Notre_Dame':[70891,70892,70893,70894]}.items():one(b,ids)