"""The double-drawn text layer, on PDFs this file builds itself.

Some CIM producers fake a bold weight by drawing the same glyph run twice at
identical coordinates. The characters are correct and single in `page.chars`;
it is `extract_text()`, sorting by position, that interleaves the two passes
and yields `OOFFFFEERRIINNGG`. Measured over the 45-deck corpus: 74 of 1095
pages across 17 of 45 decks, and it lands on HEADINGS -- which is what
`_find_property_name`, `location.locate` and `portfolio_signal` read.

**These build their own PDFs rather than reading the corpus**, because the
corpus is gitignored (`tests/test_extraction_corpus.py` skips without it and CI
therefore never runs it). A defect CI cannot see is a defect that comes back.
The generator below is ~40 lines of raw PDF and needs no new dependency; it can
place a run of glyphs once or twice at the same origin, which is the entire
shape of the bug.
"""
import pdfplumber
import pytest

from extract.pdf_reader import (
    DEDUPE_TOLERANCE,
    deduped_page,
    extract_pdf,
    page_text,
)


# ------------------------------------------------------------------ generator

def _pdf(draws, *, width=300, height=200):
    """A one-page PDF drawing `draws` = [(text, x, y), ...] in Helvetica 12.

    Passing the same (text, x, y) twice is how a fake-bold double-draw is
    reproduced: two Tj operators at one origin, which is what the real decks
    contain.
    """
    stream = "BT /F1 12 Tf\n" + "".join(
        f"1 0 0 1 {x} {y} Tm ({t}) Tj\n" for t, x, y in draws
    ) + "ET"
    body = stream.encode("latin-1")

    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
         f"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>").encode(),
        b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"

    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


def _write(tmp_path, name, draws, **kw):
    p = tmp_path / name
    p.write_bytes(_pdf(draws, **kw))
    return p


# The generator has to actually reproduce the bug, or every assertion below is
# testing nothing. This is the control.
def test_the_generator_reproduces_the_doubling_it_claims_to(tmp_path):
    doubled = _write(tmp_path, "d.pdf", [("OFFERING", 20, 150)] * 2)
    with pdfplumber.open(doubled) as pdf:
        raw = pdf.pages[0].extract_text() or ""
    assert "OOFFFFEERRIINNGG" in raw


# ---------------------------------------------------------------- the repair

def test_a_double_drawn_heading_reads_once(tmp_path):
    path = _write(tmp_path, "d.pdf", [("OFFERING MEMORANDUM", 20, 150)] * 2)
    with pdfplumber.open(path) as pdf:
        assert "OFFERING MEMORANDUM" in page_text(pdf.pages[0])


def test_a_singly_drawn_page_is_left_alone(tmp_path):
    """Dedupe must be inert on the 94% of pages that were never damaged."""
    draws = [("OFFERING MEMORANDUM", 20, 150), ("Rogers, Arkansas", 20, 120)]
    path = _write(tmp_path, "s.pdf", draws)
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        assert page_text(page) == (page.extract_text() or "")


def test_a_repeated_run_at_a_DIFFERENT_origin_survives(tmp_path):
    """Dedupe collapses a double-DRAW, never a word that genuinely recurs.

    A CIM says `Abilene` on the cover and again in the address block. Collapsing
    those would be a data-loss bug wearing this fix's badge, so the property is
    asserted rather than assumed.
    """
    path = _write(tmp_path, "r.pdf", [("Abilene", 20, 150), ("Abilene", 20, 100)])
    with pdfplumber.open(path) as pdf:
        assert page_text(pdf.pages[0]).count("Abilene") == 2


def test_the_tolerance_does_not_eat_an_adjacent_space(tmp_path):
    """Why DEDUPE_TOLERANCE is 0.1 and not pdfplumber's default 1.0.

    At the default, two space characters roughly a point apart -- ordinary tight
    kerning -- are treated as one draw of the same glyph. Measured on the real
    Abilene cover, that turned `THE STORAGE PLACE - ABILENE` into
    `THE STORAGE PLACE -ABILENE`, and the `_fin_` label regexes match on
    wording. A genuine double-draw is a copy at the SAME origin, so the slack
    buys nothing and only risks eating real glyphs.
    """
    assert DEDUPE_TOLERANCE < 1.0
    path = _write(tmp_path, "t.pdf", [("A B", 20, 150)] * 2)
    with pdfplumber.open(path) as pdf:
        assert page_text(pdf.pages[0]) == "A B"


# ------------------------------------------------------- the seam it sits on

def test_extract_pdf_returns_the_repaired_text(tmp_path):
    path = _write(tmp_path, "d.pdf", [("OFFERING MEMORANDUM", 20, 150)] * 2)
    raw = extract_pdf(str(path))
    assert "OFFERING MEMORANDUM" in raw["pages"][0]
    assert "OFFERING MEMORANDUM" in raw["text"]
    assert "OOFFFFEERR" not in raw["text"]


def test_the_page_separator_format_is_unchanged(tmp_path):
    """`parser._REGION_END_RE` terminates the demographics scan on these two
    lines, and `tests/test_parser_noi.py` hardcodes the rule. Repairing the text
    must not move the frame around it."""
    path = _write(tmp_path, "s.pdf", [("Rogers, Arkansas", 20, 150)])
    text = extract_pdf(str(path))["text"]
    assert "=" * 60 in text
    assert "--- PAGE 1 ---" in text


def test_deduped_page_still_serves_tables(tmp_path):
    """`extract_pdf` takes text AND tables off one deduped page. The filtered
    page must remain a page, not just a text source."""
    path = _write(tmp_path, "s.pdf", [("Rogers, Arkansas", 20, 150)])
    with pdfplumber.open(path) as pdf:
        assert deduped_page(pdf.pages[0]).extract_tables() == []


def test_extract_pdf_still_refuses_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract_pdf(str(tmp_path / "nope.pdf"))
