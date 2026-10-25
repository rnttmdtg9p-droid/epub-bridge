from pathlib import Path
import json,os,urllib.request,urllib.error,hashlib,sys,time,datetime,xml.etree.ElementTree as E
H=lambda s:hashlib.sha256(s.encode()).hexdigest();index=int(sys.argv[1]);root=Path(__file__).resolve().parents[2];inputs=root/'bb-ru71-20261025/source';out=root/'translation/ru71';out.mkdir(parents=True,exist_ok=True);src=json.load(open(inputs/f'{index:02}.json'));unit=src['unit'];assert H(json.dumps(unit,ensure_ascii=False,sort_keys=True,separators=(',',':')))==src['unit_source_sha256'];assert all(H(r['source'])==r['source_sha256'] and H(r['source_xml'])==r['source_xml_sha256'] for r in unit['rows']);uid=unit['id'];path=out/f'{index:02}-{uid}.json';key=os.environ.get('DEEPL_AUTH_KEY','').strip()
if not key:raise SystemExit('MISSING_APPROVED_DEEPL_CONNECTION')
host='https://api-free.deepl.com' if key.endswith(':fx') else 'https://api.deepl.com'
if index>0:
 prior=list(out.glob(f'{index-1:02}-*.json'))
 if len(prior)!=1 or json.load(open(prior[0])).get('state')!='COMPLETE_TRANSLATION_DRAFT_REQUIRES_COMPLETE_AI_REVIEW':raise SystemExit('PREVIOUS_AUTHENTIC_UNIT_NOT_COMPLETED')
if path.exists():
 data=json.load(open(path));assert data['unit_source_sha256']==src['unit_source_sha256'];assert len(data['rows'])==len(unit['rows']);assert [r['id'] for r in data['rows']]==[r['id'] for r in unit['rows']]
else:data=dict(rank=71,unit=uid,source_title='Three Men in a Boat (To Say Nothing of the Dog)',source_edition='J.W.Arrowsmith1889;ProjectGutenberg308',source_document_sha256=src['source_document_sha256'],unit_source_sha256=src['unit_source_sha256'],engine='DeepL',requested_model='quality_optimized',requested_tag_handling_version='v2',state='TRANSLATION_IN_PROGRESS',rows=[dict(r) for r in unit['rows']],batches=[])
def save():
 data['updated_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat();tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2));tmp.replace(path)
def call(endpoint,payload=None):
 req=urllib.request.Request(host+endpoint,data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None,headers={'Authorization':'DeepL-Auth-Key '+key,'Content-Type':'application/json','X-DeepL-Reporting-Tag':'boundary-bay-ru71'})
 for attempt in range(5):
  try:
   with urllib.request.urlopen(req,timeout=120) as response:return json.load(response)
  except urllib.error.HTTPError as e:
   if e.code not in [429,500,502,503,504,529] or attempt==4:raise RuntimeError('DEEPL_HTTP_'+str(e.code)) from None
   delay=min(30,max(5,int(e.headers.get('Retry-After','5')) if str(e.headers.get('Retry-After','5')).isdigit() else 5)*(attempt+1));print('PROVIDER_RETRY_HTTP',e.code,'DELAY',delay,flush=True);time.sleep(delay)
  except (TimeoutError,urllib.error.URLError) as e:
   if attempt==4:raise RuntimeError('DEEPL_NETWORK_'+type(e).__name__) from None
   time.sleep(min(30,5*(attempt+1)))
def contract(source,target):
 a=E.fromstring(source);b=E.fromstring(target)
 if a.tag!='p' or b.tag!='p':raise ValueError('PARAGRAPH_XML_ROOT_CHANGED')
 from collections import Counter
 source_tags=Counter(x.tag for x in a.iter());target_tags=Counter(x.tag for x in b.iter());findings=[]
 if source_tags!=target_tags:findings.append(dict(kind='SOURCE_INLINE_MARKUP_REPAIR_REQUIRED',source_tags=dict(source_tags),target_tags=dict(target_tags),state='UNRESOLVED_BLOCKS_FINAL_RELEASE',reason='Provider may omit or fragment emphasis; complete source-aligned AI review must repair or evidence semantic preservation. Raw response retained; not an inline PASS.'))
 if [(x.get('n'),x.text) for x in a.iter('bbnote')]!=[(x.get('n'),x.text) for x in b.iter('bbnote')]:raise ValueError('SOURCE_NOTE_REFERENCES_CHANGED')
 def plaintext(n):
  value=n.text or ''
  for child in n:
   value+=('\n' if child.tag=='br' else plaintext(child))+(child.tail or '')
  return value
 text=plaintext(b).strip()
 if not text:raise ValueError('EMPTY_TARGET')
 return text,findings
save()
try:
 usage=call('/v2/usage');remaining=sum(len(r['source']) for r in data['rows'] if not r.get('target_xml'))
 if usage.get('character_limit',0)>0 and usage.get('character_count',0)+remaining>usage['character_limit']:raise RuntimeError('INSUFFICIENT_REMAINING_APPROVED_QUOTA')
 data['usage_before']={k:usage.get(k) for k in ['character_count','character_limit']}
 for start in range(0,len(data['rows']),8):
  batch=data['rows'][start:start+8];pending=[r for r in batch if not r.get('target_xml')]
  if not pending:continue
  context='Jerome K. Jerome, Three Men in a Boat (1889). First-person British comic narrative with dry irony and lyrical river descriptions. Chapter: '+unit['source_heading']+'. Terminology: George — Джордж; Harris — Гаррис; Montmorency — Монморанси; Thames — Темза; Mrs. Poppets — миссис Поппетс. Surrounding original source, for context only:\n'+'\n\n'.join(r['source'] for r in unit['rows'][max(0,start-3):min(len(unit['rows']),start+11)])
  payload=dict(text=[r['source_xml'] for r in pending],source_lang='EN',target_lang='RU',model_type='quality_optimized',context=context,tag_handling='xml',tag_handling_version='v2',split_sentences='nonewlines',ignore_tags=['bbnote'],non_splitting_tags=['em','bbnote'])
  serialized=json.dumps(payload,ensure_ascii=False);assert len(serialized.encode())<128*1024
  cached=next((b for b in reversed(data['batches']) if b.get('ids')==[r['id'] for r in pending] and b.get('state')=='RESPONSE_RECEIVED_INLINE_VALIDATION_PENDING' and b.get('request_sha256')==H(serialized)),None)
  answer=cached['response'] if cached else call('/v2/translate',payload);translations=answer.get('translations',[])
  if len(translations)!=len(pending):raise RuntimeError('SOURCE_TARGET_ROW_COUNT_MISMATCH')
  exchange=cached if cached else dict(start=start,ids=[r['id'] for r in pending],request_sha256=H(serialized),context_sha256=H(context),response=answer,state='RESPONSE_RECEIVED_INLINE_VALIDATION_PENDING')
  if cached:exchange['reused_preserved_response_without_retranslation']=True
  else:data['batches'].append(exchange)
  save()
  accepted=[]
  for row,value in zip(pending,translations):
   model=value.get('model_type_used');version=value.get('tag_handling_version');target=value.get('text','').strip()
   if model!='quality_optimized':raise RuntimeError('RETURNED_MODEL_NOT_CONFIRMED_QUALITY_OPTIMIZED')
   text,findings=contract(row['source_xml'],target);accepted.append(dict(inline_markup_findings=findings,inline_markup_state='REPAIR_REQUIRED_BLOCKS_RELEASE' if findings else 'COUNTS_PRESERVED_FULL_SEMANTIC_REVIEW_STILL_REQUIRED',target=text,target_xml=target,target_sha256=H(text),target_xml_sha256=H(target),model_type_used=model,tag_handling_version_used=version,detected_source_language=value.get('detected_source_language')))
  for row,value in zip(pending,accepted):row.update(value)
  exchange.update(state='ALL_PROVIDER_ROWS_PRESERVED_FOR_MANDATORY_AI_REVIEW',source_to_target_count_equal=True,inline_contract='REPAIR_REQUIRED_BLOCKS_RELEASE' if any(r.get('inline_markup_findings') for r in pending) else 'PASS_TAG_COUNTS_NOTE_MARKERS_AND_NONEMPTY_TEXT')
  save();print('TRANSLATED',uid,min(start+8,len(data['rows'])),'OF',len(data['rows']),flush=True)
 assert all(r.get('target_xml') and r.get('model_type_used')=='quality_optimized' for r in data['rows']);data['state']='COMPLETE_TRANSLATION_DRAFT_REQUIRES_COMPLETE_AI_REVIEW';data['fidelity_review']='NOT_RUN';data['literary_review']='NOT_RUN';data['inline_markup_findings']=[dict(id=r['id'],findings=r.get('inline_markup_findings',[])) for r in data['rows'] if r.get('inline_markup_findings')];data['release_ready']=False;save();print('AUTHENTIC_UNIT_COMPLETE_DRAFT',index,uid,len(data['rows']),flush=True)
except Exception as e:
 data['state']='TRANSLATION_UNIT_PENDING_RECOVERY';data['error_type']=type(e).__name__;data['error_code']=str(e) if isinstance(e,(RuntimeError,ValueError)) else 'SANITIZED_PROVIDER_OR_RUNTIME_FAILURE';save();print('UNIT_PENDING_RECOVERY',uid,data['error_type'],data['error_code'],flush=True);raise SystemExit(1)
