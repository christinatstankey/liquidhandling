#!/usr/bin/env python3
"""
clean_pubchem_cache.py — trim pubchem_cache.json to a known-good state.

Rules applied:
  KEEP  sigma: keys with a resolved CAS (all dates)   — product-specific, reliable
  KEEP  sigma: failures queried TODAY                  — used clean identifiers, legitimate
  CLEAR sigma: failures queried before today           — may predate identifier normalization
                                                         or be transient; worth retrying
  CLEAR all name: keys                                 — full clean slate for Tier 2

Always backs up to pubchem_cache.pre_clean.json before writing.

Usage:
    python research/star_methods/scripts/clean_pubchem_cache.py [--dry-run]
"""
from __future__ import annotations
import argparse
import json
from datetime import date
from pathlib import Path

CACHE_PATH  = Path(__file__).resolve().parent.parent / "parsed" / "pubchem_cache.json"
BACKUP_PATH = CACHE_PATH.with_suffix(".pre_clean.json")

TODAY = str(date.today())


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be removed without writing.")
    args = p.parse_args(argv)

    cache = json.loads(CACHE_PATH.read_text())
    original_count = len(cache)

    kept   = {}
    cleared = {"sigma_old_failures": [], "name": []}

    for key, val in cache.items():
        if key.startswith("sigma:"):
            has_cas    = bool(val.get("cas"))
            queried_at = val.get("queried_at", "")
            if has_cas:
                kept[key] = val          # always keep resolved sigma_sid
            elif queried_at == TODAY:
                kept[key] = val          # today's failures used clean identifiers
            else:
                cleared["sigma_old_failures"].append(key)  # retry candidate
        elif key.startswith("name:"):
            cleared["name"].append(key)  # full Tier 2 reset
        else:
            kept[key] = val              # unknown prefix: leave alone

    print(f"Cache entries before: {original_count}")
    print(f"  KEEP  sigma successes (all dates):   {sum(1 for k in kept if k.startswith('sigma:') and kept[k].get('cas'))}")
    print(f"  KEEP  sigma failures (today):        {sum(1 for k in kept if k.startswith('sigma:') and not kept[k].get('cas'))}")
    print(f"  CLEAR sigma failures (old session):  {len(cleared['sigma_old_failures'])}")
    print(f"  CLEAR name: keys (Tier 2 reset):     {len(cleared['name'])}")
    print(f"Cache entries after:  {len(kept)}")

    if args.dry_run:
        print("\n[dry-run] No files written.")
        return 0

    # Backup before writing.
    BACKUP_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")
    print(f"\nBacked up to {BACKUP_PATH.name}")

    CACHE_PATH.write_text(json.dumps(kept, indent=2, sort_keys=True) + "\n")
    print(f"Wrote cleaned cache → {CACHE_PATH.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
