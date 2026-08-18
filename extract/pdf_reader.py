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

from extract import ocr

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


def extract_pdf(filepath: str, transcriber=None) -> dict:
    """
    Extract all text and tables from a PDF file.

    Pages with no usable text layer are handed to `transcriber` when OCR is
    enabled (`CIM_OCR_ENABLED`). The default transcribes nothing, so the
    returned dict is byte-for-byte what it was before OCR existed — see
    `extract/ocr.py` on why the inert default is the point rather than a stub.

    Returns:
        {
            "text": str           — full text, pages separated by markers,
            "tables": list        — list of tables (each table is list of rows),
            "page_count": int,
            "pages": list[str]    — text per page,
            "ocr_pages": list[int] — 1-based pages whose text came from OCR,
        }
    """
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"PDF not found: {filepath}")

    pages_text = []
    all_tables = []
    ocr_pages = []

    use_ocr = ocr.ocr_enabled() and transcriber is not None
    # Hashing the file is wasted work on the overwhelming majority of decks,
    # which have no page needing OCR at all, so it is deferred until one does.
    doc_digest = None
    cache = ocr.PageCache() if use_ocr else None

    with pdfplumber.open(filepath) as pdf:
        page_count = len(pdf.pages)

        for i, page in enumerate(pdf.pages):
            # One dedupe, reused for both reads below.
            deduped = deduped_page(page)

            # Extract text
            text = deduped.extract_text() or ""
            tables = deduped.extract_tables()

            if use_ocr and ocr.needs_ocr(page):
                if doc_digest is None:
                    doc_digest = ocr.document_digest(filepath)
                transcribed = ocr.transcribe_page(page, doc_digest, transcriber, cache)
                if transcribed:
                    # OCR only ever ADDS to a page the text layer left empty --
                    # `needs_ocr` fired, so there is nothing here to overwrite.
                    # Never letting a transcription displace real embedded text
                    # keeps the extracted document at least as trustworthy as
                    # the PDF it came from.
                    text = transcribed.text
                    tables = transcribed.tables
                    ocr_pages.append(i + 1)

            pages_text.append(text)

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
        "ocr_pages": ocr_pages,
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
