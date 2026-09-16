#!/usr/bin/env python3
"""Apply source-bound editorial overrides and enforce the hard-fault gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from bb_repair_alignment import flags, hard_flags


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alignment", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--rank", required=True, type=int)
    parser.add_argument("--overrides", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--require-zero-hard", action="store_true")
    parser.add_argument("--require-all-overrides", action="store_true")
    args = parser.parse_args()

    source_path = Path(args.alignment)
    records = [
        json.loads(line)
        for line in source_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    initial_hard = {
        record["unit_id"]: hard_flags(record, args.language)
        for record in records
        if hard_flags(record, args.language)
    }

    payload = json.loads(Path(args.overrides).read_text(encoding="utf-8"))
    selected = [
        entry for entry in payload.get("overrides", [])
        if int(entry["rank"]) == args.rank
    ]
    by_unit: dict[str, dict] = {}
    for entry in selected:
        unit_id = str(entry["unit_id"])
        if unit_id in by_unit:
            raise SystemExit(f"duplicate override for rank {args.rank}: {unit_id}")
        by_unit[unit_id] = entry

    seen_ids: set[str] = set()
    applied: list[dict] = []
    for record in records:
        unit_id = str(record["unit_id"])
        if unit_id in seen_ids:
            raise SystemExit(f"duplicate alignment unit ID: {unit_id}")
        seen_ids.add(unit_id)
        entry = by_unit.get(unit_id)
        if not entry:
            continue
        actual_source_sha = sha_text(str(record.get("source", "")))
        expected_source_sha = str(entry["source_sha256"])
        if actual_source_sha != expected_source_sha:
            raise SystemExit(
                f"source hash mismatch for rank {args.rank} {unit_id}: "
                f"{actual_source_sha} != {expected_source_sha}"
            )
        translation = str(entry["translation"])
        candidate = {
            "source": record.get("source", ""),
            "translation": translation,
            "kind": record.get("kind", "paragraph"),
        }
        remaining = hard_flags(candidate, args.language)
        if remaining:
            raise SystemExit(
                f"override still has hard flags for rank {args.rank} "
                f"{unit_id}: {remaining}"
            )
        prior_sha = sha_text(str(record.get("translation", "")))
        record["translation"] = translation
        record["translation_sha256"] = sha_text(translation)
        applied.append({
            "unit_id": unit_id,
            "source_sha256": actual_source_sha,
            "prior_translation_sha256": prior_sha,
            "translation_sha256": record["translation_sha256"],
            "reason": entry.get("reason", "source-bound editorial override"),
        })

    missing = sorted(set(by_unit) - {entry["unit_id"] for entry in applied})
    if args.require_all_overrides and missing:
        raise SystemExit(
            f"required overrides absent for rank {args.rank}: {missing}"
        )

    remaining_soft = {
        record["unit_id"]: flags(record, args.language)
        for record in records
        if flags(record, args.language)
    }
    remaining_hard = {
        record["unit_id"]: hard_flags(record, args.language)
        for record in records
        if hard_flags(record, args.language)
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )

    report = {
        "status": "FAIL" if remaining_hard else (
            "PASS" if not remaining_soft else "REVIEW_REQUIRED"
        ),
        "rank": args.rank,
        "language": args.language,
        "unit_count": len(records),
        "initial_hard_flagged_unit_count": len(initial_hard),
        "initial_hard_flags": initial_hard,
        "override_count_for_rank": len(selected),
        "overrides_applied": applied,
        "missing_overrides": missing,
        "remaining_flagged_unit_count": len(remaining_soft),
        "remaining_flags": dict(list(remaining_soft.items())[:100]),
        "remaining_hard_flagged_unit_count": len(remaining_hard),
        "remaining_hard_flags": dict(list(remaining_hard.items())[:100]),
        "input_alignment_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "output_alignment_sha256": hashlib.sha256(out_path.read_bytes()).hexdigest(),
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)

    if args.require_zero_hard and remaining_hard:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
