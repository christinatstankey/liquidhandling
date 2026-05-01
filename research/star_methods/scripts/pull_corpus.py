"""
pull_corpus.py
==============

Phase 1 of the STAR Methods pipeline: discover and download Cell Press
open-access papers from Europe PMC, since 2017 (when STAR Methods became
mandatory).

What this script does
---------------------
1. Queries the Europe PMC search API for papers matching:
     ISSN:<issn> AND OPEN_ACCESS:Y AND FIRST_PDATE:[2017-01-01 TO <today>]
   for each Cell Press journal in `journals.json`.
2. Optionally downloads the full-text JATS XML for each result into
   `xml_cache/<PMCID>.xml`. Existing cache files are left alone, so this
   is safe to re-run incrementally.
3. Writes a manifest CSV (`manifest.csv`) with columns:
     pmcid, journal, year, title, has_xml, fetch_ok
   The manifest is the entry point for the parser in Phase 2.

Why Europe PMC and not NCBI E-utilities
---------------------------------------
Both mirror the same OA subset, but Europe PMC's REST API is JSON-native,
returns hitCount on the first call (good for corpus sizing), and serves
JATS XML at a stable URL pattern (`/{pmcid}/fullTextXML`). NCBI's PMC
E-utilities require a more elaborate efetch dance and rate-limit harder
without an API key.

Usage examples
--------------
# Estimate corpus size only (no downloads, just hitCount per journal):
    python pull_corpus.py --estimate

# Pull a 10-paper validation set from one journal (recent papers, since
# format drift over time is a known risk):
    python pull_corpus.py --journal "Cell Reports" --year-from 2024 \\
        --limit 10 --fetch

# Full corpus pull (will take a few hours; uses incremental cache):
    python pull_corpus.py --all --year-from 2017 --fetch

Network requirements
--------------------
Run this from a machine with outbound HTTPS access to
`www.ebi.ac.uk/europepmc/`. The script is courteous: 200 ms between
calls (well under Europe PMC's 10 req/s soft limit), retries with
exponential backoff on 5xx, and resumes from cache.

Dependencies
------------
- requests
- pandas

Both pinned in requirements.txt.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Iterator

import requests
from lxml import etree

# ---- Paths --------------------------------------------------------------

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                        # research/star_methods/
JOURNALS_JSON = ROOT / "journals.json"
XML_CACHE = ROOT / "xml_cache"
MANIFEST_CSV = ROOT / "manifest.csv"

# ---- Europe PMC API constants -------------------------------------------

EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EPMC_FULLTEXT = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"

# Be polite: 5 req/s leaves headroom under the 10 req/s soft limit.
SLEEP_BETWEEN_CALLS = 0.2
HTTP_TIMEOUT = 30
MAX_RETRIES = 3
PAGE_SIZE = 1000  # Europe PMC max


# ---- Helpers ------------------------------------------------------------

def load_journals(path: Path) -> list[dict]:
    """Load the journals.json metadata file."""
    with path.open() as f:
        return json.load(f)["journals"]


def epmc_get(url: str, params: dict | None = None) -> dict:
    """
    GET an Europe PMC URL with retries on transient errors. Returns
    parsed JSON. Raises RuntimeError on persistent failure so callers
    don't silently get partial results.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if 500 <= r.status_code < 600 and attempt < MAX_RETRIES:
                # Transient server error — back off and retry.
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"HTTP {r.status_code} from {url}: {r.text[:200]}")
        except (requests.RequestException, json.JSONDecodeError) as e:
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Request failed after {MAX_RETRIES} attempts: {e}")
    raise RuntimeError(f"Unreachable: retry loop exited for {url}")


def search_journal(
    journal_name: str,
    issn: str,
    eissn: str,
    year_from: int,
    year_to: int,
    limit: int | None = None,
) -> Iterator[dict]:
    """
    Yield Europe PMC search hits for a single journal, restricted to OA
    papers in the given date range. Uses cursorMark pagination so we get
    everything beyond Europe PMC's 1000-result first-page cap.

    Each yielded dict carries Europe PMC's full result record; downstream
    code only uses `pmcid`, `journalTitle`, `firstPublicationDate`, and
    `title`, but we keep the rest in case we want it later.
    """
    # Search both ISSN forms — papers may be indexed under either, and
    # parens + OR works in Europe PMC's Lucene syntax.
    issn_clause = f'(ISSN:"{issn}" OR ISSN:"{eissn}")' if issn != eissn else f'ISSN:"{issn}"'
    query = (
        f'{issn_clause}'
        f' AND OPEN_ACCESS:Y'
        f' AND HAS_FT:Y'
        f' AND FIRST_PDATE:[{year_from}-01-01 TO {year_to}-12-31]'
    )

    cursor = "*"
    yielded = 0
    while True:
        data = epmc_get(
            EPMC_SEARCH,
            params={
                "query": query,
                "format": "json",
                "resultType": "core",  # 'core' includes journal title and dates
                "pageSize": PAGE_SIZE,
                "cursorMark": cursor,
            },
        )
        time.sleep(SLEEP_BETWEEN_CALLS)

        results = data.get("resultList", {}).get("result", [])
        if not results:
            return
        for r in results:
            yield r
            yielded += 1
            if limit is not None and yielded >= limit:
                return

        next_cursor = data.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor:
            return
        cursor = next_cursor


def hitcount_for_journal(issn: str, eissn: str, year_from: int, year_to: int) -> int:
    """Cheap version of search_journal: just returns the hitCount for sizing."""
    issn_clause = f'(ISSN:"{issn}" OR ISSN:"{eissn}")' if issn != eissn else f'ISSN:"{issn}"'
    query = (
        f'{issn_clause}'
        f' AND OPEN_ACCESS:Y'
        f' AND HAS_FT:Y'
        f' AND FIRST_PDATE:[{year_from}-01-01 TO {year_to}-12-31]'
    )
    data = epmc_get(
        EPMC_SEARCH,
        params={"query": query, "format": "json", "pageSize": 1},
    )
    time.sleep(SLEEP_BETWEEN_CALLS)
    return int(data.get("hitCount", 0))


def _is_valid_jats(content: bytes) -> bool:
    """
    Sanity-check that downloaded bytes parse as JATS XML with an
    `<article>` root (or a `<pmc-articleset>` wrapper around one).

    Europe PMC has been observed to return HTTP 200 with HTML error
    pages or empty stubs during transient backend issues, which we
    don't want to cache as if they were real article XMLs. We use
    a strict parser (recover=False) so malformed bodies are rejected.
    """
    if not content or len(content) < 200:
        return False
    try:
        # resolve_entities=False guards against XXE in untrusted XML.
        parser = etree.XMLParser(resolve_entities=False, recover=False, huge_tree=True)
        root = etree.fromstring(content, parser)
    except (etree.XMLSyntaxError, ValueError):
        return False

    tag = etree.QName(root).localname.lower()
    if tag == "article":
        return True
    if tag == "pmc-articleset":
        # Wrapper element; accept if it contains at least one <article>.
        return any(etree.QName(c).localname.lower() == "article" for c in root)
    return False


def fetch_fulltext_xml(pmcid: str, dest: Path) -> bool:
    """
    Download the full-text JATS XML for a PMCID into `dest`. Returns True
    on success, False if Europe PMC returns 404, persistently returns a
    non-JATS body, or fails after retries.

    Validation matters because HTTP 200 alone is not enough — Europe PMC
    has been observed to return 200 with HTML error pages during outages.
    A bad body cached here would propagate to parse_krt.py and look like
    a parse failure rather than a fetch failure, conflating two distinct
    issues. We validate BEFORE writing so the cache is always usable.

    Treatment of an HTTP 200 with a non-JATS body: since the docstring
    rationale assumes the bad body is *transient*, we retry with the
    same exponential backoff as a 5xx before giving up. Only after
    `MAX_RETRIES` of bad-body 200s do we mark `fetch_ok=False`.

    Existing cache files (size > 0) are trusted on re-run. If a cached
    file may have been written before validation existed (or is
    suspected corrupt), pass `--revalidate-cache` to `pull_corpus.py`
    once before the fetch — invalid cache files are deleted there and
    re-fetched here.
    """
    if dest.exists() and dest.stat().st_size > 0:
        return True

    url = EPMC_FULLTEXT.format(pmcid=pmcid)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, timeout=HTTP_TIMEOUT)
            if r.status_code == 200:
                if _is_valid_jats(r.content):
                    dest.write_bytes(r.content)
                    time.sleep(SLEEP_BETWEEN_CALLS)
                    return True
                # 200 but the body isn't JATS. Treat as transient and
                # retry with backoff before giving up — Europe PMC HTML
                # error pages typically clear within a few seconds.
                if attempt < MAX_RETRIES:
                    time.sleep(2 ** attempt)
                    continue
                return False
            if r.status_code == 404:
                # 404 is decisive (paper has metadata but no XML body),
                # so don't waste retries on it.
                time.sleep(SLEEP_BETWEEN_CALLS)
                return False
            if 500 <= r.status_code < 600 and attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
            return False
        except requests.RequestException:
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
            return False
    return False


def revalidate_cache() -> tuple[int, int]:
    """
    Walk `xml_cache/`, parse each cached file, and delete any that don't
    pass `_is_valid_jats`. Returns (n_valid, n_removed).

    Use case: cache files written before content validation was added
    (or files corrupted by an interrupted write) may be malformed but
    non-empty, so `fetch_fulltext_xml`'s short-circuit `dest.exists()`
    check would treat them as valid. Running this once before a
    pull-fetch sweeps them out so they get re-downloaded cleanly.

    Skips non-`.xml` files. Reports each deletion to stdout for audit.
    """
    if not XML_CACHE.exists():
        return (0, 0)
    n_valid = 0
    n_removed = 0
    for path in sorted(XML_CACHE.glob("*.xml")):
        try:
            content = path.read_bytes()
        except OSError:
            n_removed += 1
            print(f"  unreadable, removing: {path.name}")
            try:
                path.unlink()
            except OSError:
                pass
            continue
        if _is_valid_jats(content):
            n_valid += 1
        else:
            n_removed += 1
            print(f"  invalid JATS, removing: {path.name}")
            path.unlink()
    return (n_valid, n_removed)


# ---- Top-level commands -------------------------------------------------

def cmd_estimate(journals: list[dict], year_from: int, year_to: int) -> int:
    """Print a per-journal hitCount table. No downloads. ~14 API calls."""
    print(f"Cell Press OA + full-text papers, {year_from}-{year_to}:")
    print(f"{'journal':<30} {'count':>8}")
    print("-" * 40)
    total = 0
    for j in journals:
        n = hitcount_for_journal(j["issn"], j["eissn"], year_from, year_to)
        total += n
        print(f"{j['name']:<30} {n:>8}")
    print("-" * 40)
    print(f"{'TOTAL':<30} {total:>8}")
    return 0


def cmd_pull(
    journals: list[dict],
    year_from: int,
    year_to: int,
    limit_per_journal: int | None,
    fetch: bool,
) -> int:
    """
    Walk through the chosen journals, yield search hits, optionally
    download full-text XML, and write the manifest CSV.
    """
    XML_CACHE.mkdir(exist_ok=True)
    rows = []

    for j in journals:
        print(f"\n=== {j['name']} ===")
        n = 0
        for r in search_journal(
            j["name"], j["issn"], j["eissn"], year_from, year_to, limit_per_journal
        ):
            pmcid = r.get("pmcid", "")
            if not pmcid:
                continue
            year = (r.get("firstPublicationDate", "") or "")[:4]
            title = r.get("title", "")[:200]

            row = {
                "pmcid": pmcid,
                "journal": j["name"],
                "year": year,
                "title": title,
                "has_xml": False,
                "fetch_ok": False,
            }

            if fetch:
                dest = XML_CACHE / f"{pmcid}.xml"
                ok = fetch_fulltext_xml(pmcid, dest)
                row["has_xml"] = dest.exists()
                row["fetch_ok"] = ok
                status = "ok" if ok else ("cached" if dest.exists() else "miss")
                print(f"  {pmcid}  {year}  {status}  {title[:80]}")
            else:
                print(f"  {pmcid}  {year}  {title[:80]}")

            rows.append(row)
            n += 1
        print(f"  ({n} papers)")

    # Append-or-replace manifest. Simplest: rewrite from scratch each run,
    # since search_journal is deterministic given the date range.
    with MANIFEST_CSV.open("w", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["pmcid", "journal", "year", "title", "has_xml", "fetch_ok"]
        )
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {len(rows)} rows to {MANIFEST_CSV.relative_to(ROOT.parent)}")
    return 0


# ---- CLI ----------------------------------------------------------------

def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = p.add_subparsers(dest="cmd", required=False)

    p.add_argument("--journal", default=None,
                   help="Restrict to a single journal name (matches journals.json).")
    p.add_argument("--all", action="store_true",
                   help="All Cell Press journals (default).")
    p.add_argument("--year-from", type=int, default=2017)
    p.add_argument("--year-to", type=int, default=date.today().year)
    p.add_argument("--limit", type=int, default=None,
                   help="Max papers per journal (useful for validation pulls).")
    p.add_argument("--fetch", action="store_true",
                   help="Download full-text XML for each hit into xml_cache/.")
    p.add_argument("--estimate", action="store_true",
                   help="Just print hitCount per journal; no downloads.")
    p.add_argument("--revalidate-cache", action="store_true",
                   help="Before doing anything else, re-parse every cached "
                        "XML and delete any that don't pass JATS validation. "
                        "Use this once if cache files may have been written "
                        "before content validation existed.")

    args = p.parse_args(argv)

    if args.revalidate_cache:
        print(f"Revalidating cache at {XML_CACHE.relative_to(ROOT.parent)}/ ...")
        n_valid, n_removed = revalidate_cache()
        print(f"  {n_valid} valid, {n_removed} removed")
        # If --revalidate-cache was the only action requested, exit here.
        # Otherwise fall through to the pull/estimate command.
        if not (args.estimate or args.fetch or args.journal or args.all):
            return 0

    journals = load_journals(JOURNALS_JSON)
    if args.journal:
        journals = [j for j in journals if j["name"] == args.journal]
        if not journals:
            print(f"ERROR: --journal {args.journal!r} not in journals.json", file=sys.stderr)
            return 1

    if args.estimate:
        return cmd_estimate(journals, args.year_from, args.year_to)

    return cmd_pull(
        journals,
        year_from=args.year_from,
        year_to=args.year_to,
        limit_per_journal=args.limit,
        fetch=args.fetch,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
