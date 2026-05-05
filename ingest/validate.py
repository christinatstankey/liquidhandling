#!/usr/bin/env python3
"""
validate.py — check every reagent JSON in data/reagents/ against ingest/schema.json.

For schema v2.0 records also runs:
  - Confidence reconciliation: recomputes confidence from sources and warns if stored
    value disagrees.
  - Cross-flag consistency: category ↔ flag invariants.

Usage:
    python ingest/validate.py              # validate all reagents
    python ingest/validate.py --cas 64-17-5  # validate one reagent
"""
import argparse
import json
import sys
from pathlib import Path

import jsonschema

REPO_ROOT = Path(__file__).parent.parent
SCHEMA_PATH = REPO_ROOT / "ingest" / "schema.json"
REAGENTS_DIR = REPO_ROOT / "data" / "reagents"

# Source types and their confidence tier.
SOURCE_TIER = {
    "sds_phrase": "high", "storage_class": "high", "ghs_hcode": "high",
    "pubchem": "high", "chebi": "high", "uniprot": "high",
    "rule_derived": "inherit",
    "manufacturer_protocol": "medium",
    "tacit_knowledge": "low", "claude_inference": "low",
}

# Category → flag invariants. If category matches, flag.value must be true.
CATEGORY_FLAG_INVARIANTS = [
    ("fluorophore",    "is_fluorophore"),
    ("reducing_agent", "is_reducing_agent"),
    ("fixative",       "is_fixative"),
    ("detergent",      "is_detergent"),
    ("enzyme_glycerol","is_protein"),
]


def _compute_confidence(sources: list[dict], props=None) -> str:
    """
    Derive the correct confidence string from a sources list.

    If the flag has only rule_derived sources (no direct-evidence tier), the
    confidence is inherited from the parent flag named in the rule_derived ref
    (format: "rule_derived:<parent>→<this>").  props must be supplied for
    inheritance to work; if omitted, rule_derived-only flags fall back to 'low'.
    """
    non_inherit: list[tuple[str, bool]] = []
    inherit_refs: list[str] = []

    for s in sources:
        tier = SOURCE_TIER.get(s.get("type", ""), "low")
        if tier == "inherit":
            inherit_refs.append(s.get("ref", ""))
        else:
            non_inherit.append((tier, s.get("agrees", True)))

    # If there are rule_derived (inherit) sources AND no high/medium-tier direct
    # evidence, prefer inherited confidence over low-tier placeholder sources.
    has_substantive = any(t in ("high", "medium") for t, _ in non_inherit)
    if inherit_refs and not has_substantive and props is not None:
        for ref in inherit_refs:
            # Refs look like "rule_derived:is_fluorophore→is_light_sensitive"
            if "→" in ref:
                parent_flag = ref.split(":")[-1].split("→")[0]
                parent_obj = props.get(parent_flag, {})
                if isinstance(parent_obj, dict) and "confidence" in parent_obj:
                    return parent_obj["confidence"]

    if non_inherit:
        high_agrees    = any(t == "high"   and a for t, a in non_inherit)
        high_disagrees = any(t == "high"   and not a for t, a in non_inherit)
        med_agrees     = any(t == "medium" and a for t, a in non_inherit)
        if high_agrees and not high_disagrees:
            return "high"
        if (med_agrees and not high_disagrees) or (high_agrees and high_disagrees):
            return "medium"
        return "low"

    return "low"


def _check_sourced_flags(record: dict) -> list[str]:
    """
    For each sourced_boolean in properties, verify stored confidence matches
    what we'd compute from sources, and check category ↔ flag invariants.
    Returns a list of warning strings.
    """
    warnings: list[str] = []
    props = record.get("properties", {})
    category = record.get("category", "")

    for key, val in props.items():
        if not isinstance(val, dict) or "sources" not in val:
            continue
        stored_conf = val.get("confidence")
        expected_conf = _compute_confidence(val["sources"], props=props)
        if stored_conf and stored_conf != expected_conf:
            warnings.append(
                f"  CONF  {key}: stored='{stored_conf}' computed='{expected_conf}'"
            )

    for cat, flag in CATEGORY_FLAG_INVARIANTS:
        if category == cat:
            flag_obj = props.get(flag, {})
            flag_val = flag_obj.get("value") if isinstance(flag_obj, dict) else flag_obj
            if flag_val is not True:
                warnings.append(
                    f"  INVAR category='{cat}' implies {flag}=true, got {flag_val!r}"
                )

    return warnings


def load_schema() -> dict:
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def validate_file(path: Path, schema: dict) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). errors = schema failures; warnings = v2 checks."""
    with open(path) as f:
        data = json.load(f)
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    error_msgs = [f"  {list(e.path)}: {e.message}" for e in errors]

    warning_msgs: list[str] = []
    if data.get("schema_version") == "2.0":
        warning_msgs = _check_sourced_flags(data)

    return error_msgs, warning_msgs


def main():
    parser = argparse.ArgumentParser(description="Validate reagent JSON files against schema.")
    parser.add_argument("--cas", help="CAS# (or filename stem) of a single reagent to validate")
    args = parser.parse_args()

    schema = load_schema()

    if args.cas:
        paths = list(REAGENTS_DIR.glob(f"{args.cas}.json"))
        if not paths:
            print(f"ERROR: no file found for CAS '{args.cas}' in {REAGENTS_DIR}")
            sys.exit(1)
    else:
        paths = sorted(REAGENTS_DIR.glob("*.json"))
        if not paths:
            print(f"No reagent JSON files found in {REAGENTS_DIR}")
            sys.exit(0)

    passed = failed = warned = 0
    for path in paths:
        errors, warnings = validate_file(path, schema)
        if errors:
            print(f"FAIL  {path.name}")
            for e in errors:
                print(e)
            failed += 1
        elif warnings:
            print(f"WARN  {path.name}")
            for w in warnings:
                print(w)
            warned += 1
            passed += 1
        else:
            print(f"OK    {path.name}")
            passed += 1

    print(f"\n{passed} passed ({warned} with warnings), {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
