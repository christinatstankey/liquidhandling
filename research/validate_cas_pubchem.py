"""
validate_cas_pubchem.py

Validate every CAS# in candidate-reagents.csv against PubChem PUG-REST.

Why this matters
----------------
The Sigma `expansion` rows (sigma_verification = "mostly_llm_memory") in
candidate-reagents.csv have CAS#s that came from an LLM agent's memory,
not from direct verification against Sigma's site. For canonical
chemicals (the 20 amino acids, classical drugs, common nucleotides) the
memorized CAS#s are nearly always correct, but for the long-tail
"drug_tool" category they should not be trusted blindly.

This script does the cheapest possible end-to-end validation:

1. For each CAS, hit
   `https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/xref/RN/{CAS}/cids/JSON`
   and check whether PubChem resolves it to one or more CIDs.
2. For each resolved CID, fetch the canonical IUPAC name and check
   whether it shares enough tokens with the harvested name to count as
   a reasonable match.
3. Emit a validated CSV with three new columns:
       pubchem_cid       comma-separated list of CIDs (or "" on failure)
       pubchem_status    "ok" | "no_cid" | "name_mismatch" | "http_error"
       pubchem_iupac     PubChem's IUPAC name for the first CID (audit)

Output: `candidate-reagents-validated.csv` next to this script.

Run from a machine with network access to NCBI (PubChem is free, no API
key needed). Rate limits are 5 req/sec and 400 req/min; this script
sleeps 250 ms between calls and is safe to leave unattended.

Dependencies: requests, pandas. Both are in the project's
requirements.txt.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
INPUT_CSV = HERE / "candidate-reagents.csv"
OUTPUT_CSV = HERE / "candidate-reagents-validated.csv"

# PubChem PUG-REST base URL
PUG = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"

# Be polite: 4 req/sec leaves headroom under the 5/sec limit.
SLEEP_BETWEEN_CALLS = 0.25
HTTP_TIMEOUT = 10


def cas_to_cids(cas: str) -> tuple[list[int], str]:
    """
    Look up a CAS# in PubChem. Returns (list of CIDs, status string).
    Status is "ok" if 1+ CIDs resolved, "no_cid" if PubChem returns 404
    or empty IdentifierList, "http_error" on any other network failure.
    """
    url = f"{PUG}/compound/xref/RN/{quote(cas)}/cids/JSON"
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code == 404:
            return [], "no_cid"
        if r.status_code != 200:
            return [], "http_error"
        data = r.json()
        cids = data.get("IdentifierList", {}).get("CID", [])
        if not cids:
            return [], "no_cid"
        return cids, "ok"
    except (requests.RequestException, json.JSONDecodeError):
        return [], "http_error"


def cid_to_iupac(cid: int) -> str:
    """Fetch the IUPAC name for a CID. Returns empty string on failure."""
    url = f"{PUG}/compound/cid/{cid}/property/IUPACName/JSON"
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            return ""
        props = r.json().get("PropertyTable", {}).get("Properties", [])
        if not props:
            return ""
        return props[0].get("IUPACName", "")
    except (requests.RequestException, json.JSONDecodeError):
        return ""


def names_roughly_match(harvested: str, iupac: str) -> bool:
    """
    Loose check: do the harvested name and PubChem IUPAC share at least
    one substantive token? PubChem's IUPAC names are often very
    different from common names (e.g., "DMSO" vs.
    "methylsulfinylmethane"), so this is a coarse heuristic — false
    negatives are expected and the column is meant for human review,
    not automated filtering.
    """
    def _tokens(s: str) -> set[str]:
        s = s.lower()
        # Strip punctuation, split on whitespace, drop short tokens.
        out = set()
        cur = []
        for ch in s:
            if ch.isalnum():
                cur.append(ch)
            else:
                if cur:
                    out.add("".join(cur))
                    cur = []
        if cur:
            out.add("".join(cur))
        return {t for t in out if len(t) >= 4}

    a = _tokens(harvested)
    b = _tokens(iupac)
    return bool(a & b)


def main() -> int:
    if not INPUT_CSV.exists():
        print(f"ERROR: missing {INPUT_CSV}", file=sys.stderr)
        return 1

    df = pd.read_csv(INPUT_CSV)
    print(f"Loaded {len(df)} rows from {INPUT_CSV.name}")

    # Only validate rows that have a CAS#. Biologics with cas_number=""
    # get pubchem_status = "no_cas".
    has_cas = df["cas_number"].fillna("").astype(str).str.strip() != ""
    print(f"  {has_cas.sum()} rows have a CAS# to validate")
    print(f"  {(~has_cas).sum()} rows have no CAS (skipped)")

    cids_col = []
    status_col = []
    iupac_col = []

    for i, row in df.iterrows():
        cas = str(row["cas_number"]).strip()
        if not cas:
            cids_col.append("")
            status_col.append("no_cas")
            iupac_col.append("")
            continue

        cids, status = cas_to_cids(cas)
        time.sleep(SLEEP_BETWEEN_CALLS)

        iupac = ""
        if status == "ok":
            iupac = cid_to_iupac(cids[0])
            time.sleep(SLEEP_BETWEEN_CALLS)
            if iupac and not names_roughly_match(str(row["name"]), iupac):
                status = "name_mismatch"

        cids_col.append(",".join(str(c) for c in cids))
        status_col.append(status)
        iupac_col.append(iupac)

        if (i + 1) % 50 == 0:
            print(f"  ...{i+1}/{len(df)} validated")

    df["pubchem_cid"] = cids_col
    df["pubchem_status"] = status_col
    df["pubchem_iupac"] = iupac_col

    df.to_csv(OUTPUT_CSV, index=False)

    # Summary
    print()
    print("Validation complete:")
    for status, n in df["pubchem_status"].value_counts().items():
        print(f"  {status}: {n}")
    print(f"Wrote {OUTPUT_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
