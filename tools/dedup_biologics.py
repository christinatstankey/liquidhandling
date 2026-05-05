#!/usr/bin/env python3
"""
dedup_biologics.py — merge semantically duplicate biologic scaffold JSONs.

Identifies synonym clusters among slug-named reagent JSON files, keeps the
highest-frequency entry as canonical, and deletes the rest.

Merging strategy (applied in order):
  1. Vendor-prefix stripping: remove leading brand names (Gibco, HyClone, ...)
  2. Known synonym map: explicit expansions (fbs → fetal bovine serum, etc.)
  3. Uninformative-suffix stripping: trailing " solution", " reagent", etc.
  4. Fuzzy match (SequenceMatcher ≥ 0.97) on normalized form

Modifier protection: names containing functional-modifier words (heat-
inactivated, charcoal-stripped, dialyzed, high-glucose, serum-free, etc.)
are never merged with names that lack those words.

Canonical name = the entry with the highest paper count in the CSV.

Usage:
    python tools/dedup_biologics.py              # dry-run: show clusters + count
    python tools/dedup_biologics.py --write      # delete non-canonical files
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path

import pandas as pd

REPO_ROOT    = Path(__file__).parent.parent
IN_CSV       = REPO_ROOT / "research" / "star_methods" / "parsed" / "krt_reagents_cas.csv"
REAGENTS_DIR = REPO_ROOT / "data" / "reagents"

# ---------------------------------------------------------------------------
# Vendor prefixes to strip from the start of a name (longest first).
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Explicit pairs that must NEVER be merged, even if fuzzy ratio is high.
# Cytokine subtypes, product versions, and other "looks similar, different thing" cases.
# Each entry is a frozenset of two normalized names.
# ---------------------------------------------------------------------------
NEVER_MERGE: set[frozenset] = {
    # M-CSF vs GM-CSF (different cytokines)
    frozenset({"recombinant human m-csf",  "recombinant human gm-csf"}),
    frozenset({"recombinant mouse m-csf",  "recombinant mouse gm-csf"}),
    frozenset({"recombinant murine m-csf", "recombinant murine gm-csf"}),
    # IL subtypes — same species, different numbers
    frozenset({"recombinant mouse il-2",  "recombinant mouse il-12"}),
    frozenset({"recombinant mouse il-2",  "recombinant mouse il-23"}),
    frozenset({"recombinant mouse il-12", "recombinant mouse il-23"}),
    frozenset({"recombinant human il-2",  "recombinant human il-12"}),
    frozenset({"recombinant human il-2",  "recombinant human il-21"}),
    frozenset({"recombinant human il-12", "recombinant human il-21"}),
    frozenset({"recombinant human il-33", "recombinant human il-3"}),
    # SuperScript versions (different reverse transcriptases)
    frozenset({"superscript ii reverse transcriptase",
               "superscript iii reverse transcriptase"}),
    frozenset({"superscript ii reverse transcriptase",
               "superscript iv reverse transcriptase"}),
    frozenset({"superscript iii reverse transcriptase",
               "superscript iv reverse transcriptase"}),
    # EGF vs VEGF — different growth factors with different receptors
    frozenset({"recombinant human egf", "recombinant human vegf"}),
    # Dispase vs Dispase II (different grades)
    frozenset({"dispase", "dispase ii"}),
    # TGF subtypes
    frozenset({"recombinant human tgf-1", "recombinant human tgf-b1"}),
    frozenset({"recombinant human tgf-β1", "recombinant human tgf-b1"}),
}

VENDOR_PREFIXES = sorted([
    "gibco", "hyclone", "invitrogen", "thermo fisher", "thermo scientific",
    "thermo", "life technologies", "sigma aldrich", "sigma",
    "roche", "miltenyi biotec", "miltenyi", "biolegend",
    "r&d systems", "r&d", "santa cruz biotechnology", "santa cruz",
    "abcam", "jackson immunoresearch", "jackson", "pierce", "purelink",
    "ge healthcare", "merck", "clontech", "takara",
], key=len, reverse=True)

# ---------------------------------------------------------------------------
# Explicit synonym → canonical normalized_name mappings.
# The canonical is the target; keys are names that should map to it.
# If the canonical itself has no slug file, we pick the highest-frequency
# synonym as canonical instead.
# ---------------------------------------------------------------------------
SYNONYMS: dict[str, str] = {
    # FBS
    "fbs":                                        "fetal bovine serum",
    "fcs":                                        "fetal bovine serum",
    "foetal bovine serum":                        "fetal bovine serum",
    "hyclone fetal bovine serum":                 "fetal bovine serum",
    "gibco fetal bovine serum":                   "fetal bovine serum",
    # BSA
    "bovine serum albumine":                      "bovine serum albumin",
    "bovine serum albumin, bsa":                  "bovine serum albumin",
    "bovine serum albumin solution":              "bovine serum albumin",
    "bovine serum albumin fraction v":            "bovine serum albumin",
    "bovine albumin fraction v":                  "bovine serum albumin",
    # Pen-strep
    "penicillin/streptomycin":                    "penicillin-streptomycin",
    "pen strep":                                  "penicillin-streptomycin",
    "penicillin streptomycin solution":           "penicillin-streptomycin",
    "gibco penicillin streptomycin":              "penicillin-streptomycin",
    # TRIzol / TRI Reagent (same phenol-guanidinium chemistry)
    "trizol reagent":                             "trizol",
    "tri reagent":                                "trizol",
    # DNase I
    "dnase1":                                     "dnase i",
    "dnasei":                                     "dnase i",
    "dnase":                                      "dnase i",
    "deoxyribonuclease i":                        "dnase i",
    "deoxyribonuclease i from bovine pancreas":   "dnase i",
    "rnase-free dnase i":                         "dnase i",
    "dnase i from bovine pancreas":               "dnase i",
    # Opti-MEM
    "optimem":                                    "opti-mem",
    "opti-mem i":                                 "opti-mem",
    "opti-mem medium":                            "opti-mem",
    "opti-mem reduced serum medium":              "opti-mem",
    "opti-mem reduced serum media":               "opti-mem",
    "opti-mem i reduced serum medium":            "opti-mem",
    # DMEM
    "dulbecco's modified eagle medium":           "dmem",
    "dulbecco's modified eagle's medium":         "dmem",
    "dulbecco's modified eagles medium":          "dmem",
    "dulbecco's modification of eagle's medium":  "dmem",
    "dmem medium":                                "dmem",
    "dmem media":                                 "dmem",
    "dmem dulbecco's modified eagle medium":      "dmem",
    "dmem dulbecco s modified eagle medium":      "dmem",
    # RPMI 1640
    "rpmi":                                       "rpmi-1640",
    "rpmi1640":                                   "rpmi-1640",
    "rpmi 1640":                                  "rpmi-1640",
    "rpmi 1640 medium":                           "rpmi-1640",
    "rpmi 1640 media":                            "rpmi-1640",
    "rpmi medium 1640":                           "rpmi-1640",
    "rpmi medium":                                "rpmi-1640",
    "roswell park memorial institute medium":     "rpmi-1640",
    "roswell park memorial institute":            "rpmi-1640",
    # B27 — canonical uses spaces to match the JSON name field from the CSV
    "b-27":                                       "b27 supplement",
    "b-27 supplement":                            "b27 supplement",
    # N2 supplement
    "n-2 supplement":                             "n2 supplement",
    # Neurobasal (non-A variant)
    "neurobasal":                                 "neurobasal medium",
    "neurobasal media":                           "neurobasal medium",
    # Neurobasal-A (separate product for adult neurons)
    "neurobasal-a medium":                        "neurobasal-a",
    # Advanced DMEM/F12
    "advanced dmem f-12":                         "advanced dmem/f12",
    "advanced dmem f12":                          "advanced dmem/f12",
    # DMEM/F12
    "dmem/f-12":                                  "dmem/f12",
    "dmem f-12":                                  "dmem/f12",
    "dmem f12 medium":                            "dmem/f12",
    # Proteinase K
    "proteinase k solution":                      "proteinase k",
    "proteinase k recombinant pcr grade":         "proteinase k",
    "proteinase k recombinant":                   "proteinase k",
    # Dispase
    "dispase ii":                                 "dispase",
    "dispase ii powder":                          "dispase",
    # Benzonase
    "benzonase nuclease":                         "benzonase",
    # Accutase
    "accutase solution":                          "accutase",
    "stempro accutase":                           "accutase",
    # Matrigel (vendor-prefixed or redundant "matrix")
    "matrigel matrix":                            "matrigel",
    # Trypsin plain
    "trypsin from bovine pancreas":               "trypsin",
    # Trypsin-EDTA variants (all handle the same way)
    "trypsin-edta 0-25 phenol red":               "trypsin-edta",
    "trypsin-edta 0-05 phenol red":               "trypsin-edta",
    "trypsin-edta 0-25":                          "trypsin-edta",
    "trypsin edta 0 25 phenol red":               "trypsin-edta",
    "trypsin edta 0 05 phenol red":               "trypsin-edta",
    "0 25 trypsin edta":                          "trypsin-edta",
    "0 05 trypsin edta":                          "trypsin-edta",
    "0 25 trypsin":                               "trypsin",
    "0 05 trypsin":                               "trypsin",
    # Collagenase type aliases
    "collagenase type iv":                        "collagenase iv",
    "collagenase type iv powder":                 "collagenase iv",
    "collagenase type 2":                         "collagenase ii",
    # Dynabeads protein A/G (with/without "for immunoprecipitation")
    "dynabeads protein g for immunoprecipitation": "dynabeads protein g",
    "dynabeads protein a for immunoprecipitation": "dynabeads protein a",
    # Protein A beads (format-agnostic for handling)
    "protein a agarose beads":                    "protein a agarose",
    "protein a agarose resin":                    "protein a agarose",
    "protein a agarose":                          "protein a agarose",
    "protein a sepharose beads":                  "protein a sepharose",
    # Protein G beads
    "protein g agarose beads":                    "protein g agarose",
    "protein g sepharose beads":                  "protein g sepharose",
    # Phosphatase inhibitor cocktail (all brands/forms)
    "phosphatase inhibitor":                      "phosphatase inhibitor cocktail",
    "phosphatase inhibitors":                     "phosphatase inhibitor cocktail",
    "phosphatase inhibitor cocktail 2":           "phosphatase inhibitor cocktail",
    "phosphatase inhibitor cocktail 3":           "phosphatase inhibitor cocktail",
    "phosstop phosphatase inhibitor cocktail":    "phosphatase inhibitor cocktail",
    "phosstop phosphatase inhibitor tablets":     "phosphatase inhibitor cocktail",
    "phosstop phosphatase inhibitor":             "phosphatase inhibitor cocktail",
    "phosstop phosphatase inhibitors":            "phosphatase inhibitor cocktail",
    "halt phosphatase inhibitor cocktail":        "phosphatase inhibitor cocktail",
    # Protease inhibitor cocktail (all brands/forms; EDTA-free variants handle identically)
    "protease inhibitor":                         "protease inhibitor cocktail",
    "protease inhibitors":                        "protease inhibitor cocktail",
    "protease inhibitors cocktail":               "protease inhibitor cocktail",
    "complete protease inhibitor cocktail":       "protease inhibitor cocktail",
    "complete protease inhibitor":                "protease inhibitor cocktail",
    "complete protease inhibitor tablets":        "protease inhibitor cocktail",
    "protease inhibitor cocktail tablets":        "protease inhibitor cocktail",
    "edta-free protease inhibitor cocktail":      "protease inhibitor cocktail",
    "edta-free complete protease inhibitor cocktail": "protease inhibitor cocktail",
    "complete edta-free protease inhibitor cocktail": "protease inhibitor cocktail",
    "complete edta-free protease inhibitor":      "protease inhibitor cocktail",
    "complete edta-free protease inhibitor cocktail tablets": "protease inhibitor cocktail",
    "complete mini edta-free protease inhibitor cocktail": "protease inhibitor cocktail",
    "complete mini protease inhibitor cocktail":  "protease inhibitor cocktail",
    "complete protease inhibitor cocktail edta-free": "protease inhibitor cocktail",
    "edta free complete protease inhibitor cocktail": "protease inhibitor cocktail",
    "edta free protease inhibitor cocktail":      "protease inhibitor cocktail",
    "halt protease inhibitor cocktail":           "protease inhibitor cocktail",
    "halt protease inhibitor":                    "protease inhibitor cocktail",
    "protease inhibitor cocktail set iii edta-free": "protease inhibitor cocktail",
    "pierce protease inhibitor mini tablets edta-free": "protease inhibitor cocktail",
    "pierce protease inhibitor tablets":          "protease inhibitor cocktail",
    "pierce protease inhibitor":                  "protease inhibitor cocktail",
    # RNase inhibitors (different brands, identical handling)
    "rnaseout":                                   "rnase inhibitor",
    "rnase out":                                  "rnase inhibitor",
    "rnaseout recombinant ribonuclease inhibitor": "rnase inhibitor",
    "rnase out recombinant ribonuclease inhibitor": "rnase inhibitor",
    "rnaseout recombinant":                       "rnase inhibitor",
    "rnasin plus rnase inhibitor":                "rnase inhibitor",
    "rnasin plus ribonuclease inhibitor":         "rnase inhibitor",
    "rnasin ribonuclease inhibitor":              "rnase inhibitor",
    "superase in rnase inhibitor":                "rnase inhibitor",
    "superase in":                                "rnase inhibitor",
    "ribolock rnase inhibitor":                   "rnase inhibitor",
    "murine rnase inhibitor":                     "rnase inhibitor",
    "recombinant rnase inhibitor":                "rnase inhibitor",
    "recombinant rnasin ribonuclease inhibitor":  "rnase inhibitor",
    "protector rnase inhibitor":                  "rnase inhibitor",
    # Normal serum (same as plain serum for handling purposes)
    "normal goat serum":                          "goat serum",
    "normal donkey serum":                        "donkey serum",
    "normal mouse serum":                         "mouse serum",
    "normal horse serum":                         "horse serum",
    "normal rat serum":                           "rat serum",
    # High-glucose DMEM variants
    "high glucose dmem":                          "dmem high glucose",
    "dulbecco s modified eagle medium high glucose": "dmem high glucose",
    "dmem high glucose glutamax supplement":      "dmem high glucose",
    # Zymolyase
    "zymolyase 20t":                              "zymolyase",
    # RNase A / H spelling variants (rnasea, rnaseh → spaced form)
    "rnasea":                                     "rnase a",
    "rnaseh":                                     "rnase h",
    # Fetal calf serum = fetal bovine serum
    "fetal calf serum":                           "fetal bovine serum",
    # TGF-β1 / TGF-b1: "b1" is the ASCII stand-in for "β1"; both are the same cytokine.
    # Canonical is tgf-β1 (contains the actual unicode character from the CSV).
    "recombinant human tgf-b1":                   "recombinant human tgf-β1",
    # IL-6 with/without hyphen
    "recombinant human il6":                      "recombinant human il-6",
    # Catalase
    "catalase from bovine liver":                 "catalase",
    # Hyaluronidase
    "hyaluronidase from bovine testes":           "hyaluronidase",
    # Papain
    "papain from papaya latex":                   "papain",
    # Phosphate buffered saline
    "dulbecco s pbs":                             "dulbecco s phosphate buffered saline",
    # DMEM/F12 medium
    "dmem f12 glutamax":                          "dmem/f12",
}

# ---------------------------------------------------------------------------
# Modifier patterns: names containing these are never merged with names that
# don't (they're genuinely different products or formulations).
# ---------------------------------------------------------------------------
_MODIFIER_RE = re.compile(
    r"\b(heat[\s\-]?inactivat|charcoal[\s\-]?strip|dialyz|ultra[\s\-]?low[\s\-]?igg|"
    r"without[\s\-]?vitamin|no[\s\-]?glucose|high[\s\-]?glucose|low[\s\-]?glucose|"
    r"growth[\s\-]?factor[\s\-]?reduced|reduced[\s\-]?growth[\s\-]?factor|ldev[\s\-]?free|"
    r"serum[\s\-]?free|charcoal|pregnant|50x|100x|tetracycline[\s\-]?free|"
    r"endotoxin[\s\-]?free|insulin[\s\-]?free)\b",
    re.IGNORECASE,
)

# Uninformative trailing words to strip before fuzzy matching.
_TRAILING_STRIP = re.compile(
    r"\s+(solution|reagent)$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Normalization pipeline
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def normalize(name: str) -> str:
    """Return a normalized form of a reagent name for grouping."""
    n = name.lower().strip()

    # Strip vendor prefix
    for prefix in VENDOR_PREFIXES:
        pattern = r"^" + re.escape(prefix) + r"\s+"
        if re.match(pattern, n):
            n = re.sub(pattern, "", n)
            break

    # Apply explicit synonym map
    if n in SYNONYMS:
        n = SYNONYMS[n]

    # Strip trailing uninformative words
    n = _TRAILING_STRIP.sub("", n)

    return n.strip()


def has_modifier(name: str) -> bool:
    return bool(_MODIFIER_RE.search(name))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--write", action="store_true",
                   help="Delete non-canonical duplicate files (default: dry-run).")
    p.add_argument("--verbose", action="store_true",
                   help="Show all clusters, not just multi-member ones.")
    args = p.parse_args(argv)

    # Load paper counts from CSV.
    df = pd.read_csv(IN_CSV, dtype=str).fillna("")
    df["n"] = pd.to_numeric(df["n_papers_total"], errors="coerce").fillna(0).astype(int)
    name_to_n: dict[str, int] = dict(zip(df["normalized_name"], df["n"]))

    # Collect slug-named JSON files.
    slug_files = [
        f for f in REAGENTS_DIR.glob("*.json")
        if not re.match(r"^\d+(?:-\d+){1,2}$", f.stem)
    ]

    # Build list of (file, original_name, paper_count, normalized_name).
    entries: list[dict] = []
    for f in slug_files:
        rec = json.loads(f.read_text())
        orig = rec.get("name", "").lower().strip()
        # Try to find paper count by original name or slug-reconstructed name.
        n = name_to_n.get(orig, name_to_n.get(f.stem.replace("-", " "), 0))
        norm = normalize(orig)
        entries.append({"file": f, "orig": orig, "norm": norm, "n": n})

    # ── Phase 1: group by exact normalized name ────────────────────────────
    from collections import defaultdict
    groups: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        groups[e["norm"]].append(e)

    # ── Phase 2: fuzzy-merge singleton groups ─────────────────────────────
    # Only merge if neither name has a modifier the other lacks.
    norms = list(groups.keys())
    merged: dict[str, str] = {}  # norm → canonical norm

    for i, a in enumerate(norms):
        if a in merged:
            continue
        for b in norms[i + 1:]:
            if b in merged:
                continue
            # Modifier-protection: if one has a modifier and the other
            # doesn't (or has a different one), block merging.
            a_mod = has_modifier(a)
            b_mod = has_modifier(b)
            if a_mod != b_mod:
                continue
            if frozenset({a, b}) in NEVER_MERGE:
                continue
            ratio = difflib.SequenceMatcher(None, a, b).ratio()
            if ratio >= 0.97:
                # Keep the group with the higher total paper count.
                count_a = sum(e["n"] for e in groups[a])
                count_b = sum(e["n"] for e in groups[b])
                if count_b > count_a:
                    merged[a] = b
                else:
                    merged[b] = a

    # Resolve chains (a→b, b→c becomes a→c).
    def resolve(k):
        seen = set()
        while k in merged and merged[k] != k and k not in seen:
            seen.add(k)
            k = merged[k]
        return k

    for k in list(merged):
        merged[k] = resolve(merged[k])

    # Combine fuzzy-merged groups.
    final_groups: dict[str, list[dict]] = defaultdict(list)
    for norm, members in groups.items():
        canonical = merged.get(norm, norm)
        final_groups[canonical].extend(members)

    # ── Determine canonical file per group ────────────────────────────────
    total_before = len(slug_files)
    to_delete: list[Path] = []
    multi = 0

    for canonical_norm, members in sorted(final_groups.items()):
        members.sort(key=lambda e: e["n"], reverse=True)
        canonical_file = members[0]["file"]

        if len(members) > 1:
            multi += 1
            print(f"\n  ── {canonical_norm!r}  [{len(members)} files]")
            for e in members:
                tag = "KEEP" if e["file"] == canonical_file else "drop"
                print(f"      [{tag}] {e['file'].stem}  (n={e['n']})")
            for e in members[1:]:
                to_delete.append(e["file"])
        elif args.verbose:
            print(f"  ok  {canonical_norm!r}  → {members[0]['file'].stem}")

    total_after = total_before - len(to_delete)
    print(f"\n{'─'*55}")
    print(f"Slug files before dedup:  {total_before}")
    print(f"Duplicate clusters found: {multi}")
    print(f"Files to delete:          {len(to_delete)}")
    print(f"Files after dedup:        {total_after}")

    if not args.write:
        print("\n[dry-run] Pass --write to delete duplicate files.")
        return 0

    for f in to_delete:
        f.unlink()
        print(f"  deleted  {f.name}")

    print(f"\nDeleted {len(to_delete)} files. {total_after} slug files remain.")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
