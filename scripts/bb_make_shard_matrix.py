#!/usr/bin/env python3
"""Build GitHub Actions matrices from authenticated BB source-bundle plans."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", required=True)
    parser.add_argument("--configs", nargs="+", required=True)
    parser.add_argument("--github-output", action="store_true")
    args = parser.parse_args()

    prepared = Path(args.prepared)
    translate: list[dict] = []
    assemble: list[dict] = []
    for config_path in args.configs:
        meta = json.loads(Path(config_path).read_text(encoding="utf-8"))
        rank = int(meta["rank"])
        plan_path = next(prepared.rglob(f"{rank:03d}_shard_plan.json"), None)
        bundle_path = next(prepared.rglob(f"{rank:03d}_source_bundle.json"), None)
        if plan_path is None or bundle_path is None:
            raise SystemExit(f"Missing prepared plan or bundle for rank {rank:03d}")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        key = re.sub(r"[^a-z0-9]+", "-", meta["slug"].casefold()).strip("-")
        common = {
            "rank": f"{rank:03d}", "config": config_path, "key": key,
            "bundle_artifact": f"bb-source-{rank:03d}-{key}",
        }
        assemble.append(common)
        for shard in range(int(plan["shard_count"])):
            translate.append({**common, "shard": shard, "shard_padded": f"{shard:04d}"})

    if len(translate) > 256:
        raise SystemExit(f"Translation matrix has {len(translate)} jobs; GitHub Actions limit is 256")
    payloads = {
        "translate": json.dumps({"include": translate}, separators=(",", ":")),
        "assemble": json.dumps({"include": assemble}, separators=(",", ":")),
    }
    print(json.dumps({key: json.loads(value) for key, value in payloads.items()}, indent=2))
    if args.github_output:
        target = os.environ.get("GITHUB_OUTPUT")
        if not target:
            raise SystemExit("GITHUB_OUTPUT is not set")
        with Path(target).open("a", encoding="utf-8") as handle:
            for key, value in payloads.items():
                handle.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
