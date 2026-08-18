"""
PDF text and table extraction using pdfplumber.

Every page is read through `dedupe_chars()` first. Some CIM producers fake a
bold weight by drawing the same run of glyphs twice at (near) identical
coordinates; the characters themselves are correct and single in `page.chars`,
but `extract_text()` sorts by position and interleaves the two passes, so
`OFFERING MEMORANDUM` extracts as `OOFFFFEERRIINNGG MMEEMMOORRAANNDDUUMM`
(issue #91). Measured over the 45-deck corpus: 74 of 1095 pages across 17 of 45
decks. The doubling lands on HEADINGS rather than body text -- which is exactly
where the cover-wording rules key (`_find_property_name`, `location.locate`,
`portfolio_signal`), so a small character count is not a small effect.

Deduping is done ONCE per page and the filtered page is reused for both text and
tables, because `dedupe_chars()` costs roughly the extraction it precedes
(0.98s -> 1.76s over a 19-page deck) and doing it twice would pay that twice.
"""

import os
import pdfplumber

# `dedupe_chars` drops a character when another with the SAME text sits within
# this many points of it. The default of 1.0 is too coarse for tight kerning: it
# cost a legitimate space on the Abilene cover, turning
# `THE STORAGE PLACE - ABILENE` into `THE STORAGE PLACE -ABILENE`, and the
# `_fin_` label regexes match on wording. A double-draw is a copy at the SAME
# origin, not a near one, so the tolerance buys nothing and only risks eating
# real glyphs.
DEDUPE_TOLERANCE = 0.1


def deduped_page(page):
    """The page with double-drawn glyph runs collapsed.

    THE entry point for reading a page in this repo. `extract_pdf` below takes
    text AND tables off the returned object, and `scripts/cims_rename_plan.py`
    calls it rather than keeping a second pdfplumber reader whose text quality
    could drift from this one.
    """
    return page.dedupe_chars(tolerance=DEDUPE_TOLERANCE)


def page_text(page) -> str:
    """The page's text, deduped. Never None -- callers join these."""
    return deduped_page(page).extract_text() or ""


def extract_pdf(filepath: str) -> dict:
    """
    Extract all text and tables from a PDF file.

    Returns:
        {
            "text": str           — full text, pages separated by markers,
            "tables": list        — list of tables (each table is list of rows),
            "page_count": int,
            "pages": list[str]    — text per page,
        }
    """
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"PDF not found: {filepath}")

    pages_text = []
    all_tables = []

    with pdfplumber.open(filepath) as pdf:
        page_count = len(pdf.pages)

        for i, page in enumerate(pdf.pages):
            # One dedupe, reused for both reads below.
            deduped = deduped_page(page)

            # Extract text
            text = deduped.extract_text() or ""
            pages_text.append(text)

            # Extract tables
            tables = deduped.extract_tables()
            for table in tables:
                cleaned = _clean_table(table)
                if cleaned:
                    all_tables.append({
                        "page": i + 1,
                        "data": cleaned,
                    })

    full_text = ""
    for i, pt in enumerate(pages_text):
        full_text += f"\n{'='*60}\n--- PAGE {i+1} ---\n{'='*60}\n"
        full_text += pt + "\n"

    return {
        "text": full_text,
        "tables": all_tables,
        "page_count": page_count,
        "pages": pages_text,
    }


def _clean_table(table: list) -> list:
    """Remove empty rows and normalize whitespace in table cells."""
    if not table:
        return []

    cleaned = []
    for row in table:
        if row is None:
            continue
        clean_row = []
        for cell in row:
            if cell is None:
                clean_row.append("")
            else:
                clean_row.append(str(cell).strip().replace("\n", " "))
        # Skip fully empty rows
        if any(c for c in clean_row):
            cleaned.append(clean_row)

    return cleaned
