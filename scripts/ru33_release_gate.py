#!/usr/bin/env python3
"""Fail-closed rank 33 paragraph + independent-review gate. No EPUB publication."""
import argparse, hashlib, json
from pathlib import Path

def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def chapter_hash(paragraphs):
    return sha(json.dumps([(p["id"],p["target"]) for p in paragraphs],ensure_ascii=False,separators=(",",":")))

def validate(folder):
    root=Path(folder);issues=[];complete=0;reviewed=0;total=0;hashes={}
    for n in range(1,38):
        cp=root/f"chapter_{n:02}.json"
        rp=root/f"review_{n:02}.json"
        if not cp.exists():
            issues.append(f"CHAPTER_{n:02}_MISSING");continue
        try:
            data=json.loads(cp.read_text(encoding="utf-8"))
            pp=data["paragraphs"]
        except (KeyError,ValueError) as e:
            issues.append(f"CHAPTER_{n:02}_INVALID:{e}");continue
        if data.get("chapter")!=n or len(pp)<10:
            issues.append(f"CHAPTER_{n:02}_STRUCTURAL_MISMATCH");continue
        good=True
        for i,p in enumerate(pp,1):
            if p.get("id")!=f"c{n:02}-p{i:04}" or not p.get("source") or p.get("source_sha256")!=sha(p["source"]):
                issues.append(f"CHAPTER_{n:02}_SOURCE_ALIGNMENT:{i}");good=False;continue
            target=p.get("target","")
            if not isinstance(target,str) or len(target.strip())<2 or p.get("target_sha256")!=sha(target) or "claude" not in str(p.get("model","")).lower():
                issues.append(f"CHAPTER_{n:02}_TARGET_INVALID:{i}");good=False
        total+=len(pp)
        if not good:continue
        complete+=1
        h=chapter_hash(pp);hashes[str(n)]=h
        if not rp.exists():
            issues.append(f"CHAPTER_{n:02}_INDEPENDENT_REVIEW_MISSING");continue
        try: review=json.loads(rp.read_text(encoding="utf-8"))
        except ValueError:issues.append(f"CHAPTER_{n:02}_REVIEW_INVALID_JSON");continue
        if review.get("pass") is not True or review.get("critical_issues"):
            issues.append(f"CHAPTER_{n:02}_REVIEW_ISSUES");continue
        if review.get("reviewed_translation_sha256")!=h:
            issues.append(f"CHAPTER_{n:02}_REVIEW_HASH_NOT_BOUND_TO_TEXT");continue
        if not review.get("review_engine") or "claude" in str(review["review_engine"]).lower():
            issues.append(f"CHAPTER_{n:02}_REVIEW_PROVENANCE_UNVERIFIED");continue
        reviewed+=1
    return dict(rank=33,isbn="9781774965986",chapters_translated=complete,chapters_approved=reviewed,paragraphs=total,target_hashes=hashes,errors=issues,
                status="TEXT_REVIEW_GATE_PASS" if complete==reviewed==37 and not issues else "BLOCKED",
                release="BLOCKED_PENDING_SCENE_APPROVAL_ASSETS_RIGHTS_OFFICIAL_EPUBCHECK_RENDER")
if __name__=="__main__":
    a=argparse.ArgumentParser();a.add_argument("translation_dir");a.add_argument("--out",required=True);args=a.parse_args()
    result=validate(args.translation_dir)
    Path(args.out).write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(result["status"],result["chapters_translated"],result["chapters_approved"],len(result["errors"]))
    if result["status"]!="TEXT_REVIEW_GATE_PASS":raise SystemExit(2)
