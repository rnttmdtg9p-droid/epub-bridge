#!/usr/bin/env python3
"""Source-bound Marian repair for blatant MT failures in BB alignment ledgers.

The script never changes an unflagged unit.  Every replacement is bound to the
source SHA-256 and the output remains a REVIEW candidate pending complete human
literary review under BB Master SRC-14/SRC-15.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


MODEL_BY_LANGUAGE = {
    "en": ["Helsinki-NLP/opus-mt-en-it"],
    # OPUS-MT does not publish a direct ru-it checkpoint.  Use the two
    # documented public Marian checkpoints and retain the full provenance.
    "ru": ["Helsinki-NLP/opus-mt-ru-en", "Helsinki-NLP/opus-mt-en-it"],
}

REPEAT = re.compile(
    r"(?i)\b([a-zà-ÿ]{3,})(?:[\s,.;:!?—-]+\1){4,}\b"
)
CYRILLIC = re.compile(r"[\u0400-\u04ff]")
GREEK = re.compile(r"[\u0370-\u03ff]")
LONG_DIGITS = re.compile(r"\d{7,}")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hard_flags(record: dict) -> list[str]:
    source = record.get("source", "")
    target = record.get("translation", "")
    flags: list[str] = []
    if not target.strip():
        flags.append("empty_target")
    if CYRILLIC.search(target):
        flags.append("cyrillic_spillover")
    if GREEK.search(target):
        flags.append("greek_spillover")
    if LONG_DIGITS.search(target):
        flags.append("long_digit_run")
    if REPEAT.search(target):
        flags.append("decoder_repetition")
    if len(source.strip()) >= 20:
        ratio = len(target.strip()) / max(1, len(source.strip()))
        if ratio < 0.18:
            flags.append("severe_underlength")
        elif ratio > 2.40:
            flags.append("severe_overlength")
    return flags


def read_alignments(root: Path, ranks: set[str]) -> dict[str, list[dict]]:
    selected: dict[str, list[dict]] = {}
    for path in root.rglob("*_alignment.jsonl"):
        match = re.search(r"(?:^|/)(\d{3})_alignment\.jsonl$", path.as_posix())
        if not match or match.group(1) not in ranks:
            continue
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        rank = match.group(1)
        if rank not in selected or len(rows) > len(selected[rank]):
            selected[rank] = rows
    missing = ranks - set(selected)
    if missing:
        raise SystemExit(f"Missing complete alignment ledger(s): {sorted(missing)}")
    return selected


def source_segments(value: str, max_chars: int = 760) -> list[str]:
    value = re.sub(r"[ \t]+", " ", value.strip())
    if len(value) <= max_chars:
        return [value]
    rough = [x.strip() for x in re.split(r"(?<=[.!?…])\s+|\n+", value) if x.strip()]
    chunks: list[str] = []
    current = ""
    for part in rough:
        if len(part) > max_chars:
            words = part.split()
            for word in words:
                candidate = f"{current} {word}".strip()
                if current and len(candidate) > max_chars:
                    chunks.append(current)
                    current = word
                else:
                    current = candidate
            continue
        candidate = f"{current} {part}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = part
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

    translated_all: list[str] = []
    ordered = sorted(enumerate(texts), key=lambda row: len(row[1]))
    translated_by_index = [""] * len(texts)
    for offset in range(0, len(ordered), 12):
        batch = ordered[offset:offset + 12]
        batch_text = [row[1] for row in batch]
        encoded = tokenizer(batch_text, return_tensors="pt", padding=True, truncation=True, max_length=480)
        longest = int(encoded["attention_mask"].sum(dim=1).max().item())
        with torch.inference_mode():
            output = model.generate(
                **encoded,
                num_beams=4,
                max_new_tokens=min(480, max(24, int(longest * 1.75) + 20)),
                no_repeat_ngram_size=3,
                repetition_penalty=1.18,
                length_penalty=1.0,
                early_stopping=True,
            )
        decoded = tokenizer.batch_decode(output, skip_special_tokens=True)
        for (original_index, _), target in zip(batch, decoded):
            translated_by_index[original_index] = re.sub(r"\s+", " ", target).strip()
        print(f"{model_id}: translated {min(offset + len(batch), len(ordered))}/{len(ordered)} segments", flush=True)
    del model
    return translated_by_index


def translate_records(records: list[dict], model_ids: list[str]) -> list[str]:

    segments: list[tuple[int, int, str]] = []
    by_record: list[list[str]] = []
    for record_index, record in enumerate(records):
        pieces = source_segments(record["source"])
        by_record.append([""] * len(pieces))
        for piece_index, piece in enumerate(pieces):
            segments.append((record_index, piece_index, piece))

    translated_segments = [row[2] for row in segments]
    for model_id in model_ids:
        translated_segments = translate_batch(translated_segments, model_id)
    for (record_index, piece_index, _), target in zip(segments, translated_segments):
        by_record[record_index][piece_index] = target
    return [" ".join(pieces).strip() for pieces in by_record]


def repair(args: argparse.Namespace) -> None:
    ranks = {value.zfill(3) for value in args.ranks.split(",") if value.strip()}
    ledgers = read_alignments(Path(args.inputs), ranks)
    candidates: list[dict] = []
    preflight: dict[str, dict] = {}
    for rank in sorted(ledgers):
        flagged = []
        for record in ledgers[rank]:
            flags = hard_flags(record)
            if flags:
                item = dict(record)
                item["rank"] = rank
                item["pre_flags"] = flags
                candidates.append(item)
                flagged.append({"unit_id": item["unit_id"], "flags": flags})
        preflight[rank] = {
            "alignment_units": len(ledgers[rank]),
            "flagged_units": len(flagged),
            "flag_counts": dict(Counter(flag for row in flagged for flag in row["flags"])),
            "items": flagged,
        }

    model_ids = MODEL_BY_LANGUAGE[args.language]
    model_provenance = " -> ".join(model_ids)
    translations = translate_records(candidates, model_ids) if candidates else []
    corrections: dict[str, dict] = {rank: {} for rank in sorted(ranks)}
    failures = []
    for record, translation in zip(candidates, translations):
        repaired = dict(record)
        repaired["translation"] = translation
        post_flags = hard_flags(repaired)
        correction = {
            "chapter": int(record["chapter"]),
            "kind": record["kind"],
            "source_sha256": record.get("source_sha256") or sha256_text(record["source"]),
            "source": record["source"],
            "old_translation_sha256": record.get("translation_sha256") or sha256_text(record["translation"]),
            "translation": translation,
            "translation_sha256": sha256_text(translation),
            "pre_flags": record["pre_flags"],
            "post_flags": post_flags,
            "model": model_provenance,
            "editorial_status": "REVIEW_REQUIRED",
        }
        corrections[record["rank"]][record["unit_id"]] = correction
        if post_flags:
            failures.append({"rank": record["rank"], "unit_id": record["unit_id"], "flags": post_flags})

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "bb-targeted-source-bound-corrections-v1",
        "language": args.language,
        "model": model_provenance,
        "scope": "blatant hard flags and severe length anomalies only",
        "editorial_status": "REVIEW_REQUIRED",
        "corrections": corrections,
    }
    (out / f"corrections-{args.language}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    qa = {
        "status": "PASS" if not failures else "FAIL",
        "language": args.language,
        "model": model_provenance,
        "preflight": preflight,
        "replacement_count": sum(len(value) for value in corrections.values()),
        "remaining_hard_failures": failures,
        "release_decision": "REVIEW_REQUIRED",
        "release_blockers": [
            "Complete qualified human Italian literary review against the original is not evidenced",
            "Exact-candidate intended-reader validation is not evidenced",
        ],
    }
    (out / f"qa-{args.language}.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": qa["status"], "replacement_count": qa["replacement_count"], "failures": len(failures)}))
    if failures:
        raise SystemExit(1)


def merge(args: argparse.Namespace) -> None:
    sources = sorted(Path(args.inputs).rglob("corrections-*.json"))
    if not sources:
        raise SystemExit("No correction files found")
    merged = {
        "schema": "bb-targeted-source-bound-corrections-v1",
        "scope": "blatant hard flags and severe length anomalies only",
        "editorial_status": "REVIEW_REQUIRED",
        "corrections": {},
        "source_files": [],
    }
    for source in sources:
        payload = json.loads(source.read_text(encoding="utf-8"))
        merged["source_files"].append(source.name)
        for rank, items in payload["corrections"].items():
            overlap = set(merged["corrections"].get(rank, {})) & set(items)
            if overlap:
                raise SystemExit(f"Duplicate corrections for {rank}: {sorted(overlap)[:5]}")
            merged["corrections"].setdefault(rank, {}).update(items)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "targeted-marian-corrections.json").write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": "PASS", "ranks": sorted(merged["corrections"]), "replacement_count": sum(len(x) for x in merged["corrections"].values())}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("repair", "merge"), default="repair")
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--language", choices=sorted(MODEL_BY_LANGUAGE))
    parser.add_argument("--ranks", default="")
    args = parser.parse_args()
    if args.mode == "repair":
        if not args.language or not args.ranks:
            parser.error("--language and --ranks are required in repair mode")
        repair(args)
    else:
        merge(args)


if __name__ == "__main__":
    main()
