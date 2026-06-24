"""
rank_reagents.py
================

Phase 3 of the STAR Methods pipeline: deduplicate and frequency-rank
reagents from the parsed KRT rows CSV.

Produces three output files in one run:

1. krt_reagent_names_ranked.csv  — one row per normalized concept name.
   Answers "What reagent is commonly used?"
   Used for: project prioritization, UI statistics.

2. krt_reagent_products_ranked.csv  — one row per (name, source, identifier).
   Answers "Which exact product should we ingest?"
   Used for: SDS download (tools/download_sds.py), CAS resolution
   (crosswalk_pubchem.py), and vendor-specific handling facts.

3. krt_reagent_name_variants.json  — keyed by normalized_name, lists all
   product variants with counts. Used for ingestion selection.

Key design choice: two-layer dedup
------------------------------------
Name-level and product-level dedup answer different questions:
  - Name: "How often do scientists use DMSO?" — groups all vendor mentions
    together to get a concept frequency. Used for prioritization.
  - Product: "Which DTT bottle is this paper citing?" — keeps
    (name, canonical-vendor, stripped-catalog#) distinct so downstream can
    prefer Sigma rows with parsable SDS templates and sigma_sid CAS lookups.

Both layers use per-year normalization:

    normalized_freq(unit, year) =
        papers_citing_unit_in_year / papers_with_any_KRT_row_in_year

The denominator counts all KRT-bearing papers in that year (any category),
not just those with chemical rows, so the fraction is meaningful as a
fraction of the full KRT corpus.

Normalization details
---------------------
  normalize_name:
    Lowercase, collapse whitespace, strip trailing parenthetical vendor
    qualifiers like "(Gibco)" — but only short, non-numeric ones.

  normalize_identifier:
    (a) Replace Unicode dashes (en-dash, em-dash, etc.) with ASCII hyphen.
    (b) Strip catalog-number prefixes: Cat#, Cat# , CAT#, Catalog#,
        Cat. No., and bare # — so "Cat# D9542", "CAT#D9542", and "D9542"
        all become "D9542".
    (c) Uppercase and collapse whitespace.

  normalize_source:
    Maps raw vendor strings to a controlled canonical name using a lookup
    table. Sigma-Aldrich / Sigma / MilliporeSigma / Millipore / Merck →
    "Sigma-Aldrich"; Thermo Fisher Scientific / Invitrogen / Gibco /
    Fisher Scientific → "Thermo Fisher Scientific"; etc. Unrecognized
    strings are title-cased and whitespace-normalized. The canonical name
    is used as the product grouping key; raw source strings are preserved
    in raw_source_examples for auditing.

  --min-papers (default 5): concept-level filter. Products inherit it via
    their parent concept's paper count.

Usage
-----
  python rank_reagents.py                          # default settings
  python rank_reagents.py --sort trend_slope --top 200
  python rank_reagents.py --year-from 2020 --min-papers 2

Dependencies: pandas, numpy
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE          = Path(__file__).resolve().parent
ROOT          = HERE.parent
IN_CSV        = ROOT / "parsed" / "krt_rows.csv"
OUT_NAMES     = ROOT / "parsed" / "krt_reagent_names_ranked.csv"
OUT_PRODUCTS  = ROOT / "parsed" / "krt_reagent_products_ranked.csv"
OUT_VARIANTS  = ROOT / "parsed" / "krt_reagent_name_variants.json"


# ── Normalization helpers ────────────────────────────────────────────────────

# Strip trailing parenthetical vendor/grade qualifiers from names:
# "fetal bovine serum (gibco)" → "fetal bovine serum".
# Only vendor-attribution parens are stripped; formulation qualifiers like
# "(high glucose)" or "(heat inactivated)" are preserved — see normalize_name.
_TRAILING_PAREN_RE = re.compile(r"\s*\([^)]{1,30}\)\s*$")

# Catalog-number prefix patterns to strip before identifier normalization.
# Handles: Cat#, CAT#, Catalog#, Cat. No., Cat number, Catalog number:, Number:.
_CAT_PREFIX = re.compile(
    r"(?i)^(?:"
    r"cat(?:alog)?\s*\.?\s*(?:no\.?|number)?\s*#?\s*:?\s*"  # Cat#, Cat No., Cat number, Catalog number:
    r"|number\s*:?\s*"                                        # Number:, NUMBER:
    r")"
)
_HASH_PREFIX   = re.compile(r"^#\s*")
_COLON_PREFIX  = re.compile(r"^:\s*")    # strips leading ": D9542" → "D9542"

# Unicode non-ASCII dashes → ASCII hyphen.
_UNICODE_DASH = re.compile(
    r"[‐‑‒–—―−﹘﹣－]"
)

# Split compound identifiers on ;, |, / or comma-space.
# Comma-space (not bare comma) to preserve "10,500,064" as a single token.
_IDENT_SPLIT_RE = re.compile(r"\s*(?:[;|/]|,\s+)\s*")

# Metadata keyword prefixes: skip these parts after splitting.
_METADATA_PREFIX_RE = re.compile(
    r"(?i)^(?:cas\s*#?\s*:?\s*|rrid\s*:?\s*|ridd\s*:?\s*"
    r"|lot\s*#?\s*|batch\s*#?\s*|ref\s*:|ab\s*:)"
)

# Trailing space-appended metadata like " CAS: 28718-90-3" or " RRID:AB_123".
# Handles cases where the annotation isn't comma/semicolon-separated.
_TRAILING_META_RE = re.compile(
    r"\s+(?:CAS\s*#?\s*:?\s*\S+|RRID\s*:?\s*\S+|RIDD\s*:?\s*\S+"
    r"|LOT\s*#?\s*\S+|BATCH\s*#?\s*\S+)(?:\s.*)?$",
    re.IGNORECASE,
)

# Pack-size suffixes appended to Sigma catalog numbers: D9542-1MG, D8417-5MG.
_SIZE_SUFFIX_RE = re.compile(
    r"(?i)-\d+\s*(?:NG|UG|MG|G|KG|NL|UL|ML|L|UN|U|PC|EA|PK)S?\s*$"
)

# Collapse letter-prefix + separator + digit-body: "D-9779", "D 9779" → "D9779".
_ALPHA_NUM_CAT_RE = re.compile(r"^([A-Z]{1,4})[-\s](\d{4,})$")

# Vendor canonicalization: (compiled pattern, canonical name).
# Applied in order — first match wins. Unmatched strings fall through to
# the whitespace-normalize-and-title-case fallback.
_VENDOR_CANON: list[tuple[re.Pattern, str]] = [
    # Sigma-Aldrich / MilliporeSigma / Merck (chemical division)
    # Also handles no-separator (SigmaAldrich) and single-char-typo variants
    # (MilliporSigma, MillioporeSigma, MiliporeSigma, Sigman Aldrich).
    (re.compile(r"(?i)\bsigma\b"
                r"|sigma[-\s]?aldrich"          # Sigma-Aldrich, SigmaAldrich, Sigma Aldrich
                r"|sigman\s+aldrich"            # typo: Sigman Aldrich
                r"|mill?i?[io]?pore?\s*sigma"   # MilliporeSigma and single-char variants
                r"|\bmillipore\b|\bmerck\b"),
     "Sigma-Aldrich"),
    # Thermo Fisher Scientific and all acquired brands
    (re.compile(r"(?i)thermo\s*fisher|thermo\s*scientific"
                r"|invitrogen|\bgibco\b|life\s*tech(?:nologies)?"
                r"|fisher\s*scientific|\bfisher\b|\bpierce\b"),
     "Thermo Fisher Scientific"),
    # New England Biolabs
    (re.compile(r"(?i)\bneb\b|new\s*england\s*biolabs"),
     "New England Biolabs"),
    # Qiagen
    (re.compile(r"(?i)\bqiagen\b"),
     "Qiagen"),
    # Bio-Rad
    (re.compile(r"(?i)bio[-\s]?rad\b"),
     "Bio-Rad"),
    # Cell Signaling Technology
    (re.compile(r"(?i)cell\s*signaling|\bcst\b"),
     "Cell Signaling Technology"),
    # BD Biosciences
    (re.compile(r"(?i)bd\s*biosciences|becton\s*dickinson"),
     "BD Biosciences"),
    # BioLegend
    (re.compile(r"(?i)biolegend"),
     "BioLegend"),
    # PeproTech
    (re.compile(r"(?i)peprotech"),
     "PeproTech"),
    # Tocris
    (re.compile(r"(?i)\btocris\b"),
     "Tocris"),
    # Selleck Chemicals
    (re.compile(r"(?i)selleck(?:chem|chemicals?)?"),
     "Selleck Chemicals"),
    # InvivoGen
    (re.compile(r"(?i)invivogen"),
     "InvivoGen"),
    # R&D Systems
    (re.compile(r"(?i)r&d\s*systems"),
     "R&D Systems"),
    # Roche
    (re.compile(r"(?i)\broche\b"),
     "Roche"),
    # Miltenyi Biotec
    (re.compile(r"(?i)miltenyi"),
     "Miltenyi Biotec"),
    # Promega
    (re.compile(r"(?i)\bpromega\b"),
     "Promega"),
    # Cayman Chemical
    (re.compile(r"(?i)\bcayman\b"),
     "Cayman Chemical"),
    # Abcam
    (re.compile(r"(?i)\babcam\b"),
     "Abcam"),
    # MedChemExpress
    (re.compile(r"(?i)medchemexpress|\bmce\b"),
     "MedChemExpress"),
    # Corning
    (re.compile(r"(?i)\bcorning\b"),
     "Corning"),
    # Vector Laboratories
    (re.compile(r"(?i)vector\s*lab"),
     "Vector Laboratories"),
    # Cytiva / GE Healthcare
    (re.compile(r"(?i)\bcytiva\b|ge\s*healthcare"),
     "Cytiva"),
    # VWR
    (re.compile(r"(?i)\bvwr\b"),
     "VWR"),
    # Electron Microscopy Sciences
    (re.compile(r"(?i)electron\s*microscopy\s*sciences|\bems\b"),
     "Electron Microscopy Sciences"),
]


def _is_vendor_parenthetical(inner: str) -> bool:
    """Return True if the (already-lowercased) parenthetical content is a vendor name."""
    for pattern, _ in _VENDOR_CANON:
        if pattern.search(inner):
            return True
    return False


def normalize_name(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    # Strip trailing parens only when the content is a vendor attribution.
    # Formulation qualifiers like "(high glucose)" and "(heat inactivated)"
    # distinguish real product differences, so they stay in the name.
    m = _TRAILING_PAREN_RE.search(s)
    if m:
        inner = m.group(0).strip().strip("()").strip()
        if _is_vendor_parenthetical(inner):
            s = s[:m.start()].strip()
    return s


def normalize_identifier(s: str) -> str:
    """
    Normalize a catalog identifier to a clean, collapsible key.

    Steps applied in order:
    1. Unicode dashes → ASCII hyphen.
    2. Strip Cat#/catalog-No./# prefixes and leading colons.
    3. Uppercase and collapse whitespace.
    4. Split on compound separators (;, |, / or comma-space); take the first
       non-metadata part — discards appended CAS, RRID, Lot, Batch annotations.
    5. Collapse thousands-separator commas in all-digit identifiers.
    6. Strip pack-size suffixes (-1MG, -5ML, etc.).
    7. Collapse letter-prefix + separator + digit-body (D-9779/D 9779 → D9779).
    """
    s = _UNICODE_DASH.sub("-", s).strip()
    s = _CAT_PREFIX.sub("", s)
    s = _HASH_PREFIX.sub("", s)
    s = _COLON_PREFIX.sub("", s)
    s = re.sub(r"\s+", " ", s.strip().upper())

    # Split on compound separators; pick first non-metadata part.
    parts = _IDENT_SPLIT_RE.split(s)
    for part in parts:
        part = part.strip().strip(":").strip()
        if part and not _METADATA_PREFIX_RE.match(part):
            s = part
            break

    # Strip space-appended metadata not caught by the separator split
    # (e.g., "D9542 CAS: 28718-90-3" → "D9542", "566349 RRID:AB_123" → "566349").
    s = _TRAILING_META_RE.sub("", s).strip()

    # Strip trailing comma or dot left by malformed identifiers.
    s = s.rstrip(",.").strip()

    # Collapse thousands-separator commas in all-digit identifiers.
    if re.match(r"^\d{1,3}(?:,\d{3})+$", s):
        s = s.replace(",", "")

    # Strip pack-size suffixes.
    s = _SIZE_SUFFIX_RE.sub("", s).rstrip("-").strip()

    # Collapse letter-prefix + separator + digit-body (D-9779, D 9779 → D9779).
    m = _ALPHA_NUM_CAT_RE.match(s)
    if m:
        s = m.group(1) + m.group(2)

    # If the whole identifier is a metadata annotation with no catalog number
    # (e.g., "CAS#9002-93-1", "RRID:AB_123", "LOT#20200115"), blank it so it
    # doesn't create a spurious product key.  raw_identifier_examples preserves
    # the original value for auditing.
    if _METADATA_PREFIX_RE.match(s):
        return ""

    return s


def normalize_source(s: str) -> str:
    """
    Return the canonical vendor name for s, or a whitespace-normalized
    title-cased fallback if no vendor pattern matches.
    """
    s = s.strip()
    if not s:
        return ""
    for pattern, canonical in _VENDOR_CANON:
        if pattern.search(s):
            return canonical
    # Fallback: collapse internal whitespace; preserve original casing.
    return re.sub(r"\s+", " ", s)


def ols_slope(years: list[int], values: list[float]) -> float | None:
    """OLS slope of values ~ years. None if fewer than 3 data points."""
    if len(years) < 3:
        return None
    x = np.array(years, dtype=float)
    y = np.array(values, dtype=float)
    x -= x.mean()
    slope = np.dot(x, y) / np.dot(x, x)
    return float(slope)


def most_common(series: pd.Series) -> str:
    """Most frequent non-empty string value in a Series."""
    counts = series[series.str.strip() != ""].value_counts()
    return counts.index[0] if len(counts) else ""


def freq_stats(grp: pd.DataFrame, papers_per_year: dict[int, int]) -> dict:
    """
    Per-year counts, normalized frequencies, mean, and OLS slope for a group.
    Caller pops/renames 'n_papers' as appropriate for name vs product level.
    """
    annual_counts: dict[int, int] = (
        grp.groupby("year_int")["pmcid"].nunique().to_dict()
    )
    annual_norm: dict[int, float] = {
        yr: cnt / papers_per_year[yr]
        for yr, cnt in annual_counts.items()
    }
    years_sorted = sorted(annual_counts)
    norm_values  = [annual_norm[y] for y in years_sorted]
    slope        = ols_slope(years_sorted, norm_values)

    return {
        "n_papers":       grp["pmcid"].nunique(),
        "years_active":   ",".join(str(y) for y in years_sorted),
        "annual_counts":  json.dumps(annual_counts),
        "annual_norm":    json.dumps({y: round(v, 6) for y, v in annual_norm.items()}),
        "mean_norm_freq": round(float(np.mean(norm_values)), 6),
        "trend_slope":    round(slope, 8) if slope is not None else "",
    }


def build_name_records(
    df: pd.DataFrame,
    papers_per_year: dict[int, int],
) -> tuple[list[dict], dict[str, int]]:
    """
    Group by norm_name. Returns (name-level records, {norm_name: n_papers}).
    """
    records: list[dict] = []
    name_paper_counts: dict[str, int] = {}

    for norm_name, grp in df.groupby("norm_name", sort=False):
        stats = freq_stats(grp, papers_per_year)
        n_papers = stats.pop("n_papers")
        name_paper_counts[norm_name] = n_papers

        records.append({
            "normalized_name": norm_name,
            "n_papers_total":  n_papers,
            **stats,
            "top_source":      most_common(grp["source"]),
            "top_identifier":  most_common(grp["identifier"]),
            "variant_count":   grp.groupby(["norm_src", "norm_id"]).ngroups,
        })

    return records, name_paper_counts


def build_product_records(
    df: pd.DataFrame,
    papers_per_year: dict[int, int],
    name_paper_counts: dict[str, int],
) -> list[dict]:
    """
    Group by (norm_name, norm_src, norm_id) — i.e., canonical-name ×
    canonical-vendor × stripped-catalog#. Each record carries raw source
    and identifier examples for auditing, and the parent concept's paper
    count for downstream sorting and selection.
    """
    records: list[dict] = []

    for (norm_name, norm_src, norm_id), grp in df.groupby(
            ["norm_name", "norm_src", "norm_id"], sort=False):
        stats = freq_stats(grp, papers_per_year)
        n_papers = stats.pop("n_papers")

        raw_names  = grp["reagent_name"].dropna().unique()
        raw_srcs   = grp["source"].dropna().unique()
        raw_ids    = grp["identifier"].dropna().unique()

        records.append({
            "normalized_name":         norm_name,
            "normalized_source":       norm_src,
            "normalized_identifier":   norm_id,
            "raw_name_examples":       " | ".join(list(raw_names)[:3]),
            "raw_source_examples":     " | ".join(list(raw_srcs)[:3]),
            "raw_identifier_examples": " | ".join(list(raw_ids)[:3]),
            "n_papers":                n_papers,
            "name_n_papers":           name_paper_counts.get(norm_name, 0),
            **stats,
            "cas":        "",
            "cas_method": "",
        })

    return records


def build_variants_json(product_records: list[dict]) -> dict[str, list[dict]]:
    """
    Nested dict keyed by normalized_name. Each entry lists product variants
    sorted by n_papers descending.
    """
    variants: dict[str, list[dict]] = defaultdict(list)
    for rec in product_records:
        variants[rec["normalized_name"]].append({
            "source":      rec["normalized_source"],
            "identifier":  rec["normalized_identifier"],
            "n_papers":    rec["n_papers"],
            "years_active": rec["years_active"],
        })
    return {
        k: sorted(v, key=lambda x: x["n_papers"], reverse=True)
        for k, v in variants.items()
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--in-csv", default=str(IN_CSV))
    p.add_argument("--out-names",    default=str(OUT_NAMES))
    p.add_argument("--out-products", default=str(OUT_PRODUCTS))
    p.add_argument("--out-variants", default=str(OUT_VARIANTS))
    p.add_argument("--year-from", type=int, default=None)
    p.add_argument("--year-to",   type=int, default=None)
    p.add_argument("--sort", default="mean_norm_freq",
                   choices=["mean_norm_freq", "n_papers_total", "trend_slope"])
    p.add_argument("--top", type=int, default=None,
                   help="Limit name-level output to top N rows.")
    p.add_argument("--min-papers", type=int, default=5,
                   help="Exclude concepts cited in fewer than N papers (default 5). "
                        "Products inherit this via their parent concept's count.")
    args = p.parse_args(argv)

    # ── Load full KRT rows (all categories) ───────────────────────────────────
    # Parse year before splitting so the denominator (papers_per_year) counts
    # all KRT-bearing papers, not just those that emitted chemical rows.
    df_all = pd.read_csv(args.in_csv, dtype=str).fillna("")
    df_all["year_int"] = pd.to_numeric(df_all["year"], errors="coerce")
    df_all = df_all.dropna(subset=["year_int"])
    df_all["year_int"] = df_all["year_int"].astype(int)

    if args.year_from:
        df_all = df_all[df_all["year_int"] >= args.year_from]
    if args.year_to:
        df_all = df_all[df_all["year_int"] <= args.year_to]

    if df_all.empty:
        print("ERROR: no rows after year filters.", file=sys.stderr)
        return 1

    # Denominator: distinct papers with at least one KRT row (any category).
    papers_per_year: dict[int, int] = (
        df_all.groupby("year_int")["pmcid"].nunique().to_dict()
    )

    # ── Filter to chemicals ───────────────────────────────────────────────────
    df = df_all[
        df_all["category"].str.contains("Chemical", case=False, na=False)
    ].copy()

    if df.empty:
        print("ERROR: no chemical rows after filters.", file=sys.stderr)
        return 1

    # ── Normalize ─────────────────────────────────────────────────────────────
    df["norm_name"] = df["reagent_name"].apply(normalize_name)
    df["norm_src"]  = df["source"].apply(normalize_source)      # canonical vendor
    df["norm_id"]   = df["identifier"].apply(normalize_identifier)  # Cat#-stripped

    # ── Build name-level records ──────────────────────────────────────────────
    name_records, name_paper_counts = build_name_records(df, papers_per_year)
    names_df = pd.DataFrame(name_records)

    before = len(names_df)
    names_df = names_df[names_df["n_papers_total"] >= args.min_papers]
    print(f"Name-level:  dropped {before - len(names_df)} concepts "
          f"below --min-papers {args.min_papers}  ({len(names_df)} kept)")

    if args.sort == "trend_slope":
        names_df["_slope_num"] = pd.to_numeric(
            names_df["trend_slope"], errors="coerce")
        names_df = names_df.sort_values(
            "_slope_num", ascending=False, na_position="last")
        names_df = names_df.drop(columns=["_slope_num"])
    else:
        names_df = names_df.sort_values(args.sort, ascending=False)

    if args.top:
        names_df = names_df.head(args.top)

    # ── Build product-level records ───────────────────────────────────────────
    product_records = build_product_records(df, papers_per_year, name_paper_counts)
    products_df = pd.DataFrame(product_records)

    qualifying_names = set(names_df["normalized_name"])
    before_prod = len(products_df)
    products_df = products_df[
        products_df["normalized_name"].isin(qualifying_names)
    ].copy()
    print(f"Product-level: dropped {before_prod - len(products_df)} products "
          f"whose concept is below threshold  ({len(products_df)} kept)")

    # Sort: same concept order as the name-level output, then product n_papers desc.
    name_rank = (
        names_df.reset_index(drop=True)["normalized_name"]
        .reset_index()
        .rename(columns={"index": "rank"})
    )
    products_df = (
        products_df
        .merge(name_rank, on="normalized_name", how="left")
        .sort_values(["rank", "n_papers"], ascending=[True, False])
        .drop(columns=["rank"])
    )

    # ── Build variants JSON ───────────────────────────────────────────────────
    variants_json = build_variants_json(products_df.to_dict(orient="records"))

    # ── Write ─────────────────────────────────────────────────────────────────
    for path in [args.out_names, args.out_products, args.out_variants]:
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    names_df.to_csv(args.out_names, index=False)
    products_df.to_csv(args.out_products, index=False)
    Path(args.out_variants).write_text(
        json.dumps(variants_json, indent=2, sort_keys=True) + "\n"
    )

    print(f"\nInput rows (chemicals):           {len(df)}")
    print(f"Unique concepts (names):           {len(names_df)}")
    print(f"Unique products (name×src×id):     {len(products_df)}")
    print(f"Wrote {args.out_names}")
    print(f"Wrote {args.out_products}")
    print(f"Wrote {args.out_variants}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
