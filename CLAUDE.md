# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Reagent Handling Intelligence

Proof-of-work project for a lab automation company hiring team. Turns SDS sheets
+ tacit bench knowledge into robot-actionable handling profiles, keyed by CAS#.

Defer to `~/.claude/CLAUDE.md` for anything not covered here.

---

## Thesis

SDS = legal safety facts. Bench scientist = how to actually use the reagent
(pre-wet for volatiles, thaw enzymes on ice, LoBind for dilute proteins, make DTT
fresh). This project is the bridge: CAS# → structured JSON profile a liquid
handler could consume directly.

The differentiator vs. "another SDS database" is a transparent rules layer —
properties + reagent class → handling, every rule cited.

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

1. **MVP:** hand-author JSON for 10 reagents, static frontend, deploy. Sendable.
2. **Ingestion:** SDS PDF → JSON. Validate against MVP set. Expand to ~30.
3. **Rules engine:** lift handling logic from per-reagent JSON into `rules.yaml`.
   Expose the file in the UI.
4. **Polish:** SVG pipetting diagrams, rule-fired tooltips, About-page essay,
   deploy.

MVP reagent set must cover this diversity (each forces a distinct rule):
enzyme in 50% glycerol, volatile solvent, viscous reagent, detergent, fluorophore,
reducing agent, fixative, DMSO, dilute oligo/antibody, competent cells.

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
- Scale: 10 → 30 → 100 reagents. Don't gold-plate the demo before shipping.
- Public `GET /handling/{cas}` endpoint: probably yes for the pitch, not in v1.
- Rule conflicts: design surfacing before there are conflicts to surface.
