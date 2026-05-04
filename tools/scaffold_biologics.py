#!/usr/bin/env python3
"""
scaffold_biologics.py — create minimal v2.0 reagent JSON scaffolds for
biological reagents from the STAR Methods ranked list that have no PubChem CAS.

Reads research/star_methods/parsed/krt_reagents_cas.csv, classifies null-CAS
rows by name pattern, and writes one scaffold JSON per non-kit biological
reagent that doesn't already have a file in data/reagents/.

Categories kept:
  serum         → is_serum=true, is_protein=true
  enzyme        → is_protein=true
  antibody      → is_protein=true, is_adsorption_prone=true
  growth_factor → is_protein=true, is_adsorption_prone=true
  media         → (no flags; added for completeness)

Categories skipped:
  kit           → proprietary mixtures with no meaningful chemical handling
  other         → unrecognised; logged for review

All class-derived flags use source type "tacit_knowledge"; all remaining
sourced_boolean flags initialise as null with "not_yet_assessed".

Usage:
  python tools/scaffold_biologics.py              # dry-run: show counts + samples
  python tools/scaffold_biologics.py --write      # create JSON files
  python tools/scaffold_biologics.py --show-other # list unclassified rows
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

REPO_ROOT  = Path(__file__).parent.parent
IN_CSV     = REPO_ROOT / "research" / "star_methods" / "parsed" / "krt_reagents_cas.csv"
REAGENTS   = REPO_ROOT / "data" / "reagents"

SCHEMA_VERSION = "2.0"

# All sourced_boolean fields defined in ingest/schema.json.
SOURCED_BOOLEANS = [
    "is_protein", "is_volatile", "is_flammable", "is_light_sensitive",
    "is_fluorophore", "is_reducing_agent", "oxidizes_in_solution", "make_fresh",
    "is_detergent", "is_foaming", "is_fixative", "fume_hood_required",
    "is_hygroscopic", "is_deliquescent", "is_corrosive", "is_adsorption_prone",
    "lo_bind_required", "requires_ice", "skin_penetrant", "is_oxidizer",
    "is_air_sensitive", "is_peroxide_forming", "is_water_reactive",
    "is_rnase_decontaminant", "is_serum",
]

# Flags set to true for each class.  Source: tacit_knowledge (class membership).
CLASS_FLAGS: dict[str, dict[str, bool]] = {
    "serum":         {"is_serum": True, "is_protein": True},
    "enzyme":        {"is_protein": True},
    "antibody":      {"is_protein": True, "is_adsorption_prone": True},
    "growth_factor": {"is_protein": True, "is_adsorption_prone": True},
    "media":         {},
}

CAS_NOTES: dict[str, str] = {
    "serum":         "biological mixture; no single CAS",
    "enzyme":        "recombinant protein or enzyme; CAS unavailable or absent from PubChem",
    "antibody":      "antibody; no CAS",
    "growth_factor": "recombinant cytokine or growth factor; CAS unavailable or absent from PubChem",
    "media":         "defined mixture (cell culture medium/supplement); no single CAS",
}

KEEP_CATEGORIES = set(CLASS_FLAGS.keys())  # serum, enzyme, antibody, growth_factor, media


# ── Classification ────────────────────────────────────────────────────────────

def classify(name: str) -> str:
    """
    Classify a reagent name into one of: serum, enzyme, antibody,
    growth_factor, media, kit, other.
    """
    n = name.lower().strip()

    # ── Kit: skip these ───────────────────────────────────────────────────
    if re.search(r"\bkit\b", n):
        return "kit"
    if any(x in n for x in [
        "master mix", "mastermix", "assembly master",
        "quickextract", "trizol", "tri reagent", "qiazol",
        "transfection reagent", "transfection mix",
        "gateway cloning", "topo cloning",
        "cell dissociation buffer",
        "mounting medium", "mounting media", "prolong", "vectashield",
        "slowfade", "fluoromount",
    ]):
        return "kit"

    # ── Serum ─────────────────────────────────────────────────────────────
    # Check for medium/media first: "opti-mem reduced serum medium" is media, not serum.
    if "serum" in n and not any(x in n for x in ["medium", "media", "replacement", "supplement"]):
        return "serum"
    if re.search(r"\bplasma\b", n) and "plasmid" not in n and "medium" not in n:
        return "serum"

    # ── Antibody ──────────────────────────────────────────────────────────
    if any(x in n for x in ["antibody", "antibodies"]):
        return "antibody"
    # "anti-CD3", "anti-mouse", "anti-FLAG" etc. — but not "antimycin",
    # "antibiotic", "antifade"
    if re.search(r"\banti[-\s]\w", n):
        if not any(x in n for x in ["antimycin", "antibiotic", "antimycotic", "antifade",
                                     "antioxidant", "antigen"]):
            return "antibody"
    if re.search(r"\b(igg|igm|iga|igy)\b", n):
        return "antibody"
    if any(x in n for x in ["monoclonal ab", "polyclonal ab", "isotype control",
                              "secondary ab", "primary ab"]):
        return "antibody"

    # ── Growth factor / cytokine ──────────────────────────────────────────
    gf_signals = [
        "il-", "interleukin", "tnf-", "tumor necrosis factor",
        "ifn-", "interferon-", "growth factor", "cytokine", "chemokine",
        "pdgf", "vegf", "egf", "fgf-", "tgf-b", "tgf-a", "ngf", "bdnf",
        "gdnf", "nt-3", "nt-4", "cntf", "g-csf", "m-csf", "gm-csf",
        "thrombopoietin", "erythropoietin", "scf", "stem cell factor",
        "bmp-", " bmp ", "wnt-", "sonic hedgehog", " shh",
        "r-spondin", "rspo", "noggin", "dkk-", "activin-", "follistatin",
        "oncostatin", "leukemia inhibitory factor", " lif",
        "cxcl", " ccl2", " ccl5",
        "insulin-like growth factor", " igf-",
    ]
    if any(x in n for x in gf_signals):
        return "growth_factor"
    # Catch "recombinant <species> <protein>" that didn't match above
    if re.search(r"\brecombinant\b", n) and not re.search(r"\b(trypsin|dnase|rnase|cas9)\b", n):
        return "growth_factor"

    # ── Cell culture media and antibiotic/supplement reagents ─────────────
    media_signals = [
        "dmem", "rpmi", "neurobasal", "opti-mem", "optimem",
        "iscove", "leibovitz", "dulbecco", "roswell park",
        "minimum essential medium", "eagle medium",
        "penicillin-streptomycin", "pen/strep", "pen strep",
        "b-27", "b27 supplement", "n2 supplement", "n-2 supplement",
        "insulin-transferrin-selenium", " its ", "its+",
        "glutamax", "knockout serum replacement", "knockout sr",
        "middlebrook", "gentamicin", "hygromycin", "zeocin",
        "blasticidin", "normocin", "fungizone", "amphotericin",
    ]
    if any(x in n for x in media_signals):
        return "media"
    if re.search(r"\bmedium\b", n) and not any(x in n for x in ["chain", "pore"]):
        return "media"

    # ── Enzyme / recombinant protein ──────────────────────────────────────
    # Skip plain water (nuclease-free, RNase-free) and pure buffers/gels.
    if re.search(r"\b(nuclease|rnase|dnase)-free\s+water\b", n):
        return "kit"
    if re.search(r"\b(reaction\s+buffer|ligation\s+buffer|buffer\s+only)\b", n):
        return "kit"
    if re.search(r"\b(protein\s+gels?|polyacrylamide\s+gel|bis-tris)\b", n):
        return "kit"
    if re.search(r"\b(sensor\s+chip|chromatography\s+column|sephadex|superdex|superose|sepharose\s+cl)\b", n):
        return "kit"
    # Assay substrates and dye reagents are kits, not enzymes.
    if re.search(r"\b(assay\s+reagent|assay\s+substrate|dab\s+substrate|hrp\s+substrate|"
                 r"peroxidase\s+substrate|lysis\s+reagent|electroporation\s+enhancer)\b", n):
        return "kit"
    if "assay dye" in n or "protein assay dye" in n:
        return "kit"

    enzyme_signals = [
        "dnase", "rnase", "collagenase", "dispase", "liberase",
        "trypsin", "elastase", "papain", "hyaluronidase", "chitinase",
        "lysozyme", "proteinase k", "benzonase", "nuclease", "protease",
        "cre recombinase", "flp recombinase",
        "cas9", "cpf1", "cas12", "crispr",
        "luciferase", "galactosidase", "glucuronidase",
        "alkaline phosphatase", "horseradish peroxidase",
        "fibronectin", "laminin", "vitronectin", "entactin",
        "matrigel", "geltrex", "basement membrane",
        "streptavidin", "neutravidin", "avidin",
        "protein a", "protein g", "protein l",
        "albumin",        # BSA, HSA, etc. that didn't resolve via PubChem
        "coenzyme a", "coenzyme ",
        "cytochalasin", "jasplakinolide",  # actin-binding proteins/reagents
        "phalloidin",    # fungal peptide that stains actin
    ]
    if any(x in n for x in enzyme_signals):
        return "enzyme"
    # Generic: any word ending in "-ase" (enzymes are named this way)
    if re.search(r"\b\w{3,}ase\b", n):
        return "enzyme"
    if re.search(r"\b(polymerase|ligase|kinase|phosphatase|synthase|reductase|"
                 r"oxidase|transferase|isomerase|transposase|integrase|helicase)\b", n):
        return "enzyme"

    return "other"


# ── Scaffold builders ─────────────────────────────────────────────────────────

def _null_sourced_bool() -> dict:
    return {
        "value":      None,
        "confidence": "low",
        "sources":    [{"type": "claude_inference", "ref": "not_yet_assessed",
                        "agrees": False}],
    }


def _true_sourced_bool(category: str) -> dict:
    return {
        "value":      True,
        "confidence": "low",
        "sources":    [{"type": "tacit_knowledge",
                        "ref": f"reagent_class:{category}", "agrees": True}],
    }


def build_scaffold(row: "pd.Series", category: str) -> dict:
    raw_name  = str(row.get("normalized_name", "")).strip()
    name      = raw_name.title()   # best-effort title-case
    source    = str(row.get("source",     "")).strip()
    identifier = str(row.get("identifier", "")).strip()
    vendor_example = f"{source} {identifier}".strip()

    true_flags = CLASS_FLAGS.get(category, {})
    props = {
        flag: (_true_sourced_bool(category) if flag in true_flags
               else _null_sourced_bool())
        for flag in SOURCED_BOOLEANS
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "name":           name,
        "cas":            None,
        "cas_note":       CAS_NOTES.get(category, "CAS unavailable"),
        "category":       None,
        "vendor_example": vendor_example,
        "physical_state": "liquid",
        "properties":     props,
        "ghs": {
            "pictograms":        [],
            "signal_word":       None,
            "hazard_statements": [],
        },
        "sds_facts": {
            "storage":           "",
            "ppe":               [],
            "incompatibilities": [],
        },
        "bench_knowledge": [],
    }


def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--write",      action="store_true",
                   help="Write scaffold JSON files (default: dry-run only).")
    p.add_argument("--show-other", action="store_true",
                   help="Print the rows classified as 'other' for review.")
    args = p.parse_args(argv)

    df = pd.read_csv(IN_CSV, dtype=str).fillna("")

    # Work only on rows where CAS is null (not_found or not_queried).
    null_df = df[df["cas"].isna() | (df["cas"] == "")].copy()
    print(f"Null-CAS rows: {len(null_df)}")

    # Classify each row.
    null_df["_class"] = null_df["normalized_name"].apply(classify)

    # Summary table.
    counts = null_df["_class"].value_counts()
    print("\nClassification counts:")
    for cls, n in counts.items():
        action = "write" if cls in KEEP_CATEGORIES else "SKIP"
        print(f"  {cls:<15} {n:>4}  [{action}]")

    if args.show_other:
        other = null_df[null_df["_class"] == "other"]
        print(f"\nOther ({len(other)} rows):")
        for _, r in other.iterrows():
            print(f"  {r['normalized_name']!r:<40}  {r['source']}")

    # Filter to kept categories.
    keep_df = null_df[null_df["_class"].isin(KEEP_CATEGORIES)].copy()
    print(f"\nTotal to scaffold: {len(keep_df)}")

    written = 0
    skipped_exists = 0
    for _, row in keep_df.iterrows():
        category = row["_class"]
        slug     = slugify(row["normalized_name"])
        out_path = REAGENTS / f"{slug}.json"

        if out_path.exists():
            skipped_exists += 1
            continue

        scaffold = build_scaffold(row, category)

        if args.write:
            out_path.write_text(json.dumps(scaffold, indent=2) + "\n")
            written += 1
        else:
            print(f"  dry  [{category}]  {slug}.json")

    if args.write:
        print(f"\nWrote {written} new scaffolds  ({skipped_exists} already existed, skipped)")
    else:
        print(f"\n[dry-run] {len(keep_df) - skipped_exists} would be written"
              f"  ({skipped_exists} already exist)")
        print("Pass --write to create files.")


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
