#!/usr/bin/env python3
"""Acquire scan-backed original-language Wikisource leaves, translate, and build BB EPUB."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
import urllib.parse
import urllib.error
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
    load_shard_translations,
    load_source_bundle,
    materialize_runtime,
    qa,
    save_source_bundle,
    sha,
    split_long_block,
    translate_units,
    write_alignment,
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
        except urllib.error.HTTPError as exc:
            error = exc
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After", "")
                try:
                    delay = max(12, int(retry_after))
                except ValueError:
                    delay = 20
                time.sleep(delay * (attempt + 1))
            else:
                time.sleep(2 ** attempt)
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
        remainder = normalized[len(prefix):] if normalized.startswith(prefix) else ""
        if remainder and "/" not in remainder and title not in results:
            results.append(title)
    return results


def ordered_links(titles: list[str]) -> list[str]:
    words = {
        "от автора": 0,
        "первая": 1, "первый": 1, "первое": 1,
        "вторая": 2, "второй": 2, "второе": 2,
        "третья": 3, "третий": 3, "третье": 3,
        "четвертая": 4, "четвёртая": 4, "четвертое": 4, "четвёртое": 4,
        "пятая": 5, "шестая": 6, "седьмая": 7, "восьмая": 8,
        "девятая": 9, "десятая": 10, "одиннадцатая": 11,
        "двенадцатая": 12, "эпилог": 90,
    }

    def key(title: str) -> tuple:
        leaf = title.rsplit("/", 1)[-1].casefold().replace("ё", "е")
        for word, value in words.items():
            if word.replace("ё", "е") in leaf:
                return (0, value, leaf)
        roman = re.fullmatch(r"[ivxlcdm]+", leaf, re.I)
        if roman:
            values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
            total, prior = 0, 0
            for char in reversed(leaf):
                value = values[char]
                total += -value if value < prior else value
                prior = max(prior, value)
            return (0, total, leaf)
        match = re.search(r"\d+", leaf)
        if match:
            return (0, int(match.group()), leaf)
        return (1, leaf)

    return sorted(titles, key=key)


def split_marker_pages(page: dict, expected: int) -> list[dict]:
    """Split single-page plays whose act labels are visual, not wiki sections."""
    if expected <= 1:
        return []
    soup = BeautifulSoup(page["html"], "lxml")
    root = soup.select_one(".prp-pages-output") or soup.select_one(".mw-parser-output") or soup
    marker = re.compile(
        r"^(?:ДЕЙСТВИЕ|АКТ)\s+(?:ПЕРВОЕ|ВТОРОЕ|ТРЕТЬЕ|ЧЕТВЕРТОЕ|ПЯТОЕ)\.?$",
        re.I,
    )
    prelude: list[str] = []
    groups: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    title = ""
    for node in root.select("center, h2, h3, h4, p, blockquote, div.poem"):
        if node.name == "p" and node.find_parent("div", class_="poem"):
            continue
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
        if marker.fullmatch(text):
            if current is not None:
                groups.append((title, current))
            title, current = text, []
            continue
        if current is None:
            prelude.append(str(node))
        else:
            current.append(str(node))
    if current is not None:
        groups.append((title, current))
    if len(groups) != expected:
        return []
    groups[0][1][:0] = prelude
    return [{
        "title": f'{page["title"]}/{group_title}',
        "revid": page["revid"],
        "html": '<div class="mw-parser-output">' + "".join(nodes) + "</div>",
        "links": [],
        "sections": [],
    } for group_title, nodes in groups]


def split_heading_pages(page: dict, expected: int) -> list[dict]:
    """Split transcluded whole books at roman-numeral DOM headings."""
    if expected <= 1:
        return []
    soup = BeautifulSoup(page["html"], "lxml")
    root = soup.select_one(".prp-pages-output") or soup.select_one(".mw-parser-output") or soup
    headings = [
        node for node in root.select("h2")
        if re.fullmatch(r"[IVXLCDM]+\.?", node.get_text(" ", strip=True), re.I)
    ]
    if len(headings) != expected:
        return []
    marker_ids = {id(node): node.get_text(" ", strip=True) for node in headings}
    prelude: list[str] = []
    groups: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    title = ""
    for node in root.select("h2, h3, h4, p, blockquote, div.poem"):
        if node.name == "p" and node.find_parent("div", class_="poem"):
            continue
        if id(node) in marker_ids:
            if current is not None:
                groups.append((title, current))
            title, current = marker_ids[id(node)], []
            continue
        if current is None:
            prelude.append(str(node))
        else:
            current.append(str(node))
    if current is not None:
        groups.append((title, current))
    if len(groups) != expected:
        return []
    groups[0][1][:0] = prelude
    return [{
        "title": f'{page["title"]}/{group_title}',
        "revid": page["revid"],
        "html": '<div class="mw-parser-output">' + "".join(nodes) + "</div>",
        "links": [],
        "sections": [],
    } for group_title, nodes in groups]


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
        children = child_links(page, page["title"])
        # Index/root pages often expose the complete descendant tree while
        # intermediate transclusion pages omit links to their own chapter
        # leaves. Use the authenticated root index as a second directory.
        for child in child_links(root_page, page["title"]):
            if child not in children:
                children.append(child)
        children = ordered_links(children)
        if children and depth < 4:
            for child in children:
                walk(child, depth + 1)
        else:
            level_two = [s for s in page.get("sections", []) if str(s.get("level")) == "2"]
            if len(level_two) == expected:
                for section in level_two:
                    time.sleep(2.5)
                    virtual = parse_page(lang, page["title"], str(section["index"]))
                    virtual["title"] = f'{page["title"]}/{section.get("line", section["index"])}'
                    ordered.append(virtual)
            else:
                ordered.extend(
                    split_marker_pages(page, expected)
                    or split_heading_pages(page, expected)
                    or [page]
                )
        time.sleep(2.5)

    root_level_two = [s for s in root_page.get("sections", []) if str(s.get("level")) == "2"]
    start_children = ordered_links(child_links(root_page, canonical_root))
    if start_children and len(start_children) >= expected:
        for child in start_children:
            walk(child, 1)
    elif expected > 1 and len(root_level_two) == expected:
        for section in root_level_two[:expected]:
            time.sleep(2.5)
            virtual = parse_page(lang, canonical_root, str(section["index"]))
            virtual["title"] = f'{canonical_root}/{section.get("line", section["index"])}'
            ordered.append(virtual)
    elif start_children:
        for child in start_children:
            walk(child, 1)
    else:
        ordered.extend(
            split_marker_pages(root_page, expected)
            or split_heading_pages(root_page, expected)
            or [root_page]
        )
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


def acquire_wikisource(meta: dict) -> tuple[str, list[list[Unit]], dict, list[dict]]:
    root, leaves = leaf_pages(meta["wikisource_lang"], meta["wikisource_title"], int(meta["expected_chapters"]))
    chapters, page_records = page_units(leaves)
    expected = int(meta["expected_chapters"])
    if len(chapters) != expected:
        raise ValueError(json.dumps({
            "status": "FAIL_SOURCE_EXTENT",
            "expected_sections": expected,
            "discovered_sections": len(chapters),
            "root": {"title": root["title"], "revid": root["revid"]},
            "leaves": page_records,
        }, ensure_ascii=False, indent=2))
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
    return joined, chapters, source, page_records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", default="dist")
    parser.add_argument("--mode", choices=["full", "prepare", "shard", "assemble"], default="full")
    parser.add_argument("--shard-index", type=int)
    parser.add_argument("--shard-size", type=int, default=96)
    parser.add_argument("--shards-dir")
    parser.add_argument("--source-bundle")
    args = parser.parse_args()
    meta = json.loads(Path(args.config).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        if args.source_bundle:
            _, joined, _, chapters, source = load_source_bundle(Path(args.source_bundle), meta)
            page_records = source.get("page_revisions", [])
        else:
            joined, chapters, source, page_records = acquire_wikisource(meta)
    except ValueError as exc:
        try:
            discovery = json.loads(str(exc))
        except Exception:
            raise
        (out / f'{meta["rank"]:03d}_SOURCE_EXTENT_FAILURE.json').write_text(
            json.dumps(discovery, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise SystemExit(json.dumps(discovery, ensure_ascii=False, indent=2))
    flat = [unit for chapter in chapters for unit in chapter]

    if args.mode == "prepare":
        plan = {
            "rank": meta["rank"], "config": args.config, "unit_count": len(flat),
            "chapter_count": len(chapters), "shard_size": args.shard_size,
            "shard_count": math.ceil(len(flat) / args.shard_size),
            "source_sha256": source["source_sha256"],
            "source_bundle": f'{meta["rank"]:03d}_source_bundle.json',
        }
        save_source_bundle(out / plan["source_bundle"], meta, joined, "", chapters, source)
        (out / f'{meta["rank"]:03d}_shard_plan.json').write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(plan, ensure_ascii=False))
        return

    if args.mode == "shard":
        if args.shard_index is None or args.shard_index < 0:
            raise ValueError("--shard-index must be a non-negative integer in shard mode")
        start = args.shard_index * args.shard_size
        selected = flat[start:start + args.shard_size]
        if not selected:
            raise ValueError(f"Shard {args.shard_index} starts beyond {len(flat)} units")
        model_dir, tokenizer = materialize_runtime()
        runtime = translate_units([selected], model_dir, tokenizer)
        alignment = out / f'{meta["rank"]:03d}_shard_{args.shard_index:04d}_alignment.jsonl'
        write_alignment(alignment, selected)
        report = {
            "status": "PASS", "rank": meta["rank"], "shard_index": args.shard_index,
            "shard_size": args.shard_size, "start_unit": start, "translated_units": len(selected),
            "first_unit": selected[0].unit_id, "last_unit": selected[-1].unit_id,
            "source_sha256": source["source_sha256"], "translator": MADLAD,
            "fal_used": False, "runtime": runtime,
        }
        (out / f'{meta["rank"]:03d}_shard_{args.shard_index:04d}_QA.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        return

    if args.mode == "assemble":
        if not args.shards_dir:
            raise ValueError("--shards-dir is required in assemble mode")
        records = load_shard_translations(Path(args.shards_dir), int(meta["rank"]))
        missing = []
        for unit in flat:
            record = records.get(unit.unit_id)
            if not record or record.get("source_sha256") != sha(unit.source.encode()):
                missing.append(unit.unit_id)
            else:
                unit.translation = record["translation"]
        if missing:
            raise ValueError(f"Missing or source-mismatched aligned translations: {len(missing)}; first={missing[:8]}")

    (out / f'{meta["rank"]:03d}_source.txt').write_text(joined, encoding="utf-8")
    (out / f'{meta["rank"]:03d}_source_record.json').write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.mode == "full":
        model_dir, tokenizer = materialize_runtime()
        runtime = translate_units(chapters, model_dir, tokenizer)
    else:
        runtime = {
            "model": MADLAD, "runtime_model": MADLAD_RUNTIME,
            "decoding": {"beam_size": 1, "max_decoding_length": 640},
            "unit_count": len(flat), "assembled_from_shards": True,
        }
    alignment = out / f'{meta["rank"]:03d}_alignment.jsonl'
    write_alignment(alignment, flat)
    epub, build = write_epub(out, meta, "", chapters, source)
    report = qa(meta, chapters, source, epub, build)
    report["runtime"] = runtime
    report["source_extent"] = {"root_revision": source.get("root_revision"), "leaf_count": len(page_records), "leaves": page_records}
    qa_path = out / f'{meta["rank"]:03d}_{meta["slug"]}_QA.json'
    qa_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if report["status"] != "PASS":
        raise SystemExit(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"status": "PASS", "epub": str(epub), "qa": str(qa_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
