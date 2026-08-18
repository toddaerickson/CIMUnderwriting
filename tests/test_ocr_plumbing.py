"""The OCR fallback's plumbing, with a fake transcriber — never the network.

`extract/ocr.py` ships everything except the transcription: the trigger, the
render, the cache, the merge back into `extract_pdf`'s dict. The transcriber is
injected and the default returns nothing, so these can pin the whole mechanism
offline, which is the only way CI can see it at all.

The PDFs are built here, same generator as `tests/test_pdf_reader_dedupe.py`,
because the corpus is gitignored and CI never runs the corpus tests. A page
with no text at all is exactly what a scanned CIM page is, and it takes four
lines to make one.
"""
import math

import pdfplumber
import pytest

from extract import ocr
from extract.pdf_reader import extract_pdf
from tests.test_pdf_reader_dedupe import _pdf


#: A page's worth of statement lines. It has to clear
#: `MIN_CHARS_FOR_TEXT_LAYER` to stand in for a real text page -- a genuine CIM
#: page carries 200-1300 characters, and one short line would trip the very
#: floor these tests exercise.
STATEMENT = [
    ("Real Estate Taxes 285,745 294,318", 20, 170),
    ("Insurance 12,404 12,776", 20, 150),
    ("Advertising and Marketing 34,000 35,020", 20, 130),
    ("Total Expenses 563,066 585,165", 20, 110),
]


@pytest.fixture
def scanned(tmp_path):
    """A two-page PDF: page 1 has real text, page 2 has none."""
    text_page = _pdf(STATEMENT)
    blank = _pdf([])
    # Two single-page files stand in for a mixed document wherever the test
    # only needs one page's behaviour; the mixed case is built explicitly below.
    p1, p2 = tmp_path / "text.pdf", tmp_path / "blank.pdf"
    p1.write_bytes(text_page)
    p2.write_bytes(blank)
    return p1, p2


def _fake(text="RECOVERED", tables=None, calls=None):
    def transcriber(image):
        if calls is not None:
            calls.append(image)
        assert isinstance(image, bytes) and image[:4] == b"\x89PNG", \
            "transcribers are handed rendered PNG bytes"
        return ocr.OcrPage(text=text, tables=tables or [])
    return transcriber


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Never touch the developer's real cache directory."""
    monkeypatch.setenv("CIM_OCR_CACHE_DIR", str(tmp_path / "cache"))


# ------------------------------------------------------------------- trigger

def test_needs_ocr_fires_on_a_page_with_no_text_layer(scanned):
    _, blank = scanned
    with pdfplumber.open(blank) as pdf:
        assert ocr.needs_ocr(pdf.pages[0])


def test_needs_ocr_leaves_a_real_text_page_alone(scanned):
    text_page, _ = scanned
    with pdfplumber.open(text_page) as pdf:
        assert not ocr.needs_ocr(pdf.pages[0])


def test_the_trigger_and_the_metric_share_one_constant():
    """`scripts/extraction_report.py` counts `image_only_pages` with the same
    floor `needs_ocr` acts on. Two definitions of one word is how a metric
    starts disagreeing with the behaviour it claims to describe."""
    from scripts.extraction_report import MIN_CHARS_FOR_TEXT_LAYER
    assert MIN_CHARS_FOR_TEXT_LAYER is ocr.MIN_CHARS_FOR_TEXT_LAYER


# --------------------------------------------------------------- off by default

def test_ocr_is_off_unless_the_env_var_says_otherwise(monkeypatch):
    monkeypatch.delenv("CIM_OCR_ENABLED", raising=False)
    assert not ocr.ocr_enabled()
    monkeypatch.setenv("CIM_OCR_ENABLED", "1")
    assert ocr.ocr_enabled()
    monkeypatch.setenv("CIM_OCR_ENABLED", "no")
    assert not ocr.ocr_enabled()


def test_a_disabled_run_never_calls_the_transcriber(scanned, monkeypatch):
    """A paid per-page call must not be reachable by accident."""
    monkeypatch.delenv("CIM_OCR_ENABLED", raising=False)
    _, blank = scanned
    calls = []
    raw = extract_pdf(str(blank), transcriber=_fake(calls=calls))
    assert calls == []
    assert raw["pages"][0] == ""
    assert raw["ocr_pages"] == []


def test_the_default_transcriber_transcribes_nothing(monkeypatch):
    monkeypatch.setenv("CIM_OCR_ENABLED", "1")
    assert not ocr.null_transcriber(b"whatever")


def test_an_enabled_run_with_no_transcriber_is_inert(scanned, monkeypatch):
    monkeypatch.setenv("CIM_OCR_ENABLED", "1")
    _, blank = scanned
    raw = extract_pdf(str(blank))
    assert raw["pages"][0] == ""
    assert raw["ocr_pages"] == []


# ------------------------------------------------------------------ the merge

def test_a_scanned_page_gets_its_text_from_the_transcriber(scanned, monkeypatch):
    monkeypatch.setenv("CIM_OCR_ENABLED", "1")
    _, blank = scanned
    raw = extract_pdf(str(blank), transcriber=_fake("RECOVERED TEXT"))
    assert raw["pages"][0] == "RECOVERED TEXT"
    assert "RECOVERED TEXT" in raw["text"]
    assert raw["ocr_pages"] == [1]


def test_transcribed_tables_reach_the_tables_list(scanned, monkeypatch):
    """The whole reason a text-only transcriber is near-useless: `tables.py`
    assigns periods to columns BY LIST INDEX, so rows must arrive row-shaped."""
    monkeypatch.setenv("CIM_OCR_ENABLED", "1")
    _, blank = scanned
    rows = [["", "T-12", "Year 1"], ["Real Estate Taxes", "285,745", "294,318"]]
    raw = extract_pdf(str(blank), transcriber=_fake("x", tables=[rows]))
    assert [t["data"] for t in raw["tables"]] == [rows]
    assert raw["tables"][0]["page"] == 1


def test_a_real_text_layer_is_never_displaced_by_ocr(scanned, monkeypatch):
    """OCR only ever fills a page the text layer left empty. A transcription
    overwriting embedded text would make the extracted document less
    trustworthy than the PDF it came from."""
    monkeypatch.setenv("CIM_OCR_ENABLED", "1")
    text_page, _ = scanned
    calls = []
    raw = extract_pdf(str(text_page), transcriber=_fake("WRONG", calls=calls))
    assert calls == []
    assert "Total Expenses" in raw["pages"][0]
    assert "WRONG" not in raw["text"]
    assert raw["ocr_pages"] == []


def test_only_the_scanned_page_of_a_mixed_document_is_transcribed(tmp_path, monkeypatch):
    monkeypatch.setenv("CIM_OCR_ENABLED", "1")
    # One document, page 1 with text and page 2 without, so page NUMBERING is
    # exercised rather than assumed.
    path = tmp_path / "mixed.pdf"
    path.write_bytes(_mixed())
    raw = extract_pdf(str(path), transcriber=_fake("FROM PAGE TWO"))
    assert raw["page_count"] == 2
    assert "Total Expenses" in raw["pages"][0]
    assert raw["pages"][1] == "FROM PAGE TWO"
    assert raw["ocr_pages"] == [2]


def test_the_page_separator_format_survives_ocr(scanned, monkeypatch):
    """`parser._REGION_END_RE` terminates the demographics scan on these lines."""
    monkeypatch.setenv("CIM_OCR_ENABLED", "1")
    _, blank = scanned
    text = extract_pdf(str(blank), transcriber=_fake("RECOVERED"))["text"]
    assert "=" * 60 in text and "--- PAGE 1 ---" in text


def test_an_empty_transcription_leaves_the_page_alone(scanned, monkeypatch):
    """A transcriber that found nothing must not be recorded as an OCR'd page —
    that would claim a provenance no text has."""
    monkeypatch.setenv("CIM_OCR_ENABLED", "1")
    _, blank = scanned
    raw = extract_pdf(str(blank), transcriber=lambda img: ocr.OcrPage())
    assert raw["pages"][0] == ""
    assert raw["ocr_pages"] == []


def test_a_failing_transcriber_does_not_lose_the_document(tmp_path, monkeypatch):
    """One unreadable page must not cost the other thirty-one."""
    monkeypatch.setenv("CIM_OCR_ENABLED", "1")
    path = tmp_path / "mixed.pdf"
    path.write_bytes(_mixed())

    def boom(image):
        raise RuntimeError("vendor 503")

    raw = extract_pdf(str(path), transcriber=boom)
    assert "Total Expenses" in raw["pages"][0]
    assert raw["pages"][1] == ""
    assert raw["ocr_pages"] == []


# -------------------------------------------------------------------- cache

def test_the_second_extraction_does_not_re_transcribe(tmp_path, monkeypatch):
    """`engine.py` calls `extract_pdf` twice for one deal — once in
    `extract_pdf_data`, once in `run_analysis`. Uncached, every deal bills
    twice for identical output."""
    monkeypatch.setenv("CIM_OCR_ENABLED", "1")
    path = tmp_path / "blank.pdf"
    path.write_bytes(_pdf([]))
    calls = []
    t = _fake("RECOVERED", calls=calls)

    first = extract_pdf(str(path), transcriber=t)
    second = extract_pdf(str(path), transcriber=t)

    assert len(calls) == 1
    assert first["pages"] == second["pages"] == ["RECOVERED"]


def test_the_cache_is_keyed_on_content_not_path(tmp_path, monkeypatch):
    """Deal folders rename and re-upload. A path-keyed cache would serve one
    document's text for another's."""
    monkeypatch.setenv("CIM_OCR_ENABLED", "1")
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    blank = _pdf([])
    a.write_bytes(blank)
    b.write_bytes(blank)          # same bytes, different name
    calls = []
    t = _fake("RECOVERED", calls=calls)

    extract_pdf(str(a), transcriber=t)
    extract_pdf(str(b), transcriber=t)
    assert len(calls) == 1, "identical documents share a cache entry"

    different = tmp_path / "c.pdf"
    different.write_bytes(_pdf([], width=301))   # different bytes
    extract_pdf(str(different), transcriber=t)
    assert len(calls) == 2, "a different document must not read a cached page"


def test_a_version_bump_invalidates_cached_pages(tmp_path, monkeypatch):
    """The key carries TRANSCRIBER_VERSION so changing the DPI, prompt or model
    cannot silently serve output the current code would not produce."""
    monkeypatch.setenv("CIM_OCR_ENABLED", "1")
    path = tmp_path / "blank.pdf"
    path.write_bytes(_pdf([]))
    calls = []
    t = _fake("RECOVERED", calls=calls)

    extract_pdf(str(path), transcriber=t)
    monkeypatch.setattr(ocr, "TRANSCRIBER_VERSION", ocr.TRANSCRIBER_VERSION + 1)
    extract_pdf(str(path), transcriber=t)
    assert len(calls) == 2


def test_an_unwritable_cache_still_serves_the_transcription(tmp_path, monkeypatch):
    """A cache that cannot write is slow and expensive, not broken.

    The failure is injected at the filesystem, not at `PageCache.put`, so what
    is exercised is `put`'s own OSError handling rather than a stub standing in
    for it.
    """
    monkeypatch.setenv("CIM_OCR_ENABLED", "1")

    def read_only(*a, **k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(ocr.os, "makedirs", read_only)
    path = tmp_path / "blank.pdf"
    path.write_bytes(_pdf([]))
    raw = extract_pdf(str(path), transcriber=_fake("RECOVERED"))
    assert raw["pages"][0] == "RECOVERED"
    assert raw["ocr_pages"] == [1]


def test_a_cache_hit_never_renders(tmp_path, monkeypatch):
    """Rendering at 200 DPI is the expensive half; a hit must skip it."""
    monkeypatch.setenv("CIM_OCR_ENABLED", "1")
    cache = ocr.PageCache(str(tmp_path / "c"))
    cache.put("digest", 1, ocr.OcrPage(text="CACHED"))

    def explode(page, dpi=ocr.RENDER_DPI):
        raise AssertionError("rendered on a cache hit")

    monkeypatch.setattr(ocr, "render_page", explode)
    path = tmp_path / "blank.pdf"
    path.write_bytes(_pdf([]))
    with pdfplumber.open(path) as pdf:
        got = ocr.transcribe_page(pdf.pages[0], "digest", _fake(), cache)
    assert got.text == "CACHED"


def test_a_corrupt_cache_entry_is_a_miss_not_a_crash(tmp_path):
    cache = ocr.PageCache(str(tmp_path / "c"))
    cache.put("digest", 1, ocr.OcrPage(text="fine"))
    path = cache._path("digest", 1)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert cache.get("digest", 1) is None


def test_a_cached_page_round_trips_its_tables(tmp_path):
    rows = [["Label", "T-12"], ["Insurance", "12,404"]]
    cache = ocr.PageCache(str(tmp_path / "c"))
    cache.put("digest", 3, ocr.OcrPage(text="t", tables=[rows]))
    got = cache.get("digest", 3)
    assert got.text == "t" and got.tables == [rows]


# -------------------------------------------------------------------- render

def test_render_page_produces_png_bytes(scanned):
    text_page, _ = scanned
    with pdfplumber.open(text_page) as pdf:
        png = ocr.render_page(pdf.pages[0])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


class _Page:
    """Just the two attributes `render_scale` reads, in POINTS."""

    def __init__(self, width, height):
        self.width, self.height = width, height


def test_an_ordinary_page_renders_at_the_full_dpi():
    """The cap is a ceiling, not a target. A letter page at 200 DPI is
    1700x2200 and must be left there — upscaling to the long-edge limit adds
    pixels and no information."""
    assert ocr.render_scale(_Page(612, 792)) == pytest.approx(200 / 72)


def test_an_oversized_page_is_capped_to_both_tier_ceilings():
    """The measured Tucson case, and the reason a fixed DPI is the wrong knob.

    Its pages are 1650x1275 POINTS. At 200 DPI that renders 4584x3542 =
    16.24 MP — 4.3x over the 3.75 MP tier ceiling, 9 MB of PNG, and
    downsampled server-side anyway.
    """
    scale = ocr.render_scale(_Page(1650, 1275))
    # ceil, not round: that is what pdfium's `render()` does to each side.
    w, h = math.ceil(1650 * scale), math.ceil(1275 * scale)
    assert max(w, h) <= ocr.MAX_IMAGE_EDGE
    assert w * h <= ocr.MAX_IMAGE_PIXELS
    assert scale < 200 / 72          # genuinely reduced, not a no-op


@pytest.mark.parametrize("width,height", [
    (1650, 1275), (1275, 1650), (1224, 1584), (2000, 2000), (3400, 1912),
])
def test_the_ceiling_holds_on_the_rounded_bitmap_not_the_ideal_one(width, height):
    """An exact-fit scale is not good enough, because `render()` rounds each
    side UP: the naive `sqrt(max_pixels / area)` puts Tucson at 2203x1703,
    1,709 pixels past a constant named `MAX_IMAGE_PIXELS`. A ceiling the code
    quietly exceeds is worse than no ceiling — it reads as checked."""
    scale = ocr.render_scale(_Page(width, height))
    w, h = math.ceil(width * scale), math.ceil(height * scale)
    assert w * h <= ocr.MAX_IMAGE_PIXELS
    assert max(w, h) <= ocr.MAX_IMAGE_EDGE


def test_the_area_ceiling_binds_where_the_edge_ceiling_does_not():
    """The half a long-edge cap alone gets wrong, and the reason this is two
    constants rather than one.

    At a 2576 long edge Tucson's 4:3 sheet is 2576x1991 = 5.13 MP: inside the
    edge limit and 37% over the area limit. Only a page near 16:9 has both
    bind at once.
    """
    page = _Page(1650, 1275)
    edge_only = ocr.MAX_IMAGE_EDGE / 1650
    assert round(1650 * edge_only) * round(1275 * edge_only) > ocr.MAX_IMAGE_PIXELS
    assert ocr.render_scale(page) < edge_only


def test_the_cap_reads_the_long_edge_whichever_side_it_is():
    """Portrait and landscape of the same sheet must render identically —
    reading `width` alone would cap one and not the other."""
    assert ocr.render_scale(_Page(1650, 1275)) == ocr.render_scale(
        _Page(1275, 1650))


def test_a_degenerate_page_size_does_not_divide_by_zero():
    assert ocr.render_scale(_Page(0, 0)) == pytest.approx(200 / 72)


def test_an_oversized_scan_falls_back_to_jpeg(scanned, monkeypatch):
    """PNG is tried first — a synthetic page stays small and crisp losslessly
    — and only a page that blows the byte budget re-encodes. Forced here by
    dropping the budget rather than by building a 9 MB fixture."""
    monkeypatch.setattr(ocr, "MAX_IMAGE_BYTES", 1)
    text_page, _ = scanned
    with pdfplumber.open(text_page) as pdf:
        image = ocr.render_page(pdf.pages[0])
    assert image[:3] == b"\xff\xd8\xff"


def test_an_ocr_page_is_falsy_when_it_found_nothing():
    assert not ocr.OcrPage()
    assert not ocr.OcrPage(text="   ")
    assert ocr.OcrPage(text="x")
    assert ocr.OcrPage(tables=[[["a"]]])


# ------------------------------------------------------------------- helpers

def _mixed():
    """A two-page PDF: page 1 with text, page 2 without."""
    return _pdf_multi([STATEMENT, []])


def _pdf_multi(pages_draws, width=300, height=200):
    """Multi-page sibling of `test_pdf_reader_dedupe._pdf`."""
    objs = []
    n_pages = len(pages_draws)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_pages))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    font_obj = 3 + 2 * n_pages
    for i, draws in enumerate(pages_draws):
        stream = "BT /F1 12 Tf\n" + "".join(
            f"1 0 0 1 {x} {y} Tm ({t}) Tj\n" for t, x, y in draws) + "ET"
        body = stream.encode("latin-1")
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
                     f"/Resources << /Font << /F1 {font_obj} 0 R >> >> "
                     f"/Contents {4 + 2 * i} 0 R >>").encode())
        objs.append(b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n"
                    + body + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)
