"""
rank_reagents.py
================

Phase 3 of the STAR Methods pipeline: deduplicate and frequency-rank
reagents from the parsed KRT rows CSV.

Key design choice: per-year normalization
-----------------------------------------
Raw citation counts are biased by corpus size — a journal that published
twice as many OA papers in 2023 as in 2020 will produce twice as many
reagent mentions even if nothing changed at the bench. The clean metric is:

    normalized_freq(reagent, year) =
        papers_citing_reagent_in_year / papers_with_krt_in_year

This is robust to corpus growth and lets us detect *trending* reagents —
ones whose normalized frequency is rising over time — as opposed to
merely common ones. A linear trend slope (OLS across years) captures this.

Deduplication strategy
-----------------------
KRT reagent entries are notoriously noisy: "Fetal Bovine Serum",
"fetal bovine serum", "FBS", "FBS (Gibco)", and "Gibco FBS" all refer
to the same thing. Full resolution requires PubChem/catalog# crosswalk
(Phase 4). Here we do light normalization only:

  1. Lowercase + strip whitespace.
  2. Collapse internal whitespace.
  3. Strip trailing parenthetical qualifications like "(Sigma)" that
     encode vendor info already in the source column.
  4. Group by normalized_name only. Source and identifier are recorded
     as the most-common values within the group and are purely metadata
     — they do NOT split reagents by vendor. Rationale: for frequency
     ranking we want "how often do researchers use DMSO?", not "how
     often do they use DMSO from Sigma catalog D2650?". Phase 4 will
     crosswalk to CAS and can split by grade/purity if needed.

  --min-papers (default 5): reagents cited in fewer than N distinct
  papers are excluded. Prevents single-paper oddities from dominating
  the normalized-frequency ranking in low-volume journal-years.

Output columns
--------------
  normalized_name     — deduplicated reagent name (lowercased)
  source              — most common source string in the group
  identifier          — most common identifier string in the group
  n_papers_total      — distinct PMCIDs citing this reagent across all years
  years_active        — comma-separated sorted list of years with ≥1 citation
  annual_counts       — JSON object: {year: n_papers} for years with data
  annual_norm         — JSON object: {year: normalized_freq} (n_papers /
                        papers_with_krt_in_year), rounded to 6 dp
  mean_norm_freq      — mean of annual_norm values (overall prevalence)
  trend_slope         — OLS slope of normalized_freq vs year (positive =
                        growing use); None if fewer than 3 data years

Usage
-----
  # Default: rank by mean normalized frequency, reagents cited in ≥5 papers.
  python rank_reagents.py

  # Top 100 by trend slope (fastest-growing reagents).
  python rank_reagents.py --sort trend_slope --top 100

  # Restrict to papers from 2020 onward.
  python rank_reagents.py --year-from 2020

  # Lower min-papers threshold.
  python rank_reagents.py --min-papers 2

  # Write full table (no --top limit).
  python rank_reagents.py --out parsed/krt_reagents_ranked.csv

Dependencies
------------
- pandas
- numpy (for OLS trend)

Both available in the project venv.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
IN_CSV  = ROOT / "parsed" / "krt_rows.csv"
OUT_CSV = ROOT / "parsed" / "krt_reagents_ranked.csv"

# Parenthetical vendor/grade suffixes to strip during normalization,
# e.g. "fetal bovine serum (gibco)" -> "fetal bovine serum".
# Only strips a trailing paren whose content looks like a vendor name
# (no digits, short). Avoids stripping informative parens like
# "doxorubicin (2 mg/mL stock)".
_TRAILING_PAREN_RE = re.compile(r"\s*\([^)]{1,30}\)\s*$")


def normalize_name(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    s = _TRAILING_PAREN_RE.sub("", s).strip()
    return s


def normalize_source(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip()) if s else ""


def normalize_identifier(s: str) -> str:
    """Uppercase catalog numbers; strip surrounding whitespace."""
    s = s.strip().upper()
    return re.sub(r"\s+", " ", s)


def ols_slope(years: list[int], values: list[float]) -> float | None:
    """
    OLS slope of values ~ years. Returns None if fewer than 3 points
    (slope would be meaningless or perfectly determined by 2 points).
    """
    if len(years) < 3:
        return None
    x = np.array(years, dtype=float)
    y = np.array(values, dtype=float)
    x -= x.mean()  # center to improve numerical stability
    slope = np.dot(x, y) / np.dot(x, x)
    return float(slope)


def most_common(series: pd.Series) -> str:
    """Return the most frequent non-empty value in a Series."""
    counts = series[series.str.strip() != ""].value_counts()
    return counts.index[0] if len(counts) else ""


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--in-csv", default=str(IN_CSV),
                   help="Input CSV (default: parsed/krt_rows.csv)")
    p.add_argument("--out", default=str(OUT_CSV),
                   help="Output path (default: parsed/krt_reagents_ranked.csv)")
    p.add_argument("--year-from", type=int, default=None,
                   help="Exclude papers before this year.")
    p.add_argument("--year-to", type=int, default=None,
                   help="Exclude papers after this year.")
    p.add_argument("--sort", default="mean_norm_freq",
                   choices=["mean_norm_freq", "n_papers_total", "trend_slope"],
                   help="Column to sort output by (descending).")
    p.add_argument("--top", type=int, default=None,
                   help="Only emit the top N rows.")
    p.add_argument("--min-papers", type=int, default=5,
                   help="Exclude reagents cited in fewer than N distinct papers "
                        "(default 5). Prevents low-volume journal-year artefacts "
                        "from inflating normalized frequency.")
    args = p.parse_args(argv)

    # ── Load ─────────────────────────────────────────────────────────────
    df = pd.read_csv(args.in_csv, dtype=str).fillna("")

    # Keep only chemicals rows (in case the CSV wasn't pre-filtered).
    df = df[df["category"].str.contains("Chemical", case=False, na=False)].copy()

    # Parse year as int; drop rows with unparseable years.
    df["year_int"] = pd.to_numeric(df["year"], errors="coerce")
    df = df.dropna(subset=["year_int"])
    df["year_int"] = df["year_int"].astype(int)

    if args.year_from:
        df = df[df["year_int"] >= args.year_from]
    if args.year_to:
        df = df[df["year_int"] <= args.year_to]

    if df.empty:
        print("ERROR: no rows after filters.", file=sys.stderr)
        return 1

    # ── Normalize ────────────────────────────────────────────────────────
    df["norm_name"]  = df["reagent_name"].apply(normalize_name)
    df["norm_src"]   = df["source"].apply(normalize_source)
    df["norm_id"]    = df["identifier"].apply(normalize_identifier)

    # ── Per-year denominator: distinct papers with ≥1 KRT row ───────────
    # We count distinct PMCIDs per year across the *full* chemicals subset
    # (before deduplication). This is conservative: a paper with a KRT but
    # zero chemicals rows won't appear here, so the denominator slightly
    # understates total KRT coverage. Acceptable for relative comparisons.
    papers_per_year: dict[int, int] = (
        df.groupby("year_int")["pmcid"].nunique().to_dict()
    )

    # ── Group by normalized name only ────────────────────────────────────
    # Source and identifier are metadata within the group, not split keys.
    # See module docstring for the rationale.
    groups = df.groupby("norm_name", sort=False)

    records = []
    for norm_name, grp in groups:
        # Per-year distinct paper counts.
        annual_counts: dict[int, int] = (
            grp.groupby("year_int")["pmcid"].nunique().to_dict()
        )
        # Normalize by total KRT papers that year.
        annual_norm: dict[int, float] = {
            yr: cnt / papers_per_year[yr]
            for yr, cnt in annual_counts.items()
        }

        n_papers_total = grp["pmcid"].nunique()

        years_sorted = sorted(annual_counts)
        norm_values  = [annual_norm[y] for y in years_sorted]
        mean_norm    = float(np.mean(norm_values))
        slope        = ols_slope(years_sorted, norm_values)

        records.append({
            "normalized_name": norm_name,
            "source":          most_common(grp["source"]),
            "identifier":      most_common(grp["identifier"]),
            "n_papers_total":  n_papers_total,
            "years_active":    ",".join(str(y) for y in years_sorted),
            "annual_counts":   json.dumps(annual_counts),
            "annual_norm":     json.dumps({y: round(v, 6) for y, v in annual_norm.items()}),
            "mean_norm_freq":  round(mean_norm, 6),
            "trend_slope":     round(slope, 8) if slope is not None else "",
        })

    out_df = pd.DataFrame(records)

    # ── Min-papers filter ─────────────────────────────────────────────────
    before = len(out_df)
    out_df = out_df[out_df["n_papers_total"] >= args.min_papers]
    print(f"Dropped {before - len(out_df)} reagents below --min-papers {args.min_papers}")

    # ── Sort ──────────────────────────────────────────────────────────────
    sort_col = args.sort
    if sort_col == "trend_slope":
        # Rows with no slope (< 3 years) sort last.
        out_df["_slope_num"] = pd.to_numeric(out_df["trend_slope"], errors="coerce")
        out_df = out_df.sort_values("_slope_num", ascending=False, na_position="last")
        out_df = out_df.drop(columns=["_slope_num"])
    else:
        out_df = out_df.sort_values(sort_col, ascending=False)

    if args.top:
        out_df = out_df.head(args.top)

    # ── Write ─────────────────────────────────────────────────────────────
    Path(args.out).parent.mkdir(exist_ok=True)
    out_df.to_csv(args.out, index=False)

    print(f"Input rows (chemicals):   {len(df)}")
    print(f"Unique (name, src, id):   {len(records)}")
    print(f"Output rows (after sort): {len(out_df)}")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
