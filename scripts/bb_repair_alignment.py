#!/usr/bin/env python3
"""Repair MADLAD alignment spillover, omissions, and decoder loops.

This keeps the frozen unit IDs and source hashes intact, retranslates only
flagged units with the same MADLAD-400 first-pass model, and emits an evidence
report suitable for the Boundary Bay REVIEW checkpoint.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
from pathlib import Path

import ctranslate2
import sentencepiece as spm
from huggingface_hub import hf_hub_download, snapshot_download

from bb_translate_gutenberg import (
    MADLAD,
    MADLAD_RUNTIME,
    collapse_decoder_repetitions,
    sentence_segments,
    translation_risk,
)


CYRILLIC = re.compile(r"[\u0400-\u052f]")
GREEK = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]")


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalized(value: str) -> str:
    return re.sub(r"\W+", " ", value.casefold()).strip()


def source_script_count(value: str, language: str) -> int:
    if language == "ru":
        return len(CYRILLIC.findall(value))
    if language in {"grc", "el"}:
        return len(GREEK.findall(value))
    return 0


def unchanged_similarity(source: str, target: str, kind: str) -> float:
    if kind == "heading":
        return 0.0
    left, right = normalized(source), normalized(target)
    if min(len(left), len(right)) < 18:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def flags(record: dict, language: str) -> list[str]:
    source = str(record.get("source", ""))
    target = str(record.get("translation", ""))
    kind = str(record.get("kind", "paragraph"))
    found = []
    if not target.strip():
        found.append("empty")
    if kind != "heading" and translation_risk(source, target):
        found.append("length_or_repetition")
    if source_script_count(target, language):
        found.append("source_script_spillover")
    if language in {"en", "fr", "de"} and unchanged_similarity(source, target, kind) >= 0.72:
        found.append("unchanged_source_language")
    return found


def score(source: str, target: str, kind: str, language: str) -> float:
    if not target.strip():
        return 1_000_000.0
    value = 0.0
    value += 2500.0 * source_script_count(target, language)
    if kind != "heading" and translation_risk(source, target):
        value += 1200.0
    similarity = unchanged_similarity(source, target, kind)
    if language in {"en", "fr", "de"} and similarity >= 0.72:
        value += 900.0 + 500.0 * similarity
    ratio = len(target) / max(1, len(source))
    if ratio < 0.48:
        value += (0.48 - ratio) * 1500.0
    if ratio > 1.85:
        value += (ratio - 1.85) * 900.0
    return value


def runtime() -> tuple[ctranslate2.Translator, spm.SentencePieceProcessor]:
    model_target = Path(os.environ.get("BB_MADLAD_MODEL_DIR", "madlad_ct2"))
    tokenizer_target = Path(os.environ.get("BB_MADLAD_TOKENIZER_DIR", "madlad_tokenizer"))
    model_dir = snapshot_download(
        repo_id=MADLAD_RUNTIME,
        local_dir=str(model_target),
        local_files_only=(model_target / "model.bin").exists(),
    )
    tokenizer_file = hf_hub_download(
        repo_id=MADLAD,
        filename="spiece.model",
        local_dir=str(tokenizer_target),
        local_files_only=(tokenizer_target / "spiece.model").exists(),
    )
    processor = spm.SentencePieceProcessor(model_file=tokenizer_file)
    translator = ctranslate2.Translator(
        model_dir,
        device="cpu",
        compute_type="int8",
        inter_threads=1,
        intra_threads=max(2, min(8, os.cpu_count() or 2)),
    )
    return translator, processor


def translate_segments(
    translator: ctranslate2.Translator,
    processor: spm.SentencePieceProcessor,
    source: str,
    beam_size: int,
    penalized: bool,
) -> str:
    segments = sentence_segments(source) or [source]
    encoded = [processor.encode("<2it> " + part, out_type=str) for part in segments]
    kwargs = {
        "beam_size": beam_size,
        "max_decoding_length": min(512, max(96, max(map(len, encoded)) * 2)),
        "batch_type": "tokens",
        "max_batch_size": 1024,
    }
    if penalized:
        kwargs.update(repetition_penalty=1.18, no_repeat_ngram_size=3)
    outputs = translator.translate_batch(encoded, **kwargs)
    value = " ".join(processor.decode(item.hypotheses[0]).strip() for item in outputs)
    return collapse_decoder_repetitions(source, value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alignment", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    source_path = Path(args.alignment)
    records = [json.loads(line) for line in source_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    before = {r["unit_id"]: flags(r, args.language) for r in records}
    before = {key: value for key, value in before.items() if value}
    translator, processor = runtime()
    changed = []

    for index, record in enumerate(records, 1):
        initial_flags = flags(record, args.language)
        cleaned = collapse_decoder_repetitions(record["source"], record.get("translation", ""))
        candidates = [record.get("translation", ""), cleaned]
        if initial_flags:
            candidates.append(translate_segments(translator, processor, record["source"], 1, False))
            candidates.append(translate_segments(translator, processor, record["source"], 4, True))
        best = min(
            (candidate for candidate in candidates if candidate.strip()),
            key=lambda value: score(record["source"], value, record.get("kind", "paragraph"), args.language),
        )
        if best != record.get("translation", ""):
            changed.append(record["unit_id"])
            record["translation"] = best
            record["translation_sha256"] = sha_text(best)
        if index % 100 == 0:
            print(f"reviewed {index}/{len(records)} units; changed={len(changed)}", flush=True)

    after = {r["unit_id"]: flags(r, args.language) for r in records}
    after = {key: value for key, value in after.items() if value}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
        encoding="utf-8",
    )
    report = {
        "status": "PASS" if not after else "REVIEW_REQUIRED",
        "translator": MADLAD,
        "runtime_model": MADLAD_RUNTIME,
        "language": args.language,
        "unit_count": len(records),
        "initial_flagged_unit_count": len(before),
        "changed_unit_count": len(changed),
        "remaining_flagged_unit_count": len(after),
        "remaining_flags": dict(list(after.items())[:100]),
        "input_alignment_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "output_alignment_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
