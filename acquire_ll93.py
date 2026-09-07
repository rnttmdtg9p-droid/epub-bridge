#!/usr/bin/env python3
"""Conservative source acquisition using published Liber Liber download links.
Preserve originals. All selections remain subject to title/edition/rights review.
"""
from pathlib import Path
from urllib.parse import urlencode,urljoin,urlparse,quote,unquote,parse_qs
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from email.utils import parsedate_to_datetime
import json,time,hashlib,re,unicodedata,html,difflib,zipfile,io,datetime
from bs4 import BeautifulSoup

OUT=Path('output');OUT.mkdir(exist_ok=True)
ROSTER=json.loads(Path('roster.json').read_text())
UA='BoundaryBaySourceAcquisition/1.0 (public-domain text research)'
NEXT={};EVENTS=[]
ALIAS={7:['Don Chisciotte'],11:['Anna Karenine','Anna Karenin'],20:['Milione'],21:['Werther'],24:['Sogno di una notte'],25:['Candido'],34:['Confessioni di un italiano'],35:['Tom Sawyer'],36:['Huckleberry Finn'],40:['Gulliver'],41:['Isola del tesoro'],42:['Jekyll'],52:['Cantico di Natale','Canto di Natale'],54:['Sherlock Holmes'],55:['Baskerville'],58:['Importanza di essere onesto','Importanza di chiamarsi Ernesto'],61:['Ivan Ilic'],65:['Memorie del sottosuolo','Ricordi dal sottosuolo'],68:['Il mantello'],70:['Nostra Signora di Parigi'],73:['Venti anni dopo'],75:['Papa Goriot','Il padre Goriot'],76:['Eugenie Grandet'],79:['Germinal'],80:['Nanà'],82:['Palla di sego','Boule de suif'],89:['Zio Vania'],96:['Le metamorfosi'],99:['Ricordi','Pensieri'],100:['Repubblica']}
COVERED={16,31,40,72,73}

def norm(s):return re.sub(r'[^a-z0-9]+',' ',unicodedata.normalize('NFKD',html.unescape(s)).encode('ascii','ignore').decode().lower()).strip()

def log(x):
 EVENTS.append(x)
 with (OUT/'transfer_events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(x,ensure_ascii=False)+'\n')

def fetch(url,path,max_bytes=55000000,timeout=60):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 host=urlparse(url).hostname
 for attempt in range(3):
  delay=NEXT.get(host,0)-time.monotonic()
  if delay>0:time.sleep(delay)
  NEXT[host]=time.monotonic()+1.2
  try:
   with urlopen(Request(url,headers={'User-Agent':UA}),timeout=timeout) as r:
    b=r.read(max_bytes+1)
    if len(b)>max_bytes:raise ValueError('source file exceeds 55MB transfer limit')
    rec={'url':url,'final_url':r.url,'status':r.status,'content_type':r.headers.get('Content-Type'),'path':str(path.relative_to(OUT)),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
   path.write_bytes(b);log(rec);return b,rec
  except HTTPError as e:
   body=e.read(120000)
   if e.code==429 and attempt<2:
    ra=e.headers.get('Retry-After','60')
    try:wait=max(60,int(ra))
    except ValueError:
     try:wait=max(60,(parsedate_to_datetime(ra)-datetime.datetime.now(datetime.timezone.utc)).total_seconds())
     except:wait=90
    NEXT[host]=time.monotonic()+wait
    log({'url':url,'status':429,'wait_seconds':wait,'attempt':attempt+1});continue
   err=path.with_name(path.name+'.error.txt');err.write_bytes(body)
   log({'url':url,'status':e.code,'error':str(e),'error_body':str(err.relative_to(OUT))});raise
  except Exception as e:
   if attempt<1:time.sleep(2);continue
   log({'url':url,'status':'ERROR','error':str(e)});raise

def gj(url,path):return json.loads(fetch(url,path)[0].decode('utf-8-sig'))

def score(t,q):
 a,b=norm(t),norm(q)
 if a==b:return 1.
 if (' '+b+' ') in (' '+a+' ') or (' '+a+' ') in (' '+b+' '):return .91
 return difflib.SequenceMatcher(None,a,b).ratio()

def epi(data):
 from xml.etree import ElementTree as E
 with zipfile.ZipFile(io.BytesIO(data)) as z:
  if z.testzip():raise ValueError('EPUB CRC failed')
  ct=E.fromstring(z.read('META-INF/container.xml'))
  fn=ct.find('.//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile').get('full-path')
  op=E.fromstring(z.read(fn));dc='{http://purl.org/dc/elements/1.1/}'
  d={k:[x.text or '' for x in op.iter(dc+k)] for k in ['title','creator','contributor','language','rights','publisher','date','source','identifier']}
  d['spine_count']=len(op.findall('.//{http://www.idpf.org/2007/opf}itemref'));d['crc']='PASS';return d

def get_download(url,folder,pid,kind):
 b,rec=fetch(url,folder/'discovery'/f'link_{pid}_{kind}.response')
 if not b.startswith((b'PK',b'%PDF')):
  soup=BeautifulSoup(b,'html.parser');found=[]
  for a in soup.find_all('a',href=True):
   u=urljoin(rec['final_url'],html.unescape(a['href']));path=urlparse(u).path.lower()
   if path.endswith('.'+kind) or (kind=='epub' and path.endswith('.epub.zip')):found.append(u)
  # Download only explicit published links, never infer private storage endpoints.
  if not found:raise ValueError('Download page has no explicit '+kind+' file link')
  b,rec2=fetch(found[0],folder/'sources'/f'll_{pid}.{kind}')
  rec2['download_landing_url']=url;rec=rec2
 else:
  path=folder/'sources'/f'll_{pid}.{kind}';path.parent.mkdir(exist_ok=True);path.write_bytes(b)
  rec=dict(rec,path=str(path.relative_to(OUT)))
 if kind=='epub':rec['epub']=epi(b)
 elif kind=='pdf' and not b.startswith(b'%PDF'):raise ValueError('Not PDF bytes')
 return rec

def run(row):
 rank,title,author=row;folder=OUT/f'{rank:03d}';(folder/'discovery').mkdir(parents=True,exist_ok=True)
 result={'rank':rank,'title':title,'author':author,'queries':[],'candidates':[],'errors':[],'rights_status':'REVIEW_REQUIRED','completeness_status':'REVIEW_REQUIRED'}
 if rank in COVERED:
  result['status']='SOURCE_ALREADY_SAVED_IN_PHASE1';return result
 queries=list(dict.fromkeys([title]+ALIAS.get(rank,[])))
 candidates={}
 for qi,q in enumerate(queries[:3]):
  u='https://liberliber.it/wp-json/wp/v2/search?search='+quote(q,safe='')+'&per_page=20'
  result['queries'].append(u)
  try:
   found=gj(u,folder/'discovery'/f'search_{qi}.json')
   if not isinstance(found,list):continue
   for item in found:
    if item.get('subtype')!='page':continue
    t=html.unescape(item.get('title',''));url=item.get('url','')
    if 'audiolibr' in norm(t) or 'libro parlato' in norm(t):continue
    val=max(score(t,x) for x in queries)
    at=[x for x in norm(author).split() if len(x)>3]
    au=any(x in norm(url).split() for x in at)
    if val>=.69 and (au or val>=.99):candidates[item['id']]=(val,au,item)
   if any(x[0]>=.99 and x[1] for x in candidates.values()):break
  except Exception as e:result['errors'].append(str(e))
 for val,au,item in sorted(candidates.values(),key=lambda x:(x[1],x[0]),reverse=True)[:18]:
  pid=item['id'];c={'id':pid,'title':html.unescape(item['title']),'url':item['url'],'match_score':val,'author_url_match':au,'assets':[]}
  try:
   obj=gj('https://liberliber.it/wp-json/wp/v2/pages/'+str(pid),folder/'discovery'/f'page_{pid}.json')
   markup=obj.get('content',{}).get('rendered','');soup=BeautifulSoup(markup,'html.parser')
   (folder/'discovery'/f'page_{pid}.html').write_text(markup,encoding='utf-8')
   c['page_text']=soup.get_text(' ',strip=True)[:50000]
   links=[urljoin(item['url'],html.unescape(a['href'])) for a in soup.find_all('a',href=True)]
   c['source_links']=[u for u in links if 'opere/download/' in u or re.search(r'\.(epub|pdf|txt|zip)$',urlparse(u).path.lower())]
   eps=[u for u in c['source_links'] if 'type=opera_url_epub' in u or urlparse(u).path.lower().endswith('.epub')]
   pdfs=[u for u in c['source_links'] if 'type=opera_url_pdf' in u or urlparse(u).path.lower().endswith('.pdf')]
   pick=('epub',eps[0]) if eps else ('pdf',pdfs[0]) if pdfs else None
   if pick:
    try:c['assets'].append(get_download(pick[1],folder,pid,pick[0]))
    except Exception as e:c['download_error']=str(e)
   c['status']='SOURCE_CANDIDATE_SAVED' if c['assets'] else 'NO_SOURCE_SAVED'
  except Exception as e:c['error']=str(e)
  result['candidates'].append(c)
 result['status']='SOURCE_CANDIDATE_SAVED' if any(c['assets'] for c in result['candidates']) else 'NO_SOURCE_SAVED'
 (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 return result

rows=[]
for row in ROSTER:
 try:r=run(row)
 except Exception as e:r={'rank':row[0],'title':row[1],'status':'ERROR','error':str(e)}
 rows.append(r)
 (OUT/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
 print(f'{row[0]:03d} {row[1]}: {r["status"]}; saved {sum(len(c.get("assets",[])) for c in r.get("candidates",[]))}',flush=True)
print('ROSTER_PROCESSED',len(rows),flush=True)
