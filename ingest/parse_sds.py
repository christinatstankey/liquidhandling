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

    Accepts both the older period delimiter (SECTION 9. Physical...) and the
    newer colon delimiter (SECTION 9: Physical...) used in some Sigma templates.
    The [.:] alternation still filters inline cross-refs like "section 2.2"
    and CFR citations like "Section 720.36" because those are lowercase.
    """
    # (?m) = MULTILINE so ^ matches start of each line
    pattern = re.compile(r"(?m)^SECTION\s+(\d{1,2})[.:]\s+[A-Z]")
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

    # Older template: "Signal word : Danger"
    # Newer template: "Signal Word Danger" (label elements block, no colon)
    signal_word = _first_match(r"Signal [Ww]ord\s*:?\s*(Danger|Warning)", sec, re.IGNORECASE)

    # H-statements: two formats:
    #  Older: "H302 Harmful if swallowed."  (code then description)
    #  Newer: "Acute toxicity, Oral (Category 4), H302"  (description then code)
    # Collect all unique H-codes, preferring the labelled form with descriptions.
    seen: set[str] = set()
    hazard_statements: list[str] = []

    # Form 1: H-code followed by description on the same line (GHS label section)
    # Use [ \t]+ (not \s+) to prevent matching across line boundaries, which would
    # incorrectly join a classification-table code at end-of-line with the next line.
    for code, desc in re.findall(r"\b(H\d{3}[A-Z+]*)[ \t]+([A-Z][^\n]{5,80}\.?)", sec):
        if code not in seen:
            hazard_statements.append(f"{code} {desc.strip().rstrip('.')}")
            seen.add(code)

    # Form 2: description then H-code (classification table in newer template)
    # e.g. "Acute toxicity, Oral (Category 4), H302"
    for code in re.findall(r",\s*(H\d{3}[A-Z+]*)\s*(?:\n|,|$)", sec):
        if code not in seen:
            hazard_statements.append(code)
            seen.add(code)

    return {
        "signal_word": signal_word,
        "hazard_statements": hazard_statements,
    }


def parse_section7(sec: str) -> dict:
    """Extract recommended storage temperature and storage conditions text."""
    # Older template: "Recommended storage : 2-8°C\ntemperature"
    temp_raw = _first_match(
        r"Recommended storage\s*:\s*(.+?)(?:\n|$)", sec, re.IGNORECASE
    )
    # Newer template: "Recommended storage temperature\n{0–6 page-header lines}\n2 - 8 °C"
    # The temperature value is on its own line, potentially after page header/footer
    # boilerplate (Sigma/Page X/life science disclaimer) that pdfplumber injects.
    if not temp_raw:
        m = re.search(
            r"Recommended storage temperature\s*\n(?:[^\n]*\n){0,6}\s*([-\d\s]+°C)",
            sec, re.IGNORECASE,
        )
        if m:
            temp_raw = m.group(1)
    storage_temp_C = _parse_temp_C(temp_raw) if temp_raw else None

    # Helpers shared by all storage-condition parsing branches below.
    _BOILERPLATE = re.compile(
        r"^(?:Page \d+ of \d+|The life science business|operates as MilliporeSigma"
        r"|Sigma|SigmaAldrich|SIGALD|SIAL|ALDRICH)[^\n]*$",
        re.IGNORECASE,
    )
    _STOP = re.compile(r"^(?:Storage class|Recommended storage|7\.\d)", re.IGNORECASE)

    # Free-text storage conditions (tightly closed, dry, ventilated, etc.)
    # Older single-line format: "Further information on storage conditions : Keep tightly closed..."
    cond = _first_match(
        r"Further information on\s+storage conditions\s*:\s*(.+?)(?:\nStorage class|\nRecommended|\Z)",
        sec, re.IGNORECASE | re.DOTALL,
    )
    if not cond:
        # Two-column PDF layout splits the key across two lines:
        #   "Further information on : Tightly closed."
        #   "storage conditions Dry."
        # Capture the value from line 1 and the continuation from line 2.
        # Use [ \t]* (not \s*) after "storage conditions" to avoid consuming the
        # newline that separates it from the next section header (Storage class).
        # "Further information on : {value1}\nstorage {word} {value2}"
        # The continuation label can be "conditions", "stability", etc.
        m = re.search(
            r"Further information on\s*:\s*(.+?)[ \t]*\nstorage[ \t]+\w+[ \t]*(.*?)(?=\nStorage class|\nRecommended|\Z)",
            sec, re.IGNORECASE | re.DOTALL,
        )
        if m:
            part1 = m.group(1).strip()
            # group 2 may contain multiple continuation lines and page boilerplate;
            # filter out boilerplate and stop at section boundaries.
            raw2 = m.group(2)
            clean_parts = []
            for line in raw2.splitlines():
                line = line.strip()
                if not line:
                    continue
                if _BOILERPLATE.match(line) or _STOP.match(line):
                    break
                clean_parts.append(line)
            part2 = " ".join(clean_parts)
            cond = f"{part1} {part2}".strip() if part2 else part1
    if not cond:
        # Alternate period-format split: "Conditions for safe : {value}\nstorage"
        m = re.search(
            r"Conditions for safe\s*:\s*(.+?)[ \t]*\nstorage",
            sec, re.IGNORECASE,
        )
        if m:
            raw = m.group(1).strip()
            if not re.search(r"no data available", raw, re.IGNORECASE):
                cond = raw
    if not cond:
        # Newer colon-format template: conditions appear as free text directly
        # under a "Storage conditions" heading (no "Further information on" line).
        # Strategy: find the heading, then collect lines until a stop word,
        # skipping Sigma page-header boilerplate lines injected by pdfplumber.
        m = re.search(r"(?m)^Storage conditions[ \t]*$", sec, re.IGNORECASE)
        if m:
            lines = sec[m.end():].lstrip("\n").splitlines()
            collected = []
            for line in lines:
                if _STOP.match(line):
                    break
                if _BOILERPLATE.match(line):
                    continue
                if line.strip():
                    collected.append(line.strip())
            cond = " ".join(collected) or None
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


def _parse_section9_colon(sec: str) -> dict:
    """
    Parse Section 9 in the newer GHS lettered-list format used by some Sigma SDSs.

    Template uses a) through t) entries instead of "Key : value" pairs.
    Viscosity is kinematic (mm²/s); we convert to dynamic (mPa·s) using the
    density extracted from entry m) when available, otherwise use as-is
    (accurate enough for water-based and near-density-1 reagents).
    """
    result: dict = {}

    STATE_MAP = {
        "liquid": "liquid", "solution": "solution",
        "viscous": "liquid", "gel": "liquid", "oil": "liquid",
        "powder": "powder",
        "solid": "solid", "pellets": "solid", "flakes": "solid",
        "granules": "solid", "crystals": "solid", "crystalline": "solid",
    }

    # a) Appearance  Form: liquid / "clear, liquid" / "white crystalline powder" etc.
    # Grab the full Form: line, then search for a known state keyword within it.
    app_line = _first_match(r"a\)\s+Appearance\s+Form:\s*([^\n]+)", sec, re.IGNORECASE)
    if app_line:
        app_lower = app_line.lower()
        for keyword in STATE_MAP:
            if keyword in app_lower:
                result["physical_state"] = STATE_MAP[keyword]
                break

    # g) Flash point  99 °C (210 °F) — grab everything up to the next lettered entry
    fp_m = re.search(r"g\)\s+Flash point\s+(.*?)(?=\n[a-z]\)|\Z)", sec, re.DOTALL)
    if fp_m:
        fp_text = fp_m.group(1)
        if not re.search(r"not applicable|no data", fp_text, re.IGNORECASE):
            result["flash_point_C"] = _parse_temp_C(fp_text)
        else:
            result["flash_point_C"] = None
    else:
        result["flash_point_C"] = None

    # k) Vapor pressure  0.01 hPa at 20 °C — prefer the 20 °C datapoint
    vp_m = re.search(r"k\)\s+Vapor pressure\s+(.*?)(?=\n[a-z]\)|\Z)", sec, re.DOTALL)
    if vp_m:
        vp_text = vp_m.group(1)
        # Only use value explicitly noted at or near 20 °C (±5 °C).
        # Falling back to a VP at a higher temperature (e.g. 39.5 °C) would
        # inflate the value and misclassify low-volatility reagents as volatile.
        vp_20 = re.search(r"([\d.]+)\s*hPa\s+at\s+([\d.]+)\s*°C", vp_text, re.IGNORECASE)
        if vp_20 and abs(float(vp_20.group(2)) - 20) <= 5:
            result["vapor_pressure_kPa_20C"] = round(float(vp_20.group(1)) / 10, 4)
        else:
            result["vapor_pressure_kPa_20C"] = None
    else:
        result["vapor_pressure_kPa_20C"] = None

    # m) Density  1.25 g/cm3 — needed for kinematic → dynamic viscosity conversion
    dens_m = re.search(r"m\)\s+(?:Relative\s+)?[Dd]ensity\s+([\d.]+)\s*g/cm", sec)
    density_g_cm3 = float(dens_m.group(1)) if dens_m else None

    # e) Melting point — only record for liquids (same policy as period-format parser)
    physical_state = result.get("physical_state")
    melt_m = re.search(r"e\)\s+Melting.*?Melting point(?:/freezing point)?:\s*(.*?)(?=\n[a-z]\)|\Z)", sec, re.DOTALL)
    if melt_m and physical_state in ("liquid", "solution"):
        melt_text = melt_m.group(1)
        if not re.search(r"no data", melt_text, re.IGNORECASE):
            result["freeze_point_C"] = _parse_temp_C(melt_text)
        else:
            result["freeze_point_C"] = None
    else:
        result["freeze_point_C"] = None

    # r) Viscosity  88.9 mm2/s — kinematic; multiply by density to get mPa·s
    visc_m = re.search(r"r\)\s+Viscosity\s+(.*?)(?=\n[a-z]\)|\Z)", sec, re.DOTALL)
    if visc_m:
        visc_text = visc_m.group(1)
        kin_m = re.search(r"([\d,]+(?:\.\d+)?)\s*mm2/s", visc_text, re.IGNORECASE)
        if kin_m:
            kinematic = float(kin_m.group(1).replace(",", ""))
            if density_g_cm3:
                result["viscosity_mPas_20C"] = round(kinematic * density_g_cm3, 1)
            else:
                result["viscosity_mPas_20C"] = kinematic
        else:
            result["viscosity_mPas_20C"] = _parse_viscosity_mPas(visc_text)
    else:
        result["viscosity_mPas_20C"] = None

    return result


def parse_section9(sec: str) -> dict:
    """Extract physical/chemical properties from Section 9."""
    # Detect which template: older "Key : value" vs newer lettered "a) b) ..." list
    if re.search(r"\ba\)\s+Appearance\b", sec, re.IGNORECASE):
        return _parse_section9_colon(sec)

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

    # is_flammable: flash point below 60°C (Class I/II/IIIA) or flammable H-codes.
    # Absence of these data is not evidence that a reagent is non-flammable.
    flammable_codes = {"H224", "H225", "H226", "H228"}
    fp = sds_data.get("flash_point_C")
    if (fp is not None and fp < 60) or bool(h_codes & flammable_codes):
        derived["is_flammable"] = True

    # is_corrosive: H314 (skin corrosion/burns) or H290 (corrosive to metals).
    # H318 (serious eye damage) is intentionally excluded — eye damage does not
    # imply bulk corrosive handling behaviour (same decision as enrich_sds_sources.py).
    if {"H290", "H314"} & h_codes:
        derived["is_corrosive"] = True

    # is_volatile: vapor pressure meaningfully above zero. Low vapor pressure is
    # recorded numerically, but not promoted to a high-confidence false flag.
    vp = sds_data.get("vapor_pressure_kPa_20C")
    if vp is not None and vp > 0.1:
        derived["is_volatile"] = True

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


def derived_property_sources(sds_data: dict) -> dict[str, tuple[str, str]]:
    """Return provenance for positively derived sourced booleans."""
    h_codes = set()
    for stmt in sds_data.get("hazard_statements", []):
        m = re.match(r"(H\d{3})", stmt)
        if m:
            h_codes.add(m.group(1))

    sources: dict[str, tuple[str, str]] = {}

    flammable_codes = {"H224", "H225", "H226", "H228"}
    flammable_hits = sorted(h_codes & flammable_codes)
    if flammable_hits:
        sources["is_flammable"] = ("ghs_hcode", ",".join(flammable_hits))
    elif sds_data.get("flash_point_C") is not None and sds_data["flash_point_C"] < 60:
        sources["is_flammable"] = (
            "rule_derived",
            "rule_derived:flash_point_C→is_flammable",
        )

    corrosive_hits = sorted(h_codes & {"H290", "H314"})
    if corrosive_hits:
        sources["is_corrosive"] = ("ghs_hcode", ",".join(corrosive_hits))

    if sds_data.get("vapor_pressure_kPa_20C") is not None and sds_data["vapor_pressure_kPa_20C"] > 0.1:
        sources["is_volatile"] = (
            "rule_derived",
            "rule_derived:vapor_pressure_kPa_20C→is_volatile",
        )

    return sources


# ── Merge and output ────────────────────────────────────────────────────────

def _build_storage_text(sds_data: dict) -> str:
    """Generate a basic storage string for new reagents that have no existing JSON."""
    temp = sds_data.get("storage_temp_C")
    cond = sds_data.get("storage_conditions") or ""

    if temp is None:
        # Use conditions prose if available; fall back to generic placeholder.
        return cond if cond else "Store per SDS."
    if temp <= -15:
        return f"Store at {int(temp)}°C (freezer). {cond}".strip()
    if temp <= 8:
        return f"Store at 2–8°C (refrigerator). {cond}".strip()
    return f"Store at approximately {int(temp)}°C. {cond}".strip()


def _wrap_sourced(
    value,
    source_ref: str,
    confidence: str = "high",
    source_type: str = "ghs_hcode",
) -> dict:
    """Wrap a derived boolean in a sourced_boolean envelope for schema v2.0 records."""
    return {
        "value":      value,
        "confidence": confidence,
        "sources":    [{"type": source_type, "ref": source_ref, "agrees": True}],
    }


def _not_assessed_sourced(value=None) -> dict:
    """Return the standard placeholder used until a later enrichment pass adds evidence."""
    return {
        "value": value,
        "confidence": "low",
        "sources": [
            {"type": "claude_inference", "ref": "not_yet_assessed", "agrees": False}
        ],
    }


_STALE_DERIVED_SOURCE_REFS = {
    "is_flammable": {"flash_point_and_H22x"},
    "is_corrosive": {"H290_H314"},
    "is_volatile": {"vapor_pressure_kPa_20C"},
}


def _is_stale_parser_source(key: str, raw) -> bool:
    """
    Detect provenance emitted by older parse_sds versions that wrapped derived
    false values as if a high-confidence positive SDS source supported them.
    """
    if not isinstance(raw, dict):
        return False
    stale_refs = _STALE_DERIVED_SOURCE_REFS.get(key, set())
    return any(
        s.get("type") == "ghs_hcode" and s.get("ref") in stale_refs
        for s in raw.get("sources", [])
    )


def build_output(sds_data: dict, existing: dict | None) -> dict:
    """
    Merge SDS-extracted data with an existing reagent JSON (if any).

    SDS-grounded fields win for physical/GHS data.
    Tacit knowledge (bench_knowledge, category, striking_fact, and
    properties not derivable from SDS) is preserved from existing or
    left null for new reagents.

    When the existing record is schema v2.0, derived booleans are wrapped in
    sourced_boolean envelopes so the output stays schema-valid.
    """
    ex = existing or {}
    ex_props = ex.get("properties", {})
    # Detect v2.0 by schema_version OR by inspecting whether any existing property
    # already uses the sourced_boolean envelope — handles the case where a prior
    # parse pass wrote "1.0" even though the properties were v2.0 shaped.
    is_v2 = (
        ex.get("schema_version") == "2.0"
        or any(isinstance(v, dict) and "sources" in v for v in ex_props.values())
    )

    derived = derive_properties(sds_data)
    derived_sources = derived_property_sources(sds_data)

    # Build properties: SDS/derived take precedence for SDS_GROUNDED keys;
    # tacit keys fall back to existing JSON or null.
    properties: dict = {}
    for key in ALL_PROPERTY_KEYS:
        if key in SDS_GROUNDED:
            # Prefer directly extracted value, then derived boolean, then existing
            source_info = None
            if sds_data.get(key) is not None:
                raw = sds_data[key]
            elif derived.get(key) is not None:
                raw = derived[key]
                source_info = derived_sources.get(key)
            else:
                raw = ex_props.get(key)
            # Wrap only booleans newly derived from positive SDS evidence. Existing
            # sourced flags are preserved, and absence of a hazard code is not
            # converted into high-confidence negative provenance.
            if isinstance(raw, bool) and source_info:
                source_type, source_ref = source_info
                confidence = "low" if source_type == "rule_derived" else "high"
                properties[key] = _wrap_sourced(
                    raw, source_ref, confidence=confidence, source_type=source_type
                )
            elif _is_stale_parser_source(key, raw):
                properties[key] = _not_assessed_sourced()
            else:
                properties[key] = raw
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

    # sds_facts.storage: always re-derive from SDS data so stale or incorrect
    # extractions from prior parse runs are overwritten on each re-parse.
    storage_text = _build_storage_text(sds_data)

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
        "schema_version": "2.0" if is_v2 else "1.0",
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
