#!/usr/bin/env python3
"""
fetch_missing_pdfs.py — download SDS PDFs for reagents that have a JSON record
but are missing their local PDF.

Unlike download_sds.py (which targets CAS numbers with no JSON yet), this
script targets CAS numbers that already have data/reagents/<CAS>.json but are
missing data/sds-pdfs/<CAS>.pdf. Uses the same catalog cache and download
logic as download_sds.py.

Usage:
    python tools/fetch_missing_pdfs.py              # dry-run: show what would download
    python tools/fetch_missing_pdfs.py --fetch      # download missing PDFs
    python tools/fetch_missing_pdfs.py --fetch --limit 5   # test on 5 first
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

# Import shared logic from download_sds so we don't duplicate the download,
# catalog-resolution, or failure-ledger code.
from download_sds import (  # noqa: E402
    download_pdf,
    find_sigma_catalog_for_cid,
    _cid_from_cas,
    _clean_catalog,
    _ordered_unique,
    load_json,
    save_json,
    _update_failure_ledger,
    PC_DELAY,
    SDS_DIR,
    REAGENTS_DIR,
    CAT_CACHE,
    FAILURE_LEDGER,
    RS_EXACT_SIGMA,
    RS_ALT_SIGMA,
    RS_MANUAL,
)

import json
import requests
import pandas as pd

IN_CSV   = REPO_ROOT / "research" / "star_methods" / "parsed" / "krt_reagent_products_cas.csv"


def _build_sigma_catalogs(df: pd.DataFrame) -> dict[str, list[str]]:
    """Build CAS → [catalog, ...] map from sigma_sid rows in the input CSV."""
    sigma_rows = df[df["cas_method"] == "sigma_sid"].copy()
    sigma_rows["_name_papers"] = pd.to_numeric(sigma_rows.get("name_n_papers", 0), errors="coerce").fillna(0)
    sigma_rows["_papers"]      = pd.to_numeric(sigma_rows.get("n_papers", 0),      errors="coerce").fillna(0)
    sigma_rows = sigma_rows.sort_values(["_name_papers", "_papers"], ascending=[False, False])

    out: dict[str, list[str]] = {}
    for cas, group in sigma_rows.groupby("cas", sort=False):
        out[cas] = _ordered_unique(
            _clean_catalog(v) for v in group["normalized_identifier"]
        )
    return out


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--fetch", action="store_true",
                   help="Resolve catalogs and download PDFs.")
    p.add_argument("--limit", type=int, default=None,
                   help="Process at most N CAS numbers (for testing).")
    args = p.parse_args(argv)

    # ── Find CAS numbers that need PDFs ───────────────────────────────────
    existing_pdf = {f.stem for f in SDS_DIR.glob("*.pdf")}

    # Read the cas field from each reagent JSON (may differ from filename
    # after CAS-mismatch corrections).
    missing: list[str] = []
    for json_path in sorted(REAGENTS_DIR.glob("*.json")):
        if not re.match(r"^\d+(?:-\d+){1,2}$", json_path.stem):
            continue  # skip non-CAS slugs
        try:
            reagent = json.loads(json_path.read_text())
        except Exception:
            continue
        cas = reagent.get("cas") or json_path.stem
        if cas not in existing_pdf:
            missing.append(cas)

    # Skip known unresolved failures.
    failures = load_json(FAILURE_LEDGER)
    unresolved = {
        f["cas"] for f in failures.get("failures", [])
        if f.get("status") == "unresolved"
    }
    missing = [c for c in missing if c not in unresolved]

    if args.limit:
        missing = missing[:args.limit]

    # ── Load catalog sources ───────────────────────────────────────────────
    cat_cache = load_json(CAT_CACHE)
    sigma_catalogs_by_cas: dict[str, list[str]] = {}
    if IN_CSV.exists():
        df = pd.read_csv(IN_CSV, dtype=str).fillna("")
        sigma_catalogs_by_cas = _build_sigma_catalogs(df)

    cached_count  = sum(1 for c in missing if cat_cache.get(c))
    csv_count     = sum(1 for c in missing if c in sigma_catalogs_by_cas)
    neither_count = sum(1 for c in missing if not cat_cache.get(c) and c not in sigma_catalogs_by_cas)

    print(f"Missing PDFs (excluding unresolved failures): {len(missing)}")
    print(f"  Have cached catalog:  {cached_count}")
    print(f"  In CSV (sigma_sid):   {csv_count}")
    print(f"  Needs PubChem lookup: {neither_count}")
    print(f"  Skipped (unresolved failures): {len(unresolved)}")

    if not args.fetch:
        print("\n[dry-run] First 20 targets:")
        for cas in missing[:20]:
            catalogs = sigma_catalogs_by_cas.get(cas, [])
            cached   = cat_cache.get(cas)
            if cached and cached not in catalogs:
                catalogs = catalogs + [cached]
            cat_str = ",".join(catalogs[:3]) if catalogs else (cached or "?pubchem")
            print(f"  {cas:<22} catalog={cat_str}")
        if len(missing) > 20:
            print(f"  ... and {len(missing) - 20} more")
        print("\nPass --fetch to download.")
        return 0

    # ── Download loop ──────────────────────────────────────────────────────
    pc_session = requests.Session()
    pc_session.headers["User-Agent"] = "MedraReagentPipeline/1.0 (research)"

    downloaded = 0
    failed_cat = 0
    failed_dl  = 0

    for i, cas in enumerate(missing):
        # Build candidate catalog list.
        catalogs = sigma_catalogs_by_cas.get(cas, []).copy()
        cached_catalog = cat_cache.get(cas)
        if cached_catalog:
            catalogs = _ordered_unique(catalogs + [_clean_catalog(cached_catalog)])

        if not catalogs:
            # Try PubChem: CAS → CID → Sigma substance.
            cid = _cid_from_cas(cas, pc_session)
            if cid:
                found = find_sigma_catalog_for_cid(int(cid), pc_session)
                catalogs = [found] if found else []
            if not catalogs:
                print(f"[{i+1}/{len(missing)}] no catalog  {cas}")
                failed_cat += 1
                continue

        # State 1: try each catalog candidate.
        failure_details = []
        resolver_status = RS_MANUAL

        for catalog in catalogs:
            ok, detail = download_pdf(cas, catalog)
            if ok:
                cat_cache[cas] = catalog
                save_json(CAT_CACHE, cat_cache)
                pdf_path = SDS_DIR / f"{cas}.pdf"
                size_kb  = pdf_path.stat().st_size // 1024
                print(f"[{i+1}/{len(missing)}] OK  {cas}  catalog={catalog}/{detail}  {size_kb} KB",
                      flush=True)
                downloaded += 1
                resolver_status = RS_EXACT_SIGMA
                break
            failure_details.append(f"{catalog}: {detail}")
        else:
            # State 2: fresh PubChem lookup for an alternate Sigma SKU.
            cid = _cid_from_cas(cas, pc_session)
            if cid:
                fresh = find_sigma_catalog_for_cid(int(cid), pc_session)
                if fresh and fresh not in catalogs:
                    ok, detail = download_pdf(cas, fresh)
                    if ok:
                        cat_cache[cas] = fresh
                        save_json(CAT_CACHE, cat_cache)
                        pdf_path = SDS_DIR / f"{cas}.pdf"
                        size_kb  = pdf_path.stat().st_size // 1024
                        print(f"[{i+1}/{len(missing)}] OK  {cas}  catalog={fresh}/{detail}  {size_kb} KB"
                              f"  [alt_sku]", flush=True)
                        downloaded += 1
                        resolver_status = RS_ALT_SIGMA
                    else:
                        failure_details.append(f"{fresh}(alt): {detail}")

            if resolver_status == RS_MANUAL:
                cat_cache.setdefault(cas, None)
                save_json(CAT_CACHE, cat_cache)
                print(f"[{i+1}/{len(missing)}] FAIL  {cas}  "
                      f"catalogs={','.join(catalogs)}  "
                      f"reason={' || '.join(failure_details)}", flush=True)
                _update_failure_ledger({
                    "cas": cas,
                    "name": cas,
                    "tried_skus": catalogs,
                    "failure_type": "http_404",
                    "observed_sds_cas": None,
                    "resolver_status": RS_MANUAL,
                    "status": "unresolved",
                })
                failed_dl += 1

    print(f"\n──────────────────────────────")
    print(f"Downloaded:   {downloaded}")
    print(f"No catalog:   {failed_cat}")
    print(f"Download fail:{failed_dl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
