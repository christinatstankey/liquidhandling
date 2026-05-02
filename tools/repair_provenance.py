#!/usr/bin/env python3
"""
repair_provenance.py — one-shot data cleanup for three provenance bugs.

Problems fixed:
  1. Phase 2 records carry legacy_handauthored_mvp sources — replace with
     not_yet_assessed (only MVP-10 records should have legacy provenance).
  2. All records: legacy_handauthored_mvp sources have agrees=True even when
     the flag value is False — fix agrees to reflect the actual value.
  3. Seven records: is_corrosive=True sourced solely by ghs_hcode:H318 —
     H318 (serious eye damage) does not imply bulk corrosive behavior;
     remove H318 from is_corrosive sources and reset the value/confidence.

Usage:
    python tools/repair_provenance.py           # dry-run
    python tools/repair_provenance.py --write   # apply changes
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
REAGENTS_DIR = REPO_ROOT / "data" / "reagents"

MVP_10_CAS = {
    "9012-90-2", "64-17-5", "56-81-5", "9005-64-5", "28718-90-3",
    "3483-12-3", "30525-89-4", "67-68-5", "N/A", "1310-73-2",
}

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
    high_agrees    = any(t == "high"   and a for t, a in tiers)
    high_disagrees = any(t == "high"   and not a for t, a in tiers)
    med_agrees     = any(t == "medium" and a for t, a in tiers)
    if high_agrees and not high_disagrees:
        return "high"
    if (med_agrees and not high_disagrees) or (high_agrees and high_disagrees):
        return "medium"
    return "low"


def _repair_flag(flag_obj: dict, is_mvp: bool) -> list[str]:
    """
    Apply all three repairs to one sourced_boolean object.
    Returns list of human-readable change descriptions (empty = no changes).
    """
    changes: list[str] = []
    sources = flag_obj.get("sources", [])
    value   = flag_obj.get("value")

    new_sources: list[dict] = []
    for src in sources:
        ref  = src.get("ref", "")
        stype = src.get("type", "")

        # Repair 1: Phase 2 records must not carry legacy_handauthored_mvp.
        if not is_mvp and ref == "legacy_handauthored_mvp":
            replacement = {"type": "claude_inference", "ref": "not_yet_assessed", "agrees": False}
            new_sources.append(replacement)
            changes.append(f"legacy_handauthored_mvp → not_yet_assessed")
            continue

        # Repair 2: Fix agrees=True when value is not True on legacy source.
        if ref == "legacy_handauthored_mvp":
            correct_agrees = (value is True)
            if src.get("agrees") != correct_agrees:
                src = dict(src)
                src["agrees"] = correct_agrees
                changes.append(f"legacy_handauthored_mvp agrees {not correct_agrees}→{correct_agrees}")

        # Repair 3: Remove H318-sourced is_corrosive (handled at call site below).
        new_sources.append(src)

    if new_sources != sources:
        flag_obj["sources"] = new_sources
        flag_obj["confidence"] = _compute_confidence(new_sources)

    return changes


def _repair_h318_corrosive(props: dict) -> list[str]:
    """
    Remove ghs_hcode:H318 from is_corrosive.sources.
    If no high-tier agrees sources remain, reset value to None.
    Returns change descriptions.
    """
    ic = props.get("is_corrosive")
    if not isinstance(ic, dict):
        return []

    sources = ic.get("sources", [])
    h318_present = any(s.get("type") == "ghs_hcode" and s.get("ref") == "H318"
                       for s in sources)
    if not h318_present:
        return []

    changes = ["is_corrosive: removed H318 source"]
    new_sources = [s for s in sources
                   if not (s.get("type") == "ghs_hcode" and s.get("ref") == "H318")]

    # If no high-tier source remains that affirms True, clear the True value.
    has_affirming_high = any(
        SOURCE_TIER.get(s.get("type", ""), "low") == "high" and s.get("agrees", False)
        for s in new_sources
    )
    if not has_affirming_high and ic.get("value") is True:
        ic["value"] = None
        changes.append("is_corrosive: value True→None (no corroborating high-tier source)")

    ic["sources"] = new_sources
    ic["confidence"] = _compute_confidence(new_sources)
    return changes


def repair_record(record: dict) -> tuple[dict, list[str]]:
    """Apply all repairs to one reagent record. Returns (record, all_changes)."""
    cas    = record.get("cas") or "N/A"
    is_mvp = cas in MVP_10_CAS
    props  = record.get("properties", {})
    all_changes: list[str] = []

    for flag_name, flag_obj in props.items():
        if not isinstance(flag_obj, dict) or "sources" not in flag_obj:
            continue
        flag_changes = _repair_flag(flag_obj, is_mvp)
        for c in flag_changes:
            all_changes.append(f"  {flag_name}: {c}")

    # Repair 3: H318 → is_corrosive (applies to all records).
    h318_changes = _repair_h318_corrosive(props)
    for c in h318_changes:
        all_changes.append(f"  {c}")

    record["properties"] = props
    return record, all_changes


def main():
    parser = argparse.ArgumentParser(description="Repair provenance bugs in reagent JSONs.")
    parser.add_argument("--write", action="store_true",
                        help="Write repaired files (default: dry-run).")
    args = parser.parse_args()

    total_changed = 0
    for path in sorted(REAGENTS_DIR.glob("*.json")):
        record = json.loads(path.read_text())
        if record.get("schema_version") != "2.0":
            print(f"SKIP  {path.name}  (not v2.0)")
            continue

        repaired, changes = repair_record(record)
        if not changes:
            print(f"ok    {path.name}")
            continue

        total_changed += 1
        if args.write:
            path.write_text(json.dumps(repaired, indent=2) + "\n")
            print(f"wrote {path.name}  ({len(changes)} change(s))")
        else:
            print(f"dry   {path.name}  ({len(changes)} change(s))")
        for c in changes[:8]:
            print(c)
        if len(changes) > 8:
            print(f"  ... and {len(changes) - 8} more")

    if not args.write:
        print(f"\n[dry-run] {total_changed} file(s) would change. Pass --write to apply.")
    else:
        print(f"\nRepaired {total_changed} file(s).")


if __name__ == "__main__":
    main()
