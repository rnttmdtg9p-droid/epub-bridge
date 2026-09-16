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


def repeated_target_span(target: str) -> bool:
    """Detect exact or near-exact repeated target sentences, not ordinary expansion."""
    segments = sentence_segments(target)
    seen: list[str] = []
    for segment in segments:
        value = normalized(segment)
        if len(value) < 35:
            continue
        if any(
            value == prior or difflib.SequenceMatcher(None, value, prior).ratio() >= 0.93
            for prior in seen
        ):
            return True
        seen.append(value)
    return False


def hard_flags(record: dict, language: str) -> list[str]:
    """Release-blocking MT faults; heuristic similarity/length stays review evidence."""
    source = str(record.get("source", ""))
    target = str(record.get("translation", ""))
    found: list[str] = []
    if not target.strip():
        found.append("empty")
    if source_script_count(target, language):
        found.append("source_script_spillover")
    ratio = len(target) / max(1, len(source))
    if len(source) >= 90 and (ratio < 0.30 or ratio > 2.75):
        found.append("severe_length_anomaly")
    if repeated_target_span(target):
        found.append("decoder_repetition")
    return found


def atomic_segments(source: str) -> list[str]:
    """Split difficult units into bounded clauses for a genuinely different decode."""
    out: list[str] = []
    for sentence in sentence_segments(source) or [source]:
        clauses = [
            part.strip()
            for part in re.split(r"(?<=[,;:!?…—–])\s+", sentence)
            if part.strip()
        ]
        for clause in clauses or [sentence]:
            if len(clause) <= 180:
                out.append(clause)
                continue
            words = clause.split()
            chunk: list[str] = []
            size = 0
            for word in words:
                if chunk and size + 1 + len(word) > 150:
                    out.append(" ".join(chunk))
                    chunk, size = [], 0
                chunk.append(word)
                size += len(word) + (1 if size else 0)
            if chunk:
                out.append(" ".join(chunk))
    return out


def bounded_segments(source: str, limit: int) -> list[str]:
    """Split source into short, punctuation-aware pieces for hard-fault retries."""
    pieces: list[str] = []
    pending = ""
    for token in re.split(r"(\s+)", source):
        if not token:
            continue
        if pending and len(pending) + len(token) > limit:
            pieces.append(pending.strip())
            pending = ""
        pending += token
    if pending.strip():
        pieces.append(pending.strip())
    return pieces or [source]


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


def translate_parts(
    translator: ctranslate2.Translator,
    processor: spm.SentencePieceProcessor,
    source: str,
    parts: list[str],
    beam_size: int,
    repetition_penalty: float = 1.0,
) -> str:
    """Translate supplied parts directly so retry segmentation is preserved."""
    encoded = [processor.encode("<2it> " + part, out_type=str) for part in parts]
    kwargs = {
        "beam_size": beam_size,
        "max_decoding_length": min(512, max(96, max(map(len, encoded)) * 2)),
        "batch_type": "tokens",
        "max_batch_size": 1024,
    }
    if repetition_penalty > 1.0:
        kwargs.update(
            repetition_penalty=repetition_penalty,
            no_repeat_ngram_size=3,
        )
    outputs = translator.translate_batch(encoded, **kwargs)
    value = " ".join(processor.decode(item.hypotheses[0]).strip() for item in outputs)
    return collapse_decoder_repetitions(source, value)


def translate_segments(
    translator: ctranslate2.Translator,
    processor: spm.SentencePieceProcessor,
    source: str,
    beam_size: int,
    penalized: bool,
) -> str:
    return translate_parts(
        translator,
        processor,
        source,
        sentence_segments(source) or [source],
        beam_size,
        1.18 if penalized else 1.0,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alignment", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--hard-only", action="store_true", help="Retranslate only release-blocking units")
    args = parser.parse_args()

    source_path = Path(args.alignment)
    records = [json.loads(line) for line in source_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    before = {r["unit_id"]: flags(r, args.language) for r in records}
    before = {key: value for key, value in before.items() if value}
    translator, processor = runtime()
    changed = []

    for index, record in enumerate(records, 1):
        initial_flags = hard_flags(record, args.language) if args.hard_only else flags(record, args.language)
        cleaned = collapse_decoder_repetitions(record["source"], record.get("translation", ""))
        candidates = [record.get("translation", ""), cleaned]
        if initial_flags:
            candidates.append(translate_segments(translator, processor, record["source"], 1, False))
            candidates.append(translate_segments(translator, processor, record["source"], 4, True))
            atomic = atomic_segments(record["source"])
            if atomic != (sentence_segments(record["source"]) or [record["source"]]):
                candidates.append(translate_parts(
                    translator, processor, record["source"], atomic, 2, 1.25
                ))
                candidates.append(translate_parts(
                    translator, processor, record["source"], atomic, 6, 1.35
                ))

            # Escalate only release-blocking units. Short independent decodes avoid
            # carrying source-script spillover and decoder loops across clauses.
            if not any(
                not hard_flags(
                    {"source": record["source"], "translation": candidate,
                     "kind": record.get("kind", "paragraph")},
                    args.language,
                )
                for candidate in candidates if candidate.strip()
            ):
                for limit, beam, penalty in ((110, 3, 1.25), (70, 5, 1.35), (42, 8, 1.45), (28, 10, 1.55), (18, 12, 1.65)):
                    candidates.append(translate_parts(
                        translator, processor, record["source"],
                        bounded_segments(record["source"], limit), beam, penalty
                    ))

        valid_candidates = [candidate for candidate in candidates if candidate.strip()]
        hard_clear = [
            candidate for candidate in valid_candidates
            if not hard_flags(
                {"source": record["source"], "translation": candidate,
                 "kind": record.get("kind", "paragraph")},
                args.language,
            )
        ]
        best = min(
            hard_clear or valid_candidates,
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
    hard_after = {r["unit_id"]: hard_flags(r, args.language) for r in records}
    hard_after = {key: value for key, value in hard_after.items() if value}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
        encoding="utf-8",
    )
    report = {
        "status": "FAIL" if hard_after else ("PASS" if not after else "REVIEW_REQUIRED"),
        "translator": MADLAD,
        "runtime_model": MADLAD_RUNTIME,
        "language": args.language,
        "unit_count": len(records),
        "initial_flagged_unit_count": len(before),
        "changed_unit_count": len(changed),
        "remaining_flagged_unit_count": len(after),
        "remaining_flags": dict(list(after.items())[:100]),
        "remaining_hard_flagged_unit_count": len(hard_after),
        "remaining_hard_flags": dict(list(hard_after.items())[:100]),
        "input_alignment_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "output_alignment_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
