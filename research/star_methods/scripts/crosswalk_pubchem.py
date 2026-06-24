#!/usr/bin/env python3
"""
crosswalk_pubchem.py — resolve CAS numbers for STAR Methods reagents via PubChem.

Input:  research/star_methods/parsed/krt_reagent_products_ranked.csv
        (one row per (normalized_name, normalized_source, normalized_identifier))
Output: research/star_methods/parsed/krt_reagent_products_cas.csv
        (same rows with cas and cas_method columns filled in)
Cache:  research/star_methods/parsed/pubchem_cache.json    (keyed by lookup string)

Two lookup paths, run as separate tiers:

  Tier 1 — sigma_sid (high confidence, run first):
    normalized_identifier → PubChem substance (source "Sigma-Aldrich") →
    standardized CID → CAS synonym.  Only attempted for Sigma rows
    (normalized_source == "Sigma-Aldrich") with a non-empty catalog number.
    This CAS is product-specific — Sigma assigned it to their specific SKU.

  Tier 2 — name (lower confidence, run separately):
    normalized_name → PubChem compound name search → CID → CAS synonym.
    Used for non-Sigma rows and as a prioritization signal. Should not
    be used as canonical product identity without SDS/product confirmation.

Mixtures (DMEM, Pen/Strep) and biologics (FBS) have no CAS in PubChem;
those rows get cas=null, which is correct — the pipeline uses (vendor, catalog#)
as the canonical identifier when CAS is unavailable.

Both successes and failures are cached so re-runs skip already-queried entries.
The cache is keyed by lookup string (not row index) so it survives CSV regeneration.

Rate limit: 5 requests/sec (PubChem free-tier limit).

Usage:
    # Show cache coverage stats only (no API calls):
    python crosswalk_pubchem.py

    # Tier 1: Sigma SID lookups only — fast, high-confidence, drives SDS collection.
    python crosswalk_pubchem.py --fetch --sigma-only

    # Tier 2: Name lookups only — run after Tier 1 is complete.
    python crosswalk_pubchem.py --fetch --name-only

    # Test on first 10 rows before a full run:
    python crosswalk_pubchem.py --fetch --sigma-only --limit 10
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
IN_CSV     = ROOT / "parsed" / "krt_reagent_products_ranked.csv"
OUT_CSV    = ROOT / "parsed" / "krt_reagent_products_cas.csv"
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
    identifier = row.get("normalized_identifier", "").strip()
    catalog = _clean_catalog(identifier)
    return f"sigma:{catalog}" if catalog else ""


def _name_key(row: "pd.Series") -> str:
    """Cache key for a name-based lookup."""
    return f"name:{row['normalized_name'].lower().strip()}"


def _is_sigma(row: "pd.Series") -> bool:
    """True if the row's normalized_source indicates Sigma-Aldrich."""
    return "sigma" in row.get("normalized_source", "").lower()


def _is_cached(row: "pd.Series", cache: dict) -> bool:
    """
    True if this row has been queried and does not need a fresh API call.

    Sigma rows with a catalog number require the Sigma SID key to be in cache;
    a cached name result alone is not sufficient because the SID path yields
    more reliable CAS numbers and many Sigma rows were previously skipped
    when only their name key was cached.
    Non-Sigma rows are cached when their name key is present.
    """
    skey = _sigma_key(row)
    nkey = _name_key(row)
    if _is_sigma(row) and skey:
        return skey in cache
    return nkey in cache


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--fetch", action="store_true",
                   help="Query PubChem for uncached rows (requires internet).")
    p.add_argument("--sigma-only", action="store_true",
                   help="Tier 1: only fetch Sigma SID lookups (normalized_source == "
                        "'Sigma-Aldrich' with a non-empty catalog number). "
                        "Skips name fallback entirely. Run this first.")
    p.add_argument("--name-only", action="store_true",
                   help="Tier 2: only fetch name-based lookups. Skips Sigma SID. "
                        "Run after --sigma-only is complete.")
    p.add_argument("--limit", type=int, default=None,
                   help="Only process the first N eligible rows (for testing).")
    p.add_argument("--in-csv",  default=str(IN_CSV))
    p.add_argument("--out-csv", default=str(OUT_CSV))
    args = p.parse_args(argv)

    if args.sigma_only and args.name_only:
        p.error("--sigma-only and --name-only are mutually exclusive.")

    df = pd.read_csv(args.in_csv, dtype=str).fillna("")

    cache = load_cache(CACHE_PATH)
    today = str(date.today())

    # ── Fetch missing entries ──────────────────────────────────────────────
    if args.fetch:
        session = requests.Session()
        session.headers["User-Agent"] = (
            "MedraReagentPipeline/1.0 (research; contact via GitHub)"
        )

        # Build eligible list deduplicated by cache key so the same lookup
        # is never queued twice in one run, even if multiple product rows
        # share the same catalog number or normalized name.
        if args.sigma_only:
            # Tier 1: one row per unique sigma:<catalog> key not already cached.
            seen: set[str] = set()
            eligible = []
            for _, row in df.iterrows():
                if not (_is_sigma(row) and _sigma_key(row)):
                    continue
                skey = _sigma_key(row)
                if skey in cache or skey in seen:
                    continue
                seen.add(skey)
                eligible.append(row)
            log.info("Mode: --sigma-only  |  Unique Sigma SID keys to query: %d", len(eligible))

        elif args.name_only:
            # Tier 2: one row per unique name:<normalized_name> key not already
            # cached.  Skip rows whose Sigma SID already resolved a CAS — name
            # evidence is redundant for canonical product identity in those cases.
            seen = set()
            eligible = []
            for _, row in df.iterrows():
                nkey = _name_key(row)
                if nkey in cache or nkey in seen:
                    continue
                skey = _sigma_key(row)
                if _is_sigma(row) and skey and cache.get(skey, {}).get("cas"):
                    continue  # sigma_sid already resolved; name lookup not needed
                seen.add(nkey)
                eligible.append(row)
            log.info("Mode: --name-only  |  Unique name keys to query: %d", len(eligible))

        else:
            # Default: one row per unique key (sigma key for Sigma rows, name
            # key for others) not already in cache.
            seen = set()
            eligible = []
            for _, row in df.iterrows():
                key = _sigma_key(row) if (_is_sigma(row) and _sigma_key(row)) else _name_key(row)
                if key in cache or key in seen:
                    continue
                seen.add(key)
                eligible.append(row)
            log.info("Mode: default  |  Unique keys to query: %d", len(eligible))

        if args.limit:
            eligible = eligible[:args.limit]
        log.info("After --limit: %d keys to query", len(eligible))

        for i, row in enumerate(eligible):
            name = row["normalized_name"]
            src  = row.get("normalized_source", "")
            log.info("[%d/%d] %s (%s)", i + 1, len(eligible), name, src)

            cid, cas, method = None, None, None

            # Path 1: Sigma SID — skipped in --name-only mode.
            if not args.name_only:
                skey = _sigma_key(row)
                if skey and _is_sigma(row):
                    if skey in cache:
                        # Populated by a previous run or restart; skip API call.
                        cas = cache[skey].get("cas")
                        log.debug("  sigma_sid already cached (restart guard)")
                    else:
                        cid, cas, method = _lookup_sigma(row["normalized_identifier"], session)
                        cache[skey] = {
                            "cas": cas, "cid": cid,
                            "method": method or "sigma_not_found",
                            "queried_at": today,
                        }
                        log.info("  sigma_sid → cas=%s  cid=%s", cas, cid)

            # Path 2: Name lookup — skipped in --sigma-only mode.
            if not args.sigma_only and cas is None:
                nkey = _name_key(row)
                if nkey in cache:
                    cas = cache[nkey].get("cas")
                    log.debug("  name already cached (restart guard)")
                else:
                    cid, cas, method = _lookup_name(name, session)
                    cache[nkey] = {
                        "cas": cas, "cid": cid,
                        "method": method or "not_found",
                        "queried_at": today,
                    }
                    log.info("  name      → cas=%s  cid=%s", cas, cid)

            # Write cache after every key so progress survives interruption.
            save_cache(CACHE_PATH, cache)

    # ── Assemble output ────────────────────────────────────────────────────
    cas_col    = []
    method_col = []

    for _, row in df.iterrows():
        skey = _sigma_key(row)
        nkey = _name_key(row)

        # Prefer a Sigma SID result that actually resolved a CAS, but only for
        # Sigma rows — a cached sigma: key must not be applied to non-Sigma rows
        # that happen to share the same catalog-number string.
        entry = None
        if _is_sigma(row) and skey and skey in cache and cache[skey].get("cas"):
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
    # Row counts from the assembled output.
    total        = len(df)
    resolved     = df["cas"].notna().sum()
    via_sid      = (df["cas_method"] == "sigma_sid").sum()
    via_name     = (df["cas_method"] == "name").sum()
    not_queried  = (df["cas_method"] == "not_queried").sum()
    null_queried = total - resolved - not_queried

    # True pending counts = unique cache keys missing from cache.
    # These are the actual API calls each tier would make, after dedup.
    tier1_keys = {
        _sigma_key(r) for _, r in df.iterrows()
        if _is_sigma(r) and _sigma_key(r) and _sigma_key(r) not in cache
    }
    tier2_keys = {
        _name_key(r) for _, r in df.iterrows()
        if _name_key(r) not in cache
        and not (_is_sigma(r) and _sigma_key(r) and cache.get(_sigma_key(r), {}).get("cas"))
    }

    log.info("─" * 50)
    log.info("Total rows:                         %d", total)
    log.info("CAS resolved:                       %d  (%.0f%%)", resolved, 100 * resolved / total)
    log.info("  via sigma_sid (Tier 1):           %d", via_sid)
    log.info("  via name      (Tier 2):           %d", via_name)
    log.info("Not yet queried (rows):             %d", not_queried)
    log.info("Pending unique keys:")
    log.info("  Tier 1 --sigma-only:              %d  unique sigma:<catalog> keys", len(tier1_keys))
    log.info("  Tier 2 --name-only:               %d  unique name:<name> keys",    len(tier2_keys))
    log.info("Null after query (expected):        %d  (mixtures/biologics)", null_queried)
    log.info("Wrote %s", out_path)

    if tier1_keys and not args.fetch:
        log.info("Tip: run --fetch --sigma-only first, then --fetch --name-only.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
