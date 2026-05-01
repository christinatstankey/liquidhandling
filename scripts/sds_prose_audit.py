#!/usr/bin/env python3
"""
sds_prose_audit.py — Scan all SDS PDFs for prose phrases that map to
tacit reagent flags, and report coverage vs. the existing reagent JSONs.

This is a one-off audit (not part of the parser) to evaluate whether
extending parse_sds.py to mine Section 7/10 prose is worth the effort.

Output: a wide CSV with one row per reagent and one column per detected
flag, plus a summary printed to stdout.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "ingest"))
from parse_sds import extract_full_text, split_sections  # noqa: E402

PDF_DIR = REPO_ROOT / "data" / "sds-pdfs"
JSON_DIR = REPO_ROOT / "data" / "reagents"

# Phrase patterns. Each entry maps a *flag* to a list of (regex, sections)
# tuples. `sections` restricts where the phrase counts; () means anywhere.
# Regexes are case-insensitive and use \b word boundaries where helpful.
#
# A flag fires if ANY of its phrases match in the allowed sections.
PHRASE_PATTERNS: dict[str, list[tuple[str, tuple[int, ...]]]] = {
    # Storage stability hints (Section 7) — these are the cleanest hits
    "is_hygroscopic": [
        (r"\bhygroscopic\b", (7,)),
    ],
    "is_deliquescent": [
        (r"\bdeliquescent\b", (7, 9, 10)),
    ],
    "is_light_sensitive": [
        (r"\blight[- ]sensitive\b", (7, 10)),
        (r"\bprotect(ed)? from light\b", (7,)),
        (r"\bstore in (the )?dark\b", (7,)),
        (r"\bConditions to avoid\s*:[^\n]*\bLight\b", (10,)),
    ],
    "is_air_sensitive": [
        (r"\bair[- ]sensitive\b", (7, 10)),
        (r"\bstore under (inert gas|nitrogen|argon)\b", (7,)),
        (r"\bConditions to avoid\s*:[^\n]*\bAir\b", (10,)),
    ],
    "is_heat_sensitive": [
        (r"\bheat[- ]sensitive\b", (7, 10)),
    ],
    "is_peroxide_forming": [
        (r"\bperoxide formation\b", (7, 10)),
        (r"\bformation of peroxides\b", (7, 10)),
        (r"\btest for peroxide", (7, 10)),
    ],
    "is_oxidizer": [
        # Sigma's Storage class line: "5.1B, Oxidizing hazardous materials"
        (r"\bStorage class\s*:\s*5\.\d", (7,)),
        (r"\boxidizing (hazardous )?material", (7,)),
    ],
    "is_water_reactive": [
        (r"\breacts violently with water\b", (7, 10)),
        (r"\bwater[- ]reactive\b", (7, 10)),
        # Sigma idiom: "reaction partners of water" listed as violent reaction
        (r"\breaction partners of water\b", (10,)),
    ],
    "is_pyrophoric": [
        (r"\bpyrophoric\b", (7, 10)),
        (r"\bignites? (spontaneously )?(on|in) contact with air\b", (7, 10)),
    ],
    "fume_hood_required": [
        (r"\bwork under (the |a )?hood\b", (7, 8)),
        (r"\bfume hood\b", (7, 8)),
        (r"\bavoid generation of vapou?rs?/aerosols?\b", (7,)),
    ],
    # Categorical class hints — weaker but useful as tiebreakers
    "is_corrosive_storage_hint": [
        (r"\bStorage class\s*:\s*8", (7,)),  # Sigma class 8 = corrosive
    ],
    "is_dry_storage_hint": [
        # "Tightly closed. Dry." — weak signal that something is moisture-sensitive
        (r"storage conditions[^\n]*\n[^\n]*Dry\b", (7,)),
        (r"\bkeep dry\b", (7,)),
    ],
}

# Flags we ALSO want to track from the existing JSON, to compare with prose
JSON_FLAGS = [
    "is_hygroscopic",
    "is_deliquescent",
    "is_light_sensitive",
    "is_reducing_agent",
    "is_fluorophore",
    "make_fresh",
    "fume_hood_required",
    "is_corrosive",
    "is_fixative",
    "oxidizes_in_solution",
]


def scan_pdf(pdf_path: Path) -> dict[str, list[str]]:
    """
    Scan a single PDF and return a dict of flag → list of matched phrases
    (with the section they were found in).  Empty list = flag not fired.
    """
    text = extract_full_text(pdf_path)
    sections = split_sections(text)

    hits: dict[str, list[str]] = {flag: [] for flag in PHRASE_PATTERNS}
    for flag, patterns in PHRASE_PATTERNS.items():
        for regex, allowed_sections in patterns:
            search_targets = (
                [(n, sections.get(n, "")) for n in allowed_sections]
                if allowed_sections else [(0, text)]
            )
            for sec_num, sec_text in search_targets:
                if not sec_text:
                    continue
                m = re.search(regex, sec_text, re.IGNORECASE)
                if m:
                    snippet = m.group(0).strip()[:60]
                    hits[flag].append(f"S{sec_num}:{snippet}")
    return hits


def load_existing_json(cas_or_slug: str) -> dict | None:
    p = JSON_DIR / f"{cas_or_slug}.json"
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def main() -> None:
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    print(f"Scanning {len(pdfs)} SDS PDFs...\n")

    results: list[dict] = []
    for pdf in pdfs:
        slug = pdf.stem
        hits = scan_pdf(pdf)
        existing = load_existing_json(slug)
        ex_props = existing.get("properties", {}) if existing else {}
        ex_name = existing.get("name") if existing else None
        ex_category = existing.get("category") if existing else None

        results.append({
            "slug": slug,
            "name": ex_name,
            "category": ex_category,
            "has_json": existing is not None,
            "hits": hits,
            "json_flags": {k: ex_props.get(k) for k in JSON_FLAGS},
        })

    # ── Per-reagent table ──
    flag_cols = list(PHRASE_PATTERNS.keys())
    print("PROSE HITS PER REAGENT")
    print("-" * 100)
    header = f"{'slug':<18} {'name':<28} " + " ".join(
        f"{f[:14]:<15}" for f in flag_cols
    )
    print(header)
    print("-" * len(header))
    for r in results:
        cells = []
        for flag in flag_cols:
            n = len(r["hits"][flag])
            cells.append("✓" if n else "·")
        name_short = (r["name"] or r["slug"])[:27]
        print(f"{r['slug']:<18} {name_short:<28} " + " ".join(
            f"{c:<15}" for c in cells
        ))

    # ── Per-flag tallies ──
    print("\n\nPER-FLAG SUMMARY")
    print("-" * 80)
    for flag in flag_cols:
        firing = [r for r in results if r["hits"][flag]]
        print(f"\n{flag}: {len(firing)}/{len(results)} reagents")
        for r in firing:
            phrases = ", ".join(r["hits"][flag][:2])
            print(f"   {r['slug']:<14} {(r['name'] or '?')[:30]:<32} {phrases}")

    # ── Cross-check: prose vs existing JSON ──
    print("\n\nCROSS-CHECK: PROSE vs EXISTING JSON (10 reagents with JSONs)")
    print("-" * 80)
    overlap_flags = ["is_hygroscopic", "is_light_sensitive",
                     "fume_hood_required"]
    for flag in overlap_flags:
        print(f"\n{flag}:")
        for r in results:
            if not r["has_json"]:
                continue
            prose = bool(r["hits"][flag])
            json_val = r["json_flags"].get(flag)
            agreement = (prose and json_val is True) or (
                not prose and json_val is False
            )
            tag = "agree" if agreement else "DISAGREE"
            print(f"   {tag:<10} {r['slug']:<14} {(r['name'] or '?')[:30]:<32} "
                  f"prose={prose}  json={json_val}")


if __name__ == "__main__":
    main()
