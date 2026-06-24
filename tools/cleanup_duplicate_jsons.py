#!/usr/bin/env python3
"""Resolve Finder-style duplicate JSON files.

The pipeline keys reagent and handling records by CAS filename. Files such as
``64-17-5 2.json`` are ambiguous for both manifests and static endpoints.

For canonical reagent records, suffixed files can represent alternate SDS
parses for the same CAS. In that case we keep the file with the later
``sds_source.revision_date`` as required by AGENTS.md. Generated handling and
docs copies are simply deleted; they are rebuilt from canonical data.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
TARGET_DIRS = [
    REPO_ROOT / "data" / "handling",
    REPO_ROOT / "docs" / "data" / "reagents",
    REPO_ROOT / "docs" / "data" / "profiles",
    REPO_ROOT / "docs" / "handling",
]
SUFFIX_RE = re.compile(r" \d+(\.json)$")


def canonical_name(name: str) -> str:
    return SUFFIX_RE.sub(r"\1", name)


def load_json(path: Path):
    return json.loads(path.read_text())


def parse_revision(value: str | None) -> dt.date | None:
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def reagent_revision(payload: dict) -> dt.date | None:
    return parse_revision(payload.get("sds_source", {}).get("revision_date"))


def resolve_reagent_duplicates(write: bool) -> dict[str, int]:
    path = REPO_ROOT / "data" / "reagents"
    counts = {
        "suffixed": 0,
        "removed": 0,
        "promoted": 0,
        "missing_canonical": 0,
        "cas_mismatch": 0,
        "unresolved": 0,
        "invalid_json": 0,
    }

    for duplicate in sorted(path.glob("*.json")):
        if not SUFFIX_RE.search(duplicate.name):
            continue

        counts["suffixed"] += 1
        canonical = duplicate.with_name(canonical_name(duplicate.name))

        if not canonical.exists():
            counts["missing_canonical"] += 1
            continue

        try:
            duplicate_json = load_json(duplicate)
            canonical_json = load_json(canonical)
        except json.JSONDecodeError:
            counts["invalid_json"] += 1
            continue

        if duplicate_json.get("cas") != canonical_json.get("cas"):
            counts["cas_mismatch"] += 1
            continue

        duplicate_rev = reagent_revision(duplicate_json)
        canonical_rev = reagent_revision(canonical_json)

        if duplicate_rev is None and canonical_rev is None:
            counts["unresolved"] += 1
            continue

        if duplicate_rev is not None and (
            canonical_rev is None or duplicate_rev > canonical_rev
        ):
            counts["promoted"] += 1
            if write:
                canonical.write_text(json.dumps(duplicate_json, indent=2) + "\n")

        counts["removed"] += 1
        if write:
            duplicate.unlink()

    return counts


def remove_generated_duplicates(path: Path, write: bool) -> dict[str, int]:
    counts = {
        "suffixed": 0,
        "removed": 0,
    }

    for duplicate in sorted(path.glob("*.json")):
        if not SUFFIX_RE.search(duplicate.name):
            continue

        counts["suffixed"] += 1
        counts["removed"] += 1
        if write:
            duplicate.unlink()

    return counts


def count_json(path: Path) -> int:
    return sum(1 for _ in path.glob("*.json"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="delete verified duplicates")
    args = parser.parse_args()

    mode = "write" if args.write else "dry-run"
    print(f"cleanup duplicate JSONs ({mode})")

    reagent_counts = resolve_reagent_duplicates(args.write)
    print(
        "data/reagents: "
        f"suffixed={reagent_counts['suffixed']} "
        f"promoted={reagent_counts['promoted']} "
        f"removed={reagent_counts['removed']} "
        f"remaining_json={count_json(REPO_ROOT / 'data' / 'reagents')}"
    )

    total_unresolved = (
        reagent_counts["missing_canonical"]
        + reagent_counts["cas_mismatch"]
        + reagent_counts["unresolved"]
        + reagent_counts["invalid_json"]
    )

    for directory in TARGET_DIRS:
        counts = remove_generated_duplicates(directory, args.write)
        rel = directory.relative_to(REPO_ROOT)
        print(
            f"{rel}: suffixed={counts['suffixed']} "
            f"removed={counts['removed']} remaining_json={count_json(directory)}"
        )

    if total_unresolved:
        print(
            "refusing clean success: "
            f"missing_canonical={reagent_counts['missing_canonical']} "
            f"cas_mismatch={reagent_counts['cas_mismatch']} "
            f"unresolved_revision={reagent_counts['unresolved']} "
            f"invalid_json={reagent_counts['invalid_json']}"
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
