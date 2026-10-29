import sys,json,hashlib,datetime,subprocess
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
import bb_ru85_claude as initial
r=initial.ROOT;out=r/'results/ru85-dedication-initial';out.mkdir(parents=True,exist_ok=True)
src=json.load(open(r/'inputs/ru85-dedication-source.json'));row=src['row'];assert initial.sha(row['text'])==row['sha256'];done=out/'dedication.translated.json'
if done.exists():
 item=json.load(open(done));initial.validate(item['rows'],[row]);print('REUSED_VALID_INITIAL_DEDICATION')
else:
 system="Authorized INITIAL translation ONLY of one newly recovered authentic 1850 author dedication from David Copperfield into modern literary Russian. This is not literary review. Translate solely supplied English, never consult any published Russian translation. Preserve complete dedication meaning, honorific, both recipients, Richard Watson, Rockingham and Northamptonshire. The 5 source lines are typographic dedication lines, not 5 sentences: preserve exactly5 nonempty target lines while allowing natural Russian grammar across lines. Preserve identifiers. Do not translate the reader's handwritten ownership inscription: it is not in supplied source. No invented heading, names, dates, notes or explanation. Return requested JSON only."
 result,receipt=initial.checked_call(system,{'task':'Return {"rows":[{"source_id":"en85-supp-dedication","target":"complete dedication in5 nonempty lines"}]}','source_rows':[row]},out/'dedication.initial',[row]);item={'rank':85,'state':'INITIAL_TRANSLATION_ONLY_PENDING_INDEPENDENT_REVIEW','rows':result['rows'],'provider_receipts':[receipt],'source_qualification':src['qualification'],'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()};initial.save(done,item)
subprocess.run(['git','add','results/ru85-dedication-initial'],cwd=r,check=True)
if subprocess.run(['git','diff','--cached','--quiet'],cwd=r).returncode:
 subprocess.run(['git','commit','-m','Rank85 initial Claude translation of recovered authentic dedication only'],cwd=r,check=True);subprocess.run(['git','push','origin','HEAD'],cwd=r,check=True)
print('INITIAL_DEDICATION_SAVED_REVIEW_PENDING',item['provider_receipts'])
