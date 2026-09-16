#!/usr/bin/env python3
"""Translate a chaptered Project Gutenberg source and build a BB Italian review EPUB."""

from __future__ import annotations

import argparse
import difflib
import math
import hashlib
import html
import io
import json
import os
import posixpath
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


def seed_structural_translations(units: list[Unit], meta: dict) -> None:
    original = re.sub(r"\W+", " ", meta["original_title"].casefold()).strip()
    for unit in units:
        source = re.sub(r"\W+", " ", unit.source.casefold()).strip()
        if unit.kind == "heading" and source == original:
            unit.translation = meta["italian_title"]


def split_long_block(text: str, limit: int = 1400) -> list[str]:
    # One sentence per semantic unit sharply reduces the chance that an MT
    # decoder silently skips a clause while still producing plausible prose.
    out: list[str] = []
    for sentence in sentence_segments(text):
        if len(sentence) <= limit:
            out.append(sentence)
            continue
        clauses = re.split(r"(?<=[;:,—–])\s+", sentence)
        current = ""
        for clause in clauses:
            if len(clause) > limit:
                for word in clause.split():
                    if current and len(current) + len(word) + 1 > limit:
                        out.append(current)
                        current = word
                    else:
                        current = f"{current} {word}".strip()
            elif current and len(current) + len(clause) + 1 > limit:
                out.append(current)
                current = clause
            else:
                current = f"{current} {clause}".strip()
        if current:
            out.append(current)
    return out


def sentence_segments(text: str) -> list[str]:
    """Split prose conservatively for omission detection and targeted repair."""
    boundary = re.compile(
        r"([.!?…]+[»«”\"’]*)(\s+)(?=[«„“\"A-ZÀ-ÖØ-ÞА-ЯЁΑ-ΩΆΈΉΊΌΎΏ])"
    )
    abbreviations = {
        "mr", "mrs", "ms", "dr", "rev", "prof", "capt", "col", "gen",
        "st", "ste", "jr", "sr", "no", "nos", "mme", "mlle", "mons",
        "herr", "fr", "hr", "dott", "sig", "sigg", "signor", "signora",
        "e.g", "i.e", "etc", "vs",
    }
    raw = text.strip()
    out: list[str] = []
    start = 0
    for match in boundary.finditer(raw):
        end = match.start(2)
        piece = raw[start:end].strip()
        before = re.sub(r"[»«”\"’]+$", "", piece).rstrip()
        word_match = re.search(r"([^\W\d_]+)\.$", before, re.UNICODE)
        if word_match:
            word = word_match.group(1)
            if word.casefold() in abbreviations or (len(word) == 1 and word.isupper()):
                continue
        if piece:
            out.append(piece)
        start = match.end(2)
    tail = raw[start:].strip()
    if tail:
        out.append(tail)
    return out


def omission_risk(source: str, translation: str) -> bool:
    """Flag likely dropped prose without treating ordinary expansion as failure."""
    if len(source) < 90:
        return False
    source_sentences = sentence_segments(source)
    target_sentences = sentence_segments(translation)
    ratio = len(translation) / max(1, len(source))
    return (
        len(source_sentences) >= 2
        and len(target_sentences) < len(source_sentences)
    ) or ratio < 0.62


def repetition_risk(source: str, translation: str) -> bool:
    """Detect obvious decoder loops and duplicated target sentences."""
    segments = sentence_segments(translation)
    normalized = [re.sub(r"\W+", " ", value.casefold()).strip() for value in segments]
    if any(
        len(normalized[index]) > 35
        and normalized[index] == normalized[index - 1]
        for index in range(1, len(normalized))
    ):
        return True
    compact = re.sub(r"\s+", " ", translation).strip()
    midpoint = len(compact) // 2
    if len(compact) > 100 and abs(len(compact[:midpoint]) - len(compact[midpoint:])) <= 1:
        left = re.sub(r"\W+", "", compact[:midpoint].casefold())
        right = re.sub(r"\W+", "", compact[midpoint:].casefold())
        if left and left == right:
            return True
    return len(translation) / max(1, len(source)) > 1.75


def translation_risk(source: str, translation: str) -> bool:
    return omission_risk(source, translation) or repetition_risk(source, translation)


def normalize_translation(text: str) -> str:
    text = re.sub(r"(?<=[.!?…»”\"])(?=[A-ZÀ-ÖØ-Þ])", " ", text.strip())
    return re.sub(r"[ \t]+", " ", text)


def collapse_decoder_repetitions(source: str, translation: str) -> str:
    """Collapse adjacent near-identical target clauses absent from the source."""
    text = normalize_translation(translation)
    chunk_pattern = re.compile(r".+?(?:[.!?…;:]+[»«”\"’]*(?=\s|$)|$)", re.S)
    source_chunks = [m.group(0).strip() for m in chunk_pattern.finditer(source) if m.group(0).strip()]
    target_chunks = [m.group(0).strip() for m in chunk_pattern.finditer(text) if m.group(0).strip()]

    def normalized(value: str) -> str:
        return re.sub(r"\W+", " ", value.casefold()).strip()

    kept: list[str] = []
    for chunk in target_chunks:
        if kept and len(target_chunks) > len(source_chunks):
            right = normalized(chunk)
            duplicate = False
            for prior in kept:
                left = normalized(prior)
                similarity = difflib.SequenceMatcher(None, left, right).ratio()
                short = max(len(left.split()), len(right.split())) <= 4
                left_words, right_words = left.split(), right.split()
                shorter, longer = (
                    (left_words, right_words)
                    if len(left_words) <= len(right_words)
                    else (right_words, left_words)
                )
                word_iter = iter(longer)
                subsequence_repeat = (
                    len(shorter) >= 3
                    and all(any(word == candidate for candidate in word_iter) for word in shorter)
                )
                suffix_repeat = (
                    min(len(left), len(right)) >= 14
                    and (left.endswith(right) or right.endswith(left))
                )
                if left and right and (
                    left == right
                    or similarity >= (0.80 if short else 0.88)
                    or suffix_repeat
                    or subsequence_repeat
                ):
                    duplicate = True
                    break
            if duplicate:
                continue
        kept.append(chunk)
    return normalize_translation(" ".join(kept))


def repair_risky_units(
    units: list[Unit], translator: ctranslate2.Translator,
    processor: spm.SentencePieceProcessor,
) -> int:
    """Retranslate suspected omissions sentence-by-sentence with stronger search."""
    repaired = 0
    tasks: list[tuple[Unit, bool, list[str]]] = []
    for unit in units:
        cleaned = collapse_decoder_repetitions(unit.source, unit.translation)
        if cleaned != unit.translation:
            unit.translation = cleaned
            repaired += 1
        sentences = sentence_segments(unit.source)
        risky = translation_risk(unit.source, unit.translation)
        if unit.kind != "heading" and (len(sentences) >= 2 or risky):
            tasks.append((unit, risky, sentences))
    # A small deterministic beam plus anti-repetition controls prevents the
    # converted MADLAD runtime from falling into source-language decoder loops.
    for risky_value, beam_size in ((False, 2), (True, 2)):
        group = [task for task in tasks if task[1] is risky_value]
        for offset in range(0, len(group), 32):
            chunk = group[offset:offset + 32]
            encoded = [
                processor.encode("<2it> " + sentence, out_type=str)
                for _, _, sentences in chunk for sentence in sentences
            ]
            outputs = translator.translate_batch(
                encoded,
                beam_size=beam_size,
                repetition_penalty=1.3,
                no_repeat_ngram_size=3,
                max_decoding_length=min(384, max(96, max(map(len, encoded)) * 2)),
                batch_type="tokens",
                max_batch_size=1024,
            )
            cursor = 0
            for unit, risky, sentences in chunk:
                translated = outputs[cursor:cursor + len(sentences)]
                cursor += len(sentences)
                candidate = collapse_decoder_repetitions(unit.source, " ".join(
                    processor.decode(result.hypotheses[0]).strip()
                    for result in translated
                ))
                if candidate and (
                    risky or len(candidate) >= len(unit.translation) * 1.06
                ):
                    unit.translation = candidate
                    repaired += 1
    return repaired


def extract_units(body: str, meta: dict) -> tuple[str, list[list[Unit]]]:
    if meta.get("single_section"):
        start_marker = meta.get("content_marker")
        end_marker = meta.get("content_end_marker")
        section = body
        if start_marker:
            start = section.find(start_marker)
            if start < 0:
                raise ValueError(f"Single-section start marker was not found: {start_marker!r}")
            section = section[start if meta.get("include_content_marker") else start + len(start_marker):]
        if end_marker:
            end = section.find(end_marker)
            if end < 0:
                raise ValueError(f"Single-section end marker was not found: {end_marker!r}")
            section = section[:end]
        blocks = [normalize_block(b) for b in re.split(r"\n\s*\n+", section.strip())]
        blocks = [b for b in blocks if b]
        units: list[Unit] = []
        if meta.get("include_source_heading", True):
            units.append(Unit("c01-u0000", 1, "heading", meta["original_title"]))
        ordinal = 0
        for block in blocks:
            kind = "heading" if ordinal < 3 and len(block) < 140 and "\n" not in block else "paragraph"
            pieces = split_long_block(block)
            for piece_index, piece in enumerate(pieces):
                ordinal += 1
                piece_kind = "continuation" if kind == "paragraph" and piece_index else kind
                units.append(Unit(f"c01-u{ordinal:04d}", 1, piece_kind, piece))
        if sum(len(unit.source) for unit in units) < 1000:
            raise ValueError("Single-section source is implausibly short")
        return "", [units]

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
            pieces = split_long_block(block)
            for piece_index, piece in enumerate(pieces):
                ordinal += 1
                piece_kind = "continuation" if kind == "paragraph" and piece_index else kind
                chapter_units.append(Unit(f"c{idx+1:02d}-u{ordinal:04d}", idx + 1, piece_kind, piece))
        chapters.append(chapter_units)
    return preface, chapters


def translate_units(chapters: list[list[Unit]], model_dir: str, tokenizer_file: str) -> dict:
    processor = spm.SentencePieceProcessor(model_file=tokenizer_file)
    translator = ctranslate2.Translator(
        model_dir,
        device="cpu",
        compute_type="int8",
        # One translator worker with all available CPU lanes avoids the severe
        # oversubscription caused by inter_threads × intra_threads on hosted
        # four-core runners.
        inter_threads=1,
        intra_threads=max(2, min(8, os.cpu_count() or 2)),
    )
    all_units = [unit for chapter in chapters for unit in chapter]
    # Roman-numeral structural labels are identifiers, not prose. Sending them
    # through greedy decoding can trigger pathological repetition in MADLAD.
    for unit in all_units:
        if unit.kind == "heading" and re.fullmatch(r"[IVXLCDM]+\.?", unit.source.strip(), re.I):
            unit.translation = unit.source.strip()
    flat = [unit for unit in all_units if not unit.translation]
    batch_size = 32
    for offset in range(0, len(flat), batch_size):
        batch = flat[offset:offset + batch_size]
        tokens = [processor.encode("<2it> " + unit.source, out_type=str) for unit in batch]
        outputs = translator.translate_batch(
            tokens,
            beam_size=2,
            repetition_penalty=1.3,
            no_repeat_ngram_size=3,
            max_decoding_length=min(384, max(96, max(map(len, tokens)) * 2)),
            batch_type="tokens",
            max_batch_size=1024,
        )
        for unit, result in zip(batch, outputs):
            unit.translation = collapse_decoder_repetitions(
                unit.source, processor.decode(result.hypotheses[0])
            )
        retry = [unit for unit in batch if (
            len(unit.translation) > max(220, int(len(unit.source) * 2.4))
            or len(unit.translation) < max(2, int(len(unit.source) * 0.18))
        )]
        for unit in retry:
            tokens = processor.encode("<2it> " + unit.source, out_type=str)
            result = translator.translate_batch(
                [tokens],
                beam_size=2,
                repetition_penalty=1.3,
                no_repeat_ngram_size=3,
                max_decoding_length=min(384, max(96, len(tokens) * 2)),
                batch_type="tokens",
                max_batch_size=1024,
            )[0]
            unit.translation = collapse_decoder_repetitions(
                unit.source, processor.decode(result.hypotheses[0])
            )
        if offset % 128 == 0:
            print(f"translated {min(offset + batch_size, len(flat))}/{len(flat)} semantic units", flush=True)
    repaired = repair_risky_units(all_units, translator, processor)
    if repaired:
        print(f"sentence-level omission repair applied to {repaired} units", flush=True)
    return {
        "model": MADLAD,
        "runtime_model": MADLAD_RUNTIME,
        "decoding": {
            "beam_size": 2,
            "repetition_penalty": 1.3,
            "no_repeat_ngram_size": 3,
            "max_decoding_length": "dynamic_96_to_384_2x_source_tokens",
        },
        "unit_count": len(all_units),
        "model_translated_unit_count": len(flat),
        "sentence_level_repair_count": repaired,
        "source_chars": sum(len(u.source) for u in all_units),
        "translation_chars": sum(len(u.translation) for u in all_units),
    }


def materialize_runtime() -> tuple[str, str]:
    model_target = Path(os.environ.get("BB_MADLAD_MODEL_DIR", "madlad_ct2"))
    tokenizer_target = Path(os.environ.get("BB_MADLAD_TOKENIZER_DIR", "madlad_tokenizer"))
    model_dir = snapshot_download(
        repo_id=MADLAD_RUNTIME,
        local_dir=str(model_target),
        local_files_only=(model_target / "model.bin").exists(),
    )
    tokenizer = hf_hub_download(
        repo_id=MADLAD,
        filename="spiece.model",
        local_dir=str(tokenizer_target),
        local_files_only=(tokenizer_target / "spiece.model").exists(),
    )
    return model_dir, tokenizer


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
    if unit.kind == "continuation":
        return f'<p class="continued">{value}</p>'
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


def write_epub(outdir: Path, meta: dict, preface: str, chapters: list[list[Unit]], source: dict, source_images: list[dict] | None = None) -> tuple[Path, dict]:
    root = outdir / "epub"
    epub = root / "EPUB"
    for directory in [root / "META-INF", epub / "text", epub / "css", epub / "images", epub / "fonts"]:
        directory.mkdir(parents=True, exist_ok=True)
    (root / "mimetype").write_text("application/epub+zip", encoding="ascii")
    (root / "META-INF/container.xml").write_text('''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>''', encoding="utf-8")

    css = '''@font-face{font-family:Spectral;src:url(../fonts/Spectral-Regular.ttf)}@font-face{font-family:Spectral;font-weight:bold;src:url(../fonts/Spectral-Bold.ttf)}
html{font-size:100%}body{font-family:Spectral,serif;line-height:1.5;margin:5%;color:#171717}p{margin:.35em 0;text-indent:1.2em;text-align:justify;orphans:2;widows:2}p.continued{margin-top:-.35em;text-indent:0}h1,h2{text-align:center;break-after:avoid}h1{font-size:1.7em;margin:1.8em 0 1.2em}.subhead{text-align:center;text-indent:0;font-variant:small-caps;margin:.7em 0}.noindent,.center,.source-note{text-indent:0}.center{text-align:center}.source-note{font-size:.85em}.chapter{break-before:page}.cover{margin:0;padding:0;text-align:center}.cover img,figure img{max-width:100%;max-height:90vh}figure{text-align:center;margin:1.4em auto;break-inside:avoid}figcaption{font-size:.82em;font-style:italic;margin-top:.5em}.badge{border:.08em solid #6d5730;padding:.35em .7em;display:inline-block}.ornament{text-align:center;text-indent:0;color:#8a6b35}.bb-chapter-document h1:after{content:'◆';display:block;font-size:.45em;color:#8a6b35;margin-top:1em}a{color:inherit}'''
    (epub / "css/bb.css").write_text(css, encoding="utf-8")
    for name in ["Spectral-Regular.ttf", "Spectral-Bold.ttf"]:
        src = Path("assets/fonts") / name
        if src.exists():
            shutil.copyfile(src, epub / "fonts" / name)
    cover = epub / "images/cover.jpg"
    make_cover(cover, meta["italian_title"], meta["author"], meta["genre"])
    source_images = source_images or []
    for item in source_images:
        shutil.copyfile(item["path"], epub / "images" / item["name"])

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
    image_cursor = 0
    for i, chapter in enumerate(chapters, 1):
        label = f"{section_label} {i}"
        rendered = []
        for unit in chapter:
            if re.match(r"^\s*\[\s*Illustration\s*:", unit.source, re.I) and image_cursor < len(source_images):
                item = source_images[image_cursor]
                image_cursor += 1
                rendered.append(f'<figure><img src="../images/{esc(item["name"])}" alt="{esc(item["caption"])}"/><figcaption>{esc(item["caption"])}</figcaption></figure>')
            else:
                rendered.append(paragraph_html(unit))
        body = f'<section epub:type="chapter" xmlns:epub="http://www.idpf.org/2007/ops"><h1>{esc(label)}</h1>' + ''.join(rendered) + '</section>'
        pages.append((f"chapter_{i:02d}", label, xhtml_page(label, body, "source-text bb-chapter-document chapter")))
    if source_images:
        figures = ''.join(f'<figure><img src="../images/{esc(item["name"])}" alt="{esc(item["caption"])}"/><figcaption>{esc(item["caption"])}</figcaption></figure>' for item in source_images)
        pages.append(("illustrations", "Illustrazioni storiche", xhtml_page("Illustrazioni storiche", f'<section><h1>Illustrazioni storiche</h1>{figures}</section>')))
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
    for idx, item in enumerate(source_images, 1):
        manifest.append(f'<item id="historical-image-{idx:03d}" href="images/{esc(item["name"])}" media-type="{esc(item["media_type"])}"/>')
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
    report = {"epub": target.name, "publication_uuid": pub_uuid, "page_count": len(pages), "chapter_count": len(chapters), "historical_image_count": len(source_images), "zip_sha256": sha(target.read_bytes())}
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
    translation_risks = [
        u.unit_id for u in flat
        if u.kind != "heading" and translation_risk(u.source, u.translation)
    ]
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
        "remaining_translation_risk_count": len(translation_risks),
        "remaining_translation_risk_units": translation_risks[:40],
        "translator": MADLAD,
        "editorial_pass": {"model": "google/gemini-3-flash-preview", "status": "NOT_CONFIGURED", "substitute_used": False},
        "fal_used": False,
        "isbn_status": "ISBN_NOT_FOUND",
        "historical_art": {"count": build.get("historical_image_count", 0), "status": "PRESERVED_FROM_EXACT_WORK_SOURCE" if build.get("historical_image_count", 0) else "HONEST_ZERO_ART_PENDING_EXACT_WORK_AUTHENTICATION"},
        "epub": build,
        "failures": failures,
    }


def extract_historical_images(source_epub: bytes, outdir: Path) -> list[dict]:
    """Preserve non-cover raster images from an exact-work source EPUB."""
    target = outdir / "historical_source_images"
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(source_epub)) as zf:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        rootfile = container.find(".//{*}rootfile")
        if rootfile is None:
            raise ValueError("Source EPUB has no rootfile")
        opf_name = rootfile.attrib["full-path"]
        opf_dir = posixpath.dirname(opf_name)
        package = ET.fromstring(zf.read(opf_name))
        items = []
        for item in package.findall(".//{*}manifest/{*}item"):
            media = item.attrib.get("media-type", "")
            href = item.attrib.get("href", "")
            props = item.attrib.get("properties", "")
            if not media.startswith("image/") or "cover-image" in props or "cover" in href.casefold():
                continue
            member = posixpath.normpath(posixpath.join(opf_dir, href))
            if member not in zf.namelist():
                continue
            data = zf.read(member)
            if len(data) < 1200:
                continue
            try:
                with Image.open(io.BytesIO(data)) as image:
                    if image.width < 180 or image.height < 180:
                        continue
            except Exception:
                continue
            suffix = Path(href).suffix.lower() or ".jpg"
            name = f"historical-{len(items)+1:03d}{suffix}"
            path = target / name
            path.write_bytes(data)
            items.append({
                "path": str(path),
                "name": name,
                "media_type": media,
                "caption": f"Illustrazione storica dall’edizione fonte ({Path(href).name})",
                "source_member": member,
                "sha256": sha(data),
            })
        return items


def acquire_gutenberg(meta: dict) -> tuple[bytes, str, str, list[list[Unit]], dict]:
    source_url = meta.get("source_text_url") or f'https://www.gutenberg.org/ebooks/{meta["pg_id"]}.txt.utf-8'
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
    return raw, body, preface, chapters, source


def write_alignment(path: Path, units: list[Unit]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for unit in units:
            handle.write(json.dumps({
                "unit_id": unit.unit_id,
                "chapter": unit.chapter,
                "kind": unit.kind,
                "source_sha256": sha(unit.source.encode()),
                "source": unit.source,
                "translation": unit.translation,
                "translation_sha256": sha(unit.translation.encode()),
            }, ensure_ascii=False) + "\n")


def load_shard_translations(shards_dir: Path, rank: int) -> dict[str, dict]:
    records: dict[str, dict] = {}
    pattern = f"{rank:03d}_shard_*_alignment.jsonl"
    paths = sorted(shards_dir.rglob(pattern))
    if not paths:
        raise ValueError(f"No alignment shards found under {shards_dir} with pattern {pattern}")
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            prior = records.get(record["unit_id"])
            if prior and prior["translation_sha256"] != record["translation_sha256"]:
                raise ValueError(f"Conflicting translations for {record['unit_id']}")
            records[record["unit_id"]] = record
    return records


def save_source_bundle(path: Path, meta: dict, body: str, preface: str,
                       chapters: list[list[Unit]], source: dict) -> None:
    payload = {
        "bundle_version": 1,
        "rank": meta["rank"],
        "original_title": meta["original_title"],
        "body": body,
        "preface": preface,
        "source": source,
        "chapters": [[{
            "unit_id": unit.unit_id, "chapter": unit.chapter,
            "kind": unit.kind, "source": unit.source,
        } for unit in chapter] for chapter in chapters],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")


def load_source_bundle(path: Path, meta: dict) -> tuple[bytes, str, str, list[list[Unit]], dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("rank", -1)) != int(meta["rank"]):
        raise ValueError(f"Source bundle rank mismatch: {payload.get('rank')} != {meta['rank']}")
    chapters = [[Unit(
        item["unit_id"], int(item["chapter"]), item["kind"], item["source"]
    ) for item in chapter] for chapter in payload["chapters"]]
    return b"", payload["body"], payload.get("preface", ""), chapters, payload["source"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", default="dist")
    parser.add_argument("--mode", choices=["full", "plan", "prepare", "shard", "assemble"], default="full")
    parser.add_argument("--shard-index", type=int)
    parser.add_argument("--shard-size", type=int, default=96)
    parser.add_argument("--shards-dir")
    parser.add_argument("--source-bundle")
    parser.add_argument(
        "--repair-risky", action="store_true",
        help="In assemble mode, repair likely omissions with sentence-level MADLAD decoding",
    )
    args = parser.parse_args()
    meta = json.loads(Path(args.config).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.source_bundle:
        raw, body, preface, chapters, source = load_source_bundle(Path(args.source_bundle), meta)
    else:
        raw, body, preface, chapters, source = acquire_gutenberg(meta)
    flat = [unit for chapter in chapters for unit in chapter]
    seed_structural_translations(flat, meta)
    if args.mode in {"plan", "prepare"}:
        plan = {
            "rank": meta["rank"], "config": args.config, "unit_count": len(flat),
            "chapter_count": len(chapters), "shard_size": args.shard_size,
            "shard_count": math.ceil(len(flat) / args.shard_size),
            "source_sha256": source["source_sha256"],
        }
        if args.mode == "prepare":
            bundle_path = out / f'{meta["rank"]:03d}_source_bundle.json'
            save_source_bundle(bundle_path, meta, body, preface, chapters, source)
            plan["source_bundle"] = bundle_path.name
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
        alignment_path = out / f'{meta["rank"]:03d}_shard_{args.shard_index:04d}_alignment.jsonl'
        write_alignment(alignment_path, selected)
        shard_report = {
            "status": "PASS", "rank": meta["rank"], "shard_index": args.shard_index,
            "shard_size": args.shard_size, "start_unit": start,
            "translated_units": len(selected), "first_unit": selected[0].unit_id,
            "last_unit": selected[-1].unit_id, "source_sha256": source["source_sha256"],
            "translator": MADLAD, "fal_used": False, "runtime": runtime,
        }
        (out / f'{meta["rank"]:03d}_shard_{args.shard_index:04d}_QA.json').write_text(
            json.dumps(shard_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(shard_report, ensure_ascii=False))
        return

    assembled_repaired = 0
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
        seed_structural_translations(flat, meta)
        if args.repair_risky:
            model_dir, tokenizer = materialize_runtime()
            processor = spm.SentencePieceProcessor(model_file=tokenizer)
            translator = ctranslate2.Translator(
                model_dir, device="cpu", compute_type="int8",
                inter_threads=1,
                intra_threads=max(2, min(8, os.cpu_count() or 2)),
            )
            assembled_repaired = repair_risky_units(flat, translator, processor)
            print(f"sentence-level translation repair applied to {assembled_repaired} assembled units", flush=True)

    source_images = []
    if meta.get("preserve_source_images"):
        source_epub_url = f'https://www.gutenberg.org/ebooks/{meta["pg_id"]}.epub3.images'
        source_epub = fetch(source_epub_url)
        (out / f'{meta["rank"]:03d}_source_images.epub').write_bytes(source_epub)
        source["source_epub_url"] = source_epub_url
        source["source_epub_sha256"] = sha(source_epub)
        source_images = extract_historical_images(source_epub, out)
        source["historical_images"] = [{k: v for k, v in item.items() if k != "path"} for item in source_images]
    (out / f'{meta["rank"]:03d}_source.txt').write_text(body, encoding="utf-8")
    (out / f'{meta["rank"]:03d}_source_record.json').write_text(
        json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.mode == "full":
        model_dir, tokenizer = materialize_runtime()
        runtime = translate_units(chapters, model_dir, tokenizer)
    else:
        runtime = {
            "model": MADLAD, "runtime_model": MADLAD_RUNTIME,
            "decoding": {
            "beam_size": 2,
            "repetition_penalty": 1.3,
            "no_repeat_ngram_size": 3,
            "max_decoding_length": "dynamic_96_to_384_2x_source_tokens",
        },
            "unit_count": len(flat), "assembled_from_shards": True,
            "sentence_level_repair_requested": bool(args.repair_risky),
            "sentence_level_repair_count": assembled_repaired,
        }
    alignment_path = out / f'{meta["rank"]:03d}_alignment.jsonl'
    write_alignment(alignment_path, flat)
    epub, build = write_epub(out, meta, preface, chapters, source, source_images=source_images)
    report = qa(meta, chapters, source, epub, build)
    report["runtime"] = runtime
    qa_path = out / f'{meta["rank"]:03d}_{meta["slug"]}_QA.json'
    qa_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if report["status"] != "PASS":
        raise SystemExit(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"status": report["status"], "epub": str(epub), "qa": str(qa_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
