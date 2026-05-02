#!/usr/bin/env python3
"""
migrate_to_sourced_flags.py — one-shot v1 → v2.0 schema migration.

Wraps every tacit boolean in data/reagents/*.json with the sourced_boolean
envelope required by schema v2.0.  The source assigned depends on record origin:

  MVP-10 (hand-authored):   claude_inference:legacy_handauthored_mvp, confidence=medium
  Phase 2+ (SDS-parsed):    claude_inference:not_yet_assessed,         confidence=low

The four new flags added in v2.0 (is_oxidizer, is_air_sensitive,
is_peroxide_forming, is_water_reactive) are always initialised as null with
not_yet_assessed — subsequent enrichment passes will upgrade them.

Usage:
    python tools/migrate_to_sourced_flags.py           # dry-run, shows diffs
    python tools/migrate_to_sourced_flags.py --write   # writes files
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
REAGENTS_DIR = REPO_ROOT / "data" / "reagents"

# The 19 tacit booleans present in v1 JSONs that become sourced_boolean in v2.
TACIT_BOOLEANS = [
    "is_protein", "is_volatile", "is_flammable", "is_light_sensitive",
    "is_fluorophore", "is_reducing_agent", "oxidizes_in_solution", "make_fresh",
    "is_detergent", "is_foaming", "is_fixative", "fume_hood_required",
    "is_hygroscopic", "is_deliquescent", "is_corrosive", "is_adsorption_prone",
    "lo_bind_required", "requires_ice", "skin_penetrant",
]

# Four new flags introduced in v2.0 — not present in v1 JSONs.
NEW_FLAGS = ["is_oxidizer", "is_air_sensitive", "is_peroxide_forming", "is_water_reactive"]

# The original 10 hand-authored reagents.  Only these records should carry
# the legacy_handauthored_mvp provenance source.
MVP_10_CAS = {
    "9012-90-2",   # Taq DNA Polymerase
    "64-17-5",     # Ethanol
    "56-81-5",     # Glycerol
    "9005-64-5",   # Tween-20
    "28718-90-3",  # DAPI
    "3483-12-3",   # DTT
    "30525-89-4",  # PFA
    "67-68-5",     # DMSO
    "N/A",         # polyclonal IgG (no CAS; json has cas: null, keyed as "N/A" here)
    "1310-73-2",   # NaOH
}


def _wrap_mvp(value) -> dict:
    """Sourced boolean for MVP-10 hand-authored values (medium confidence).

    agrees reflects what the source asserts: True means 'source says this flag
    is True'; False means 'source says this flag is not True (False/null).'
    """
    return {
        "value":      value,
        "confidence": "medium" if value is not None else "low",
        "sources":    [{"type": "claude_inference", "ref": "legacy_handauthored_mvp",
                        "agrees": value is True}],
    }


def _wrap_bulk(value) -> dict:
    """Sourced boolean for Phase 2+ SDS-parsed values (low confidence placeholder).

    Uses not_yet_assessed — the downstream enrichment passes (enrich_sds_sources,
    enrich_chebi, apply_overrides) are the authoritative evidence sources.
    """
    return {
        "value":      value,
        "confidence": "low",
        "sources":    [{"type": "claude_inference", "ref": "not_yet_assessed",
                        "agrees": False}],
    }


def _unassessed() -> dict:
    """Initialise a new flag as null/unassessed."""
    return {
        "value":      None,
        "confidence": "low",
        "sources":    [{"type": "claude_inference", "ref": "not_yet_assessed",
                        "agrees": False}],
    }


def migrate_record(record: dict, is_mvp: bool) -> dict:
    """Return a new record dict migrated to schema v2.0."""
    record = dict(record)
    props = dict(record.get("properties", {}))
    wrap = _wrap_mvp if is_mvp else _wrap_bulk

    for flag in TACIT_BOOLEANS:
        raw = props.get(flag)
        # Already migrated (e.g. this script run twice) — skip.
        if isinstance(raw, dict) and "sources" in raw:
            continue
        props[flag] = wrap(raw)

    for flag in NEW_FLAGS:
        if flag not in props:
            props[flag] = _unassessed()
        elif not isinstance(props[flag], dict):
            props[flag] = _unassessed()

    record["properties"] = props
    record["schema_version"] = "2.0"
    return record


def _is_mvp(record: dict) -> bool:
    cas = record.get("cas") or "N/A"
    return cas in MVP_10_CAS


def main():
    parser = argparse.ArgumentParser(description="Migrate reagent JSONs to schema v2.0.")
    parser.add_argument("--write", action="store_true",
                        help="Write migrated files (default: dry-run, show diffs only).")
    args = parser.parse_args()

    paths = sorted(REAGENTS_DIR.glob("*.json"))
    if not paths:
        print("No reagent JSON files found.")
        return

    for path in paths:
        record = json.loads(path.read_text())
        if record.get("schema_version") == "2.0":
            print(f"skip   {path.name}  (already v2.0)")
            continue

        is_mvp = _is_mvp(record)
        migrated = migrate_record(record, is_mvp=is_mvp)
        out = json.dumps(migrated, indent=2) + "\n"

        if args.write:
            path.write_text(out)
            print(f"wrote  {path.name}  ({'mvp' if is_mvp else 'bulk'})")
        else:
            # Dry-run: show which flags changed.
            old_flags = {k: record.get("properties", {}).get(k)
                         for k in TACIT_BOOLEANS + NEW_FLAGS}
            changed = [k for k in old_flags
                       if not isinstance(old_flags[k], dict)]
            print(f"dry    {path.name}  ({'mvp' if is_mvp else 'bulk'})  "
                  f"→ would wrap {len(changed)} flag(s): "
                  + ", ".join(changed[:6]) + ("…" if len(changed) > 6 else ""))

    if not args.write:
        print("\n[dry-run] Pass --write to apply changes.")


if __name__ == "__main__":
    main()
