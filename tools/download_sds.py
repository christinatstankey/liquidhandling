#!/usr/bin/env python3
"""
download_sds.py — download Sigma-Aldrich SDS PDFs for CAS-resolved reagents.

Reads research/star_methods/parsed/krt_reagent_products_cas.csv and
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
import tempfile
from pathlib import Path

# Override the OS-level socket timeout (default 60s) so stalled connections
# don't block longer than our intended application timeout.
socket.setdefaulttimeout(40)

import pandas as pd
import pdfplumber
import requests

REPO_ROOT    = Path(__file__).parent.parent
IN_CSV       = REPO_ROOT / "research" / "star_methods" / "parsed" / "krt_reagent_products_cas.csv"
PC_CACHE     = REPO_ROOT / "research" / "star_methods" / "parsed" / "pubchem_cache.json"
SDS_DIR      = REPO_ROOT / "data" / "sds-pdfs"
REAGENTS_DIR = REPO_ROOT / "data" / "reagents"
CAT_CACHE    = SDS_DIR / "catalog_cache.json"

PUBCHEM    = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
SIGMA_SDS  = "https://www.sigmaaldrich.com/US/en/sds"
BRANDS     = ["sigma", "sigald", "sial"]   # "aldrich" excluded — causes OS-level timeouts
PC_DELAY   = 0.21           # seconds between PubChem requests
DL_DELAY   = 6.0            # base seconds between SDS downloads; jitter added below
DL_TIMEOUT = (10, 35)       # (connect_timeout, read_timeout) — Sigma can take 7-10s to serve a PDF
MIN_PDF_KB = 50             # reject files smaller than this as non-PDFs

# Resolver pipeline states — only EXACT and ALT_SIGMA auto-ingest.
# Everything else stops at manual_review_required.
RS_EXACT_SIGMA   = "exact_sigma_sku"
RS_ALT_SIGMA     = "alternate_sigma_sku_same_cas"
RS_ALT_VENDOR    = "alternate_vendor_same_cas"   # reserved for future use
RS_MANUAL        = "manual_review_required"
AUTO_INGEST_STATES = {RS_EXACT_SIGMA, RS_ALT_SIGMA}

FAILURE_LEDGER   = REPO_ROOT / "data" / "download_failures.json"
CAS_OVERRIDES    = REPO_ROOT / "data" / "sds-cas-overrides.json"


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


def _ordered_unique(values) -> list[str]:
    seen = set()
    out = []
    for value in values:
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


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


def _cid_from_cas(cas: str, session: requests.Session) -> int | None:
    """Look up the primary PubChem CID for a CAS number."""
    url = f"{PUBCHEM}/compound/name/{requests.utils.quote(cas)}/cids/JSON"
    data = _pc_get(session, url)
    if not data:
        return None
    cids = data.get("IdentifierList", {}).get("CID", [])
    return cids[0] if cids else None


def _load_overrides() -> dict[str, dict]:
    """Return override map keyed by intended_cas."""
    raw = load_json(CAS_OVERRIDES)
    return {o["intended_cas"]: o for o in raw.get("overrides", [])}


def _check_override(intended_cas: str, observed_sds_cas: str, overrides: dict) -> bool:
    """True if an explicit approved override exists for this (intended, observed) CAS pair."""
    o = overrides.get(intended_cas)
    return (o is not None
            and o.get("observed_sds_cas") == observed_sds_cas
            and o.get("acceptable_substitution") is True)


def _update_failure_ledger(entry: dict) -> None:
    """Upsert a failure entry (keyed by cas) into data/download_failures.json."""
    ledger = load_json(FAILURE_LEDGER)
    failures = {f["cas"]: f for f in ledger.get("failures", [])}
    failures[entry["cas"]] = entry
    ledger["failures"] = list(failures.values())
    FAILURE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    FAILURE_LEDGER.write_text(json.dumps(ledger, indent=2) + "\n")


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


def _pdf_identity(pdf_path: Path) -> dict:
    """
    Extract enough Sigma SDS identity fields to reject wrong-product PDFs.

    The parser intentionally reads only the first two pages; Section 1 is there
    in Sigma's template, and this keeps validation cheap during batch downloads.
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages[:2])
    except Exception as exc:
        return {"error": type(exc).__name__}

    product_number = None
    cas = None

    m = re.search(r"Product Number\s*:\s*(\S+)", text, re.IGNORECASE)
    if m:
        product_number = _clean_catalog(m.group(1))

    m = re.search(r"CAS-No\.\s*:\s*([\d-]+)", text, re.IGNORECASE)
    if m:
        cas = m.group(1).strip()

    return {"product_number": product_number, "cas": cas}


def _identity_matches(identity: dict, cas: str, catalog: str) -> tuple[bool, str]:
    product_number = identity.get("product_number")
    sds_cas = identity.get("cas")
    clean_catalog = _clean_catalog(catalog)

    if sds_cas and sds_cas != cas:
        return False, f"cas-mismatch(expected={cas}, sds={sds_cas}, product={product_number})"
    if product_number and _clean_catalog(product_number) == clean_catalog:
        return True, f"product={product_number}"
    if sds_cas and sds_cas == cas:
        return True, f"cas={sds_cas}"
    if identity.get("error"):
        return False, f"identity-parse-error({identity['error']})"
    return False, f"identity-mismatch(product={product_number}, cas={sds_cas})"


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
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/pdf,*/*",
    })
    all_reasons = []
    for brand in BRANDS:
        url = f"{SIGMA_SDS}/{brand}/{catalog}"
        try:
            r = session.get(url, timeout=DL_TIMEOUT, allow_redirects=True)
            ct = r.headers.get("Content-Type", "?")[:40]
            is_pdf = r.content[:4] == b"%PDF"
            size_kb = len(r.content) // 1024
            if r.status_code != 200:
                all_reasons.append(f"HTTP {r.status_code}/{brand}")
                continue       # no sleep between brand retries
            if not is_pdf:
                all_reasons.append(f"not-PDF({ct})/{brand}")
                continue
            if size_kb < MIN_PDF_KB:
                all_reasons.append(f"too-small-{size_kb}KB/{brand}")
                continue
            with tempfile.NamedTemporaryFile(
                dir=SDS_DIR, prefix=f".{cas}.", suffix=".pdf", delete=False
            ) as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(r.content)
            identity = _pdf_identity(tmp_path)
            identity_ok, identity_detail = _identity_matches(identity, cas, catalog)
            if not identity_ok:
                tmp_path.unlink(missing_ok=True)
                all_reasons.append(f"{identity_detail}/{brand}")
                continue
            tmp_path.replace(out_path)
            time.sleep(DL_DELAY + random.uniform(0, 4))  # polite gap after success
            return True, f"{brand}/{identity_detail}"
        except Exception as exc:
            all_reasons.append(f"{type(exc).__name__}/{brand}")
            continue           # no sleep between brand retries
    time.sleep(DL_DELAY + random.uniform(0, 4))           # polite gap after all-fail
    return False, " | ".join(all_reasons)


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

    # Resolved rows with unique CAS.
    # Sort sigma_sid rows ahead of name rows before deduplicating so that when
    # the same CAS appears via both methods, the sigma_sid row wins.  This
    # matters because sigma_sid gives the exact CAS Sigma assigned to that
    # specific product (e.g., 2497-59-8 for Triton X-100 lot D6100), while
    # name lookup returns a generic PubChem consensus CAS (e.g., 2315-67-5)
    # that may be wrong for the actual product in the catalog.
    _METHOD_RANK = {"sigma_sid": 0, "name": 1, "not_found": 2}
    df_res = df[df["cas"].notna() & (df["cas"] != "")].copy()
    df_res["_method_rank"] = df_res["cas_method"].map(_METHOD_RANK).fillna(3).astype(int)
    resolved = (
        df_res
        .sort_values("_method_rank")
        .drop_duplicates("cas")
        .drop(columns=["_method_rank"])
        .copy()
    )
    # Sort by concept importance first, then product paper count, so a popular
    # concept with many small-count variants isn't buried behind a less important
    # concept that happens to have one high-count product row.
    resolved["_name_papers"] = pd.to_numeric(resolved["name_n_papers"], errors="coerce").fillna(0)
    resolved["_papers"]      = pd.to_numeric(resolved["n_papers"],      errors="coerce").fillna(0)
    resolved = resolved.sort_values(["_name_papers", "_papers"], ascending=[False, False])
    sigma_catalogs_by_cas = {}
    if not df_res.empty:
        sigma_rows = df_res[df_res["cas_method"] == "sigma_sid"].copy()
        sigma_rows["_name_papers"] = pd.to_numeric(
            sigma_rows["name_n_papers"], errors="coerce"
        ).fillna(0)
        sigma_rows["_papers"] = pd.to_numeric(
            sigma_rows["n_papers"], errors="coerce"
        ).fillna(0)
        sigma_rows = sigma_rows.sort_values(
            ["_name_papers", "_papers"], ascending=[False, False]
        )
        for cas, group in sigma_rows.groupby("cas", sort=False):
            sigma_catalogs_by_cas[cas] = _ordered_unique(
                _clean_catalog(v) for v in group["normalized_identifier"]
            )

    # Filter to new CAS numbers (no JSON and no PDF yet, or PDF exists but no JSON).
    todo = resolved[~resolved["cas"].isin(existing_json)].copy()
    new_without_pdf = todo[~todo["cas"].isin(existing_pdf)]
    new_with_pdf    = todo[todo["cas"].isin(existing_pdf)]

    # Sanity-check the actual download queue.  Name-derived CAS values are useful
    # prioritization hints, but they are not product-specific enough to fetch and
    # ingest as canonical Sigma SDS identity.  Dry-runs show the problem; fetches
    # fail closed so a repopulated Tier 2 cache cannot silently enter ingestion.
    name_in_resolved = (resolved["cas_method"] == "name").sum()
    name_in_download_queue = (new_without_pdf["cas_method"] == "name").sum()
    if name_in_download_queue:
        print(f"WARNING: {name_in_download_queue} name-derived CAS rows in "
              "the download queue. Run clean_pubchem_cache.py + reassemble "
              "before fetching to avoid ingesting non-vendor-confirmed CAS as "
              "canonical.", flush=True)
        if args.fetch:
            print("Refusing to fetch while name-derived CAS rows are queued.",
                  flush=True)
            return 1

    print(f"CAS-resolved unique:       {len(resolved)}", flush=True)
    print(f"  via sigma_sid:           {(resolved['cas_method']=='sigma_sid').sum()}")
    print(f"  via name (weaker):       {name_in_resolved}")
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
            candidates = sigma_catalogs_by_cas.get(row["cas"], [])
            cached = cat_cache.get(row["cas"])
            if cached and cached not in candidates:
                candidates = candidates + [cached]
            cat = ",".join(candidates[:3]) if candidates else cached
            if len(candidates) > 3:
                cat += ",..."
            if cat in ("", None):
                cat = "?"
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

        # ── Resolve Sigma catalog candidates ───────────────────────────────
        catalogs = sigma_catalogs_by_cas.get(cas, []).copy()
        primary_catalog = _clean_catalog(row.get("normalized_identifier", ""))
        if method == "sigma_sid" and primary_catalog:
            catalogs = _ordered_unique([primary_catalog] + catalogs)

        cached_catalog = cat_cache.get(cas)
        if cached_catalog:
            catalogs = _ordered_unique(catalogs + [_clean_catalog(cached_catalog)])

        if method != "sigma_sid" and not catalogs:
            # Name-resolved: find Sigma catalog via PubChem substance lookup.
            # CID is stored in pc_cache under "name:{normalized_name}".
            nkey = f"name:{name.lower().strip()}"
            cid  = pc_cache.get(nkey, {}).get("cid")
            if cid:
                found = find_sigma_catalog_for_cid(int(cid), pc_session)
                catalogs = [found] if found else []

        # ── Download PDF ───────────────────────────────────────────────────
        pdf_path = SDS_DIR / f"{cas}.pdf"
        if pdf_path.exists():
            print(f"[{i+1}/{len(rows_to_dl)}] skip (PDF exists) {cas}  {name}")
            continue

        if not catalogs:
            print(f"[{i+1}/{len(rows_to_dl)}] no catalog  {cas}  {name}")
            cat_cache.setdefault(cas, None)
            save_json(CAT_CACHE, cat_cache)
            failed_cat += 1
            continue

        overrides = _load_overrides()
        failure_details = []
        resolver_status = RS_MANUAL
        observed_sds_cas = None

        # ── State 1: exact_sigma_sku ───────────────────────────────────────
        for catalog in catalogs:
            ok, detail = download_pdf(cas, catalog)
            if ok:
                cat_cache[cas] = catalog
                save_json(CAT_CACHE, cat_cache)
                size_kb = pdf_path.stat().st_size // 1024
                print(f"[{i+1}/{len(rows_to_dl)}] OK  {cas}  {name}  "
                      f"catalog={catalog}/{detail}  {size_kb} KB", flush=True)
                downloaded += 1
                resolver_status = RS_EXACT_SIGMA
                break

            # Extract observed CAS from identity-mismatch messages for override check.
            m = re.search(r"cas-mismatch\(expected=[\d-]+, sds=([\d-]+)", detail)
            if m:
                observed_sds_cas = m.group(1)
                if _check_override(cas, observed_sds_cas, overrides):
                    # Explicit approved override — re-download accepting the SDS CAS.
                    # download_pdf already deleted the temp file; need a flag to skip check.
                    # For now: log as overridden and continue — PDF already written on first
                    # successful brand hit (the identity check stopped it). Caller must
                    # re-run with the override SKU once the override is in place.
                    pass

            failure_details.append(f"{catalog}: {detail}")
        else:
            # State 1 exhausted.  Try State 2: fresh Sigma catalog via CAS → CID → substance.
            # ── State 2: alternate_sigma_sku_same_cas ─────────────────────
            cid = _cid_from_cas(cas, pc_session)
            if cid:
                fresh_catalog = find_sigma_catalog_for_cid(cid, pc_session)
                if fresh_catalog and fresh_catalog not in catalogs:
                    ok, detail = download_pdf(cas, fresh_catalog)
                    if ok:
                        cat_cache[cas] = fresh_catalog
                        save_json(CAT_CACHE, cat_cache)
                        size_kb = pdf_path.stat().st_size // 1024
                        print(f"[{i+1}/{len(rows_to_dl)}] OK  {cas}  {name}  "
                              f"catalog={fresh_catalog}/{detail}  {size_kb} KB  "
                              f"[{RS_ALT_SIGMA}]", flush=True)
                        downloaded += 1
                        resolver_status = RS_ALT_SIGMA
                    else:
                        failure_details.append(f"{fresh_catalog}(alt): {detail}")

            if resolver_status == RS_MANUAL:
                # States 1 and 2 failed.  Log to ledger; do not ingest.
                cat_cache.setdefault(cas, None)
                save_json(CAT_CACHE, cat_cache)
                failure_type = "cas_mismatch" if observed_sds_cas else "http_404"
                print(f"[{i+1}/{len(rows_to_dl)}] FAIL  {cas}  {name}  "
                      f"catalogs={','.join(catalogs)}  "
                      f"reason={' || '.join(failure_details)}", flush=True)
                _update_failure_ledger({
                    "cas": cas,
                    "name": name,
                    "tried_skus": catalogs,
                    "failure_type": failure_type,
                    "observed_sds_cas": observed_sds_cas,
                    "resolver_status": RS_MANUAL,
                    "status": "unresolved",
                })
                failed_dl += 1

    print(f"\n─────────────────────────────")
    print(f"Downloaded:   {downloaded}")
    print(f"No catalog:   {failed_cat}")
    print(f"Download fail:{failed_dl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
