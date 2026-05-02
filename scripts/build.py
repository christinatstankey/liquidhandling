#!/usr/bin/env python3
"""
build.py — build the static site from data/.

Steps:
  1. Copy data/reagents/*.json → site/data/reagents/
  2. Run apply_rules.py on each reagent → site/data/profiles/
  3. Generate site/data/manifest.json (index of all reagents for the frontend)
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
REAGENTS_SRC  = REPO_ROOT / "data" / "reagents"
REAGENTS_DEST = REPO_ROOT / "site" / "data" / "reagents"
PROFILES_DEST = REPO_ROOT / "site" / "data" / "profiles"
MANIFEST_PATH = REPO_ROOT / "site" / "data" / "manifest.json"
APPLY_RULES   = REPO_ROOT / "ingest" / "apply_rules.py"

# Diversity-matrix slot order (slot 1–10). Controls card order on the index page.
CATEGORY_ORDER = [
    "enzyme_glycerol",
    "volatile_solvent",
    "viscous_reagent",
    "detergent",
    "fluorophore",
    "reducing_agent",
    "fixative",
    "dmso",
    "adsorption_prone",
    "hygroscopic_solid",
]


def _sort_key(path: Path) -> int:
    """Sort reagent files by diversity-matrix slot; unknown categories go last."""
    with open(path) as f:
        data = json.load(f)
    cat = data.get("category", "")
    try:
        return CATEGORY_ORDER.index(cat)
    except ValueError:
        return len(CATEGORY_ORDER)


def copy_reagents():
    REAGENTS_DEST.mkdir(parents=True, exist_ok=True)
    for src in sorted(REAGENTS_SRC.glob("*.json"), key=_sort_key):
        dest = REAGENTS_DEST / src.name
        shutil.copy2(src, dest)
        print(f"  copied {src.name}")


def generate_profiles():
    PROFILES_DEST.mkdir(parents=True, exist_ok=True)
    for src in sorted(REAGENTS_SRC.glob("*.json"), key=_sort_key):
        stem = src.stem
        out = PROFILES_DEST / f"{stem}.json"
        result = subprocess.run(
            [sys.executable, str(APPLY_RULES), str(src), "--out", str(out)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"  ERROR generating profile for {src.name}:")
            print(result.stderr)
        else:
            print(f"  profile {stem}.json")


def build_manifest():
    entries = []
    for src in sorted(REAGENTS_SRC.glob("*.json"), key=_sort_key):
        with open(src) as f:
            reagent = json.load(f)
        # top_pictogram is the first (most severe) GHS pictogram, or None
        pictograms = reagent.get("ghs", {}).get("pictograms", [])
        entries.append({
            "name":          reagent["name"],
            "cas":           reagent["cas"],
            "category":      reagent["category"],
            "top_pictogram": pictograms[0] if pictograms else None,
            "signal_word":   reagent.get("ghs", {}).get("signal_word"),
            "file":          src.name,
        })
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH, "w") as f:
        json.dump(entries, f, indent=2)
    print(f"  manifest.json — {len(entries)} reagents")


def main():
    print("=== build: copying reagents ===")
    copy_reagents()

    print("\n=== build: generating handling profiles ===")
    generate_profiles()

    print("\n=== build: manifest ===")
    build_manifest()

    print("\nDone. Serve with: python -m http.server 8080 --directory site/")


if __name__ == "__main__":
    main()
