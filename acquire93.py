#!/usr/bin/env python3
"""Acquire immutable Italian source candidates; never equate acquisition with rights/edition approval."""
from pathlib import Path
from urllib.parse import urlencode, urlparse, urljoin, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, json, hashlib, time, re, unicodedata, html, difflib, zipfile, io, os, threading
from bs4 import BeautifulSoup

ROOT=Path('output'); ROOT.mkdir(exist_ok=True)
ROSTER=json.loads(Path('roster.json').read_text(encoding='utf-8'))
HEADERS={'User-Agent':'BoundaryBaySourceAcquisition/1.0 (public-domain source research; no commercial clearance asserted)'}
ALIASES={7:['Don Chisciotte'],11:['Anna Karenin'],21:['Werther'],24:['Sogno di una notte'],25:['Candido'],34:['Le confessioni di un italiano'],35:['Tom Sawyer'],36:['Huckleberry Finn'],40:['Gulliver'],41:['Isola del tesoro'],42:['Jekyll'],52:['Cantico di Natale','Canto di Natale'],54:['Sherlock Holmes'],55:['Baskerville'],58:['Importanza di essere onesto','Importanza di chiamarsi Ernesto'],61:['La morte di Ivan Ilic','Ivan Ilic'],64:['Idiota'],65:['Memorie del sottosuolo','Ricordi dal sottosuolo'],68:['Il mantello','Il cappotto'],70:['Nostra Signora di Parigi'],73:['Venti anni dopo'],75:['Papa Goriot','Il padre Goriot'],76:['Eugenie Grandet'],79:['Germinal'],80:['Nana','Nanà'],82:['Palla di sego','Pallina','Boule de suif'],89:['Zio Vania'],96:['Le metamorfosi'],99:['Ricordi','Pensieri'],100:['Repubblica']}
# Only these Gutenberg works were positively matched to Italian catalogue entries by title and author.
PG={16:[55236],31:[71218],40:[61179],72:[60641,60642,60643,60644],73:[67846]}
LOCK=threading.Lock(); EVENTS=[]

def norm(s):
    return re.sub(r'[^a-z0-9]+',' ',unicodedata.normalize('NFKD',html.unescape(s)).encode('ascii','ignore').decode().lower()).strip()

def event(d):
    with LOCK:
        EVENTS.append(d)
        with (ROOT/'transfer_events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(d,ensure_ascii=False)+'\n')

def fetch(url,path,limit=35000000):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():return path.read_bytes()
    for attempt in range(2):
        try:
            r=requests.get(url,headers=HEADERS,timeout=(15,45),stream=True)
            if r.status_code in (429,502,503,504) and attempt==0:
                time.sleep(min(10,int(r.headers.get('Retry-After','3')) if r.headers.get('Retry-After','').isdigit() else 3));continue
            r.raise_for_status()
            data=bytearray()
            for chunk in r.iter_content(65536):
                data.extend(chunk)
                if len(data)>limit:raise ValueError('File exceeds acquisition safety limit')
            b=bytes(data);path.write_bytes(b)
            event({'url':url,'final_url':r.url,'path':str(path.relative_to(ROOT)),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'status':'SAVED','content_type':r.headers.get('content-type')})
            return b
        except Exception as e:
            if attempt==1:
                event({'url':url,'path':str(path.relative_to(ROOT)),'status':'FAILED','error':str(e)})
                raise
            time.sleep(1)

def getjson(url,path):return json.loads(fetch(url,path).decode('utf-8-sig'))

def epub_info(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        bad=z.testzip()
        if bad:raise ValueError('EPUB CRC failure: '+bad)
        from xml.etree import ElementTree as ET
        c=ET.fromstring(z.read('META-INF/container.xml'))
        opf=c.find('.//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile').attrib['full-path']
        tree=ET.fromstring(z.read(opf))
        dc='{http://purl.org/dc/elements/1.1/}'
        info={k:[e.text or '' for e in tree.iter(dc+k)] for k in ['title','creator','language','rights','source','date','identifier']}
        info['spine_count']=len(tree.findall('.//{http://www.idpf.org/2007/opf}itemref'))
        info['zip_members']=len(z.infolist());info['crc']='PASS'
        return info

def score(title,query):
    a,b=norm(title),norm(query)
    if a==b:return 1.0
    if a.startswith(b+' ') or b.startswith(a+' '):return .90
    return difflib.SequenceMatcher(None,a,b).ratio()

def acquire_pg(rank,folder,result):
    for num in PG.get(rank,[]):
        stem=folder/'sources'/f'pg{num}'
        info={'provider':'Project Gutenberg','id':num}
        for ext,url in [('rdf',f'https://www.gutenberg.org/ebooks/{num}.rdf'),('epub',f'https://www.gutenberg.org/ebooks/{num}.epub3.images'),('txt',f'https://www.gutenberg.org/cache/epub/{num}/pg{num}.txt')]:
            try:
                data=fetch(url,stem.with_suffix('.'+ext))
                if ext=='epub':info['epub']=epub_info(data)
                info[ext+'_path']=str(stem.with_suffix('.'+ext).relative_to(ROOT))
            except Exception as e:info[ext+'_error']=str(e)
        result['gutenberg'].append(info)

def acquire_ll(rank,title,author,folder,result):
    queries=[title]+ALIASES.get(rank,[])
    seen=set();candidates=[]
    for qi,q in enumerate(queries):
        u='https://liberliber.it/wp-json/wp/v2/search?'+urlencode({'search':q,'subtype':'page','per_page':100})
        try:
            found=getjson(u,folder/'discovery'/f'll_search_{qi}.json')
            if not isinstance(found,list):continue
            for item in found:
                t=html.unescape(item.get('title',''));url=item.get('url','');pid=item.get('id')
                if pid in seen or 'audiolibr' in norm(t) or 'libro parlato' in norm(t):continue
                best=max(score(t,x) for x in queries)
                atok=[x for x in norm(author).split() if len(x)>3]
                author_url=any(x in norm(url).split() for x in atok)
                # Title-only exact matches still remain unapproved candidates; author checked later from source metadata.
                if best>=.70 and (author_url or best>=.95):
                    seen.add(pid);candidates.append((best,item))
            if candidates and qi==0:break
        except Exception as e:result['errors'].append('LL search '+q+': '+str(e))
        time.sleep(.3)
    candidates.sort(key=lambda x:x[0],reverse=True)
    # Multi-volume matches are kept, not silently reduced to one volume.
    for best,item in candidates[:24]:
        pid=item['id'];rec={'id':pid,'title':html.unescape(item['title']),'url':item['url'],'match_score':best,'assets':[],'status':'CANDIDATE_ONLY'}
        api='https://liberliber.it/wp-json/wp/v2/pages/'+str(pid)
        try:
            page=getjson(api,folder/'discovery'/f'll_page_{pid}.json')
            markup=page.get('content',{}).get('rendered','')
            (folder/'discovery'/f'll_page_{pid}.html').write_text(markup,encoding='utf-8')
            soup=BeautifulSoup(markup,'html.parser')
            rec['page_text']=soup.get_text(' ',strip=True)[:40000]
            links=[]
            for a in soup.find_all('a',href=True):
                url=urljoin(item['url'],html.unescape(a['href']))
                pth=urlparse(url).path.lower()
                if re.search(r'\.(?:epub|txt|txt\.zip|html?\.zip|pdf|odt)$',pth):
                    if not any(w in pth for w in ['audiolib','mp3','podcast']):links.append(url)
            links=list(dict.fromkeys(links));rec['source_links']=links
            # Preserve one native EPUB and the plain transcription where offered; PDF only as fallback.
            epubs=[u for u in links if urlparse(u).path.lower().endswith('.epub')]
            texts=[u for u in links if re.search(r'\.(?:txt|txt\.zip|html?\.zip)$',urlparse(u).path.lower())]
            picks=epubs[:2]+texts[:2]
            if not picks:picks=[u for u in links if urlparse(u).path.lower().endswith('.pdf')][:1]
            for j,url in enumerate(picks):
                basename=unquote(Path(urlparse(url).path).name)
                dest=folder/'sources'/f'll_{pid}_{j}_{basename}'
                try:
                    data=fetch(url,dest)
                    asset={'url':url,'path':str(dest.relative_to(ROOT)),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
                    if basename.lower().endswith('.epub'):asset['epub']=epub_info(data)
                    elif basename.lower().endswith('.zip'):
                        with zipfile.ZipFile(io.BytesIO(data)) as z:
                            asset['zip_members']=z.namelist();asset['crc']='PASS' if z.testzip() is None else 'FAIL'
                    elif basename.lower().endswith('.pdf') and not data.startswith(b'%PDF'):raise ValueError('not a PDF')
                    rec['assets'].append(asset)
                except Exception as e:rec.setdefault('download_errors',[]).append(str(e))
            if rec['assets']:rec['status']='SOURCE_CANDIDATE_SAVED'
        except Exception as e:rec['error']=str(e)
        result['liberliber'].append(rec)
        time.sleep(.25)

def discover_ws(rank,title,folder,result):
    titlemap={25:'Candido',99:'Ricordi',100:'La Repubblica',73:'Vent\'anni dopo'}
    wtitle=titlemap.get(rank,title)
    params={'action':'parse','page':wtitle,'prop':'text|links|revid','format':'json','redirects':1,'disableeditsection':1,'maxlag':5}
    url='https://it.wikisource.org/w/api.php?'+urlencode(params)
    try:
        obj=getjson(url,folder/'discovery'/'wikisource_index.json')
        if 'parse' in obj:
            p=obj['parse'];markup=p.get('text',{}).get('*','')
            (folder/'discovery'/'wikisource_index.html').write_text(markup,encoding='utf-8')
            s=BeautifulSoup(markup,'html.parser')
            links=[x for x in p.get('links',[]) if x.get('ns')==0]
            result['wikisource']={'title':p.get('title'),'revid':p.get('revid'),'url':'https://it.wikisource.org/wiki/'+wtitle.replace(' ','_'),'links':links,'text_preview':s.get_text(' ',strip=True)[:10000],'status':'INDEX_SAVED_NOT_COMPLETE_TEXT'}
        else:result['wikisource']={'status':'INDEX_NOT_RESOLVED','error':obj.get('error')}
    except Exception as e:result['wikisource']={'status':'FAILED','error':str(e)}

def run(row):
    rank,title,author=row;folder=ROOT/f'{rank:03d}';folder.mkdir(exist_ok=True)
    (folder/'discovery').mkdir(exist_ok=True)
    result={'rank':rank,'title':title,'author':author,'gutenberg':[],'liberliber':[],'wikisource':{},'errors':[],'rights_status':'NOT_CLEARED_FOR_COMMERCIAL_EDITION','completeness_status':'REVIEW_REQUIRED'}
    acquire_pg(rank,folder,result)
    acquire_ll(rank,title,author,folder,result)
    discover_ws(rank,title,folder,result)
    (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    count=sum(len(x['assets']) for x in result['liberliber'])+sum('epub_path' in x for x in result['gutenberg'])
    print(f'{rank:03d} {title}: {count} source candidates; WS {result["wikisource"].get("status")}',flush=True)
    return result

with ThreadPoolExecutor(max_workers=3) as ex:
    futures={ex.submit(run,row):row for row in ROSTER}
    results=[]
    for f in as_completed(futures):
        try:results.append(f.result())
        except Exception as e:
            row=futures[f];results.append({'rank':row[0],'title':row[1],'fatal_error':str(e)})
results.sort(key=lambda x:x['rank'])
(ROOT/'discovery_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
(ROOT/'README.txt').write_text('Immutable source-candidate acquisition. Downloads are NOT production EPUBs. Original source licence notices are retained. Matching, translation rights, volume completeness and editorial integrity must be reviewed separately. Search indexes are never counted as complete books.\n',encoding='utf-8')
print('Finished roster:',len(results),flush=True)
