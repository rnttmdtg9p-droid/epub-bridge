import os,re,json,hashlib,zipfile,io,time
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from PIL import Image
OUT=Path('assets'); OUT.mkdir(exist_ok=True)
S=requests.Session(); S.headers['User-Agent']='BoundaryBayClassics/5.0.14 exact-work illustration research (github.com/rnttmdtg9p-droid/epub-bridge)'
def get(url, **kw):
    last=None
    for i in range(5):
        try:
            r=S.get(url,timeout=60,**kw)
            if r.status_code in (429,500,502,503,504): time.sleep(min(20,2**i)); last=RuntimeError(f'{r.status_code} {url}'); continue
            r.raise_for_status(); return r
        except Exception as e: last=e; time.sleep(min(20,2**i))
    raise last
def safe(s): return re.sub(r'[^A-Za-z0-9._-]+','_',s).strip('_')[:180]
def sha(b): return hashlib.sha256(b).hexdigest()
def save_bytes(path,b): path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(b); return sha(b)
def commons_api(params):
    p={'action':'query','format':'json','formatversion':2}; p.update(params); return get('https://commons.wikimedia.org/w/api.php',params=p).json()
def commons_file_info(title):
    q=commons_api({'prop':'imageinfo','iiprop':'url|extmetadata|size','titles':title}); pg=q['query']['pages'][0]; return pg.get('imageinfo',[{}])[0]
def pd_ok(info):
    em=info.get('extmetadata',{}) or {}
    def v(k):
        x=em.get(k,{}); return str(x.get('value','') if isinstance(x,dict) else x)
    text=' '.join([v('LicenseShortName'),v('UsageTerms'),v('Copyrighted'),v('License')]).lower()
    return ('public domain' in text or 'cc0' in text or 'pdm' in text) and 'noncommercial' not in text and 'no derivatives' not in text
def category_titles(cat):
    titles=[]; cont=None
    while True:
        p={'list':'categorymembers','cmtitle':'Category:'+cat,'cmnamespace':6,'cmlimit':'500'}
        if cont:p['cmcontinue']=cont
        q=commons_api(p); titles += [x['title'] for x in q['query']['categorymembers']]; cont=q.get('continue',{}).get('cmcontinue')
        if not cont: break
    return titles
def search_titles(query, limit=500):
    titles=[]; off=0
    while len(titles)<limit:
        q=commons_api({'list':'search','srsearch':query,'srnamespace':6,'srlimit':min(500,limit-len(titles)),'sroffset':off}); batch=[x['title'] for x in q['query']['search']]; titles += batch
        if not batch or 'continue' not in q: break
        off=q['continue']['sroffset']
    return titles
def download_commons_titles(book,titles,extra=None):
    rec=[]; d=OUT/book/'images'; d.mkdir(parents=True,exist_ok=True)
    for title in titles:
        try:
            info=commons_file_info(title); url=info.get('url')
            if not url: continue
            ext=Path(url.split('?')[0]).suffix.lower()
            if ext not in ('.jpg','.jpeg','.png','.tif','.tiff','.webp'): continue
            rights=pd_ok(info); b=get(url).content; fn=safe(title.removeprefix('File:'))
            if not Path(fn).suffix: fn += ext
            dest=d/fn; save_bytes(dest,b); rec.append({'file_title':title,'url':url,'description_url':info.get('descriptionurl'),'width':info.get('width'),'height':info.get('height'),'sha256':sha(b),'rights_pass':rights,'extmetadata':info.get('extmetadata',{})})
        except Exception as e: rec.append({'file_title':title,'error':repr(e)})
        time.sleep(.12)
    (OUT/book/'records.json').write_text(json.dumps({'book':book,'source':'Wikimedia Commons','records':rec,'extra':extra},ensure_ascii=False,indent=2)); return rec
def pg_download(book, ebook_ids):
    root=OUT/book; root.mkdir(parents=True,exist_ok=True); rec={'book':book,'source':'Project Gutenberg','ebooks':[]}
    for eid in ebook_ids:
        urls=[f'https://www.gutenberg.org/cache/epub/{eid}/pg{eid}-h.zip',f'https://www.gutenberg.org/files/{eid}/{eid}-h.zip',f'https://www.gutenberg.org/cache/epub/{eid}/pg{eid}-images.html',f'https://www.gutenberg.org/files/{eid}/{eid}-h/{eid}-h.htm']
        got=None
        for u in urls:
            try:
                r=get(u)
                if len(r.content)>1000: got=(u,r.content,r.headers.get('content-type','')); break
            except: pass
        if not got: rec['ebooks'].append({'id':eid,'status':'FAILED'}); continue
        u,b,ct=got; er={'id':eid,'url':u,'sha256':sha(b),'content_type':ct,'images':[]}; work=root/f'pg{eid}'; work.mkdir(exist_ok=True); html_files=[]
        if b[:2]==b'PK':
            with zipfile.ZipFile(io.BytesIO(b)) as z:
                for n in z.namelist():
                    if n.lower().endswith(('.htm','.html')):
                        dest=work/safe(Path(n).name); dest.write_bytes(z.read(n)); html_files.append(dest)
                    elif n.lower().endswith(('.jpg','.jpeg','.png','.gif')): save_bytes(work/'raw'/safe(Path(n).name),z.read(n))
        else:
            dest=work/'source.html'; dest.write_bytes(b); html_files=[dest]
        seen=set(); order=0
        for hp in html_files:
            soup=BeautifulSoup(hp.read_bytes(),'html.parser')
            for im in soup.find_all('img'):
                src=im.get('src')
                if not src: continue
                key=Path(src.split('?')[0]).name
                if key in seen: continue
                seen.add(key); order+=1; raw_path=work/'raw'/safe(key)
                if not raw_path.exists():
                    from urllib.parse import urljoin
                    try: save_bytes(raw_path,get(urljoin(u,src)).content)
                    except: pass
                heading=None
                prev=im.find_previous(['h1','h2','h3','h4','h5','h6'])
                if prev: heading=' '.join(prev.stripped_strings)
                parent=im.parent; cap=' '.join(parent.stripped_strings) if parent else ''
                if not cap: cap=im.get('alt','') or im.get('title','') or ''
                if raw_path.exists():
                    try:
                        with Image.open(raw_path) as ii: wh=list(ii.size)
                    except: wh=None
                    er['images'].append({'order':order,'filename':raw_path.name,'sha256':sha(raw_path.read_bytes()),'width_height':wh,'alt':im.get('alt',''),'caption':cap[:1000],'nearest_heading':heading})
        er['status']='DOWNLOADED'; rec['ebooks'].append(er)
    (root/'records.json').write_text(json.dumps(rec,ensure_ascii=False,indent=2)); return rec
# Project Gutenberg exact-work illustrated editions
pg_download('044_Frankenstein',[42324]); pg_download('046_Ragione_e_sentimento',[21839]); pg_download('054_Sherlock_Holmes',[48320]); pg_download('059_Canterville',[14522]); pg_download('070_Notre_Dame',[70891,70892,70893,70894])
# Great Expectations scan with Marcus Stone illustrations
try:
    u='https://upload.wikimedia.org/wikipedia/commons/6/6b/Great_expectations_and_Hard_times_%28IA_greatexpectation08dick%29.pdf'; b=get(u).content; save_bytes(OUT/'050_Grandi_speranze'/'greatexpectations_hardtimes.pdf',b); (OUT/'050_Grandi_speranze'/'records.json').write_text(json.dumps({'source':'Wikimedia Commons/Internet Archive scan','url':u,'sha256':sha(b),'note':'Great Expectations with Marcus Stone illustrations; exact-work plates extracted in integration stage'},indent=2))
except Exception as e:
    (OUT/'050_Grandi_speranze').mkdir(parents=True,exist_ok=True); (OUT/'050_Grandi_speranze'/'records.json').write_text(json.dumps({'error':repr(e)}))
# Commons exact-work programs
bal=search_titles('intitle:"Illustrations de Le Père Goriot"',100); download_commons_titles('075_Papa_Goriot',bal,{'expected_program':'10 etchings, Quantin 1885, Albert Lynch / Eugène Abot'})
st=category_titles('Illustration from La Chartreuse de Parme by Valentin Foulquier (1883)'); download_commons_titles('078_Certosa_di_Parma',st,{'expected_program':'Conquet/Foulquier 1883 complete Commons category'})
bel=search_titles('intitle:"Maupassant Bel-ami"',300); bel=[t for t in bel if re.search(r'Maupassant Bel-ami \d+\.jpg$',t,re.I)]; download_commons_titles('081_Bel_Ami',bel,{'expected_program':'Ferdinand Bac / G. Lemoine, Ollendorff 1901; 103 illustrations advertised'})
bou=category_titles('Boule de suif')+search_titles('intitle:"Boule de Suif" Thévenot Romagnol',100)+search_titles('intitle:"Maupassant - Boule de suif, 1902 page"',200); download_commons_titles('082_Boule_de_suif',list(dict.fromkeys(bou)),{'preferred_programs':'Armand Magnier 1897 Thévenot/Romagnoli or Ollendorff 1902 Jeanniot; exact-work only'})
# Chekhov exact-play historical production image candidates
for book,queries in {
 '087_Il_gabbiano':['"The Seagull" Chekhov Moscow Art Theatre 1898','Чайка Чехов Московский художественный театр 1898','Чехов Чайка спектакль'],
 '088_Giardino_dei_ciliegi':['"The Cherry Orchard" Chekhov Moscow Art Theatre 1904','Вишнёвый сад Чехов Московский художественный театр 1904','Чехов Вишневый сад спектакль'],
 '089_Zio_Vanja':['"Uncle Vanya" Chekhov Moscow Art Theatre 1899','Дядя Ваня Чехов Московский художественный театр 1899','Чехов Дядя Ваня спектакль']}.items():
    ts=[]
    for q in queries: ts += search_titles(q,100)
    download_commons_titles(book,list(dict.fromkeys(ts)),{'role':'candidate exact-play historical production images; manual review required'})
# Metamorphosis early exact-work candidates
met=[]
for q in ['"Die Verwandlung" Kafka illustration','"Die Verwandlung" Kafka Rosy Lilienfeld','"Die Verwandlung" Kafka Wessel','"Metamorphosis" Kafka illustration']: met += search_titles(q,100)
download_commons_titles('091_Metamorfosi',list(dict.fromkeys(met)),{'role':'candidate exact-work illustrations; cover-only assets do not satisfy interior program'})
# overall manifest
manifest=[]
for p in OUT.rglob('records.json'):
    try:
        d=json.loads(p.read_text()); manifest.append({'path':str(p),'sha256':sha(p.read_bytes()),'summary':{'records':len(d.get('records',[])),'ebooks':len(d.get('ebooks',[]))}})
    except: pass
(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))