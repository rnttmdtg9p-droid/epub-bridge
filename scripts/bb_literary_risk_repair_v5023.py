#!/usr/bin/env python3
"""Source-bound automated repair pass for BB Master v5.0.23.

This script replaces only mechanically evidenced high-risk units.  It emits an
immutable correction ledger and never labels its output as human literary
review.  Existing source-bound Marian corrections are applied before candidate
selection so this pass does not regress already-repaired text.
"""

from __future__ import annotations

import argparse
import base64
import collections
import gzip
import hashlib
import html
import json
import re
import unicodedata
from pathlib import Path


MODEL_BY_LANGUAGE = {
    "en": ["Helsinki-NLP/opus-mt-en-it"],
    "fr": ["Helsinki-NLP/opus-mt-fr-it"],
    "de": ["Helsinki-NLP/opus-mt-de-it"],
    "ru": ["Helsinki-NLP/opus-mt-ru-en", "Helsinki-NLP/opus-mt-en-it"],
}

WORD_RE = re.compile(
    r"[0-9A-Za-zÀ-ÖØ-öø-ÿĀ-žΑ-ωΆ-ώА-яЁё]+(?:['’][0-9A-Za-zÀ-ÖØ-öø-ÿĀ-žΑ-ωΆ-ώА-яЁё]+)?",
    re.UNICODE,
)
END_MARKERS = {"the end", "end", "finis", "fine", "la fine", "ende", "конец", "τέλος"}
IT_FUNCTION = {
    "il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "e", "o", "ma", "se", "che",
    "questo", "questa", "questi", "queste", "io", "tu", "lui", "lei", "noi", "voi", "loro",
    "mi", "ti", "si", "ci", "vi", "mio", "mia", "tuo", "tua", "suo", "sua", "nostro",
    "vostro", "è", "sono", "era", "erano", "essere", "stato", "stata", "ho", "hai", "ha",
    "hanno", "aveva", "avevano", "fare", "fa", "non", "no", "mai", "di", "a", "da", "in",
    "su", "per", "con", "senza", "come", "tra", "fra", "prima", "dopo", "quando", "dove",
    "chi", "quale", "cosa", "perché", "così", "molto", "più", "alcuni", "ogni", "altro",
    "altra", "qui", "lì", "solo", "della", "delle", "degli", "del", "dei", "alla", "alle",
    "allo", "agli", "nel", "nella", "nelle", "nello", "nei", "dal", "dalla", "dalle", "dallo",
    "dai", "sul", "sulla", "sulle", "sullo", "sui", "anche", "ancora", "quella", "quello",
    "mentre", "verso", "tutto", "tutti", "tutte", "dovrebbe", "potrebbe", "avevo", "abbiamo",
}
SOURCE_FUNCTION = {
    "en": {
        "the", "a", "an", "and", "or", "but", "if", "then", "than", "that", "this", "these",
        "those", "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us",
        "them", "my", "your", "his", "its", "our", "their", "is", "am", "are", "was", "were",
        "be", "been", "being", "have", "has", "had", "do", "does", "did", "will", "would",
        "shall", "should", "can", "could", "may", "might", "must", "not", "no", "never", "of",
        "to", "in", "on", "at", "by", "for", "from", "with", "without", "as", "into", "over",
        "under", "up", "down", "out", "about", "before", "after", "when", "where", "who", "which",
        "what", "why", "how", "so", "very", "more", "most", "some", "any", "all", "each", "every",
    },
    "fr": {
        "le", "la", "les", "un", "une", "des", "du", "de", "et", "ou", "mais", "si", "que",
        "qui", "je", "tu", "il", "elle", "nous", "vous", "ils", "elles", "me", "te", "se", "ce",
        "cette", "ces", "mon", "ma", "mes", "son", "sa", "ses", "notre", "votre", "leur", "est",
        "sont", "était", "étaient", "être", "avoir", "ai", "a", "ont", "avait", "avaient", "ne",
        "pas", "jamais", "rien", "personne", "dans", "sur", "sous", "avec", "sans", "pour", "par",
        "avant", "après", "quand", "où", "comme", "plus", "moins", "tout", "tous", "toute", "toutes",
    },
    "de": {
        "der", "die", "das", "ein", "eine", "einer", "eines", "und", "oder", "aber", "wenn", "dass",
        "ich", "du", "er", "sie", "es", "wir", "ihr", "mich", "dich", "ihn", "uns", "mein", "dein",
        "sein", "unser", "euer", "ist", "sind", "war", "waren", "haben", "hat", "hatte", "nicht",
        "kein", "keine", "keinen", "niemals", "nie", "nichts", "niemand", "von", "zu", "in", "auf",
        "an", "bei", "für", "mit", "ohne", "als", "über", "unter", "vor", "nach", "wo", "wer", "was",
    },
    "ru": {
        "и", "а", "но", "или", "если", "что", "это", "этот", "эта", "я", "ты", "он", "она", "оно",
        "мы", "вы", "они", "мне", "тебе", "его", "ее", "наш", "ваш", "их", "есть", "был", "была",
        "были", "быть", "иметь", "не", "нет", "никогда", "ни", "из", "в", "на", "к", "по", "для",
        "с", "без", "как", "над", "под", "до", "после", "когда", "где", "кто", "который", "почему",
    },
}
BOILERPLATE = [
    re.compile(p, re.I)
    for p in (
        r"project gutenberg", r"gutenberg license", r"grosset\s*&\s*dunlap", r"more to follow",
        r"other side of the wrapper", r"wherever books are sold", r"publishers?,?\s+new york",
    )
]
MODERN = re.compile(
    r"\b(?:film|cinema|televisione|tv|streaming|podcast|internet|website|sito web|isbn|edizione digitale)\b",
    re.I,
)


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def norm_space(value: str) -> str:
    value = unicodedata.normalize("NFKC", html.unescape(value or ""))
    value = value.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", value).strip()


def words(value: str) -> list[str]:
    return [match.group(0).casefold().replace("’", "'") for match in WORD_RE.finditer(norm_space(value))]


def repeated_adjacent(ts: list[str]) -> dict | None:
    best = None
    for size in range(1, min(32, len(ts) // 2) + 1):
        for start in range(len(ts) - 2 * size + 1):
            block = ts[start : start + size]
            count = 1
            pos = start + size
            while pos + size <= len(ts) and ts[pos : pos + size] == block:
                count += 1
                pos += size
            if count < 2 or (size == 1 and count < 3):
                continue
            item = {"token_count": size, "repeat_count": count, "score": size * count, "phrase": " ".join(block)}
            if best is None or (item["score"], size, count) > (best["score"], best["token_count"], best["repeat_count"]):
                best = item
    return best


def duplicate_half(src: list[str], tgt: list[str]) -> bool:
    if len(tgt) < 4 or len(tgt) % 2:
        return False
    half = len(tgt) // 2
    if tgt[:half] != tgt[half:]:
        return False
    return not (len(src) % 2 == 0 and src[: len(src) // 2] == src[len(src) // 2 :])


def decoder_loop(value: str) -> bool:
    return bool(
        re.search(r"([^\w\s])\1{5,}", value)
        or re.search(r"(?i)([a-zà-öø-ÿ]{2,15})\1{3,}", value)
        or re.search(r"(?i)\b([a-zà-öø-ÿ]{2,25})(?:[.\s_-]+\1){3,}\b", value)
    )


def source_residue(tgt: list[str], language: str, value: str) -> bool:
    if re.search(r"[А-Яа-яЁё]", value) or re.search(r"[Α-ωΆ-ώ]", value):
        return True
    src_words = [word for word in tgt if word in SOURCE_FUNCTION[language] and word not in IT_FUNCTION]
    it_words = [word for word in tgt if word in IT_FUNCTION]
    return len(src_words) >= 4 and len(src_words) > len(it_words)


def source_copy(src: list[str], tgt: list[str], language: str) -> bool:
    if len(src) < 5 or len(tgt) < 5:
        return False
    for size in range(min(18, len(src), len(tgt)), 4, -1):
        target_grams = {tuple(tgt[i : i + size]) for i in range(len(tgt) - size + 1)}
        for index in range(len(src) - size + 1):
            gram = tuple(src[index : index + size])
            if gram in target_grams and sum(word in SOURCE_FUNCTION[language] for word in gram) >= 2:
                return True
    return False


def candidate_reasons(record: dict, language: str) -> list[str]:
    source = str(record.get("source", ""))
    target = str(record.get("translation", ""))
    src = words(source)
    tgt = words(target)
    reasons: list[str] = []
    if not target.strip():
        reasons.append("missing_translation")
    if decoder_loop(target):
        reasons.append("decoder_loop")
    if source_residue(tgt, language, target):
        reasons.append("source_language_residue")
    if duplicate_half(src, tgt):
        reasons.append("exact_duplicate_half")
    target_repeat = repeated_adjacent(tgt)
    source_repeat = repeated_adjacent(src)
    if target_repeat:
        source_count = source_repeat["repeat_count"] if source_repeat else 1
        source_score = source_repeat["score"] if source_repeat else 0
        unmatched = target_repeat["repeat_count"] > source_count or target_repeat["score"] >= source_score + 4
        strong = target_repeat["repeat_count"] >= 3 or target_repeat["token_count"] >= 4
        if unmatched and strong:
            reasons.append("strong_source_unmatched_repetition")
    if source_copy(src, tgt, language):
        reasons.append("source_language_copy")
    if len(src) >= 8:
        ratio = len(tgt) / max(1, len(src))
        if ratio > 2.5 or ratio < 0.35:
            reasons.append("severe_length_anomaly")
    if MODERN.search(target) and not MODERN.search(source):
        reasons.append("modern_intrusion")
    return reasons


def after_end_indexes(rows: list[dict]) -> set[int]:
    markers = [index for index, row in enumerate(rows) if " ".join(words(str(row.get("source", "")))) in END_MARKERS]
    if not markers:
        return set()
    marker = markers[-1]
    tail = rows[marker + 1 :]
    has_cue = any(any(pattern.search(str(row.get(field, ""))) for pattern in BOILERPLATE for field in ("source", "translation")) for row in tail)
    return set(range(marker + 1, len(rows))) if marker >= 100 and tail and has_cue else set()


def read_existing_corrections(root: Path) -> dict[str, dict[str, dict]]:
    merged: dict[str, dict[str, dict]] = collections.defaultdict(dict)
    for path in root.rglob("corrections-*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for rank, values in payload.get("corrections", {}).items():
            merged[rank].update(values)
    return merged


def read_alignments(root: Path, ranks: set[str], existing: dict[str, dict[str, dict]]) -> dict[str, list[dict]]:
    selected: dict[str, list[dict]] = {}
    for path in root.rglob("*_alignment.jsonl"):
        match = re.search(r"(?:^|/)(\d{3})_alignment\.jsonl$", path.as_posix())
        if not match or match.group(1) not in ranks:
            continue
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        rank = match.group(1)
        if rank not in selected or len(rows) > len(selected[rank]):
            selected[rank] = rows
    for rank, rows in selected.items():
        by_id = {str(row["unit_id"]): row for row in rows}
        for unit_id, correction in existing.get(rank, {}).items():
            row = by_id.get(unit_id)
            if row is None or row.get("source_sha256") != correction.get("source_sha256"):
                continue
            row["translation"] = correction["translation"]
            row["translation_sha256"] = correction["translation_sha256"]
    return selected


def read_fallback(path: Path, ranks: set[str], language: str) -> dict[str, list[dict]]:
    raw = gzip.decompress(base64.b64decode(path.read_bytes()))
    payload = json.loads(raw)
    found: dict[str, list[dict]] = collections.defaultdict(list)
    for item in payload["records"]:
        if item["rank"] not in ranks or item["source_language"] != language:
            continue
        found[item["rank"]].append(
            {
                "unit_id": item["unit_id"],
                "chapter": item["chapter"],
                "kind": item["kind"],
                "source": item["source"],
                "source_sha256": item["source_sha256"],
                "translation": item["old_translation"],
                "translation_sha256": item["old_translation_sha256"],
                "fallback_audit_reasons": item["audit_reasons"],
            }
        )
    return found


def source_segments(value: str, max_chars: int = 700) -> list[str]:
    value = re.sub(r"[ \t]+", " ", value.strip())
    if len(value) <= max_chars:
        return [value]
    rough = [piece.strip() for piece in re.split(r"(?<=[.!?…])\s+|\n+", value) if piece.strip()]
    chunks: list[str] = []
    current = ""
    for part in rough:
        pieces = [part]
        if len(part) > max_chars:
            pieces = []
            words_in_part = part.split()
            fragment = ""
            for word in words_in_part:
                candidate = f"{fragment} {word}".strip()
                if fragment and len(candidate) > max_chars:
                    pieces.append(fragment)
                    fragment = word
                else:
                    fragment = candidate
            if fragment:
                pieces.append(fragment)
        for piece in pieces:
            candidate = f"{current} {piece}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def translate_batch(texts: list[str], model_id: str) -> list[str]:
    import torch
    from transformers import MarianMTModel, MarianTokenizer

    tokenizer = MarianTokenizer.from_pretrained(model_id)
    model = MarianMTModel.from_pretrained(model_id)
    model.eval()
    ordered = sorted(enumerate(texts), key=lambda row: len(row[1]))
    translated = [""] * len(texts)
    for offset in range(0, len(ordered), 12):
        batch = ordered[offset : offset + 12]
        encoded = tokenizer(
            [row[1] for row in batch], return_tensors="pt", padding=True, truncation=True, max_length=480
        )
        longest = int(encoded["attention_mask"].sum(dim=1).max().item())
        with torch.inference_mode():
            output = model.generate(
                **encoded,
                num_beams=5,
                max_new_tokens=min(480, max(24, int(longest * 1.8) + 24)),
                no_repeat_ngram_size=3,
                repetition_penalty=1.22,
                length_penalty=1.0,
                early_stopping=True,
            )
        decoded = tokenizer.batch_decode(output, skip_special_tokens=True)
        for (original_index, _), value in zip(batch, decoded):
            translated[original_index] = re.sub(r"\s+", " ", value).strip()
        print(f"{model_id}: {min(offset + len(batch), len(ordered))}/{len(ordered)} segments", flush=True)
    del model
    return translated


def translate_records(records: list[dict], models: list[str]) -> list[str]:
    segments: list[tuple[int, int, str]] = []
    assembled: list[list[str]] = []
    for record_index, record in enumerate(records):
        pieces = source_segments(record["source"])
        assembled.append([""] * len(pieces))
        segments.extend((record_index, piece_index, piece) for piece_index, piece in enumerate(pieces))
    values = [row[2] for row in segments]
    for model in models:
        values = translate_batch(values, model)
    for (record_index, piece_index, _), value in zip(segments, values):
        assembled[record_index][piece_index] = value
    return [" ".join(pieces).strip() for pieces in assembled]


def post_failures(source: str, target: str, language: str) -> list[str]:
    src = words(source)
    tgt = words(target)
    failures: list[str] = []
    if not target.strip():
        failures.append("empty_target")
    if decoder_loop(target):
        failures.append("decoder_loop")
    if source_residue(tgt, language, target):
        failures.append("source_language_residue")
    if duplicate_half(src, tgt):
        failures.append("exact_duplicate_half")
    if len(src) >= 8:
        ratio = len(tgt) / max(1, len(src))
        if ratio > 2.5 or ratio < 0.35:
            failures.append("severe_length_anomaly")
    if MODERN.search(target) and not MODERN.search(source):
        failures.append("modern_intrusion")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--fallback", required=True)
    parser.add_argument("--existing-corrections", required=True)
    parser.add_argument("--language", choices=sorted(MODEL_BY_LANGUAGE), required=True)
    parser.add_argument("--ranks", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    language = args.language
    ranks = {value.zfill(3) for value in args.ranks.split(",") if value.strip()}
    existing = read_existing_corrections(Path(args.existing_corrections))
    alignments = read_alignments(Path(args.inputs), ranks, existing)
    fallback = read_fallback(Path(args.fallback), ranks, language)
    candidates: list[dict] = []
    preflight: dict[str, dict] = {}
    for rank in sorted(ranks):
        rows = alignments.get(rank)
        fallback_used = False
        if rows is None:
            rows = fallback.get(rank)
            fallback_used = True
        if not rows:
            raise SystemExit(f"No source-bound records for rank {rank}")
        skip = after_end_indexes(rows) if not fallback_used else set()
        selected = []
        for index, record in enumerate(rows):
            if index in skip:
                continue
            reasons = (
                sorted(record.get("fallback_audit_reasons", {}).keys())
                if fallback_used
                else candidate_reasons(record, language)
            )
            if not reasons:
                continue
            item = dict(record)
            item["rank"] = rank
            item["pre_flags"] = reasons
            candidates.append(item)
            selected.append({"unit_id": item["unit_id"], "flags": reasons})
        preflight[rank] = {
            "input_unit_count": len(rows),
            "fallback_candidate_payload_used": fallback_used,
            "selected_unit_count": len(selected),
            "flag_counts": dict(collections.Counter(flag for item in selected for flag in item["flags"])),
        }

    models = MODEL_BY_LANGUAGE[language]
    translations = translate_records(candidates, models) if candidates else []
    corrections: dict[str, dict] = {rank: {} for rank in sorted(ranks)}
    rejected: list[dict] = []
    unchanged = 0
    for record, target in zip(candidates, translations):
        failures = post_failures(record["source"], target, language)
        if failures:
            rejected.append({"rank": record["rank"], "unit_id": record["unit_id"], "failures": failures})
            continue
        if norm_space(target) == norm_space(record.get("translation", "")):
            unchanged += 1
            continue
        corrections[record["rank"]][record["unit_id"]] = {
            "chapter": record["chapter"],
            "kind": record.get("kind"),
            "source": record["source"],
            "source_sha256": record.get("source_sha256") or sha_text(record["source"]),
            "old_translation": record.get("translation", ""),
            "old_translation_sha256": record.get("translation_sha256") or sha_text(record.get("translation", "")),
            "translation": target,
            "translation_sha256": sha_text(target),
            "pre_flags": record["pre_flags"],
            "post_flags": [],
            "model": " -> ".join(models),
            "editorial_status": "AUTOMATED_EDITORIAL_PASS_REVIEW_REQUIRED",
        }

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "bb-v5.0.23-source-bound-corrections-v1",
        "language": language,
        "models": models,
        "scope": "mechanically evidenced high-risk units only",
        "editorial_status": "AUTOMATED_EDITORIAL_PASS_REVIEW_REQUIRED",
        "corrections": corrections,
    }
    (out / f"corrections-v5023-{language}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    replacement_count = sum(len(values) for values in corrections.values())
    qa = {
        "status": "PASS_WITH_REVIEW" if not rejected else "REVIEW_REQUIRED_WITH_REJECTED_OUTPUTS",
        "language": language,
        "models": models,
        "preflight": preflight,
        "selected_unit_count": len(candidates),
        "replacement_count": replacement_count,
        "unchanged_count": unchanged,
        "rejected_output_count": len(rejected),
        "rejected_outputs": rejected,
        "release_state": "HOLD",
        "release_blockers": [
            "Complete qualified human Italian literary review against the original is not evidenced",
            "Exact-candidate intended-reader validation is not evidenced",
        ],
    }
    (out / f"qa-v5023-{language}.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": qa["status"], "selected": len(candidates), "replacements": replacement_count, "rejected": len(rejected)}))


if __name__ == "__main__":
    main()
