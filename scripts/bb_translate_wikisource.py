#!/usr/bin/env python3
"""Acquire scan-backed original-language Wikisource leaves, translate, and build BB EPUB."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from huggingface_hub import hf_hub_download, snapshot_download

from bb_translate_gutenberg import (
    BB_MASTER,
    MADLAD,
    MADLAD_RUNTIME,
    Unit,
    qa,
    sha,
    split_long_block,
    translate_units,
    write_epub,
)


UA = "BoundaryBayProduction/5.0.13 (Wikisource source authentication)"


def api_json(lang: str, params: dict, retries: int = 4) -> dict:
    params = {**params, "format": "json", "formatversion": "2", "utf8": "1"}
    url = f"https://{lang}.wikisource.org/w/api.php?{urllib.parse.urlencode(params)}"
    error = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.load(response)
        except Exception as exc:
            error = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"MediaWiki API failed: {url}: {error}")


def parse_page(lang: str, title: str, section: str | None = None) -> dict:
    params = {"action": "parse", "page": title, "prop": "text|links|revid|displaytitle|sections"}
    if section is not None:
        params["section"] = section
    data = api_json(lang, params)
    if "error" in data:
        raise RuntimeError(f"Wikisource parse error for {title}: {data['error']}")
    parsed = data["parse"]
    return {
        "title": parsed["title"],
        "revid": parsed.get("revid"),
        "html": parsed.get("text", ""),
        "links": [link["title"] for link in parsed.get("links", []) if link.get("ns") == 0 and link.get("exists", True)],
        "sections": parsed.get("sections", []),
    }


def child_links(page: dict, root: str) -> list[str]:
    prefix = root.replace(" ", "_") + "/"
    results = []
    for title in page["links"]:
        normalized = title.replace(" ", "_")
        if normalized.startswith(prefix) and title not in results:
            results.append(title)
    return results


def leaf_pages(lang: str, root: str, expected: int, max_pages: int = 240) -> tuple[dict, list[dict]]:
    root_page = parse_page(lang, root)
    canonical_root = root_page["title"]
    ordered: list[dict] = []
    visited: set[str] = set()

    def walk(title: str, depth: int) -> None:
        if title in visited or len(visited) >= max_pages:
            return
        visited.add(title)
        page = root_page if title == canonical_root else parse_page(lang, title)
        children = child_links(page, canonical_root)
        if children and depth < 4:
            for child in children:
                walk(child, depth + 1)
        else:
            ordered.append(page)
        time.sleep(0.05)

    start_children = child_links(root_page, canonical_root)
    if start_children:
        for child in start_children:
            walk(child, 1)
    else:
        level_two = [s for s in root_page.get("sections", []) if str(s.get("level")) == "2"]
        if expected > 1 and len(level_two) >= expected:
            for section in level_two[:expected]:
                virtual = parse_page(lang, canonical_root, str(section["index"]))
                virtual["title"] = f'{canonical_root}/{section.get("line", section["index"])}'
                ordered.append(virtual)
        else:
            ordered.append(root_page)
    return root_page, ordered


def clean_blocks(raw_html: str) -> list[str]:
    soup = BeautifulSoup(raw_html, "lxml")
    for selector in [
        "style", "script", "table", "sup.reference", ".ws-noexport", ".noprint",
        ".header", ".subpages", ".mw-editsection", ".printfooter", ".catlinks",
    ]:
        for node in soup.select(selector):
            node.decompose()
    root = soup.select_one(".prp-pages-output") or soup.select_one(".mw-parser-output") or soup
    candidates = root.select("h2, h3, h4, p, blockquote, div.poem")
    blocks: list[str] = []
    seen: set[str] = set()
    for node in candidates:
        if node.name == "p" and node.find_parent("div", class_="poem"):
            continue
        text = node.get_text("\n" if "poem" in (node.get("class") or []) else " ", strip=True)
        text = re.sub(r"[ \t\u00a0]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) < 2 or text in seen:
            continue
        if re.fullmatch(r"[0-9ivxlcdm. –—-]+", text, flags=re.I):
            continue
        seen.add(text)
        blocks.append(text)
    return blocks


def page_units(pages: list[dict]) -> tuple[list[list[Unit]], list[dict]]:
    chapters: list[list[Unit]] = []
    records: list[dict] = []
    for index, page in enumerate(pages, 1):
        blocks = clean_blocks(page["html"])
        chars = sum(len(b) for b in blocks)
        if chars < 180:
            continue
        units = [Unit(f"c{index:03d}-u0000", index, "heading", page["title"].split("/")[-1].replace("_", " "))]
        ordinal = 0
        for block in blocks:
            for piece in split_long_block(block):
                ordinal += 1
                units.append(Unit(f"c{index:03d}-u{ordinal:04d}", index, "paragraph", piece))
        chapters.append(units)
        records.append({"title": page["title"], "revid": page["revid"], "block_count": len(blocks), "character_count": chars})
    # Renumber after filtering tiny apparatus leaves.
    for chapter_index, chapter in enumerate(chapters, 1):
        for unit_index, unit in enumerate(chapter):
            unit.chapter = chapter_index
            unit.unit_id = f"c{chapter_index:03d}-u{unit_index:04d}"
    return chapters, records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", default="dist")
    args = parser.parse_args()
    meta = json.loads(Path(args.config).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    root, leaves = leaf_pages(meta["wikisource_lang"], meta["wikisource_title"], int(meta["expected_chapters"]))
    chapters, page_records = page_units(leaves)
    expected = int(meta["expected_chapters"])
    if len(chapters) != expected:
        discovery = {
            "status": "FAIL_SOURCE_EXTENT",
            "expected_sections": expected,
            "discovered_sections": len(chapters),
            "root": {"title": root["title"], "revid": root["revid"]},
            "leaves": page_records,
        }
        (out / f'{meta["rank"]:03d}_SOURCE_EXTENT_FAILURE.json').write_text(json.dumps(discovery, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise SystemExit(json.dumps(discovery, ensure_ascii=False, indent=2))

    joined = "\n\n".join(u.source for c in chapters for u in c)
    source_sha = sha(joined.encode("utf-8"))
    source_url = f'https://{meta["wikisource_lang"]}.wikisource.org/wiki/{urllib.parse.quote(root["title"].replace(" ", "_"))}'
    source = {
        "provider": f'{meta["wikisource_lang"]}.wikisource.org',
        "metadata_url": source_url,
        "download_url": source_url,
        "source_sha256": source_sha,
        "clean_body_sha256": source_sha,
        "retrieved_utc": datetime.now(timezone.utc).isoformat(),
        "language": meta["source_language"],
        "citation": f'Wikisource, <i>{meta["original_title"]}</i>, revisione radice {root["revid"]}',
        "root_revision": root["revid"],
        "page_revisions": page_records,
    }
    (out / f'{meta["rank"]:03d}_source.txt').write_text(joined, encoding="utf-8")
    (out / f'{meta["rank"]:03d}_source_record.json').write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    model_dir = snapshot_download(repo_id=MADLAD_RUNTIME, local_dir="madlad_ct2")
    tokenizer = hf_hub_download(repo_id=MADLAD, filename="spiece.model", local_dir="madlad_tokenizer")
    runtime = translate_units(chapters, model_dir, tokenizer)
    alignment = out / f'{meta["rank"]:03d}_alignment.jsonl'
    with alignment.open("w", encoding="utf-8") as handle:
        for chapter in chapters:
            for unit in chapter:
                handle.write(json.dumps({
                    "unit_id": unit.unit_id, "chapter": unit.chapter, "kind": unit.kind,
                    "source_sha256": sha(unit.source.encode()), "source": unit.source,
                    "translation": unit.translation, "translation_sha256": sha(unit.translation.encode()),
                }, ensure_ascii=False) + "\n")
    epub, build = write_epub(out, meta, "", chapters, source)
    report = qa(meta, chapters, source, epub, build)
    report["runtime"] = runtime
    report["source_extent"] = {"root_revision": root["revid"], "leaf_count": len(page_records), "leaves": page_records}
    qa_path = out / f'{meta["rank"]:03d}_{meta["slug"]}_QA.json'
    qa_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if report["status"] != "PASS":
        raise SystemExit(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"status": "PASS", "epub": str(epub), "qa": str(qa_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
