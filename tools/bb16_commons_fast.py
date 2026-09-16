import requests,json,re,time,hashlib
from pathlib import Path
S=requests.Session();S.headers['User-Agent']='BoundaryBayClassics/5.0.14 exact-work illustration research (github.com/rnttmdtg9p-droid/epub-bridge)';API='https://commons.wikimedia.org/w/api.php';OUT=Path('commons_fast');OUT.mkdir()
def api(p):
 p={'action':'query','format':'json','formatversion':2,**p}
 for i in range(5):
  r=S.get(API,params=p,timeout=60)
  if r.status_code in (429,500,502,503,504):time.sleep(2**i);continue
  r.raise_for_status();return r.json()
 raise RuntimeError('api')
def cat(c):
 out=[];cont=None
 while 1:
  p={'list':'categorymembers','cmtitle':'Category:'+c,'cmnamespace':6,'cmlimit':500}
  if cont:p['cmcontinue']=cont
  q=api(p);out += [x['title'] for x in q['query']['categorymembers']];cont=q.get('continue',{}).get('cmcontinue')
  if not cont:return out
def search(q,limit=300):
 out=[];off=0
 while len(out)<limit:
  d=api({'list':'search','srsearch':q,'srnamespace':6,'srlimit':min(500,limit-len(out)),'sroffset':off});b=[x['title'] for x in d['query']['search']];out+=b
  if not b or 'continue' not in d:return out
  off=d['continue']['sroffset']
def val(em,k):
 x=(em or {}).get(k,{});return str(x.get('value','') if isinstance(x,dict) else x)
def pd(em):
 t=' '.join([val(em,'LicenseShortName'),val(em,'UsageTerms'),val(em,'Copyrighted')]).lower();return ('public domain' in t or 'cc0' in t or 'pdm' in t) and 'noncommercial' not in t
def acquire(book,titles,extra):
 root=OUT/book;root.mkdir();titles=list(dict.fromkeys(titles));records=[]
 for j in range(0,len(titles),40):
  batch=titles[j:j+40];q=api({'prop':'imageinfo','iiprop':'url|extmetadata|size','iiurlwidth':1600,'titles':'|'.join(batch)})
  for pg in q.get('query',{}).get('pages',[]):
   title=pg.get('title');info=(pg.get('imageinfo') or [{}])[0];em=info.get('extmetadata',{});url=info.get('thumburl') or info.get('url');ok=pd(em)
   rec={'title':title,'url':url,'descriptionurl':info.get('descriptionurl'),'width':info.get('width'),'height':info.get('height'),'thumbwidth':info.get('thumbwidth'),'thumbheight':info.get('thumbheight'),'rights_pass':ok,'license':val(em,'LicenseShortName'),'artist':val(em,'Artist'),'date':val(em,'DateTimeOriginal'),'description':val(em,'ImageDescription'),'categories':val(em,'Categories')}
   if url and ok and re.search(r'\.(jpe?g|png|tiff?|webp)(\?|$)',url,re.I):
    try:
     b=S.get(url,timeout=90).content;fn=re.sub(r'[^A-Za-z0-9._-]+','_',title.replace('File:',''))[:170];ext=Path(url.split('?')[0]).suffix.lower();
     if not Path(fn).suffix:fn+=ext
     (root/fn).write_bytes(b);rec['file']=fn;rec['sha256']=hashlib.sha256(b).hexdigest()
    except Exception as e:rec['download_error']=repr(e)
   records.append(rec);time.sleep(.08)
 (root/'records.json').write_text(json.dumps({'book':book,'extra':extra,'records':records},ensure_ascii=False,indent=2))
acquire('075_Papa_Goriot',search('intitle:"Illustrations de Le Père Goriot"',100),{'program':'Quantin 1885, Albert Lynch / Eugène Abot'})
acquire('078_Certosa_di_Parma',cat('Illustration from La Chartreuse de Parme by Valentin Foulquier (1883)'),{'program':'Conquet/Foulquier 1883'})
bel=[t for t in search('intitle:"Maupassant Bel-ami"',300) if re.search(r'Maupassant Bel-ami \d+\.jpg$',t,re.I)];acquire('081_Bel_Ami',bel,{'program':'Ollendorff 1901, Ferdinand Bac / G. Lemoine'})
bou=cat('Boule de suif')+search('intitle:"Boule de Suif" Thévenot Romagnol',100)+search('intitle:"Maupassant - Boule de suif, 1902 page"',200);acquire('082_Boule_de_suif',bou,{'program':'prefer 1897 Thévenot/Romagnoli or 1902 Jeanniot'})
for book,qs in {'087_Il_gabbiano':['"The Seagull" Chekhov Moscow Art Theatre 1898','Чайка Чехов Московский художественный театр 1898'],'088_Giardino_dei_ciliegi':['"The Cherry Orchard" Chekhov Moscow Art Theatre 1904','Вишнёвый сад Чехов Московский художественный театр 1904'],'089_Zio_Vanja':['"Uncle Vanya" Chekhov Moscow Art Theatre 1899','Дядя Ваня Чехов Московский художественный театр 1899']}.items():
 ts=[]
 for q in qs:ts+=search(q,80)
 acquire(book,ts,{'program':'exact-play historical production images; manual review'})