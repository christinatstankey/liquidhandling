#!/usr/bin/env python3
"""
enrich_chebi.py — add ChEBI ontology sources to v2.0 reagent JSONs.

Primary source: data/chebi-lookup.yaml — curated CAS → ChEBI role mappings.
Each role adds a high-confidence 'chebi' source to the relevant sourced_boolean
flag in the reagent JSON.

A rule-derived corollary is also applied: if is_fluorophore is confirmed true
by ChEBI, is_light_sensitive is set via a rule_derived source (fluorophores
are photosensitive by definition).

Usage:
    python ingest/enrich_chebi.py data/reagents/3483-12-3.json
    python ingest/enrich_chebi.py data/reagents/3483-12-3.json --dry-run
    python ingest/enrich_chebi.py --all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT      = Path(__file__).parent.parent
REAGENTS_DIR   = REPO_ROOT / "data" / "reagents"
LOOKUP_PATH    = REPO_ROOT / "data" / "chebi-lookup.yaml"

# ── Source-tier table ────────────────────────────────────────────────────────
SOURCE_TIER = {
    "sds_phrase": "high", "storage_class": "high", "ghs_hcode": "high",
    "pubchem": "high", "chebi": "high",
    "rule_derived": "inherit",
    "manufacturer_protocol": "medium",
    "tacit_knowledge": "low", "claude_inference": "low",
}


def _compute_confidence(sources: list[dict]) -> str:
    tiers = []
    for s in sources:
        tier = SOURCE_TIER.get(s.get("type", ""), "low")
        if tier != "inherit":
            tiers.append((tier, s.get("agrees", True)))
    high_agrees    = any(t == "high" and a  for t, a in tiers)
    high_disagrees = any(t == "high" and not a for t, a in tiers)
    med_agrees     = any(t == "medium" and a for t, a in tiers)
    if high_agrees and not high_disagrees:
        return "high"
    if (med_agrees and not high_disagrees) or (high_agrees and high_disagrees):
        return "medium"
    return "low"


def _add_source(flag_obj: dict, src_type: str, ref: str, agrees: bool) -> bool:
    """Append a source if not already present. Returns True if added."""
    sources = flag_obj.setdefault("sources", [])
    if any(s.get("type") == src_type and s.get("ref") == ref for s in sources):
        return False
    sources.append({"type": src_type, "ref": ref, "agrees": agrees})
    flag_obj["confidence"] = _compute_confidence(sources)
    return True


def _ensure_sourced(raw) -> dict:
    if isinstance(raw, dict) and "sources" in raw:
        return raw
    return {"value": raw, "confidence": "low",
            "sources": [{"type": "claude_inference",
                          "ref": "not_yet_assessed", "agrees": False}]}


def load_lookup() -> dict[str | None, dict]:
    """Return lookup keyed by CAS string (or None for no-CAS entries)."""
    entries = yaml.safe_load(LOOKUP_PATH.read_text())
    return {e["cas"]: e for e in entries}


def enrich(reagent: dict, lookup: dict) -> tuple[dict, list[str]]:
    cas     = reagent.get("cas")
    props   = reagent.get("properties", {})
    changes: list[str] = []

    entry = lookup.get(cas)
    if entry is None:
        return reagent, [f"  (CAS {cas!r} not in chebi-lookup.yaml)"]

    chebi_compound_id = entry.get("chebi_id")  # compound's own ChEBI ID
    roles = entry.get("roles", [])

    for role in roles:
        flag   = role.get("flag")
        agrees = role.get("agrees", True)
        term   = role.get("term", "")
        role_chebi = role.get("role_chebi")

        if not flag:
            continue

        # Build ref: prefer role ChEBI ID; fall back to compound ID + term
        if role_chebi:
            ref = role_chebi
        elif chebi_compound_id:
            ref = f"{chebi_compound_id}:{term}"
        else:
            ref = term

        if flag not in props:
            props[flag] = _ensure_sourced(None)
        else:
            props[flag] = _ensure_sourced(props[flag])

        added = _add_source(props[flag], "chebi", ref, agrees)
        if added:
            if props[flag].get("value") is None and agrees:
                props[flag]["value"] = True
            changes.append(f"  + {flag}: chebi:{ref} ({term!r})")

    # Rule-derived corollary: is_fluorophore=true → is_light_sensitive=true.
    # Confidence is inherited from the parent is_fluorophore flag rather than
    # recomputed from the rule_derived tier (which has no tier weight of its own).
    fluor = props.get("is_fluorophore", {})
    if isinstance(fluor, dict) and fluor.get("value") is True:
        ls_key = "is_light_sensitive"
        if ls_key not in props:
            props[ls_key] = _ensure_sourced(None)
        else:
            props[ls_key] = _ensure_sourced(props[ls_key])
        ref = "rule_derived:is_fluorophore→is_light_sensitive"
        added = _add_source(props[ls_key], "rule_derived", ref, True)
        if added and props[ls_key].get("value") is None:
            props[ls_key]["value"] = True

        # Always sync confidence to parent, not just on first add.
        # rule_derived has no tier weight of its own, so confidence would
        # otherwise stay low even when the parent ChEBI evidence is high.
        parent_conf = fluor.get("confidence", "low")
        if props[ls_key].get("confidence") != parent_conf:
            props[ls_key]["confidence"] = parent_conf
            changes.append(
                f"  + is_light_sensitive: confidence synced to is_fluorophore={parent_conf}"
            )
        elif added:
            changes.append(
                f"  + is_light_sensitive: rule_derived (from is_fluorophore=true, "
                f"confidence={parent_conf})"
            )

    reagent["properties"] = props
    return reagent, changes


def process(json_path: Path, lookup: dict, dry_run: bool = False) -> None:
    reagent = json.loads(json_path.read_text())

    if reagent.get("schema_version") != "2.0":
        print(f"SKIP  {json_path.name}  (not v2.0)")
        return

    updated, changes = enrich(reagent, lookup)
    real_changes = [c for c in changes if c.startswith("  +")]
    info_changes  = [c for c in changes if not c.startswith("  +")]

    if info_changes:
        for c in info_changes:
            print(f"      {json_path.name}: {c.strip()}")

    if not real_changes:
        print(f"OK    {json_path.name}  (no ChEBI sources to add)")
        return

    if dry_run:
        print(f"dry   {json_path.name}  ({len(real_changes)} new source(s))")
    else:
        json_path.write_text(json.dumps(updated, indent=2) + "\n")
        print(f"wrote {json_path.name}  ({len(real_changes)} new source(s))")
    for c in real_changes:
        print(c)


def main():
    parser = argparse.ArgumentParser(description="Add ChEBI sources to reagent JSONs.")
    parser.add_argument("json_paths", nargs="*", help="Reagent JSON file(s)")
    parser.add_argument("--all",     action="store_true", help="Enrich all reagent JSONs")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = parser.parse_args()

    lookup = load_lookup()

    if args.all:
        paths = sorted(REAGENTS_DIR.glob("*.json"))
    elif args.json_paths:
        paths = [Path(p) for p in args.json_paths]
    else:
        parser.print_help()
        sys.exit(1)

    for p in paths:
        process(p, lookup, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
