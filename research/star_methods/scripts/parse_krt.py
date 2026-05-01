"""
parse_krt.py
============

Phase 2 of the STAR Methods pipeline: parse cached JATS XML files into a
tidy CSV of reagent rows from Key Resources Tables.

Inputs
------
- manifest.csv           : produced by pull_corpus.py — lists which
                           PMCIDs are in scope for THIS run, with
                           has_xml/fetch_ok columns. THIS IS THE SOURCE
                           OF TRUTH for which papers to parse.
- xml_cache/<PMCID>.xml  : full-text JATS XML files from Europe PMC.
                           The cache is intentionally incremental, so
                           it can contain XMLs from prior runs that
                           are not in the current manifest.

The manifest, not the cache, drives iteration. This means re-running
pull_corpus.py with different filters (different journal, year range,
limit) gives parse_krt.py a different working set, and stale XMLs from
previous runs are silently ignored — UNLESS you pass
`--include-cache-orphans`, which adds them to the iteration tagged with
journal "(cache-orphan)" for audit purposes.

Output
------
- parsed/krt_rows.csv : one row per reagent extracted from a KRT.

Output schema
-------------
  pmcid              source paper
  journal            from manifest.csv
  year               from manifest.csv
  category           KRT section (canonical; e.g.,
                     "Chemicals, Peptides, and Recombinant Proteins").
                     Sourced from a colspan'd header row OR from a
                     dedicated category column if the table has one.
  reagent_name       REAGENT or RESOURCE column
  source             SOURCE column (vendor/lab)
  identifier         IDENTIFIER column (Cat#/RRID)
  additional_info    ADDITIONAL INFORMATION column from 4-col KRTs.
                     Also captures any extra cells beyond the mapped
                     layout, joined with " | ", so that data isn't
                     silently dropped.
  parser_warnings    semicolon-joined list of soft warnings; rows are
                     kept either way so we can audit them later

Column-mapping strategy
-----------------------
Cell Press STAR Methods tables come in two main shapes — a 3-column
form (REAGENT or RESOURCE | SOURCE | IDENTIFIER) and a 4-column form
that adds ADDITIONAL INFORMATION — and a small fraction of papers use
non-standard variants such as a leading "Category" column or rowspan'd
category cells. To handle all of these without column shift, the
parser:

  1. Reads `<thead>` to classify each column by its label
     (`reagent`/`source`/`identifier`/`additional`/`category`) and
     records the cell index for each role. If `<thead>` is missing or
     unrecognized, falls back to the standard 0/1/2 positional layout.
  2. Expands rowspan/colspan attributes in the body so a row that
     "lost" cells to a prior row's rowspan is re-materialized with the
     same values at the right positions.
  3. Assigns fields by mapped index, not by hard-coded position. A
     category-as-column table thus sees `cells[0]=category`,
     `cells[1]=reagent`, etc., and the chemicals filter still picks
     the right rows.
  4. Captures any cells past the mapped layout into `additional_info`
     and adds a `captured_N_extra_cells` warning, so a 4th column
     in an undeclared 4-col table isn't silently dropped.

KRT detection heuristic
-----------------------
1. Walk every `<table-wrap>` in the article body.
2. Concatenate the caption text (across `<title>`, `<p>`, etc.) and
   lowercase it. If it contains the substring "key resource(s) table",
   treat it as a KRT.
3. Within the KRT, walk `<tbody>` rows. A row is treated as a
   *category header* (and updates the active category) only when:
     a. cells[1:] are all empty/whitespace, AND
     b. cell 0's normalized text matches one of the category patterns.
   This guards against data rows that happen to contain a category
   word being mis-classified as a header.
4. All other rows are emitted as data rows tagged with the active
   category. Rows are kept liberally; anomalies go in parser_warnings.

Category matching is intentionally permissive: STAR Methods predates
rigid schema enforcement, so "Chemicals, Peptides & Recombinant
Proteins" (ampersand), "Chemicals, Peptides and Recombinant Proteins"
(no Oxford comma), "CHEMICALS, PEPTIDES, AND RECOMBINANT PROTEINS"
(uppercase), and split variants like "Recombinant Proteins" alone all
need to map to the same canonical category.

Usage
-----
  # Parse the current manifest's papers, debug-dump every KRT it finds.
  python parse_krt.py --debug

  # Parse the first 10 manifest entries (validation runs).
  python parse_krt.py --debug --limit 10

  # Phase 2 scope — chemicals only.
  python parse_krt.py \\
      --category-filter "Chemicals, Peptides, and Recombinant Proteins"

  # Audit mode: also process XMLs in cache that aren't in the manifest.
  python parse_krt.py --include-cache-orphans

Dependencies
------------
- lxml      (fast, robust JATS parser; handles the namespace soup)
- pandas    (not strictly needed here; kept consistent with pipeline)

Pinned in requirements.txt.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

from lxml import etree


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
XML_CACHE = ROOT / "xml_cache"
MANIFEST_CSV = ROOT / "manifest.csv"
OUT_DIR = ROOT / "parsed"
OUT_CSV = OUT_DIR / "krt_rows.csv"


KRT_CAPTION_RE = re.compile(r"key\s+resources?\s+table", re.IGNORECASE)


# ----------------------------------------------------------------------
# Category detection patterns
# ----------------------------------------------------------------------
#
# KRT_CATEGORY_PATTERNS is a list of (canonical_name, [anchor_set, ...])
# tuples. A normalized cell-0 string matches a category if there exists
# an anchor set in which every anchor appears as a substring of the
# normalized text. The first matching category in list order wins, so
# more-specific patterns must come BEFORE more-general ones.
#
# Normalization (in `_normalize_for_match`):
#   - lowercase
#   - replace `,` `/` `&` `-` with single spaces
#   - replace " and " with single space
#   - collapse whitespace
#   - strip trailing colon
#
# After normalization, "Chemicals, Peptides, & Recombinant Proteins"
# becomes "chemicals peptides recombinant proteins" — substring matching
# on stems like "chemical" / "peptide" / "recombinant" / "protein"
# survives the punctuation drift.

KRT_CATEGORY_PATTERNS: list[tuple[str, list[list[str]]]] = [
    # --- Specific patterns first to avoid being shadowed by chemicals.
    ("Recombinant DNA", [
        ["recombinant dna"],
        ["plasmid"],
        ["expression vector"],
    ]),
    ("Experimental Models: Cell Lines", [
        ["cell line"],          # matches "cell line" and "cell lines"
        ["experimental model", "cell"],
    ]),
    ("Experimental Models: Organisms/Strains", [
        ["mouse strain"], ["mouse line"],
        ["zebrafish"], ["drosophila"], ["c elegans"],
        ["experimental model", "organism"],
        ["organism"],          # last-resort within this group
    ]),
    ("Bacterial and Virus Strains", [
        ["bacterial", "strain"],
        ["virus", "strain"],
        ["viral", "strain"],
        ["bacteria"],          # short-form "Bacteria" header
    ]),
    ("Antibodies", [
        ["antibod"],           # antibody / antibodies
    ]),
    ("Critical Commercial Assays", [
        ["critical", "assay"],
        ["commercial", "assay"],
        ["commercial kit"],
    ]),
    ("Deposited Data", [
        ["deposited", "data"],
        ["public data"],
        ["data deposited"],
    ]),
    ("Oligonucleotides", [
        ["oligonucleotide"],
        ["oligo"],
        ["primer"],
        ["sgrna"], ["sirna"], ["shrna"],
    ]),
    ("Software and Algorithms", [
        ["software"],
        ["algorithm"],
    ]),
    ("Biological Samples", [
        ["biological", "sample"],
        ["biospecimen"],
        ["clinical sample"],
        ["patient sample"],
    ]),

    # --- Most permissive last so it doesn't shadow specific ones.
    # The chemicals category is the one we filter on for Phase 2, so
    # it MUST be robust to format drift.
    ("Chemicals, Peptides, and Recombinant Proteins", [
        ["chemical"],                      # "Chemicals", "chemical"
        ["peptide"],                       # "Peptides"
        ["recombinant", "protein"],        # "Recombinant Proteins"
        ["recombinant peptide"],
    ]),

    ("Other", [
        ["other"],
    ]),
]


# ----------------------------------------------------------------------
# XML / text helpers
# ----------------------------------------------------------------------

def load_xml(path: Path) -> etree._Element | None:
    """Parse an XML file. Returns None on parse failure (rare)."""
    try:
        # JATS XML may declare a DTD with external entities; resolve_entities=False
        # is safer when running over thousands of files of unknown provenance.
        # recover=True so partially-malformed papers still yield as much as
        # possible — pull_corpus.py already validates strictly at fetch time.
        parser = etree.XMLParser(resolve_entities=False, recover=True, huge_tree=True)
        tree = etree.parse(str(path), parser)
        return tree.getroot()
    except (etree.XMLSyntaxError, OSError):
        return None


def text_of(el: etree._Element | None) -> str:
    """All descendant text of an element, whitespace-collapsed."""
    if el is None:
        return ""
    raw = " ".join(el.itertext())
    return re.sub(r"\s+", " ", raw).strip()


def _table_wrap_is_krt(tw: etree._Element) -> tuple[bool, str]:
    """
    Determine whether a `<table-wrap>` is a KRT, returning (matched,
    where) so callers can log how the match was made.

    Match sources, in priority order:
      1. The table-wrap's own `<caption>` text.
      2. The table-wrap's own `<label>` text (some authors put the
         "Key Resources Table" string here instead of in <caption>).
      3. The DIRECT `<title>` child of any ancestor `<sec>`. Some PMC
         deposits encode "Key Resources Table" only on the enclosing
         section, not the table caption itself; without this check the
         parser would silently miss those tables.

    Restricting #3 to direct `<title>` children avoids confusing a
    section's title with titles of nested subsections.
    """
    caption_els = tw.xpath(".//*[local-name()='caption']")
    caption_text = " ".join(text_of(c) for c in caption_els)
    if KRT_CAPTION_RE.search(caption_text):
        return (True, "caption")

    label_els = tw.xpath(".//*[local-name()='label']")
    label_text = " ".join(text_of(l) for l in label_els)
    if KRT_CAPTION_RE.search(label_text):
        return (True, "label")

    for ancestor in tw.iterancestors():
        if etree.QName(ancestor).localname.lower() != "sec":
            continue
        for t in ancestor.xpath("./*[local-name()='title']"):
            if KRT_CAPTION_RE.search(text_of(t)):
                return (True, "section-title")

    return (False, "")


def find_krt_table_wraps(root: etree._Element) -> list[tuple[etree._Element, str]]:
    """
    Locate all `<table-wrap>` elements that look like KRTs. Returns a
    list of `(table_wrap, match_source)` tuples so callers can log
    which signal triggered the match — useful for validating against
    real Cell Press papers.

    JATS uses no namespace by default, but some Elsevier-deposited
    files declare one — using local-name() in XPath sidesteps that.
    """
    table_wraps = root.xpath("//*[local-name()='table-wrap']")
    krt: list[tuple[etree._Element, str]] = []
    for tw in table_wraps:
        matched, where = _table_wrap_is_krt(tw)
        if matched:
            krt.append((tw, where))
    return krt


def row_cells(tr: etree._Element) -> list[str]:
    """Extract whitespace-collapsed text per cell of a <tr>."""
    cells = tr.xpath("./*[local-name()='td' or local-name()='th']")
    return [text_of(c) for c in cells]


# ----------------------------------------------------------------------
# Category detection
# ----------------------------------------------------------------------

_NORM_PUNCT_RE = re.compile(r"[,/&\-]+")
_NORM_AND_RE = re.compile(r"\s+(and|or)\s+")
_NORM_WS_RE = re.compile(r"\s+")


def _normalize_for_match(s: str) -> str:
    """
    Collapse a candidate category-header cell to a normalized form for
    substring matching. See module docstring for the normalization rules.
    """
    s = s.lower().strip().rstrip(":").strip()
    s = _NORM_PUNCT_RE.sub(" ", s)
    s = _NORM_AND_RE.sub(" ", s)
    s = _NORM_WS_RE.sub(" ", s).strip()
    return s


# Common placeholder values that appear in KRT category-header rows when
# the layout has been denormalized (e.g., a colspan'd cell rendered as
# repeated cells, or category rows that explicitly fill non-category
# columns with a dash). These are treated as "effectively empty" by the
# category-detection guard so a real category header isn't rejected
# just because cells 1-2 contain a placeholder.
_FILLER_TOKENS = frozenset({
    "—", "–", "-",                       # em-dash, en-dash, hyphen
    ".", "...", "…",                     # ellipsis variants
    "n/a", "na", "none", "not applicable",
    "—", "*",                            # leftover formatting
})


def _is_filler(s: str, reference: str = "") -> bool:
    """
    True if a cell counts as 'effectively empty' for the category-header
    test: blank, a known placeholder, or an exact repeat of the
    reference cell (which happens when a colspan'd cell is rendered as
    separate identical cells across the row).

    `reference` is normally cell 0; passing it lets us recognize the
    `[X, X, X]` denormalized-colspan case.
    """
    s = s.strip()
    if not s:
        return True
    if s.lower() in _FILLER_TOKENS:
        return True
    if reference and s == reference.strip():
        return True
    return False


def _canonicalize_category(text: str) -> str | None:
    """
    Map a free-form category-cell string to its canonical KRT name, or
    None if no pattern matches. Used in two places:
      - inside `detect_category` after the empty-rest guard passes
        (colspan'd header rows), AND
      - on the value of a dedicated category COLUMN, when the table
        encodes category as a real column rather than a header row.
    """
    norm = _normalize_for_match(text)
    if not norm:
        return None
    for canonical, anchor_sets in KRT_CATEGORY_PATTERNS:
        for anchors in anchor_sets:
            if all(a in norm for a in anchors):
                return canonical
    return None


def detect_category(cells: list[str]) -> str | None:
    """
    A KRT category-header row has the category text in cell 0 and
    empty-or-filler in all other cells. Returns the canonical category
    name on match, or None if cells doesn't look like a category header.

    The "empty-or-filler" check is what distinguishes a category header
    from a data row that happens to mention a category word (e.g., a
    reagent named "Antibody calibrator" in the chemicals section). It
    accepts:
      - genuinely empty cells (the canonical case),
      - common placeholder text (em-dash, "N/A", etc.),
      - exact repeats of cell 0 (denormalized colspan rendering).

    A multi-cell category row of the form `[X, "real data", "real data"]`
    is correctly rejected — `reference != "real data"` and the data
    cells aren't filler.
    """
    if not cells:
        return None
    first = cells[0].strip()
    if not first:
        return None

    rest_filler = all(_is_filler(c, reference=first) for c in cells[1:])
    if not rest_filler:
        return None

    return _canonicalize_category(first)


# ----------------------------------------------------------------------
# Table layout: rowspan/colspan expansion + thead column classification
# ----------------------------------------------------------------------

def _attr_int(el: etree._Element, name: str, default: int = 1) -> int:
    """Read an integer HTML/JATS attribute defensively."""
    val = el.get(name)
    if not val:
        return default
    try:
        return max(int(val), 1)
    except (ValueError, TypeError):
        return default


def _expand_table_rows(rows: list[etree._Element]) -> list[list[str]]:
    """
    Expand HTML/JATS rowspan and colspan attributes into a flat 2D
    grid of cell-text strings.

    A `rowspan="N"` cell appears in N consecutive rows, all at the
    same column; a `colspan="N"` cell appears N times across one row.
    Without expansion, downstream code sees rows that "lost cells" to
    a prior row's rowspan and ends up with column-shifted data — which
    is exactly the failure mode that mis-attributes category text into
    the reagent column when category is rowspan'd.

    Implementation note: lxml does NOT auto-materialize spans; the
    attribute values are present on the original cell element but the
    table is otherwise sparse. We build a column-indexed `pending`
    map (col -> remaining rows) so each output row gets its full
    complement of values.
    """
    expanded: list[list[str]] = []
    pending: dict[int, tuple[str, int]] = {}  # col -> (text, rows_left)

    for row in rows:
        raw_cells = row.xpath("./*[local-name()='td' or local-name()='th']")
        out: list[str] = []
        cells_iter = iter(raw_cells)
        col = 0
        cells_exhausted = False

        while True:
            # 1. If a rowspan from a prior row covers this column, emit it.
            if col in pending:
                text, rem = pending[col]
                out.append(text)
                if rem <= 1:
                    del pending[col]
                else:
                    pending[col] = (text, rem - 1)
                col += 1
                continue

            # 2. Otherwise consume the next physical cell from this row.
            if not cells_exhausted:
                try:
                    cell = next(cells_iter)
                except StopIteration:
                    cells_exhausted = True
                    continue
                rs = _attr_int(cell, "rowspan", 1)
                cs = _attr_int(cell, "colspan", 1)
                text = text_of(cell)
                for k in range(cs):
                    if rs > 1:
                        # This cell continues into subsequent rows; record
                        # it so the next row sees it at the same column.
                        pending[col + k] = (text, rs - 1)
                    out.append(text)
                col += cs
                continue

            # 3. Cells exhausted; if no more pending at >= col, this row done.
            higher = [k for k in pending if k >= col]
            if not higher:
                break
            # Pad gap with empty cells, then loop will pick up pending.
            next_col = min(higher)
            while col < next_col:
                out.append("")
                col += 1

        expanded.append(out)

    return expanded


def _classify_header(text: str) -> str | None:
    """
    Map a `<thead>` cell text to a logical column role, or None if it
    doesn't look like any known KRT column.

    Priority matters: more specific patterns first. "Resource Type"
    must classify as `category` (not `reagent`); a bare "Resource"
    must classify as `reagent` (not `source`, even though "source" is
    a Python substring of "resource"). Both of those subtleties drive
    the ordering below.
    """
    n = text.lower().strip()
    if not n:
        return None
    # Category-style headers (non-standard, but seen in some papers).
    if "category" in n or "resource type" in n:
        return "category"
    # Reagent: matches "REAGENT or RESOURCE".
    if "reagent" in n:
        return "reagent"
    # Bare 'resource' (no 'reagent', no 'type') -> reagent column.
    # MUST run before the 'source' check, because 'source' is a
    # substring of 'resource' and would otherwise capture this case.
    if "resource" in n:
        return "reagent"
    # 'Source' (vendor/lab origin).
    if "source" in n:
        return "source"
    # Identifier / RRID / Cat# / catalog number.
    if "identifier" in n or "rrid" in n or "cat#" in n or "catalog" in n:
        return "identifier"
    # 4th column variants.
    if "additional" in n or "note" in n or "comment" in n:
        return "additional"
    return None


def _detect_column_layout(tw: etree._Element) -> dict[str, int]:
    """
    Read the `<thead>` of a KRT table-wrap and return
    {logical_role: cell_index} for each role we recognize. Roles:
    'category', 'reagent', 'source', 'identifier', 'additional'.

    Returns `{}` if there's no `<thead>` or none of its cells classify;
    callers fall back to the standard 0/1/2 positional layout.

    Multi-row headers are handled by reading the LAST `<thead>` row,
    where the actual column labels live (the upper rows are usually
    grouping titles).
    """
    thead_rows = tw.xpath(".//*[local-name()='thead']/*[local-name()='tr']")
    if not thead_rows:
        return {}
    expanded = _expand_table_rows(thead_rows)
    if not expanded:
        return {}
    header = expanded[-1]
    layout: dict[str, int] = {}
    for i, h in enumerate(header):
        role = _classify_header(h)
        if role and role not in layout:
            layout[role] = i
    return layout


# ----------------------------------------------------------------------
# Per-paper parse
# ----------------------------------------------------------------------

def parse_paper(pmcid: str, xml_path: Path, debug: bool = False) -> dict:
    """
    Parse a single paper's JATS XML for KRT data rows.

    Returns:
        {
            "xml_parse_ok":   bool,    # XML loaded successfully
            "krt_table_count": int,    # number of KRT table-wraps found
            "rows":           list[dict],  # extracted data rows, pre-filter
        }

    Returning a dict (rather than yielding rows) lets main() track
    distinct counters for "had a KRT" vs. "emitted rows after filter" —
    a paper can have a KRT but no chemical rows, and we want that
    distinction in the summary.
    """
    out: dict = {"xml_parse_ok": False, "krt_table_count": 0, "rows": []}

    root = load_xml(xml_path)
    if root is None:
        if debug:
            print(f"  [{pmcid}] XML parse failed")
        return out
    out["xml_parse_ok"] = True

    krt_tables = find_krt_table_wraps(root)
    out["krt_table_count"] = len(krt_tables)
    if not krt_tables:
        if debug:
            print(f"  [{pmcid}] no KRT table-wrap found")
        return out

    if debug:
        sources = [where for _, where in krt_tables]
        print(f"  [{pmcid}] found {len(krt_tables)} KRT table-wrap(s) "
              f"(matched via: {', '.join(sources)})")

    # Header-row text we use to skip a literal column-header row inside
    # the body (some KRTs duplicate the column header in <tbody>).
    HEADER_CELL_TEXTS = {
        "reagent or resource", "reagent", "resource", "reagent/resource",
        "reagent or resources",
    }

    for ti, (tw, _match_source) in enumerate(krt_tables):
        # Step A: read thead to figure out which cell index holds which
        # logical column. Empty layout means "fall back to standard 0/1/2
        # positional layout" — the legacy behaviour for tables without a
        # recognizable header row.
        layout = _detect_column_layout(tw)
        if debug and layout:
            print(f"    table[{ti}] column layout from thead: {layout}")

        reagent_idx = layout.get("reagent", 0)
        source_idx = layout.get("source", 1)
        identifier_idx = layout.get("identifier", 2)
        additional_idx = layout.get("additional")  # may be None
        category_idx = layout.get("category")      # may be None

        used_indices = [
            i for i in (reagent_idx, source_idx, identifier_idx,
                        additional_idx, category_idx)
            if i is not None
        ]
        # 'expected_width' is how many columns the layout claims. Rows
        # with more cells indicate either an undeclared 4th column or
        # an extra trailing field; either way we capture the extras
        # into additional_info rather than dropping them silently.
        expected_width = (max(used_indices) + 1) if used_indices else 3

        # Step B: collect body rows. Prefer <tbody>; fall back to "all
        # <tr> not under <thead>" if the table lacks <tbody> markup.
        tbody_trs = tw.xpath(".//*[local-name()='tbody']/*[local-name()='tr']")
        if not tbody_trs:
            thead_tr_ids = {
                id(x) for x in tw.xpath(".//*[local-name()='thead']/*[local-name()='tr']")
            }
            tbody_trs = [
                tr for tr in tw.xpath(".//*[local-name()='tr']")
                if id(tr) not in thead_tr_ids
            ]

        # Step C: expand rowspan/colspan. After this, body_grid[i] is
        # the materialized cell list for row i, with rowspan'd values
        # carried forward and colspan'd values duplicated. This is the
        # single change that fixes the category-as-rowspan failure mode.
        body_grid = _expand_table_rows(tbody_trs)

        active_category: str | None = None
        emitted_in_this_table = 0

        for ri, cells in enumerate(body_grid):
            if not cells:
                continue

            # 1. Colspan'd category-header row? (Single category text in
            #    cell 0, rest empty/filler/repeat.)
            cat = detect_category(cells)
            if cat is not None:
                active_category = cat
                if debug:
                    print(f"    table[{ti}] row[{ri}] -> category={cat!r}  "
                          f"(raw cell0={cells[0]!r})")
                continue

            # 2. Data row — extract by mapped indices, not hard-coded
            #    positions. This is what protects against column shift
            #    when the table has a category column (see Step A).
            n = len(cells)
            warnings: list[str] = []

            def _get(idx: int | None) -> str:
                return cells[idx] if idx is not None and 0 <= idx < n else ""

            reagent_name = _get(reagent_idx)
            source = _get(source_idx)
            identifier = _get(identifier_idx)
            additional = _get(additional_idx)

            # 3. Capture stray cells past the mapped layout into
            #    additional_info, so a 4th column in an undeclared 4-col
            #    table isn't silently dropped.
            if n > expected_width:
                extras = [cells[i] for i in range(expected_width, n)
                          if cells[i].strip()]
                if extras:
                    joined = " | ".join(extras)
                    additional = (additional + " | " + joined) if additional else joined
                    warnings.append(f"captured_{len(extras)}_extra_cells")
            elif n < expected_width:
                warnings.append(f"only_{n}_cells_expected_{expected_width}")

            # 4. Determine the row's category. A column-encoded category
            #    overrides the active (colspan'd) one for this row only.
            row_category = active_category
            if category_idx is not None:
                col_cat = _get(category_idx).strip()
                if col_cat:
                    canon = _canonicalize_category(col_cat)
                    row_category = canon if canon else col_cat

            # 5. Skip apparent column-header rows duplicated in body.
            if reagent_name.lower().strip() in HEADER_CELL_TEXTS:
                continue
            if not reagent_name.strip():
                continue
            if row_category is None:
                warnings.append("no_active_category")

            out["rows"].append({
                "pmcid": pmcid,
                "category": row_category or "",
                "reagent_name": reagent_name,
                "source": source,
                "identifier": identifier,
                "additional_info": additional,
                "parser_warnings": ";".join(warnings),
            })
            emitted_in_this_table += 1

        if debug:
            print(f"    table[{ti}] emitted {emitted_in_this_table} data rows")

    return out


# ----------------------------------------------------------------------
# Manifest handling
# ----------------------------------------------------------------------

def truthy(s: object) -> bool:
    """Accept Python booleans or their CSV-stringified forms ('True'/'1')."""
    return str(s).strip().lower() in {"true", "1", "yes"}


def load_manifest(path: Path) -> list[dict]:
    """
    Load manifest.csv as a list of row-dicts, preserving file order.
    Returns [] if the manifest is missing.
    """
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


# ----------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------

def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--debug", action="store_true",
                   help="Verbose per-paper logging; useful on validation runs.")
    p.add_argument("--limit", type=int, default=None,
                   help="Stop after parsing N papers (counts against the "
                        "manifest in file order, not the alphabetical cache).")
    p.add_argument("--category-filter", default=None,
                   help="Only emit rows where category matches this string "
                        "(case-insensitive substring). Default: keep all.")
    p.add_argument("--include-cache-orphans", action="store_true",
                   help="Also parse XMLs in xml_cache/ that are not listed "
                        "in the current manifest.csv. Tagged with "
                        "journal=\"(cache-orphan)\" in the output. Useful "
                        "for auditing leftover cache from earlier pulls.")
    args = p.parse_args(argv)

    manifest = load_manifest(MANIFEST_CSV)

    # Build the iteration target list. Manifest order is preserved; orphans
    # (if requested) are appended after, in alphabetical PMCID order.
    targets: list[dict] = []
    seen_pmcids: set[str] = set()
    for row in manifest:
        pmcid = (row.get("pmcid") or "").strip()
        if not pmcid or pmcid in seen_pmcids:
            continue
        if not (truthy(row.get("has_xml")) and truthy(row.get("fetch_ok"))):
            continue
        targets.append(row)
        seen_pmcids.add(pmcid)

    if args.include_cache_orphans:
        for path in sorted(XML_CACHE.glob("*.xml")):
            if path.stem in seen_pmcids:
                continue
            targets.append({
                "pmcid": path.stem,
                "journal": "(cache-orphan)",
                "year": "",
                "title": "",
            })
            seen_pmcids.add(path.stem)

    if not targets:
        if not manifest:
            print(
                f"ERROR: {MANIFEST_CSV.name} is missing or empty.\n"
                "Run pull_corpus.py first, or pass --include-cache-orphans "
                "to scan xml_cache/ directly.",
                file=sys.stderr,
            )
        else:
            print(
                "ERROR: manifest has no rows with has_xml=True and fetch_ok=True.\n"
                "Either re-run pull_corpus.py with --fetch, or pass "
                "--include-cache-orphans to inspect leftover cache.",
                file=sys.stderr,
            )
        return 1

    if args.limit is not None:
        targets = targets[: args.limit]

    OUT_DIR.mkdir(exist_ok=True)
    fieldnames = [
        "pmcid", "journal", "year",
        "category", "reagent_name", "source", "identifier", "additional_info",
        "parser_warnings",
    ]

    # Counters tracked separately so the summary distinguishes
    # XML-parse-ok / KRT-detected / rows-emitted.
    n_papers = 0
    n_papers_xml_ok = 0
    n_papers_with_krt = 0
    n_papers_with_emitted_rows = 0
    n_rows_total = 0
    n_rows_kept = 0
    n_skipped_missing_xml = 0

    cat_filter = args.category_filter.lower() if args.category_filter else None

    with OUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for tgt in targets:
            pmcid = (tgt.get("pmcid") or "").strip()
            xml_path = XML_CACHE / f"{pmcid}.xml"
            if not xml_path.exists():
                # Manifest claimed has_xml/fetch_ok=True but the file is gone.
                # Skip with a debug note rather than failing the whole run.
                n_skipped_missing_xml += 1
                if args.debug:
                    print(f"  [{pmcid}] manifest expected XML, file missing — skipped")
                continue

            n_papers += 1
            result = parse_paper(pmcid, xml_path, debug=args.debug)

            if result["xml_parse_ok"]:
                n_papers_xml_ok += 1
            if result["krt_table_count"] > 0:
                n_papers_with_krt += 1
            n_rows_total += len(result["rows"])

            paper_emitted = 0
            for r in result["rows"]:
                if cat_filter is not None and cat_filter not in (r["category"] or "").lower():
                    continue
                r["journal"] = tgt.get("journal", "")
                r["year"] = tgt.get("year", "")
                writer.writerow(r)
                n_rows_kept += 1
                paper_emitted += 1

            if paper_emitted > 0:
                n_papers_with_emitted_rows += 1

    # Summary. Each counter answers a distinct question, so a regression
    # in one (e.g., KRT detection drops) is visible without conflating
    # with another (e.g., chemicals filter rejecting them).
    print()
    print(f"Manifest entries selected:    {len(targets)}")
    if n_skipped_missing_xml:
        print(f"  skipped (XML file missing): {n_skipped_missing_xml}")
    print(f"Papers parsed:                {n_papers}")
    print(f"  XML parsed OK:              {n_papers_xml_ok}")
    print(f"  KRT detected:               {n_papers_with_krt}"
          f"  ({n_papers_with_krt / max(n_papers, 1):.0%} of parsed)")
    print(f"Rows extracted (raw):         {n_rows_total}")
    if cat_filter:
        print(f"Rows kept after filter:       {n_rows_kept}"
              f"  (category contains {args.category_filter!r})")
        print(f"Papers contributing rows:     {n_papers_with_emitted_rows}")
    else:
        print(f"Rows emitted:                 {n_rows_kept}")
    try:
        rel = OUT_CSV.relative_to(ROOT.parent)
    except ValueError:
        rel = OUT_CSV
    print(f"Wrote {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
