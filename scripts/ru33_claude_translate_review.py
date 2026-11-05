#!/usr/bin/env python3
"""Rank 33 draft translation; Claude only for initial French->Russian, OpenAI only for independent literary review.
The job NEVER releases or publishes an EPUB."""
import os, re, json, hashlib, time, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone

ROOT=Path("results/ru33-french-claude-20261105")
ROOT.mkdir(parents=True,exist_ok=True)
ORIGINAL="https://www.gutenberg.org/cache/epub/46541/pg46541.txt"
MODEL=os.getenv("ANTHROPIC_MODEL","claude-sonnet-5-5")
KEY=os.getenv("ANTHROPIC_API_KEY","").strip()
REVIEW_KEY=os.getenv("OPENAI_API_KEY","").strip()
def save(p,obj): (ROOT/p).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def digest(s):return hashlib.sha256(s.encode("utf-8")).hexdigest()
def fetch_source():
    req=urllib.request.Request(ORIGINAL,headers={"User-Agent":"BoundaryBayClassics-Editorial/1.0 (source verification)"})
    with urllib.request.urlopen(req,timeout=90) as r: raw=r.read()
    src=raw.decode("utf-8-sig").replace("\r\n","\n")
    if "*** START OF THE PROJECT GUTENBERG EBOOK" not in src or "*** END OF THE PROJECT GUTENBERG EBOOK" not in src: raise RuntimeError("SOURCE_MARKERS_MISSING")
    return src
def roman(n):
    v=[(100,"C"),(90,"XC"),(50,"L"),(40,"XL"),(10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I")]
    s=""
    for a,b in v:
        while n>=a:s+=b;n-=a
    return s
def load_chapters(src):
    body=src.split("*** START OF THE PROJECT GUTENBERG EBOOK",1)[1].split("*** END OF THE PROJECT GUTENBERG EBOOK",1)[0]
    marks=list(re.finditer(r"(?m)^[ \t]*([IVXLCDM]+)[ \t]*$",body))
    chosen=[]
    last=-1
    for i in range(1,38):
        candidate=next((m for m in marks if m.start()>last and m.group(1)==roman(i)),None)
        if candidate is None: raise RuntimeError(f"CHAPTER_{i}_MISSING")
        chosen.append(candidate);last=candidate.start()
    chapters=[]
    for i,m in enumerate(chosen):
        end=chosen[i+1].start() if i+1<len(chosen) else len(body)
        chunk=body[m.end():end].strip()
        segments=re.split(r"\n[ \t]*\n+",chunk)
        blocks=[]
        for seg in segments:
            clean=" ".join(line.strip() for line in seg.splitlines() if line.strip())
            clean=re.sub(r"\s+"," ",clean).strip()
            if clean:blocks.append(clean)
        if len(blocks)<10:raise RuntimeError(f"CHAPTER_{i+1}_TOO_SHORT")
        chapters.append({"chapter":i+1,"roman":roman(i+1),"paragraphs":[{"id":f"c{i+1:02d}-p{j+1:04d}","source":v,"source_sha256":digest(v)} for j,v in enumerate(blocks)]})
    if len(chapters)!=37: raise RuntimeError("CHAPTER_COUNT")
    return chapters
def request_json(url,headers,payload,timeout=120):
    for attempt in range(6):
        req=urllib.request.Request(url,data=json.dumps(payload,ensure_ascii=False).encode("utf-8"),headers=headers,method="POST")
        try:
            with urllib.request.urlopen(req,timeout=timeout) as r:return json.load(r)
        except urllib.error.HTTPError as e:
            body=e.read(1000).decode(errors="replace")
            if e.code in [429,500,502,503,529] and attempt<5:
                time.sleep(min(60,5*(attempt+1)**2));continue
            raise RuntimeError(f"API_HTTP_{e.code}: {body[:200]}")
        except (TimeoutError,urllib.error.URLError):
            if attempt==5:raise
            time.sleep(5*(attempt+1))
    raise RuntimeError("API_RETRY_EXHAUSTED")
def claude_translate(items):
    if not KEY:raise RuntimeError("MISSING_ANTHROPIC_API_KEY")
    payload={"model":MODEL,"max_tokens":8192,
        "system":"You are an expert literary translator. Translate Jules Verne's authentic 1873 French prose into fluent, elegant literary Russian. Maintain meaning and historical texture; avoid omissions, additions, censorship, simplification or modern retelling. Keep character names consistent: Phileas Fogg = Филеас Фогг, Passepartout = Паспарту, Fix = Фикс, Aouda = Ауда. Output ONLY a JSON object with a field translations: an array of {id, target} in EXACTLY the input order, preserving all paragraph IDs.",
        "messages":[{"role":"user","content":"Direct translation from French original. Each entry is a complete source paragraph. Return one complete Russian paragraph for each source entry and preserve headings as headings.\n"+json.dumps(items,ensure_ascii=False)}],
        "tools":[{"name":"emit_translations","description":"Submit the complete, accurately aligned Russian literary translation for every paragraph ID.",
                  "input_schema":{"type":"object","properties":{"translations":{"type":"array","items":{"type":"object","properties":{"id":{"type":"string"},"target":{"type":"string"}},"required":["id","target"]}}},"required":["translations"]}}],
        "tool_choice":{"type":"tool","name":"emit_translations"}}
    d=request_json("https://api.anthropic.com/v1/messages",{"x-api-key":KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},payload)
    tool_outputs=[p.get("input",{}) for p in d.get("content",[]) if p.get("type")=="tool_use" and p.get("name")=="emit_translations"]
    if not tool_outputs:
        response="".join(p.get("text","") for p in d.get("content",[]) if p.get("type")=="text").strip()
        try: obj=json.loads(response)
        except json.JSONDecodeError as exc: raise RuntimeError("CLAUDE_NO_STRUCTURED_TOOL_OUTPUT") from exc
    else:
        obj=tool_outputs[0]
    if not isinstance(obj,dict): raise RuntimeError("CLAUDE_TOOL_INPUT_INVALID")
    rows=obj.get("translations",[])
    if len(rows)!=len(items) or [r.get("id") for r in rows]!=[r["id"] for r in items]:
        raise RuntimeError(f"CLAUDE_ALIGNMENT_FAILURE:{len(rows)}/{len(items)}")
    if any(not isinstance(r.get("target"),str) or len(r["target"].strip())<2 for r in rows):raise RuntimeError("EMPTY_RUSSIAN_PARAGRAPH")
    return rows,d.get("id","")
def batches(paras):
    groups=[];current=[];size=0
    for p in paras:
        n=len(p["source"])
        if current and (size+n>3700 or len(current)>=7):
            groups.append(current);current=[];size=0
        current.append({"id":p["id"],"source":p["source"]});size+=n
    if current:groups.append(current)
    return groups
def translate(chapters):
    for ch in chapters:
        path=ROOT/f"chapter_{ch['chapter']:02d}.json"
        if path.exists():
            saved=json.loads(path.read_text(encoding="utf-8"))
            if len(saved["paragraphs"])==len(ch["paragraphs"]) and all(x["id"]==y["id"] and x["source_sha256"]==y["source_sha256"] and (not x.get("target") or x.get("target_sha256")==digest(x["target"])) for x,y in zip(saved["paragraphs"],ch["paragraphs"])):
                ch["paragraphs"]=saved["paragraphs"]
            else:
                raise RuntimeError("CHECKPOINT_LINEAGE_OR_TARGET_HASH_MISMATCH_"+str(ch["chapter"]))
        pending=[p for p in ch["paragraphs"] if not p.get("target")]
        if not pending:continue
        for g in batches(ch["paragraphs"]):
            todo=[p for p in g if not next(x for x in ch["paragraphs"] if x["id"]==p["id"]).get("target")]
            if not todo:continue
            def translate_robust(rows):
                last_error=None
                for attempt in range(2):
                    try:
                        return claude_translate(rows)
                    except Exception as exc:
                        last_error=exc
                        print("RETRY_TRANSLATION_ALIGNMENT",ch["chapter"],len(rows),str(exc)[:150],flush=True)
                        time.sleep(3)
                if len(rows)>1:
                    middle=len(rows)//2
                    a,req_a=translate_robust(rows[:middle])
                    b,req_b=translate_robust(rows[middle:])
                    return a+b, req_a+";"+req_b
                raise last_error
            translated,request_id=translate_robust(todo)
            byid={p["id"]:p for p in ch["paragraphs"]}
            for row in translated:
                p=byid[row["id"]];p["target"]=row["target"].strip();p["target_sha256"]=digest(p["target"]);p["model"]=MODEL;p["request_id"]=request_id
            save(path.name,ch)
        print("TRANSLATED_CHAPTER",ch["chapter"],len(ch["paragraphs"]),flush=True)
def openai_review(ch):
    if not REVIEW_KEY:raise RuntimeError("MISSING_OPENAI_API_KEY_INDEPENDENT_REVIEW_BLOCKED")
    para=ch["paragraphs"]
    src="\n\n".join(f"{p['id']}: {p['source']}" for p in para)
    ru="\n\n".join(f"{p['id']}: {p.get('target','')}" for p in para)
    request={
      "model":"gpt-4.1","temperature":0,"response_format":{"type":"json_object"},
      "messages":[
       {"role":"system","content":"You are an independent Russian literary editor. You did NOT translate this material. Assess a Russian translation of Jules Verne against the original French. Review all supplied paragraphs for fidelity, completeness, accidental omissions, names, 19th-century literary tone, chronology and unnatural Russian. Do not invent issues. Respond JSON object with keys pass (boolean), critical_issues (array with paragraph IDs and details), stylistic_issues (array with paragraph IDs and suggestions), summary (string). Set pass=false whenever there is a material meaning error, omission, or unexplained paragraph mismatch."},
       {"role":"user","content":"French original, chapter "+str(ch["chapter"])+":\n"+src+"\n\nNEW RUSSIAN TRANSLATION:\n"+ru}
      ],"max_tokens":3200
    }
    d=request_json("https://api.openai.com/v1/chat/completions",{"Authorization":"Bearer "+REVIEW_KEY,"Content-Type":"application/json"},request,timeout=180)
    return json.loads(d["choices"][0]["message"]["content"])
def main():
    state={"rank":33,"isbn":"9781774965986","title":"Вокруг света в восемьдесят дней",
           "original":"Le Tour du monde en quatre-vingts jours","source_url":ORIGINAL,
           "translation_engine":"Claude","translation_model":MODEL,"review_engine":"OpenAI (never Claude)",
           "release":"BLOCKED_PENDING_INDEPENDENT_REVIEW_AND_FINAL_EPUB_QA",
           "started_utc":datetime.now(timezone.utc).isoformat()}
    try:
        src=fetch_source()
        state["source_sha256"]=digest(src);state["source_length"]=len(src)
        chs=load_chapters(src)
        state["chapters"]=len(chs);state["source_paragraphs"]=sum(len(ch["paragraphs"]) for ch in chs)
        save("source_manifest.json",{"source_url":ORIGINAL,"sha256":digest(src),"chapters":37,"paragraphs":state["source_paragraphs"]})
        save("status.json",state)
        translate(chs)
        state["translation"]="COMPLETE";save("status.json",state)
        issues=0;passed=0
        for ch in chs:
            p=ROOT/f"review_{ch['chapter']:02d}.json"
            if p.exists(): review=json.loads(p.read_text())
            else:
                review=openai_review(ch);save(p.name,review)
            if review.get("pass") is True and not review.get("critical_issues"):passed+=1
            else:issues+=1
            print("INDEPENDENT_REVIEW",ch["chapter"],"PASS" if review.get("pass") else "ISSUES",flush=True)
        state.update(reviewed_chapters=passed+issues,pass_chapters=passed,issue_chapters=issues,
                     review="COMPLETE_WITH_ISSUES" if issues else "COMPLETE_CLEAR",
                     release="ON_HOLD_FINAL_EPUB_ASSEMBLY_AND_EPUBCHECK_NOT_DONE")
    except Exception as e:
        state.update(error=str(e),release="BLOCKED",state="FAILED_OR_PARTIAL")
        print("RANK33_BLOCKER",str(e),flush=True)
        raise
    finally:
        state["updated_utc"]=datetime.now(timezone.utc).isoformat()
        save("status.json",state)
if __name__=="__main__":main()
