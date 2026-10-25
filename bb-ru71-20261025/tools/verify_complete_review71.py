from pathlib import Path
import json,os,time,hashlib,urllib.request,urllib.error,zipfile,io,xml.etree.ElementTree as E,re,unicodedata
ROOT=Path.cwd();OUT=ROOT/'verified-review';OUT.mkdir(exist_ok=True)
REPO='rnttmdtg9p-droid/epub-bridge';REVIEW_RUN=40173299575;DRAFT_RUN=40169507560
SOURCE_SHA='ce89228f73983326dc0a3a4c368caa4002741ae7dea168ba114829615493e1f3'
H=lambda b:hashlib.sha256(b).hexdigest()
TOKEN=os.environ['GITHUB_TOKEN']
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
def api(path):
 req=urllib.request.Request('https://api.github.com/repos/'+REPO+path,headers={'Authorization':'Bearer '+TOKEN,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'BoundaryBay-rank71-native-verifier'})
 with urllib.request.urlopen(req,timeout=90) as q:return json.load(q)
def archive(a):
 req=urllib.request.Request('https://api.github.com/repos/'+REPO+'/actions/artifacts/'+str(a['id'])+'/zip',headers={'Authorization':'Bearer '+TOKEN,'Accept':'application/vnd.github+json','User-Agent':'BoundaryBay-rank71-native-verifier'})
 try:
  with urllib.request.build_opener(NoRedirect).open(req,timeout=90) as q:blob=q.read()
 except urllib.error.HTTPError as e:
  if e.code not in [301,302,303,307,308]:raise
  location=e.headers['Location']
  # Signed cross-host URL receives no GitHub Authorization header.
  with urllib.request.urlopen(urllib.request.Request(location,headers={'User-Agent':'BoundaryBay-rank71-native-verifier'}),timeout=180) as q:blob=q.read()
 assert a.get('digest','').startswith('sha256:') and H(blob)==a['digest'].split(':',1)[1],('ARTIFACT_NATIVE_HASH',a['name'])
 z=zipfile.ZipFile(io.BytesIO(blob));assert z.testzip() is None
 names=z.namelist();assert len(names)==1 and Path(names[0]).name==names[0] and names[0].endswith('.json')
 (OUT/'native').mkdir(exist_ok=True);(OUT/'native'/(a['name']+'.zip')).write_bytes(blob)
 raw=z.read(names[0]);return json.loads(raw),dict(artifact_id=a['id'],name=a['name'],archive_sha256=H(blob),archive_bytes=len(blob),member=names[0],member_sha256=H(raw),member_bytes=len(raw))
def plain(n):
 v=n.text or ''
 for c in n:v+=('\n' if c.tag=='br' else plain(c))+(c.tail or '')
 return v
def norm(t):return ' '.join(unicodedata.normalize('NFC',t).split()).casefold()
deadline=time.time()+21000
while True:
 run=api('/actions/runs/'+str(REVIEW_RUN))
 assert run['head_branch']=='bb-ru71-production-20261025-a' and run['head_sha']=='d6bb728368260604491080e6c129d8571873dfa8'
 if run['status']=='completed':
  assert run['conclusion']=='success',('REVIEW_RUN_NEEDS_AUTOMATIC_RECOVERY',run['conclusion'])
  break
 assert time.time()<deadline,'COMPLETE_REVIEW_STILL_RUNNING_RETRY_VERIFIER_LATER'
 print('WAITING_FOR_COMPLETE_REVIEW_RUN',REVIEW_RUN,flush=True);time.sleep(45)
drafts=api('/actions/runs/'+str(DRAFT_RUN)+'/artifacts?per_page=100')['artifacts'];reviews=api('/actions/runs/'+str(REVIEW_RUN)+'/artifacts?per_page=100')['artifacts']
drafts={a['name']:a for a in drafts if a['name'].startswith('ru71-deepl-')};reviews={a['name']:a for a in reviews if a['name'].startswith('ru71-reviewed-')}
assert len(drafts)==len(reviews)==20
units=[];artifact_proofs=[];closures=[];models=set();changed=0;emtotal=0;brtotal=0;notetotal=0;allids=[]
for i in range(20):
 srcpath=ROOT/'bb-ru71-20261025/source'/f'{i:02}.json';src=json.load(open(srcpath));u=src['unit'];uid=u['id']
 assert src['source_document_sha256']==SOURCE_SHA and H(json.dumps(u,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())==src['unit_source_sha256']
 assert uid==('preface' if i==0 else f'chapter-{i:02}')
 da=next(a for name,a in drafts.items() if name.endswith(f'{i:02}-{uid}'))
 ra=reviews[f'ru71-reviewed-{i:02}-{uid}'];draft,dp=archive(da);review,rp=archive(ra);artifact_proofs.extend([dp,rp])
 assert draft['state']=='COMPLETE_TRANSLATION_DRAFT_REQUIRES_COMPLETE_AI_REVIEW' and review['state']=='PASS_COMPLETE_UNIT_AI_FIDELITY_LITERARY_CORRECTIONS_AND_INLINE_COVERAGE'
 assert draft['unit_source_sha256']==review['unit_source_sha256']==src['unit_source_sha256']
 assert draft['source_document_sha256']==review['source_document_sha256']==SOURCE_SHA
 assert len(u['rows'])==len(draft['rows'])==len(review['rows'])
 assert [r['id'] for r in u['rows']]==[r['id'] for r in draft['rows']]==[r['id'] for r in review['rows']]
 exchanges={e['request_sha256']:e for e in review['exchanges']}
 for e in exchanges.values():
  assert H(json.dumps(e['request_body'],ensure_ascii=False).encode())==e['request_sha256']
  assert H(json.dumps(e['response'],ensure_ascii=False).encode())==e['response_sha256']
  assert e['response_model']==e['response']['model']=='claude-sonnet-5-5';models.add(e['response_model'])
 response_records=draft.get('provider_exchange_cache',[])+draft.get('batches',[])
 returned={(t['text'].strip(),t.get('model_type_used')) for e in response_records for t in e.get('response',{}).get('translations',[])}
 for s,d,r in zip(u['rows'],draft['rows'],review['rows']):
  allids.append(s['id']);assert H(s['source'].encode())==s['source_sha256'] and H(s['source_xml'].encode())==s['source_xml_sha256']
  assert r['source_sha256']==d['source_sha256']==s['source_sha256'] and r['source_xml_sha256']==d['source_xml_sha256']==s['source_xml_sha256']
  assert d['model_type_used']=='quality_optimized' and (d['target_xml'],'quality_optimized') in returned
  assert H(d['target'].encode())==d['target_sha256'] and H(d['target_xml'].encode())==d['target_xml_sha256']
  assert r['draft_target_sha256']==d['target_sha256'] and r['draft_target_xml_sha256']==d['target_xml_sha256']
  assert r['state']=='PASS_COMPLETE_SOURCE_ALIGNED_FIDELITY_LITERARY_AND_CORRECTION_VERIFICATION' and r['fidelity_status']==r['literary_status']=='PASS'
  assert r['fidelity_reason'] and r['literary_reason'] and r['review_request_sha256'] in exchanges
  xml=r['accepted_target_xml'];a=E.fromstring(s['source_xml']);b=E.fromstring(xml)
  assert b.tag=='p' and sum(n.tag=='p' for n in b.iter())==1 and all(n.tag in ['p','em','br','bbnote'] for n in b.iter())
  assert all(not n.attrib or (n.tag=='bbnote' and set(n.attrib)=={'n'}) for n in b.iter())
  assert H(xml.encode())==r['accepted_target_xml_sha256'] and H(plain(b).strip().encode())==r['accepted_target_sha256']
  assert not re.search('Харрис|Монморенс',plain(b),re.I)
  assert len(list(a.iter('br')))==len(list(b.iter('br')))
  assert [(n.get('n'),n.text) for n in a.iter('bbnote')]==[(n.get('n'),n.text) for n in b.iter('bbnote')]
  sem=[plain(n).strip() for n in a.iter('em')];tem=[plain(n).strip() for n in b.iter('em')];coverage=r['emphasis_coverage'];assert sorted(c['source_index'] for c in coverage)==list(range(len(sem)))
  combined=' '.join(norm(t) for t in tem);covered=set()
  for c in coverage:
   assert c['target_texts'] and c['reason']
   for part in c['target_texts']:
    matches=list(re.finditer(re.escape(norm(part)),combined));assert matches
    for m in matches:covered.update(range(m.start(),m.end()))
  assert not any(ch.isalnum() and k not in covered for k,ch in enumerate(combined))
  if not sem:assert not tem
  different=E.canonicalize(xml)!=E.canonicalize(d['target_xml'])
  if different:
   changed+=1;assert r['comparison_passes'];last=r['comparison_passes'][-1];assert last['request_sha256'] in exchanges
   proposed=next(t for t in json.loads(exchanges[last['request_sha256']]['request_body']['messages'][0]['content'])['rows'] if t['id']==r['id'])['candidate_target_xml']
   assert E.canonicalize(proposed)==E.canonicalize(last['review']['accepted_target_xml'])==E.canonicalize(xml)
  for v in r['comparison_passes']:assert v['request_sha256'] in exchanges
  if d.get('inline_markup_findings'):
   assert r['raw_markup_finding_disposition']=='REPAIRED_AND_SOURCE_VERIFIED';closures.append(dict(id=r['id'],unit=uid,raw_findings=d['inline_markup_findings'],accepted_xml_changed=different,source_xml_sha256=s['source_xml_sha256'],draft_target_xml_sha256=d['target_xml_sha256'],accepted_target_xml_sha256=r['accepted_target_xml_sha256'],source_emphasis_spans=len(sem),accepted_emphasis_spans=len(tem),emphasis_coverage=coverage,disposition='PASS_SOURCE_SEMANTIC_COVERAGE_AND_CORRECTION_VERIFICATION'))
  emtotal+=len(sem);brtotal+=len(list(a.iter('br')));notetotal+=len(list(a.iter('bbnote')))
 units.append(dict(id=uid,number=u['number'],source_heading=u['source_heading'],native_review_sha256=rp['member_sha256'],rows=review['rows']))
 (OUT/'reviewed-units').mkdir(exist_ok=True);(OUT/'reviewed-units'/f'{i:02}-{uid}.json').write_text(json.dumps(review,ensure_ascii=False,indent=2))
 print('NATIVE_SOURCE_MODEL_FIDELITY_LITERARY_CORRECTION_INLINE_VERIFIED',uid,len(review['rows']),flush=True)
assert len(allids)==len(set(allids))==1417 and emtotal==173 and brtotal==28 and notetotal==2 and len(closures)==21 and sum(len(c['raw_findings']) for c in closures)==21
side=next(r for u in units for r in u['rows'] if r['id']=='chapter-16-r0020');assert re.search('сторон|берег',side['accepted_target'],re.I)
note=next(r for u in units for r in u['rows'] if r['id']=='chapter-19-authorial-note-2');assert re.search(r'[-–—]{2,}|(?:окрестност|район|близост|неподал|соседств).*[-–—]',note['accepted_target'],re.I)
frozen=dict(state='PASS_COMPLETE_1417_SOURCE_ALIGNED_AI_REVIEW_AND_CORRECTION_VERIFICATION',rank=71,source_document_sha256=SOURCE_SHA,review_models=sorted(models),reviewed_rows=1417,source_emphasis_spans=173,source_line_breaks=28,note_markers=2,corrected_rows=changed,raw_markup_finding_rows_closed=21,raw_markup_findings_closed=21,units=units,release_ready=False)
path=OUT/'071_Frozen_Reviewed_Translation.json';path.write_text(json.dumps(frozen,ensure_ascii=False,indent=2))
proof=dict(state='PASS_ALL40_NATIVE_PROVIDER_ARTIFACTS_ALL20_REVIEWED_UNITS1417_ROWS',rank=71,source_document_sha256=SOURCE_SHA,frozen_reviewed_translation_sha256=H(path.read_bytes()),draft_run=DRAFT_RUN,review_run=REVIEW_RUN,review_models=sorted(models),reviewed_rows=1417,source_emphasis_spans=173,source_br=28,note_markers=2,raw_inline_findings_closed=21,corrected_rows=changed,artifacts=artifact_proofs,closures=closures,release_ready=False,EPUB_final_rebuild_and_exact_byte_acceptance='PENDING_EXECUTION_WORKSPACE_AVAILABILITY',counts=dict(released=66,historical_saved=112,initial_remaining=74,active_rank=71),checkpoint_is_stop_condition=False)
(OUT/'071_Complete_Reviewed_Translation_Native_Verification.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))
summary={k:proof[k] for k in ['state','rank','source_document_sha256','frozen_reviewed_translation_sha256','reviewed_rows','source_emphasis_spans','source_br','note_markers','raw_inline_findings_closed','corrected_rows','release_ready','counts']}
print('COMPLETE_REVIEW_VERIFIED',json.dumps(summary),flush=True)
with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as q:q.write('Rank71: complete native source-aligned AI review independently verified. 20units,1417rows,173source emphasis spans,28linebreaks,2notes,21raw markup findings closed. Final EPUB rebuild and final-byte acceptance remain pending; not released.\n\n'+json.dumps(summary,indent=2))
