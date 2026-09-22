from pathlib import Path
import urllib.request,urllib.parse,json,time,hashlib
R=Path('results/ru29-selected');R.mkdir(parents=True,exist_ok=True);rows=[]
items=[(29,'Двадцать тысяч лье под водой (Верн; Вовчок)'),(33,'Вокруг света за восемьдесят дней (Жюль Верн)'),(34,'Похождения Тома Сойера (Твен; Энгельгардт)'),(43,'Похождения Гекльберри Финна (Твен; Энгельгардт)')]
for rank,title in items:
 time.sleep(6)
 u='https://ru.wikisource.org/w/api.php?'+urllib.parse.urlencode(dict(format='json',action='parse',page=title,prop='text|revid'))
 try:
  d=json.load(urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'BBClassicsProduction/1.0 public-domain research'}),timeout=60))['parse']
  text=d['text']['*'];(R/f'{rank:03}.html').write_text(text);rows.append(dict(rank=rank,title=d['title'],revision=d['revid'],url=u,sha256=hashlib.sha256(text.encode()).hexdigest(),status='RECOVERED'))
 except Exception as e:rows.append(dict(rank=rank,error=str(e),status='FAILED'))
 (R/'ledger.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
