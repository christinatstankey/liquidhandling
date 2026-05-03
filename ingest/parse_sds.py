#!/usr/bin/env python3
"""
parse_sds.py  —  Sigma-Aldrich SDS PDF → reagent JSON

Extracts GHS hazard data, physical/chemical properties, storage,
PPE, and incompatibilities from a Sigma-Aldrich SDS PDF (16-section
GHS/OSHA template) and writes a schema-valid reagent JSON.

For reagents that already have a JSON in data/reagents/ (the 10 MVP
hand-authored records), SDS-grounded values overwrite the physical
property and GHS slots; bench_knowledge, category, striking_fact, and
tacit-knowledge properties are carried forward unchanged.

Usage:
    python ingest/parse_sds.py data/sds-pdfs/3483-12-3.pdf
    python ingest/parse_sds.py data/sds-pdfs/3483-12-3.pdf --out data/reagents/3483-12-3.json
    python ingest/parse_sds.py data/sds-pdfs/3483-12-3.pdf --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pdfplumber

REPO_ROOT = Path(__file__).parent.parent
REAGENTS_DIR = REPO_ROOT / "data" / "reagents"

# ── GHS H-code → pictogram (GHS Rev. 9 / OSHA HCS 2012) ───────────────────
# Used to infer pictograms from text-extracted H-codes, since
# Sigma embeds pictogram images (not text) in the PDF.
PICTOGRAM_FOR_HCODE: dict[str, str] = {
    **{h: "exploding_bomb"  for h in ["H200","H201","H202","H203","H204","H205","H240","H241"]},
    **{h: "flame"           for h in ["H224","H225","H226","H228","H241","H242","H252"]},
    **{h: "oxidizer"        for h in ["H270","H271","H272"]},
    **{h: "compressed_gas"  for h in ["H280","H281"]},
    **{h: "corrosion"       for h in ["H290","H314","H318"]},
    **{h: "skull_crossbones" for h in ["H300","H301","H310","H311","H330","H331"]},
    **{h: "exclamation_mark" for h in ["H302","H312","H315","H317","H319","H332","H335","H336"]},
    **{h: "health_hazard"   for h in ["H304","H334","H340","H341","H350","H351",
                                       "H360","H361","H370","H371","H372","H373"]},
    **{h: "environment"     for h in ["H400","H410","H411","H412","H413"]},
}

# Properties the parser can derive from SDS data (overwrites existing JSON).
SDS_GROUNDED = {
    "flash_point_C", "vapor_pressure_kPa_20C", "viscosity_mPas_20C",
    "storage_temp_C", "freeze_point_C",
    # Derived booleans we can compute reliably from the above + H-codes:
    "is_flammable", "is_corrosive", "is_volatile", "viscosity_class",
}

# All property keys in schema order (for stable output).
ALL_PROPERTY_KEYS = [
    "is_protein", "glycerol_pct", "viscosity_class", "viscosity_mPas_20C",
    "vapor_pressure_kPa_20C", "is_volatile", "is_flammable", "flash_point_C",
    "is_light_sensitive", "is_fluorophore", "excitation_nm", "emission_nm",
    "is_reducing_agent", "oxidizes_in_solution", "solution_half_life_at_4C_days",
    "make_fresh", "is_detergent", "is_foaming", "is_fixative", "fume_hood_required",
    "is_hygroscopic", "is_deliquescent", "is_corrosive", "is_adsorption_prone",
    "lo_bind_required", "freeze_point_C", "requires_ice", "freeze_thaw_max_cycles",
    "skin_penetrant", "plastic_incompatible", "storage_temp_C",
]


# ── PDF text extraction ─────────────────────────────────────────────────────

def extract_full_text(pdf_path: Path) -> str:
    """Return concatenated text from all pages."""
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def split_sections(text: str) -> dict[int, str]:
    """
    Split full SDS text into a dict keyed by section number.

    Requires uppercase SECTION at the start of a line followed by
    1–2 digits, a period, and an uppercase title character — this
    filters out inline cross-references like "section 2.2", "section 8.",
    and CFR citations like "Section 720.36".
    """
    # (?m) = MULTILINE so ^ matches start of each line
    pattern = re.compile(r"(?m)^SECTION\s+(\d{1,2})\.\s+[A-Z]")
    matches = list(pattern.finditer(text))
    sections: dict[int, str] = {}
    for i, m in enumerate(matches):
        num = int(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[num] = text[start:end]
    return sections


# ── Parsing helpers ─────────────────────────────────────────────────────────

def _first_match(pattern: str, text: str, flags: int = 0) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def _parse_temp_C(raw: str) -> float | None:
    """
    Parse a temperature string from Sigma SDSs to °C.

    Handles:
      "55 °F / 13 °C"          → 13.0
      "36 - 46 °F / 2 - 8 °C" → 5.0  (midpoint of range)
      "-20 °C"                  → -20.0
      "41 - 44 °C"             → 42.5
      "Not applicable"          → None
    """
    if not raw or re.search(r"not applicable|no data", raw, re.IGNORECASE):
        return None

    # "…/ lo - hi °C" — range after the Fahrenheit/slash
    m = re.search(r"/\s*([-\d.]+)\s*[-–]\s*([-\d.]+)\s*°C", raw)
    if m:
        return round((float(m.group(1)) + float(m.group(2))) / 2, 1)

    # "…/ value °C"
    m = re.search(r"/\s*([-\d.]+)\s*°C", raw)
    if m:
        return float(m.group(1))

    # Celsius only, range: "41 - 44 °C"
    m = re.search(r"([-\d.]+)\s*[-–]\s*([-\d.]+)\s*°C", raw)
    if m:
        return round((float(m.group(1)) + float(m.group(2))) / 2, 1)

    # Celsius only, single: "-20 °C"
    m = re.search(r"([-\d.]+)\s*°C", raw)
    if m:
        return float(m.group(1))

    return None


def _parse_vapor_pressure_kPa(raw: str) -> float | None:
    """
    Parse vapor pressure to kPa.

    Sigma reports in hPa; the parser converts to kPa (÷10) to match
    the field name vapor_pressure_kPa_20C and the pre_wet_volatile
    rule threshold of ≥5 kPa.

    "57.26 hPa (67.3 °F / 19.6 °C)" → 5.726
    "< 0.1 hPa (25 °C)"             → 0.01  (negligible, below threshold)
    "No data available"              → None
    """
    if not raw or re.search(r"no data", raw, re.IGNORECASE):
        return None

    m = re.search(r"<\s*([\d.]+)\s*hPa", raw, re.IGNORECASE)
    if m:
        return round(float(m.group(1)) / 10, 4)

    m = re.search(r"([\d.]+)\s*hPa", raw, re.IGNORECASE)
    if m:
        return round(float(m.group(1)) / 10, 4)

    m = re.search(r"([\d.]+)\s*kPa", raw, re.IGNORECASE)
    if m:
        return float(m.group(1))

    return None


def _parse_viscosity_mPas(raw: str) -> float | None:
    if not raw or re.search(r"no data", raw, re.IGNORECASE):
        return None
    # Match numbers with optional thousands comma: "1,412 mPa.s"
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*mPa\.?s", raw, re.IGNORECASE)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


# ── Section parsers ─────────────────────────────────────────────────────────

def parse_header(text: str) -> dict:
    """Extract revision date and version from the document header (pre-Section 1)."""
    # Header appears in the first few hundred characters before SECTION 1
    header = text[:600]
    return {
        "revision_date": _first_match(r"Revision Date\s+([\d/]+)", header),
        "version":       _first_match(r"Version\s+([\d.]+)", header),
    }


def parse_section1(sec: str) -> dict:
    return {
        "name":           _first_match(r"Product name\s*:\s*(.+)", sec),
        "cas":            _first_match(r"CAS-No\.\s*:\s*([\d-]+)", sec),
        "product_number": _first_match(r"Product Number\s*:\s*(\S+)", sec),
        "brand":          _first_match(r"Brand\s*:\s*(\S+)", sec),
    }


def parse_section2(sec: str) -> dict:
    """Extract GHS signal word and hazard statements."""
    # Non-hazardous products say "Not a hazardous substance" or "no signal word"
    if re.search(r"not a hazardous substance|no signal word", sec, re.IGNORECASE):
        return {"signal_word": None, "hazard_statements": []}

    signal_word = _first_match(r"Signal word\s*:\s*(Danger|Warning)", sec, re.IGNORECASE)

    # H-statements: look for lines like "H302 Harmful if swallowed."
    # The regex allows the description to span a few words before a period.
    raw_stmts = re.findall(r"\b(H\d{3}[A-Z+]*)\s+([A-Z][^\n]{5,80}\.?)", sec)
    hazard_statements = []
    seen = set()
    for code, desc in raw_stmts:
        clean = f"{code} {desc.strip().rstrip('.')}"
        if code not in seen:
            hazard_statements.append(clean)
            seen.add(code)

    return {
        "signal_word": signal_word,
        "hazard_statements": hazard_statements,
    }


def parse_section7(sec: str) -> dict:
    """Extract recommended storage temperature and storage conditions text."""
    # Sigma format: "Recommended storage : 2-8°C\ntemperature"
    # "temperature" is a continuation label on the next line, not part of the key.
    temp_raw = _first_match(
        r"Recommended storage\s*:\s*(.+?)(?:\n|$)", sec, re.IGNORECASE
    )
    storage_temp_C = _parse_temp_C(temp_raw) if temp_raw else None

    # Free-text storage conditions (tightly closed, dry, ventilated, etc.)
    cond = _first_match(
        r"Further information on\s+storage conditions\s*:\s*(.+?)(?:\nStorage class|\nRecommended|\Z)",
        sec, re.IGNORECASE | re.DOTALL,
    )
    storage_conditions = cond.replace("\n", " ").strip() if cond else None

    return {
        "storage_temp_C": storage_temp_C,
        "storage_conditions": storage_conditions,
    }


def parse_section8(sec: str) -> dict:
    """
    Extract PPE as a list of human-readable strings.

    The Sigma SDS Section 8 lists glove material, eye protection type,
    and whether respiratory protection is required.
    """
    ppe = []

    # Glove material — first Material: line in the hand-protection block
    glove_match = re.search(r"Hand protection.*?Material\s*:\s*(.+?)(?:\n|Break)", sec,
                             re.IGNORECASE | re.DOTALL)
    if glove_match:
        mat = glove_match.group(1).strip().lower()
        if "nitrile" in mat:
            ppe.append("nitrile gloves")
        elif "butyl" in mat:
            ppe.append("butyl rubber gloves")
        elif "neoprene" in mat:
            ppe.append("neoprene gloves")
        elif "latex" in mat:
            ppe.append("latex gloves")
        else:
            ppe.append("chemical-resistant gloves")
    else:
        ppe.append("gloves")

    # Eye protection — look for the specific type after "Eye protection :"
    eye_match = re.search(r"Eye protection\s*:\s*(.+?)(?=Skin and body|Hygiene|SECTION|\Z)",
                           sec, re.IGNORECASE | re.DOTALL)
    if eye_match:
        eye_text = eye_match.group(1).lower()
        if "goggle" in eye_text:
            ppe.append("safety goggles")
        elif "glasses" in eye_text or "spectacles" in eye_text:
            ppe.append("safety glasses")
        else:
            ppe.append("eye protection")
    else:
        ppe.append("eye protection")

    # Respiratory protection — required only if the SDS says so explicitly
    resp_match = re.search(
        r"Respiratory protection\s*:\s*(.+?)(?=Our recommendation|Engineering|Hand|$)",
        sec, re.IGNORECASE | re.DOTALL,
    )
    if resp_match:
        resp_text = resp_match.group(1).strip()
        if re.search(r"required|wear|necessary", resp_text, re.IGNORECASE):
            if not re.search(r"not required|no respiratory", resp_text, re.IGNORECASE):
                ppe.append("respiratory protection")

    return {"ppe": ppe}


def parse_section9(sec: str) -> dict:
    """Extract physical/chemical properties from Section 9."""
    result: dict = {}

    # Physical state from Appearance field
    appearance = _first_match(r"Appearance\s*:\s*(\w+)", sec)
    if appearance:
        appearance = appearance.lower()
        state_map = {
            "liquid": "liquid", "solution": "solution",
            "viscous": "liquid", "gel": "liquid", "oil": "liquid",
            "powder": "powder",
            "solid": "solid", "pellets": "solid", "flakes": "solid",
            "granules": "solid", "crystals": "solid", "crystalline": "solid",
        }
        result["physical_state"] = state_map.get(appearance)

    # Flash point
    fp_raw = _first_match(r"Flash point\s*:\s*(.+?)(?:\n|Method:)", sec)
    if fp_raw and not re.search(r"not applicable|no data", fp_raw, re.IGNORECASE):
        result["flash_point_C"] = _parse_temp_C(fp_raw)
    else:
        result["flash_point_C"] = None

    # Vapor pressure (in hPa on Sigma SDSs → convert to kPa)
    vp_raw = _first_match(r"Vapor pressure\s*:\s*(.+?)(?:\n|Method:)", sec)
    result["vapor_pressure_kPa_20C"] = _parse_vapor_pressure_kPa(vp_raw) if vp_raw else None

    # Melting point → freeze_point_C, but only for liquids/solutions.
    # For powders/solids the melting point is not a relevant liquid-handler
    # parameter and would incorrectly fire the freeze_point_C >= 15 rule.
    # When physical_state is unknown (None), default to not setting it —
    # the existing JSON's value will be preserved via the merge fallback.
    melt_raw = _first_match(r"Melting point/\s*range\s*:\s*(.+?)(?:\n|Method:)", sec)
    physical_state = result.get("physical_state")
    if melt_raw and not re.search(r"no data", melt_raw, re.IGNORECASE):
        if physical_state in ("liquid", "solution"):
            result["freeze_point_C"] = _parse_temp_C(melt_raw)
        else:
            result["freeze_point_C"] = None
    else:
        result["freeze_point_C"] = None

    # Dynamic viscosity
    visc_raw = _first_match(r"Viscosity,\s*dynamic\s*:\s*(.+?)(?:\n|Method:)", sec)
    result["viscosity_mPas_20C"] = _parse_viscosity_mPas(visc_raw) if visc_raw else None

    return result


def parse_section10(sec: str) -> dict:
    """Extract incompatible materials as a list of strings."""
    incompat_raw = _first_match(
        r"Incompatible materials\s*:\s*(.+?)(?:\nHazardous decomp|\nSECTION|\Z)",
        sec, re.IGNORECASE | re.DOTALL,
    )
    if not incompat_raw or re.search(r"no data|none", incompat_raw, re.IGNORECASE):
        return {"incompatibilities": []}

    # Each incompatible item is typically on its own line
    lines = [
        l.strip() for l in incompat_raw.split("\n")
        if l.strip() and not re.match(r"Sigma|Page \d|The life science", l)
    ]
    seen: set[str] = set()
    unique = [l for l in lines if len(l) > 2 and not (l in seen or seen.add(l))]
    return {"incompatibilities": unique}


# ── Inference from extracted data ───────────────────────────────────────────

def infer_pictograms(hazard_statements: list[str]) -> list[str]:
    """Map H-codes to GHS pictogram names (no duplicates, sorted)."""
    pictograms: set[str] = set()
    for stmt in hazard_statements:
        m = re.match(r"(H\d{3}[A-Z+]*)", stmt)
        if m and m.group(1) in PICTOGRAM_FOR_HCODE:
            pictograms.add(PICTOGRAM_FOR_HCODE[m.group(1)])
    return sorted(pictograms)


def derive_properties(sds_data: dict) -> dict:
    """
    Compute boolean properties that can be derived from SDS numeric/GHS data.
    Returns only the keys that are determinable; callers should check for None.
    """
    h_codes = set()
    for stmt in sds_data.get("hazard_statements", []):
        m = re.match(r"(H\d{3})", stmt)
        if m:
            h_codes.add(m.group(1))

    derived: dict = {}

    # is_flammable: flash point below 60°C (Class I/II/IIIA) or flammable H-codes
    flammable_codes = {"H224", "H225", "H226", "H228"}
    fp = sds_data.get("flash_point_C")
    if (fp is not None and fp < 60) or bool(h_codes & flammable_codes):
        derived["is_flammable"] = True
    elif h_codes or fp is not None:
        derived["is_flammable"] = False

    # is_corrosive: H314 (skin corrosion/burns) or H290 (corrosive to metals).
    # H318 (serious eye damage) is intentionally excluded — eye damage does not
    # imply bulk corrosive handling behaviour (same decision as enrich_sds_sources.py).
    if {"H290", "H314"} & h_codes:
        derived["is_corrosive"] = True
    elif h_codes:
        derived["is_corrosive"] = False

    # is_volatile: vapor pressure meaningfully above zero
    vp = sds_data.get("vapor_pressure_kPa_20C")
    if vp is not None:
        derived["is_volatile"] = vp > 0.1

    # viscosity_class: thresholds match lab intuition and the rules engine
    visc = sds_data.get("viscosity_mPas_20C")
    if visc is not None:
        if visc < 5:
            derived["viscosity_class"] = "low"
        elif visc <= 100:
            derived["viscosity_class"] = "moderate"
        else:
            derived["viscosity_class"] = "high"

    return derived


# ── Merge and output ────────────────────────────────────────────────────────

def _build_storage_text(sds_data: dict) -> str:
    """Generate a basic storage string for new reagents that have no existing JSON."""
    temp = sds_data.get("storage_temp_C")
    cond = sds_data.get("storage_conditions") or ""

    if temp is None:
        return f"Store per SDS. {cond}".strip()
    if temp <= -15:
        return f"Store at {int(temp)}°C (freezer). {cond}".strip()
    if temp <= 8:
        return f"Store at 2–8°C (refrigerator). {cond}".strip()
    return f"Store at approximately {int(temp)}°C. {cond}".strip()


def build_output(sds_data: dict, existing: dict | None) -> dict:
    """
    Merge SDS-extracted data with an existing reagent JSON (if any).

    SDS-grounded fields win for physical/GHS data.
    Tacit knowledge (bench_knowledge, category, striking_fact, and
    properties not derivable from SDS) is preserved from existing or
    left null for new reagents.
    """
    ex = existing or {}
    ex_props = ex.get("properties", {})

    derived = derive_properties(sds_data)

    # Build properties: SDS/derived take precedence for SDS_GROUNDED keys;
    # tacit keys fall back to existing JSON or null.
    properties: dict = {}
    for key in ALL_PROPERTY_KEYS:
        if key in SDS_GROUNDED:
            # Prefer directly extracted value, then derived boolean, then existing
            if sds_data.get(key) is not None:
                properties[key] = sds_data[key]
            elif derived.get(key) is not None:
                properties[key] = derived[key]
            else:
                properties[key] = ex_props.get(key)
        else:
            # Tacit knowledge: preserve existing; null for new reagents
            properties[key] = ex_props.get(key) if key in ex_props else ([] if key == "plastic_incompatible" else None)

    # GHS section is entirely SDS-grounded
    h_stmts = sds_data.get("hazard_statements", [])
    ghs = {
        "pictograms":        infer_pictograms(h_stmts),
        "signal_word":       sds_data.get("signal_word"),
        "hazard_statements": h_stmts,
    }

    # sds_facts.storage: preserve existing (richer) over SDS-derived text;
    # generate from SDS only for new reagents.
    existing_storage = ex.get("sds_facts", {}).get("storage", "")
    storage_text = existing_storage if existing_storage else _build_storage_text(sds_data)

    sds_facts = {
        "storage":           storage_text,
        "ppe":               sds_data.get("ppe") or ex.get("sds_facts", {}).get("ppe", []),
        "incompatibilities": sds_data.get("incompatibilities") or ex.get("sds_facts", {}).get("incompatibilities", []),
    }

    # vendor_example: prefer existing (has grade/catalog detail); build from SDS for new reagents
    brand = sds_data.get("brand", "")
    pn = sds_data.get("product_number", "")
    vendor_sds = f"{brand} {pn}".strip() if brand or pn else ""
    vendor_example = ex.get("vendor_example") or vendor_sds

    output = {
        # Prefer the curated name in an existing JSON (includes common name/abbreviation);
        # fall back to the SDS official name for new reagents.
        "name":           ex.get("name") or sds_data.get("name") or "",
        "cas":            sds_data.get("cas") or ex.get("cas"),
        "category":       ex.get("category") or None,
        "vendor_example": vendor_example,
        "physical_state": sds_data.get("physical_state") or ex.get("physical_state", "liquid"),
        "properties":     properties,
        "ghs":            ghs,
        "sds_facts":      sds_facts,
        "bench_knowledge": ex.get("bench_knowledge", []),
        "sds_source": {
            "revision_date":  sds_data.get("revision_date"),
            "version":        sds_data.get("version"),
            "product_number": sds_data.get("product_number"),
        },
    }

    return output


# ── Diff report ─────────────────────────────────────────────────────────────

def _report_diff(sds_data: dict, existing: dict) -> None:
    """
    Print a human-readable comparison of SDS-extracted values vs the
    existing JSON for the SDS-grounded fields.  Helps confirm the SDS
    parse is correct before overwriting.
    """
    ex_props = existing.get("properties", {})
    ex_ghs = existing.get("ghs", {})
    print("\n── SDS vs existing JSON ──────────────────────────────────────────")

    checks = [
        ("name",                   existing.get("name"),                     sds_data.get("name")),
        ("ghs.signal_word",        ex_ghs.get("signal_word"),                sds_data.get("signal_word")),
        ("flash_point_C",          ex_props.get("flash_point_C"),            sds_data.get("flash_point_C")),
        ("vapor_pressure_kPa_20C", ex_props.get("vapor_pressure_kPa_20C"),   sds_data.get("vapor_pressure_kPa_20C")),
        ("viscosity_mPas_20C",     ex_props.get("viscosity_mPas_20C"),       sds_data.get("viscosity_mPas_20C")),
        ("storage_temp_C",         ex_props.get("storage_temp_C"),           sds_data.get("storage_temp_C")),
        ("freeze_point_C",         ex_props.get("freeze_point_C"),           sds_data.get("freeze_point_C")),
        ("physical_state",         existing.get("physical_state"),           sds_data.get("physical_state")),
    ]
    for label, old, new in checks:
        if old != new:
            print(f"  CHANGED  {label}: {old!r}  →  {new!r}")
        else:
            print(f"  same     {label}: {old!r}")

    # H-statement diff
    old_h = set(existing.get("ghs", {}).get("hazard_statements", []))
    new_h = set(sds_data.get("hazard_statements", []))
    for s in sorted(new_h - old_h):
        print(f"  + H-stmt: {s}")
    for s in sorted(old_h - new_h):
        print(f"  - H-stmt: {s}")

    print("─────────────────────────────────────────────────────────────────\n")


# ── Main ────────────────────────────────────────────────────────────────────

def parse_pdf(pdf_path: Path) -> dict:
    """
    Full parse pipeline: PDF → flat sds_data dict with all extracted fields.
    Returns a flat dict (not yet the final reagent JSON structure).
    """
    text = extract_full_text(pdf_path)
    sections = split_sections(text)

    sds_data: dict = {}
    sds_data.update(parse_header(text))

    if 1 in sections:
        sds_data.update(parse_section1(sections[1]))
    if 2 in sections:
        sds_data.update(parse_section2(sections[2]))
    if 7 in sections:
        sds_data.update(parse_section7(sections[7]))
    if 8 in sections:
        sds_data.update(parse_section8(sections[8]))
    if 9 in sections:
        sds_data.update(parse_section9(sections[9]))
    if 10 in sections:
        sds_data.update(parse_section10(sections[10]))

    return sds_data


def main():
    parser = argparse.ArgumentParser(description="Parse a Sigma SDS PDF into a reagent JSON.")
    parser.add_argument("pdf_path", help="Path to the SDS PDF, e.g. data/sds-pdfs/3483-12-3.pdf")
    parser.add_argument("--out", help="Write output to this path (default: stdout)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and show diff vs existing JSON but do not write anything")
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    # Parse the PDF
    sds_data = parse_pdf(pdf_path)

    # CAS fallback: if the PDF text didn't contain a CAS, infer it from the
    # filename (pattern: <CAS>.pdf).  This handles SDSs where parse_pdf can't
    # locate the CAS-No. line (e.g., enzyme/protein SDSs, slug-named files).
    # Slug names like "polyclonal-igg" are kept as-is (no hyphenated CAS).
    if not sds_data.get("cas"):
        stem = pdf_path.stem
        # A valid CAS looks like digits-digits-digits (e.g. 9012-90-2)
        if re.match(r"^\d+(?:-\d+){1,2}$", stem):
            sds_data["cas"] = stem

    # Try to find an existing reagent JSON by CAS or by the PDF stem
    cas = sds_data.get("cas")
    existing = None
    existing_path = None

    if cas:
        candidate = REAGENTS_DIR / f"{cas}.json"
        if candidate.exists():
            with open(candidate) as f:
                existing = json.load(f)
            existing_path = candidate
    else:
        # No CAS (e.g., polyclonal-igg.pdf) — try matching by filename stem
        candidate = REAGENTS_DIR / f"{pdf_path.stem}.json"
        if candidate.exists():
            with open(candidate) as f:
                existing = json.load(f)
            existing_path = candidate

    if existing:
        print(f"Merging with existing JSON: {existing_path}")
        _report_diff(sds_data, existing)
    else:
        print(f"No existing JSON found — generating new record for CAS {cas or pdf_path.stem}")

    output = build_output(sds_data, existing)
    output_json = json.dumps(output, indent=2)

    if args.dry_run:
        print(output_json)
        print("\n[dry-run] Nothing written.")
        return

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output_json + "\n")
        print(f"Written to {out_path}")
    else:
        print(output_json)


if __name__ == "__main__":
    main()
