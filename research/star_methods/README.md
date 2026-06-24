# STAR Methods corpus harvest

Build an empirically-grounded reagent candidate list by mining Key
Resources Tables (KRTs) from Cell Press papers in PMC Open Access since
2017. Replaces the LLM-memory candidate lists in `research/sigma_*.csv`
and `research/neb_enzymes.csv` with a frequency-ranked list keyed to
real bench usage.

See `CLAUDE.md` (project root) for thesis and Phase 2 context.

## Scope

- **Source**: PMC Open Access subset only (free, reproducible). Realistic
  coverage of Cell Press post-2017: ~30-50% of papers, ~15-30K total.
  Elsevier ScienceDirect TDM API would close the gap; deferred until
  after Phase 4 if coverage looks thin.
- **Journals**: 14 Cell Press titles listed in `journals.json`.
- **Category filter**: `Chemicals, Peptides, and Recombinant Proteins`
  only. Antibodies, oligos, kits, cell lines are deferred — different
  rules-engine inputs, different downstream uses.
- **Time range**: 2017-01-01 onward (when STAR Methods became mandatory).

## Pipeline

```
  Europe PMC search        ──► manifest.csv  (pmcid, journal, year, title)
       │
       ▼
  fetch fullTextXML        ──► xml_cache/<PMCID>.xml
       │
       ▼
  parse_krt.py             ──► parsed/krt_rows.csv
       │
       ▼
  Phase 3: dedup +         ──► parsed/krt_rows_normalized.csv
  identifier normalization      (dedup on (name, source, identifier))
       │
       ▼
  Phase 4: freq rank +     ──► parsed/krt_reagents_ranked.csv
  PubChem crosswalk             (per-year normalized; trend_slope)
```

Phases 3 and 4 are not yet implemented; Phase 2 (parser) is the validation
gate. The design below pins the metric down up front so it doesn't get
bolted on after the parser ships.

## Phase 3/4 design

**Phase 3 — deduplication and identifier normalization.** The unit of
identity is the `(reagent_name, source, identifier)` tuple, not
`reagent_name` alone. The same chemical name can resolve to different
vendors and catalog#s with materially different products (e.g., DTT from
Sigma D9779 vs Thermo R0861 — same molecule, but different formulation /
grade / downstream handling rules), and we want both visible in the
ranked output rather than collapsed.

Light normalization is applied before the dedup to absorb formatting
drift, not to merge across vendors:

- Lowercase and strip surrounding whitespace.
- Collapse internal whitespace runs to a single space.
- Normalize catalog# variants — hyphens, spaces, underscores, leading
  zeros — so `D-9779`, `D 9779`, and `D9779` collapse to the same
  identifier.
- Preserve the original strings in `reagent_name_raw` and
  `identifier_raw` columns for downstream auditing.

Output: `parsed/krt_rows_normalized.csv` — one row per
`(paper, normalized_reagent_tuple)`. Same shape as `krt_rows.csv` plus
the `_raw` columns.

**Phase 4 — frequency ranking, normalized by papers-with-KRT.** For each
`(reagent_name, source, identifier)`, count how many *distinct papers*
cite it per year. Normalize by the total number of papers-*with-KRT* in
that year — **not** the corpus total. Only KRT-bearing papers can
contribute reagent mentions, so the right base rate is "what fraction of
papers that could have mentioned this reagent did." Year-by-year
normalization also absorbs the fact that the proportion of OA Cell Press
papers carrying a parseable KRT changes over time (mandate phase-in,
journal-level template drift, parser coverage gaps).

Output: `parsed/krt_reagents_ranked.csv`, one row per reagent tuple:

```
reagent_name | source | identifier | years_cited | total_papers | normalized_freq_per_year | trend_slope
```

- `years_cited` — count of distinct years the reagent appeared in any
  paper.
- `total_papers` — distinct paper count across all years.
- `normalized_freq_per_year` — JSON-encoded year→fraction dict.
- `trend_slope` — linear regression slope of the per-year fractions
  against year. `null` if `years_cited < 2`.

The slope is the useful derived column. A reagent used in ~2% of
KRT-bearing papers every year is stable (slope ≈ 0). One going from
0.5% to 3% over five years is trending up (slope > 0). Stable-and-high
and trending-up are both interesting candidates for the rules engine,
and the slope cleanly separates them. Downstream consumers should
require `years_cited >= 3` before treating the slope as meaningful —
reagents only present in the most recent 1–2 years can throw
artificially large slopes.

PubChem crosswalk (CAS lookup keyed by vendor catalog#) is best-effort
and runs alongside Phase 4, not as a hard prerequisite. Sigma is
indexed in PubChem; NEB / Cell Signaling / Tocris are not. Reagents
without a resolved CAS keep `(vendor, catalog#)` as their canonical
identifier — the rules engine doesn't require CAS for the
adsorption / freeze-thaw / dilute-protein rule families that biologics
fire.

## How to run

The pipeline is **manifest-driven**: `pull_corpus.py` writes
`manifest.csv` listing exactly the papers in scope for the current run,
and `parse_krt.py` parses only those papers. The `xml_cache/` directory
is intentionally incremental (so re-running with different filters
doesn't re-download papers you already have), but stale XMLs from
previous runs are silently ignored — the manifest is the source of
truth. See the parser's `--include-cache-orphans` flag if you ever
need to audit leftover cache.

```bash
# Activate the project venv (created on your machine, not the sandbox).
source .venv/bin/activate
pip install -r requirements.txt

# 1. Estimate corpus size — 14 API calls, no downloads. Run this first.
python research/star_methods/scripts/pull_corpus.py --estimate

# 2. Validation pull: 10 recent Cell Reports papers. Inspect the XML.
#    Rewrites manifest.csv to list exactly these 10 papers.
python research/star_methods/scripts/pull_corpus.py \
    --journal "Cell Reports" --year-from 2024 --limit 10 --fetch

# 3. Run the parser in debug mode against the validation set.
#    --limit applies to manifest order, so this inspects the 10 papers
#    you just pulled (not the alphabetical-first 10 in xml_cache/).
python research/star_methods/scripts/parse_krt.py --debug

# 4. (After spot-checking the validation output) full corpus pull.
#    Manifest is rewritten to all OA Cell Press papers since 2017.
python research/star_methods/scripts/pull_corpus.py --year-from 2017 --fetch

# 5. Full parse, filter to chemicals only.
python research/star_methods/scripts/parse_krt.py \
    --category-filter "Chemicals, Peptides, and Recombinant Proteins"

# (Optional) Audit any XMLs in xml_cache/ that aren't in the current manifest.
python research/star_methods/scripts/parse_krt.py \
    --include-cache-orphans --debug
```

## Fetch-time validation

`pull_corpus.py` validates downloaded bytes parse as JATS XML
(root `<article>` or `<pmc-articleset>`) BEFORE caching. Europe PMC has
been observed to return HTTP 200 with HTML error pages during transient
backend issues; without validation those would be cached as if they
were real article XMLs and surface as parse errors much later in the
pipeline. Invalid bodies are retried with the same exponential backoff
as 5xx responses (since they're typically transient), and only marked
`fetch_ok=False` after `MAX_RETRIES` consecutive bad responses.

If you suspect the cache contains bad files written before validation
existed (or files corrupted by an interrupted write), run:

```bash
python research/star_methods/scripts/pull_corpus.py --revalidate-cache
```

This walks `xml_cache/` once, deletes anything that doesn't parse as
JATS, and reports counts. Subsequent fetches will re-download cleanly.
Combine with `--fetch` to revalidate-then-pull in one command.

## Validation gate (per CLAUDE.md)

Before scaling to the full corpus, paste back to Claude:

1. Output of `--estimate` (per-journal hitCounts).
2. The console output of the debug parse run, including the per-paper
   "found N KRT table-wrap(s)" lines, every "category=" line, and the
   final summary block (Manifest entries / Papers parsed / KRT detected
   / Rows extracted / Rows kept).
3. First ~30 lines of `parsed/krt_rows.csv`.

We then spot-check 20 rows against the original PDFs together (Claude
needs the rows; you need to open the PDFs since this sandbox can't reach
journal sites). Only after that gate passes do we run the full corpus
pull, which will take a few hours and ~5-10 GB of cached XML.

## Files in this directory

- `journals.json`            — Cell Press ISSN list (edit if scope changes)
- `scripts/pull_corpus.py`   — Phase 1: search + fetch JATS XML
- `scripts/parse_krt.py`     — Phase 2: JATS XML → tidy reagent CSV
- `xml_cache/`               — downloaded JATS XMLs, gitignored
- `parsed/`                  — parser output CSVs, committable
- `manifest.csv`             — pmcid → journal/year/title (committable)

## Known risks

- **Cell Press OA coverage is partial.** ~30-50% sampling, with no
  obvious bias against any reagent class. Documented as a sample, not a
  census.
- **KRT format drift.** ~10-20% of papers have idiosyncratic layouts the
  parser may misread. The `parser_warnings` column flags suspicious
  rows; spot-check anything with warnings in the top 100 by frequency.
- **Vendor catalog → CAS coverage is uneven.** PubChem indexes Sigma
  and (partially) Thermo. NEB, Cell Signaling, Tocris are not. For
  unmapped catalog#s we keep the (vendor, catalog#) tuple as the
  canonical identifier — works for the rules engine, since adsorption
  / freeze-thaw / dilute-protein rules don't require a CAS.
- **Some KRTs live only in supplementary docx files.** `parse_krt.py`
  in its current form does not chase those; they show up as "no KRT
  found" in debug output. If a meaningful fraction of papers fall into
  this bucket we add a supplements pass.
