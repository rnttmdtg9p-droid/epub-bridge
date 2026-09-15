#!/usr/bin/env python3
"""Authenticate sources, freeze source bundles, and emit BB shard matrices."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bb_translate_gutenberg import acquire_gutenberg, save_source_bundle
from bb_translate_wikisource import acquire_wikisource


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=["gutenberg", "wikisource"], required=True)
    parser.add_argument("--configs-json", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--shard-size", type=int, default=64)
    parser.add_argument("--github-output", action="store_true")
    args = parser.parse_args()

    configs = json.loads(args.configs_json)
    if not isinstance(configs, list) or not configs:
        raise SystemExit("--configs-json must be a non-empty JSON list")
    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    translate: list[dict] = []
    assemble: list[dict] = []
    source_summary: list[dict] = []

    for config_path in configs:
        meta = json.loads(Path(config_path).read_text(encoding="utf-8"))
        rank = int(meta["rank"])
        title_out = root / f"{rank:03d}"
        title_out.mkdir(parents=True, exist_ok=True)
        if args.engine == "gutenberg":
            _, body, preface, chapters, source = acquire_gutenberg(meta)
        else:
            body, chapters, source, _ = acquire_wikisource(meta)
            preface = ""
        flat = [unit for chapter in chapters for unit in chapter]
        if len(chapters) != int(meta["expected_chapters"]):
            raise SystemExit(f"Rank {rank:03d}: {len(chapters)} sections != expected {meta['expected_chapters']}")
        shard_count = math.ceil(len(flat) / args.shard_size)
        bundle_name = f"{rank:03d}_source_bundle.json"
        save_source_bundle(title_out / bundle_name, meta, body, preface, chapters, source)
        plan = {
            "rank": rank, "config": config_path, "engine": args.engine,
            "chapter_count": len(chapters), "unit_count": len(flat),
            "shard_size": args.shard_size, "shard_count": shard_count,
            "source_sha256": source["source_sha256"], "source_bundle": bundle_name,
        }
        (title_out / f"{rank:03d}_shard_plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        key = re.sub(r"[^a-z0-9]+", "-", meta["slug"].casefold()).strip("-")
        common = {"rank": f"{rank:03d}", "config": config_path, "key": key}
        assemble.append(common)
        for shard in range(shard_count):
            translate.append({**common, "shard": shard, "shard_padded": f"{shard:04d}"})
        source_summary.append(plan)
        print(json.dumps(plan, ensure_ascii=False), flush=True)

    if len(translate) > 256:
        raise SystemExit(f"Translation matrix has {len(translate)} jobs; GitHub Actions limit is 256")
    (root / "source_summary.json").write_text(
        json.dumps(source_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    outputs = {
        "translate": json.dumps({"include": translate}, separators=(",", ":")),
        "assemble": json.dumps({"include": assemble}, separators=(",", ":")),
    }
    if args.github_output:
        output_path = os.environ.get("GITHUB_OUTPUT")
        if not output_path:
            raise SystemExit("GITHUB_OUTPUT is not set")
        with Path(output_path).open("a", encoding="utf-8") as handle:
            for key, value in outputs.items():
                handle.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
