#!/usr/bin/env python3
"""
ingest_batch.py — run the full ingestion pipeline on new SDS PDFs.

For every PDF in data/sds-pdfs/<CAS>.pdf that doesn't yet have a
corresponding data/reagents/<CAS>.json, runs the pipeline in batch:

  Phase A — per-PDF:
    parse_sds.py <pdf>        → data/reagents/<CAS>.json  (flat v1 JSON)

  Phase B — batch (all new files together):
    migrate_to_sourced_flags.py --write   (wraps flat booleans; idempotent)
    enrich_sds_sources.py <json> ...      (adds sds_phrase/ghs_hcode sources)
    enrich_chebi.py <json> ...            (adds pubchem/chebi sources)
    apply_overrides.py --write            (applies phase2-overrides.yaml)

  Phase C — per-file:
    apply_rules.py --write-bench-knowledge <json>

  Phase D — final check:
    validate.py

Usage:
    python tools/ingest_batch.py              # dry-run: list pending PDFs
    python tools/ingest_batch.py --run        # execute the pipeline
    python tools/ingest_batch.py --run --limit 5   # test on 5 first
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT    = Path(__file__).parent.parent
SDS_DIR      = REPO_ROOT / "data" / "sds-pdfs"
REAGENTS_DIR = REPO_ROOT / "data" / "reagents"
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


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--run",   action="store_true",
                   help="Execute pipeline (default: dry-run).")
    p.add_argument("--limit", type=int, default=None,
                   help="Process at most N PDFs (for testing).")
    args = p.parse_args(argv)

    # Existing CAS-keyed JSON records.
    existing_json = {
        f.stem for f in REAGENTS_DIR.glob("*.json")
        if re.match(r"^\d+(?:-\d+){1,2}$", f.stem)
    }

    # PDFs without matching JSON, sorted by CAS for reproducibility.
    pending = sorted(
        f for f in SDS_DIR.glob("*.pdf")
        if re.match(r"^\d+(?:-\d+){1,2}$", f.stem)
        and f.stem not in existing_json
    )
    if args.limit:
        pending = pending[:args.limit]

    print(f"PDFs pending ingestion: {len(pending)}")
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
        cas       = pdf.stem
        json_path = REAGENTS_DIR / f"{cas}.json"
        r = run([PYTHON, str(INGEST / "parse_sds.py"), str(pdf)], "parse_sds", check=False)
        if r.returncode == 0 and json_path.exists():
            parsed_paths.append(json_path)
            print(f"  [{i+1}/{len(pending)}] OK    {cas}")
        else:
            parse_failed.append(cas)
            last_err = (r.stderr or r.stdout or "").strip().splitlines()
            print(f"  [{i+1}/{len(pending)}] FAIL  {cas}"
                  + (f"  — {last_err[-1]}" if last_err else ""))

    print(f"  Parsed: {len(parsed_paths)}  Failed: {len(parse_failed)}")
    if not parsed_paths:
        print("Nothing to continue with.")
        return 1

    # ── Phase B: batch enrichment ─────────────────────────────────────────
    json_str_list = [str(p) for p in parsed_paths]

    print(f"\n── Phase B: batch enrichment ({len(parsed_paths)} files) ──")

    print("  migrate_to_sourced_flags...", end=" ", flush=True)
    r = run([PYTHON, str(TOOLS / "migrate_to_sourced_flags.py"), "--write"],
            "migrate")
    print("OK" if r.returncode == 0 else "FAIL")

    print("  enrich_sds_sources...", end=" ", flush=True)
    r = run([PYTHON, str(INGEST / "enrich_sds_sources.py")] + json_str_list,
            "enrich_sds")
    print("OK" if r.returncode == 0 else "FAIL")

    print("  enrich_chebi...", end=" ", flush=True)
    r = run([PYTHON, str(INGEST / "enrich_chebi.py")] + json_str_list,
            "enrich_chebi")
    print("OK" if r.returncode == 0 else "FAIL")

    print("  apply_overrides...", end=" ", flush=True)
    r = run([PYTHON, str(TOOLS / "apply_overrides.py"), "--write"],
            "apply_overrides")
    print("OK" if r.returncode == 0 else "FAIL")

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

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{'─'*40}")
    print(f"Parsed OK:    {len(parsed_paths)}")
    print(f"Parse failed: {len(parse_failed)}" +
          (f"  {parse_failed[:5]}" if parse_failed else ""))
    print(f"Rules failed: {len(rules_failed)}" +
          (f"  {rules_failed[:5]}" if rules_failed else ""))

    return 0 if (not parse_failed and not rules_failed) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
