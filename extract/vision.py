"""The transcriber `extract/ocr.py` left injected — Claude vision.

`ocr.py` shipped everything around the transcription: the trigger, the
render, the cache, the merge, and a `null_transcriber` that returns nothing.
This module is the one piece it deliberately omitted, and it stays a separate
module for the reason the injection exists at all — every test in
`tests/test_ocr_plumbing.py` runs the plumbing with a fake, and nothing here
is importable into that path by accident.

## Why a vision model rather than a local OCR engine

Not a preference — a deploy constraint. `render.yaml` is `runtime: python`
with a pip-only `buildCommand`, so tesseract/ocrmypdf would force a Dockerfile
migration, and the `starter` plan's 512 MB (already running gunicorn at
2 workers x 4 threads) is a poor host for onnxruntime's ~300 MB resident.

The deciding reason is not the packaging, though. **The hard problem is column
attribution, and a word-box engine does not solve it.** `extract/tables.py`
assigns statement periods to columns BY LIST INDEX (`find_header`,
`assign_periods`) and no x0/x1 geometry reaches `parser.py`, so a transcriber
that returns a text blob populates the document-wide regex fields and produces
ZERO `FinancialLine`s. Row-shaped tables are the entire deliverable; see the
`extract/ocr.py` module docstring, which says the same thing from the other
side.

## This transcribes. It does not extract.

The output feeds the existing deterministic parser, so a value read off a
transcribed page keeps provenance `cim` — the model is standing in for the
PDF's missing text layer, not deciding what a number means. That is why the
prompt forbids interpreting, correcting and computing, and why an unreadable
glyph is `[illegible]` rather than a plausible digit: the parser series' bar
is 0 WRONG / 0 hallucinated, and OCR must not be the thing that reintroduces
either. An `[illegible]` in a numeric cell fails to parse into a
`FinancialLine`, which is the designed refusal.

A truncated response is refused for the same reason and it is the sharper
case: a half-transcribed statement yields SOME rows and drops others silently,
which reads downstream as a complete statement that is simply missing lines.
`stop_reason == "max_tokens"` therefore returns nothing at all.
"""

from __future__ import annotations

import base64
import json
import logging
import os

from extract.ocr import OcrPage, ocr_enabled

logger = logging.getLogger(__name__)

#: Read at call time from `CIM_OCR_MODEL` so a model can be pinned per
#: deployment without a redeploy of this file. `TRANSCRIBER_VERSION` in
#: `ocr.py` is what invalidates the cache when the answer changes — bump it
#: alongside any change here that would change a transcription.
DEFAULT_MODEL = "claude-opus-5"

#: One rendered page at 200 DPI. A dense financial statement transcribes to
#: roughly 2-4k tokens, so this is headroom rather than a target — and
#: because truncation is REFUSED rather than truncated-and-served (see the
#: module docstring), a ceiling set too low costs a page, not a wrong number.
MAX_TOKENS = 8000

#: The shape `pdfplumber.extract_tables()` returns: a list of tables, each a
#: list of rows, each a list of cell strings. It is declared as a schema
#: rather than requested in prose because `extract/tables.py` indexes into
#: these lists positionally — a row one cell short silently shifts every
#: period assignment after it.
TRANSCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": (
                "The page's full visible text, verbatim, in reading order, "
                "including the text of any tables."),
        },
        "tables": {
            "type": "array",
            "description": "Each table on the page, as rows of cells.",
            "items": {
                "type": "array",
                "items": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "required": ["text", "tables"],
    "additionalProperties": False,
}

PROMPT = """\
This is one page of a commercial real-estate offering memorandum, rendered as \
an image because the PDF carries no text layer for it. Transcribe it.

You are a transcriber, not an analyst. Do not interpret, summarise, correct, \
reconcile or compute anything. If the page prints a total that does not add \
up, transcribe the total that is printed.

`text`: every visible word and number on the page, in reading order, exactly \
as printed — including the contents of tables. Keep the printed form of \
numbers: currency symbols, thousands separators, decimals, percent signs, \
parentheses for negatives, and minus signs all stay as they appear.

`tables`: every table on the page, separately, as a list of rows.
- One entry per PRINTED row, in top-to-bottom order.
- One cell per PRINTED column, in left-to-right order. A cell that is blank \
in that row is the empty string — never omit it, never shift a value left \
into a neighbouring column.
- EVERY row of a table must have exactly the same number of cells as that \
table's widest row, including the header row. Downstream code matches a \
column to its statement period by POSITION, so one short row misreads every \
figure after it.
- Include the header row, with the period labels as printed \
("2024", "T-12", "Trailing 12", "Pro Forma", "Year 1", ...). A column with \
no printed header is the empty string.
- Do not invent a total row, a subtotal, or a column that is not printed.
- A page with no tabular content returns an empty list.

If a character or a figure is not legible with confidence, write \
`[illegible]` in its place. Never guess a digit. A refused figure is handled \
correctly downstream; a wrong one is not."""


class ClaudeTranscriber:
    """Transcribes one rendered page via the Messages API.

    The client is injectable and constructed lazily. Both matter: tests hand
    in a fake and never import `anthropic` at all, and a process that has this
    module imported but OCR switched off never builds a client or reads a key.
    """

    def __init__(self, client=None, model: str | None = None,
                 max_tokens: int = MAX_TOKENS):
        self._client = client
        self.model = model or os.environ.get("CIM_OCR_MODEL") or DEFAULT_MODEL
        self.max_tokens = max_tokens

    @property
    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def __call__(self, image: bytes) -> OcrPage:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            # Thinking is on by default on Opus 5 and `budget_tokens` is a
            # 400 there, so it is left unset rather than configured. `effort`
            # is raised because column attribution on a scanned statement is
            # the genuinely hard part of this task, and the volume is tiny —
            # 44 pages across the whole 45-deck corpus, 18 of them one deck.
            output_config={
                "effort": "high",
                "format": {"type": "json_schema",
                           "schema": TRANSCRIPTION_SCHEMA},
            },
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "base64",
                                "media_type": _media_type(image),
                                "data": base64.standard_b64encode(image).decode()}},
                    {"type": "text", "text": PROMPT},
                ],
            }],
        )
        return self._read(response)

    def _read(self, response) -> OcrPage:
        """The response, or nothing — never a partial page.

        Both refusal paths return an empty `OcrPage`, which is the state the
        page was already in: no text layer. `ocr.transcribe_page` does not
        cache a falsy result, so neither outcome is remembered as an answer.
        """
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            logger.warning("OCR transcription refused: %s",
                           getattr(response, "stop_details", None))
            return OcrPage()
        if stop == "max_tokens":
            # See the module docstring: a truncated statement reads
            # downstream as a complete one that is missing lines.
            logger.warning(
                "OCR transcription hit max_tokens (%s); refusing the page "
                "rather than serving a partial transcription", self.max_tokens)
            return OcrPage()

        blob = _first_json(response)
        if blob is None:
            logger.warning("OCR transcription returned no JSON text block")
            return OcrPage()
        return OcrPage(text=blob.get("text") or "",
                       tables=_clean_tables(blob.get("tables")))


#: Magic numbers, because `ocr.render_page` returns PNG for most pages and
#: JPEG for the oversized scans that blow the size budget. Declaring the
#: media_type from the BYTES rather than from a second return value keeps
#: `Transcriber = Callable[[bytes], OcrPage]` intact — that one-argument shape
#: is the whole injection contract `extract/ocr.py` is built on.
_MEDIA_TYPES = ((b"\x89PNG\r\n\x1a\n", "image/png"),
                (b"\xff\xd8\xff", "image/jpeg"))


def _media_type(image: bytes) -> str:
    for magic, media_type in _MEDIA_TYPES:
        if image.startswith(magic):
            return media_type
    # Nothing else is produced today. Naming PNG rather than raising keeps a
    # future encoder from failing the page at the transcriber instead of at
    # the API, where the error says what was actually wrong.
    return "image/png"


def _first_json(response):
    """The first text block, parsed. `output_config.format` guarantees the
    JSON, but a thinking block precedes it — so this selects by type rather
    than taking `content[0]`."""
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "text":
            try:
                return json.loads(block.text)
            except (TypeError, ValueError):
                return None
    return None


def _clean_tables(tables) -> list:
    """Coerce to the exact shape `extract/tables.py` indexes into.

    The schema constrains this, but the value crosses a process boundary and
    then gets indexed positionally, so a malformed table is dropped here
    rather than raising three modules downstream. Cells are stringified
    because `_clean_table` in `pdf_reader` calls `.strip()` on them.
    """
    out = []
    for table in tables or []:
        if not isinstance(table, list):
            continue
        rows = [[("" if cell is None else str(cell)) for cell in row]
                for row in table if isinstance(row, list)]
        if rows:
            out.append(rows)
    return out


def transcriber_from_env(client=None):
    """The configured transcriber, or None — which means "no OCR".

    `None` is `extract_pdf`'s own "do not transcribe" value, so an
    unconfigured deployment takes exactly the path it took before this module
    existed.

    Resolution lives HERE and at the two call sites rather than inside
    `extract_pdf`, deliberately. Making `transcriber=None` mean "resolve from
    the environment" would let any `extract_pdf` test on a machine that
    happens to export `ANTHROPIC_API_KEY` place a paid call; two explicit call
    sites are the cheaper price for that guarantee.
    """
    if not ocr_enabled():
        return None
    if not (os.environ.get("ANTHROPIC_API_KEY") or client):
        # Asked for and not configured. Loud, because the alternative is a
        # deck that quietly parses as if its scanned pages were blank.
        logger.warning(
            "CIM_OCR_ENABLED is set but ANTHROPIC_API_KEY is not; pages with "
            "no text layer will be left empty")
        return None
    return ClaudeTranscriber(client=client)
