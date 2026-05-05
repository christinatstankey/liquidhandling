#!/usr/bin/env python3
"""
build_handling_profiles.py — batch-generate data/handling/ from all reagent JSONs.

Runs apply_rules.apply_rules() on every record in data/reagents/ and writes
the handling profile to data/handling/<stem>.json.  Idempotent: re-running
overwrites existing profiles.

Usage:
    python tools/build_handling_profiles.py
    python tools/build_handling_profiles.py --dry-run    # show stats, no writes
    python tools/build_handling_profiles.py --min-rules 1  # only report records with >= 1 rule fired
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

REPO_ROOT    = Path(__file__).parent.parent
REAGENTS_DIR = REPO_ROOT / "data" / "reagents"
HANDLING_DIR = REPO_ROOT / "data" / "handling"
RULES_PATH   = REPO_ROOT / "data" / "rules.yaml"

# Import apply_rules from the ingest package directly
sys.path.insert(0, str(REPO_ROOT / "ingest"))
from apply_rules import apply_rules  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--dry-run",   action="store_true", help="Print stats without writing files.")
    ap.add_argument("--min-rules", type=int, default=0,
                    help="Only print records with at least this many rules fired (default: 0 = print all).")
    args = ap.parse_args()

    rules = yaml.safe_load(RULES_PATH.read_text())
    paths = sorted(REAGENTS_DIR.glob("*.json"))

    if not args.dry_run:
        HANDLING_DIR.mkdir(parents=True, exist_ok=True)

    rule_fire_counts: Counter = Counter()   # rule_id → how many records it fired on
    records_by_n_rules: Counter = Counter() # n_rules_fired → how many records
    total_written = 0

    for jf in paths:
        reagent = json.loads(jf.read_text())
        profile = apply_rules(reagent, rules)

        n = len(profile["rules_fired"])
        records_by_n_rules[n] += 1
        for r in profile["rules_fired"]:
            rule_fire_counts[r["id"]] += 1

        if n >= args.min_rules and args.min_rules > 0:
            fired_ids = ", ".join(r["id"] for r in profile["rules_fired"])
            print(f"  {jf.stem:<45}  {n} rule(s): {fired_ids}")

        if not args.dry_run:
            out = HANDLING_DIR / jf.name
            out.write_text(json.dumps(profile, indent=2) + "\n")
            total_written += 1

    # Summary
    print(f"\n{'[dry-run] ' if args.dry_run else ''}Processed {len(paths)} records.")
    if not args.dry_run:
        print(f"Wrote {total_written} profiles to {HANDLING_DIR}/")

    print("\nRules fired distribution (# rules fired → # records):")
    for n_rules in sorted(records_by_n_rules):
        label = "no rules" if n_rules == 0 else f"{n_rules} rule(s)"
        print(f"  {label:<15} {records_by_n_rules[n_rules]:>4} records")

    print("\nPer-rule fire frequency (records that triggered each rule):")
    for rule_id, count in rule_fire_counts.most_common():
        print(f"  {rule_id:<45} {count:>4}")


if __name__ == "__main__":
    main()
