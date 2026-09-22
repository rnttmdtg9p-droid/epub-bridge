from pathlib import Path
from html.parser import HTMLParser
import urllib.request,urllib.parse,json,time,hashlib
R=Path('results/ru29-art');R.mkdir(parents=True,exist_ok=True)
class P(HTMLParser):
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if tag=='img' and 'Wern_z_text' in a.get('src',''):urls.append(('https:'+a['src']).split('?')[0])
urls=[];P().feed(Path('results/ru29-selected/029.html').read_text());rows=[]
for u in urls:
 time.sleep(2);p=R/u.rsplit('/',1)[-1]
 try:
  data=urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'BBClassicsProduction/1.0 source research'}),timeout=45).read();p.write_bytes(data);rows.append(dict(url=u,file=p.name,sha256=hashlib.sha256(data).hexdigest(),status='RECOVERED'))
 except Exception as e:rows.append(dict(url=u,status='FAILED',error=str(e)))
 (R/'ledger.json').write_text(json.dumps(rows,indent=2))
