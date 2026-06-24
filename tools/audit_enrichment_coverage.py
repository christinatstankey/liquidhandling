#!/usr/bin/env python3
"""
audit_enrichment_coverage.py - check whether generated data shows evidence
that enrichment and publication stages actually ran.

This is not a schema validator. It is an operational coverage audit:
schema-valid records can still be under-enriched if every sourced flag remains
at the not_yet_assessed placeholder.

Usage:
    python tools/audit_enrichment_coverage.py
    python tools/audit_enrichment_coverage.py --require-sds-provenance data/reagents/64-17-5.json
    python tools/audit_enrichment_coverage.py --publication
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).parent.parent
REAGENTS_DIR = REPO_ROOT / "data" / "reagents"
HANDLING_DIR = REPO_ROOT / "data" / "handling"
SDS_DIR = REPO_ROOT / "data" / "sds-pdfs"

DOCS_REAGENTS_DIR = REPO_ROOT / "docs" / "data" / "reagents"
DOCS_PROFILES_DIR = REPO_ROOT / "docs" / "data" / "profiles"
DOCS_HANDLING_DIR = REPO_ROOT / "docs" / "handling"
DOCS_MANIFEST = REPO_ROOT / "docs" / "data" / "manifest.json"
DATA_INDEX = REPO_ROOT / "data" / "index.json"
DOCS_INDEX = REPO_ROOT / "docs" / "data" / "index.json"

SDS_SOURCE_TYPES = {"sds_phrase", "storage_class", "ghs_hcode"}
PLACEHOLDER_SOURCE = ("claude_inference", "not_yet_assessed")
LOCAL_CONFLICT_COPY_RE = re.compile(r" \d+$")


def _json_files(paths: list[str]) -> list[Path]:
    if paths:
        return [Path(p) for p in paths]
    return sorted(REAGENTS_DIR.glob("*.json"))


def _load_json(path: Path):
    with path.open() as f:
        return json.load(f)


def _record_id(path: Path, record: dict) -> str:
    return record.get("cas") or path.stem


def _flag_objects(record: dict) -> Iterable[tuple[str, dict]]:
    for key, value in record.get("properties", {}).items():
        if isinstance(value, dict) and isinstance(value.get("sources"), list):
            yield key, value


def _has_source_type(record: dict, source_types: set[str]) -> bool:
    return any(
        source.get("type") in source_types
        for _, flag in _flag_objects(record)
        for source in flag.get("sources", [])
    )


def _sds_enrichment_status(record: dict) -> str | None:
    sds_source = record.get("sds_source")
    if not isinstance(sds_source, dict):
        return None
    status = sds_source.get("enrichment_status")
    return status if isinstance(status, str) else None


def _is_only_placeholder(flag: dict) -> bool:
    sources = flag.get("sources", [])
    return (
        len(sources) == 1
        and sources[0].get("type") == PLACEHOLDER_SOURCE[0]
        and sources[0].get("ref") == PLACEHOLDER_SOURCE[1]
    )


def _path_stems(directory: Path) -> set[str]:
    if not directory.exists():
        return set()
    return {
        path.stem
        for path in directory.glob("*.json")
        if not LOCAL_CONFLICT_COPY_RE.search(path.stem)
    }


def _read_list(path: Path, failures: list[str]) -> list:
    if not path.exists():
        failures.append(f"missing {path.relative_to(REPO_ROOT)}")
        return []
    data = _load_json(path)
    if not isinstance(data, list):
        failures.append(f"{path.relative_to(REPO_ROOT)} is not a JSON list")
        return []
    return data


def audit_enrichment(paths: list[Path], require_sds_provenance: bool) -> list[str]:
    failures: list[str] = []
    records = []

    for path in paths:
        record = _load_json(path)
        rid = _record_id(path, record)
        records.append((path, record, rid))

    local_pdf_ids = {path.stem for path in SDS_DIR.glob("*.pdf")}
    records_with_local_pdf = [
        (path, record, rid)
        for path, record, rid in records
        if rid in local_pdf_ids or path.stem in local_pdf_ids
    ]
    records_with_sds_sources = [
        (path, rid)
        for path, record, rid in records_with_local_pdf
        if _has_source_type(record, SDS_SOURCE_TYPES)
    ]
    parsed_with_actionable = [
        (path, rid)
        for path, record, rid in records_with_local_pdf
        if _sds_enrichment_status(record) == "parsed_with_actionable_flags"
    ]
    parsed_without_actionable = [
        (path, rid)
        for path, record, rid in records_with_local_pdf
        if _sds_enrichment_status(record) == "parsed_no_actionable_flags"
    ]
    parse_failed = [
        (path, rid)
        for path, record, rid in records_with_local_pdf
        if _sds_enrichment_status(record) == "parse_failed"
    ]
    missing_parse_marker = [
        (path, rid)
        for path, record, rid in records_with_local_pdf
        if _sds_enrichment_status(record) is None
    ]
    placeholder_flags = sum(
        1
        for _, record, _ in records
        for _, flag in _flag_objects(record)
        if _is_only_placeholder(flag)
    )

    print("=== enrichment coverage ===")
    print(f"records audited:              {len(records)}")
    print(f"local PDFs:                   {len(local_pdf_ids)}")
    print(f"audited records with PDF:      {len(records_with_local_pdf)}")
    print(f"records with actionable SDS flags: {len(records_with_sds_sources)}")
    print(f"SDS parsed with actionable flags:  {len(parsed_with_actionable)}")
    print(f"SDS parsed with no actionable flags: {len(parsed_without_actionable)}")
    print(f"SDS parse failed:             {len(parse_failed)}")
    print(f"records missing SDS parse marker: {len(missing_parse_marker)}")
    print(f"placeholder-only flag fields:  {placeholder_flags}")

    if require_sds_provenance and records_with_local_pdf and not records_with_sds_sources:
        failures.append(
            "no audited records with local PDFs contain sds_phrase, "
            "storage_class, or ghs_hcode sources"
        )

    no_actionable_sds_source = [
        rid
        for _, record, rid in records_with_local_pdf
        if not _has_source_type(record, SDS_SOURCE_TYPES)
    ]
    if no_actionable_sds_source:
        preview = ", ".join(sorted(no_actionable_sds_source)[:12])
        suffix = "" if len(no_actionable_sds_source) <= 12 else f", ... +{len(no_actionable_sds_source) - 12}"
        print(
            "records with parsed/no actionable SDS flag: "
            f"{len(no_actionable_sds_source)} ({preview}{suffix})"
        )

    return failures


def audit_publication() -> list[str]:
    failures: list[str] = []
    reagent_stems = _path_stems(REAGENTS_DIR)
    handling_stems = _path_stems(HANDLING_DIR)
    docs_reagent_stems = _path_stems(DOCS_REAGENTS_DIR)
    docs_profile_stems = _path_stems(DOCS_PROFILES_DIR)
    docs_handling_stems = _path_stems(DOCS_HANDLING_DIR)

    print("\n=== publication coverage ===")
    print(f"data/reagents:                {len(reagent_stems)}")
    print(f"data/handling:                {len(handling_stems)}")
    print(f"docs/data/reagents:           {len(docs_reagent_stems)}")
    print(f"docs/data/profiles:           {len(docs_profile_stems)}")
    print(f"docs/handling:                {len(docs_handling_stems)}")

    comparisons = [
        ("data/handling", reagent_stems, handling_stems),
        ("docs/data/reagents", reagent_stems, docs_reagent_stems),
        ("docs/data/profiles", handling_stems, docs_profile_stems),
        ("docs/handling", handling_stems, docs_handling_stems),
    ]
    for label, expected, observed in comparisons:
        missing = expected - observed
        extra = observed - expected
        if missing:
            failures.append(f"{label} missing {len(missing)} file(s): {sorted(missing)[:5]}")
        if extra:
            failures.append(f"{label} has {len(extra)} stale extra file(s): {sorted(extra)[:5]}")

    manifest = _read_list(DOCS_MANIFEST, failures)
    data_index = _read_list(DATA_INDEX, failures)
    docs_index = _read_list(DOCS_INDEX, failures)
    print(f"docs/data/manifest.json:      {len(manifest)}")
    print(f"data/index.json:              {len(data_index)}")
    print(f"docs/data/index.json:         {len(docs_index)}")

    for label, entries in [
        ("docs/data/manifest.json", manifest),
        ("data/index.json", data_index),
        ("docs/data/index.json", docs_index),
    ]:
        if entries and len(entries) != len(reagent_stems):
            failures.append(
                f"{label} has {len(entries)} entries, expected {len(reagent_stems)}"
            )

    return failures


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Audit enrichment and publication coverage.")
    parser.add_argument("json_paths", nargs="*", help="Optional reagent JSON files to audit.")
    parser.add_argument(
        "--require-sds-provenance",
        action="store_true",
        help="Fail if audited records with local PDFs have no SDS-derived provenance at all.",
    )
    parser.add_argument(
        "--publication",
        action="store_true",
        help="Also verify generated docs/index/profile files match canonical data counts.",
    )
    args = parser.parse_args(argv)

    failures = audit_enrichment(_json_files(args.json_paths), args.require_sds_provenance)
    if args.publication:
        failures.extend(audit_publication())

    if failures:
        print("\nFAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
