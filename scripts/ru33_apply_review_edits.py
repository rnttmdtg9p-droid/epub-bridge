#!/usr/bin/env python3
"""Apply reviewed rank33 edits without destroying Claude translations or bypassing release gates.
Input: 37 chapter original artifact 42104369530, 37 OpenAI first-pass reviews,
and 20 full-paragraph editorial patch records. Post-correction re-review still required.
"""
import argparse, hashlib, json, pathlib
SHA=lambda x:hashlib.sha256(x.encode('utf-8')).hexdigest()
OVERRIDES = {
'c21-p0041':[('велел взять рифы на всех парусах шхуны','велел убрать все паруса шхуны'),('Бушприт убрали','Выдвижной бушприт убрали'),('штормовой кливер из прочной парусины','штормовой парус из прочной парусины'),('вместо трисселя','вместо стакселя')],
'c21-p0072':[('отчаянным движением руки оттолкнул штурвальное колесо','отчаянным движением руки оттолкнул румпель')],
'c25-p0040':[('некоторые индийцы','некоторые индейцы')],
'c27-p0027':[('путешественники снова были на вокзале и заняли свои места в вагонах','путешественники снова оказались на станции и заняли места в вагонах поезда, шедшего обратно в Огден')],
'c29-p0062':[('вволю расстреливать один другого из ружей','вволю стрелять друг в друга из револьверов')],
'c29-p0068':[('Машиниста и кочегара они оглушили ударами томагавков и едва не убили.','Машиниста и кочегара они едва не оглушили ударами дубинок.')],
'c29-p0072':[('тяжело раненных пулями или томагавками','тяжело раненных пулями или ударами дубинок')],
'c30-p0011':[('Он только что произнёс собственный приговор: полный разорение.','Он только что обрёк себя на полное разорение.')]
}
EDITORIAL_HOLDS={
'c24-p0006':'French text itself says Yokohama, whereas preceding narrative is Hong Kong; preserve French fidelity pending explicit editorial erratum decision.',
'c36-p0008':'French original is chronologically inconsistent 9:00 vs 8:25; check an authoritative edition before altering.'
}
def load(f):return json.loads(pathlib.Path(f).read_text(encoding='utf-8'))
def main(a):
 raw, reviews, corrections, out = [pathlib.Path(x) for x in (a.translation_dir,a.reviews_dir,a.editorial_dir,a.output_dir)]
 out.mkdir(parents=True,exist_ok=True)
 assert len(list(raw.glob('chapter_*.json')))==37,'Require exact 37 translated chapters'
 assert len(list(reviews.glob('chapter_*.json')))==37,'Require 37 independent reviews'
 assert len(list(corrections.glob('chapter_*.json')))==37,'Require complete 37 chapter editorial correction records'
 status=load(raw/'status.json')
 assert status['isbn']=='9781774965986' and status['translation']=='COMPLETE'
 assert status['chapters']==37 and status['source_paragraphs']==2117
 assert status['translation_model']=='claude-sonnet-5-5'
 summary={'rank':33,'isbn':'9781774965986','original_artifact_run':42104369530,'first_pass_review_run':42112058296,
   'original_french_sha256':status['source_sha256'],'chapters':0,'source_paragraphs':0,
   'critical_first_pass':0,'editorial_rows_changed':0,'addressed_critical_ids':[],
   'held_critical_ids':EDITORIAL_HOLDS,'unresolved_critical_ids':[],'pending_style_ids':[],
   'translation_source':'Claude initial only','editorial_source':'independent OpenAI reviewer and reviewed targeted replacements',
   'post_correction_independent_review':'NOT_PERFORMED','epubcheck':'NOT_PERFORMED',
   'v3_cover':'NOT_VERIFIED','release':'BLOCKED','status':'CORRECTED_DRAFT__RE_REVIEW_REQUIRED'}
 ledger=[]
 for n in range(1,38):
  d=load(raw/f'chapter_{n:02}.json');r=load(reviews/f'chapter_{n:02}.json');v=r['review'];par=d['paragraphs'];byid={p['id']:p for p in par}
  assert d['chapter']==n and r['chapter']==n and r['independent_from_claude'] is True
  assert r['reviewer']=='openai/gpt-6-sol' and v['checked_paragraphs']==len(par)
  assert set(byid)=={f'c{n:02}-p{i:04}' for i in range(1,len(par)+1)}
  summary['chapters']+=1;summary['source_paragraphs']+=len(par)
  summary['critical_first_pass']+=len(v['critical_issues'])
  for p in par:
   assert SHA(p['source'])==p['source_sha256'],('SOURCE_HASH_CHANGED',p['id'])
   assert SHA(p['target'])==p['target_sha256'],('TARGET_HASH_CHANGED',p['id'])
   assert p['model']=='claude-sonnet-5-5'
  if n<=37:
   e=load(corrections/f'chapter_{n:02}.json')
   assert e['validation']=='NEEDS_INDEPENDENT_POST_CORRECTION_REVIEW' and e['editor_model'].startswith('openai/')
   for c in e['corrections']['corrected']:
    p=byid[c['id']];before=p['target'];after=c['target']
    assert after.strip() and after!=before,(c['id'],'INVALID_EDIT')
    p['target']=after;p['target_sha256']=SHA(after);p['editorial_status']='PENDING_INDEPENDENT_RE_REVIEW'
    ledger.append({'id':c['id'],'original_sha256':SHA(before),'new_sha256':SHA(after),'basis':'curated_full_paragraph_openai','post_review':'PENDING'})
   edited_ids={c['id'] for c in e['corrections']['corrected']}
   summary['addressed_critical_ids'].extend(q['id'] for q in v['critical_issues'] if q['id'] in edited_ids and q['id'] not in EDITORIAL_HOLDS)
   summary['unresolved_critical_ids'].extend(q['id'] for q in v['critical_issues'] if q['id'] not in edited_ids and q['id'] not in EDITORIAL_HOLDS)
   summary['pending_style_ids'].extend(q['id'] for q in v['stylistic_issues'] if q['id'] not in edited_ids)
  else:
   for q in v['critical_issues']:
    rid=q['id'];p=byid[rid];before=p['target']
    if rid in EDITORIAL_HOLDS:continue
    if rid in OVERRIDES:
     after=before
     for old,new in OVERRIDES[rid]:
      assert after.count(old)==1,('INVALID_CONTEXT',rid,old)
      after=after.replace(old,new,1)
    else:
     old=q.get('russian_quote','');new=q.get('suggested_russian','')
     if not old or '...' in old or '…' in old or before.count(old)!=1:
      summary['unresolved_critical_ids'].append(rid);continue
     after=before.replace(old,new,1)
    if after==before:summary['unresolved_critical_ids'].append(rid);continue
    p['target']=after;p['target_sha256']=SHA(after);p['editorial_status']='PENDING_INDEPENDENT_RE_REVIEW'
    summary['addressed_critical_ids'].append(rid)
    ledger.append({'id':rid,'original_sha256':SHA(before),'new_sha256':SHA(after),'basis':'targeted_independent_review_critical_fix','post_review':'PENDING'})
   summary['pending_style_ids'].extend(q['id'] for q in v['stylistic_issues'])
  for p in par:assert SHA(p['target'])==p['target_sha256'],('CORRECTED_HASH_INVALID',p['id'])
  (out/f'chapter_{n:02}.json').write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 assert summary['chapters']==37 and summary['source_paragraphs']==2117
 assert summary['critical_first_pass']==78
 summary['editorial_rows_changed']=len(ledger)
 (out/'editorial_change_ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 (out/'editorial_checkpoint_status.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print('RANK33_DRAFT_PATCH_RESULT',json.dumps({'chapters':37,'paragraphs':2117,'edited_rows':len(ledger),
       'initial_critical_issues':78,'critical_hold_count':len(EDITORIAL_HOLDS),
       'unresolved_critical':summary['unresolved_critical_ids'],
       'post_correction_review':'PENDING','release':'BLOCKED'},ensure_ascii=False))
if __name__=='__main__':
 ap=argparse.ArgumentParser()
 for opt in ('translation-dir','reviews-dir','editorial-dir','output-dir'):ap.add_argument('--'+opt,required=True)
 main(ap.parse_args())
