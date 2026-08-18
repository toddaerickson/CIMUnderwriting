"""Portfolio / multi-property CIM detection tests.

Cover strings are verbatim from the real CIMs in the repo root (the PDFs are
gitignored, so the text is inlined here to stay CI-runnable — the same
convention test_parser_location.py uses). Three are genuine two-property
portfolios and must be flagged; three are single properties that must NOT be.
"""

import json

import pytest

from extract.portfolio import (
    PortfolioSignal,
    cover_street_addresses,
    is_portfolio,
    portfolio_signal,
)


# ── Real cover text (verbatim from the repo-root PDFs) ─────────────

# 2-property portfolios
WICHITA_COVER = ("People's Choice Storage – Wichita, KS Two Property Portfolio "
                 "3401 North Hillside Street, Wichita, KS 67219 & "
                 "6209 West Kellogg Drive, Wichita, KS 67209 Offering Memorandum")
PREMIER_COVER = ("PREMIER STORAGE TWO PROPERTY PORTFOLIO "
                 "5076 COLUMBUS ROAD SOUTHWEST, GRANVILLE, OH 43023 "
                 "13761 LUCILLE LYND ROAD, NEW ALBANY, OH 43054 OFFERING MEMORANDUM")
BASTROP_COVER = ("Bastrop Guardian Self-Storage, 2.69 acres of Vacant Land and "
                 "Tahitian Village Storage 290 Industrial Boulevard, "
                 "3000 State Hwy 71 and 135 Industry Drive Bastrop, Texas 78602")

# single properties
ROGERS_COVER = ("Self Storage – Rogers, Arkansas "
                "1250 W Commons Drive, $4,800,000 | $71.00/RSF | 67,550 RSF "
                "Rogers, Arkansas 72756 NEWLY EXPANDED | 5.12 ACRES")
DALLAS_COVER = ("US STORAGE CENTERS (MANAGED) OFFERING MEMORANDUM "
                "2601 Willowbrook Road, Dallas, TX 75220 "
                "FSFP Austin, LLC | A Licensed Texas Broker #9013618 "
                "41,610 NRSF | Self-Storage Property")
KERRVILLE_COVER = ("STORWISE SELF-STORAGE OPPORTUNITY KERRVILLE, TX "
                   "SELF-STORAGE INVESTMENT | 66,198 NRSF KERRVILLE, TX")


# ── portfolio_signal against real covers ───────────────────────────

@pytest.mark.parametrize("cover", [WICHITA_COVER, PREMIER_COVER, BASTROP_COVER])
def test_real_portfolio_covers_are_flagged(cover):
    s = portfolio_signal([cover])
    assert s.is_portfolio is True
    assert s.evidence


@pytest.mark.parametrize("cover", [ROGERS_COVER, DALLAS_COVER, KERRVILLE_COVER])
def test_real_single_property_covers_are_not_flagged(cover):
    s = portfolio_signal([cover])
    assert s.is_portfolio is False
    assert s.evidence == []


def test_wichita_flagged_by_wording_and_two_addresses():
    s = portfolio_signal([WICHITA_COVER])
    assert len(s.addresses) == 2
    assert any("wording" in e for e in s.evidence)
    assert any("addresses" in e for e in s.evidence)


def test_premier_flagged_by_wording_two_addresses_and_two_cities():
    s = portfolio_signal([PREMIER_COVER])
    assert set(s.cities) == {"Granville", "New Albany"}
    assert len(s.addresses) == 2
    assert any("cities" in e for e in s.evidence)


def test_bastrop_flagged_by_two_addresses_alone():
    """Same city, no portfolio wording — the multi-address signal is what
    catches it. This is the case PORTFOLIO_RE alone would miss."""
    s = portfolio_signal([BASTROP_COVER])
    assert s.cities == ["Bastrop"]
    assert not any("cities" in e for e in s.evidence)
    assert not any("wording" in e for e in s.evidence)
    assert any("addresses" in e for e in s.evidence)
    assert len(s.addresses) >= 2


# ── unit pieces ────────────────────────────────────────────────────

def test_is_portfolio_matches_offering_wording():
    assert is_portfolio("Two Property Portfolio")
    assert is_portfolio("portfolio of 3 assets")
    assert is_portfolio("multi-site self storage opportunity")


def test_is_portfolio_rejects_single_asset_wording():
    assert not is_portfolio("Self-Storage Investment")
    assert not is_portfolio("Offering Memorandum for one property")


def test_cover_street_addresses_splits_compound_addresses():
    # "3000 State Hwy 71 and 135 Industry Drive" must yield two hits, and a
    # bare parcel phrase ("2.69 acres of Vacant Land") must yield none.
    addrs = cover_street_addresses([BASTROP_COVER])
    assert "3000 State Hwy" in addrs
    assert "135 Industry Drive" in addrs
    assert all("acres" not in a for a in addrs)


def test_recovery_does_not_eat_a_highway_address_house_number():
    """`_recover_address` treats the LAST bare integer in the captured name
    as a bleed-through house number — but in "1200 N Interstate 35 Frontage
    Road" the 35 is the route number inside the street's own name, and
    recovering it returned "35 Frontage Road" with the real house number
    eaten. A route number is one preceded by a highway designator."""
    addrs = cover_street_addresses(
        ["1200 N Interstate 35 Frontage Road Suite 100\nRound Rock, TX"])
    assert addrs == ["1200 N Interstate 35 Frontage Road"]


def test_recovery_still_fires_on_zip_bleed_through():
    # The case recovery exists for — unchanged by the highway guard.
    addrs = cover_street_addresses(["43023 13761 LUCILLE LYND ROAD"])
    assert addrs == ["13761 LUCILLE LYND ROAD"]


def test_recovery_composes_zip_bleed_with_a_highway_name():
    """A ZIP bleeding into a highway address: the sweep steps past the
    route number (highway-designated) and recovers the real house number
    behind it."""
    addrs = cover_street_addresses(
        ["43023 1200 N Interstate 35 Frontage Road"])
    assert addrs == ["1200 N Interstate 35 Frontage Road"]


def test_cover_street_addresses_suppresses_broker_blocks():
    # Subject address at the top, broker signature block far below (>110
    # chars, as on real covers) — the subject must survive and the broker's
    # own address must not add a false second property.
    cover = ("Subject Storage, 101 Main Street, Austin, TX 78701. "
             "This confidential offering memorandum is delivered solely for "
             "your use in evaluating the possible acquisition of the subject "
             "property and may not be reproduced. Presented by Marcus & "
             "Millichap, 420 Lexington Avenue, New York, NY 10170.")
    assert "101 Main Street" in cover_street_addresses([cover])
    assert "420 Lexington Avenue" not in cover_street_addresses([cover])


def test_single_cover_address_is_not_a_portfolio():
    assert len(cover_street_addresses([ROGERS_COVER])) == 1
    assert not portfolio_signal([ROGERS_COVER]).is_portfolio


def test_comp_in_other_city_in_body_is_ignored():
    """A single-asset CIM lists comps in other cities in the BODY; only the
    cover page is scanned, so this must not flag."""
    cover = "Expo Storage, 101 Main St, Belton, TX 76513"
    body = ("Subject comps: Waco, TX 76701; Killeen, TX 76541; "
            "Georgetown, TX 78626; Temple, TX 76502")
    assert portfolio_signal([cover, body]).is_portfolio is False


def test_signal_as_dict_is_json_round_trippable():
    s = portfolio_signal([WICHITA_COVER])
    d = s.as_dict()
    assert json.loads(json.dumps(d))["is_portfolio"] is True
    assert s == PortfolioSignal(**d)



# ── parse_cim integration ──────────────────────────────────────────

def test_parse_cim_sets_portfolio_signal_on_a_portfolio():
    from extract.parser import parse_cim
    raw = {"text": WICHITA_COVER, "tables": [],
           "pages": [WICHITA_COVER, "financial pages"]}
    data = parse_cim(raw)
    assert data.portfolio_signal is not None
    assert data.portfolio_signal["is_portfolio"] is True


def test_parse_cim_leaves_portfolio_signal_none_for_single_asset():
    from extract.parser import parse_cim
    raw = {"text": DALLAS_COVER, "tables": [],
           "pages": [DALLAS_COVER, "financial pages"]}
    assert parse_cim(raw).portfolio_signal is None


def test_portfolio_signal_survives_cim_dict_round_trip():
    from extract.parser import parse_cim
    from webapp.services import cim_from_dict, cim_to_dict
    raw = {"text": WICHITA_COVER, "tables": [],
           "pages": [WICHITA_COVER]}
    data = parse_cim(raw)
    restored = cim_from_dict(json.loads(json.dumps(cim_to_dict(data))))
    assert restored.portfolio_signal is not None
    assert restored.portfolio_signal["is_portfolio"] is True


def test_extraction_report_is_unchanged_by_portfolio_detection():
    """portfolio_signal must not move confidence or pad Missing fields."""
    from extract.parser import CIMData
    plain = CIMData(property_name="X", nrsf=1000.0)
    sig = CIMData(property_name="X", nrsf=1000.0,
                  portfolio_signal={"is_portfolio": True, "evidence": ["x"]})
    assert plain.extraction_report() == sig.extraction_report()


# ── The one warning sentence, on every analysis surface ────────────

_EVIDENCE = ["2 distinct property addresses on the cover page"]
_SIGNAL = {"is_portfolio": True, "evidence": _EVIDENCE}


def test_warning_text_is_ascii_with_and_without_evidence():
    """The memo feeds it to python-docx and the summary budget measures
    ASCII-folded text — a stray en-dash would be the drift this shared
    definition exists to prevent."""
    from extract.portfolio import warning_text
    bare = warning_text()
    with_ev = warning_text(_EVIDENCE)
    assert bare.startswith("Possible multi-property / portfolio CIM")
    assert _EVIDENCE[0] in with_ev
    assert bare.isascii() and with_ev.isascii()


def test_run_analysis_carries_the_portfolio_warning(
        tmp_path, monkeypatch, mock_cim_data):
    """result.errors flows to run_warnings on the results page and into
    every stored AnalysisRun — the run's outputs must be as loud as the
    assumptions page."""
    monkeypatch.setattr("data.comp_db.COMP_DB_PATH", str(tmp_path / "c.db"))
    from engine import AnalysisResult, run_analysis
    from extract.portfolio import warning_text

    mock_cim_data.portfolio_signal = dict(_SIGNAL)
    result = AnalysisResult(pdf_path=str(tmp_path / "x.pdf"))
    result.cim_data = mock_cim_data
    run_analysis(result, output_dir=str(tmp_path))

    assert warning_text(_EVIDENCE) in result.errors


def test_run_analysis_appends_no_warning_for_a_single_asset(
        tmp_path, monkeypatch, mock_cim_data):
    # The sentinel is the warning SENTENCE, not the bare word: unrelated
    # errors can carry "portfolio" in a file path (a worktree named
    # portfolio-ship did exactly that) or in a property name.
    monkeypatch.setattr("data.comp_db.COMP_DB_PATH", str(tmp_path / "c.db"))
    from engine import AnalysisResult, run_analysis

    result = AnalysisResult(pdf_path=str(tmp_path / "x.pdf"))
    result.cim_data = mock_cim_data
    run_analysis(result, output_dir=str(tmp_path))

    assert not any("multi-property / portfolio CIM" in e
                   for e in result.errors)


def test_extract_path_does_not_double_carry_the_warning(monkeypatch, tmp_path):
    """The extract path surfaces the flag through Deal.portfolio_suspect and
    the dedicated assumptions-page banner. result.errors also feeds the
    GENERIC warnings box on that same page, so an append here rendered the
    caveat twice — the flag must arrive on the signal alone."""
    import engine

    raw = {"text": WICHITA_COVER, "tables": [], "pages": [WICHITA_COVER]}
    monkeypatch.setattr("data.comp_db.COMP_DB_PATH", str(tmp_path / "c.db"))
    # `transcriber` is accepted and ignored: the engine now resolves one from
    # the environment and passes it, and this test is about the portfolio
    # warning rather than the reader's signature.
    monkeypatch.setattr("extract.pdf_reader.extract_pdf",
                        lambda p, transcriber=None: raw)
    monkeypatch.setattr(
        "extract.enrichment.enrich_cim_data",
        lambda cim, comp_db=None: type("E", (), {"errors": []})())
    monkeypatch.setattr(
        "extract.rent_survey.run_rent_survey",
        lambda **kw: type("S", (), {"success": False,
                                    "market_rent_per_sf_mo": None})())

    result = engine.extract_pdf_data(str(tmp_path / "wichita.pdf"))

    assert result.cim_data.portfolio_signal["is_portfolio"] is True
    assert not any("multi-property / portfolio CIM" in e
                   for e in result.errors)


# ── The IC memo caveat ─────────────────────────────────────────────

def _memo_paragraphs(tmp_path, cim_data):
    from docx import Document
    from output.memo_writer import generate_memo
    path = generate_memo(
        property_name="X", cim_data=cim_data, gate_results=[],
        market_analysis={}, physical_analysis={}, financial_analysis={},
        rent_analysis={}, scenario_results={}, value_add={}, risk_analysis={},
        max_offer={}, output_dir=str(tmp_path))
    return [p.text for p in Document(path).paragraphs]


def test_memo_carries_the_portfolio_caveat_before_section_1(
        tmp_path, mock_cim_data):
    from extract.portfolio import warning_text

    mock_cim_data.portfolio_signal = dict(_SIGNAL)
    paras = _memo_paragraphs(tmp_path, mock_cim_data)

    caveat = next(i for i, t in enumerate(paras) if "PORTFOLIO CIM" in t)
    # THE shared sentence, verbatim — not a paraphrase that can drift.
    assert warning_text() in paras[caveat]
    # Before the first number: the analyst-facing evidence renders, and the
    # whole block precedes section 1's heading.
    assert any(_EVIDENCE[0] in t for t in paras)
    section_1 = next(i for i, t in enumerate(paras)
                     if t.startswith("1. Investment Summary"))
    assert caveat < section_1


def test_memo_has_no_portfolio_caveat_for_a_single_asset(
        tmp_path, mock_cim_data):
    paras = _memo_paragraphs(tmp_path, mock_cim_data)
    assert not any("PORTFOLIO CIM" in t for t in paras)
