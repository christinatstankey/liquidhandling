#!/usr/bin/env python3
"""
validate.py — check every reagent JSON in data/reagents/ against ingest/schema.json.

Usage:
    python ingest/validate.py              # validate all reagents
    python ingest/validate.py --cas 64-17-5  # validate one reagent
"""
import argparse
import json
import sys
from pathlib import Path

import jsonschema

REPO_ROOT = Path(__file__).parent.parent
SCHEMA_PATH = REPO_ROOT / "ingest" / "schema.json"
REAGENTS_DIR = REPO_ROOT / "data" / "reagents"


def load_schema() -> dict:
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def validate_file(path: Path, schema: dict) -> list[str]:
    """Return a list of error messages; empty list means valid."""
    with open(path) as f:
        data = json.load(f)
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    return [f"{list(e.path)}: {e.message}" for e in errors]


def main():
    parser = argparse.ArgumentParser(description="Validate reagent JSON files against schema.")
    parser.add_argument("--cas", help="CAS# (or filename stem) of a single reagent to validate")
    args = parser.parse_args()

    schema = load_schema()

    if args.cas:
        paths = list(REAGENTS_DIR.glob(f"{args.cas}.json"))
        if not paths:
            print(f"ERROR: no file found for CAS '{args.cas}' in {REAGENTS_DIR}")
            sys.exit(1)
    else:
        paths = sorted(REAGENTS_DIR.glob("*.json"))
        if not paths:
            print(f"No reagent JSON files found in {REAGENTS_DIR}")
            sys.exit(0)

    passed = 0
    failed = 0
    for path in paths:
        errors = validate_file(path, schema)
        if errors:
            print(f"FAIL  {path.name}")
            for err in errors:
                print(f"      {err}")
            failed += 1
        else:
            print(f"OK    {path.name}")
            passed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
