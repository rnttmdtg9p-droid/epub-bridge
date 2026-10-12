#!/usr/bin/env python3
"""Direct, hash-bound Claude API translation runner for Boundary Bay text units.

The runner has no third-party runtime dependency. It reads an exact XHTML unit,
hashes every retained paragraph, calls Anthropic's Messages API directly, checks
the returned schema and alignment, and writes durable evidence with phase timing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


API_VERSION = "2023-06-01"
DEFAULT_ENDPOINT = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"
XHTML_NS = {"x": "http://www.w3.org/1999/xhtml"}
CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def write_json_atomic(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)
    readback = path.read_bytes()
    json.loads(readback)
    if readback != payload:
        raise RuntimeError(f"READBACK_MISMATCH:{path}")
    return sha256_bytes(readback)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-xhtml", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--rank", type=int, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--unit-id", required=True, help="Stable unit ID, e.g. u002")
    parser.add_argument("--chapter-number", required=True)
    parser.add_argument("--chapter-title-source", required=True)
    parser.add_argument("--chapter-title-target", required=True)
    parser.add_argument("--source-epub-sha256")
    parser.add_argument("--source-library-file-id")
    parser.add_argument("--expected-xhtml-sha256")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument(
        "--api-key-env",
        default="ANTHROPIC_API_KEY",
        help="Environment variable or network-secret placeholder containing the API credential",
    )
    parser.add_argument("--max-tokens", type=int, default=8000)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument(
        "--preflight",
        action="store_true",
        help="Validate the exact source, request and credential presence without calling Claude",
    )
    return parser.parse_args()


def extract_source_rows(path: Path, unit_id: str) -> tuple[str, list[dict[str, str]]]:
    source_bytes = path.read_bytes()
    xhtml_sha256 = sha256_bytes(source_bytes)
    root = ET.fromstring(source_bytes)
    paragraphs = root.findall(".//x:section/x:p", XHTML_NS)
    if not paragraphs:
        raise RuntimeError("NO_SOURCE_PARAGRAPHS")
    rows: list[dict[str, str]] = []
    for number, paragraph in enumerate(paragraphs, 1):
        text = "".join(paragraph.itertext()).strip()
        if not text:
            raise RuntimeError(f"EMPTY_SOURCE_PARAGRAPH:{number}")
        rows.append(
            {
                "id": f"{unit_id}-p{number:04d}",
                "source_sha256": sha256_bytes(text.encode("utf-8")),
                "source": text,
            }
        )
    return xhtml_sha256, rows


def request_payload(args: argparse.Namespace, rows: list[dict[str, str]]) -> dict[str, Any]:
    system = (
        f"You are translating Fyodor Dostoevsky's {args.title} from the provided "
        "Russian source into substantive, publication-quality modern French. "
        "Preserve meaning, tone, paragraph boundaries, names, dialogue punctuation, "
        "and every source detail. Do not summarize, omit, add commentary, or modernize "
        "facts. Return only a JSON array. Each object must contain exactly id, "
        "source_sha256, and french."
    )
    prompt = (
        f"Translate the complete Chapter {args.chapter_number}, "
        f"'{args.chapter_title_source}', in these source-aligned paragraphs. "
        "Copy each id and source_sha256 unchanged.\n"
        + json.dumps(rows, ensure_ascii=False)
    )
    return {
        "model": args.model,
        "max_tokens": args.max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }


def call_anthropic(
    args: argparse.Namespace,
    request_body: dict[str, Any],
) -> tuple[dict[str, Any], float]:
    key = os.environ.get(args.api_key_env, "").strip()
    if not key:
        raise RuntimeError(f"MISSING_{args.api_key_env}")
    headers = {
        "x-api-key": key,
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    if workspace_id:
        headers["anthropic-workspace-id"] = workspace_id
    request = urllib.request.Request(
        args.endpoint,
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=args.timeout_seconds) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        safe_body = error.read().decode("utf-8", errors="replace").replace(key, "[REDACTED]")
        raise RuntimeError(f"ANTHROPIC_HTTP_{error.code}:{safe_body[:1000]}") from error
    except Exception as error:
        raise RuntimeError(f"ANTHROPIC_NETWORK:{type(error).__name__}:{error}") from error
    return result, time.perf_counter() - started


def response_text(api_response: dict[str, Any]) -> str:
    blocks = [
        block.get("text", "")
        for block in api_response.get("content", [])
        if block.get("type") == "text"
    ]
    raw = "\n".join(blocks).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.S | re.I)
    return fenced.group(1).strip() if fenced else raw


def validate_translation(
    api_response: dict[str, Any],
    source_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    try:
        translated_rows = json.loads(response_text(api_response))
    except json.JSONDecodeError as error:
        raise RuntimeError("NON_JSON_RESPONSE") from error
    expected = {row["id"]: row for row in source_rows}
    if not isinstance(translated_rows, list) or len(translated_rows) != len(source_rows):
        raise RuntimeError("PARAGRAPH_COUNT_MISMATCH")
    seen: set[str] = set()
    evidence_rows: list[dict[str, str]] = []
    for translated in translated_rows:
        if not isinstance(translated, dict) or set(translated) != {
            "id",
            "source_sha256",
            "french",
        }:
            raise RuntimeError("OUTPUT_SCHEMA_MISMATCH")
        row_id = translated["id"]
        if row_id not in expected or row_id in seen:
            raise RuntimeError("OUTPUT_ID_MISMATCH")
        if translated["source_sha256"] != expected[row_id]["source_sha256"]:
            raise RuntimeError("SOURCE_HASH_NOT_PRESERVED")
        french = translated["french"].strip()
        if not french:
            raise RuntimeError("EMPTY_TRANSLATION")
        if CYRILLIC.search(french):
            raise RuntimeError(f"CYRILLIC_REMAINS:{row_id}")
        seen.add(row_id)
        evidence_rows.append(
            {
                "id": row_id,
                "source_sha256": expected[row_id]["source_sha256"],
                "translation_sha256": sha256_bytes(french.encode("utf-8")),
                "french": french,
            }
        )
    if seen != set(expected):
        raise RuntimeError("INCOMPLETE_ID_SET")
    return evidence_rows


def main() -> int:
    process_started_utc = utc_now()
    process_started = time.perf_counter()
    args = parse_args()
    phase_seconds: dict[str, float] = {}

    phase_started = time.perf_counter()
    xhtml_sha256, source_rows = extract_source_rows(args.source_xhtml, args.unit_id)
    if args.expected_xhtml_sha256 and xhtml_sha256 != args.expected_xhtml_sha256:
        raise RuntimeError(
            f"XHTML_HASH_MISMATCH:{xhtml_sha256}:{args.expected_xhtml_sha256}"
        )
    phase_seconds["source_preflight"] = time.perf_counter() - phase_started

    phase_started = time.perf_counter()
    body = request_payload(args, source_rows)
    request_sha256 = sha256_bytes(canonical_json(body))
    source_rows_sha256 = sha256_bytes(canonical_json(source_rows))
    phase_seconds["request_build"] = time.perf_counter() - phase_started

    common = {
        "process_started_utc": process_started_utc,
        "rank": args.rank,
        "title": args.title,
        "source_language": "ru",
        "target_language": "fr",
        "source_xhtml": str(args.source_xhtml.resolve()),
        "source_xhtml_sha256": xhtml_sha256,
        "source_epub_sha256": args.source_epub_sha256,
        "source_library_file_id": args.source_library_file_id,
        "source_unit": args.unit_id,
        "chapter_number": args.chapter_number,
        "chapter_title_source": args.chapter_title_source,
        "chapter_title_target": args.chapter_title_target,
        "source_paragraphs": len(source_rows),
        "source_rows_sha256": source_rows_sha256,
        "endpoint": args.endpoint,
        "api_version": API_VERSION,
        "requested_model": args.model,
        "request_sha256": request_sha256,
        "authentication": {
            "credential_environment_variable": args.api_key_env,
            "credential_present": bool(os.environ.get(args.api_key_env, "").strip()),
        },
    }

    if args.preflight:
        credential_present = common["authentication"]["credential_present"]
        phase_seconds["total"] = time.perf_counter() - process_started
        receipt = {
            **common,
            "status": (
                "PREFLIGHT_PASS_API_NOT_CALLED"
                if credential_present
                else "BLOCKED_BEFORE_API_EXECUTION"
            ),
            "api_request_attempted": False,
            "network_access_verified": False,
            "provider_authentication_verified": False,
            "phase_seconds": phase_seconds,
        }
        if not credential_present:
            receipt["blocker"] = f"MISSING_{args.api_key_env}"
        receipt_hash = write_json_atomic(args.output_dir / "preflight_receipt.json", receipt)
        print("DIRECT_CLAUDE:", receipt["status"])
        if not credential_present:
            print("DIRECT_CLAUDE: BLOCKER", receipt["blocker"])
        print("DIRECT_CLAUDE: PARAGRAPHS", len(source_rows))
        print("DIRECT_CLAUDE: RECEIPT_SHA256", receipt_hash)
        return 0 if credential_present else 2

    if args.dry_run:
        phase_seconds["total"] = time.perf_counter() - process_started
        receipt = {
            **common,
            "status": "DRY_RUN_SOURCE_AND_REQUEST_VALIDATED_API_NOT_CALLED",
            "api_request_attempted": False,
            "network_access_verified": False,
            "provider_authentication_verified": False,
            "phase_seconds": phase_seconds,
            "source_rows": source_rows,
        }
        receipt_hash = write_json_atomic(args.output_dir / "dry_run_receipt.json", receipt)
        print("DIRECT_CLAUDE: DRY_RUN_SOURCE_AND_REQUEST_VALIDATED_API_NOT_CALLED")
        print("DIRECT_CLAUDE: PARAGRAPHS", len(source_rows))
        print("DIRECT_CLAUDE: TOTAL_SECONDS", f"{phase_seconds['total']:.6f}")
        print("DIRECT_CLAUDE: RECEIPT_SHA256", receipt_hash)
        return 0

    try:
        api_response, api_seconds = call_anthropic(args, body)
    except RuntimeError as error:
        phase_seconds["total_to_blocker"] = time.perf_counter() - process_started
        failure = str(error).split(":", 1)[0]
        missing_credential = failure == f"MISSING_{args.api_key_env}"
        blocker = {
            **common,
            "status": (
                "BLOCKED_BEFORE_API_EXECUTION"
                if missing_credential
                else "API_REQUEST_FAILED"
            ),
            "blocker": failure,
            "api_request_attempted": not missing_credential,
            "provider_response_received": failure.startswith("ANTHROPIC_HTTP_"),
            "phase_seconds": phase_seconds,
        }
        blocker_hash = write_json_atomic(args.output_dir / "blocker.json", blocker)
        print("DIRECT_CLAUDE:", blocker["status"], blocker["blocker"])
        print(
            "DIRECT_CLAUDE: TOTAL_SECONDS_TO_BLOCKER",
            f"{phase_seconds['total_to_blocker']:.6f}",
        )
        print("DIRECT_CLAUDE: BLOCKER_SHA256", blocker_hash)
        return 2
    phase_seconds["api_request"] = api_seconds

    phase_started = time.perf_counter()
    rows = validate_translation(api_response, source_rows)
    phase_seconds["response_validation"] = time.perf_counter() - phase_started
    phase_seconds["total_through_validation"] = time.perf_counter() - process_started
    evidence = {
        **common,
        "status": "API_EXECUTION_PASS_LITERARY_REVIEW_PENDING",
        "api_request_attempted": True,
        "provider_response_received": True,
        "generated_at_utc": utc_now(),
        "response_model": api_response.get("model"),
        "response_id": api_response.get("id"),
        "stop_reason": api_response.get("stop_reason"),
        "usage": api_response.get("usage", {}),
        "phase_seconds": phase_seconds,
        "rows": rows,
    }
    result_hash = write_json_atomic(args.output_dir / "translation_result.json", evidence)
    finished = time.perf_counter()
    receipt = {
        **common,
        "status": "API_EXECUTION_PASS_LITERARY_REVIEW_PENDING",
        "translation_result_sha256": result_hash,
        "response_id": api_response.get("id"),
        "response_model": api_response.get("model"),
        "phase_seconds": {
            **phase_seconds,
            "output_write_and_readback": finished
            - process_started
            - phase_seconds["total_through_validation"],
            "total": finished - process_started,
        },
    }
    receipt_hash = write_json_atomic(args.output_dir / "run_receipt.json", receipt)
    print("DIRECT_CLAUDE: API_EXECUTION_PASS_LITERARY_REVIEW_PENDING")
    print("DIRECT_CLAUDE: RESPONSE_MODEL", api_response.get("model"))
    print("DIRECT_CLAUDE: RESPONSE_ID", api_response.get("id"))
    print("DIRECT_CLAUDE: PARAGRAPHS", len(rows))
    print("DIRECT_CLAUDE: TOTAL_SECONDS", f"{receipt['phase_seconds']['total']:.6f}")
    print("DIRECT_CLAUDE: RESULT_SHA256", result_hash)
    print("DIRECT_CLAUDE: RECEIPT_SHA256", receipt_hash)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"DIRECT_CLAUDE: FATAL {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
