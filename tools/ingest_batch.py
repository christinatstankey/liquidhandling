#!/usr/bin/env python3
"""
ingest_batch.py — run the full ingestion pipeline on new SDS PDFs.

For every PDF in data/sds-pdfs/<CAS-or-slug>.pdf that doesn't yet have a
corresponding data/reagents/<CAS-or-slug>.json, runs the pipeline in batch:

  Phase A — per-PDF:
    parse_sds.py <pdf>        → data/reagents/<CAS>.json  (flat v1 JSON)

  Phase B — batch (all new files together):
    migrate_to_sourced_flags.py --write   (wraps flat booleans; idempotent)
    enrich_sds_sources.py <json> ...      (adds sds_phrase/ghs_hcode sources)
    enrich_chebi.py <json> ...            (adds pubchem/chebi sources)
    apply_overrides.py --write            (applies phase2-overrides.yaml)
    audit_enrichment_coverage.py           (confirms enrichers left provenance)

  Phase C — per-file:
    apply_rules.py --write-bench-knowledge <json>

  Phase D — final check:
    validate.py

Usage:
    python tools/ingest_batch.py              # dry-run: list pending PDFs
    python tools/ingest_batch.py --run        # execute the pipeline
    python tools/ingest_batch.py --run --limit 5   # test on 5 first
    python tools/ingest_batch.py --fresh      # dry-run: show fresh-ingest reset
    python tools/ingest_batch.py --run --fresh  # clear old records, then ingest all SDS PDFs
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT    = Path(__file__).parent.parent
SDS_DIR      = REPO_ROOT / "data" / "sds-pdfs"
REAGENTS_DIR = REPO_ROOT / "data" / "reagents"
HANDLING_DIR = REPO_ROOT / "data" / "handling"
INDEX_PATH   = REPO_ROOT / "data" / "index.json"
INGEST       = REPO_ROOT / "ingest"
TOOLS        = REPO_ROOT / "tools"
PYTHON       = sys.executable


def run(cmd: list[str], label: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 and check:
        print(f"\nERROR in [{label}]")
        if result.stderr.strip():
            for line in result.stderr.strip().splitlines()[-5:]:
                print(" ", line)
    return result


def _json_files(path: Path) -> list[Path]:
    return sorted(path.glob("*.json")) if path.exists() else []


def fresh_reset(run_reset: bool) -> tuple[int, int, bool]:
    """
    Remove canonical generated records so this batch starts from verified PDFs.

    Keeps the source SDS PDFs and all lookup/config files. The published docs
    copy is intentionally left alone; scripts/build.py clears stale docs files
    when the site is rebuilt from the fresh data set.
    """
    reagent_files = _json_files(REAGENTS_DIR)
    handling_files = _json_files(HANDLING_DIR)
    index_exists = INDEX_PATH.exists()

    if not run_reset:
        return len(reagent_files), len(handling_files), index_exists

    for path in reagent_files + handling_files:
        path.unlink()
    if index_exists:
        INDEX_PATH.unlink()

    return len(reagent_files), len(handling_files), index_exists


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--run",   action="store_true",
                   help="Execute pipeline (default: dry-run).")
    p.add_argument("--limit", type=int, default=None,
                   help="Process at most N PDFs (for testing).")
    p.add_argument(
        "--fresh", action="store_true",
        help="Start from verified SDS PDFs by clearing data/reagents/*.json, "
             "data/handling/*.json, and data/index.json before ingestion. "
             "Requires --run to delete anything; dry-run only reports the reset.",
    )
    args = p.parse_args(argv)

    if args.fresh:
        n_reagents, n_handling, had_index = fresh_reset(run_reset=args.run)
        action = "Cleared" if args.run else "Would clear"
        print(
            f"{action} {n_reagents} reagent JSON(s), {n_handling} handling "
            f"profile(s), and {'1' if had_index else '0'} data/index.json file(s)."
        )
        if not args.run:
            print("Fresh reset is dry-run only. Pass --run --fresh to delete and ingest.")

    # Existing JSON records keyed by filename stem. Most are CAS-keyed; a few
    # biologics use stable slugs such as polyclonal-igg.
    existing_json = set() if args.fresh else {f.stem for f in REAGENTS_DIR.glob("*.json")}

    # PDFs without matching JSON, sorted by stem for reproducibility.
    pending = sorted(
        f for f in SDS_DIR.glob("*.pdf")
        if f.stem not in existing_json
    )
    if args.limit is not None:
        pending = pending[:args.limit]

    print(f"PDFs pending ingestion: {len(pending)}")
    if not pending:
        print("Nothing pending.")
        return 0

    if not args.run:
        for f in pending[:30]:
            print(f"  {f.stem}")
        if len(pending) > 30:
            print(f"  ... and {len(pending) - 30} more")
        print("\n[dry-run] Pass --run to execute.")
        return 0

    # ── Phase A: parse each PDF → flat JSON ───────────────────────────────
    print(f"\n── Phase A: parse_sds ({len(pending)} PDFs) ──")
    parsed_paths = []
    parse_failed = []

    for i, pdf in enumerate(pending):
        record_id = pdf.stem
        json_path = REAGENTS_DIR / f"{record_id}.json"
        r = run([PYTHON, str(INGEST / "parse_sds.py"), str(pdf),
                 "--out", str(json_path)], "parse_sds", check=False)
        if r.returncode == 0 and json_path.exists():
            parsed_paths.append(json_path)
            print(f"  [{i+1}/{len(pending)}] OK    {record_id}")
        else:
            parse_failed.append(record_id)
            last_err = (r.stderr or r.stdout or "").strip().splitlines()
            print(f"  [{i+1}/{len(pending)}] FAIL  {record_id}"
                  + (f"  — {last_err[-1]}" if last_err else ""))

    print(f"  Parsed: {len(parsed_paths)}  Failed: {len(parse_failed)}")
    if not parsed_paths:
        print("Nothing to continue with.")
        return 1

    # ── Phase B: batch enrichment ─────────────────────────────────────────
    json_str_list = [str(p) for p in parsed_paths]
    enrich_failed: list[str] = []

    print(f"\n── Phase B: batch enrichment ({len(parsed_paths)} files) ──")

    print("  migrate_to_sourced_flags...", end=" ", flush=True)
    r = run([PYTHON, str(TOOLS / "migrate_to_sourced_flags.py"), "--write"] + json_str_list,
            "migrate")
    status = "OK" if r.returncode == 0 else "FAIL"
    print(status)
    if r.returncode != 0:
        enrich_failed.append("migrate_to_sourced_flags")

    print("  enrich_sds_sources...", end=" ", flush=True)
    r = run([PYTHON, str(INGEST / "enrich_sds_sources.py")] + json_str_list,
            "enrich_sds")
    status = "OK" if r.returncode == 0 else "FAIL"
    print(status)
    if r.returncode != 0:
        enrich_failed.append("enrich_sds_sources")

    print("  enrich_chebi...", end=" ", flush=True)
    r = run([PYTHON, str(INGEST / "enrich_chebi.py")] + json_str_list,
            "enrich_chebi")
    status = "OK" if r.returncode == 0 else "FAIL"
    print(status)
    if r.returncode != 0:
        enrich_failed.append("enrich_chebi")

    print("  apply_overrides...", end=" ", flush=True)
    r = run([PYTHON, str(TOOLS / "apply_overrides.py"), "--write"] + json_str_list,
            "apply_overrides")
    status = "OK" if r.returncode == 0 else "FAIL"
    print(status)
    if r.returncode != 0:
        enrich_failed.append("apply_overrides")

    print("  audit_enrichment_coverage...", end=" ", flush=True)
    r = run(
        [PYTHON, str(TOOLS / "audit_enrichment_coverage.py"),
         "--require-sds-provenance"] + json_str_list,
        "audit_enrichment",
    )
    status = "OK" if r.returncode == 0 else "FAIL"
    print(status)
    if r.stdout.strip():
        for line in r.stdout.strip().splitlines()[-8:]:
            print("   ", line)
    if r.returncode != 0:
        enrich_failed.append("audit_enrichment_coverage")

    # ── Phase C: apply rules per-file ─────────────────────────────────────
    print(f"\n── Phase C: apply_rules ({len(parsed_paths)} files) ──")
    rules_failed = []
    for i, json_path in enumerate(parsed_paths):
        r = run([PYTHON, str(INGEST / "apply_rules.py"),
                 "--write-bench-knowledge", str(json_path)],
                "apply_rules", check=False)
        if r.returncode != 0:
            rules_failed.append(json_path.stem)
            print(f"  FAIL  {json_path.stem}")
    if not rules_failed:
        print(f"  OK ({len(parsed_paths)} files)")

    # ── Phase D: validate ─────────────────────────────────────────────────
    print("\n── Phase D: validate ──")
    r = run([PYTHON, str(INGEST / "validate.py")], "validate")
    for line in r.stdout.strip().splitlines()[-4:]:
        print(" ", line)
    validate_failed = r.returncode != 0

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{'─'*40}")
    print(f"Parsed OK:       {len(parsed_paths)}")
    print(f"Parse failed:    {len(parse_failed)}" +
          (f"  {parse_failed[:5]}" if parse_failed else ""))
    print(f"Enrich failed:   {len(enrich_failed)}" +
          (f"  {enrich_failed}" if enrich_failed else ""))
    print(f"Rules failed:    {len(rules_failed)}" +
          (f"  {rules_failed[:5]}" if rules_failed else ""))
    print(f"Validate failed: {'yes' if validate_failed else 'no'}")

    any_failed = parse_failed or enrich_failed or rules_failed or validate_failed
    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
