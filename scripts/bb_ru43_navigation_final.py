"""Actual whole-body-grounded concise navigation with separate AI verification."""
from pathlib import Path
import os,json,time,urllib.request,zipfile,hashlib,io,shutil,datetime
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'results/ru43-navigation-final';OUT.mkdir(parents=True,exist_ok=True)
TOKEN=os.environ.get('GITHUB_TOKEN','');REPO='rnttmdtg9p-droid/epub-bridge';RUN=39966310480
H=lambda b:hashlib.sha256(b).hexdigest()
def save(p,d):
 p.parent.mkdir(parents=True,exist_ok=True);q=p.with_suffix('.writing');q.write_text(json.dumps(d,ensure_ascii=False,indent=2));q.replace(p)
def api(path):
 req=urllib.request.Request('https://api.github.com/repos/'+REPO+path,headers={'Authorization':'Bearer '+TOKEN,'Accept':'application/vnd.github+json','User-Agent':'BoundaryBayProduction'})
 class SafeRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,req,fp,code,msg,headers,newurl):
   redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
   from urllib.parse import urlparse
   if redirected is not None and urlparse(req.full_url).netloc!=urlparse(newurl).netloc:redirected.remove_header('Authorization')
   return redirected
 with urllib.request.build_opener(SafeRedirect()).open(req,timeout=60) as r:return r.read()
def main():
 while True:
  run=json.loads(api(f'/actions/runs/{RUN}'));save(OUT/'main-run-observed.json',{k:run.get(k) for k in ['id','status','conclusion','head_sha','updated_at']})
  if run['status']=='completed':
   assert run['conclusion']=='success','MAIN_TRANSLATION_RUN_NOT_SUCCESS';break
  print('WAITING_FOR_ACTUAL_FULL_TRANSLATION',run['status'],flush=True);time.sleep(30)
 arts=json.loads(api(f'/actions/runs/{RUN}/artifacts'))['artifacts'];matches=[a for a in arts if a['name']=='ru43-original-translation-review-v5070'];assert len(matches)==1
 a=matches[0];raw=api(f'/actions/artifacts/{a["id"]}/zip');(OUT/'actual-main-source-exchanges.zip').write_bytes(raw)
 z=zipfile.ZipFile(io.BytesIO(raw));assert z.testzip() is None
 for n in z.namelist():assert not n.startswith('/') and '..' not in Path(n).parts
 dest=ROOT/'results/ru43-original';dest.mkdir(exist_ok=True);z.extractall(dest)
 save(OUT/'actual-main-artifact.json',{'run_id':RUN,'artifact_id':a['id'],'sha256':H(raw),'bytes':len(raw),'zip_crc':'PASS','book_release':False})
 from bb_ru43_original import SOURCE,call,MODEL
 from bb_ru43_verify import qualify_unit
 assert len(SOURCE)==46
 accepted={}
 for u in SOURCE:
  c,proof=qualify_unit(ROOT,SOURCE,u['unit'],output_name='ru43-original');accepted[u['unit']]=c;save(OUT/'main-qualification'/f'unit-{u["unit"]:03}.json',proof)
 data=json.loads((ROOT/'inputs/ru43-navigation-final.json').read_text());overrides={x['source_id']:x for x in data['body_overlays']}
 for unit,c in accepted.items():
  for i,row in enumerate(c['rows']):
   if row['source_id'] in overrides:
    replacement=overrides[row['source_id']];assert replacement['source_sha256']==row['source_sha256'];c['rows'][i]=replacement
 save(OUT/'final-body-overlay-input.json',{'input_sha256':H((ROOT/'inputs/ru43-navigation-final.json').read_bytes()),'actual_source_comparison_runs':data['actual_source_comparison_runs'],'overlaid_source_ids':sorted(overrides)})
 results={};previous=[]
 system='You are the Boundary Bay Russian literary navigation editor. Analyze EVERY supplied body row, not its excluded chapter summary. Create a concise specific grammatical Russian chapter description, STRICTLY3–5space-separatedRussianwords and at most24Unicodecharacters; these are hard validated limits, not suggestions. Use the proposed editorial suggestion only if the complete body supports it. Name Huckleberry as Гек, never Хак. (the source division prefix is added separately). No generic labels, no invented facts, no quotation from omitted summary, no truncation of a summary, no typography tricks. Use the chapter narrative dominant event/theme, grounded by two non-adjacent body source IDs. All material claims must be supported by both anchors or explain complementary anchors for combined themes. Keep exact names consistent with reviewed Russian. Avoid all previous descriptions. Return JSON only: {"description":"Russian description without chapter number","anchors":["sourceID1","sourceID2"],"reason":"specific content grounding"}.'
 for u in SOURCE[3:]:
  n=u['unit'];c=accepted[n];targets={r['source_id']:r['target'] for r in c['rows']};rows=[{**r,'reviewed_russian':targets[r['id']]} for r in u['rows'] if r['tag'] not in ['source_argument','source_caption']];ids=[r['id'] for r in rows];bodyH=H(json.dumps(rows,ensure_ascii=False,sort_keys=True).encode());folder=OUT/f'{n:03}';folder.mkdir(exist_ok=True)
  payload={'task':'Read the complete eligible chapter body and draft a source-grounded concise navigation description.','editorial_starting_suggestion':data['proposals'][str(n)],'source_division':u['heading'],'russian_division':c['heading']['target'],'body_rows':rows,'previous_accepted_descriptions':previous,'excluded_roles':['source_argument','source_caption','BBmodules','notes','metadata'],'body_sha256':bodyH}
  def valid(d):
   assert isinstance(d.get('description'),str) and d['description'].strip()==d['description'] and 3<=len(d['description'].split())<=5 and len(d['description'])<=24
   assert isinstance(d.get('anchors'),list) and len(d['anchors'])>=2 and len(set(d['anchors']))==len(d['anchors'])
   assert all(i in ids for i in d['anchors']) and max(ids.index(i) for i in d['anchors'])-min(ids.index(i) for i in d['anchors'])>=2
   assert d.get('reason') and d['description'] not in previous
   return d
  def checked(p,path):
   last=None
   for attempt in range(3):
    q=dict(p)
    if last:q['format_retry_instruction']=last
    try:
     d,meta=call(system,q,folder/(path+f'.attempt{attempt:02}'),max_tokens=4096);return valid(d),meta
    except (ValueError,AssertionError,KeyError,TypeError) as e:last=type(e).__name__+': '+str(e)
   raise RuntimeError('NAV_STRICT_FORMAT_RETRIES_EXHAUSTED '+str(last))
  d,meta=checked(payload,'draft');history=[{'kind':'draft','result':d,'api':meta}]
  for turn in range(6):
   q=dict(payload);q.update(task='INDEPENDENT SOURCE-COMPARISON PASS: verify this proposed description against the WHOLE supplied body. Check every material claim, both exact source-ID anchors and Russian quality/uniqueness. Automatically correct if necessary, otherwise preserve exact description and anchors. Return same JSON schema; reason must explicitly assess support.',proposed=d)
   revised,meta=checked(q,f'comparison{turn:02}');history.append({'kind':'source-comparison','result':revised,'api':meta})
   stable=all(revised[k]==d[k] for k in ['description','anchors']);d=revised
   if stable:break
  else:raise RuntimeError('NAV_SOURCE_COMPARISON_NOT_STABLE')
  record={'unit':n,'chapter':u['chapter'],'source_role':'AUTHENTIC_CONTENTS_ARGUMENT_RETAINED_FULL_AT_OPENING','full_argument':next(r['text'] for r in u['rows'] if r['tag']=='source_argument'),'label':c['heading']['target'].rstrip('. ')+' — '+d['description'],'description':d['description'],'body_sha256':bodyH,'body_rows':len(rows),'excluded_node_ids':[r['id'] for r in u['rows'] if r['tag'] in ['source_argument','source_caption']],'anchors':[{**next(r for r in rows if r['id']==i)} for i in d['anchors']],'target':f'text/unit-{n:03}.xhtml#unit-{n:03}','history':history,'state':'ACTUAL_AI_BODY_GROUNDED_AND_SEPARATELY_SOURCE_COMPARED_PENDING_DEFAULT_TOC_RENDER','model':MODEL,'book_release':False}
  results[str(n)]=record;previous.append(d['description']);save(folder/'navigation.verified.json',record);save(OUT/'043_Qualified_Concise_Navigation.json',results);print('BODY_GROUNDED_NAVIGATION',n,'OF46',flush=True)
 assert set(results)=={str(n) for n in range(4,47)}
 save(OUT/'run-status.json',{'state':'COMPLETE_FINAL_BODY_GROUNDED_STRICT_CONCISE_NAVIGATION_PENDING_RENDER','chapters':43,'main_translation_run':RUN,'book_release':False,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
if __name__=='__main__':main()
