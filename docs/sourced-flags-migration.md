# Sourced Flags Migration — Design Doc

**Status:** approved, ready for implementation. Six open decisions
resolved (see *Resolved decisions* section). No code changes shipped yet.
**Scope:** schema change only. No `parse_sds.py` extension, no
prose-detector wiring, no rules-engine logic changes — those are
follow-up docs.

---

## Goal

Every tacit boolean in a reagent JSON currently looks like this:

```json
"is_hygroscopic": true
```

…with no record of *why* it's true. After migration, every tacit boolean
carries a value plus a list of sources and an aggregated confidence:

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

This surfaces:
- **Provenance** — every flag is auditable back to a phrase, an ontology
  ID, a structural pattern, or a tacit-knowledge anchor.
- **Disagreement** — when prose, structure, and tacit knowledge don't
  agree, the disagreement is in the record (not silently resolved).
- **Confidence tiering** — robots and rule writers can decide whether to
  trust a single low-confidence signal vs. require corroboration.

The flat fields stay flat for any property that is intrinsically numeric
or already SDS-grounded with high reliability (vapor pressure, flash
point, viscosity, melting point, storage temperature). Migration is
**tacit booleans only** in this round.

---

## Source taxonomy (controlled vocabulary)

Every entry in a `sources` list has `{ type, ref, agrees }`. The `type`
field is a controlled enum so `validate.py` can grade confidence
deterministically. `ref` is a free-text string keyed to the source type.
`agrees` is true when the source supports the flag's `value`, false when
it contradicts (kept in the record so disagreements are visible).

| `type` | What it is | `ref` shape | Confidence |
|---|---|---|---|
| `sds_phrase` | Literal phrase match in §7/§10 | `section_<n>:"<exact phrase>"` | high |
| `storage_class` | Sigma ADR class line in §7 | `<class>-<short_label>` (e.g. `8B-corrosive`) | high |
| `ghs_hcode` | Hazard code in §2 | `H<code>` (e.g. `H271`) | high |
| `pubchem` | PubChem PUG REST property | `cid:<n>:<property>` | high |
| `chebi` | ChEBI ontology classification | `CHEBI:<n>` | high |
| `rule_derived` | Derived from another flag via rules engine | `rule_id:<id>` | inherits source's confidence |
| `tacit_knowledge` | Anchor in `tacit-knowledge.md` | `#<anchor>` | low |
| `claude_inference` | Claude judgment with no other corroboration | `<short reason>` | low |
| `manufacturer_protocol` | Vendor product page or protocol | `<vendor>:<url>` | medium |

Out of scope for v1 (can be added later): `wikidata`, `cas_classification`,
`hsdb`, vendor-specific schemas.

---

## Confidence aggregation rules

`confidence` on the flag is computed by `validate.py` from the sources:

- **`high`** — at least one `high`-tier source agrees, AND no `high`-tier
  source disagrees.
- **`medium`** — at least one `medium`-tier source agrees and no high
  source contradicts; OR exactly one `high` source disagrees with the
  rest (flag the disagreement but don't auto-flip).
- **`low`** — only `low`-tier sources support the value, OR sources
  contradict with no clear majority among high-tier signals.

Disagreement detection is `validate.py`'s job. Disagreements don't block
validation — they emit a warning the user can review, in keeping with
non-negotiable #5 ("when two rules conflict, surface the conflict").

The "Tightly closed. Dry." case worked through:

- For NaOH (CAS 1310-73-2, currently `is_hygroscopic: true` in the
  hand-authored JSON): sources after migration would be
  `[ {storage_class: "8B-corrosive"}, {sds_phrase: "Tightly closed. Dry."},
    {chebi: "CHEBI:32145"} ]` — three signals, all agree. Confidence:
  high.
- For DTT (CAS 3483-12-3, currently `is_hygroscopic: false`): sources
  would be `[ {sds_phrase: "Tightly closed. Dry."} ]` — only the weak
  hint. Single low-tier source isn't enough to flip the flag. Confidence:
  low. Value stays false unless ChEBI corroborates.

This is what makes the weak phrase usable: it's evidence, not a verdict.

---

## Schema diff (`ingest/schema.json`)

### Current

```json
"properties": {
  "is_hygroscopic": { "type": ["boolean", "null"] },
  ...
}
```

### Target

```json
"properties": {
  "is_hygroscopic": { "$ref": "#/definitions/sourced_boolean" },
  ...
},
"definitions": {
  "sourced_boolean": {
    "type": "object",
    "required": ["value", "sources"],
    "additionalProperties": false,
    "properties": {
      "value":      { "type": ["boolean", "null"] },
      "confidence": { "type": "string", "enum": ["high", "medium", "low"] },
      "sources": {
        "type": "array",
        "minItems": 1,
        "items": {
          "type": "object",
          "required": ["type", "ref", "agrees"],
          "additionalProperties": false,
          "properties": {
            "type": {
              "type": "string",
              "enum": [
                "sds_phrase", "storage_class", "ghs_hcode",
                "pubchem", "chebi", "rule_derived",
                "tacit_knowledge", "claude_inference",
                "manufacturer_protocol"
              ]
            },
            "ref":    { "type": "string" },
            "agrees": { "type": "boolean" }
          }
        }
      }
    }
  }
}
```

### Tacit booleans being migrated (in-scope)

```
is_protein, is_volatile, is_flammable, is_light_sensitive, is_fluorophore,
is_reducing_agent, oxidizes_in_solution, make_fresh, is_detergent,
is_foaming, is_fixative, fume_hood_required, is_hygroscopic,
is_deliquescent, is_corrosive, is_adsorption_prone, lo_bind_required,
requires_ice, skin_penetrant
```

### Staying flat (out of scope this round)

```
glycerol_pct, viscosity_class, viscosity_mPas_20C, vapor_pressure_kPa_20C,
flash_point_C, excitation_nm, emission_nm, solution_half_life_at_4C_days,
freeze_point_C, freeze_thaw_max_cycles, plastic_incompatible, storage_temp_C
```

Numeric properties have a different provenance problem (PubChem vs SDS
disagreement on the same number) and deserve their own migration. Mixing
them into this round bloats the diff and slows the decision.

### `schema_version` field

CLAUDE.md says every reagent JSON should carry `schema_version`. None
currently do. Add it as a top-level required field at the same time:

```json
"schema_version": { "type": "string", "pattern": "^\\d+\\.\\d+$" }
```

After migration, all 10 existing records become `"schema_version": "2.0"`.
The flat schema retroactively becomes `1.0` (no record actually carries
that, but the bump is documented).

---

## Migration approach for the 10 existing JSONs

The 10 hand-authored MVP records already encode tacit knowledge — that's
the truth-set. Migration must preserve their values without falsely
attributing them to extracted sources.

Rule: every existing tacit boolean gets wrapped with a single source of
type `claude_inference` with `ref: "legacy_handauthored_mvp"` and
`confidence: medium`. This honestly records that the value came from
chemical knowledge during MVP authoring, not from extracted SDS data,
without forcing us to retroactively cite each one.

Then, in a follow-up pass after the prose detector ships, those records
get re-derived flags from prose/structural/ontology channels. Sources
accumulate; the original `claude_inference:legacy_handauthored_mvp`
source remains in the list as part of the audit trail.

Concretely:

```python
# tools/migrate_to_sourced_flags.py (sketch)
TACIT_BOOLEANS = {...}  # the 19 names listed above
LEGACY_SOURCE = {
    "type": "claude_inference",
    "ref":  "legacy_handauthored_mvp",
    "agrees": True,
}
for path in REAGENTS_DIR.glob("*.json"):
    record = json.loads(path.read_text())
    for flag in TACIT_BOOLEANS:
        old_value = record["properties"].get(flag)
        record["properties"][flag] = {
            "value":      old_value,
            "confidence": "medium" if old_value is not None else "low",
            "sources":    [LEGACY_SOURCE],
        }
    record["schema_version"] = "2.0"
    path.write_text(json.dumps(record, indent=2) + "\n")
```

This is a one-shot script committed under `tools/` (NOT `ingest/`),
runnable once per database. It's not part of the steady-state pipeline.

---

## `validate.py` changes

After migration, `validate.py` does three things:

1. **Schema validation** (existing) — JSONSchema check against the new
   schema.
2. **Source-confidence reconciliation** (new) — for each tacit boolean,
   recompute `confidence` from `sources` and warn if the stored
   `confidence` disagrees. (Keep them stored explicitly so a robot
   reading the JSON doesn't have to recompute.)
3. **Cross-flag consistency** (new) — opinionated invariants. Initial set:
   - `category: fluorophore` ⟹ `is_fluorophore.value = true`
   - `category: reducing_agent` ⟹ `is_reducing_agent.value = true`
   - `is_hygroscopic.value = true` ⟹ `sds_facts.storage` mentions
     "desiccator" OR "dry"
   - GHS includes H271/H272 ⟹ `is_oxidizer.value = true`
     *(needs new flag — see open question 4)*
   - Sigma storage class 8 ⟹ `is_corrosive.value = true`

Invariant violations are warnings, not errors. They go to a separate
report file (`data/validation-warnings.md`) so the diffs are reviewable.

---

## `apply_rules.py` changes

The rules engine reads tacit booleans through a new accessor that
defaults to `.value`:

```python
# Before
if reagent["properties"]["is_hygroscopic"]:
    fire_rule(...)

# After
def flag_value(reagent, name):
    raw = reagent["properties"].get(name)
    if isinstance(raw, dict):
        return raw.get("value")
    return raw  # backward-compat for any unmigrated record

if flag_value(reagent, "is_hygroscopic"):
    fire_rule(...)
```

Rules in `rules.yaml` don't change — they still reference `is_hygroscopic`
by name. Only the lookup in `apply_rules.py` changes.

The rules engine **also gets confidence-aware**: each fired rule records
the confidence of the flag(s) that triggered it. Output JSON
in `data/handling/<CAS>.json` includes:

```json
{
  "rules_fired": [
    {
      "rule_id":   "desiccator_storage_hygroscopic_solid",
      "triggered_by": ["is_hygroscopic"],
      "confidence":   "high",
      "cite":         "..."
    }
  ]
}
```

Robots and humans can both filter by confidence — e.g., "only show
high-confidence rules in the production handling profile, surface
medium-confidence ones for review."

---

## Phased rollout

1. **Schema + migration script** (this doc, then implementation).
   - Update `ingest/schema.json` to v2.0.
   - Write `tools/migrate_to_sourced_flags.py`.
   - Run on the 10 MVP JSONs.
   - Update `validate.py` to handle v2.0.
   - Update `apply_rules.py` accessor.
   - All 10 records still pass validation; rules engine output unchanged.
   - Commit.
2. **Prose detector → SDS sources** (separate doc, follow-up).
   - Extend `parse_sds.py` with the phrase patterns from
     `scripts/sds_prose_audit.py`.
   - Patch sources into existing records where prose corroborates.
   - DTT/DAPI etc. now have `claude_inference + sds_phrase("Tightly
     closed. Dry.")`; NaOH gets `+ storage_class:8B`; etc.
3. **ChEBI ontology channel** (separate doc, follow-up).
   - PubChem PUG REST → ChEBI ID lookup keyed by CAS.
   - Adds `chebi:` sources to records and supports class-membership flags
     like `is_fluorophore`, `is_reducing_agent`, `is_intercalator`.
   - DAPI gets `chebi:CHEBI:51231 + rule_derived:fluorophore→light_sensitive`.
   - Anything ChEBI doesn't classify falls through to the honest
     `tacit_knowledge` / `claude_inference` channel — no structural
     inference layer.
4. **Phase 2 expansion 20 reagents** authored with the full pipeline
   from day one — they never have a flat-schema phase.

---

## Resolved decisions

1. **Source as list of structured entries.** Each tacit boolean carries
   `{ value, confidence, sources[] }` where `sources` is a list. Single
   strings would lose multi-source corroboration.
2. **Confidence stored explicitly + reconciled by `validate.py`.**
   Robots reading `data/handling/<CAS>.json` should not have to
   reimplement aggregation logic; storing is friendlier downstream.
3. **Numeric properties stay flat in this round.** PubChem-vs-SDS
   numeric provenance is a separate problem deferred to a v2.1
   migration; mixing it in here bloats the diff.
4. **The four new flags are added in this same migration:**
   `is_oxidizer`, `is_air_sensitive`, `is_peroxide_forming`,
   `is_water_reactive`. They land as required keys in the schema with
   `value: null` and `sources: [{type: claude_inference, ref:
   "not_yet_assessed", agrees: false}]` for the 10 existing JSONs —
   honest about the fact we haven't evaluated them yet. (Schema
   `minItems: 1` on sources forces this — every flag must have at
   least one source, even if it's a placeholder.)
5. **Migration script lives in `tools/`** (new directory). Keeps
   one-shot migrations distinct from the steady-state ingest and
   build pipelines.
6. **Soft accessor in `apply_rules.py`** that handles both flat and
   sourced shapes. Reduces risk of breaking the rules engine during a
   partially-migrated state.

### Source channels (final, simplified)

After consolidation, every flag's `sources` list contains entries from
exactly three families:

- **SDS-derived** (`sds_phrase`, `storage_class`, `ghs_hcode`) — primary
  channel, fully automated by `parse_sds.py` extension.
- **ChEBI ontology** (`chebi`) — secondary channel, fully automated via
  PubChem PUG REST → ChEBI ID lookup. Catches class memberships the
  SDS won't tell you (fluorophores → light-sensitive, reducing agents,
  intercalators).
- **Honest fallback** (`tacit_knowledge`, `claude_inference`) — when
  neither SDS nor ChEBI corroborate, the flag is set from chemical
  knowledge with explicit citation back to `tacit-knowledge.md` or a
  short reason string. Confidence: low.

No RDKit / SMARTS structural channel. If neither the SDS nor ChEBI
catches a class, we fall back honestly to tacit knowledge rather than
add a third inference layer.

---

## What this doc does NOT do

- Doesn't change `parse_sds.py` (that's the next doc).
- Doesn't add prose detection or structural inference.
- Doesn't migrate the bench_knowledge bullets to carry rule_id tags
  (separate concern, mentioned in CLAUDE.md principle 8).
- Doesn't address numeric property provenance (v2.1).
- Doesn't introduce a `data/index.json` manifest (CLAUDE.md principle 4
  — separate concern).
