"""Tests for the optional AI extraction gap-filler (item B1).

The network is never touched: every test either mocks `ai_fill._call_llm`
(the single completion seam) or monkeypatches `ai_fill_missing` itself. The
safety invariants these pin are the whole reason the feature is allowed to
exist — the required-field exclusion, canonical-unit coercion, and silent
degradation.
"""
import json

import pytest

import config as cfg
from extract import ai_fill
from extract.ai_fill import AI_EXTRACTABLE_FIELDS, ai_fill_missing


# ── BLOCKER 1: required underwriting fields are never AI-fillable ────
def test_ai_never_touches_required_underwriting_fields():
    """The exclusion `require_underwritable` and the red required-flag rely on.

    If any of these became AI-fillable, an LLM value would satisfy the
    refusal and strip the assumptions page's required flag — re-opening the
    hole design decision #9 closed.
    """
    from webapp.forms import REQUIRED_FIELDS
    from analysis.fills import REQUIRED_UNDERWRITING_FIELDS

    fillable = set(AI_EXTRACTABLE_FIELDS)
    assert fillable & REQUIRED_FIELDS == set(), (
        "AI-fillable fields overlap the run-gating REQUIRED_FIELDS: "
        f"{sorted(fillable & REQUIRED_FIELDS)}")

    refusal_attrs = {attr for _label, attr, _fn in REQUIRED_UNDERWRITING_FIELDS}
    assert fillable & refusal_attrs == set(), (
        "AI-fillable fields overlap require_underwritable's inputs: "
        f"{sorted(fillable & refusal_attrs)}")


def test_ai_excludes_list_and_analyst_only_fields():
    fillable = set(AI_EXTRACTABLE_FIELDS)
    # list/table fields (v1 excludes structured extraction)
    assert not (fillable & {"unit_mix", "income_lines", "expense_lines",
                            "comp_data"})
    # analyst-only screening inputs must not be machine-guessed
    assert not (fillable & {"market_verification", "competitive_supply_sf_3mi",
                            "pipeline_supply_sf_3mi", "street_rate_trend",
                            "in_place_avg_rent_psf"})


# ── coercion + canonical-unit bounds (BLOCKER 3) ─────────────────────
def _run(monkeypatch, returned, missing, key="k", text="Storage facility."):
    monkeypatch.setattr(cfg, "DEEPSEEK_API_KEY", key)
    monkeypatch.setattr(ai_fill, "_call_llm",
                        lambda system, user: json.dumps(returned))
    return ai_fill_missing({"text": text}, missing)


def test_occupancy_eightyfive_is_rejected_not_stored_as_8500pct(monkeypatch):
    # A model answering "85" for 85% is ambiguous, not off-by-100: refuse it.
    values, err = _run(monkeypatch, {"economic_occupancy": 85},
                       ["economic_occupancy"])
    assert err is None
    assert "economic_occupancy" not in values


def test_valid_fraction_is_kept(monkeypatch):
    values, _ = _run(monkeypatch, {"economic_occupancy": 0.88},
                     ["economic_occupancy"])
    assert values["economic_occupancy"] == 0.88


def test_out_of_bounds_value_is_dropped(monkeypatch):
    # year_built far in the future is nonsense → dropped, field stays missing.
    values, _ = _run(monkeypatch, {"year_built": 3500}, ["year_built"])
    assert "year_built" not in values


def test_in_bounds_dollars_kept(monkeypatch):
    values, _ = _run(monkeypatch, {"cim_yr1_noi": 640000}, ["cim_yr1_noi"])
    assert values["cim_yr1_noi"] == 640000.0


def test_null_from_model_leaves_field_missing(monkeypatch):
    values, err = _run(monkeypatch, {"msa": None}, ["msa"])
    assert err is None and "msa" not in values


def test_only_requested_and_allowlisted_fields_returned(monkeypatch):
    # A field not in `missing` and a field not in the allowlist are ignored.
    values, _ = _run(monkeypatch,
                     {"msa": "Dallas-Fort Worth, TX", "nrsf": 50000,
                      "unit_mix": [{"count": 1}]},
                     ["msa"])
    assert set(values) == {"msa"}


# ── degrade silently (BLOCKER 2 tail: never raise) ───────────────────
def test_client_exception_returns_empty_and_never_raises(monkeypatch):
    monkeypatch.setattr(cfg, "DEEPSEEK_API_KEY", "k")

    def boom(system, user):
        raise TimeoutError("socket hung")

    monkeypatch.setattr(ai_fill, "_call_llm", boom)
    values, err = ai_fill_missing({"text": "x"}, ["msa"])
    assert values == {} and "TimeoutError" in err


def test_bad_json_returns_empty(monkeypatch):
    monkeypatch.setattr(cfg, "DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(ai_fill, "_call_llm", lambda s, u: "not json {")
    values, err = ai_fill_missing({"text": "x"}, ["msa"])
    assert values == {} and err


def test_no_key_skips_without_calling(monkeypatch):
    monkeypatch.setattr(cfg, "DEEPSEEK_API_KEY", "")
    called = []
    monkeypatch.setattr(ai_fill, "_call_llm",
                        lambda s, u: called.append(1) or "{}")
    values, err = ai_fill_missing({"text": "x"}, ["msa"])
    assert values == {} and err == "no API key configured" and not called


def test_empty_text_skips(monkeypatch):
    monkeypatch.setattr(cfg, "DEEPSEEK_API_KEY", "k")
    called = []
    monkeypatch.setattr(ai_fill, "_call_llm",
                        lambda s, u: called.append(1) or "{}")
    values, err = ai_fill_missing({"text": "   "}, ["msa"])
    assert values == {} and "no extractable text" in err and not called


def test_no_fillable_fields_is_a_noop(monkeypatch):
    called = []
    monkeypatch.setattr(ai_fill, "_call_llm",
                        lambda s, u: called.append(1) or "{}")
    # only required/non-allowlisted fields missing → nothing to ask
    values, err = ai_fill_missing({"text": "x"}, ["ttm_noi", "nrsf"])
    assert values == {} and err is None and not called


# ── engine integration: fill-only-missing + provenance ───────────────
def test_engine_fills_only_missing_and_records_provenance(monkeypatch):
    """The AI value fills a blank field, never overwrites a parsed one, and
    the required occupancy stays None so require_underwritable still refuses.
    """
    import engine
    from extract.parser import CIMData

    monkeypatch.setattr(cfg, "AI_EXTRACTION_ENABLED", True)
    monkeypatch.setattr(cfg, "DEEPSEEK_API_KEY", "k")

    parsed = CIMData()
    parsed.nrsf = 50000                 # parser already has it
    parsed.ttm_gpr = None               # missing, AI-fillable
    parsed.physical_occupancy = None    # missing, NOT AI-fillable
    parsed.market_rent_psf = 1.0        # set → skips the rent-survey network
    parsed.city = None                  # skips enrichment geocode path

    monkeypatch.setattr("extract.pdf_reader.extract_pdf",
                        lambda path: {"text": "CIM text", "tables": [],
                                      "pages": ["CIM text"]})
    monkeypatch.setattr("extract.parser.parse_cim", lambda raw: parsed)
    monkeypatch.setattr("extract.enrichment.enrich_cim_data",
                        lambda cim, comp_db=None: {})
    # AI proposes a value for the blank field AND for the already-set nrsf;
    # the engine must apply only the blank one.
    monkeypatch.setattr("extract.ai_fill.ai_fill_missing",
                        lambda raw, missing: ({"ttm_gpr": 600000.0,
                                               "nrsf": 999.0}, None))

    result = engine.extract_pdf_data("/fake/does-not-exist.pdf")
    cim = result.cim_data

    assert cim.ttm_gpr == 600000.0                 # blank field filled
    assert cim.nrsf == 50000                        # parsed value untouched
    assert cim.physical_occupancy is None           # required field never AI-filled
    assert result.extraction_report["ai_filled"] == ["ttm_gpr"]

    # the refusal invariant still holds
    from analysis.fills import require_underwritable
    with pytest.raises(Exception):
        require_underwritable(cim)
