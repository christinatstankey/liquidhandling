#!/usr/bin/env python3
"""
crosswalk_pubchem.py — resolve CAS numbers for STAR Methods reagents via PubChem.

Input:  research/star_methods/parsed/krt_reagents_ranked.csv
Output: research/star_methods/parsed/krt_reagents_cas.csv  (adds cas, cas_method columns)
Cache:  research/star_methods/parsed/pubchem_cache.json    (keyed by lookup string)

Two lookup paths per row, tried in order:
  1. Sigma SID: catalog# → PubChem substance (source "Sigma-Aldrich" or "MilliporeSigma")
     → standardized CID → CAS synonym.  Only attempted for Sigma rows.
  2. Name: normalized_name → PubChem compound name search → CID → CAS synonym.
     Used as fallback for all rows, including non-Sigma vendors.

Mixtures (DMEM, Pen/Strep) and biologics (FBS) have no CAS in PubChem;
those rows get cas=null, which is correct — the pipeline uses (vendor, catalog#)
as the canonical identifier when CAS is unavailable.

Both successes and failures are cached so re-runs skip already-queried entries.

Rate limit: 5 requests/sec (PubChem free-tier limit). Full 2,450-row run
takes ~20-30 minutes. The cache makes subsequent runs instant.

Usage:
    # Show cache coverage stats only (no API calls):
    python crosswalk_pubchem.py

    # Resolve missing entries (requires internet):
    python crosswalk_pubchem.py --fetch

    # Test on first 10 ranked rows before running the full set:
    python crosswalk_pubchem.py --fetch --limit 10
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

HERE       = Path(__file__).resolve().parent
ROOT       = HERE.parent
IN_CSV     = ROOT / "parsed" / "krt_reagents_ranked.csv"
OUT_CSV    = ROOT / "parsed" / "krt_reagents_cas.csv"
CACHE_PATH = ROOT / "parsed" / "pubchem_cache.json"

PUBCHEM   = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
REQ_DELAY = 0.21   # seconds between requests — stays under PubChem's 5/sec free-tier limit
CAS_RE    = re.compile(r"^\d{1,7}-\d{2}-\d$")

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)


# ── Cache ─────────────────────────────────────────────────────────────────────

def load_cache(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_cache(path: Path, cache: dict) -> None:
    path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")


# ── PubChem helpers ───────────────────────────────────────────────────────────

def _get(session: requests.Session, url: str):
    """GET url; return parsed JSON or None on any error (including 404)."""
    try:
        r = session.get(url, timeout=15)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.debug("GET %s → %s", url, exc)
        return None
    finally:
        time.sleep(REQ_DELAY)


def _cas_from_cid(cid: int, session: requests.Session):
    """Return the first CAS-pattern synonym for a PubChem CID, or None."""
    url = f"{PUBCHEM}/compound/cid/{cid}/synonyms/JSON"
    data = _get(session, url)
    if not data:
        return None
    synonyms = (
        data.get("InformationList", {})
            .get("Information", [{}])[0]
            .get("Synonym", [])
    )
    for syn in synonyms:
        if CAS_RE.match(syn):
            return syn
    return None


def _clean_catalog(identifier: str) -> str:
    """Strip 'Cat#', 'Cat# ', '#' prefixes and surrounding whitespace."""
    s = identifier.strip()
    s = re.sub(r"(?i)^cat\s*#\s*", "", s)
    s = re.sub(r"^#\s*", "", s)
    return s.strip()


def _lookup_sigma(identifier: str, session: requests.Session):
    """
    Sigma SID lookup: catalog# → substance → standardized CID → CAS.

    PubChem's source IDs for Sigma-Aldrich substances have brand suffixes:
    D9542 is stored as "D9542_SIGMA", "D9542_SIAL", etc.  We try the three
    known suffixes in order.  The standardized compound entry has type==1
    (integer, not the string "standardized").

    Returns (cid, cas, "sigma_sid") or (None, None, None).
    """
    catalog = _clean_catalog(identifier)
    if not catalog:
        return None, None, None

    # Known brand suffixes Sigma-Aldrich uses in PubChem substance records.
    suffixes = ("_SIGMA", "_SIAL", "_SIGALD", "")
    for suffix in suffixes:
        source_id = f"{catalog}{suffix}"
        url = f"{PUBCHEM}/substance/sourceid/Sigma-Aldrich/{source_id}/JSON"
        data = _get(session, url)
        if not data:
            continue
        for substance in data.get("PC_Substances", []):
            for compound in substance.get("compound", []):
                # type==1 is standardized; PubChem returns an integer, not "standardized"
                if compound.get("id", {}).get("type") == 1:
                    cid = compound["id"]["id"].get("cid")
                    if cid:
                        cas = _cas_from_cid(cid, session)
                        return cid, cas, "sigma_sid"

    return None, None, None


def _lookup_name(name: str, session: requests.Session):
    """
    Name-based PubChem search: name → CID → CAS.
    Returns (cid, cas, "name") or (None, None, None).
    """
    encoded = requests.utils.quote(name)
    url = f"{PUBCHEM}/compound/name/{encoded}/cids/JSON"
    data = _get(session, url)
    if not data:
        return None, None, None
    cids = data.get("IdentifierList", {}).get("CID", [])
    if not cids:
        return None, None, None
    cid = cids[0]
    cas = _cas_from_cid(cid, session)
    return cid, cas, "name"


# ── Cache key helpers ─────────────────────────────────────────────────────────

def _sigma_key(row: "pd.Series") -> str:
    """Cache key for a Sigma SID lookup, or '' if not applicable."""
    identifier = row.get("identifier", "").strip()
    catalog = _clean_catalog(identifier)
    return f"sigma:{catalog}" if catalog else ""


def _name_key(row: "pd.Series") -> str:
    """Cache key for a name-based lookup."""
    return f"name:{row['normalized_name'].lower().strip()}"


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--fetch", action="store_true",
                   help="Query PubChem for uncached rows (requires internet).")
    p.add_argument("--limit", type=int, default=None,
                   help="Only process the first N ranked rows (for testing).")
    p.add_argument("--in-csv",  default=str(IN_CSV))
    p.add_argument("--out-csv", default=str(OUT_CSV))
    args = p.parse_args(argv)

    df = pd.read_csv(args.in_csv, dtype=str).fillna("")
    if args.limit:
        df = df.head(args.limit)

    cache = load_cache(CACHE_PATH)
    today = str(date.today())

    # ── Fetch missing entries ──────────────────────────────────────────────
    if args.fetch:
        session = requests.Session()
        session.headers["User-Agent"] = (
            "MedraReagentPipeline/1.0 (research; contact via GitHub)"
        )

        # A row is uncached if neither of its keys is in the cache.
        uncached = [
            row for _, row in df.iterrows()
            if _sigma_key(row) not in cache and _name_key(row) not in cache
        ]
        log.info("Rows to query: %d / %d", len(uncached), len(df))

        for i, row in enumerate(uncached):
            name = row["normalized_name"]
            src  = row.get("source", "")
            log.info("[%d/%d] %s (%s)", i + 1, len(uncached), name, src)

            cid, cas, method = None, None, None

            # Path 1: Sigma SID — only for Sigma rows with a catalog number.
            skey = _sigma_key(row)
            if skey and "sigma" in src.lower():
                cid, cas, method = _lookup_sigma(row["identifier"], session)
                cache[skey] = {
                    "cas": cas, "cid": cid,
                    "method": method or "sigma_not_found",
                    "queried_at": today,
                }
                log.info("  sigma_sid → cas=%s  cid=%s", cas, cid)

            # Path 2: Name fallback — if Sigma path didn't resolve, or non-Sigma.
            if cid is None:
                cid, cas, method = _lookup_name(name, session)
                cache[_name_key(row)] = {
                    "cas": cas, "cid": cid,
                    "method": method or "not_found",
                    "queried_at": today,
                }
                log.info("  name      → cas=%s  cid=%s", cas, cid)

            # Write cache after every row so progress survives interruption.
            save_cache(CACHE_PATH, cache)

    # ── Assemble output ────────────────────────────────────────────────────
    cas_col    = []
    method_col = []

    for _, row in df.iterrows():
        skey = _sigma_key(row)
        nkey = _name_key(row)

        # Prefer a Sigma SID result that actually resolved a CAS.
        # Fall back to the name-lookup result (which may also be null).
        entry = None
        if skey and skey in cache and cache[skey].get("cas"):
            entry = cache[skey]
        elif nkey in cache:
            entry = cache[nkey]

        if entry:
            cas_col.append(entry.get("cas"))
            method_col.append(entry.get("method"))
        else:
            cas_col.append(None)
            method_col.append("not_queried")

    df["cas"]        = cas_col
    df["cas_method"] = method_col

    out_path = Path(args.out_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)

    # ── Coverage report ────────────────────────────────────────────────────
    resolved   = df["cas"].notna().sum()
    total      = len(df)
    not_queried = (df["cas_method"] == "not_queried").sum()

    log.info("─" * 50)
    log.info("Total rows:      %d", total)
    log.info("CAS resolved:    %d  (%.0f%%)", resolved, 100 * resolved / total)
    log.info("Not queried:     %d  (run --fetch to fill)", not_queried)
    log.info("Still null:      %d  (mixtures/biologics — expected)", total - resolved - not_queried)
    log.info("Wrote %s", out_path)

    if not_queried and not args.fetch:
        log.info("Tip: run with --fetch to resolve the %d unqueried rows.", not_queried)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
