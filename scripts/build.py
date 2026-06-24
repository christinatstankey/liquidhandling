#!/usr/bin/env python3
"""
build.py — build the static site from data/.

Steps:
  1. Copy data/reagents/*.json  → docs/data/reagents/   (clears stale files first)
  2. Copy data/handling/*.json  → docs/data/profiles/   (clears stale files first)
  3. Copy data/handling/*.json  → docs/handling/        (public GET /handling/{cas} endpoint)
  4. Copy data/schemas/*.json    → docs/data/schemas/    (public schema contracts)
  5. Generate docs/data/manifest.json (index for the frontend)
  6. Generate data/index.json + docs/data/index.json (lean index for agents)
"""
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

REPO_ROOT        = Path(__file__).parent.parent
REAGENTS_SRC     = REPO_ROOT / "data" / "reagents"
HANDLING_SRC     = REPO_ROOT / "data" / "handling"
SCHEMAS_SRC      = REPO_ROOT / "data" / "schemas"
REAGENTS_DEST    = REPO_ROOT / "docs" / "data" / "reagents"
PROFILES_DEST    = REPO_ROOT / "docs" / "data" / "profiles"
SCHEMAS_DEST     = REPO_ROOT / "docs" / "data" / "schemas"
HANDLING_ENDPOINT= REPO_ROOT / "docs" / "handling"   # public GET /handling/{cas}
MANIFEST_PATH    = REPO_ROOT / "docs" / "data" / "manifest.json"
AGENT_INDEX_PATH = REPO_ROOT / "data" / "index.json"
PUBLIC_AGENT_INDEX_PATH = REPO_ROOT / "docs" / "data" / "index.json"

# GHS pictogram severity ranking (most → least dangerous).
# Used to pick the most safety-relevant badge for the card view.
PICTOGRAM_SEVERITY = [
    "skull_crossbones",  # acute toxicity
    "exploding_bomb",    # explosives
    "flame",             # flammable; operationally dominant for pipetting volatile solvents
    "oxidizer",          # oxidiser
    "corrosion",         # corrosive
    "health_hazard",     # CMR, respiratory sensitizer, aspiration hazard
    "gas_cylinder",      # compressed gas
    "environment",       # environmental hazard
    "exclamation_mark",  # irritant / warning
]


def _top_pictogram(pictograms: list):
    """Return the highest-severity pictogram from the list, or the first if none match."""
    for p in PICTOGRAM_SEVERITY:
        if p in pictograms:
            return p
    return pictograms[0] if pictograms else None


def _flag(props: dict, key: str):
    """Read a sourced_boolean or flat property value."""
    v = props.get(key)
    return v.get("value") if isinstance(v, dict) else v


def _derive_category(reagent: dict):
    """
    Derive the display category from stored flags and properties.

    Uses the JSON-stored category as a starting point but overrides it
    when the flags say something more specific. Priority order matters:
    enzyme_glycerol and fluorophore are more specific than adsorption_prone.
    Returns "general_reagent" as a final catch-all so the manifest never
    contains null categories.
    """
    stored = reagent.get("category")
    props  = reagent.get("properties", {})
    vp     = _flag(props, "vapor_pressure_kPa_20C")
    visc   = props.get("viscosity_class")
    state  = reagent.get("physical_state")

    # Highest-specificity categories first
    name = reagent.get("name", "").lower()
    cas  = reagent.get("cas", "") or ""

    # Name-pattern lookup tables — used both before and after flag checks so
    # that high-priority name categories (fixative, detergent) beat flag-derived
    # volatile_solvent for reagents whose specific flags haven't been enriched yet.
    _FIXATIVE_NAMES   = {"formaldehyde", "formalin", "acrolein"}
    _DETERGENT_NAMES  = {
        "sds", "sodium dodecyl sulfate", "sodium lauryl sulfate",
        "tween", "triton", "chaps", "chapso", "digitonin", "np-40",
        "brij", "lubrol", "igepal", "nonidet", "pluronic", "cremophor",
        "sarkosyl", "empigen", "zwittergent", "octyl glucoside",
        "octylthioglucoside", "lauryl maltoside", "dodecyl maltoside",
        "4-tert-octylphenol", "nonyl phenyl", "nonoxynol",
    }
    _REDUCING_NAMES   = {
        "dithiothreitol", "dtt", "beta-mercaptoethanol", "2-mercaptoethanol",
        "tcep", "glutathione", "ascorbic acid", "sodium bisulfite",
        "sodium metabisulfite", "tris(2-carboxyethyl)phosphine",
    }
    # Common fluorescent dyes and bioluminescent substrates identifiable by name.
    # "fura" uses "fura-" / "fura " to avoid matching tetrahydrofuran / arabinofuranoside.
    _FLUOROPHORE_NAMES = {
        "fura-", "fura ", "rhodamine", "propidium", "ethidium", "hoechst", "dapi",
        "sytox", "calcein", "coelenterazine", "luciferin", "cresyl violet",
        "dihydroethidium", "mitotracker", "lysotracker", "bodipy", "acridine orange",
    }

    # Flag-based checks — highest specificity first.
    # Primary handling categories: technique- or safety-defining properties
    # take priority over secondary descriptors (light/air sensitivity).
    if _flag(props, "is_fluorophore") is True or any(k in name for k in _FLUOROPHORE_NAMES):
        return "fluorophore"
    if _flag(props, "is_fixative") is True or any(k in name for k in _FIXATIVE_NAMES):
        return "fixative"
    if _flag(props, "is_reducing_agent") is True or any(k in name for k in _REDUCING_NAMES):
        return "reducing_agent"
    if _flag(props, "is_detergent") is True or any(k in name for k in _DETERGENT_NAMES):
        return "detergent"
    if cas == "67-68-5":
        return "dmso"
    # Use is_volatile flag, but require VP > 0.3 kPa when VP is known to exclude
    # low-volatility liquids (oleic acid 0.13 kPa, sulfuric acid 0.13 kPa) while
    # still catching DMF (0.38 kPa) and reagents where VP was not extracted (None).
    if (_flag(props, "is_volatile") is True
            and state not in ("solid", "powder")
            and (not isinstance(vp, (int, float)) or vp > 0.3)):
        return "volatile_solvent"
    if visc == "high" and _flag(props, "is_protein") is not True:
        return "viscous_reagent"
    if _flag(props, "is_hygroscopic") is True and state in ("solid", "powder"):
        return "hygroscopic_solid"
    if _flag(props, "is_protein") is True and (props.get("glycerol_pct") or 0) >= 50:
        return "enzyme_glycerol"
    if _flag(props, "is_protein") is True or _flag(props, "is_adsorption_prone") is True:
        return "adsorption_prone"
    # Secondary categories: for reagents that don't match a primary handling
    # category, surface the most important safety/storage flag.
    if _flag(props, "is_light_sensitive") is True:
        return "light_sensitive"
    if _flag(props, "is_air_sensitive") is True:
        return "air_sensitive"
    # Fall back to stored value, then general_reagent — never return null.
    return stored or "general_reagent"


def _clear_dest(dest: Path) -> int:
    """Delete all *.json files in dest, return count removed."""
    removed = 0
    for f in dest.glob("*.json"):
        f.unlink()
        removed += 1
    return removed

# One-line card summary per rule — highest-priority match wins
RULE_SUMMARIES = {
    "protein_glycerol_on_ice":      "Reverse-pipette on ice — enzyme in 50% glycerol",
    "protein_keep_on_ice":          "Keep on ice throughout — protein",
    "thaw_serum_gently":            "Thaw at 37 °C water bath — serum",
    "pre_wet_volatile":             "Pre-wet tips 3× before aspirating — volatile",
    "viscous_no_protein":           "Wide-bore tips, slow aspirate — viscous",
    "detergent_no_foam":            "Slow dispense, wall touch-off — detergent",
    "light_protect_sensitive":      "Amber tube or foil wrap — photosensitive",
    "make_fresh_reducing_agent":    "Make fresh on day of use — oxidises in hours",
    "fume_hood_fixative":           "Fume hood required, make fresh — fixative",
    "warm_freeze_prone_liquid":     "Warm to RT before pipetting — freezes near RT",
    "lo_bind_adsorption_prone":     "LoBind tubes required — adsorption-prone reagent",
    "desiccator_hygroscopic_solid": "Desiccator storage, weigh fast — hygroscopic",
    "oxidizer_segregate":           "Segregate from organics — strong oxidiser",
    "peroxide_forming_track":       "Test for peroxides before opening",
    "corrosive_add_to_water":       "Add reagent to water, not reverse — corrosive",
    "tcep_air_stable":              "Air-stable reducing agent — stable at 4 °C",
    "skin_penetrant_hazard":        "Nitrile gloves — penetrates intact skin",
    "depc_tris_incompatible":       "Incompatible with Tris buffer — DEPC",
    "depc_autoclave_inactivate":    "Autoclave after treatment — DEPC",
    "fume_hood_toxic":              "Fume hood required — SDS ventilation control",
    "flammable_storage":            "Flammable storage cabinet required",
    "air_sensitive_seal":           "Keep tightly sealed — degrades in air",
}

RULE_PRIORITY = list(RULE_SUMMARIES.keys())


def _summary_fact(profile: dict) -> str:
    fired_ids = {r["id"] for r in profile.get("rules_fired", [])}
    for rule_id in RULE_PRIORITY:
        if rule_id in fired_ids:
            return RULE_SUMMARIES[rule_id]
    return "No handling rule assigned yet"


def copy_reagents() -> int:
    REAGENTS_DEST.mkdir(parents=True, exist_ok=True)
    removed = _clear_dest(REAGENTS_DEST)
    if removed:
        print(f"  cleared {removed} stale reagent JSONs from docs/data/reagents/")
    count = 0
    for src in sorted(REAGENTS_SRC.glob("*.json")):
        shutil.copy2(src, REAGENTS_DEST / src.name)
        count += 1
    print(f"  {count} reagent JSONs → docs/data/reagents/")
    return count


def copy_profiles() -> int:
    PROFILES_DEST.mkdir(parents=True, exist_ok=True)
    removed = _clear_dest(PROFILES_DEST)
    if removed:
        print(f"  cleared {removed} stale profiles from docs/data/profiles/")
    count = 0
    for src in sorted(HANDLING_SRC.glob("*.json")):
        shutil.copy2(src, PROFILES_DEST / src.name)
        count += 1
    print(f"  {count} handling profiles → docs/data/profiles/")
    return count


def copy_handling_endpoint() -> int:
    """Publish profiles at /handling/{cas}.json to fulfil the documented v1 URL contract."""
    HANDLING_ENDPOINT.mkdir(parents=True, exist_ok=True)
    removed = _clear_dest(HANDLING_ENDPOINT)
    if removed:
        print(f"  cleared {removed} stale files from docs/handling/")
    count = 0
    for src in sorted(HANDLING_SRC.glob("*.json")):
        shutil.copy2(src, HANDLING_ENDPOINT / src.name)
        count += 1
    print(f"  {count} handling profiles → docs/handling/  (GET /handling/{{cas}})")
    return count


def copy_schemas() -> int:
    """Publish machine-readable JSON Schemas under /data/schemas/."""
    SCHEMAS_DEST.mkdir(parents=True, exist_ok=True)
    removed = _clear_dest(SCHEMAS_DEST)
    if removed:
        print(f"  cleared {removed} stale schemas from docs/data/schemas/")
    count = 0
    for src in sorted(SCHEMAS_SRC.glob("*.json")):
        shutil.copy2(src, SCHEMAS_DEST / src.name)
        count += 1
    print(f"  {count} JSON Schema files → docs/data/schemas/")
    return count


def build_manifest() -> int:
    entries = []
    for reagent_path in sorted(REAGENTS_SRC.glob("*.json")):
        reagent = json.loads(reagent_path.read_text())

        profile_path = HANDLING_SRC / reagent_path.name
        profile = json.loads(profile_path.read_text()) if profile_path.exists() else {}

        pictograms = reagent.get("ghs", {}).get("pictograms", [])
        entries.append({
            "name":              reagent["name"],
            "cas":               reagent.get("cas"),
            "category":          _derive_category(reagent),
            "top_pictogram":     _top_pictogram(pictograms),
            "signal_word":       reagent.get("ghs", {}).get("signal_word"),
            "summary_fact":      _summary_fact(profile),
            "rules_fired_count": len(profile.get("rules_fired", [])),
            "file":              reagent_path.name,
        })

    # Explicit category order: chemical categories first, biologics last.
    CATEGORY_ORDER = {
        "volatile_solvent":  0,
        "fixative":          1,
        "reducing_agent":    2,
        "fluorophore":       3,
        "light_sensitive":   4,
        "air_sensitive":     5,
        "detergent":         6,
        "viscous_reagent":   7,
        "dmso":              8,
        "hygroscopic_solid": 9,
        "enzyme_glycerol":  10,
        "general_reagent":  50,
        "adsorption_prone": 99,  # proteins / biologics — last
    }
    entries.sort(key=lambda e: (
        CATEGORY_ORDER.get(e["category"], 8 if e["category"] else 10),
        e["name"].lower(),
    ))

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(entries, indent=2) + "\n")
    print(f"  manifest.json — {len(entries)} entries")
    return len(entries)


def build_agent_index() -> int:
    entries = []
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for reagent_path in sorted(REAGENTS_SRC.glob("*.json")):
        reagent = json.loads(reagent_path.read_text())
        cas = reagent.get("cas")
        handling_path = HANDLING_SRC / reagent_path.name
        profile = json.loads(handling_path.read_text()) if handling_path.exists() else {}
        entries.append({
            "cas": cas,
            "name": reagent["name"],
            "category": _derive_category(reagent),
            "reagent_path": f"/data/reagents/{cas}.json",
            "handling_path": f"/handling/{cas}.json",
            "schema_version": reagent["schema_version"],
            "profile_version": profile.get("profile_version"),
            "reagent_sha256": sha256(reagent_path.read_bytes()).hexdigest(),
            "handling_sha256": sha256(handling_path.read_bytes()).hexdigest() if handling_path.exists() else None,
            "generated_at": generated_at,
        })

    entries.sort(key=lambda e: e["cas"])
    payload = json.dumps(entries, indent=2) + "\n"

    AGENT_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGENT_INDEX_PATH.write_text(payload)

    PUBLIC_AGENT_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_AGENT_INDEX_PATH.write_text(payload)

    print(f"  index.json — {len(entries)} entries")
    return len(entries)


def main():
    print("=== build: reagents ===")
    copy_reagents()
    print("\n=== build: handling profiles ===")
    copy_profiles()
    print("\n=== build: /handling endpoint ===")
    copy_handling_endpoint()
    print("\n=== build: schemas ===")
    copy_schemas()
    print("\n=== build: manifest ===")
    build_manifest()
    print("\n=== build: agent index ===")
    build_agent_index()
    print("\n=== build: audit ===")
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "audit_enrichment_coverage.py"),
         "--publication"],
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(result.returncode)
    print("\nDone. Serve with: python3 -m http.server 8080 --directory docs/")


if __name__ == "__main__":
    main()
