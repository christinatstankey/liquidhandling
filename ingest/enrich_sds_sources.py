#!/usr/bin/env python3
"""
enrich_sds_sources.py — add SDS-derived sources to a v2.0 reagent JSON.

Reads the reagent's SDS PDF and adds three categories of sources to
sourced_boolean flags:

  ghs_hcode    — H-codes from Section 2 that determine is_flammable,
                 is_corrosive, is_oxidizer, is_volatile
  storage_class — Sigma ADR storage-class line (e.g. "Storage class: 8")
  sds_phrase   — prose hits in Section 7/10 (hygroscopic, light-sensitive, …)

Existing sources are preserved; new sources are appended only if not already
present. Confidence is recomputed from the updated source list and stored.

Usage:
    python ingest/enrich_sds_sources.py data/reagents/3483-12-3.json
    python ingest/enrich_sds_sources.py data/reagents/3483-12-3.json --dry-run
    python ingest/enrich_sds_sources.py --all        # all reagents with SDSs
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "ingest"))
from parse_sds import extract_full_text, split_sections  # noqa: E402

PDF_DIR    = REPO_ROOT / "data" / "sds-pdfs"
REAGENTS_DIR = REPO_ROOT / "data" / "reagents"

# ── Source-tier table (mirrors validate.py) ─────────────────────────────────
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


# ── GHS H-code → flag derivations ───────────────────────────────────────────
# Each entry: (flag_name, agrees, set_value_to)
#   agrees=True  → this H-code supports flag=True
#   agrees=False → would be used if we ever want to record a contradiction;
#                  not needed here since we only fire when the code IS present.
HCODE_FLAG_MAP: dict[str, list[tuple[str, bool]]] = {
    "H224": [("is_flammable", True), ("is_volatile", True)],
    "H225": [("is_flammable", True), ("is_volatile", True)],
    "H226": [("is_flammable", True)],
    "H228": [("is_flammable", True)],
    "H290": [("is_corrosive", True)],
    "H314": [("is_corrosive", True)],
    # H318 = serious eye damage — eye-specific injury, not bulk corrosive behavior.
    # Mapping H318 to is_corrosive overfires (SDS, Triton, DTT, 2-ME all have it).
    # H314 (skin corrosion/burns) and H290 (corrosive to metals) are correct signals.
    "H270": [("is_oxidizer", True)],
    "H271": [("is_oxidizer", True)],
    "H272": [("is_oxidizer", True)],
}

# ── Storage-class → flag derivations ────────────────────────────────────────
# Sigma Section 7 line: "Storage class : 8B Corrosive hazardous materials"
STORAGE_CLASS_MAP: list[tuple[str, str, bool]] = [
    (r"Storage class\s*:\s*8",      "is_corrosive", True),
    (r"Storage class\s*:\s*5\.1",   "is_oxidizer",  True),
    (r"Storage class\s*:\s*5\.2",   "is_peroxide_forming", True),
    (r"Storage class\s*:\s*4\.3",   "is_water_reactive", True),
]

# ── Prose-phrase → flag derivations ─────────────────────────────────────────
# Format: (flag_name, regex, allowed_sections, source_type)
# allowed_sections: tuple of section ints to search; () = search full text
PROSE_PATTERNS: list[tuple[str, str, tuple[int, ...], str]] = [
    ("is_hygroscopic",     r"\bhygroscopic\b",                           (7,),     "sds_phrase"),
    ("is_deliquescent",    r"\bdeliquescent\b",                          (7, 9, 10), "sds_phrase"),
    ("is_light_sensitive", r"\blight[- ]sensitive\b",                   (7, 10),  "sds_phrase"),
    ("is_light_sensitive", r"\bprotect(?:ed)? from light\b",            (7,),     "sds_phrase"),
    ("is_light_sensitive", r"\bstore in (?:the )?dark\b",               (7,),     "sds_phrase"),
    ("is_light_sensitive", r"Conditions to avoid\s*:[^\n]*\blight\b",   (10,),    "sds_phrase"),
    ("is_air_sensitive",   r"\bair[- ]sensitive\b",                      (7, 10),  "sds_phrase"),
    ("is_air_sensitive",   r"\bstore under (?:inert gas|nitrogen|argon)\b", (7,), "sds_phrase"),
    ("is_air_sensitive",   r"Conditions to avoid\s*:[^\n]*\bair\b",     (10,),    "sds_phrase"),
    ("is_peroxide_forming",r"\bperoxide formation\b",                    (7, 10),  "sds_phrase"),
    ("is_peroxide_forming",r"\bformation of peroxides\b",               (7, 10),  "sds_phrase"),
    ("is_peroxide_forming",r"\btest for peroxide\b",                    (7, 10),  "sds_phrase"),
    ("is_water_reactive",  r"\breacts violently with water\b",          (7, 10),  "sds_phrase"),
    ("is_water_reactive",  r"\bwater[- ]reactive\b",                    (7, 10),  "sds_phrase"),
    ("fume_hood_required", r"\bwork under (?:the |a )?hood\b",          (7, 8),   "sds_phrase"),
    ("fume_hood_required", r"\bfume hood\b",                             (7, 8),   "sds_phrase"),
    ("fume_hood_required", r"\bavoid (?:breathing|inhalation of) vapou?rs?\b", (7,), "sds_phrase"),
    ("is_oxidizer",        r"\boxidizing (?:hazardous )?material",       (7,),     "storage_class"),
]


# ── Source helpers ───────────────────────────────────────────────────────────

def _source_present(sources: list[dict], src_type: str, ref: str) -> bool:
    return any(s.get("type") == src_type and s.get("ref") == ref for s in sources)


def _add_source(flag_obj: dict, src_type: str, ref: str, agrees: bool) -> bool:
    """Append source if not already present. Returns True if added."""
    if _source_present(flag_obj.get("sources", []), src_type, ref):
        return False
    flag_obj.setdefault("sources", []).append(
        {"type": src_type, "ref": ref, "agrees": agrees}
    )
    # Recompute and store confidence
    flag_obj["confidence"] = _compute_confidence(flag_obj["sources"])
    return True


def _ensure_sourced(flag_obj, current_value) -> dict:
    """Return a sourced_boolean dict, creating one if the field is missing."""
    if isinstance(flag_obj, dict) and "sources" in flag_obj:
        return flag_obj
    return {"value": current_value, "confidence": "low",
            "sources": [{"type": "claude_inference",
                          "ref": "not_yet_assessed", "agrees": False}]}


# ── Main enrichment logic ────────────────────────────────────────────────────

def enrich(reagent: dict, pdf_path: Path) -> tuple[dict, list[str]]:
    """
    Add SDS-derived sources to a reagent record.
    Returns (updated_record, list_of_changes_for_logging).
    """
    props = reagent.get("properties", {})
    changes: list[str] = []

    # --- 1. Parse PDF --------------------------------------------------------
    text = extract_full_text(pdf_path)
    sections = split_sections(text)

    # --- 2. Collect H-codes from GHS section (already in JSON, but re-parse
    #         from text so we can cite the exact code) -------------------------
    h_codes: set[str] = set()
    for stmt in reagent.get("ghs", {}).get("hazard_statements", []):
        m = re.match(r"(H\d{3})", stmt)
        if m:
            h_codes.add(m.group(1))

    # --- 3. GHS H-code sources -----------------------------------------------
    for hcode, flag_pairs in HCODE_FLAG_MAP.items():
        if hcode not in h_codes:
            continue
        for flag, agrees in flag_pairs:
            if flag not in props:
                props[flag] = _ensure_sourced(None, True if agrees else None)
            else:
                props[flag] = _ensure_sourced(props[flag], props[flag] if not isinstance(props[flag], dict) else props[flag].get("value"))
            added = _add_source(props[flag], "ghs_hcode", hcode, agrees)
            if added:
                # If the value was null/unassessed and this source confirms True,
                # set the value — the SDS tells us definitively.
                if props[flag].get("value") is None and agrees:
                    props[flag]["value"] = True
                changes.append(f"  + {flag}: ghs_hcode:{hcode}")

    # --- 4. Storage-class sources (Section 7 text) ---------------------------
    sec7 = sections.get(7, "")
    for pattern, flag, agrees in STORAGE_CLASS_MAP:
        m = re.search(pattern, sec7, re.IGNORECASE)
        if not m:
            continue
        ref = m.group(0).strip()[:60]
        if flag not in props:
            props[flag] = _ensure_sourced(None, True if agrees else None)
        else:
            props[flag] = _ensure_sourced(props[flag], props[flag] if not isinstance(props[flag], dict) else props[flag].get("value"))
        added = _add_source(props[flag], "storage_class", ref, agrees)
        if added:
            if props[flag].get("value") is None and agrees:
                props[flag]["value"] = True
            changes.append(f"  + {flag}: storage_class:{ref[:40]}")

    # --- 5. Prose-phrase sources (Sections 7, 8, 10) -------------------------
    for flag, pattern, allowed_secs, src_type in PROSE_PATTERNS:
        targets = [(n, sections.get(n, "")) for n in allowed_secs] if allowed_secs else [(0, text)]
        for sec_num, sec_text in targets:
            if not sec_text:
                continue
            m = re.search(pattern, sec_text, re.IGNORECASE)
            if not m:
                continue
            snippet = m.group(0).strip()[:60]
            ref = f"section_{sec_num}:{snippet!r}"
            if flag not in props:
                props[flag] = _ensure_sourced(None, True)
            else:
                props[flag] = _ensure_sourced(props[flag], props[flag] if not isinstance(props[flag], dict) else props[flag].get("value"))
            added = _add_source(props[flag], src_type, ref, True)
            if added:
                if props[flag].get("value") is None:
                    props[flag]["value"] = True
                changes.append(f"  + {flag}: {src_type}:S{sec_num}:{snippet[:30]!r}")
            break  # one hit per flag per section set is enough

    reagent["properties"] = props
    return reagent, changes


def _pdf_for_reagent(reagent: dict) -> Path | None:
    """Find the SDS PDF for a reagent by CAS or slug."""
    cas = reagent.get("cas")
    if cas:
        p = PDF_DIR / f"{cas}.pdf"
        if p.exists():
            return p
    # Fallback: try filename stem matching via vendor_example or name
    return None


def process(json_path: Path, dry_run: bool = False) -> None:
    reagent = json.loads(json_path.read_text())

    if reagent.get("schema_version") != "2.0":
        print(f"SKIP  {json_path.name}  (not schema v2.0 — run migration first)")
        return

    pdf_path = _pdf_for_reagent(reagent)
    if pdf_path is None:
        slug = json_path.stem
        # polyclonal-igg special case
        alt = PDF_DIR / f"{slug}.pdf"
        if alt.exists():
            pdf_path = alt
    if pdf_path is None:
        print(f"SKIP  {json_path.name}  (no SDS PDF found)")
        return

    updated, changes = enrich(reagent, pdf_path)

    if not changes:
        print(f"OK    {json_path.name}  (no new sources)")
        return

    if dry_run:
        print(f"dry   {json_path.name}  ({len(changes)} new source(s))")
        for c in changes:
            print(c)
    else:
        json_path.write_text(json.dumps(updated, indent=2) + "\n")
        print(f"wrote {json_path.name}  ({len(changes)} new source(s))")
        for c in changes:
            print(c)


def main():
    parser = argparse.ArgumentParser(description="Add SDS-phrase sources to reagent JSONs.")
    parser.add_argument("json_paths", nargs="*", help="Reagent JSON file(s) to enrich")
    parser.add_argument("--all",     action="store_true", help="Enrich all reagent JSONs")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = parser.parse_args()

    if args.all:
        paths = sorted(REAGENTS_DIR.glob("*.json"))
    elif args.json_paths:
        paths = [Path(p) for p in args.json_paths]
    else:
        parser.print_help()
        sys.exit(1)

    for p in paths:
        process(p, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
