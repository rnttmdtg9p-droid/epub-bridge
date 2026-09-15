#!/usr/bin/env python3
"""Resolve original-language source candidates for the unresolved BB Italian Top 100."""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


BOOKS = [
    (43, "Dracula", "Dracula", "Bram Stoker", "en", 1912),
    (44, "Frankenstein", "Frankenstein; or, The Modern Prometheus", "Mary Shelley", "en", 1851),
    (46, "Ragione e sentimento", "Sense and Sensibility", "Jane Austen", "en", 1817),
    (47, "Emma", "Emma", "Jane Austen", "en", 1817),
    (49, "Cime tempestose", "Wuthering Heights", "Emily Brontë", "en", 1848),
    (50, "Grandi speranze", "Great Expectations", "Charles Dickens", "en", 1870),
    (54, "Le avventure di Sherlock Holmes", "The Adventures of Sherlock Holmes", "Arthur Conan Doyle", "en", 1930),
    (55, "Il mastino dei Baskerville", "The Hound of the Baskervilles", "Arthur Conan Doyle", "en", 1930),
    (58, "L'importanza di chiamarsi Ernesto", "The Importance of Being Earnest", "Oscar Wilde", "en", 1900),
    (59, "Il fantasma di Canterville", "The Canterville Ghost", "Oscar Wilde", "en", 1900),
    (61, "La morte di Ivan Il'ič", "Смерть Ивана Ильича", "Лев Толстой", "ru", 1910),
    (63, "I fratelli Karamazov", "Братья Карамазовы", "Фёдор Достоевский", "ru", 1881),
    (64, "L'idiota", "Идиот", "Фёдор Достоевский", "ru", 1881),
    (65, "Memorie dal sottosuolo", "Записки из подполья", "Фёдор Достоевский", "ru", 1881),
    (70, "Notre-Dame de Paris", "Notre-Dame de Paris", "Victor Hugo", "fr", 1885),
    (74, "La signora delle camelie", "La Dame aux camélias", "Alexandre Dumas fils", "fr", 1895),
    (75, "Papà Goriot", "Le Père Goriot", "Honoré de Balzac", "fr", 1850),
    (78, "La Certosa di Parma", "La Chartreuse de Parme", "Stendhal", "fr", 1842),
    (79, "Germinale", "Germinal", "Émile Zola", "fr", 1902),
    (81, "Bel-Ami", "Bel-Ami", "Guy de Maupassant", "fr", 1893),
    (82, "Boule de suif", "Boule de suif", "Guy de Maupassant", "fr", 1893),
    (85, "I masnadieri", "Die Räuber", "Friedrich Schiller", "de", 1805),
    (87, "Il gabbiano", "Чайка", "Антон Чехов", "ru", 1904),
    (88, "Il giardino dei ciliegi", "Вишнёвый сад", "Антон Чехов", "ru", 1904),
    (89, "Zio Vanja", "Дядя Ваня", "Антон Чехов", "ru", 1904),
    (90, "Il processo", "Der Prozess", "Franz Kafka", "de", 1924),
    (91, "La metamorfosi", "Die Verwandlung", "Franz Kafka", "de", 1924),
    (92, "Il castello", "Das Schloss", "Franz Kafka", "de", 1924),
    (93, "Siddhartha", "Siddhartha", "Hermann Hesse", "de", 1962),
    (94, "Demian", "Demian", "Hermann Hesse", "de", 1962),
    (95, "Il lupo della steppa", "Der Steppenwolf", "Hermann Hesse", "de", 1962),
    (100, "La Repubblica", "Πολιτεία", "Πλάτων", "grc", -347),
]

WIKISOURCE = {"ru": "ru", "fr": "fr", "de": "de", "grc": "el", "en": "en"}
UA = "BoundaryBaySourceResolver/1.0 (source authentication; github.com/rnttmdtg9p-droid/epub-bridge)"


def get_json(url: str, retries: int = 3) -> dict:
    err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as response:
                return json.load(response)
        except Exception as exc:  # pragma: no cover - network variability
            err = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"GET failed: {url}: {err}")


def folded(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    return "".join(c for c in value if not unicodedata.combining(c))


def gutendex_candidates(title: str, author: str, language: str) -> list[dict]:
    query = urllib.parse.urlencode({"search": f"{title} {author}"})
    data = get_json(f"https://gutendex.com/books/?{query}")
    rows = []
    for book in data.get("results", [])[:12]:
        langs = book.get("languages", [])
        creators = [p.get("name", "") for p in book.get("authors", [])]
        score = 0
        if language in langs:
            score += 8
        title_fold = folded(title)
        candidate_fold = folded(book.get("title", ""))
        for token in re.findall(r"\w+", title_fold):
            if len(token) > 3 and token in candidate_fold:
                score += 1
        family = folded(author).split()[-1]
        if any(family in folded(name) for name in creators):
            score += 4
        formats = book.get("formats", {})
        rows.append({
            "provider": "Project Gutenberg / Gutendex",
            "ebook_id": book.get("id"),
            "title": book.get("title"),
            "authors": creators,
            "languages": langs,
            "copyright": book.get("copyright"),
            "download_count": book.get("download_count"),
            "metadata_url": f"https://www.gutenberg.org/ebooks/{book.get('id')}",
            "epub_url": formats.get("application/epub+zip"),
            "html_url": formats.get("text/html"),
            "text_url": formats.get("text/plain; charset=utf-8") or formats.get("text/plain; charset=us-ascii"),
            "score": score,
        })
    return sorted(rows, key=lambda r: (-r["score"], -(r["download_count"] or 0)))[:5]


def wikisource_candidates(title: str, language: str) -> list[dict]:
    subdomain = WIKISOURCE[language]
    params = urllib.parse.urlencode({
        "action": "query",
        "list": "search",
        "srsearch": f'intitle:"{title}"',
        "srnamespace": "0",
        "srlimit": "10",
        "format": "json",
        "utf8": "1",
    })
    data = get_json(f"https://{subdomain}.wikisource.org/w/api.php?{params}")
    rows = []
    for item in data.get("query", {}).get("search", []):
        page = item.get("title", "")
        rows.append({
            "provider": f"{subdomain}.wikisource.org",
            "page_title": page,
            "page_id": item.get("pageid"),
            "word_count": item.get("wordcount"),
            "size": item.get("size"),
            "timestamp": item.get("timestamp"),
            "page_url": f"https://{subdomain}.wikisource.org/wiki/{urllib.parse.quote(page.replace(' ', '_'))}",
            "api_parse_url": f"https://{subdomain}.wikisource.org/w/api.php?action=parse&page={urllib.parse.quote(page)}&prop=text|sections|revid&format=json&utf8=1",
        })
    return rows


def main() -> None:
    output = {
        "schema": "bb-original-source-candidates-1.0",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "bb_master": "5.0.13",
        "policy": {
            "source_language": "original only",
            "translation_model": "google/madlad400-3b-mt",
            "fallback": "Wikisource candidates require scan/edition and completeness authentication",
            "rights": "EU/Italy life-plus-70 review gate; do not publish blocked titles without rights",
        },
        "books": [],
    }
    for rank, italian, original, author, lang, death in BOOKS:
        row = {
            "rank": rank,
            "italian_title": italian,
            "original_title": original,
            "author": author,
            "original_language": lang,
            "author_death_year": death,
            "italy_eu_pd_2026": death < 1956,
            "gutenberg": [],
            "wikisource": [],
            "errors": [],
        }
        try:
            row["gutenberg"] = gutendex_candidates(original, author, lang)
        except Exception as exc:
            row["errors"].append(f"Gutendex: {exc}")
        try:
            row["wikisource"] = wikisource_candidates(original, lang)
        except Exception as exc:
            row["errors"].append(f"Wikisource: {exc}")
        output["books"].append(row)
        time.sleep(0.2)

    canonical = json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    output["record_sha256_before_self_field"] = hashlib.sha256(canonical.encode()).hexdigest()
    target = Path("results/bb-top100-original-source-candidates.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
