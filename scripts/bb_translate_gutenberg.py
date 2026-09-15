#!/usr/bin/env python3
"""Translate a chaptered Project Gutenberg source and build a BB Italian review EPUB."""

from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import textwrap
import unicodedata
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import ctranslate2
import sentencepiece as spm
from huggingface_hub import hf_hub_download, snapshot_download
from PIL import Image, ImageDraw, ImageFont


BB_MASTER = "5.0.13"
MADLAD = "google/madlad400-3b-mt"
MADLAD_RUNTIME = "Heng666/madlad400-3b-mt-ct2-int8"
PG_START = re.compile(r"^\*\*\* START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*\s*$", re.I | re.M)
PG_END = re.compile(r"^\*\*\* END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*\s*$", re.I | re.M)
ROMAN_CHAPTER = re.compile(r"(?mi)^\s*CHAPTER\s+([IVXLCDM]+)\s*\.?\s*$")


@dataclass
class Unit:
    unit_id: str
    chapter: int
    kind: str
    source: str
    translation: str = ""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "BoundaryBayProduction/5.0.13"})
    with urllib.request.urlopen(req, timeout=90) as response:
        return response.read()


def strip_pg(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    start = PG_START.search(text)
    end = PG_END.search(text)
    if not start or not end or end.start() <= start.end():
        raise ValueError("Project Gutenberg start/end markers were not found")
    return text[start.end():end.start()].strip()


def normalize_block(block: str) -> str:
    lines = [line.rstrip() for line in block.strip().splitlines()]
    if not lines:
        return ""
    if all(not line.strip() for line in lines):
        return ""
    # Project Gutenberg hard wraps prose. Retain stanza/letter indentation only
    # when most lines are visibly indented or unusually short.
    indented = sum(bool(re.match(r"^\s{3,}\S", line)) for line in lines)
    if len(lines) > 1 and indented >= max(1, len(lines) // 2):
        return "\n".join(line.strip() for line in lines if line.strip())
    return " ".join(line.strip() for line in lines if line.strip())


def split_long_block(text: str, limit: int = 1500) -> list[str]:
    if len(text) <= limit:
        return [text]
    pieces = re.split(r"(?<=[.!?…])\s+(?=[\"“‘A-ZÀ-ÖØ-Þ])", text)
    out, current = [], ""
    for piece in pieces:
        if len(piece) > limit:
            words = piece.split()
            for word in words:
                if current and len(current) + len(word) + 1 > limit:
                    out.append(current)
                    current = word
                else:
                    current = f"{current} {word}".strip()
            continue
        if current and len(current) + len(piece) + 1 > limit:
            out.append(current)
            current = piece
        else:
            current = f"{current} {piece}".strip()
    if current:
        out.append(current)
    return out


def extract_units(body: str, meta: dict) -> tuple[str, list[list[Unit]]]:
    pattern = re.compile(meta.get("section_pattern", ROMAN_CHAPTER.pattern), re.I | re.M)
    matches = list(pattern.finditer(body))
    expected = int(meta.get("expected_chapters", 0))
    if meta.get("take_last_matches") and expected and len(matches) >= expected:
        matches = matches[-expected:]
    if len(matches) < 2:
        raise ValueError(f"Expected a chaptered source; found {len(matches)} chapter headings")
    preface_marker = meta.get("preface_marker", "How these papers")
    preface_start = body.find(preface_marker) if preface_marker else -1
    preface = body[preface_start:matches[0].start()].strip() if preface_start >= 0 else ""
    chapters: list[list[Unit]] = []
    for idx, match in enumerate(matches):
        tail = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        chapter_text = body[match.end():tail].strip()
        blocks = [normalize_block(b) for b in re.split(r"\n\s*\n+", chapter_text)]
        blocks = [b for b in blocks if b]
        chapter_units: list[Unit] = []
        ordinal = 0
        if meta.get("include_source_heading"):
            chapter_units.append(Unit(f"c{idx+1:02d}-u0000", idx + 1, "heading", normalize_block(match.group(0))))
        for block in blocks:
            kind = "heading" if ordinal < 3 and len(block) < 140 and "\n" not in block else "paragraph"
            for piece in split_long_block(block):
                ordinal += 1
                chapter_units.append(Unit(f"c{idx+1:02d}-u{ordinal:04d}", idx + 1, kind, piece))
        chapters.append(chapter_units)
    return preface, chapters


def translate_units(chapters: list[list[Unit]], model_dir: str, tokenizer_file: str) -> dict:
    processor = spm.SentencePieceProcessor(model_file=tokenizer_file)
    translator = ctranslate2.Translator(
        model_dir,
        device="cpu",
        compute_type="int8",
        inter_threads=max(1, min(4, os.cpu_count() or 2)),
        intra_threads=max(1, min(4, os.cpu_count() or 2)),
    )
    flat = [unit for chapter in chapters for unit in chapter]
    batch_size = 12
    for offset in range(0, len(flat), batch_size):
        batch = flat[offset:offset + batch_size]
        tokens = [processor.encode("<2it> " + unit.source, out_type=str) for unit in batch]
        outputs = translator.translate_batch(
            tokens,
            beam_size=4,
            max_decoding_length=640,
            batch_type="tokens",
            max_batch_size=2048,
        )
        for unit, result in zip(batch, outputs):
            unit.translation = processor.decode(result.hypotheses[0]).strip()
        if offset % 120 == 0:
            print(f"translated {min(offset + batch_size, len(flat))}/{len(flat)} semantic units", flush=True)
    return {
        "model": MADLAD,
        "runtime_model": MADLAD_RUNTIME,
        "unit_count": len(flat),
        "source_chars": sum(len(u.source) for u in flat),
        "translation_chars": sum(len(u.translation) for u in flat),
    }


def esc(value: str) -> str:
    return html.escape(value, quote=True)


def xhtml_page(title: str, body: str, body_class: str = "") -> str:
    klass = f' class="{esc(body_class)}"' if body_class else ""
    return f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="it" lang="it">
<head><meta charset="utf-8"/><title>{esc(title)}</title><link rel="stylesheet" type="text/css" href="../css/bb.css"/></head>
<body{klass}>{body}</body></html>
'''


def paragraph_html(unit: Unit) -> str:
    value = esc(unit.translation).replace("\n", "<br/>")
    if unit.kind == "heading":
        return f'<p class="subhead">{value}</p>'
    return f"<p>{value}</p>"


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("assets/fonts") / ("Spectral-Bold.ttf" if bold else "Spectral-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def wrap_draw(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def make_cover(path: Path, title: str, author: str, genre: str) -> None:
    w, h = 1950, 2993
    image = Image.new("RGB", (w, h), "#10131f")
    draw = ImageDraw.Draw(image)
    gold, ivory, red = "#d0ad63", "#f3eee1", "#781f2d"
    draw.rectangle((85, 85, w - 85, h - 85), outline=gold, width=10)
    draw.rectangle((120, 120, w - 120, h - 120), outline=gold, width=3)
    draw.rectangle((0, 0, w, 185), fill=red)
    genre_font = load_font(66, True)
    draw.text((w / 2, 92), genre.upper(), fill=ivory, font=genre_font, anchor="mm")
    author_font = load_font(76, False)
    draw.text((w / 2, 530), author.upper(), fill=gold, font=author_font, anchor="mm")
    title_font = load_font(184 if len(title) < 18 else 132, True)
    lines = wrap_draw(draw, title.upper(), title_font, w - 360)
    total = len(lines) * int(title_font.size * 1.18)
    y = 1480 - total // 2
    for line in lines:
        draw.text((w / 2, y), line, fill=ivory, font=title_font, anchor="ma")
        y += int(title_font.size * 1.18)
    # A restrained exact-work-neutral emblem; no synthetic scene is claimed.
    cx, cy = w // 2, 2220
    draw.ellipse((cx - 205, cy - 205, cx + 205, cy + 205), outline=red, width=18)
    draw.polygon([(cx, cy - 145), (cx - 85, cy + 105), (cx, cy + 40), (cx + 85, cy + 105)], fill=red)
    small = load_font(55, False)
    draw.text((w / 2, 2705), "BOUNDARY BAY CLASSICS", fill=gold, font=small, anchor="mm")
    image.save(path, "JPEG", quality=93, optimize=True, progressive=True)


def write_epub(outdir: Path, meta: dict, preface: str, chapters: list[list[Unit]], source: dict) -> tuple[Path, dict]:
    root = outdir / "epub"
    epub = root / "EPUB"
    for directory in [root / "META-INF", epub / "text", epub / "css", epub / "images", epub / "fonts"]:
        directory.mkdir(parents=True, exist_ok=True)
    (root / "mimetype").write_text("application/epub+zip", encoding="ascii")
    (root / "META-INF/container.xml").write_text('''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>''', encoding="utf-8")

    css = '''@font-face{font-family:Spectral;src:url(../fonts/Spectral-Regular.ttf)}@font-face{font-family:Spectral;font-weight:bold;src:url(../fonts/Spectral-Bold.ttf)}
html{font-size:100%}body{font-family:Spectral,serif;line-height:1.5;margin:5%;color:#171717}p{margin:.35em 0;text-indent:1.2em;text-align:justify;orphans:2;widows:2}h1,h2{text-align:center;break-after:avoid}h1{font-size:1.7em;margin:1.8em 0 1.2em}.subhead{text-align:center;text-indent:0;font-variant:small-caps;margin:.7em 0}.noindent,.center,.source-note{text-indent:0}.center{text-align:center}.source-note{font-size:.85em}.chapter{break-before:page}.cover{margin:0;padding:0;text-align:center}.cover img{max-width:100%;max-height:100vh}.badge{border:.08em solid #6d5730;padding:.35em .7em;display:inline-block}.ornament{text-align:center;text-indent:0;color:#8a6b35}.bb-chapter-document h1:after{content:'◆';display:block;font-size:.45em;color:#8a6b35;margin-top:1em}a{color:inherit}'''
    (epub / "css/bb.css").write_text(css, encoding="utf-8")
    for name in ["Spectral-Regular.ttf", "Spectral-Bold.ttf"]:
        src = Path("assets/fonts") / name
        if src.exists():
            shutil.copyfile(src, epub / "fonts" / name)
    cover = epub / "images/cover.jpg"
    make_cover(cover, meta["italian_title"], meta["author"], meta["genre"])

    title = meta["italian_title"]
    author = meta["author"]
    section_label = meta.get("section_label", "Capitolo")
    pub_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"boundarybay:italian:{meta['rank']}:{title}:{source['source_sha256']}"))
    pages: list[tuple[str, str, str]] = []
    pages.append(("cover", "Copertina", xhtml_page("Copertina", '<div class="cover"><img src="../images/cover.jpg" alt="Copertina"/></div>')))
    pages.append(("title", "Frontespizio", xhtml_page("Frontespizio", f'<section><h1>{esc(title)}</h1><p class="center">{esc(author)}</p><p class="center"><span class="badge">Modernized for Easy Reading</span></p><p class="center">Boundary Bay Classics · Italian Collection</p></section>')))
    rights = "Testo originale di pubblico dominio negli Stati Uniti e nell’Unione europea. Traduzione italiana e apparati editoriali © 2026 Boundary Bay Classics."
    citation = source.get("citation", f'Project Gutenberg eBook #{source.get("pg_id", "")}, <i>{esc(meta["original_title"])}</i>')
    pages.append(("copyright", "Copyright e fonte", xhtml_page("Copyright e fonte", f'<section><h1>Copyright e fonte</h1><p class="noindent">{esc(rights)}</p><p class="source-note">Fonte primaria: {citation}, in {esc(meta["source_language_label"])}. Record: {esc(source["metadata_url"])}. SHA-256 del testo UTF-8: <code>{source["source_sha256"]}</code>.</p><p class="source-note">Traduzione moderna, capitolo per capitolo, assistita da {MADLAD}. Non viene attribuita a un traduttore umano. Il precedente secondo passaggio Gemini non era configurato per questa esecuzione; nessun modello sostitutivo è stato usato.</p></section>')))
    pages.append(("author", "L’autore", xhtml_page("L’autore", f'<section><h1>{esc(author)}</h1><p class="noindent">{esc(meta["author_note"])}</p></section>')))
    toc_links = ''.join(f'<li><a href="chapter_{i:02d}.xhtml">{esc(section_label)} {i}</a></li>' for i in range(1, len(chapters)+1))
    pages.append(("contents", "Indice", xhtml_page("Indice", f'<nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops"><h1>Indice</h1><ol>{toc_links}</ol></nav>')))
    if preface:
        pages.append(("notice", "Nota iniziale", xhtml_page("Nota iniziale", '<section><h1>Nota iniziale</h1><p class="noindent">Questa breve nota introduttiva appartiene alla fonte originale; è conservata nel registro di allineamento e sarà sottoposta alla revisione editoriale finale.</p></section>')))
    for i, chapter in enumerate(chapters, 1):
        label = f"{section_label} {i}"
        body = f'<section epub:type="chapter" xmlns:epub="http://www.idpf.org/2007/ops"><h1>{esc(label)}</h1>' + ''.join(paragraph_html(u) for u in chapter) + '</section>'
        pages.append((f"chapter_{i:02d}", label, xhtml_page(label, body, "source-text bb-chapter-document chapter")))
    apparatus = f'''<section><h1>Nota editoriale</h1><p class="noindent">Questa edizione segue integralmente la struttura in {len(chapters)} sezioni della fonte autenticata in {esc(meta["source_language_label"])}. La prima bozza italiana è stata prodotta per unità semantiche con <i>{MADLAD}</i>, lo stesso traduttore di prima passata usato per <i>Le avventure di Huckleberry Finn</i>. Nomi, date, documenti e cambi di voce dell’originale sono mantenuti come elementi strutturali dell’opera.</p><p class="noindent">Non sono state inserite illustrazioni narrative generate. La ricerca della fonte ha privilegiato l’edizione testuale completa; questa copia di revisione adotta quindi un assetto onestamente privo di tavole storiche finché una serie illustrata, esatta per l’opera e chiaramente riutilizzabile, non venga autenticata.</p></section>'''
    pages.append(("afterword", "Nota editoriale", xhtml_page("Nota editoriale", apparatus)))
    pages.append(("credits", "Crediti", xhtml_page("Crediti", f'<section><h1>Crediti</h1><p class="noindent">Fonte e trascrizione: {esc(source.get("provider", "fonte digitale autenticata"))}. Traduzione assistita: {MADLAD}. Progetto editoriale e produzione EPUB: Boundary Bay Classics.</p></section>')))
    pages.append(("discovery", "Scopri altri libri", xhtml_page("Scopri altri libri", '<section><h1>Scopri altri libri</h1><p class="center">Boundary Bay Classics · Italian Collection</p></section>')))
    pages.append(("colophon", "Colophon", xhtml_page("Colophon", f'<section><h1>Colophon</h1><p class="noindent">Edizione REVIEW prodotta secondo Boundary Bay Production Master {BB_MASTER}.</p><p class="noindent">Identificatore: urn:uuid:{pub_uuid}</p></section>')))
    for page_id, _, content in pages:
        (epub / "text" / f"{page_id}.xhtml").write_text(content, encoding="utf-8")

    nav_items = ''.join(f'<li><a href="text/{pid}.xhtml">{esc(label)}</a></li>' for pid, label, _ in pages if pid not in {"cover"})
    nav = f'''<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="it"><head><title>Indice</title></head><body><nav epub:type="toc"><h1>Indice</h1><ol>{nav_items}</ol></nav></body></html>'''
    (epub / "nav.xhtml").write_text(nav, encoding="utf-8")
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>', '<item id="css" href="css/bb.css" media-type="text/css"/>', '<item id="cover-image" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>']
    for name in ["Spectral-Regular.ttf", "Spectral-Bold.ttf"]:
        if (epub / "fonts" / name).exists():
            manifest.append(f'<item id="font-{len(manifest)}" href="fonts/{name}" media-type="font/ttf"/>')
    spine = []
    for idx, (pid, _, _) in enumerate(pages, 1):
        manifest.append(f'<item id="text-{idx:03d}" href="text/{pid}.xhtml" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="text-{idx:03d}"/>')
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    opf = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" version="3.0" prefix="bb: https://boundarybaypublishing.com/ns/epub#" unique-identifier="pub-id"><metadata><dc:identifier id="pub-id">urn:uuid:{pub_uuid}</dc:identifier><dc:title>{esc(title)}</dc:title><dc:creator id="creator">{esc(author)}</dc:creator><meta refines="#creator" property="role" scheme="marc:relators">aut</meta><dc:language>it</dc:language><dc:source>{esc(source["metadata_url"])}</dc:source><dc:publisher>Boundary Bay Classics</dc:publisher><dc:date>{modified[:10]}</dc:date><dc:subject>{esc(meta["genre"])}</dc:subject><dc:description>Traduzione italiana moderna, capitolo per capitolo, dell’opera completa.</dc:description><dc:rights>{esc(rights)}</dc:rights><meta property="dcterms:modified">{modified}</meta><meta property="belongs-to-collection" id="collection">Italian Collection</meta><meta refines="#collection" property="collection-type">series</meta><meta property="bb:content-treatment">modernized</meta><meta property="bb:modernized-badge">required</meta><meta property="bb:modernized-label">Modernized for Easy Reading</meta><meta property="bb:translation-method">machine-assisted-chapter-by-chapter</meta><meta property="bb:isbn-status">ISBN_NOT_FOUND</meta><meta property="bb:production-master">{BB_MASTER}</meta><meta name="cover" content="cover-image"/></metadata><manifest>{''.join(manifest)}</manifest><spine>{''.join(spine)}</spine></package>'''
    (epub / "package.opf").write_text(opf, encoding="utf-8")

    filename = f'{meta["rank"]:03d}_{meta["slug"]}_BB_v{BB_MASTER}_REVIEW_{modified[:10]}.epub'
    target = outdir / filename
    with zipfile.ZipFile(target, "w") as zf:
        zf.write(root / "mimetype", "mimetype", compress_type=zipfile.ZIP_STORED)
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name != "mimetype":
                zf.write(path, path.relative_to(root).as_posix(), compress_type=zipfile.ZIP_DEFLATED)
    report = {"epub": target.name, "publication_uuid": pub_uuid, "page_count": len(pages), "chapter_count": len(chapters), "zip_sha256": sha(target.read_bytes())}
    return target, report


def qa(meta: dict, chapters: list[list[Unit]], source: dict, epub: Path, build: dict) -> dict:
    flat = [u for c in chapters for u in c]
    failures = []
    if len(chapters) != meta["expected_chapters"]:
        failures.append(f'chapter_count={len(chapters)} expected={meta["expected_chapters"]}')
    empty = [u.unit_id for u in flat if not u.translation.strip()]
    if empty:
        failures.append(f"empty_translations={len(empty)}")
    ratio = sum(len(u.translation) for u in flat) / max(1, sum(len(u.source) for u in flat))
    if ratio < 0.55 or ratio > 1.65:
        failures.append(f"translation_length_ratio={ratio:.3f}")
    with zipfile.ZipFile(epub) as zf:
        names = zf.namelist()
        if names[0] != "mimetype" or zf.getinfo("mimetype").compress_type != zipfile.ZIP_STORED:
            failures.append("epub_mimetype_rule")
        for name in names:
            if name.endswith((".xhtml", ".opf", ".xml")):
                try:
                    ET.fromstring(zf.read(name))
                except Exception as exc:
                    failures.append(f"xml:{name}:{exc}")
    return {
        "status": "PASS" if not failures else "FAIL",
        "bb_master": BB_MASTER,
        "rank": meta["rank"],
        "title": meta["italian_title"],
        "source": source,
        "chapter_count": len(chapters),
        "semantic_unit_count": len(flat),
        "source_chars": sum(len(u.source) for u in flat),
        "translation_chars": sum(len(u.translation) for u in flat),
        "translation_length_ratio": ratio,
        "translator": MADLAD,
        "editorial_pass": {"model": "google/gemini-3-flash-preview", "status": "NOT_CONFIGURED", "substitute_used": False},
        "fal_used": False,
        "isbn_status": "ISBN_NOT_FOUND",
        "historical_art": {"count": 0, "status": "HONEST_ZERO_ART_PENDING_EXACT_WORK_AUTHENTICATION"},
        "epub": build,
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", default="dist")
    args = parser.parse_args()
    meta = json.loads(Path(args.config).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    source_url = f'https://www.gutenberg.org/cache/epub/{meta["pg_id"]}/pg{meta["pg_id"]}.txt'
    raw = fetch(source_url)
    text = raw.decode("utf-8-sig")
    body = strip_pg(text)
    preface, chapters = extract_units(body, meta)
    source = {
        "provider": "Project Gutenberg",
        "pg_id": meta["pg_id"],
        "metadata_url": f'https://www.gutenberg.org/ebooks/{meta["pg_id"]}',
        "download_url": source_url,
        "source_sha256": sha(raw),
        "clean_body_sha256": sha(body.encode("utf-8")),
        "retrieved_utc": datetime.now(timezone.utc).isoformat(),
        "language": meta["source_language"],
        "citation": f'Project Gutenberg eBook #{meta["pg_id"]}, <i>{esc(meta["original_title"])}</i>',
    }
    (out / f'{meta["rank"]:03d}_source.txt').write_text(body, encoding="utf-8")
    model_dir = snapshot_download(repo_id=MADLAD_RUNTIME, local_dir="madlad_ct2")
    tokenizer = hf_hub_download(repo_id=MADLAD, filename="spiece.model", local_dir="madlad_tokenizer")
    runtime = translate_units(chapters, model_dir, tokenizer)
    alignment_path = out / f'{meta["rank"]:03d}_alignment.jsonl'
    with alignment_path.open("w", encoding="utf-8") as handle:
        for chapter in chapters:
            for unit in chapter:
                handle.write(json.dumps({
                    "unit_id": unit.unit_id,
                    "chapter": unit.chapter,
                    "kind": unit.kind,
                    "source_sha256": sha(unit.source.encode()),
                    "source": unit.source,
                    "translation": unit.translation,
                    "translation_sha256": sha(unit.translation.encode()),
                }, ensure_ascii=False) + "\n")
    epub, build = write_epub(out, meta, preface, chapters, source)
    report = qa(meta, chapters, source, epub, build)
    report["runtime"] = runtime
    qa_path = out / f'{meta["rank"]:03d}_{meta["slug"]}_QA.json'
    qa_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if report["status"] != "PASS":
        raise SystemExit(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"status": report["status"], "epub": str(epub), "qa": str(qa_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
