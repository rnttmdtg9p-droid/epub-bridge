"""Exact-byte rank71 release guard. New AI translation always requires complete review."""
from pathlib import Path
import hashlib
EXPECTED=['source_integrity','content_purity','translation_and_literary_review','structure_and_navigation','toc_consistency','heading_recognition_and_label_integrity','heading_candidate_accounting','heading_container_integrity','spatial_layout_integrity','resources_and_typography','assets_and_rights','bb_technical_preflight','epubcheck','rendered_layout','reader_navigation','delivery_verification','chapter_opening_matrix','rendered_illustration_crop_matrix','publication_wide_notes','cover_and_discovery','author_profile_and_portrait','source_artifact_zero_residual','letter_initial_placement','illustration_diversity']
MASTER='c5e27e97c2b6a4a2cd6cac37c3b41698b2078e1321dc071d5505ce7abd48ca6f'
SOURCE='ce89228f73983326dc0a3a4c368caa4002741ae7dea168ba114829615493e1f3'
def file_sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for block in iter(lambda:f.read(8388608),b''):h.update(block)
 return h.hexdigest()
def join(records,epub_sha256,master_sha256,phase='candidate',delivery=None):
 assert phase in ['candidate','release']
 errors=[];seen={}
 if master_sha256!=MASTER:errors.append('WRONG_APPROVED_MASTER')
 for row in records:seen.setdefault(row.get('gate'),[]).append(row)
 if set(seen)!=set(EXPECTED):errors.append('INCOMPLETE_OR_UNEXPECTED_GATE_SET')
 for name in EXPECTED:
  rows=seen.get(name,[])
  if len(rows)!=1:errors.append('MISSING_OR_CONFLICTING_GATE:'+name);continue
  row=rows[0]
  if row.get('rank')!=71 or row.get('epub_sha256')!=epub_sha256 or row.get('master_sha256')!=MASTER:errors.append('STALE_OR_WRONG_IDENTITY:'+name)
  if not row.get('complete') or row.get('errors'):errors.append('INCOMPLETE:'+name)
  dependencies=row.get('dependencies',{})
  if not dependencies:errors.append('MISSING_EVIDENCE:'+name)
  for filename,digest in dependencies.items():
   p=Path(filename)
   if not p.is_file() or file_sha(p)!=digest:errors.append('MISSING_OR_CHANGED_EVIDENCE:'+name)
  state=row.get('state');reason=row.get('reason')
  if name=='reader_navigation' and state=='NOT_APPLICABLE' and reason=='TEMPORARILY_DEFERRED_BY_MASTER_V5_0_66':pass
  elif name=='delivery_verification' and phase=='candidate' and state=='NOT_RUN_RELEASE_NOT_YET_SAVED':pass
  elif state!='PASS':errors.append('NON_PASS:'+name)
  if name=='translation_and_literary_review':
   if state!='PASS' or row.get('source_document_sha256')!=SOURCE or row.get('complete_reviewed_rows')!=1417:errors.append('COMPLETE_NEW_AI_TRANSLATION_REVIEW_REQUIRED')
 if phase=='release':
  if not delivery or delivery.get('epub_sha256')!=epub_sha256 or not delivery.get('all_four_native_files_verified') or not delivery.get('every_evidence_payload_verified') or not delivery.get('four_links_actually_delivered') or len(delivery.get('artifacts',[]))!=4:errors.append('FINAL_FOUR_NATIVE_AND_ACTUAL_LINK_CLOSURE_REQUIRED')
 return ('REVIEW_REQUIRED' if errors else 'CONTENT_QUALIFIED' if phase=='candidate' else 'RELEASE_READY'),errors
