"""OCR fallback for pages with no usable text layer.

**Everything here except the transcription itself.** The transcriber is
injected, and the default one returns nothing — so with no configuration this
module is inert and `extract_pdf` behaves exactly as it did before it existed.
That is deliberate: it lets the plumbing, the trigger, the cache and the merge
be tested offline and in CI, where a network call could never run.

Scope, measured before building rather than assumed. Over the 45-deck corpus
(1095 pages) there are 44 pages with no text layer, but they are not evenly
spread: ONE deck (Tucson, `6459 E. Golf Links Road`) is 18 of 18, and outside
it the image-only pages are full-bleed photos, aerials and one vector site plan
— cover ratio ~1.0, zero text objects, near-zero underwriting value. So this is
built cheap on purpose. It is not a second extraction engine; it is a fallback
for the handful of pages the first one cannot see.

**A transcriber that returns only text is close to useless here**, and that is
the single most important thing to know before writing one. `extract/tables.py`
assigns periods to columns BY LIST INDEX (`find_header`, `assign_periods`), and
there is no x0/x1 geometry anywhere in `parser.py` or `tables.py`. A text-only
transcriber will populate the document-wide regex fields and the
location/portfolio/property-name readers, and produce ZERO `FinancialLine`s —
which on a scanned financial statement is the entire point of the exercise. A
transcriber must return row-shaped `tables`.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

#: A page with fewer than this many characters has no usable text layer. It is
#: a floor, not a threshold to tune: a genuine text page in this corpus carries
#: 200-1300 characters, and a scanned page carries the handful that a stamp or
#: a page number contributes.
#:
#: This lives here rather than in `scripts/extraction_report.py`, which now
#: imports it, so that the metric REPORTING image-only pages and the trigger
#: ACTING on them can never drift to two different definitions of the same
#: word.
MIN_CHARS_FOR_TEXT_LAYER = 50

#: Render resolution. 200 DPI is the usual floor for reliable small-type
#: recognition; a CIM financial statement sets its figures small.
RENDER_DPI = 200

#: The long edge never exceeds this, whatever `RENDER_DPI` would give.
#:
#: **DPI alone is the wrong control knob**, and the Tucson deck — the one deck
#: that motivates this whole module — is the proof. Its pages are 1650 x 1275
#: POINTS (a 22.9" plan sheet, not a letter page), so 200 DPI renders them at
#: 4584 x 3542 = 16.24 MP: 4.3x over the vision tier's 3.75 MP ceiling and
#: 9 MB of PNG per page. A fixed DPI makes the pixel count a function of the
#: page's physical size, which is exactly the variable an API with a PIXEL cap
#: does not care about.
#:
#: 2576 px is the documented long-edge maximum of the high-resolution tier
#: (up to ~4784 visual tokens per image). Rendering ABOVE it buys nothing —
#: the image is downsampled server-side regardless — while paying the whole
#: cost in bytes on the wire.
#:
#: The cap is a ceiling, never a floor: a letter page at 200 DPI is 1700 x
#: 2200 and is left exactly there. Upscaling it to the cap would add pixels
#: and no information.
MAX_IMAGE_EDGE = 2576

#: The tier's OTHER documented ceiling — 3.75 MP — and it binds independently.
#:
#: Capping the long edge alone is not enough, which measurement caught and
#: arithmetic would not have: Tucson's 4:3 landscape sheet at a 2576 long edge
#: is 2576 x 1991 = 5.13 MP, inside the edge limit and 37% over the area one.
#: Only a page near 16:9 has both bind at once (2576 x 1456 = 3.75 MP); every
#: squarer page hits the area limit first.
#:
#: **The honest limit this exposes**: an oversized sheet held to 3.75 MP is
#: ~96 effective DPI, and small statement type at 96 DPI is genuinely
#: marginal. That is the tier's ceiling rather than a choice made here; the
#: fix, if the Tucson pages read poorly, is to tile a large page into regions
#: rendered at full resolution — deliberately not built until a measurement
#: says it is needed.
MAX_IMAGE_PIXELS = 3_750_000

#: Encoded-size budget. Above it the page re-encodes as JPEG.
#:
#: SELF-IMPOSED, not a documented API limit — so it is set conservatively
#: rather than tuned to a number nobody here has verified. At the edge cap a
#: scanned page is still ~4 MB of PNG (12 MB of base64 before the cap), and a
#: photograph is what PNG is worst at; the same page is ~0.85 MB at JPEG q90.
#: PNG is TRIED FIRST because a synthetic page — the vector site plan in this
#: same corpus — stays small and crisp losslessly, and JPEG ringing around
#: small type is the one artifact this pipeline cannot afford.
MAX_IMAGE_BYTES = 3_500_000
JPEG_QUALITY = 90

#: Bumped when anything that changes a transcription changes — the render, the
#: prompt, the model. It is part of the cache key, so a bump invalidates
#: cached pages instead of silently serving output the current code would not
#: produce.
#:
#: 2: the edge cap and the JPEG fallback above. No transcription existed to
#: invalidate when they landed (there was no transcriber), but the rule is
#: followed on the rule's own terms — deciding case by case whether a cache
#: "probably" holds anything is how a stale entry gets served.
TRANSCRIBER_VERSION = 2


@dataclass
class OcrPage:
    """What a transcriber returns for one page.

    `tables` is a list of tables, each a list of rows, each row a list of cell
    strings — the same shape `pdfplumber.extract_tables()` produces, because it
    feeds the same `extract/tables.py` column logic. See the module docstring
    on why omitting it makes the whole call close to pointless.
    """

    text: str = ""
    tables: list = field(default_factory=list)

    def __bool__(self) -> bool:
        """Falsy when nothing was transcribed, so callers can test the result
        rather than remembering to compare against an empty instance."""
        return bool(self.text.strip() or self.tables)


#: A transcriber turns rendered page bytes (PNG) into an `OcrPage`.
Transcriber = Callable[[bytes], OcrPage]


def null_transcriber(image: bytes) -> OcrPage:
    """The default: transcribes nothing.

    Not a stub awaiting completion — it is the configuration under which this
    repo runs by default and under which CI always runs. `extract_pdf` with a
    null transcriber returns exactly what it returned before OCR existed.
    """
    return OcrPage()


def ocr_enabled() -> bool:
    """Whether the pipeline should attempt OCR at all.

    Off unless `CIM_OCR_ENABLED` is set to a true-ish value. A paid per-page
    call defaults to off; nobody should discover it by reading a bill.
    """
    return os.environ.get("CIM_OCR_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def needs_ocr(page) -> bool:
    """Whether this page's text layer is too thin to parse.

    Counts `page.chars` — character OBJECTS — rather than the length of
    `extract_text()`, matching how `scripts/extraction_report.py` has always
    measured `image_only_pages`, so the trigger and the metric agree.
    """
    return len(page.chars) < MIN_CHARS_FOR_TEXT_LAYER


def render_scale(page, dpi: int = RENDER_DPI,
                 max_edge: int = MAX_IMAGE_EDGE,
                 max_pixels: int = MAX_IMAGE_PIXELS) -> float:
    """The pdfium render scale for this page: `dpi`, or whatever less it takes
    to satisfy BOTH tier ceilings.

    Split out from `render_page` so the decision can be asserted directly on a
    page's dimensions, without rendering a 16 MP bitmap in a test.

    A page's dimensions are in points; `scale` multiplies both, so the area
    grows as its square — hence a square root rather than a ratio.

    The area term solves for the ROUNDED bitmap, not the ideal one, and that
    is not a nicety: `render()` rounds each side UP, so the exact-fit scale
    lands Tucson at 2203 x 1703 — 1,709 pixels past a ceiling named
    `MAX_IMAGE_PIXELS`. Since `ceil(x) <= x + 1`, requiring
    `(w*s + 1)(h*s + 1) <= max_pixels` is sufficient, and that is a quadratic
    in `s` whose positive root is below.
    """
    natural = dpi / 72
    width, height = float(page.width), float(page.height)
    longest, area = max(width, height), width * height
    if longest <= 0 or area <= 0:
        return natural
    b = width + height
    area_cap = (-b + math.sqrt(b * b + 4 * area * (max_pixels - 1))) / (2 * area)
    return min(natural, max_edge / longest, area_cap)


def render_page(page, dpi: int = RENDER_DPI,
                max_edge: int = MAX_IMAGE_EDGE,
                max_pixels: int = MAX_IMAGE_PIXELS) -> bytes:
    """Render one pdfplumber page to PNG (or JPEG) bytes.

    Uses `pypdfium2`, which arrives as a pdfplumber dependency and is a
    self-contained wheel. `page.to_image()` is NOT an option: it shells out to
    ImageMagick or Ghostscript, and the Render deploy is `runtime: python` with
    a pip-only build command, so no system binary can be installed there.

    The scale is capped at the vision tier's long edge AND its pixel count,
    and the result falls back to JPEG above `MAX_IMAGE_BYTES` — see the three
    constants for the measured reasons. Callers must not assume PNG; the
    format is discoverable from the bytes, which is how `extract.vision` picks
    its `media_type`.
    """
    import io

    import pypdfium2

    pdf = pypdfium2.PdfDocument(page.pdf.stream.name)
    try:
        bitmap = pdf[page.page_number - 1].render(
            scale=render_scale(page, dpi, max_edge, max_pixels))
        image = bitmap.to_pil()
    finally:
        pdf.close()

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    if buf.tell() <= MAX_IMAGE_BYTES:
        return buf.getvalue()

    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


class PageCache:
    """Transcriptions on disk, keyed by document content and page.

    **Not an optimisation — a correctness-of-billing requirement.** `engine.py`
    calls `extract_pdf` twice for a single deal (once in `extract_pdf_data`,
    once in `run_analysis`), so an uncached paid transcription bills every deal
    twice for identical output.

    Keyed on the PDF's own bytes rather than its path: deal folders rename and
    re-upload, and a path-keyed cache would serve one document's text for
    another's. `TRANSCRIBER_VERSION` is in the key so changing the render DPI
    or the prompt invalidates entries instead of silently serving output the
    current code would not produce.
    """

    def __init__(self, directory: Optional[str] = None):
        self.directory = directory or os.environ.get(
            "CIM_OCR_CACHE_DIR", os.path.join(os.path.expanduser("~"), ".cim_ocr_cache")
        )

    def _path(self, doc_digest: str, page_number: int) -> str:
        return os.path.join(
            self.directory, f"{doc_digest}-p{page_number}-v{TRANSCRIBER_VERSION}.json"
        )

    def get(self, doc_digest: str, page_number: int) -> Optional[OcrPage]:
        import json

        try:
            with open(self._path(doc_digest, page_number), encoding="utf-8") as fh:
                blob = json.load(fh)
        except (OSError, ValueError):
            return None
        return OcrPage(text=blob.get("text", ""), tables=blob.get("tables", []))

    def put(self, doc_digest: str, page_number: int, result: OcrPage) -> None:
        import json

        try:
            os.makedirs(self.directory, exist_ok=True)
            with open(self._path(doc_digest, page_number), "w", encoding="utf-8") as fh:
                json.dump({"text": result.text, "tables": result.tables}, fh)
        except OSError:
            # A cache that cannot write is slow and expensive, not broken. The
            # transcription in hand is still correct, so serve it.
            logger.warning("OCR cache unwritable at %s", self.directory)


def document_digest(filepath: str) -> str:
    """sha256 of the file's bytes — the cache's document identity."""
    digest = hashlib.sha256()
    with open(filepath, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def transcribe_page(page, doc_digest: str, transcriber: Transcriber,
                    cache: Optional[PageCache] = None) -> OcrPage:
    """Transcribe one page, serving from cache when possible.

    A cache HIT never renders: rendering at 200 DPI is the expensive half on a
    page the transcriber is not going to be asked about.
    """
    cache = cache if cache is not None else PageCache()

    hit = cache.get(doc_digest, page.page_number)
    if hit is not None:
        return hit

    try:
        result = transcriber(render_page(page))
    except Exception:
        # One unreadable page must not lose the other thirty-one. The page is
        # left as the empty text it already was, and the deck goes on to the
        # parser, where a missing required input is refused by
        # `analysis.fills.require_underwritable` with its own reason.
        logger.exception("OCR failed on page %s", page.page_number)
        return OcrPage()

    if result:
        cache.put(doc_digest, page.page_number, result)
    return result
