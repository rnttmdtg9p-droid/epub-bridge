import requests,zipfile,io,json,re
from pathlib import Path
from bs4 import BeautifulSoup
from PIL import Image
u='https://www.gutenberg.org/cache/epub/71445/pg71445-h.zip';b=requests.get(u,timeout=90).content;root=Path('notre2');root.mkdir();z=zipfile.ZipFile(io.BytesIO(b));htmls=[]
for n in z.namelist():
 p=root/Path(n).name
 if n.lower().endswith(('.htm','.html')):p.write_bytes(z.read(n));htmls.append(p)
 elif n.lower().endswith(('.jpg','.jpeg','.png','.gif')):p.write_bytes(z.read(n))
rec=[];seen=set();order=0
for hp in htmls:
 s=BeautifulSoup(hp.read_bytes(),'html.parser')
 for im in s.find_all('img'):
  key=Path(im.get('src','')).name
  if not key or key in seen or not (root/key).exists():continue
  seen.add(key);order+=1;prev=im.find_previous(['h1','h2','h3','h4']);cap=' '.join(im.parent.stripped_strings) if im.parent else ''
  try:
   with Image.open(root/key) as ii:wh=list(ii.size)
  except:wh=None
  rec.append({'order':order,'file':key,'size':wh,'caption':cap[:1000] or im.get('alt',''),'heading':' '.join(prev.stripped_strings) if prev else None})
(root/'records.json').write_text(json.dumps({'id':71445,'url':u,'images':rec},ensure_ascii=False,indent=2))