#!/usr/bin/env python3
"""
apply_overrides.py — apply data/phase2-overrides.yaml to reagent JSONs.

Sets the `category` field and any tacit boolean flags listed in the overrides
file.  All flag updates use source type claude_inference with ref
"phase2_batch_ingest", appended to the existing source list.

Idempotent: running twice won't add duplicate sources.

Usage:
    python tools/apply_overrides.py           # dry-run
    python tools/apply_overrides.py --write   # apply changes
    python tools/apply_overrides.py --write data/reagents/64-17-5.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

REPO_ROOT      = Path(__file__).parent.parent
REAGENTS_DIR   = REPO_ROOT / "data" / "reagents"
OVERRIDES_PATH = REPO_ROOT / "data" / "phase2-overrides.yaml"

SOURCE_TIER = {
    "sds_phrase": "high", "storage_class": "high", "ghs_hcode": "high",
    "pubchem": "high", "chebi": "high", "uniprot": "high",
    "rule_derived": "inherit",
    "manufacturer_protocol": "medium",
    "tacit_knowledge": "low", "claude_inference": "low",
}
INGEST_REF = "phase2_batch_ingest"
INGEST_SRC = {"type": "claude_inference", "ref": INGEST_REF}

CATEGORY_EVIDENCE = {
    "fluorophore": ("flag", "is_fluorophore"),
    "reducing_agent": ("flag", "is_reducing_agent"),
    "fixative": ("flag", "is_fixative"),
    "detergent": ("flag", "is_detergent"),
    "enzyme_glycerol": ("protein_glycerol", None),
    "volatile_solvent": ("flag", "is_volatile"),
    "viscous_reagent": ("viscous", None),
    "adsorption_prone": ("flag", "is_adsorption_prone"),
    "hygroscopic_solid": ("hygroscopic_solid", None),
    "dmso": ("cas", "67-68-5"),
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


def _set_flag(props: dict, flag: str, value: bool | None) -> list[str]:
    """
    Set flag.value and append the ingest source.  Returns list of change strings.
    """
    changes = []
    raw = props.get(flag)

    if isinstance(raw, dict) and "sources" in raw:
        flag_obj = raw
    else:
        flag_obj = {"value": raw, "confidence": "low",
                    "sources": [{"type": "claude_inference",
                                  "ref": "not_yet_assessed", "agrees": False}]}
        props[flag] = flag_obj

    old_value = flag_obj.get("value")
    if old_value != value:
        flag_obj["value"] = value
        changes.append(f"  value {flag}: {old_value!r} → {value!r}")

    # Add ingest source if not already present
    sources = flag_obj.setdefault("sources", [])
    agrees = value is not None
    src_entry = {"type": "claude_inference", "ref": INGEST_REF, "agrees": agrees}
    if not any(s.get("type") == "claude_inference" and s.get("ref") == INGEST_REF
               for s in sources):
        sources.append(src_entry)
        flag_obj["confidence"] = _compute_confidence(sources)
        changes.append(f"  + source for {flag}: claude_inference:{INGEST_REF}")

    return changes


def _flag_value(props: dict, flag: str):
    raw = props.get(flag)
    if isinstance(raw, dict) and "value" in raw:
        return raw["value"]
    return raw


def _category_has_evidence(reagent: dict, category: str) -> bool:
    """Return True when existing sourced/numeric evidence justifies a category."""
    props = reagent.get("properties", {})
    mode, key = CATEGORY_EVIDENCE.get(category, (None, None))

    if mode == "flag":
        return _flag_value(props, key) is True
    if mode == "protein_glycerol":
        glycerol = props.get("glycerol_pct") or 0
        return _flag_value(props, "is_protein") is True and glycerol >= 20
    if mode == "viscous":
        viscosity = props.get("viscosity_mPas_20C")
        return props.get("viscosity_class") == "high" or (
            isinstance(viscosity, (int, float)) and viscosity > 100
        )
    if mode == "hygroscopic_solid":
        return (
            _flag_value(props, "is_hygroscopic") is True
            and reagent.get("physical_state") in {"solid", "powder"}
        )
    if mode == "cas":
        return reagent.get("cas") == key
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("json_paths", nargs="*",
                        help="Optional reagent JSON files to process. Defaults to all.")
    parser.add_argument("--write", action="store_true")
    parser.add_argument(
        "--allow-tacit-overrides", action="store_true",
        help="Allow setting sourced_boolean flags to true/false via claude_inference. "
             "Without this flag, non-null flag values in the overrides file are skipped "
             "to prevent bulk records from acquiring invented tacit values.",
    )
    args = parser.parse_args()

    overrides = yaml.safe_load(OVERRIDES_PATH.read_text())
    by_cas = {
        entry["cas"]: entry
        for entry in overrides
        if entry.get("cas") and entry.get("cas") != "N/A"
    }
    by_slug = {entry["slug"]: entry for entry in overrides if entry.get("slug")}

    paths = [Path(p) for p in args.json_paths] if args.json_paths else sorted(REAGENTS_DIR.glob("*.json"))

    for json_path in paths:
        reagent = json.loads(json_path.read_text())
        cas = reagent.get("cas")
        entry = by_cas.get(cas) if cas else by_slug.get(json_path.stem)
        if entry is None:
            continue

        changes: list[str] = []
        props = reagent.get("properties", {})

        # Set category
        old_cat = reagent.get("category", "")
        new_cat = entry.get("category", "")
        if new_cat and old_cat != new_cat:
            if args.allow_tacit_overrides or _category_has_evidence(reagent, new_cat):
                reagent["category"] = new_cat
                changes.append(f"  category: {old_cat!r} → {new_cat!r}")
            else:
                print(
                    f"  SKIP  category={new_cat!r} for {json_path.name} "
                    "(no supporting sourced/numeric evidence yet)"
                )

        # Set flat numeric/scalar properties (not sourced_boolean — set directly).
        for key, value in entry.get("numeric_properties", {}).items():
            old_val = props.get(key)
            if old_val != value:
                props[key] = value
                changes.append(f"  {key}: {old_val!r} → {value!r}")

        # Set sourced_boolean flags.
        # Non-null values via claude_inference violate the no-invented-tacit-knowledge
        # rule for bulk records; require an explicit opt-in.
        for flag, value in entry.get("flags", {}).items():
            if value is not None and not args.allow_tacit_overrides:
                print(f"  SKIP  {flag}={value!r} (pass --allow-tacit-overrides to set non-null flags)")
                continue
            changes.extend(_set_flag(props, flag, value))

        reagent["properties"] = props

        if not changes:
            print(f"ok    {json_path.name}")
            continue

        if args.write:
            json_path.write_text(json.dumps(reagent, indent=2) + "\n")
            print(f"wrote {json_path.name}")
        else:
            print(f"dry   {json_path.name}")
        for c in changes:
            print(c)

    if not args.write:
        print("\n[dry-run] Pass --write to apply.")


if __name__ == "__main__":
    main()
