#!/usr/bin/env python3
"""
migrate_to_sourced_flags.py — one-shot v1 → v2.0 schema migration.

Wraps every flat tacit boolean in data/reagents/*.json with the sourced_boolean
envelope required by schema v2.0 using a not_yet_assessed placeholder source.
Downstream enrichment passes (enrich_sds_sources, enrich_chebi, apply_overrides)
replace the placeholder with real evidence.

The four new flags added in v2.0 (is_oxidizer, is_air_sensitive,
is_peroxide_forming, is_water_reactive) are initialised as null/not_yet_assessed.

Usage:
    python tools/migrate_to_sourced_flags.py           # dry-run, shows diffs
    python tools/migrate_to_sourced_flags.py --write   # writes files
    python tools/migrate_to_sourced_flags.py --write data/reagents/64-17-5.json
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

# Flags added after initial v2.0 migration — initialised as null for any
# record that doesn't already have them.
NEW_FLAGS = [
    "is_oxidizer", "is_air_sensitive", "is_peroxide_forming", "is_water_reactive",
    "is_rnase_decontaminant", "is_serum",
]

# Older parse_sds.py versions emitted these generic refs as high-confidence
# positive sources even when the stored value was false. They are not valid
# evidence chains; later enrichment passes add specific H-codes or prose refs.
STALE_DERIVED_SOURCE_REFS = {
    "is_flammable": {"flash_point_and_H22x"},
    "is_corrosive": {"H290_H314"},
    "is_volatile": {"vapor_pressure_kPa_20C"},
}


def _wrap(value) -> dict:
    """Wrap a flat boolean/null as a not_yet_assessed sourced_boolean placeholder."""
    return {
        "value":      value,
        "confidence": "low",
        "sources":    [{"type": "claude_inference", "ref": "not_yet_assessed",
                        "agrees": False}],
    }


def _is_stale_parser_source(flag: str, source: dict) -> bool:
    stale_refs = STALE_DERIVED_SOURCE_REFS.get(flag, set())
    return source.get("type") == "ghs_hcode" and source.get("ref") in stale_refs


def _has_stale_parser_source(flag: str, value) -> bool:
    if not isinstance(value, dict):
        return False
    return any(_is_stale_parser_source(flag, source) for source in value.get("sources", []))


def _compute_confidence(sources: list[dict]) -> str:
    high_agrees = any(s.get("type") in {"sds_phrase", "storage_class", "ghs_hcode", "pubchem", "chebi", "uniprot"} and s.get("agrees", True) for s in sources)
    high_disagrees = any(s.get("type") in {"sds_phrase", "storage_class", "ghs_hcode", "pubchem", "chebi", "uniprot"} and not s.get("agrees", True) for s in sources)
    med_agrees = any(s.get("type") == "manufacturer_protocol" and s.get("agrees", True) for s in sources)
    if high_agrees and not high_disagrees:
        return "high"
    if (med_agrees and not high_disagrees) or (high_agrees and high_disagrees):
        return "medium"
    return "low"


def _repair_stale_parser_source(flag: str, value: dict) -> dict:
    sources = [
        source for source in value.get("sources", [])
        if not _is_stale_parser_source(flag, source)
    ]
    if not sources:
        return _wrap(None)

    repaired = dict(value)
    repaired["sources"] = sources
    repaired["confidence"] = _compute_confidence(sources)
    if any(source.get("agrees") is True for source in sources):
        repaired["value"] = True
    return repaired


def migrate_record(record: dict) -> dict:
    """Return a new record dict migrated to schema v2.0."""
    record = dict(record)
    props = dict(record.get("properties", {}))
    was_v2 = record.get("schema_version") == "2.0"

    for flag in TACIT_BOOLEANS:
        raw = props.get(flag)
        if _has_stale_parser_source(flag, raw):
            props[flag] = _repair_stale_parser_source(flag, raw)
            continue
        if isinstance(raw, dict) and "sources" in raw:
            continue  # already migrated
        props[flag] = _wrap(raw)

    for flag in NEW_FLAGS:
        if not was_v2 and (flag not in props or not isinstance(props[flag], dict)):
            props[flag] = _wrap(None)

    record["properties"] = props
    record["schema_version"] = "2.0"
    return record


def main():
    parser = argparse.ArgumentParser(description="Migrate reagent JSONs to schema v2.0.")
    parser.add_argument("json_paths", nargs="*",
                        help="Optional reagent JSON files to migrate. Defaults to all.")
    parser.add_argument("--write", action="store_true",
                        help="Write migrated files (default: dry-run, show diffs only).")
    args = parser.parse_args()

    paths = [Path(p) for p in args.json_paths] if args.json_paths else sorted(REAGENTS_DIR.glob("*.json"))
    if not paths:
        print("No reagent JSON files found.")
        return

    for path in paths:
        record = json.loads(path.read_text())
        migrated = migrate_record(record)
        changed = migrated != record
        if record.get("schema_version") == "2.0" and not changed:
            print(f"skip   {path.name}  (already v2.0)")
            continue

        out = json.dumps(migrated, indent=2) + "\n"

        if args.write:
            path.write_text(out)
            print(f"wrote  {path.name}")
        else:
            old_props = record.get("properties", {})
            changed_flags = [
                k for k in TACIT_BOOLEANS + NEW_FLAGS
                if old_props.get(k) != migrated.get("properties", {}).get(k)
            ]
            print(f"dry    {path.name}  → would update {len(changed_flags)} flag(s): "
                  + ", ".join(changed_flags[:6]) + ("…" if len(changed_flags) > 6 else ""))

    if not args.write:
        print("\n[dry-run] Pass --write to apply changes.")


if __name__ == "__main__":
    main()
