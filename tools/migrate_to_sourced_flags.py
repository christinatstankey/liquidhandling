#!/usr/bin/env python3
"""
migrate_to_sourced_flags.py — one-shot v1 → v2.0 schema migration.

Wraps every tacit boolean in data/reagents/*.json with the sourced_boolean
envelope required by schema v2.0:
  { "value": <old_value>, "confidence": "medium", "sources": [LEGACY_SOURCE] }

The four new flags added in v2.0 (is_oxidizer, is_air_sensitive,
is_peroxide_forming, is_water_reactive) are initialised as null with an
honest "not_yet_assessed" source — subsequent enrichment passes (prose
detector, ChEBI) will upgrade them.

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

# Source used for existing hand-authored values. confidence: medium because
# the values came from chemical knowledge, not from an extracted source.
LEGACY_SOURCE = {
    "type": "claude_inference",
    "ref":  "legacy_handauthored_mvp",
    "agrees": True,
}

# Placeholder source for new flags that have not yet been assessed.
UNASSESSED_SOURCE = {
    "type": "claude_inference",
    "ref":  "not_yet_assessed",
    "agrees": False,
}


def _wrap(value) -> dict:
    """Wrap a flat boolean/null as a sourced_boolean with the legacy source."""
    return {
        "value":      value,
        "confidence": "medium" if value is not None else "low",
        "sources":    [LEGACY_SOURCE],
    }


def _unassessed() -> dict:
    """Initialise a new flag as null/unassessed."""
    return {
        "value":      None,
        "confidence": "low",
        "sources":    [UNASSESSED_SOURCE],
    }


def migrate_record(record: dict) -> dict:
    """Return a new record dict migrated to schema v2.0."""
    record = dict(record)
    props = dict(record.get("properties", {}))

    for flag in TACIT_BOOLEANS:
        raw = props.get(flag)
        # Already migrated (e.g. this script run twice) — skip.
        if isinstance(raw, dict) and "sources" in raw:
            continue
        props[flag] = _wrap(raw)

    for flag in NEW_FLAGS:
        if flag not in props:
            props[flag] = _unassessed()
        elif not isinstance(props[flag], dict):
            props[flag] = _unassessed()

    record["properties"] = props
    record["schema_version"] = "2.0"
    return record


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

        migrated = migrate_record(record)
        out = json.dumps(migrated, indent=2) + "\n"

        if args.write:
            path.write_text(out)
            print(f"wrote  {path.name}")
        else:
            # Dry-run: show which flags changed.
            old_flags = {k: record.get("properties", {}).get(k)
                         for k in TACIT_BOOLEANS + NEW_FLAGS}
            changed = [k for k in old_flags
                       if not isinstance(old_flags[k], dict)]
            print(f"dry    {path.name}  → would wrap {len(changed)} flag(s): "
                  + ", ".join(changed[:6]) + ("…" if len(changed) > 6 else ""))

    if not args.write:
        print("\n[dry-run] Pass --write to apply changes.")


if __name__ == "__main__":
    main()
