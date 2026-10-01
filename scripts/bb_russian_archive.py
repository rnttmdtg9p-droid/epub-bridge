#!/usr/bin/env python3
"""Fail-closed assembly of the 186-title Russian collection from Release checkpoints.

The release is an input store, not a publication of the final collection. Its
archive-manifest.json names immutable-by-hash EPUB and QA assets; the repository
contains the frozen roster. Nothing is written to the output directory until
all title and collection gates pass.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


REPO = "rnttmdtg9p-droid/epub-bridge"
GATES = {
    "source_integrity", "content_purity", "translation_and_literary_review",
    "structure_and_navigation", "toc_consistency",
    "heading_recognition_and_label_integrity", "heading_candidate_accounting",
    "heading_container_integrity", "spatial_layout_integrity",
    "resources_and_typography", "assets_and_rights", "bb_technical_preflight",
    "epubcheck", "rendered_layout", "reader_navigation",
    "delivery_verification",
}
COLLECTION_GATES = {
    "roster_reconciliation", "follow_on_rebuilds", "master_audit",
    "rights_and_sources", "final_byte_validation",
}
FOLLOW_ON = {
    "Война и мир", "Мастер и Маргарита", "Анна Каренина",
    "Три мушкетёра", "Моби Дик", "Мы",
}
SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_PART = 750 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_name(name):
    require(isinstance(name, str) and name and name == Path(name).name
            and name not in (".", "..") and "/" not in name and "\\" not in name,
            f"unsafe asset/EPUB name: {name!r}")
    return name


def api(url, token):
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Boundary-Bay-Russian-Archive/2",
    })
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def release_assets(tag, token):
    base = f"https://api.github.com/repos/{REPO}"
    release = api(f"{base}/releases/tags/{urllib.parse.quote(tag, safe='')}", token)
    require(release["tag_name"] == tag, "checkpoint tag mismatch")
    result = {}
    page = 1
    while True:
        batch = api(f"{base}/releases/{release['id']}/assets?per_page=100&page={page}", token)
        for asset in batch:
            require(asset["name"] not in result, f"duplicate asset: {asset['name']}")
            result[asset["name"]] = asset
        if len(batch) < 100:
            break
        page += 1
    return result


def download(assets, name, target, token, expected_sha=None, expected_size=None):
    safe_name(name)
    require(name in assets, f"missing checkpoint asset: {name}")
    asset = assets[name]
    request = urllib.request.Request(asset["url"], headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/octet-stream",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Boundary-Bay-Russian-Archive/2",
    })
    with urllib.request.urlopen(request, timeout=600) as response, open(target, "wb") as out:
        shutil.copyfileobj(response, out, 1024 * 1024)
    require(target.stat().st_size == asset["size"], f"asset size mismatch: {name}")
    if expected_size is not None:
        require(target.stat().st_size == expected_size, f"manifest size mismatch: {name}")
    if expected_sha is not None:
        require(isinstance(expected_sha, str) and SHA.fullmatch(expected_sha),
                f"missing/invalid SHA-256: {name}")
        require(digest(target) == expected_sha, f"SHA-256 mismatch: {name}")


def check_zip(path):
    with zipfile.ZipFile(path) as archive:
        require(archive.testzip() is None, f"ZIP CRC failure: {path.name}")
        names = archive.namelist()
        require(len(names) == len(set(names)), f"duplicate ZIP members: {path.name}")
        for name in names:
            require(name and not name.startswith("/") and not Path(name).is_absolute()
                    and ".." not in Path(name).parts and "\\" not in name,
                    f"unsafe ZIP member: {name}")
        return names


def check_evidence(qa_zip, sha, master_sha):
    names = check_zip(qa_zip)
    require("report.json" in names, f"missing report.json: {qa_zip.name}")
    with zipfile.ZipFile(qa_zip) as archive:
        report = json.loads(archive.read("report.json"))
        require(report.get("epub_sha256") == sha, f"QA final-byte binding: {qa_zip.name}")
        require(report.get("master_sha256") == master_sha, f"QA master binding: {qa_zip.name}")
        gates = report.get("gates", {})
        require(isinstance(gates, dict) and GATES <= gates.keys(),
                f"missing title QA gates: {qa_zip.name}")
        for key in GATES:
            gate = gates[key]
            require(gate.get("state") == "PASS" and gate.get("epub_sha256") == sha,
                    f"unqualified title gate {key}: {qa_zip.name}")
            path, expected = gate.get("evidence_path"), gate.get("evidence_sha256")
            require(isinstance(path, str) and path in names and path != "report.json"
                    and isinstance(expected, str) and SHA.fullmatch(expected),
                    f"missing gate evidence {key}: {qa_zip.name}")
            require(hashlib.sha256(archive.read(path)).hexdigest() == expected,
                    f"gate evidence hash mismatch {key}: {qa_zip.name}")


def check_collection_evidence(path, manifest, roster_sha):
    names = check_zip(path)
    require("report.json" in names, "missing collection report")
    with zipfile.ZipFile(path) as archive:
        report = json.loads(archive.read("report.json"))
        require(report.get("roster_sha256") == roster_sha, "collection roster binding")
        require(report.get("master_sha256") == manifest["master_sha256"], "collection master binding")
        ledger = {"titles": manifest["titles"],
                  "follow_on_rebuilds": manifest["follow_on_rebuilds"]}
        title_digest = hashlib.sha256(json.dumps(ledger, ensure_ascii=False,
                                          sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        require(report.get("titles_sha256") == title_digest,
                "collection title ledger binding")
        gates = report.get("gates", {})
        require(isinstance(gates, dict) and COLLECTION_GATES <= gates.keys(),
                "missing collection gates")
        for key in COLLECTION_GATES:
            gate = gates[key]
            evidence, expected = gate.get("evidence_path"), gate.get("evidence_sha256")
            require(gate.get("state") == "PASS" and evidence in names
                    and evidence != "report.json" and isinstance(expected, str)
                    and SHA.fullmatch(expected)
                    and hashlib.sha256(archive.read(evidence)).hexdigest() == expected,
                    f"unqualified collection gate: {key}")


def check_epub(path, jar):
    names = check_zip(path)
    with zipfile.ZipFile(path) as archive:
        first = archive.infolist()[0]
        require(first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
                and archive.read("mimetype") == b"application/epub+zip"
                and "META-INF/container.xml" in names, f"invalid EPUB container: {path.name}")
    result = subprocess.run(["java", "-jar", str(jar), str(path), "--failonwarnings"],
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=600)
    require(result.returncode == 0, f"EPUBCheck failed for {path.name}:\n{result.stdout[-4000:]}")
    return result.stdout


def assemble(epubs, out, prefix, ledger_name):
    out.mkdir(parents=True, exist_ok=True)
    parts, group, size = [], [], 0
    for entry, path in epubs:
        if group and size + path.stat().st_size > MAX_PART:
            parts.append(group)
            group, size = [], 0
        group.append((entry, path))
        size += path.stat().st_size
    if group:
        parts.append(group)
    archives = []
    for index, group in enumerate(parts, 1):
        name = f"{prefix}_part{index:02d}.zip"
        target = out / name
        with zipfile.ZipFile(target, "w", allowZip64=False) as z:
            for entry, path in group:
                z.write(path, arcname=entry["epub_asset"], compress_type=zipfile.ZIP_STORED)
        require(check_zip(target) == [e["epub_asset"] for e, _ in group],
                f"archive roster mismatch: {name}")
        with zipfile.ZipFile(target) as z:
            for entry, _ in group:
                require(hashlib.sha256(z.read(entry["epub_asset"])).hexdigest() == entry["sha256"],
                        f"extracted EPUB mismatch: {entry['epub_asset']}")
        archives.append({"file": name, "sha256": digest(target),
                         "bytes": target.stat().st_size,
                         "ranks": [e["rank"] for e, _ in group]})
    ledger = {"titles": [{"rank": e["rank"], "file": e["epub_asset"],
                          "sha256": e["sha256"], "bytes": e["size"]} for e, _ in epubs],
              "archives": archives}
    (out / ledger_name).write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n")
    return archives


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--roster", type=Path, required=True)
    parser.add_argument("--epubcheck-jar", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    token = os.environ["GITHUB_TOKEN"]
    require(args.epubcheck_jar.is_file(), "EPUBCheck JAR missing")
    require(args.roster.is_file(), "frozen 186-title roster missing")
    roster_sha = digest(args.roster)
    roster = json.loads(args.roster.read_text(encoding="utf-8"))
    required = roster.get("titles", [])
    selected_ranks = [t["rank"] for t in required]
    excluded = roster.get("excluded_ranks", [])
    follow_roster = roster.get("follow_on_rebuilds", [])
    require(roster.get("expected_title_count") == 186 and len(required) == 186
            and selected_ranks == sorted(set(selected_ranks))
            and len(excluded) == 14 and excluded == sorted(set(excluded))
            and set(selected_ranks).isdisjoint(excluded)
            and set(selected_ranks) | set(excluded) == set(range(1, 201))
            and len(follow_roster) == 6
            and {t["title"] for t in follow_roster} == FOLLOW_ON
            and {t["rank"] for t in follow_roster} <= set(excluded)
            and all(t.get("author") for t in follow_roster)
            and all(t.get("title") and t.get("author") for t in required),
            "invalid frozen roster; need 186 selected identities and 14 excluded ranks")
    assets = release_assets(args.tag, token)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        manifest_file = work / "archive-manifest.json"
        download(assets, "archive-manifest.json", manifest_file, token)
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        manifest["_sha256"] = digest(manifest_file)
        require(manifest.get("schema_version") == 1
                and manifest.get("expected_title_count") == 186
                and manifest.get("checkpoint_tag") == args.tag
                and manifest.get("roster_sha256") == roster_sha
                and isinstance(manifest.get("master_sha256"), str)
                and SHA.fullmatch(manifest["master_sha256"]), "invalid checkpoint manifest header")
        entries = manifest.get("titles", [])
        require(len(entries) == 186 and [e["rank"] for e in entries] == selected_ranks,
                "missing, duplicate or out-of-order selected rank")
        follow = manifest.get("follow_on_rebuilds", [])
        require(len(follow) == 6 and [e["rank"] for e in follow] ==
                [e["rank"] for e in follow_roster], "six follow-on ranks not reconciled")
        names = set()
        for want, entry in list(zip(required, entries)) + list(zip(follow_roster, follow)):
            require((entry.get("title"), entry.get("author")) ==
                    (want["title"], want["author"]), f"roster identity mismatch: {want['rank']}")
            require(entry.get("status") == "RELEASED", f"rank {want['rank']} is not released")
            epub, qa = safe_name(entry.get("epub_asset")), safe_name(entry.get("qa_asset"))
            require(epub.endswith(".epub") and epub.startswith(f"{want['rank']:03d}_"),
                    f"wrong EPUB filename for rank {want['rank']}")
            require(epub not in names and qa not in names and epub != qa,
                    "duplicate checkpoint asset name")
            names.update((epub, qa))
            require(isinstance(entry.get("size"), int) and entry["size"] > 0,
                    f"invalid size for rank {want['rank']}")
        require(manifest.get("release_state") == "RELEASED", "collection not released")
        qa_spec = manifest.get("collection_qa", {})
        collection_qa = work / "collection_qa.zip"
        download(assets, qa_spec.get("asset"), collection_qa, token, qa_spec.get("sha256"))
        check_collection_evidence(collection_qa, manifest, roster_sha)
        epubs, follow_epubs = [], []
        for entry in entries + follow:
            rank = entry["rank"]
            path = work / entry["epub_asset"]
            qa_path = work / entry["qa_asset"]
            download(assets, entry["epub_asset"], path, token, entry.get("sha256"), entry["size"])
            download(assets, entry["qa_asset"], qa_path, token, entry.get("qa_sha256"))
            check_evidence(qa_path, entry["sha256"], manifest["master_sha256"])
            check_epub(path, args.epubcheck_jar)
            (epubs if entry["rank"] in selected_ranks else follow_epubs).append((entry, path))
            print(f"VERIFIED rank={rank:03d} sha256={entry['sha256']}", flush=True)
        require(not args.output.exists(), "output already exists")
        archives = assemble(epubs, args.output, "BB_Russian_Collection_186", "SHA256SUMS_186.json")
        follow_archives = assemble(follow_epubs, args.output, "BB_Russian_Follow_On_6", "SHA256SUMS_follow_on.json")
        shutil.copyfile(manifest_file, args.output / "archive-manifest.json")
        print(f"ARCHIVE_READY titles=186 parts={len(archives)} follow_on=6 follow_parts={len(follow_archives)}", flush=True)


if __name__ == "__main__":
    main()
