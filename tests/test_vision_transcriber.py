"""The Claude vision transcriber, offline.

Every test here stubs the client — `ClaudeTranscriber` takes one by injection
precisely so the network is unreachable from the suite. Nothing in this file
constructs `anthropic.Anthropic`, and the one test that asserts the request
shape reads it off a recording stub rather than a wire capture.

The subject is not "does the model transcribe well" — that is not decidable in
CI and belongs to the corpus run. It is the two things around the call that
decide whether a WRONG number can reach the model: the shape the request asks
for, and which responses are refused.
"""

import json

import pytest

from extract import ocr, vision


# ── Stubs ───────────────────────────────────────────────────────────

class _Block:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class _Response:
    def __init__(self, content=(), stop_reason="end_turn", stop_details=None):
        self.content = list(content)
        self.stop_reason = stop_reason
        self.stop_details = stop_details


class _Messages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _Client:
    def __init__(self, response):
        self.messages = _Messages(response)


def _json_response(payload, **kwargs):
    """A response in the shape `output_config.format` guarantees — a thinking
    block first, because Opus 5 thinks by default, then the JSON text."""
    return _Response(
        content=[_Block("thinking", ""), _Block("text", json.dumps(payload))],
        **kwargs)


STATEMENT = {
    "text": "OPERATING STATEMENT\nRental Income 1,009,440 1,048,120",
    "tables": [[["", "2024", "T-12"],
                ["Rental Income", "1,009,440", "1,048,120"],
                ["Total Expenses", "(248,740)", "(251,010)"]]],
}


# ── The request ─────────────────────────────────────────────────────

def test_the_page_is_sent_as_a_base64_png_beside_the_prompt():
    client = _Client(_json_response(STATEMENT))
    vision.ClaudeTranscriber(client=client)(b"\x89PNG-not-really")

    content = client.messages.calls[0]["messages"][0]["content"]
    image, text = content[0], content[1]
    assert image["source"]["media_type"] == "image/png"
    assert image["source"]["type"] == "base64"
    # Decodes back to exactly the bytes handed in — a transcoding bug here
    # would send the model a different page than the one that needed OCR.
    import base64
    assert base64.standard_b64decode(image["source"]["data"]) == b"\x89PNG-not-really"
    assert text["text"] == vision.PROMPT


def test_a_jpeg_page_declares_itself_a_jpeg():
    """`ocr.render_page` falls back to JPEG on an oversized scan, so a
    hard-coded `image/png` would mislabel exactly the pages this module
    exists for."""
    client = _Client(_json_response(STATEMENT))
    vision.ClaudeTranscriber(client=client)(b"\xff\xd8\xff\xe0 jpeg body")
    source = client.messages.calls[0]["messages"][0]["content"][0]["source"]
    assert source["media_type"] == "image/jpeg"


def test_the_request_pins_the_row_shaped_schema():
    """The single most load-bearing line in the module.

    `extract/tables.py` matches a column to its statement period BY LIST
    INDEX, so the schema — not the prose — is what makes the response
    indexable. If this degrades to a free-text format the transcriber still
    "works" and yields zero FinancialLines.
    """
    client = _Client(_json_response(STATEMENT))
    vision.ClaudeTranscriber(client=client)(b"png")

    fmt = client.messages.calls[0]["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    tables = fmt["schema"]["properties"]["tables"]
    assert tables["type"] == "array"                      # tables
    assert tables["items"]["type"] == "array"             # rows
    assert tables["items"]["items"]["type"] == "array"    # cells
    assert tables["items"]["items"]["items"]["type"] == "string"


def test_no_budget_tokens_is_sent():
    """`budget_tokens` is a 400 on Opus 5, where thinking is on by default.
    Sending it would fail every page — and `transcribe_page` swallows the
    exception, so the failure would surface as a silently blank deck."""
    client = _Client(_json_response(STATEMENT))
    vision.ClaudeTranscriber(client=client)(b"png")

    kwargs = client.messages.calls[0]
    assert "thinking" not in kwargs or "budget_tokens" not in kwargs["thinking"]
    assert kwargs["model"] == vision.DEFAULT_MODEL


def test_the_model_is_overridable_from_the_environment(monkeypatch):
    monkeypatch.setenv("CIM_OCR_MODEL", "claude-sonnet-5")
    client = _Client(_json_response(STATEMENT))
    vision.ClaudeTranscriber(client=client)(b"png")
    assert client.messages.calls[0]["model"] == "claude-sonnet-5"


# ── The response ────────────────────────────────────────────────────

def test_a_transcription_comes_back_as_text_and_row_shaped_tables():
    client = _Client(_json_response(STATEMENT))
    page = vision.ClaudeTranscriber(client=client)(b"png")

    assert page                       # truthy — something was transcribed
    assert "Rental Income" in page.text
    assert page.tables == STATEMENT["tables"]
    # Every row the same width as the header, which is the property the
    # positional column matching downstream depends on.
    widths = {len(row) for row in page.tables[0]}
    assert widths == {3}


def test_the_json_is_read_past_the_thinking_block():
    """Opus 5 thinks by default, so `content[0]` is a thinking block. Taking
    the first block instead of the first TEXT block would fail on every real
    response while passing any stub that omits thinking."""
    client = _Client(_Response(
        content=[_Block("thinking", "considering the columns"),
                 _Block("text", json.dumps(STATEMENT))]))
    assert vision.ClaudeTranscriber(client=client)(b"png").tables


def test_a_refusal_transcribes_nothing():
    client = _Client(_json_response(STATEMENT, stop_reason="refusal",
                                    stop_details={"type": "safety"}))
    page = vision.ClaudeTranscriber(client=client)(b"png")
    assert not page
    assert page.tables == []


def test_a_truncated_response_is_refused_rather_than_served():
    """The sharpest of the refusal paths.

    A page cut off at `max_tokens` yields SOME rows and drops the rest, and a
    partial statement reads downstream as a complete statement that happens to
    be missing lines — no check fires, and a real NOI gets computed off half a
    page. Refusing leaves the page in the state it was already in: empty.
    """
    truncated = {"text": "OPERATING STATEMENT\nRental Income 1,009,440",
                 "tables": [[["", "2024"], ["Rental Income", "1,009,440"]]]}
    client = _Client(_json_response(truncated, stop_reason="max_tokens"))
    page = vision.ClaudeTranscriber(client=client)(b"png")

    assert not page
    assert page.text == ""
    assert page.tables == []


def test_unparseable_json_transcribes_nothing():
    client = _Client(_Response(content=[_Block("text", "{not json")]))
    assert not vision.ClaudeTranscriber(client=client)(b"png")


def test_a_response_with_no_text_block_transcribes_nothing():
    client = _Client(_Response(content=[_Block("thinking", "hmm")]))
    assert not vision.ClaudeTranscriber(client=client)(b"png")


@pytest.mark.parametrize("malformed", [
    {"tables": "not a list"},
    {"tables": [None, 7, "rows"]},
    {"tables": [["a row is a list of cells", ["ok"]]]},
])
def test_malformed_tables_are_dropped_not_raised(malformed):
    """The value crosses a process boundary and is then INDEXED positionally
    three modules away. A shape error has to die here, where the reason is
    legible, not as an IndexError inside period assignment."""
    payload = {"text": "some text", **malformed}
    client = _Client(_json_response(payload))
    page = vision.ClaudeTranscriber(client=client)(b"png")

    assert page.text == "some text"
    for table in page.tables:
        assert isinstance(table, list)
        for row in table:
            assert isinstance(row, list)
            assert all(isinstance(cell, str) for cell in row)


def test_cells_are_stringified():
    """`pdf_reader._clean_table` calls `.strip()` on every cell, so a numeric
    cell that survived as an int would raise there rather than here."""
    client = _Client(_json_response(
        {"text": "t", "tables": [[["Rental Income", 1009440, None]]]}))
    assert vision.ClaudeTranscriber(client=client)(b"png").tables == [
        [["Rental Income", "1009440", ""]]]


def test_an_api_error_propagates_to_the_plumbings_own_handler():
    """`vision` does not catch transport errors, deliberately —
    `ocr.transcribe_page` already turns any exception into an empty page with
    a logged traceback, and a second handler here would either duplicate that
    or swallow the traceback before it was written."""
    client = _Client(RuntimeError("connection reset"))
    with pytest.raises(RuntimeError):
        vision.ClaudeTranscriber(client=client)(b"png")


def test_the_plumbing_turns_that_error_into_an_empty_page(caplog):
    """The other half of the test above, asserted through the real seam
    rather than assumed from its docstring."""
    class _Page:
        page_number = 4

    client = _Client(RuntimeError("connection reset"))
    transcriber = vision.ClaudeTranscriber(client=client)

    class _NoCache:
        def get(self, *a):
            return None

        def put(self, *a):
            raise AssertionError("a failed page must never be cached")

    result = ocr.transcribe_page(_Page(), "digest", transcriber, _NoCache())
    assert not result


# ── Wiring ──────────────────────────────────────────────────────────

def test_no_transcriber_when_ocr_is_off(monkeypatch):
    monkeypatch.delenv("CIM_OCR_ENABLED", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert vision.transcriber_from_env() is None


def test_no_transcriber_when_the_key_is_absent(monkeypatch, caplog):
    """Configured half-way is the dangerous state: OCR was asked for and the
    deck will parse as if its scanned pages were blank. It must be loud."""
    monkeypatch.setenv("CIM_OCR_ENABLED", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with caplog.at_level("WARNING"):
        assert vision.transcriber_from_env() is None
    assert "ANTHROPIC_API_KEY" in caplog.text


def test_a_transcriber_when_both_are_set(monkeypatch):
    monkeypatch.setenv("CIM_OCR_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert isinstance(vision.transcriber_from_env(), vision.ClaudeTranscriber)


def test_the_client_is_never_constructed_until_a_page_is_sent(monkeypatch):
    """A key is read and a connection pool built only when a page actually
    needs transcribing. `import anthropic` is inside the property for the same
    reason — the module must import on a box that has never installed it."""
    monkeypatch.setenv("CIM_OCR_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def _explode():
        raise AssertionError("client constructed too early")

    monkeypatch.setattr(vision.ClaudeTranscriber, "client",
                        property(lambda self: _explode()))
    vision.transcriber_from_env()   # constructing the transcriber is enough


def test_an_injected_client_stands_in_for_a_missing_key(monkeypatch):
    """So a caller holding its own configured client is not blocked by an
    env var it has already satisfied another way."""
    monkeypatch.setenv("CIM_OCR_ENABLED", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = _Client(_json_response(STATEMENT))
    assert isinstance(vision.transcriber_from_env(client=client),
                      vision.ClaudeTranscriber)


# ── Disclosure ──────────────────────────────────────────────────────

def test_a_transcribed_page_reaches_the_assumption_register():
    """Design decision 11: a `cim` provenance must not silently cover a
    figure that was machine-read off a scan."""
    from analysis import assumptions as A
    from extract.parser import CIMData

    cim = CIMData(nrsf=60000, ocr_pages=[3, 7])
    rows = A.collect(cim_data=cim)

    row = next(r for r in rows if r.key == "cim.ocr_pages")
    assert row.value == 2
    assert "3, 7" in row.detail
    assert row.provenance == A.CIM


def test_no_register_row_when_nothing_was_transcribed():
    from analysis import assumptions as A
    from extract.parser import CIMData

    rows = A.collect(cim_data=CIMData(nrsf=60000))
    assert not [r for r in rows if r.key == "cim.ocr_pages"]


def test_the_register_row_survives_a_json_round_trip():
    """It is stored in `AnalysisRun.result_json` and re-read by every surface
    that renders a stored run."""
    from analysis import assumptions as A
    from extract.parser import CIMData

    rows = A.collect(cim_data=CIMData(nrsf=60000, ocr_pages=[3]))
    back = A.from_dicts(json.loads(json.dumps(A.to_dicts(rows))))
    assert next(r for r in back if r.key == "cim.ocr_pages").value == 1


def test_ocr_pages_do_not_move_the_extraction_confidence():
    """It is metadata ABOUT extraction, like `unmapped_financial_lines` and
    `portfolio_signal`. Counting it would report a clean parse as one more
    missing field on every deck that has a text layer."""
    from extract.parser import CIMData

    clean = CIMData(nrsf=60000).extraction_report()
    scanned = CIMData(nrsf=60000, ocr_pages=[1, 2, 3]).extraction_report()
    assert clean["total_fields"] == scanned["total_fields"]
    assert clean["confidence_pct"] == scanned["confidence_pct"]
    assert "ocr_pages" not in clean["missing"]


def test_the_parser_carries_ocr_pages_off_the_reader():
    from extract.parser import parse_cim

    data = parse_cim({"text": "", "tables": [], "pages": [],
                      "page_count": 1, "ocr_pages": [4]})
    assert data.ocr_pages == [4]


def test_a_raw_dict_without_the_key_still_parses():
    """`extract_pdf` always supplies it, but `parse_cim` is called with
    hand-built dicts throughout the suite and by the manual-fill path in
    CLAUDE.md's 'When the user provides a CIM PDF'."""
    from extract.parser import parse_cim

    assert parse_cim({"text": "", "tables": [], "pages": [],
                      "page_count": 1}).ocr_pages == []
