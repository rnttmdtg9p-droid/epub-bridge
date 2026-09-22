from pathlib import Path
import urllib.request,urllib.parse,json,time,hashlib
R=Path('results/ru30-44-chapters');R.mkdir(parents=True,exist_ok=True);rows=[];counts={}
for rank,title in json.load(open('scripts/ru30-44-chapters.json')):
 counts[rank]=counts.get(rank,0)+1;name=f'{rank:03}-{counts[rank]:03}.html';time.sleep(6)
 u='https://ru.wikisource.org/w/api.php?'+urllib.parse.urlencode(dict(format='json',action='parse',page=title,prop='text|revid'))
 row=dict(rank=rank,title=title,file=name,url=u)
 try:
  d=json.load(urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'BBClassicsProduction/1.0 public-domain text research'}),timeout=45))['parse'];b=d['text']['*'];(R/name).write_text(b);row.update(status='RECOVERED',revision=d['revid'],sha256=hashlib.sha256(b.encode()).hexdigest())
 except Exception as e:row.update(status='FAILED',error=str(e))
 rows.append(row);(R/'ledger.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2));print(rank,name,row['status'],flush=True)
