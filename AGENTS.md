# AGENTS.md

This file provides guidance to AI coding agents when working with code in this repository.

# Reagent Handling Intelligence

Turns SDS sheets + tacit bench knowledge into robot-actionable handling profiles, keyed by CAS#.

---

## Current state (2026-05-13)

454 reagents ingested at schema v2.0. All have handling profiles in `data/handling/`, matching local SDS PDFs in `data/sds-pdfs/`, and generated public copies under `docs/`. Pipeline plumbing is operational: `data/index.json` and `docs/data/index.json` now provide the agent-facing reagent manifest, while `docs/data/manifest.json` serves the frontend.

Current quality caveats: enrichment is still shallow for many tacit flags. The latest audit reports 10,682 placeholder-only flag fields. All 454 records have SDS parse markers; 264 parsed records have actionable SDS-derived flags and 190 parsed cleanly but produced no actionable SDS flag. Rule coverage is useful but incomplete: 108 of 454 handling profiles fire no rules, so absence of a rule should be read as "not assigned yet," not proof that no special handling is required.

**STAR methods corpus funnel** (`research/star_methods/`):

| Stage | Count |
|---|---|
| PMC OA papers in corpus (14 Cell Press titles, 2017–2026) | 8,103 |
| Raw KRT rows extracted (Chemicals, Peptides, and Recombinant Proteins) | 91,655 |
| Deduplicated reagent tuples (name × source × catalog#) | 2,450 |
| Sigma-Aldrich tuples with CAS resolved via PubChem SID lookup | 1,441 |
| Reagents ingested end-to-end with handling profiles | **454** |

The gap between 1,441 CAS-resolved tuples and 454 ingested reflects filtering to unique CAS numbers, SDS download and identity-resolution failures, and 297 candidates with no Sigma catalog number to download from. `data/download_failures.json` currently has 25 logged entries, 14 of them unresolved.

---

## Thesis

CAS# → structured JSON profile a liquid handler can consume directly. The differentiator vs. "another SDS database" is a transparent rules layer — properties + reagent class → handling, every rule cited.

---

## Scope

In scope: **Sigma-Aldrich reagents with a resolvable CAS number** — small molecules, solvents, dyes, salts, fixatives, solid powders, detergents. Candidates sourced from frequency-ranked Sigma citations in Cell Press STAR methods sections.

Out of scope: enzymes and biologics from specialty vendors (NEB, IDT, Cell Signaling, Tocris) — excluded due to lack of PubChem SID crosswalk coverage. Also out of scope: cells, buffer prep workflows, freeform protocol text.

---

## Stack

- Frontend: static HTML/CSS/JS in `docs/`. No React unless interactivity demands it.
- Data: JSON in `data/reagents/` (canonical), `data/handling/` (computed profiles).
- Ingestion: Python 3 + `pypdf`, `pdfplumber`, `pandas`. Versions pinned in `requirements.txt`.
- Rules engine: YAML (`data/rules.yaml`) + small Python evaluator (`ingest/apply_rules.py`). Not ML.
- Hosting: GitHub Pages via `.github/workflows/deploy.yml` (push to main → deploy).

---

## Data sources

Three channels feed each reagent profile:

1. **SDS-derived** — Sigma-Aldrich is the single source of truth. `parse_sds.py` extracts `sds_phrase` (literal phrases from §7/§10), `storage_class` (Sigma ADR class from §7), and `ghs_hcode` (hazard codes from §2). All high-confidence. `data/sds-pdfs/` is gitignored — never commit or redistribute PDFs.

2. **PubChem + ChEBI** — PubChem PUG REST API for numeric properties (vapor pressure, boiling/melting point, LogP, flash point) and GHS codes. ChEBI crosswalk for class memberships (fluorophores, reducing agents, intercalators) that SDSs don't encode. Both high-confidence.

3. **Fallback** — When neither SDS nor ChEBI corroborate a flag: `value: null` with source `claude_inference: not_yet_assessed`. Never invent a true/false. The 10 legacy MVP records carry `claude_inference: legacy_handauthored_mvp` at medium confidence — no new hand-authoring on those records.

**Duplicate SDS resolution:** Two PDFs for the same CAS → keep the later Revision Date per the PDF itself (Section 1 footer). Verify with `pdfplumber`; never rely on filename or mtime.

---

## Reagent workflow

1. `parse_sds.py` → SDS scaffold (physical/GHS data + sds_phrase/storage_class/ghs_hcode sources)
2. `enrich_sds_sources.py` + `enrich_chebi.py` → add pubchem/chebi sources
3. `apply_overrides.py` → apply `data/phase2-overrides.yaml` for manual corrections
4. `validate.py` → confirm schema v2.0, reconcile confidence from sources
5. `build_handling_profiles.py` → regenerate `data/handling/<CAS>.json`

`category`, `bench_knowledge`, and `striking_fact` are populated only from extracted evidence or auto-generated from fired rules. Never write narrative prose for these fields in bulk records.

---

## Commands

```bash
# Environment setup (run once)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Full pipeline — parse → enrich → overrides → validate
python tools/ingest_batch.py                       # dry-run: list pending PDFs
python tools/ingest_batch.py --run --limit 3       # test on 3 first
python tools/ingest_batch.py --run                 # run on all new PDFs
python tools/ingest_batch.py --run --fresh         # wipe and re-ingest everything

# Individual pipeline steps (debugging / reruns)
python ingest/parse_sds.py data/sds-pdfs/<CAS#>.pdf
python ingest/enrich_sds_sources.py data/reagents/<CAS#>.json
python ingest/enrich_sds_sources.py --all
python ingest/enrich_chebi.py data/reagents/<CAS#>.json
python ingest/enrich_chebi.py --all
python tools/apply_overrides.py --write
python ingest/apply_rules.py data/reagents/<CAS#>.json   # stdout only

# Batch handling profiles
python tools/build_handling_profiles.py
python tools/build_handling_profiles.py --dry-run

# SDS download
python tools/download_sds.py                       # dry-run
python tools/download_sds.py --fetch --limit 5     # test on 5 first
python tools/download_sds.py --fetch

# Validate
python ingest/validate.py
python ingest/validate.py --cas 64-17-5

# Build + serve static site
python3 scripts/build.py
python3 -m http.server 8080 --directory docs/
```

---

## Repo layout

```
data/
  reagents/              # canonical JSON per reagent, schema v2.0 (committed)
  handling/              # computed handling profiles, one JSON per reagent
  sds-pdfs/              # source PDFs named by CAS# (gitignored)
  rules.yaml             # rules engine
  chebi-lookup.yaml      # curated CAS → ChEBI role mappings
  phase2-overrides.yaml  # manual category + flag overrides
  tacit-knowledge.md     # bench knowledge taxonomy (rules.yaml source material)
  sds-cas-overrides.json # manual SDS filename → CAS corrections
  download_failures.json # SDS download failure log
ingest/
  parse_sds.py           # SDS PDF → flat JSON scaffold
  enrich_sds_sources.py  # add sds_phrase/ghs_hcode/storage_class sources
  enrich_chebi.py        # add chebi/pubchem sources
  apply_rules.py         # reagent + rules.yaml → handling profile
  schema.json            # JSON Schema v2.0
  validate.py
tools/
  ingest_batch.py        # full pipeline orchestrator
  migrate_to_sourced_flags.py # convert legacy booleans to sourced flags
  audit_enrichment_coverage.py # verify enrichment/publication coverage
  apply_overrides.py     # apply phase2-overrides.yaml
  build_handling_profiles.py
  download_sds.py
scripts/build.py         # data/ → docs/ static site build
docs/
  index.html, reagent.html, about.html
  assets/styles.css, assets/diagrams/   # 3 SVG pipetting diagrams
  data/
    manifest.json        # reagent index for the frontend (generated by build.py)
    reagents/            # copy of data/reagents/ served statically
    profiles/            # copy of data/handling/ served statically
  handling/              # public GET /handling/{cas} endpoint
research/
  star_methods/parsed/
    krt_reagents_ranked.csv       # frequency-ranked reagent input feed
    krt_reagent_products_cas.csv  # reagent name → CAS crosswalk
    pubchem_cache.json            # PubChem API response cache
```

---

## Data contracts

- **Canonical format:** `data/reagents/<CAS>.json`, schema v2.0. CAS# is the primary key. All current records have a CAS; the corpus is Sigma-Aldrich small molecules.
- **Handling profiles:** `data/handling/<CAS>.json` — static files robots consume directly. Each lists rules that fired with `cite` strings. The static site is a view over this data, not the source of truth.
- **Agent manifest:** `data/index.json` lists every reagent `[{cas, name, path, schema_version}]`; `docs/data/index.json` is the public copy generated by `scripts/build.py`. Stable URL: `/data/reagents/<CAS>.json`.
- **Sourced flags:** Every tacit boolean: `{value, confidence, sources[]}`. Source `type` enum: `sds_phrase`, `storage_class`, `ghs_hcode`, `pubchem`, `chebi`, `rule_derived`, `tacit_knowledge`, `claude_inference`. Confidence computed by `validate.py`. Source disagreements are kept, not silently resolved.
- **Format conventions:** YAML for human-edited rules, Markdown for narrative docs, JSON for everything an agent consumes. JSON keys are descriptive (`vapor_pressure_kPa_20C`, not `vp20`); units live in field names.

```json
"is_hygroscopic": {
  "value": true,
  "confidence": "high",
  "sources": [
    { "type": "sds_phrase",    "ref": "section_7:'hygroscopic'", "agrees": true },
    { "type": "storage_class", "ref": "8B-corrosive",            "agrees": true },
    { "type": "chebi",         "ref": "CHEBI:32145",             "agrees": true }
  ]
}
```

---

## Rule format

```yaml
- id: pre_wet_tip_volatile
  when: { vapor_pressure_kPa_20C: { gt: 5 } }
  then: { pipetting.pre_wet_cycles: 3 }
  because: >
    High vapor pressure saturates tip headspace; without pre-wet, dispensed
    volume drifts and droplets form on the tip exterior.
  cite: "Eppendorf application note: pipetting volatile liquids, 2018."
```

Source material: `data/tacit-knowledge.md`. Edit that file when adding categories; lift from it into `rules.yaml` when codifying.

---

## Non-negotiables

1. Every rule cites a property in the reagent's data OR a published source.
2. Rules are declarative YAML. No ML.
3. Validate ingestion on 2–3 PDFs before running the full set.
4. When rules conflict, surface it — don't silently pick. Same for sourced flags: keep disagreements in the record.
5. Pin versions in `requirements.txt`.
6. SDS PDFs stay local — never committed, never redistributed.
7. No hallucinated tacit knowledge. Uncorroborated flags stay `null`. `category`, `bench_knowledge`, and `striking_fact` come from extracted evidence or fired rules only — never invented prose.
