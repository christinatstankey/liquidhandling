# Reagent Inventory

Single source of truth for the 30-reagent Phase 2 dataset: the original
10-reagent MVP (one per diversity-matrix slot) plus a 20-reagent expansion
chosen for **broad rule-pattern coverage** (each new reagent fires at least
one rule pattern not present in the MVP 10).

Status columns fill in as work proceeds:

- **Sigma SKU** — specific MilliporeSigma product number; you fill in based on
  the grade and package size you'd actually pipette.
- **SDS file** — `data/sds-pdfs/<CAS>.pdf` once downloaded; `—` if not yet.
- **JSON** — `data/reagents/<CAS>.json` once a reagent profile is authored.

PDFs are gitignored (see `CLAUDE.md` → Data sources). Only the extracted
JSON in `data/reagents/` is committed.

---

## MVP 10 — diversity matrix

Each slot was chosen to force a distinct rule family. All 10 JSONs already
exist in `data/reagents/`; the SDS PDFs are not yet collected.

| # | Slot | Reagent | CAS | Sigma SKU | SDS file | JSON |
|---|---|---|---|---|---|---|
| 1 | Enzyme in 50% glycerol | DNA polymerase I (*E. coli*) | 9012-90-2 | TBD | — | ✓ |
| 2 | Volatile solvent | Ethanol, anhydrous | 64-17-5 | TBD | — | ✓ |
| 3 | Viscous (no protein) | Glycerol | 56-81-5 | TBD | — | ✓ |
| 4 | Detergent | Polysorbate 20 (Tween 20) | 9005-64-5 | TBD | — | ✓ |
| 5 | Fluorophore | DAPI dihydrochloride | 28718-90-3 | TBD | — | ✓ |
| 6 | Reducing agent | DTT (dithiothreitol) | 3483-12-3 | TBD | — | ✓ |
| 7 | Fixative | Paraformaldehyde | 30525-89-4 | TBD | — | ✓ |
| 8 | DMSO | Dimethyl sulfoxide | 67-68-5 | TBD | — | ✓ |
| 9 | Dilute biologic | Polyclonal IgG | n/a | TBD | — | ✓ |
| 10 | Hygroscopic solid | Sodium hydroxide (pellets) | 1310-73-2 | TBD | — | ✓ |

> SDS PDFs for slots 1–10 not yet collected — these reagents already have JSONs
> authored, so SDS download is deferred until the Phase 2 ingestion validation
> needs them.

---

## Phase 2 expansion (20) — broad rule coverage

Each entry fires at least one rule the MVP 10 does not. Counter-rules
(e.g., TCEP as the air-stable counter to DTT) and paired-incompatibility
rules (e.g., CaCl₂ + EDTA) are deliberate.

| # | Reagent | CAS | New rule pattern it adds | Sigma SKU | SDS file | JSON |
|---|---|---|---|---|---|---|
| 11 | Hydrochloric acid 37% | 7647-01-0 | Strong mineral acid: acid-resistant tips, slow-add-to-water, neutralize before disposal | sial/258148 (ACS reagent) | ✓ | — |
| 12 | Hydrogen peroxide 30% | 7722-84-1 | Liquid oxidizer: decomposes to O₂ gas, light-sensitive, no metal contact | sigald/216763 (ACS reagent) | ✓ | — |
| 13 | Ethidium bromide 10 mg/mL | 1239-45-8 | Mutagen + intercalator: dark storage, charcoal-filter decon, regulated waste | sigma/e1510 (BioReagent 10 mg/mL) | ✓ | — |
| 14 | Diethyl ether | 60-29-7 | Peroxide-forming solvent: track date-opened, max-age limit, low flash point | sigald/346136 (ACS reagent, anhydrous) | ✓ | — |
| 15 | Trifluoroacetic acid | 76-05-1 | Hygroscopic liquid acid: glass-only bottle, never plastic reservoirs, fume hood | sigald/t6508 (ReagentPlus, 99%) | ✓ | — |
| 16 | HEPES (free acid) | 7365-45-9 | Temperature-stable zwitterionic buffer: pKa drift <0.02 / °C (counter-rule for buffers) | sigma/h3375 (≥99.5%) | ✓ | — |
| 17 | EDTA disodium dihydrate | 6381-92-6 | Divalent-cation chelator: downstream incompatibility flag with Mg²⁺/Ca²⁺ enzymes | sial/e4884 (ACS reagent) | ✓ | — |
| 18 | Glutaraldehyde 25% | 111-30-8 | Bifunctional crosslinker: must quench with NaBH₄ or glycine, autofluorescence concern | sial/g5882 (Grade I, 25%) | ✓ | — |
| 19 | TCEP·HCl | 51805-45-9 | Air-stable reducing agent: explicit counter-rule to DTT (does NOT need fresh prep) | aldrich/c4706 | ✓ | — |
| 20 | 2-Mercaptoethanol | 60-24-2 | Volatile reducing agent: combines fume-hood + reducer rules in one reagent | sigma/m3148 (Molecular Biology Grade) | ✓ | — |
| 21 | SDS (sodium dodecyl sulfate) | 151-21-3 | Anionic denaturing detergent: precipitates at 4 °C, not MS-compatible | sigma/l3771 (BioReagent) | ✓ | — |
| 22 | Triton X-100 | 9036-19-5 | Cloud-pointing non-ionic detergent: cloud point ~64 °C, viscous concentrate | sial/x100 | ✓ | — |
| 23 | CHAPS | 75621-03-3 | Zwitterionic, MS-compatible, dialyzable detergent: distinct downstream-assay flag | mm/220201 (Calbiochem, MB Grade) | ✓ | — |
| 24 | PEG 8000 | 25322-68-3 | High-MW polymer: weigh-then-dissolve workflow, viscosity scales with concentration | aldrich/p2139 (powder, avg Mw 8,000) | ✓ | — |
| 25 | Magnesium chloride hexahydrate | 7791-18-6 | Crystalline hydrate: MW calculation gotcha (203.3 vs anhydrous 95.2) | sigald/m9272 (ACS reagent) | ✓ | — |
| 26 | Calcium chloride dihydrate | 10035-04-8 | Hygroscopic divalent salt: paired-incompatibility with EDTA downstream | sial/c8106 (USP testing) | ✓ | — |
| 27 | DEPC (diethyl pyrocarbonate) | 1609-47-8 | RNase decontamination: carbamoylates amines, autoclave-to-inactivate, expires | aldrich/40718 (≥99% NT) | ✓ | — |
| 28 | Acetonitrile | 75-05-8 | Polar aprotic solvent: HPLC-grade purity rule, water-miscible volatile | sial/437557 (ACS reagent, ≥99.5%) | ✓ | — |
| 29 | Sodium hypochlorite 5–6% (bleach) | 7681-52-9 | Oxidizing decon: never-mix-with-acid, decomposes in light, dilute fresh from stock | sigald/239305 (4–5% available chlorine) | ✓ | — |
| 30 | Acrylamide (solid) | 79-06-1 | Neurotoxin powder: weigh in fume hood, dust mask + full PPE, polymerized form is safe | sigma/a9099 (MB, ≥99% HPLC) | ✓ | — |

---

## Update log

- 2026-04-30 — File created. MVP 10 JSONs already authored. SDS download
  campaign starting; validating with HCl 37% (CAS 7647-01-0) before scaling.
- 2026-04-30 — All 20 Phase 2 expansion SDSs collected from Sigma-Aldrich
  via Claude in Chrome browser automation. Saved to `data/sds-pdfs/<CAS>.pdf`.
  All files verified as real PDFs (`%PDF` magic bytes); sizes 148–385 KB,
  consistent with Sigma's 13–18-page SDS template. Sigma SKU column locked
  in. MVP 10 SDSs deferred until Phase 2 ingestion needs them.
