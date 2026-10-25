from pathlib import Path
import json,html,os,urllib.request,urllib.error,hashlib,sys,time,datetime,xml.etree.ElementTree as E
H=lambda s:hashlib.sha256(s.encode()).hexdigest();index=int(sys.argv[1]);root=Path(__file__).resolve().parents[2];inputs=root/'bb-ru75-20261025/source';out=root/'translation/ru75';out.mkdir(parents=True,exist_ok=True);src=json.load(open(inputs/f'{index:02}.json'));unit=src['unit'];assert H(json.dumps(unit,ensure_ascii=False,sort_keys=True,separators=(',',':')))==src['unit_source_sha256'];assert all(H(r['source'])==r['source_sha256'] and H(r['source_xml'])==r['source_xml_sha256'] for r in unit['rows']);uid=unit['id'];path=out/f'{index:02}-{uid}.json';key=os.environ.get('DEEPL_AUTH_KEY','').strip()
if not key:raise SystemExit('MISSING_APPROVED_DEEPL_CONNECTION')
host='https://api-free.deepl.com' if key.endswith(':fx') else 'https://api.deepl.com'
if index>0:
 prior=list(out.glob(f'{index-1:02}-*.json'))
 if len(prior)!=1 or json.load(open(prior[0])).get('state')!='COMPLETE_TRANSLATION_DRAFT_REQUIRES_COMPLETE_AI_REVIEW':raise SystemExit('PREVIOUS_AUTHENTIC_UNIT_NOT_COMPLETED')
if path.exists():
 data=json.load(open(path));assert data['unit_source_sha256']==src['unit_source_sha256'];assert len(data['rows'])==len(unit['rows']);assert [r['id'] for r in data['rows']]==[r['id'] for r in unit['rows']]
else:data=dict(rank=75,unit=uid,source_title='Cinq Semaines en ballon',source_edition='J.Hetzel undated illustrated scan; catalogued [1867?] by Robarts; Wikisource transclusion 1-259',source_document_sha256=src['source_document_sha256'],unit_source_sha256=src['unit_source_sha256'],engine='DeepL',requested_model='quality_optimized',requested_tag_handling_version='v2',state='TRANSLATION_IN_PROGRESS',rows=[dict(r) for r in unit['rows']],batches=[])
def save():
 data['updated_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat();tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2));tmp.replace(path)
def call(endpoint,payload=None):
 req=urllib.request.Request(host+endpoint,data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None,headers={'Authorization':'DeepL-Auth-Key '+key,'Content-Type':'application/json','X-DeepL-Reporting-Tag':'boundary-bay-ru75'})
 cachekey=H(json.dumps(payload,ensure_ascii=False)) if payload is not None else None
 if endpoint=='/v2/translate':
  cached=next((x for x in data.get('provider_exchange_cache',[]) if x['request_sha256']==cachekey),None)
  if cached:return cached['response']
 for attempt in range(5):
  try:
   with urllib.request.urlopen(req,timeout=120) as response:answer=json.load(response)
   if endpoint=='/v2/translate':
    data.setdefault('provider_exchange_cache',[]).append(dict(request_sha256=cachekey,response=answer));save()
   return answer
  except urllib.error.HTTPError as e:
   body=e.read(6000).decode(errors='replace').replace(key,'[REDACTED]');data.setdefault('provider_errors',[]).append(dict(endpoint=endpoint,http_status=e.code,message=body,request_sha256=cachekey));save()
   if e.code not in [429,500,502,503,504,529] or attempt==4:raise RuntimeError('DEEPL_HTTP_'+str(e.code)) from None
   delay=min(30,max(5,int(e.headers.get('Retry-After','5')) if str(e.headers.get('Retry-After','5')).isdigit() else 5)*(attempt+1));print('PROVIDER_RETRY_HTTP',e.code,'DELAY',delay,flush=True);time.sleep(delay)
  except (TimeoutError,urllib.error.URLError) as e:
   if attempt==4:raise RuntimeError('DEEPL_NETWORK_'+type(e).__name__) from None
   time.sleep(min(30,5*(attempt+1)))
def translate(payload):
 try:return call('/v2/translate',payload)
 except RuntimeError as e:
  if str(e)!='DEEPL_HTTP_400' or len(payload['text'])<=1:raise
  size=len(payload['text']);middle=size//2;data.setdefault('request_recovery',[]).append(dict(reason='HTTP400_SPLIT_REJECTED_BATCH_WITH_SAME_MODEL_CONTEXT_AND_MARKUP_PARAMETERS',texts=size,parts=[middle,size-middle]));save();left={**payload,'text':payload['text'][:middle]};right={**payload,'text':payload['text'][middle:]};a=translate(left);b=translate(right);return dict(translations=a['translations']+b['translations'],batch_recovery='COMBINED_ORDERED_SUBREQUESTS_ALL_RAW_RESPONSES_SAVED')
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
def protected_xml(source):
 root=E.fromstring(source)
 for n in root.iter('bbnote'):n.text='BBNOTE_REF_'+n.get('n').zfill(4)+'_Z'
 return E.tostring(root,encoding='unicode')
def restore_note_identity(source,raw):
 import re
 expected=[(n.get('n'),n.text) for n in E.fromstring(source).iter('bbnote')]
 if not expected:return raw,[]
 before=H(raw);root=E.fromstring(raw);repairs=[]
 opaque=re.findall(r'BBNOTE_REF_(\d{4})_Z',raw)
 if opaque:
  if [str(int(n)) for n in opaque]!=[n for n,t in expected]:raise ValueError('OPAQUE_NOTE_IDENTITY_SEQUENCE_CHANGED')
  # v2 may fragment an ignored wrapper around translated lexical text. Strip
  # only that wrapper, preserving every text character and all other tags;
  # the unique unchanged opaque tokens still fix each callout's position.
  raw=re.sub(r'</?bbnote\b[^>]*>','',raw)
  for n,t in expected:raw=raw.replace('BBNOTE_REF_'+n.zfill(4)+'_Z',f'<bbnote n="{n}">{n}</bbnote>',1)
  root=E.fromstring(raw)
  if [(n.get('n'),n.text) for n in root.iter('bbnote')]!=expected:raise ValueError('RESTORED_OPAQUE_NOTE_IDENTITY_MISMATCH')
  repairs.append(dict(operations=['UNWRAP_FRAGMENTED_PROVIDER_NOTE_TAGS_WITH_ALL_LEXICAL_TEXT_UNCHANGED','RESTORE_CANONICAL_CALLOUT_AT_EVERY_UNIQUE_OPAQUE_TOKEN'],provider_raw_target_sha256=before,canonical_target_xml_sha256=H(raw),scope='NOTE_LINK_IDENTITIES_ONLY_NO_TRANSLATED_WORD_CHANGES_NO_LITERARY_PASS'))
  return raw,repairs
 for n in root.iter('bbnote'):
  value='BBNOTE_REF_'+str(n.get('n')).zfill(4)+'_Z'
  if n.text==value:n.text=n.get('n');repairs.append('OPAQUE_TOKEN_INSIDE_NOTE_TAG_TO_CANONICAL_MARKER')
 xml=E.tostring(root,encoding='unicode')
 tokens=re.findall(r'BBNOTE_REF_(\d{4})_Z',xml)
 for token in tokens:
  n=str(int(token))
  if (n,n) not in expected:raise ValueError('UNKNOWN_RETURNED_NOTE_TOKEN')
  xml=xml.replace('BBNOTE_REF_'+token+'_Z',f'<bbnote n="{n}">{n}</bbnote>',1);repairs.append('OPAQUE_TOKEN_TO_CANONICAL_NOTE_AT_UNCHANGED_TARGET_POSITION')
 if not list(E.fromstring(xml).iter('bbnote')):
  # Recover the already preserved response only when every returned numeric
  # superscript matches the complete ordered source-reference identities and
  # no source numeric superscript could make that identity ambiguous.
  numeric_sup=[n for n in E.fromstring(source).iter('sup') if ''.join(n.itertext()).isdigit()]
  digits='⁰¹²³⁴⁵⁶⁷⁸⁹';trans=str.maketrans(digits,'0123456789');found=re.findall('['+digits+']+',xml)
  if not numeric_sup and [x.translate(trans) for x in found]==[n for n,t in expected]:
   def replace(m):
    n=m.group(0).translate(trans);return f'<bbnote n="{n}">{n}</bbnote>'
   xml=re.sub('['+digits+']+',replace,xml);repairs.append('COMPLETE_ORDERED_UNAMBIGUOUS_SUPERSCRIPT_IDENTITIES_RESTORED_FROM_PRESERVED_RESPONSE')
 if [(n.get('n'),n.text) for n in E.fromstring(xml).iter('bbnote')]!=expected:raise ValueError('SOURCE_NOTE_REFERENCES_CHANGED')
 if repairs:repairs=[dict(operations=repairs,provider_raw_target_sha256=before,canonical_target_xml_sha256=H(xml),scope='NOTE_LINK_IDENTITIES_ONLY_NO_TRANSLATED_WORD_CHANGES_NO_LITERARY_PASS')]
 return xml,repairs
save()
try:
 usage=call('/v2/usage');remaining=sum(len(r['source']) for r in data['rows'] if not r.get('target_xml'))
 if usage.get('character_limit',0)>0 and usage.get('character_count',0)+remaining>usage['character_limit']:raise RuntimeError('INSUFFICIENT_REMAINING_APPROVED_QUOTA')
 data['usage_before']={k:usage.get(k) for k in ['character_count','character_limit']}
 for start in range(0,len(data['rows']),8):
  batch=data['rows'][start:start+8];pending=[r for r in batch if not r.get('target_xml')]
  if not pending:continue
  context='Jules Verne, Cinq Semaines en ballon (first published 1863). French scientific adventure novel; NEW complete Russian translation from this frozen French original only. Keep all source details, dates, historical geography, imperial and metric units, formulas, numbers, source captions, dialogue, irony, and the narrator voice. Do not borrow or reproduce any existing Russian translation. Terminology: Samuel Fergusson — Сэмюэль Фергюсон; Dick Kennedy — Дик Кеннеди; Joe — Джо; Victoria (balloon) — Виктория; Resolute — Резолют; Zanzibar — Занзибар; Tchad — Чад. Chapter: '+unit['source_heading']+'. Surrounding original source for context only:\n'+'\n\n'.join(r['source'] for r in unit['rows'][max(0,start-3):min(len(unit['rows']),start+11)])
  payload=dict(text=[r['source_xml'] for r in pending],source_lang='FR',target_lang='RU',model_type='quality_optimized',context=html.escape(context,quote=False),tag_handling='xml',tag_handling_version='v2',split_sentences='nonewlines',ignore_tags=['bbnote'],non_splitting_tags=['em','strong','sup','sub','small','bbnote'])
  legacy_serialized=json.dumps(payload,ensure_ascii=False)
  cached=next((b for b in reversed(data['batches']) if b.get('ids')==[r['id'] for r in pending] and b.get('state')=='RESPONSE_RECEIVED_INLINE_VALIDATION_PENDING' and b.get('request_sha256')==H(legacy_serialized)),None)
  if not cached:payload['text']=[protected_xml(r['source_xml']) for r in pending]
  serialized=json.dumps(payload,ensure_ascii=False);assert len(serialized.encode())<128*1024
  answer=cached['response'] if cached else translate(payload);translations=answer.get('translations',[])
  if len(translations)!=len(pending):raise RuntimeError('SOURCE_TARGET_ROW_COUNT_MISMATCH')
  exchange=cached if cached else dict(start=start,ids=[r['id'] for r in pending],request_sha256=H(serialized),context_sha256=H(payload['context']),plain_context_sha256=H(context),context_xml_escaped=True,response=answer,state='RESPONSE_RECEIVED_INLINE_VALIDATION_PENDING')
  if cached:exchange['reused_preserved_response_without_retranslation']=True
  else:data['batches'].append(exchange)
  save()
  accepted=[]
  for row,value in zip(pending,translations):
   model=value.get('model_type_used');version=value.get('tag_handling_version');target=value.get('text','').strip()
   if model!='quality_optimized':raise RuntimeError('RETURNED_MODEL_NOT_CONFIRMED_QUALITY_OPTIMIZED')
   target,marker_repairs=restore_note_identity(row['source_xml'],target)
   text,findings=contract(row['source_xml'],target);accepted.append(dict(note_marker_transport_repairs=marker_repairs,inline_markup_findings=findings,inline_markup_state='REPAIR_REQUIRED_BLOCKS_RELEASE' if findings else 'COUNTS_PRESERVED_FULL_SEMANTIC_REVIEW_STILL_REQUIRED',target=text,target_xml=target,target_sha256=H(text),target_xml_sha256=H(target),model_type_used=model,tag_handling_version_used=version,detected_source_language=value.get('detected_source_language')))
  for row,value in zip(pending,accepted):row.update(value)
  exchange.update(state='ALL_PROVIDER_ROWS_PRESERVED_FOR_MANDATORY_AI_REVIEW',source_to_target_count_equal=True,inline_contract='REPAIR_REQUIRED_BLOCKS_RELEASE' if any(r.get('inline_markup_findings') for r in pending) else 'PASS_TAG_COUNTS_NOTE_MARKERS_AND_NONEMPTY_TEXT')
  save();print('TRANSLATED',uid,min(start+8,len(data['rows'])),'OF',len(data['rows']),flush=True)
 if data.get('error_code'):
  data.setdefault('prior_interruption_dispositions',[]).append(dict(error_code=data.pop('error_code'),error_type=data.pop('error_type',None),disposition='TRANSPORT_RESUMED_DRAFT_STILL_REQUIRES_COMPLETE_AI_REVIEW_AND_ALL_MARKUP_FINDING_CLOSURES'));
 assert all(r.get('target_xml') and r.get('model_type_used')=='quality_optimized' for r in data['rows']);data['state']='COMPLETE_TRANSLATION_DRAFT_REQUIRES_COMPLETE_AI_REVIEW';data['fidelity_review']='NOT_RUN';data['literary_review']='NOT_RUN';data['inline_markup_findings']=[dict(id=r['id'],findings=r.get('inline_markup_findings',[])) for r in data['rows'] if r.get('inline_markup_findings')];data['release_ready']=False;save();print('AUTHENTIC_UNIT_COMPLETE_DRAFT',index,uid,len(data['rows']),flush=True)
except Exception as e:
 data['state']='TRANSLATION_UNIT_PENDING_RECOVERY';data['error_type']=type(e).__name__;data['error_code']=str(e) if isinstance(e,(RuntimeError,ValueError)) else 'SANITIZED_PROVIDER_OR_RUNTIME_FAILURE';save();print('UNIT_PENDING_RECOVERY',uid,data['error_type'],data['error_code'],flush=True);raise SystemExit(1)

