import sys,json,subprocess,datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
import bb_ru85_claude as initial
r=initial.ROOT;out=r/'results/ru85-art-captions-initial';out.mkdir(parents=True,exist_ok=True);src=json.load(open(r/'inputs/ru85-art-captions-source.json'));rows=src['rows'];assert len(rows)==39;done=out/'captions.translated.json'
if done.exists():
 item=json.load(open(done));initial.validate(item['rows'],rows);print('REUSE_VALID_INITIAL_CAPTIONS39')
else:
 system=initial.SYSTEM+' This request contains ONLY39 authentic English historical plate captions, not body paragraphs. Translate ONLY supplied caption text. Context rows are source English aids, never additional output. First person remains first person, names match glossary; figurative titles are translated with narrative sense. No literary review, no evaluation of any Russian translation, no invented caption for the uncaptioned title vignette. Return39 rows in exact supplied order.'
 result,receipt=initial.checked_call(system,{'task':'Return JSON {"rows":[{"source_id":"exact supplied id","target":"complete Russian caption"}]}. Initial translation ONLY of every supplied caption, no explanations.','source_rows':rows},out/'captions.initial',rows);item=dict(rank=85,state='INITIAL_TRANSLATION_ONLY_PENDING_INDEPENDENT_REVIEW',rows=result['rows'],provider_receipts=[receipt],source_rows=39,source_qualification=src['state'],utc=datetime.datetime.now(datetime.timezone.utc).isoformat());initial.save(done,item)
subprocess.run(['git','add','results/ru85-art-captions-initial'],cwd=r,check=True)
if subprocess.run(['git','diff','--cached','--quiet'],cwd=r).returncode:
 subprocess.run(['git','commit','-m','Rank85 initial Claude translation39 historical plate captions only'],cwd=r,check=True);subprocess.run(['git','push','origin','HEAD'],cwd=r,check=True)
print('INITIAL_CAPTIONS39_SAVED_REVIEW_PENDING',item['provider_receipts'])
