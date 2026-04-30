# Tacit knowledge taxonomy

Source material for `rules.yaml`. Each bullet is a candidate rule: a property
or reagent class on the left, a handling consequence on the right. Cite where
possible; flag uncited "field consensus" entries when they're lifted into a
rule (see Non-negotiable #2 in `../CLAUDE.md`).

This file is reference material, not a spec. The canonical rules live in
`rules.yaml`; this is the bench knowledge those rules are derived from.

---

## Pipetting

- Volatile solvents (MeOH, EtOH, CHCl₃, ether, acetone): pre-wet 2–3× to
  saturate headspace; otherwise volume drifts and tip drips.
- Viscous (glycerol, PEG, Triton, 50% sucrose, Lipofectamine, mineral oil):
  wide-bore tip, slow aspiration, reverse pipette, wait before withdrawing.
- Foamy/detergent (SDS, BSA, Tween-containing): slow dispense along tube wall.
- Aqueous-organic (phenol:chloroform): pre-equilibrate tip or droplets form.

## Thawing

- Enzymes (REs, polymerases, ligases, RTs): on ice. Many never fully thaw —
  50% glycerol stays liquid at -20 °C; take out, use, return.
- dNTPs, primers, buffers: RT, then vortex + spin (solutes stratify on freezing).
- Antibodies: usually on ice, never refreeze (aliquot before first thaw). Some
  manufacturers say RT — varies.
- Cells in 10% DMSO: fast thaw at 37 °C. Slow thaw kills via ice
  recrystallization (opposite of slow freezing).
- Lipid reagents, mRNA/LNP: follow manufacturer exactly.

## Freezing-point gotchas

- DMSO freezes at 19 °C → solid on ice and in 4 °C cold rooms. Warm to RT.
- Glycerol bacterial stocks: scrape, don't thaw.
- Concentrated salts (5 M NaCl, 3 M NaOAc): keep at RT; supersaturate and crash
  if cooled.
- TRIzol/phenol from 4 °C: bring to RT (phase separation issues).

## Light / oxygen / air

- Fluorophores (FITC, Cy, Alexa), riboflavin, retinoids, DAPI: photobleach;
  dim light, foil tubes.
- Reducing agents (DTT, TCEP, β-ME): oxidize. DTT in solution ~1 wk at 4 °C;
  make fresh for critical work.
- Many kinase/phosphatase inhibitors degrade in aqueous within hours; add at use.
- Coomassie, ECL substrates, luciferin: light-sensitive.
- Sodium azide: never contact acid (HN₃) or copper plumbing.

## Adsorption / container

- Dilute proteins, peptides, oligos, siRNAs: stick to plastic. LoBind tubes;
  add carrier (BSA, tRNA, glycogen) for very dilute.
- Detergents (Triton, Tween): adsorb onto polystyrene over time.
- Cholesterol, hydrophobic drugs: glass only; partition into PP.
- LNPs, liposomes: stick to anything; passivate first.

## Mixing / resuspension

- Lyophilized oligos: spin first (powder on cap), resuspend, sit 5–10 min RT/37 °C,
  vortex. Skipping → wrong concentration.
- Pellets: flick first, then pipette.
- Vortex is fine for most things but kills proteins (esp. membrane proteins,
  dilute Ab) and shears gDNA → invert/flick.
- Magnetic beads: gentle; hard pipetting fragments them.

## Concentration / storage

- Some 100 mM inhibitor stocks precipitate at 4 °C even when fine at RT.
- MeOH-fixed cells: cold (-20 °C) MeOH added to dry wells, not reverse.
- PFA: make fresh from PFA powder for sensitive applications, not from old
  16% ampoules.
- 10% bleach: fresh-daily prep; degrades fast once diluted.
- EDTA chelates well only above pH 8.

## Cell culture

- Trypsin: cold, warm just before use; loses activity at 37 °C.
- FBS: lot-to-lot variation wrecks experiments; lot-test and reserve.
- Pen/Strep degrades in 2–3 wk at 4 °C in media.
- CO₂ equilibration takes 15–30 min; bath-warmed media is alkaline until it sits.
- Trypan blue: filter; aggregates look like dead cells.
- Mycoplasma testing: requires correct preservation, not just "saved cells."

## Centrifugation

- Always balance.
- Temperature matters (RNA always 4 °C).
- Loose pellets: don't disturb on aspiration. Invisible pellets: mark tube
  before spinning.
- Plasticware compatibility: CHCl₃ eats PS; hexane attacks PP at long exposure.

## Meta-rule

Read the whole protocol — including materials — before starting. Half of errors
come from missing a single line ("buffer at 37 °C", "add DTT fresh"). Looks
normal ≠ works: yellowed TEMED, precipitated EtBr, over-thawed antibody, "competent"
cells from a freezer with a temperature excursion.
