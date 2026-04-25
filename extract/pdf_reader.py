"""
PDF text and table extraction using pdfplumber.
"""

import os
import pdfplumber


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
            # Extract text
            text = page.extract_text() or ""
            pages_text.append(text)

            # Extract tables
            tables = page.extract_tables()
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
