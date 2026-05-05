#!/usr/bin/env python3
"""
enrich_neb.py — add manufacturer_protocol sources from the NEB lookup table.

Reads data/neb-lookup.yaml (hand-curated from NEB product pages and NEB's
published heat-inactivation table) and writes into biologic slug JSONs:

  requires_ice       True  (medium confidence, manufacturer_protocol)
  lo_bind_required   True  (medium confidence, manufacturer_protocol)
  glycerol_pct       float flat property
  sds_facts.storage  NEB storage text (if currently empty)
  bench_knowledge    heat-inactivation bullet where applicable

Source type: manufacturer_protocol → medium confidence.
Ref format:  "neb.com/products/<cat_base>:<field>"

Usage:
    python ingest/enrich_neb.py --all              # enrich all NEB slugs
    python ingest/enrich_neb.py --all --dry-run    # show changes without writing
    python ingest/enrich_neb.py data/reagents/antarctic-phosphatase.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

import yaml

REPO_ROOT    = Path(__file__).parent.parent
REAGENTS_DIR = REPO_ROOT / "data" / "reagents"
LOOKUP_PATH  = REPO_ROOT / "data" / "neb-lookup.yaml"

# ── Source-tier helpers (mirrors enrich_chebi.py) ────────────────────────────

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
    high_agrees    = any(t == "high"   and a  for t, a in tiers)
    high_disagrees = any(t == "high"   and not a for t, a in tiers)
    med_agrees     = any(t == "medium" and a  for t, a in tiers)
    if high_agrees and not high_disagrees:
        return "high"
    if (med_agrees and not high_disagrees) or (high_agrees and high_disagrees):
        return "medium"
    return "low"


def _ensure_sourced(raw) -> dict:
    if isinstance(raw, dict) and "sources" in raw:
        return raw
    return {"value": raw, "confidence": "low",
            "sources": [{"type": "claude_inference",
                          "ref": "not_yet_assessed", "agrees": False}]}


def _add_source(flag_obj: dict, src_type: str, ref: str,
                agrees: bool, value=None) -> bool:
    """Append source if not already present; optionally set value. Returns True if added."""
    sources = flag_obj.setdefault("sources", [])
    if any(s.get("type") == src_type and s.get("ref") == ref for s in sources):
        return False
    sources.append({"type": src_type, "ref": ref, "agrees": agrees})
    flag_obj["confidence"] = _compute_confidence(sources)
    if value is not None and flag_obj.get("value") is None:
        flag_obj["value"] = value
    return True


# ── Lookup loading ────────────────────────────────────────────────────────────

def load_lookup() -> dict[str, dict]:
    """Return lookup keyed by slug (filename stem)."""
    entries = yaml.safe_load(LOOKUP_PATH.read_text())
    return {e["slug"]: e for e in entries}


# ── Apply enrichment to one reagent JSON ─────────────────────────────────────

def enrich(reagent: dict, entry: dict) -> tuple[dict, list[str]]:
    """Apply one NEB lookup entry to a reagent dict. Returns (updated, changes)."""
    props     = reagent.setdefault("properties", {})
    changes:  list[str] = []
    cat_base  = entry.get("cat_base", "unknown")
    page_ref  = f"neb.com/products/{cat_base}"

    storage_temp = entry.get("storage_temp") or ""
    glycerol     = entry.get("glycerol_pct")
    heat         = entry.get("heat_inactivate")
    notes        = entry.get("notes")

    # 1. requires_ice: both -20°C and 4°C items need ice during bench handling
    if storage_temp:
        ri = _ensure_sourced(props.get("requires_ice"))
        props["requires_ice"] = ri
        ref = f"{page_ref}:storage_temp:{storage_temp.replace(' ','')}"
        if _add_source(ri, "manufacturer_protocol", ref, agrees=True, value=True):
            changes.append(f"requires_ice ← True (NEB storage: {storage_temp})")

    # 2. lo_bind_required: dilute enzyme proteins adsorb to standard tubes
    lb = _ensure_sourced(props.get("lo_bind_required"))
    props["lo_bind_required"] = lb
    ref_lo = f"{page_ref}:enzyme_protein_adsorption"
    if _add_source(lb, "manufacturer_protocol", ref_lo, agrees=True, value=True):
        changes.append("lo_bind_required ← True (NEB enzyme: adsorption-prone)")

    # 3. glycerol_pct: flat numeric — upgrade from scaffold value if different
    if glycerol is not None:
        current = props.get("glycerol_pct")
        if current != glycerol:
            props["glycerol_pct"] = glycerol
            changes.append(f"glycerol_pct ← {glycerol}%")

    # 4. sds_facts.storage: fill in if currently empty
    sds_facts = reagent.setdefault("sds_facts", {})
    if storage_temp and not sds_facts.get("storage"):
        buf = ""
        if glycerol is not None:
            buf = f" 50 mM KPO4, {glycerol}% glycerol."
        sds_facts["storage"] = f"Store at {storage_temp}.{buf}".strip()
        changes.append(f"sds_facts.storage ← '{sds_facts['storage']}'")

    # 5. bench_knowledge: heat-inactivation bullet (most actionable NEB fact)
    if heat:
        bk = reagent.setdefault("bench_knowledge", [])
        already = any(
            isinstance(b, dict) and "heat" in b.get("because", "").lower()
            for b in bk
        )
        if not already:
            bk.append({
                "rule_id":    None,
                "because":    (
                    f"Heat inactivatable: {heat}. Add to the end of the "
                    "reaction, then heat-inactivate before the next step "
                    "rather than adding EDTA or purifying."
                ),
                "cite":       f"NEB product page: https://www.{page_ref}",
                "confidence": "medium",
            })
            changes.append(f"bench_knowledge += heat_inactivate ({heat})")

    # 6. Handling note as bench_knowledge if present and not already there
    if notes:
        bk = reagent.setdefault("bench_knowledge", [])
        already = any(
            isinstance(b, dict) and notes[:30] in b.get("because", "")
            for b in bk
        )
        if not already:
            bk.append({
                "rule_id":    None,
                "because":    notes,
                "cite":       f"NEB product page: https://www.{page_ref}",
                "confidence": "medium",
            })
            changes.append(f"bench_knowledge += notes")

    return reagent, changes


# ── Main ─────────────────────────────────────────────────────────────────────

def iter_neb_paths(lookup: dict[str, dict]):
    """Yield (json_path, entry) for each slug in the lookup that has a JSON file."""
    for slug, entry in lookup.items():
        json_path = REAGENTS_DIR / f"{slug}.json"
        if json_path.exists():
            yield json_path, entry
        else:
            # Warn only if the slug isn't a known alt/duplicate
            pass


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("files", nargs="*", type=Path,
                    help="Specific JSON files to enrich.")
    ap.add_argument("--all",     action="store_true",
                    help="Enrich all slugs in neb-lookup.yaml.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would change without writing.")
    args = ap.parse_args(argv)

    lookup = load_lookup()

    if args.all:
        targets = list(iter_neb_paths(lookup))
    elif args.files:
        targets = []
        for f in args.files:
            slug  = f.stem
            entry = lookup.get(slug)
            if entry:
                targets.append((Path(f), entry))
            else:
                print(f"WARNING: {slug!r} not in neb-lookup.yaml")
    else:
        ap.print_help()
        return 1

    print(f"Targets: {len(targets)} slugs from neb-lookup.yaml")
    total_changes = 0

    for json_path, entry in targets:
        reagent = json.loads(json_path.read_text())
        name    = reagent.get("name", json_path.stem)
        reagent, changes = enrich(reagent, entry)

        if changes:
            total_changes += len(changes)
            print(f"  {json_path.name}  [{len(changes)} changes]")
            for c in changes:
                print(f"    + {c}")
            if not args.dry_run:
                json_path.write_text(json.dumps(reagent, indent=2) + "\n")
        else:
            print(f"  {json_path.name}  [no changes]")

    mode = "[dry-run] " if args.dry_run else ""
    print(f"\n{mode}Done. {len(targets)} slugs, {total_changes} field updates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
