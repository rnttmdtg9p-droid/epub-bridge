from pathlib import Path
import urllib.request,urllib.parse,json,time,hashlib
R=Path('results/ru28-44-sources');R.mkdir(parents=True,exist_ok=True);ledger=[]
def get(params):
 time.sleep(6)
 u='https://ru.wikisource.org/w/api.php?'+urllib.parse.urlencode(dict(format='json',**params))
 req=urllib.request.Request(u,headers={'User-Agent':'BBClassicsProduction/1.0 (public-domain source research)'})
 return json.load(urllib.request.urlopen(req,timeout=45)),u
for rank,title in json.load(open('scripts/ru28-44-candidates.json')):
 row=dict(rank=rank,requested_title=title)
 try:
  data,u=get(dict(action='parse',page=title,prop='text|revid'))
  if 'parse' not in data:
   search,su=get(dict(action='query',list='search',srsearch=title.split(' (')[0],srnamespace=0,srlimit=8))
   row.update(status='TITLE_NOT_RESOLVED',error=data.get('error'),search=search,search_url=su)
  else:
   d=data['parse'];b=d['text']['*'];name=f'{rank:03}-index.html';(R/name).write_text(b)
   row.update(status='RECOVERED',file=name,url=u,revision=d['revid'],source_title=d['title'],sha256=hashlib.sha256(b.encode()).hexdigest())
 except Exception as e:row.update(status='FAILED',error=str(e))
 ledger.append(row);(R/'ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2));print(rank,row['status'],flush=True)
