#!/usr/bin/env python3
"""
build.py — build the static site from data/.

Steps:
  1. Copy data/reagents/*.json  → site/data/reagents/
  2. Copy data/handling/*.json  → site/data/profiles/   (pre-generated profiles)
  3. Generate site/data/manifest.json (index for the frontend)
"""
import json
import shutil
from pathlib import Path

REPO_ROOT     = Path(__file__).parent.parent
REAGENTS_SRC  = REPO_ROOT / "data" / "reagents"
HANDLING_SRC  = REPO_ROOT / "data" / "handling"
REAGENTS_DEST = REPO_ROOT / "site" / "data" / "reagents"
PROFILES_DEST = REPO_ROOT / "site" / "data" / "profiles"
MANIFEST_PATH = REPO_ROOT / "site" / "data" / "manifest.json"

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
    "lo_bind_adsorption_prone":     "LoBind tubes required — adsorption-prone protein",
    "desiccator_hygroscopic_solid": "Desiccator storage, weigh fast — hygroscopic",
    "oxidizer_segregate":           "Segregate from organics — strong oxidiser",
    "peroxide_forming_track":       "Test for peroxides before opening",
    "corrosive_add_to_water":       "Add reagent to water, not reverse — corrosive",
    "tcep_air_stable":              "Air-stable reducing agent — stable at 4 °C",
    "skin_penetrant_hazard":        "Nitrile gloves — penetrates intact skin",
    "depc_tris_incompatible":       "Incompatible with Tris buffer — DEPC",
    "depc_autoclave_inactivate":    "Autoclave after treatment — DEPC",
    "fume_hood_toxic":              "Fume hood required — toxic or carcinogenic",
    "flammable_storage":            "Flammable storage cabinet required",
    "air_sensitive_seal":           "Keep tightly sealed — degrades in air",
}

RULE_PRIORITY = list(RULE_SUMMARIES.keys())


def _summary_fact(profile: dict) -> str:
    fired_ids = {r["id"] for r in profile.get("rules_fired", [])}
    for rule_id in RULE_PRIORITY:
        if rule_id in fired_ids:
            return RULE_SUMMARIES[rule_id]
    return "No special handling required"


def copy_reagents() -> int:
    REAGENTS_DEST.mkdir(parents=True, exist_ok=True)
    count = 0
    for src in sorted(REAGENTS_SRC.glob("*.json")):
        shutil.copy2(src, REAGENTS_DEST / src.name)
        count += 1
    print(f"  {count} reagent JSONs → site/data/reagents/")
    return count


def copy_profiles() -> int:
    PROFILES_DEST.mkdir(parents=True, exist_ok=True)
    count = 0
    for src in sorted(HANDLING_SRC.glob("*.json")):
        shutil.copy2(src, PROFILES_DEST / src.name)
        count += 1
    print(f"  {count} handling profiles → site/data/profiles/")
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
            "category":          reagent.get("category"),
            "top_pictogram":     pictograms[0] if pictograms else None,
            "signal_word":       reagent.get("ghs", {}).get("signal_word"),
            "summary_fact":      _summary_fact(profile),
            "rules_fired_count": len(profile.get("rules_fired", [])),
            "file":              reagent_path.name,
        })

    entries.sort(key=lambda e: (e["category"] or "zzz", e["name"].lower()))

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(entries, indent=2) + "\n")
    print(f"  manifest.json — {len(entries)} entries")
    return len(entries)


def main():
    print("=== build: reagents ===")
    copy_reagents()
    print("\n=== build: handling profiles ===")
    copy_profiles()
    print("\n=== build: manifest ===")
    build_manifest()
    print("\nDone. Serve with: python -m http.server 8080 --directory site/")


if __name__ == "__main__":
    main()
