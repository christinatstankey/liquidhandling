# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

# Reagent Handling Intelligence

Proof-of-work project for a lab automation company hiring team. Turns SDS sheets
+ tacit bench knowledge into robot-actionable handling profiles, keyed by CAS#.

Defer to `~/.Codex/AGENTS.md` for anything not covered here.

---

## Status (2026-05-01)

**Current phase: Phase 2 — Ingestion (in progress).**

- Phase 1 MVP shipped: 10 reagent JSONs hand-authored, static site live, rules
  engine + schema in place.
- SDS collection: **30/30 SDSs downloaded** from Sigma-Aldrich via Codex in
  Chrome (HCl 37%, CAS 7647-01-0, was the validation reagent before scaling).
  Files in `data/sds-pdfs/<CAS>.pdf` (or `polyclonal-igg.pdf` for the
  no-CAS antibody slot), all verified as real PDFs (148–385 KB, Sigma's
  standard 16-section template).
- Sigma uses brand prefixes inconsistently — `sigma/`, `sigald/`, and `sial/`
  all appear across the 30 SKUs. The download script needs to probe brand
  prefixes when building canonical URLs; don't assume one prefix per product
  line.
- Inventory of record lives in `data/reagent-inventory.md` (Sigma SKUs, SDS
  status, JSON status). Update that file as work progresses.
- Slot 1 label corrected during MVP-10 download: previously "DNA polymerase I
  (*E. coli*)", now "Taq DNA Polymerase" — matches the JSON, which was always
  authored for Taq. (Both share generic CAS 9012-90-2.)
- `ingest/parse_sds.py` **built and validated.** Extracts GHS data, physical
  properties, storage/PPE/incompatibilities from Sigma's 16-section template.
  All 10 MVP JSONs re-parsed from their SDSs; 10/10 pass `validate.py`.
  Several corrections vs. model-memory originals (e.g., DTT signal word
  Warning→Danger, H318 not H319; glycerol viscosity 412→1412 mPas;
  Tween-20 flash point 110→275°C; PFA physical_state powder→solid).
- **Sourced-flags migration approved**, pending implementation. See
  `docs/sourced-flags-migration.md`. Every tacit boolean moves from a flat
  value to `{value, confidence, sources[]}`, where sources come from SDS
  prose, GHS codes, storage class, PubChem, or ChEBI. The 10 MVP records
  are wrapped with a single `claude_inference: legacy_handauthored_mvp`
  source (medium confidence) on migration; all *new* records are populated
  only from extracted evidence — no model-authored tacit knowledge.
- **STAR methods corpus** (`research/star_methods/`) replaces model-memory
  candidate lists with a frequency-ranked reagent list mined from Cell Press
  Key Resources Tables (PMC OA, 2017+). This is the input feed for future
  bulk ingestion beyond the 30 hand-picked reagents.
- **Next:** (1) implement the sourced-flags schema migration on the MVP-10;
  (2) extend `parse_sds.py` with the prose/PubChem/ChEBI channels;
  (3) ingest the 20 Phase 2 expansion SDSs through the new pipeline with
  *no* hand-authoring of tacit fields. See `data/reagent-inventory.md` for
  expansion CAS list.

---

## Thesis

SDS = legal safety facts.
Bench scientist = how to actually use the reagent
(pre-wet for volatiles, thaw enzymes on ice, LoBind for dilute proteins, make DTT
fresh). 
This project is the bridge: CAS# → structured JSON profile a liquid
handler could consume directly.

The differentiator vs. "another SDS database" is a transparent rules layer —
properties + reagent class → handling, every rule cited.

---

## Scope

In scope: reagents — bottles you'd find on a stockroom shelf. Solvents,
enzymes, dyes, salts, fixatives, solid powders, antibodies, oligos. Each gets
a CAS#-keyed handling profile.

Out of scope (for now): cells (competent cells, cell lines, primary cells —
their handling is biologically rather than chemically constrained), buffer
prep workflows (titration, pH adjustment, sterilization), and freeform
protocol text (see Phase 2).

These exclusions are deferral, not abandonment — cells and buffer prep are
candidates for later phases once the reagent core is solid.

---

## Stack

- Frontend: static HTML/CSS/JS. Astro only if pages start to repeat. No React
  unless interactivity demands it.
- Data: JSON in `data/reagents/` for MVP, SQLite past ~100 reagents.
- Ingestion: Python 3 + `pypdf`, `pdfplumber`, `pandas`. `pytesseract` only as
  OCR fallback. Pin versions in `requirements.txt`.
- Rules engine: YAML + small Python evaluator. NOT ML.
- Hosting: GitHub Pages or Vercel.
- git from day one; commit per logical unit.

---

## Data sources

Three channels feed each reagent profile. Don't conflate them — they solve
different problems and have different reliability characteristics. The full
source taxonomy and confidence rules live in `docs/sourced-flags-migration.md`;
this section is the short version.

1. **SDS-derived (legal/safety facts + extracted prose).** Sigma-Aldrich
   (MilliporeSigma) is the single source of truth for SDSs. One vendor
   template = one parser. Fisher Scientific or specialty vendors (NEB,
   Tocris, Cell Signaling) only as fallback when Sigma doesn't carry the
   product. `parse_sds.py` extracts three SDS-grounded source types:
   `sds_phrase` (literal phrase matches in §7/§10), `storage_class` (Sigma
   ADR class line in §7), and `ghs_hcode` (hazard codes in §2). All three
   are high-confidence. No public SDS API exists; download URLs are
   predictable enough to script, but respect ToS — polite rate limits, no
   redistribution.
2. **Numeric properties + ChEBI ontology (rule inputs and class memberships).**
   PubChem PUG REST API, base URL `https://pubchem.ncbi.nlm.nih.gov/rest/pug/`.
   Free, no auth, structured JSON, keyed by CAS or CID. Pulls vapor pressure,
   melting/boiling point, density, LogP, flash point, and GHS hazard codes
   (more reliable than parsing Section 9 free text). PubChem's CID → ChEBI
   crosswalk is also the source of `chebi:` flags — class memberships the
   SDS won't tell you (fluorophores → light-sensitive, reducing agents,
   intercalators). Both are high-confidence.
3. **Honest fallback.** When neither SDS nor ChEBI corroborate a flag, the
   value is set from `tacit-knowledge.md` (`tacit_knowledge` source) or a
   short model reason string (`claude_inference` source). Both are
   low-confidence. For bulk-imported records, the preferred fallback is
   `value: null` with a placeholder source — *not* an invented true/false.
   `tacit-knowledge.md` is now rule source material, not a per-record
   authoring source.

**Coverage gap:** PubChem and ChEBI are weak on enzymes, antibodies, and
oligos. For those, fall back to manufacturer datasheets (NEB product pages,
IDT spec sheets, antibody vendor PDFs) via the `manufacturer_protocol`
source type — medium confidence. Some "properties" for biologics are
categorical (e.g., "store at -20 °C in 50% glycerol") rather than numeric.

**PDFs stay local.** `data/sds-pdfs/` is gitignored — keeping vendor SDSs
local avoids redistribution questions while preserving the ability to
re-parse when the extractor improves. Only the extracted JSON in
`data/reagents/` is committed. The directory itself is tracked via
`data/sds-pdfs/.gitkeep` so the layout is reproducible.

**Who authors tacit knowledge fields.** Two regimes — they look different
because they *are* different:

*MVP-10 (legacy, hand-authored).* The original 10 reagent JSONs were
hand-authored from chemical knowledge during MVP. On migration to the
sourced-flags schema, every tacit boolean in those records is wrapped with a
single `claude_inference: legacy_handauthored_mvp` source at medium
confidence. No new hand-authoring on these records — follow-up passes only
add corroborating sources from the extraction pipeline.

*All bulk-imported records (Phase 2 expansion 20 + STAR-methods-driven
imports).* Tacit booleans are populated **only** from extracted evidence
(SDS prose, GHS, storage class, PubChem, ChEBI). If no channel corroborates
a flag, `value: null` with a placeholder `claude_inference: not_yet_assessed`
source — never an invented true/false. The workflow for each new reagent is:

1. `parse_sds.py` generates the SDS scaffold (physical/GHS data + extracted
   `sds_phrase`, `storage_class`, `ghs_hcode` sources).
2. PubChem/ChEBI lookup adds `pubchem` and `chebi` sources for each tacit
   flag where the ontology classifies the compound.
3. `category` is assigned from the schema enum *only* if a sourced flag
   determines it unambiguously (e.g., `is_fluorophore.value=true` → category
   `fluorophore`). Otherwise `category: null`.
4. `bench_knowledge` is auto-generated: one bullet per rule that fires in
   `apply_rules.py`, citing the `rule_id`. No narrative bullets written
   from chemical knowledge for bulk records.
5. `striking_fact` is either omitted or derived deterministically from the
   highest-priority fired rule. Not model-authored prose.
6. `validate.py` confirms the record passes schema and reconciles
   `confidence` from `sources`.

**Duplicate SDS resolution.** If two PDFs exist for the same CAS (e.g.,
`<CAS>.pdf` and `<CAS> (1).pdf`), keep the one with the later Revision Date
as reported in the PDF itself (Section 1 footer on Sigma SDSs). If the
revision dates are identical, keep the higher version number. Delete the
older file — do not rename or archive it. Verify with `pdfplumber` before
deleting; never rely on filename, file size, or mtime alone.

---

## Commands

```bash
# Environment setup (run once)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Parse a single SDS PDF → JSON (validate on 2–3 before batch)
python ingest/parse_sds.py data/sds-pdfs/<CAS#>.pdf

# Apply rules.yaml to a reagent JSON → handling profile
python ingest/apply_rules.py data/reagents/<CAS#>.json

# Validate all reagent JSON against schema
python ingest/validate.py

# Build static site from data/ → site/
python scripts/build.py

# Serve locally to preview
python -m http.server 8080 --directory site/
```

---

## Repo layout

```
reagent-handler/
  AGENTS.md, README.md, requirements.txt
  data/
    reagents/             # one JSON per reagent (canonical, committed)
    sds-pdfs/             # source PDFs, named by CAS# (gitignored, local only)
    rules.yaml            # the rules engine
    reagent-inventory.md  # SKU + SDS + JSON status table for the 30-reagent set
    tacit-knowledge.md    # bench knowledge taxonomy (rules.yaml source material)
    citations.bib
  ingest/
    parse_sds.py          # PDF → JSON
    apply_rules.py        # reagent + rules.yaml → handling profile
    schema.json           # JSON Schema for reagent records
    validate.py
  site/
    index.html, reagent.html, about.html
    assets/styles.css, assets/diagrams/
  scripts/build.py        # data/ → site/
```

---

## Phases (ship one before starting next)

Current phase is tracked at the top of this file under **Status**.

1. **MVP — done.** 10 reagent JSONs hand-authored across the diversity matrix,
   static frontend up, rules engine + schema scaffolded.
2. **Ingestion — in progress.** Two sub-phases:
   - **2a — Sourced-flags schema migration.** Implement the
     `{value, confidence, sources[]}` shape per `docs/sourced-flags-migration.md`.
     Update `ingest/schema.json` to v2.0, run `tools/migrate_to_sourced_flags.py`
     on the 10 MVP records, update `validate.py` and `apply_rules.py`. All 10
     records still pass validation; rules engine output unchanged. Commit.
   - **2b — Bulk ingestion through the new pipeline.** Extend `parse_sds.py`
     with the prose detector (yields `sds_phrase`, `storage_class`,
     `ghs_hcode` sources). Add PubChem PUG REST + ChEBI lookup. Validate
     end-to-end on 2–3 reagents from the MVP set. Then ingest the 20 Phase 2
     expansion SDSs through the full pipeline with **no hand-authoring** of
     tacit fields — uncorroborated flags stay `null`.
   - *SDSs collected:* 20/20 Phase 2 expansion. MVP-10 SDSs deferred.
   - *Out of scope:* freeform protocol PDFs, lab notebook entries, or any
     unstructured procedure text. That's a different research problem.
3. **Rules engine:** lift handling logic from per-reagent JSON into `rules.yaml`.
   Expose the file in the UI. Make the rules engine confidence-aware
   (`data/handling/<CAS>.json` records the confidence of each fired rule's
   triggering flags).
4. **Polish:** SVG pipetting diagrams, rule-fired tooltips, About-page essay,
   deploy.
5. **STAR methods corpus.** Mine Cell Press Key Resources Tables (PMC OA,
   2017+) to produce a frequency-ranked reagent candidate list, replacing
   the model-memory candidate lists. See `research/star_methods/README.md`
   for the full pipeline (Europe PMC search → JATS XML → KRT parse → dedup
   → rank). Output (`parsed/krt_reagents_ranked.csv`) is the input feed for
   bulk ingestion past 30 reagents. Records produced from this list go
   through the same Phase 2b pipeline — sourced flags only, no
   hand-authoring.

---

## MVP reagent diversity matrix

*Historical — describes how the original 10 reagents were chosen. The
20-reagent Phase 2 expansion and all subsequent imports are driven by the
STAR-methods frequency ranking (see `research/star_methods/README.md`),
not by category coverage.*

The 10-reagent MVP set is structured by category, not by popularity. Each slot
forces a distinct rule family — the goal is to demonstrate the rules engine has
range, not to catalog the most-used reagents in a lab.

Specific products TBD; pick during build.

1. **Enzyme in 50% glycerol** — viscous + protein. Forces: slow aspirate/dispense,
   no vortex, on-ice handling, reverse pipetting.
2. **Volatile solvent** — high vapor pressure. Forces: pre-wet tip cycles, sealed
   reservoirs, fast dispense.
3. **Viscous reagent (no protein)** — Forces: slow aspirate + post-aspirate
   delay, reverse pipetting, wide-bore tips.
4. **Detergent** — surface-active, foaming. Forces: pre-wet, no air gaps,
   careful blow-out.
5. **Fluorophore** — light-sensitive. Forces: amber tubes / foil, photobleaching
   awareness, freeze-thaw cycles.
6. **Reducing agent** — air-oxidizing. Forces: make fresh on day of use,
   single-use aliquots, max-age limit.
7. **Fixative** — hazard + freshness. Forces: fume hood, fresh PFA from powder,
   dedicated waste stream.
8. **DMSO** — hygroscopic + plastic-incompatible + freezes at 19 °C. Forces:
   warm before pipetting, polystyrene incompatibility, skin-permeation.
9. **Dilute oligo / antibody** — adsorption-prone. Forces: LoBind, carrier
   protein, single-use aliquots, freeze-thaw avoidance.
10. **Hygroscopic / deliquescent solid** (e.g., NaOH pellets, anhydrous
    CaCl₂) — solid-state handling. Forces: desiccator storage, weigh fast
    or by difference, cap immediately, date on first opening.

Each category should fire at least one rule that no other category in the set
fires. If two slots end up firing only overlapping rules, swap one out for
something from `data/tacit-knowledge.md` that covers a missing rule family.

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

---

## Machine-readability principles

The database is built **machine-first, human-second**: a liquid handler or LLM
agent must be able to consume a reagent's complete handling profile without
parsing prose, scraping HTML, or running code. The static site is a *view* over
the canonical data — never the source of truth. Concretely:

1. **JSON is the canonical wire format.** Per-reagent files live at
   `data/reagents/<CAS>.json`. CAS# is the primary key. Biologics that lack a
   CAS (antibodies, oligo mixes) use a stable slug (`polyclonal-igg.json`) and
   set `cas: null` with a `cas_note` explaining why. JSON is the format LLMs
   handle most reliably (largest training distribution, no whitespace
   ambiguity, schema-checkable). Don't migrate to anything more exotic.
2. **YAML for rules, Markdown for prose, JSON for data.** YAML is for human-
   edited declarative content (`rules.yaml`); Markdown is for narrative docs
   (`tacit-knowledge.md`, `reagent-inventory.md`); JSON is for everything an
   agent consumes. No data lives only in HTML.
3. **One file per reagent, fully self-contained.** Small enough to fit
   comfortably in an LLM context window; no cross-file joins needed to act on
   it. Properties + GHS + bench knowledge + (eventually) computed handling
   profile all in the same record.
4. **Predictable paths and a manifest.** `data/index.json` (to be added in
   Phase 2) lists every reagent — `[{cas, name, path, schema_version}]` — so
   an agent can crawl the database with one fetch and then pull individual
   records. Stable URL pattern: `/data/reagents/<CAS>.json`.
5. **Persist computed handling profiles to disk.** `apply_rules.py` should
   write its output to `data/handling/<CAS>.json` (not just stdout) so robots
   consume static files, not Python scripts. Each profile carries the list of
   rules that fired with their `cite` strings, so the chain of reasoning is
   inspectable.
6. **Schema versioning.** Every reagent JSON includes `schema_version`; the
   schema itself is at `ingest/schema.json` and `validate.py` enforces it.
   Bumping the schema is a deliberate act with a migration note.
7. **Citations are linkable.** Move from free-text `cite:` strings toward
   BibTeX keys that resolve in `data/citations.bib`. Keeps human readability
   while letting an agent dereference the source.
8. **Bench knowledge is dual-use.** Each `bench_knowledge` bullet stays as
   readable English (so an LLM can summarize it) but, where it corresponds to
   a codified rule, gets tagged with the `rule_id` it came from. Untagged
   bullets are narrative tips; tagged bullets are the human-readable face of
   a structured rule.
9. **Public read endpoint.** A `GET /handling/{cas}` route returning
   `data/handling/<CAS>.json` is part of the v1 surface, not a stretch goal —
   robots need a stable URL scheme more than humans do.
10. **Sourced flags carry provenance.** Every tacit boolean is shaped as
    `{value, confidence, sources[]}`, where each source is
    `{type, ref, agrees}`. Source `type` is a controlled enum:
    `sds_phrase`, `storage_class`, `ghs_hcode`, `pubchem`, `chebi`,
    `rule_derived`, `tacit_knowledge`, `claude_inference`,
    `manufacturer_protocol`. `confidence` is computed by `validate.py` from
    the source list and stored explicitly so robots don't have to
    reimplement the aggregation. Disagreements between sources are kept in
    the record (not silently resolved) — see non-negotiable #5. Full spec
    in `docs/sourced-flags-migration.md`.

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

The reverse principle holds too: don't sacrifice human readability to chase
machine readability. JSON keys are descriptive (`vapor_pressure_kPa_20C`, not
`vp20`), units live in field names, and the bench-knowledge prose is written
for a postdoc, not a parser. Both audiences win when the data is precise.

---

## Non-negotiables

1. Every rule cites a property in the reagent's data OR a published source
   (manufacturer protocol, *Molecular Cloning*, *Current Protocols*, methods paper).
2. Uncited "field consensus" rules are flagged in the UI as such. Stay honest.
3. Rules are declarative and human-readable. No ML.
4. Validate ingestion on 2–3 PDFs end-to-end before running the full set.
5. When two rules conflict, surface the conflict. Don't silently pick.
   Same rule applies to sourced flags: when sources disagree, keep the
   disagreement in the record.
6. Pin versions; anyone should clone + run from `requirements.txt`.
7. SDS PDFs stay local — never committed to git, never redistributed. Only
   the extracted JSON in `data/reagents/` is committed.
8. **No hallucinated tacit knowledge in bulk-imported records.** Tacit
   booleans, `category`, `bench_knowledge`, and `striking_fact` are
   populated only from extracted evidence (SDS prose, GHS, storage class,
   PubChem, ChEBI) or auto-generated from fired rules. Uncorroborated
   values stay `null` with a placeholder source — never invented.
   Hand-authored tacit knowledge is restricted to the legacy MVP-10 set,
   which carries an explicit `claude_inference: legacy_handauthored_mvp`
   source.

---

## Aesthetic

References: Opentrons, Emerald Cloud Lab, Linear, Stripe docs. Goal: "credibly
modern lab tooling," not consumer SaaS.

- Background `#FAFAFA`, text `#0A0A0A`, accent `#E85D2F` (warm lab-amber),
  hairlines `#E5E7EB`. GHS pictograms in their official colors only.
- Type: Inter / IBM Plex Sans for prose; IBM Plex Mono / JetBrains Mono for
  CAS#, JSON, code.
- Hero = one-sentence thesis. Reagent grid of cards (name, CAS, top hazard
  pictogram, one striking handling fact). Detail page is a side-by-side
  "SDS facts | Bench knowledge" + a Robot Profile JSON tab.
- Hairlines, not shadows. SVG diagrams, not photos.

---

## Rules-engine source material

The bench knowledge that `rules.yaml` is derived from lives in
[`data/tacit-knowledge.md`](data/tacit-knowledge.md) — pipetting, thawing,
freezing-point gotchas, light/oxygen/air, adsorption, mixing, concentration,
cell culture, centrifugation, and a meta-rule. Edit that file when adding
categories or bullets; lift from it into `rules.yaml` when codifying.

---

## Open questions

- Frontend: static now; reconsider Astro / React island only if rule-tracing UI
  gets complex.
- Scale: 10 → 50 → 500 reagents. Don't gold-plate the demo before shipping.
- Rule conflicts: design surfacing before there are conflicts to surface.

### Resolved

- Sigma SKUs locked in for all 30 reagents (MVP 10 + Phase 2 expansion 20);
  see `data/reagent-inventory.md`.
- `<CAS> (1).pdf` duplicate cluster cleaned up — single canonical PDF per slot.
- Public `GET /handling/{cas}` endpoint: yes, in v1 — promoted to a
  Machine-readability principle (see above), since robots need a stable URL
  scheme more than humans do.
- Slot-1 enzyme identity (Taq vs DNA Pol I, both CAS 9012-90-2): resolved as
  Taq, matching the existing JSON; inventory label corrected.
