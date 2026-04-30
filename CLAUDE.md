# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Reagent Handling Intelligence

Proof-of-work project for a lab automation company hiring team. Turns SDS sheets
+ tacit bench knowledge into robot-actionable handling profiles, keyed by CAS#.

Defer to `~/.claude/CLAUDE.md` for anything not covered here.

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

Two parallel streams feed each reagent profile. Don't conflate them — they
solve different problems and have different reliability characteristics.

1. **SDS PDFs (legal/safety facts)** — Sigma-Aldrich (MilliporeSigma) is the
   single source of truth. One vendor template = one parser. Fisher Scientific
   or specialty vendors (NEB, Tocris, Cell Signaling) only as fallback when
   Sigma doesn't carry the product. No public SDS API exists; download URLs
   are predictable enough to script, but respect ToS — polite rate limits, no
   redistribution.
2. **Numeric properties (rule inputs)** — PubChem PUG REST API, base URL
   `https://pubchem.ncbi.nlm.nih.gov/rest/pug/`. Free, no auth, structured
   JSON, keyed by CAS or CID. Pulls vapor pressure, melting/boiling point,
   density, LogP, flash point, GHS hazard codes. More reliable than parsing
   Section 9 free text from the SDS, and gives an independent cross-check.

**Coverage gap:** PubChem is weak on enzymes, antibodies, and oligos. For
those, fall back to manufacturer datasheets (NEB product pages, IDT spec
sheets, antibody vendor PDFs) — and accept that some "properties" for
biologics are categorical (e.g., "store at -20 °C in 50% glycerol") rather
than numeric.

**PDFs stay local.** `data/sds-pdfs/` is gitignored — keeping vendor SDSs
local avoids redistribution questions while preserving the ability to
re-parse when the extractor improves. Only the extracted JSON in
`data/reagents/` is committed. The directory itself is tracked via
`data/sds-pdfs/.gitkeep` so the layout is reproducible.

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
  CLAUDE.md, README.md, requirements.txt
  data/
    reagents/         # one JSON per reagent
    sds-pdfs/         # source PDFs, named by CAS#
    rules.yaml        # the rules engine
    citations.bib
  ingest/
    parse_sds.py      # PDF → JSON
    apply_rules.py    # reagent + rules.yaml → handling profile
    validate.py
  site/
    index.html, reagent.html, about.html
    assets/styles.css, assets/diagrams/
  scripts/build.py    # data/ → site/
```

---

## Phases (ship one before starting next)

**Current phase: Phase 1 (MVP)** — update this line when advancing.

1. **MVP:** hand-author JSON for the 10 reagents in the diversity matrix
   (see below), static frontend, deploy. Sendable.
2. **Ingestion:** SDS PDF → JSON (Sigma template only — see Data sources)
   plus PubChem PUG REST for numeric properties. The standardized 16-section
   GHS/OSHA layout makes PDF parsing tractable; PubChem gives a cleaner cross-
   check on Section 9 numbers. Validate end-to-end on 2–3 reagents from the
   MVP set before scaling. Expand to ~30 reagents.
   *Out of scope:* freeform protocol PDFs, lab notebook entries, or any
   unstructured procedure text. That's a different research problem.
3. **Rules engine:** lift handling logic from per-reagent JSON into `rules.yaml`.
   Expose the file in the UI.
4. **Polish:** SVG pipetting diagrams, rule-fired tooltips, About-page essay,
   deploy.

---

## MVP reagent diversity matrix

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

## Non-negotiables

1. Every rule cites a property in the reagent's data OR a published source
   (manufacturer protocol, *Molecular Cloning*, *Current Protocols*, methods paper).
2. Uncited "field consensus" rules are flagged in the UI as such. Stay honest.
3. Rules are declarative and human-readable. No ML.
4. Validate ingestion on 2–3 PDFs end-to-end before running the full set.
5. When two rules conflict, surface the conflict. Don't silently pick.
6. Pin versions; anyone should clone + run from `requirements.txt`.
7. SDS PDFs stay local — never committed to git, never redistributed. Only
   the extracted JSON in `data/reagents/` is committed.

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
- Specific reagent products for each diversity-matrix slot: TBD as we build.
- Public `GET /handling/{cas}` endpoint: probably yes for the pitch, not in v1.
- Rule conflicts: design surfacing before there are conflicts to surface.
