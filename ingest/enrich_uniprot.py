#!/usr/bin/env python3
"""
enrich_uniprot.py — add UniProt protein-database sources to biologic slug JSONs.

For each biologic slug (cas: null) that isn't already NEB-enriched, queries the
UniProt REST API (https://rest.uniprot.org/) for a reviewed Swiss-Prot entry.

On a confident hit, adds 'uniprot' sources (high confidence) to:
  is_protein       True   — confirms the reagent is a protein
  lo_bind_required True   — all proteins adsorb to standard tubes at low conc.

Source type: 'uniprot' → high confidence (peer-reviewed Swiss-Prot entries).
Cache: data/uniprot-cache.json, keyed by slug — avoids re-querying across runs.

Search strategy per slug (tried in order, stop on first Swiss-Prot hit):
  1. Exact protein name search (good for enzyme names, cytokine full names)
  2. Gene symbol — cleaned name uppercased, stripped of spaces (good for
     "Bdnf", "Cxcl12", "G-Csf") with organism:human first, then any organism
  3. First 3 words of the name (partial match fallback)

Usage:
    python ingest/enrich_uniprot.py --all              # enrich all eligible slugs
    python ingest/enrich_uniprot.py --all --dry-run    # show changes without writing
    python ingest/enrich_uniprot.py --all --limit 10   # test on first 10
    python ingest/enrich_uniprot.py data/reagents/bdnf.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

import requests

REPO_ROOT    = Path(__file__).parent.parent
REAGENTS_DIR = REPO_ROOT / "data" / "reagents"
CACHE_PATH   = REPO_ROOT / "data" / "uniprot-cache.json"

UNIPROT_URL  = "https://rest.uniprot.org/uniprotkb/search"
FETCH_DELAY  = 0.5   # seconds between API calls (UniProt allows ~10 req/s)
HEADERS      = {"Accept": "application/json"}

# ── Source helpers (mirrors enrich_chebi.py) ─────────────────────────────────

SOURCE_TIER = {
    "sds_phrase": "high", "storage_class": "high", "ghs_hcode": "high",
    "pubchem": "high", "chebi": "high", "uniprot": "high",
    "rule_derived": "inherit",
    "manufacturer_protocol": "medium",
    "tacit_knowledge": "low", "claude_inference": "low",
}


def _compute_confidence(sources: list[dict]) -> str:
    tiers = []
    for s in sources:
        tier = SOURCE_TIER.get(s.get("type", ""), "low")
        if tier != "inherit":
            tiers.append((tier, s.get("agrees", True)))
    high_agrees    = any(t == "high"   and a  for t, a in tiers)
    high_disagrees = any(t == "high"   and not a for t, a in tiers)
    med_agrees     = any(t == "medium" and a  for t, a in tiers)
    if high_agrees and not high_disagrees:
        return "high"
    if (med_agrees and not high_disagrees) or (high_agrees and high_disagrees):
        return "medium"
    return "low"


def _ensure_sourced(raw) -> dict:
    if isinstance(raw, dict) and "sources" in raw:
        return raw
    return {"value": raw, "confidence": "low",
            "sources": [{"type": "claude_inference",
                          "ref": "not_yet_assessed", "agrees": False}]}


def _add_source(flag_obj: dict, src_type: str, ref: str,
                agrees: bool, value=None) -> bool:
    sources = flag_obj.setdefault("sources", [])
    if any(s.get("type") == src_type and s.get("ref") == ref for s in sources):
        return False
    sources.append({"type": src_type, "ref": ref, "agrees": agrees})
    flag_obj["confidence"] = _compute_confidence(sources)
    if value is not None and flag_obj.get("value") is None:
        flag_obj["value"] = value
    return True


# ── Name cleaning ─────────────────────────────────────────────────────────────

# Vendor/species/grade adjectives that UniProt doesn't use in protein names
_STRIP = re.compile(
    r'\b(recombinant|human|murine|mouse|rat|bovine|porcine|rabbit|goat|sheep|'
    r'zebrafish|drosophila|xenopus|yeast|bacterial|e\.?\s*coli|'
    r'his-?tagged?|his\d*|gst-?fusion|gst|flag-?tagged?|'
    r'active|carrier-?free|lyophilized?|biotinylated?|ultrapure|'
    r'certified|dialyzed?|heat-?inactivated?|charcoal-?stripped?|'
    r'ultra-?low|low-?endotoxin|cell-?culture-?grade|'
    r'(r&d\s*systems?|peprotech|gibco|thermo|sigma|neb|abcam|cst|'
    r'invitrogen|millipore|calbiochem|tocris|stemcell)\s*|'
    r'cat#?|≥\d+%)\b',
    re.IGNORECASE,
)


# Greek letters used in cytokine/growth-factor names → ASCII equivalents
_GREEK = {
    'α': 'A', 'Α': 'A', 'β': 'B', 'Β': 'B', 'γ': 'G', 'Γ': 'G',
    'δ': 'D', 'Δ': 'D', 'ε': 'E', 'Ε': 'E', 'κ': 'K', 'Κ': 'K',
    'λ': 'L', 'Λ': 'L', 'μ': 'M', 'Μ': 'M', 'ω': 'W', 'Ω': 'W',
}
_GREEK_RE = re.compile('[' + ''.join(_GREEK.keys()) + ']')


def _clean_name(name: str) -> str:
    """Strip vendor/species prefixes, normalise Greek letters, and collapse whitespace."""
    c = _GREEK_RE.sub(lambda m: _GREEK[m.group()], name)
    c = _STRIP.sub(" ", c)
    c = re.sub(r'\s+', ' ', c).strip()
    return c


def _gene_symbol(name: str) -> str:
    """
    Convert a cleaned name to an uppercase no-space gene symbol candidate.
    "G-Csf" → "G-CSF"  (preserve hyphens — they're part of gene symbols)
    "Cxcl12" → "CXCL12"
    "Fgf-2" → "FGF-2"
    """
    # Uppercase the whole thing; gene symbols are uppercase by convention
    return name.strip().upper()


def _queries(raw_name: str) -> list[tuple[str, str]]:
    """
    Return a prioritised list of (label, query_string) to try against UniProt.
    We stop at the first one that returns a reviewed (Swiss-Prot) hit.
    """
    clean = _clean_name(raw_name)
    gene  = _gene_symbol(clean)
    words = clean.split()
    qs: list[tuple[str, str]] = []
    # NOTE: reviewed:true must come AFTER the primary search term in UniProt queries;
    # placing it first causes HTTP 400 errors for field-based queries.
    gene_nospace  = re.sub(r'[\s]+', '', gene)
    gene_nohyphen = re.sub(r'[-\s]+', '', gene)
    is_short_gene = 2 <= len(gene_nospace) <= 12

    if is_short_gene:
        # Short gene symbols (BDNF, CXCL12, G-CSF): prefer human gene query first
        # to avoid landing on obscure organisms that match the same gene name.
        qs.append(("gene_human",    f'gene:{gene_nospace} AND reviewed:true AND organism_id:9606'))
        qs.append(("name_exact",    f'protein_name:"{clean}" AND reviewed:true AND organism_id:9606'))
        qs.append(("gene_any",      f'gene:{gene_nospace} AND reviewed:true'))
        if gene_nohyphen != gene_nospace and len(gene_nohyphen) <= 10:
            qs.append(("gene_nohyphen", f'gene:{gene_nohyphen} AND reviewed:true AND organism_id:9606'))
    else:
        # Long names (enzymes, full protein names): exact protein name first
        qs.append(("name_exact",    f'protein_name:"{clean}" AND reviewed:true'))
        if len(words) >= 3:
            partial = " ".join(words[:3])
            qs.append(("name_partial", f'protein_name:"{partial}" AND reviewed:true'))

    return qs


# ── UniProt query ─────────────────────────────────────────────────────────────

def _query_uniprot(query: str) -> Optional[dict]:
    """
    Run one UniProt search; return the first reviewed hit as a dict, or None.
    Result dict has keys: accession, protein_name, organism, keywords.
    """
    # Note: omit 'fields' — the UniProt v2 API rejects many field names with 400.
    # Fetching the full entry is fine for size=1 lookups.
    params = {
        "query":  query,
        "format": "json",
        "size":   1,
    }
    try:
        r = requests.get(UNIPROT_URL, params=params, headers=HEADERS, timeout=12)
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception:
        return None

    if not results:
        return None

    hit = results[0]
    # Only accept reviewed Swiss-Prot entries — TrEMBL hits are too noisy
    if hit.get("entryType") != "UniProtKB reviewed (Swiss-Prot)":
        return None

    acc      = hit.get("primaryAccession", "")
    org_name = hit.get("organism", {}).get("scientificName", "")
    rec_name = (hit.get("proteinDescription", {})
                   .get("recommendedName", {})
                   .get("fullName", {})
                   .get("value", ""))
    kws      = [k.get("name", "") for k in hit.get("keywords", [])]

    return {"accession": acc, "protein_name": rec_name,
            "organism": org_name, "keywords": kws}


def _lookup(slug: str, raw_name: str, cache: dict) -> Optional[dict]:
    """Return cached result or query UniProt; updates cache in place."""
    if slug in cache:
        return cache[slug]  # None means previously searched and missed

    queries = _queries(raw_name)
    result  = None
    for _label, q in queries:
        hit = _query_uniprot(q)
        time.sleep(FETCH_DELAY)
        if hit:
            result = hit
            break

    cache[slug] = result
    return result


# ── Apply enrichment ──────────────────────────────────────────────────────────

def enrich(reagent: dict, hit: dict) -> tuple[dict, list[str]]:
    """Add UniProt sources to is_protein and lo_bind_required."""
    props   = reagent.setdefault("properties", {})
    changes: list[str] = []
    acc     = hit["accession"]
    org     = hit.get("organism", "")
    ref     = f"uniprot:{acc}"

    # is_protein → True
    ip = _ensure_sourced(props.get("is_protein"))
    props["is_protein"] = ip
    if _add_source(ip, "uniprot", ref, agrees=True, value=True):
        changes.append(f"is_protein ← True ({acc} {org[:30]})")

    # lo_bind_required → True (proteins adsorb to standard labware at working concs)
    lb = _ensure_sourced(props.get("lo_bind_required"))
    props["lo_bind_required"] = lb
    if _add_source(lb, "uniprot", ref, agrees=True, value=True):
        changes.append(f"lo_bind_required ← True ({acc})")

    return reagent, changes


# ── Main ─────────────────────────────────────────────────────────────────────

def _is_neb_enriched(reagent: dict) -> bool:
    props = reagent.get("properties", {})
    return any(
        any(s.get("type") == "manufacturer_protocol" for s in v.get("sources", []))
        for v in props.values()
        if isinstance(v, dict) and "sources" in v
    )


def _iter_targets():
    """Yield (json_path, reagent) for all unenriched biologic slugs."""
    for f in sorted(REAGENTS_DIR.glob("*.json")):
        if re.match(r"^\d+(?:-\d+){1,2}\.json$", f.name):
            continue  # CAS-keyed small molecules
        reagent = json.loads(f.read_text())
        if reagent.get("cas") is not None:
            continue  # has a CAS — handled by SDS pipeline
        if _is_neb_enriched(reagent):
            continue  # already NEB-enriched, skip
        yield f, reagent


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("files",      nargs="*", type=Path)
    ap.add_argument("--all",      action="store_true")
    ap.add_argument("--dry-run",  action="store_true")
    ap.add_argument("--limit",    type=int, default=None,
                    help="Process at most N slugs (for testing).")
    ap.add_argument("--no-fetch", action="store_true",
                    help="Use cache only; skip HTTP requests.")
    args = ap.parse_args(argv)

    cache = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}

    if args.all:
        targets = list(_iter_targets())
    elif args.files:
        targets = [(Path(f), json.loads(Path(f).read_text())) for f in args.files]
    else:
        ap.print_help()
        return 1

    if args.limit:
        targets = targets[:args.limit]

    print(f"Targets: {len(targets)} biologic slugs")
    hits = misses = total_changes = 0

    for json_path, reagent in targets:
        name = reagent.get("name", json_path.stem)

        if args.no_fetch and json_path.stem not in cache:
            print(f"  SKIP (no-fetch, not cached): {json_path.name}")
            continue

        hit = _lookup(json_path.stem, name, cache)

        if not hit:
            misses += 1
            print(f"  MISS  {json_path.name}  ({name})")
            continue

        hits += 1
        reagent, changes = enrich(reagent, hit)

        if changes:
            total_changes += len(changes)
            print(f"  HIT   {json_path.name}  {hit['accession']}  [{len(changes)} changes]")
            for c in changes:
                print(f"    + {c}")
            if not args.dry_run:
                json_path.write_text(json.dumps(reagent, indent=2) + "\n")
        else:
            print(f"  HIT   {json_path.name}  {hit['accession']}  [no new changes]")

        # Save cache periodically so progress survives interruption
        if (hits + misses) % 25 == 0:
            CACHE_PATH.write_text(json.dumps(cache, indent=2))

    CACHE_PATH.write_text(json.dumps(cache, indent=2))

    mode = "[dry-run] " if args.dry_run else ""
    print(f"\n{mode}Done. {hits} hits / {misses} misses  |  {total_changes} field updates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
