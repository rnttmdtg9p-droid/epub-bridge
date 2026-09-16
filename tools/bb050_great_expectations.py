import requests,fitz,json,hashlib
from pathlib import Path
u='https://upload.wikimedia.org/wikipedia/commons/6/6b/Great_expectations_and_Hard_times_%28IA_greatexpectation08dick%29.pdf';root=Path('great_expectations');root.mkdir();pdf=root/'source.pdf';pdf.write_bytes(requests.get(u,timeout=180).content);doc=fitz.open(pdf);metrics=[];cand=[]
for i,p in enumerate(doc):
 text=' '.join(p.get_text('text').split());imgs=p.get_images(full=True);metrics.append({'page':i+1,'text_chars':len(text),'text':text[:300],'images':len(imgs)})
 # Great Expectations occupies first volume section; select low-text/full-plate candidates before Hard Times heading.
 if i<500 and (len(text)<180 or 'MARCUS STONE' in text.upper() or 'GREAT EXPECTATIONS' in text.upper() and len(text)<300):
  pix=p.get_pixmap(matrix=fitz.Matrix(1.4,1.4),alpha=False);fn=root/f'page_{i+1:04d}.jpg';pix.save(fn);cand.append(fn.name)
(root/'metrics.json').write_text(json.dumps({'url':u,'pdf_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),'pages':len(doc),'candidates':cand,'metrics':metrics},ensure_ascii=False,indent=2))