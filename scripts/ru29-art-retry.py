from pathlib import Path
import urllib.request,urllib.error,json,time,hashlib
R=Path('results/ru29-art');rows=json.load(open(R/'ledger.json'));time.sleep(90)
for row in rows:
 if row['status']=='RECOVERED':continue
 u=row['url'];p=R/u.rsplit('/',1)[-1];time.sleep(20)
 for attempt in range(2):
  try:
   response=urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'BBClassicsProduction/1.0 public-domain archival image research'}),timeout=45);b=response.read();p.write_bytes(b);row.update(status='RECOVERED',file=p.name,sha256=hashlib.sha256(b).hexdigest(),retry_attempt=attempt+1);break
  except urllib.error.HTTPError as e:
   row.update(error=str(e),retry_attempt=attempt+1)
   if e.code!=429:break
   delay=max(60,int(e.headers.get('Retry-After','60')) if e.headers.get('Retry-After','60').isdigit() else 60)
   if attempt==0:time.sleep(min(delay,300))
  except Exception as e:row.update(error=str(e),retry_attempt=attempt+1);break
 (R/'ledger.json').write_text(json.dumps(rows,indent=2));print(p.name,row['status'],flush=True)
