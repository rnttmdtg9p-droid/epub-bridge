import requests,fitz,json,hashlib
from pathlib import Path
api='https://commons.wikimedia.org/w/api.php';title='File:Great expectations and Hard times (IA greatexpectation08dick).pdf';q=requests.get(api,params={'action':'query','format':'json','prop':'imageinfo','iiprop':'url|extmetadata','titles':title},timeout=60).json();page=next(iter(q['query']['pages'].values()));info=page['imageinfo'][0];u=info['url'];root=Path('great_expectations');root.mkdir();pdf=root/'source.pdf';r=requests.get(u,timeout=180);r.raise_for_status();pdf.write_bytes(r.content);doc=fitz.open(pdf);metrics=[];cand=[]
for i,p in enumerate(doc):
 text=' '.join(p.get_text('text').split());imgs=p.get_images(full=True);metrics.append({'page':i+1,'text_chars':len(text),'text':text[:300],'images':len(imgs)})
 if i<500 and (len(text)<180 or 'MARCUS STONE' in text.upper() or ('GREAT EXPECTATIONS' in text.upper() and len(text)<300)):
  pix=p.get_pixmap(matrix=fitz.Matrix(1.4,1.4),alpha=False);fn=root/f'page_{i+1:04d}.jpg';pix.save(fn);cand.append(fn.name)
(root/'metrics.json').write_text(json.dumps({'commons_title':title,'url':u,'extmetadata':info.get('extmetadata',{}),'pdf_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),'pages':len(doc),'candidates':cand,'metrics':metrics},ensure_ascii=False,indent=2))