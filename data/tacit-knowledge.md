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

## Buffer reagent quirks

- Tris pKa shifts ~−0.03/°C: a Tris buffer titrated to pH 8.0 at RT is
  ~pH 8.6 at 4 °C and ~pH 7.7 at 37 °C. Same bottle, different pH at
  different bench locations.
- Phosphate precipitates with Ca²⁺, Mg²⁺, Zn²⁺ — incompatible at high
  concentration regardless of order of addition.
- HEPES and other Good's buffers generate H₂O₂ under fluorescent light;
  store dark.
- EDTA chelates well only above pH 8.
- Bicarbonate solutions outgas CO₂ on the bench; pH drifts upward within
  hours once uncapped.

## Water

- "Water" is not one reagent. Milli-Q ≠ autoclaved ≠ nuclease-free ≠
  DEPC-treated; not interchangeable.
- Autoclaving does not destroy RNase. Use commercial nuclease-free or
  DEPC-treated water for RNA work.
- DEPC reacts with primary amines → cannot be used with Tris. DEPC-treat
  the water first, then add Tris from dry powder.
- Milli-Q in an open bottle picks up CO₂ (pH drops), bacteria, and
  airborne nucleases within days.
- For trace-metal work, even Milli-Q has ppb-level contaminants;
  trace-metal-grade or Chelex-treated.

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

## Solvent / plasticware compatibility

- Polystyrene (most cell-culture plates, clear conicals) is dissolved by
  CHCl₃, DCM, toluene, THF, acetone, ethyl acetate. Even brief contact
  leaches styrene oligomers into the sample.
- Polypropylene (Eppendorfs, most "chemical-resistant" tubes) tolerates
  short exposure to most organics but swells in DCM, hexane (long), and
  concentrated TFA. Long storage of organic stocks → glass.
- DMSO leaches plasticizers (phthalates, oligomers) from many plastics
  over hours-to-days. Short pipetting fine; stock storage is glass.
- Strong acids/bases: PP for HCl, NaOH up to ~10 M; PTFE or glass above.
- Concentrated H₂SO₄, HNO₃, HF: never PP. Glass for the first two; PP/PTFE
  *only* for HF (HF eats glass).
- Silicone tubing absorbs hydrophobic small molecules — dosing through
  silicone lines loses drug to the wall.
- Parafilm dissolves in most organics; don't seal solvent-containing tubes.

## Mixing / resuspension

- Lyophilized oligos: spin first (powder on cap), resuspend, sit 5–10 min RT/37 °C,
  vortex. Skipping → wrong concentration.
- Pellets: flick first, then pipette.
- Vortex is fine for most things but can damage proteins (esp. membrane proteins,
  dilute Ab) → invert/flick.
- Magnetic beads: can clump so requires pipetting; gentle; hard pipetting can fragment beads.

## Weighing / handling solids

- Fine powders (SDS, agarose, fluorescent dyes, lyophilized proteins) build
  static on plastic weigh boats and jump out. Foil boats or antistatic gun;
  glass for expensive material.
- Hygroscopic solids (NaOH, CaCl₂, CsCl, many MgCl₂ hydrates) gain mass
  during weighing — work fast or weigh by difference from a sealed source.
- Deliquescent solids (NaOH pellets especially) turn to slush in humid
  air; cap immediately.
- Powder dust from toxic compounds (cycloheximide, doxorubicin, MNNG,
  many kinase inhibitors) is the dominant exposure route — weigh in a
  fume hood or enclosure, not on the open bench.
- "Anhydrous" reagents from a previously-opened bottle usually aren't;
  date on first opening.

## Concentration / storage

- Some 100 mM inhibitor stocks precipitate at 4 °C even when fine at RT.
- MeOH-fixed cells: cold (-20 °C) MeOH added to dry wells, not reverse.
- PFA: make fresh from PFA powder for sensitive applications, not from old
  16% ampoules.
- 10% bleach: fresh-daily prep; degrades fast once diluted.

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

## Hazard interactions (do not co-mingle)

- Bleach + acid → Cl₂. Bleach + ammonia (incl. ammonium-containing buffers)
  → chloramines. Bleach + EtOH/acetone → chloroform and chloroacetone.
  Decontaminate one *or* the other, never sequentially in the same waste.
- Sodium azide + acid → HN₃ (explosive, toxic). Azide + Cu/Pb plumbing →
  shock-sensitive metal azides. Never down the drain.
- Ethers (Et₂O, THF, dioxane, diisopropyl ether) form explosive peroxides
  on long storage, especially once opened. Date bottles; test or discard
  old stock. [field consensus — timeline varies by ether and stabilizer]
- Picric acid must stay wet — dry picric crystals are a primary explosive.
- Piranha (H₂SO₄/H₂O₂) reacts violently with any organic. Mix fresh, vent,
  never store sealed.
- Liquid N₂ in a sealed cryovial → explosion on warming. Vapor-phase
  storage or vented vials only.

## Meta-rule

Read the whole protocol — including materials — before starting. Half of errors
come from missing a single line ("buffer at 37 °C", "add DTT fresh"). Looks
normal ≠ works: yellowed TEMED, precipitated EtBr, over-thawed antibody, "competent"
cells from a freezer with a temperature excursion.
