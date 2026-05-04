#!/usr/bin/env python3
"""
download_sds.py — download Sigma-Aldrich SDS PDFs for CAS-resolved reagents.

Reads research/star_methods/parsed/krt_reagents_cas.csv and
research/star_methods/parsed/pubchem_cache.json, determines which CAS
numbers don't yet have an SDS PDF, resolves a Sigma-Aldrich catalog
number for each, then downloads the PDF.

Two catalog-resolution paths:
  sigma_sid rows  — catalog number is already in the crosswalk CSV
  name rows       — query PubChem substances for the CID to find a
                    Sigma-Aldrich source_id (catalog number)

Results cached in data/sds-pdfs/catalog_cache.json so re-runs skip
already-resolved entries. PDFs saved as data/sds-pdfs/<CAS>.pdf.

Usage:
    python tools/download_sds.py              # dry-run: show what would download
    python tools/download_sds.py --fetch      # resolve catalogs + download PDFs
    python tools/download_sds.py --fetch --limit 10   # test on top-10 first
"""
from __future__ import annotations

import argparse
import json
import random
import re
import socket
import sys
import time
from pathlib import Path

# Override the OS-level socket timeout (default 60s) so stalled connections
# don't block longer than our intended application timeout.
socket.setdefaulttimeout(15)

import pandas as pd
import requests

REPO_ROOT    = Path(__file__).parent.parent
IN_CSV       = REPO_ROOT / "research" / "star_methods" / "parsed" / "krt_reagents_cas.csv"
PC_CACHE     = REPO_ROOT / "research" / "star_methods" / "parsed" / "pubchem_cache.json"
SDS_DIR      = REPO_ROOT / "data" / "sds-pdfs"
REAGENTS_DIR = REPO_ROOT / "data" / "reagents"
CAT_CACHE    = SDS_DIR / "catalog_cache.json"

PUBCHEM    = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
SIGMA_SDS  = "https://www.sigmaaldrich.com/US/en/sds"
BRANDS     = ["sigma", "sigald", "sial"]   # "aldrich" excluded — causes OS-level timeouts
PC_DELAY   = 0.21           # seconds between PubChem requests
DL_DELAY   = 12.0           # base seconds between SDS downloads; jitter added below
DL_TIMEOUT = (10, 15)       # (connect_timeout, read_timeout) — overrides OS default (set above)
MIN_PDF_KB = 50             # reject files smaller than this as non-PDFs


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _clean_catalog(identifier: str) -> str:
    s = identifier.strip()
    s = re.sub(r"(?i)^cat\s*#\s*", "", s)
    return re.sub(r"^#\s*", "", s).strip()


def _sigma_catalog_from_substance(substance: dict) -> str | None:
    """Extract the clean catalog number from a PubChem substance record."""
    src = substance.get("source", {}).get("db", {})
    name = src.get("name", "")
    if "Sigma" not in name and "MilliporeSigma" not in name:
        return None
    source_id = src.get("source_id", {}).get("str", "")
    # Strip brand suffix: "D9542_SIGMA" → "D9542"
    catalog = re.sub(r"_[A-Z]+$", "", source_id).strip()
    return catalog or None


def _pc_get(session: requests.Session, url: str):
    """GET a PubChem URL with rate limiting; return JSON or None."""
    try:
        r = session.get(url, timeout=15)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception:
        return None
    finally:
        time.sleep(PC_DELAY)


def find_sigma_catalog_for_cid(cid: int, session: requests.Session) -> str | None:
    """
    Given a PubChem CID, find a Sigma-Aldrich catalog number by fetching the
    first batch of SIDs for this compound and checking each substance record.
    Returns the clean catalog number or None.
    """
    # Get all SIDs for this compound.
    url = f"{PUBCHEM}/compound/cid/{cid}/sids/JSON"
    data = _pc_get(session, url)
    if not data:
        return None
    sids = (data.get("InformationList", {})
                .get("Information", [{}])[0]
                .get("SID", []))
    if not sids:
        return None

    # Batch-fetch substance records (up to 50 SIDs at a time).
    for start in range(0, min(len(sids), 200), 50):
        batch = sids[start:start + 50]
        sid_str = ",".join(str(s) for s in batch)
        url2 = f"{PUBCHEM}/substance/sid/{sid_str}/JSON"
        data2 = _pc_get(session, url2)
        if not data2:
            continue
        for substance in data2.get("PC_Substances", []):
            catalog = _sigma_catalog_from_substance(substance)
            if catalog:
                return catalog

    return None


def download_pdf(cas: str, catalog: str) -> tuple:
    """
    Try known Sigma brand prefixes to download the SDS PDF for catalog.
    Uses a fresh session per call to avoid session-level blocking.
    Saves to data/sds-pdfs/<CAS>.pdf if successful.
    Returns (True, brand) on success, (False, reason) on failure.
    """
    out_path = SDS_DIR / f"{cas}.pdf"
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/pdf,*/*",
    })
    last_reason = "all brands failed"
    for brand in BRANDS:
        url = f"{SIGMA_SDS}/{brand}/{catalog}"
        try:
            r = session.get(url, timeout=DL_TIMEOUT, allow_redirects=True)
            if r.status_code != 200:
                last_reason = f"HTTP {r.status_code} ({brand})"
                time.sleep(DL_DELAY)
                continue
            if r.content[:4] != b"%PDF":
                last_reason = f"not PDF ({brand}), ct={r.headers.get('Content-Type','?')[:40]}"
                time.sleep(DL_DELAY)
                continue
            if len(r.content) < MIN_PDF_KB * 1024:
                last_reason = f"too small {len(r.content)//1024}KB ({brand})"
                time.sleep(DL_DELAY)
                continue
            out_path.write_bytes(r.content)
            time.sleep(DL_DELAY + random.uniform(0, 4))
            return True, brand
        except Exception as exc:
            last_reason = f"{type(exc).__name__}: {exc} ({brand})"
            time.sleep(DL_DELAY + random.uniform(0, 4))
            continue
    return False, last_reason


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--fetch", action="store_true",
                   help="Resolve catalogs and download PDFs.")
    p.add_argument("--limit", type=int, default=None,
                   help="Process at most N new CAS numbers (for testing).")
    args = p.parse_args(argv)

    SDS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Load data ──────────────────────────────────────────────────────────
    df = pd.read_csv(IN_CSV, dtype=str).fillna("")
    pc_cache   = load_json(PC_CACHE)
    cat_cache  = load_json(CAT_CACHE)   # {cas: catalog | null}

    # Existing records and existing PDFs.
    existing_json = {
        f.stem for f in REAGENTS_DIR.glob("*.json")
        if re.match(r"^\d+(?:-\d+){1,2}$", f.stem)
    }
    existing_pdf = {
        f.stem for f in SDS_DIR.glob("*.pdf")
    }

    # Resolved rows with unique CAS, sorted by paper count (most-used first).
    resolved = (
        df[df["cas"].notna() & (df["cas"] != "")]
        .drop_duplicates("cas")
        .copy()
    )
    resolved["_papers"] = pd.to_numeric(resolved["n_papers_total"], errors="coerce").fillna(0)
    resolved = resolved.sort_values("_papers", ascending=False)

    # Filter to new CAS numbers (no JSON and no PDF yet, or PDF exists but no JSON).
    todo = resolved[~resolved["cas"].isin(existing_json)].copy()
    new_without_pdf = todo[~todo["cas"].isin(existing_pdf)]
    new_with_pdf    = todo[todo["cas"].isin(existing_pdf)]

    print(f"CAS-resolved unique:       {len(resolved)}", flush=True)
    print(f"Already have JSON:          {len(resolved) - len(todo)}")
    print(f"Need PDF + ingest:          {len(new_without_pdf)}")
    print(f"Have PDF, need ingest only: {len(new_with_pdf)}")

    # ── Catalog-resolution pass ────────────────────────────────────────────
    rows_to_dl = new_without_pdf
    if args.limit:
        rows_to_dl = rows_to_dl.head(args.limit)

    print(f"\nTargeting {len(rows_to_dl)} downloads"
          + (f" (--limit {args.limit})" if args.limit else ""))

    if not args.fetch:
        print("\n[dry-run] Pass --fetch to resolve catalogs and download.")
        # Show what we'd download.
        for _, row in rows_to_dl.head(20).iterrows():
            cat = cat_cache.get(row["cas"], "?")
            print(f"  {row['cas']:<20} {row['normalized_name']:<35} "
                  f"method={row['cas_method']}  catalog={cat}")
        if len(rows_to_dl) > 20:
            print(f"  ... and {len(rows_to_dl) - 20} more")
        return 0

    # ── Fetch mode ────────────────────────────────────────────────────────
    pc_session = requests.Session()
    pc_session.headers["User-Agent"] = "MedraReagentPipeline/1.0 (research)"

    downloaded = 0
    failed_cat = 0
    failed_dl  = 0

    for i, (_, row) in enumerate(rows_to_dl.iterrows()):
        cas    = row["cas"]
        name   = row["normalized_name"]
        method = row["cas_method"]

        # ── Resolve Sigma catalog number ───────────────────────────────────
        if cas in cat_cache:
            catalog = cat_cache[cas]  # may be None (previously tried, not found)
        elif method == "sigma_sid":
            # Catalog is in the identifier column of the ranked CSV.
            catalog = _clean_catalog(row.get("identifier", ""))
            cat_cache[cas] = catalog
            save_json(CAT_CACHE, cat_cache)
        else:
            # Name-resolved: find Sigma catalog via PubChem substance lookup.
            # CID is stored in pc_cache under "name:{normalized_name}".
            nkey = f"name:{name.lower().strip()}"
            cid  = pc_cache.get(nkey, {}).get("cid")
            catalog = None
            if cid:
                catalog = find_sigma_catalog_for_cid(int(cid), pc_session)
            cat_cache[cas] = catalog
            save_json(CAT_CACHE, cat_cache)

        # ── Download PDF ───────────────────────────────────────────────────
        pdf_path = SDS_DIR / f"{cas}.pdf"
        if pdf_path.exists():
            print(f"[{i+1}/{len(rows_to_dl)}] skip (PDF exists) {cas}  {name}")
            continue

        if not catalog:
            print(f"[{i+1}/{len(rows_to_dl)}] no catalog  {cas}  {name}")
            failed_cat += 1
            continue

        ok, detail = download_pdf(cas, catalog)
        if ok:
            size_kb = pdf_path.stat().st_size // 1024
            print(f"[{i+1}/{len(rows_to_dl)}] OK  {cas}  {name}  "
                  f"catalog={catalog}/{detail}  {size_kb} KB", flush=True)
            downloaded += 1
        else:
            print(f"[{i+1}/{len(rows_to_dl)}] FAIL  {cas}  {name}  "
                  f"catalog={catalog}  reason={detail}", flush=True)
            failed_dl += 1

    print(f"\n─────────────────────────────")
    print(f"Downloaded:   {downloaded}")
    print(f"No catalog:   {failed_cat}")
    print(f"Download fail:{failed_dl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
