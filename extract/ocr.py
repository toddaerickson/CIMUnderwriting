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

#: Bumped when anything that changes a transcription changes — the render DPI,
#: the prompt, the model. It is part of the cache key, so a bump invalidates
#: cached pages instead of silently serving output the current code would not
#: produce.
TRANSCRIBER_VERSION = 1


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


def render_page(page, dpi: int = RENDER_DPI) -> bytes:
    """Render one pdfplumber page to PNG bytes.

    Uses `pypdfium2`, which arrives as a pdfplumber dependency and is a
    self-contained wheel. `page.to_image()` is NOT an option: it shells out to
    ImageMagick or Ghostscript, and the Render deploy is `runtime: python` with
    a pip-only build command, so no system binary can be installed there.
    """
    import io

    import pypdfium2

    pdf = pypdfium2.PdfDocument(page.pdf.stream.name)
    try:
        bitmap = pdf[page.page_number - 1].render(scale=dpi / 72)
        buf = io.BytesIO()
        bitmap.to_pil().save(buf, format="PNG")
        return buf.getvalue()
    finally:
        pdf.close()


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
