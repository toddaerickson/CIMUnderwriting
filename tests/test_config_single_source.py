"""Item T Category 1 — every moved key has ONE definition, and every
surface that restated it now follows it.

The characterization suite next door proves this PR changed nothing. That
is only half the claim. A literal moved into config and then read back
*wrongly* — the caller binding a stale copy, or reading a different key —
reproduces the snapshot perfectly, because the value is the same value.
Byte-for-byte green is exactly what a dead wire looks like.

So these tests move the config value and assert every surface moves WITH
it. That is the item's actual acceptance criterion ("a ConfigOverride
delta changes every output the audit found divergent"), and it is the
test that fails if someone later re-freezes one of these bindings at
import time.

Patching is done with `monkeypatch.setitem` on the live dict, which is
precisely what `webapp.services._merge_patch` does for a real
ConfigOverride row — same mutation, same in-place semantics, so a module
that survives this survives a settings override. Scalars have no such
mechanism (a module binding one by value cannot see a patch at all), so
`MGMT_FEE_TARGET_PCT` is asserted to be read through `cfg.` at call time.
"""

import pytest

import config as cfg
from analysis.financials import analyze_financials
from analysis.market import analyze_market
from analysis.risks import identify_risks
from analysis.value_add import identify_value_add
from engine import AnalysisResult, run_analysis
from tests.test_characterization import stabilized_deal


@pytest.fixture(autouse=True)
def isolated_comp_db(tmp_path, monkeypatch):
    """Same reason as the characterization suite: `engine.run_analysis`
    writes every completed run into the comp DB and
    `analysis.financials._get_benchmarks` reads it back, so an unisolated
    run here would change the benchmarks a LATER test resolves. Patch the
    module attribute, not `config.COMP_DB_PATH` — `data.comp_db` binds it
    at import."""
    import data.comp_db as comp_db_module
    monkeypatch.setattr(comp_db_module, "COMP_DB_PATH",
                        str(tmp_path / "comps.db"))


# ── GATES["max_noi_step_up"] — the flag AND the sentence ─────────────

def _risks_for(cim_data):
    return identify_risks(cim_data, gate_results=[], financial_analysis={},
                          scenario_results={})["risks"]


def _risk(risks, name):
    return next((r for r in risks if r["risk"] == name), None)


def test_noi_step_up_flag_follows_the_gate(mock_cim_data, monkeypatch):
    """340k TTM -> 360k Yr1 is a 5.9% step-up: under the 15% default, over
    a 5% gate. The literal was `0.15` in `analysis/risks.py`."""
    assert _risk(_risks_for(mock_cim_data), "Aggressive CIM pro forma") is None

    monkeypatch.setitem(cfg.GATES, "max_noi_step_up", 0.05)
    assert _risk(_risks_for(mock_cim_data), "Aggressive CIM pro forma")


def test_noi_step_up_sentence_quotes_the_gate_it_used(mock_cim_data,
                                                      monkeypatch):
    """The risk text said "exceeds 15% step-up threshold" as a string
    literal, so an overridden gate produced a sentence describing a
    threshold the code had not applied."""
    monkeypatch.setitem(cfg.GATES, "max_noi_step_up", 0.05)
    risk = _risk(_risks_for(mock_cim_data), "Aggressive CIM pro forma")

    assert "exceeds 5% step-up threshold" in risk["description"]
    assert "15%" not in risk["description"]


# ── GATES["population_3mi"] — four surfaces, one number ───────────────

def test_population_gate_drives_every_market_surface(mock_cim_data,
                                                     monkeypatch):
    """`analysis/market.py` restated the 50,000 gate three times (the
    adequacy flag, the narrative sentence, the demand negative) and
    `analysis/risks.py` a fourth (severity).

    60,000 people: above the 50,000 default, below a 65,000 override, and
    below the separate 75,000 "dense trade area" tier that would otherwise
    short-circuit the demand branch (see the test below).
    """
    mock_cim_data.population_3mi = 60_000

    market = analyze_market(mock_cim_data)
    assert market["demographics"]["pop_3mi_adequate"] is True
    assert "Adequate density" in market["demographics"]["pop_narrative"]
    assert not any("Thin trade area" in n
                   for n in market["demand_drivers"]["negatives"])

    monkeypatch.setitem(cfg.GATES, "population_3mi", 65_000)
    market = analyze_market(mock_cim_data)

    assert market["demographics"]["pop_3mi_adequate"] is False
    assert "Thin trade area" in market["demographics"]["pop_narrative"]
    assert any("Thin trade area" in n
               for n in market["demand_drivers"]["negatives"])


def test_the_density_tier_can_no_longer_shadow_the_population_gate(
        mock_cim_data, monkeypatch):
    """This test previously pinned the OPPOSITE, as a known Category 1
    limitation: a gate raised above the 75,000 tier left a deal reporting
    as a POSITIVE demand driver while the adequacy flag beside it said
    False. Making the tier settings-editable turned that from a fixed
    quirk into something a user could produce on purpose, so
    `_assess_demand` now takes `max(preferred_density, population_3mi)`.

    The two settings are independent, which means nothing stops someone
    setting the tier below the gate — the guard is that the OUTPUT stays
    coherent when they do, not that the inputs are policed.
    """
    mock_cim_data.population_3mi = 80_000
    monkeypatch.setitem(cfg.GATES, "population_3mi", 100_000)
    market = analyze_market(mock_cim_data)

    assert market["demographics"]["pop_3mi_adequate"] is False
    assert not any("Dense trade area" in p
                   for p in market["demand_drivers"]["positives"])


def test_a_tier_set_below_the_gate_cannot_contradict_it(mock_cim_data,
                                                        monkeypatch):
    """The same guard from the other direction: an operator lowering the
    preferred-density tier below the gate must not manufacture a deal that
    is simultaneously a demand positive and inadequate.

    BOTH faces are asserted here, and the second one is why: the first
    draft of this PR guarded `market.py` only, and `risks.py` — reading
    the same key — silently stopped raising "Limited trade area
    population" for a deal that fails the gate outright. Asserting the
    market face alone read as full coverage of a "one threshold, two
    faces" key while testing one of them.
    """
    mock_cim_data.population_3mi = 45_000
    monkeypatch.setitem(cfg.POPULATION_TIERS, "preferred_density", 40_000)
    market = analyze_market(mock_cim_data)

    assert market["demographics"]["pop_3mi_adequate"] is False
    assert not any("Dense trade area" in p
                   for p in market["demand_drivers"]["positives"])
    assert any("Thin trade area" in n
               for n in market["demand_drivers"]["negatives"])

    # The risk face: 45,000 sits ABOVE the lowered tier but BELOW the gate,
    # so an unguarded `pop < preferred_density` never fires.
    risk = _risk(_risks_for(mock_cim_data), "Limited trade area population")
    assert risk is not None
    assert risk["severity"] == "High"        # below the gate, not merely thin


# ── POPULATION_TIERS — narrative grading, settings-editable ──────────

def test_preferred_density_is_one_threshold_with_two_faces(mock_cim_data,
                                                           monkeypatch):
    """It was two separate 75,000 literals — `market.py`'s demand positive
    and `risks.py`'s "Limited trade area population" trigger — which could
    drift into a market that is neither a positive nor a risk, or both."""
    mock_cim_data.population_3mi = 80_000
    assert any("Dense trade area" in p for p in
               analyze_market(mock_cim_data)["demand_drivers"]["positives"])
    assert _risk(_risks_for(mock_cim_data),
                 "Limited trade area population") is None

    monkeypatch.setitem(cfg.POPULATION_TIERS, "preferred_density", 90_000)

    assert not any("Dense trade area" in p for p in
                   analyze_market(mock_cim_data)["demand_drivers"]["positives"])
    assert _risk(_risks_for(mock_cim_data), "Limited trade area population")


def test_strong_density_drives_the_top_narrative_tier(mock_cim_data,
                                                      monkeypatch):
    """`market.py`'s 100,000 "strong demand driver" tier."""
    mock_cim_data.population_3mi = 120_000
    assert "strong demand driver" in analyze_market(
        mock_cim_data)["demographics"]["pop_narrative"]

    monkeypatch.setitem(cfg.POPULATION_TIERS, "strong_density", 150_000)
    narrative = analyze_market(mock_cim_data)["demographics"]["pop_narrative"]

    assert "strong demand driver" not in narrative
    assert "Adequate density" in narrative


def test_population_tiers_are_settings_editable_end_to_end():
    """The registry is what the settings page renders and what
    `build_config_patch` validates against, so an unregistered key is
    accepted by the form and then silently skipped at run time — the exact
    "UI claims the override works" failure item T exists to kill."""
    from webapp.forms import (format_override_value, override_key_registry,
                              parse_override_value)
    from webapp.services import _PATCHED_DICTS, build_config_patch

    reg = override_key_registry()
    for key in ("POPULATION_TIERS.preferred_density",
                "POPULATION_TIERS.strong_density"):
        assert reg[key]["int"] is True and reg[key]["pct"] is False
        # Counts, not percentages: 90000 must round-trip as 90000, not 900.
        assert parse_override_value(key, "90,000") == 90_000
        assert format_override_value(key, 90_000) == "90000"

    # In _PATCHED_DICTS, so the patch actually reaches the analysis modules
    # that bound the dict at import.
    assert "POPULATION_TIERS" in _PATCHED_DICTS
    patch, _solver, skipped = build_config_patch(
        {"POPULATION_TIERS.preferred_density": 90_000})
    assert patch == {"POPULATION_TIERS": {"preferred_density": 90_000}}
    assert skipped == []


@pytest.mark.django_db
def test_a_stored_override_row_reaches_the_analysis(mock_cim_data):
    """The whole chain, from a row in the database to a changed narrative:
    ConfigOverride -> resolve -> build_config_patch -> _patched_config ->
    the module-level dict `analysis.market` bound at import.

    Every earlier link is unit-tested above; this is the one assertion
    that fails if any of them stops composing. The others would all still
    pass with a dict that no consumer reads.
    """
    from django.utils import timezone

    from webapp.models import ConfigOverride
    from webapp.services import (_patched_config, build_config_patch,
                                 resolve_config_overrides)

    mock_cim_data.population_3mi = 80_000
    assert any("Dense trade area" in p for p in
               analyze_market(mock_cim_data)["demand_drivers"]["positives"])

    ConfigOverride.objects.create(key="POPULATION_TIERS.preferred_density",
                                  value=90_000,
                                  effective_date=timezone.localdate())
    deltas = resolve_config_overrides("", timezone.localdate())
    patch, _solver, skipped = build_config_patch(deltas)
    assert skipped == []

    with _patched_config(patch):
        positives = analyze_market(mock_cim_data)["demand_drivers"]["positives"]
        risks = _risks_for(mock_cim_data)
    assert not any("Dense trade area" in p for p in positives)
    assert _risk(risks, "Limited trade area population")

    # And the patch is REVERTED on exit — a leaked mutation would silently
    # reprice every later deal in the same worker process.
    assert cfg.POPULATION_TIERS["preferred_density"] == 75_000


def test_population_narrative_quotes_the_gate_it_used(mock_cim_data,
                                                      monkeypatch):
    """"below 50,000 minimum" was hard-coded into the sentence, so an
    overridden gate printed the OLD number beside the new verdict."""
    monkeypatch.setitem(cfg.GATES, "population_3mi", 100_000)
    narrative = analyze_market(mock_cim_data)["demographics"]["pop_narrative"]

    assert "below 100,000 minimum" in narrative
    assert "50,000" not in narrative


def test_population_severity_follows_the_gate(mock_cim_data, monkeypatch):
    """`risks.py` grades a thin trade area Medium above the gate, High
    below it. At 60,000 the fixture is below the "preferred density"
    trigger either way, so only the SEVERITY should move."""
    mock_cim_data.population_3mi = 60_000
    assert _risk(_risks_for(mock_cim_data),
                 "Limited trade area population")["severity"] == "Medium"

    monkeypatch.setitem(cfg.GATES, "population_3mi", 65_000)
    assert _risk(_risks_for(mock_cim_data),
                 "Limited trade area population")["severity"] == "High"


def test_the_hhi_thresholds_are_not_the_population_gate(mock_cim_data,
                                                        monkeypatch):
    """The trap this move had to avoid: `median_hhi_3mi` is screened at
    50,000 too — dollars, not people. A sweep that replaced every `50_000`
    in `market.py` would have wired household income to the population
    gate, and nothing else in the suite would have noticed."""
    mock_cim_data.median_hhi_3mi = 55_000

    monkeypatch.setitem(cfg.GATES, "population_3mi", 100_000)
    demographics = analyze_market(mock_cim_data)["demographics"]

    assert demographics["hhi_adequate"] is True
    assert "Middle-income" in demographics["hhi_narrative"]


# ── MGMT_FEE_TARGET_PCT — one target, two modules, four sites ────────

def test_mgmt_fee_target_drives_the_financials_adjustment(mock_cim_data,
                                                          monkeypatch):
    """A CIM fee below the benchmark floor is adjusted UP to the target.
    `financials.py` restated it twice as a number and twice as text."""
    mock_cim_data.mgmt_fee_pct = 0.01
    monkeypatch.setattr(cfg, "MGMT_FEE_TARGET_PCT", 0.08)

    fin = analyze_financials(mock_cim_data)
    line = next(ln for ln in fin["expense_analysis"]["lines"]
                if ln["category"] == "Management Fee")
    egr = fin["income_summary"]["egr"]

    assert line["adjusted_pct"] == 0.08
    assert line["adjusted_value"] == pytest.approx(egr * 0.08)
    assert any("Adjusted to 8% of EGR" in a
               for a in fin["expense_analysis"]["adjustments"])


def test_mgmt_fee_target_drives_the_value_add_saving(mock_cim_data,
                                                     monkeypatch):
    """`value_add.py` sized the renegotiation saving off its own copy of
    the target, so the two modules could disagree about the same number.
    Both now resolve it through `resolve_mgmt_fee_target`.

    The fee is 8% — above the (3%, 6%) band, which is the case that used
    to crash `_expense_opportunities` outright (KeyError
    'benchmark_range'). Fixed in #41, so an above-band fee is now the
    natural fixture for "there is a real fee to renegotiate".
    """
    mock_cim_data.mgmt_fee_pct = 0.08
    fin = analyze_financials(mock_cim_data)
    egr = fin["income_summary"]["egr"]

    op = next(o for o in identify_value_add(
        mock_cim_data, fin)["expense_opportunities"]
        if o["category"] == "Management Fee Reduction")
    assert op["est_annual_impact"] == pytest.approx(
        egr * (0.08 - cfg.MGMT_FEE_TARGET_PCT))

    monkeypatch.setattr(cfg, "MGMT_FEE_TARGET_PCT", 0.04)
    op = next(o for o in identify_value_add(
        mock_cim_data, fin)["expense_opportunities"]
        if o["category"] == "Management Fee Reduction")

    assert op["est_annual_impact"] == pytest.approx(egr * (0.08 - 0.04))
    assert "to 4% of EGR" in op["description"]


def test_the_two_modules_cannot_resolve_different_targets(mock_cim_data):
    """The double-count this parameter could have introduced. If
    `analyze_financials` underwrote to 6% while `identify_value_add`
    credited a walk down to the config default, the same dollar would be
    counted twice — once as an expense the model already removed, once as
    upside still to come. Both read the ONE resolver, so a per-deal target
    handed to the engine reaches both or neither.
    """
    mock_cim_data.mgmt_fee_pct = 0.08
    fin = analyze_financials(mock_cim_data, mgmt_fee_target_pct=0.04)
    egr = fin["income_summary"]["egr"]

    op = next(o for o in identify_value_add(
        mock_cim_data, fin, mgmt_fee_target_pct=0.04)["expense_opportunities"]
        if o["category"] == "Management Fee Reduction")

    assert op["est_annual_impact"] == pytest.approx(egr * (0.08 - 0.04))
    assert "to 4% of EGR" in op["description"]


def test_a_zero_target_is_honoured_not_swallowed(mock_cim_data):
    """0.0 is a legitimate target — a self-managed property underwritten
    with no third-party fee — so the resolver keys on `is None`. A falsy
    check would silently substitute the 6% config default and quietly add
    an expense the analyst deliberately removed."""
    from analysis.financials import resolve_mgmt_fee_target

    assert resolve_mgmt_fee_target(0.0) == 0.0
    assert resolve_mgmt_fee_target(None) == cfg.MGMT_FEE_TARGET_PCT

    mock_cim_data.mgmt_fee_pct = None
    fin = analyze_financials(mock_cim_data, mgmt_fee_target_pct=0.0)
    line = next(ln for ln in fin["expense_analysis"]["lines"]
                if ln["category"] == "Management Fee")

    assert line["adjusted_value"] == 0.0


def test_an_omitted_fee_is_underwritten_at_the_target(mock_cim_data):
    """The highest-impact path, and the one the characterization net does
    NOT reach: a CIM that omits its management fee entirely.

    All three snapshot fixtures miss it — `stabilized` states 5%,
    `value_add` states 6%, and `thin` omits the fee but also has no EGR,
    so `elif egr:` never fires and no fee is assumed at all. So the
    default moving 5% -> 6% shows up in the snapshots only as a lost
    value-add opportunity, never as the NOI reduction it mainly is. This
    test is that missing coverage, asserted directly.
    """
    mock_cim_data.mgmt_fee_pct = None
    fin = analyze_financials(mock_cim_data)
    egr = fin["income_summary"]["egr"]
    line = next(ln for ln in fin["expense_analysis"]["lines"]
                if ln["category"] == "Management Fee")

    assert line["flag"] == "NOT FOUND"
    assert line["adjusted_value"] == pytest.approx(egr * 0.06)
    assert any("Assumed 6% of EGR" in a
               for a in fin["expense_analysis"]["adjustments"])


@pytest.mark.django_db
def test_the_mgmt_fee_target_round_trips_through_the_assumptions_form():
    """The percent-vs-decimal boundary, which is where a field of this
    shape usually breaks: the form takes a WHOLE number (4 meaning 4%)
    and every consumer wants a decimal. A one-way conversion stores 4.0
    as a 400% fee, or redisplays 0.04 in a box labelled "%".

    Also asserts the delta discipline the section uses: a field left at
    the config default writes NO key, so the deal stays on whatever the
    default later becomes rather than freezing today's value into every
    deal ever saved.
    """
    from django.http import QueryDict

    from webapp.forms import AssumptionsForm, build_initial, build_overrides
    from webapp.models import Deal

    deal = Deal.objects.create(deal_id="mf-rt", property_name="RT")

    form = AssumptionsForm(data={"mgmt_fee_target_pct": "4"})
    assert form.is_valid(), form.errors
    stored = build_overrides(form.cleaned_data, QueryDict(""), deal)
    assert stored["mgmt_fee_target_pct"] == pytest.approx(0.04)

    # Redisplay lands on the whole number the analyst typed, not 0.04.
    deal.assumption_overrides = stored
    deal.save()
    assert build_initial(deal)["mgmt_fee_target_pct"] == pytest.approx(4.0)

    # At the default: no key stored.
    at_default = AssumptionsForm(
        data={"mgmt_fee_target_pct": str(cfg.MGMT_FEE_TARGET_PCT * 100)})
    assert at_default.is_valid(), at_default.errors
    assert "mgmt_fee_target_pct" not in build_overrides(
        at_default.cleaned_data, QueryDict(""), deal)


def test_the_engine_accepts_a_per_deal_mgmt_fee_target():
    """`webapp.services` hands the stored override straight to
    `run_analysis`, so the parameter has to exist by that exact name.
    Pinned by signature rather than by a full run — the arithmetic is
    covered above; what breaks silently is a rename."""
    import inspect

    from engine import run_analysis

    params = inspect.signature(run_analysis).parameters
    assert "mgmt_fee_target_pct" in params
    assert params["mgmt_fee_target_pct"].default is None


@pytest.mark.django_db
def test_a_saved_mgmt_fee_target_actually_reaches_the_run(monkeypatch,
                                                          tmp_path, settings):
    """The link every other test in this file would pass without: the
    worker reading the stored override and handing it to the engine.

    Found by mutation — deleting the `mgmt_fee_target_pct=` line from
    `webapp/services.py`'s `run_analysis` call left the whole suite green.
    The analyst would type 4%, the page would save it, redisplay it, and
    the model would quietly underwrite at 6% — the exact "UI claims the
    override works and the model proves otherwise" failure item T exists
    to kill, reintroduced by the very PR adding the field.

    A 0.0 target is used deliberately: it is both a legitimate value and
    the one an `or`-style fallback would swallow.
    """
    from tests.test_web_runs import _make_extracted_deal, _start_run

    # `deals_dir` is a local fixture in three other test modules; inlined
    # here rather than copied a fourth time.
    deals_dir = tmp_path / "deals"
    deals_dir.mkdir()
    settings.CIM_DEALS_DIR = str(deals_dir)
    seen = {}

    def _fake(result, progress=None, output_dir=None, custom_scenarios=None,
              custom_va_scenarios=None, solver_target_irr=None, enrich=False,
              expense_line_overrides=None, hold_years=None,
              transaction_costs=None, capital_structure=None,
              market_cap_rate=None, market_cap=None,
              debt_terms=None, waterfall_terms=None, am_fee_pct=None,
              mgmt_fee_target_pct=None):
        seen["mgmt_fee_target_pct"] = mgmt_fee_target_pct
        result.gate_results = []
        result.gate_summary = {"passed": 0, "failed": 0, "tbd": 0, "total": 0,
                               "recommendation": "PURSUE",
                               "failed_gates": [], "tbd_gates": []}
        return result

    monkeypatch.setattr("webapp.services.run_analysis", _fake)
    deal = _make_extracted_deal(deals_dir)
    deal.assumption_overrides = {"mgmt_fee_target_pct": 0.0}
    deal.save()
    run = _start_run(deal)

    assert seen["mgmt_fee_target_pct"] == 0.0
    assert run.applied_overrides["assumptions"]["mgmt_fee_target_pct"] == 0.0


@pytest.mark.django_db
def test_a_run_on_the_default_still_stamps_the_target_it_used(monkeypatch,
                                                              tmp_path,
                                                              settings):
    """Stamped RESOLVED, not as a delta — the discipline `hold_years`,
    `transaction_costs` and the debt/waterfall blocks already follow.

    This PR moved the default 5% -> 6%. Without a resolved stamp, a run
    from before and a run from after — neither with a per-deal override —
    carry byte-identical `applied_overrides` while underwriting a
    management fee 100bp apart, which is 1.6% of adjusted NOI on a deal
    whose CIM omits the fee. A past run has to say what it ran under.
    """
    from tests.test_web_runs import _make_extracted_deal, _start_run

    deals_dir = tmp_path / "deals"
    deals_dir.mkdir()
    settings.CIM_DEALS_DIR = str(deals_dir)

    def _fake(result, *a, **kw):
        result.gate_results = []
        result.gate_summary = {"passed": 0, "failed": 0, "tbd": 0, "total": 0,
                               "recommendation": "PURSUE",
                               "failed_gates": [], "tbd_gates": []}
        return result

    monkeypatch.setattr("webapp.services.run_analysis", _fake)
    deal = _make_extracted_deal(deals_dir)
    deal.assumption_overrides = {}          # nothing set — pure default
    deal.save()
    run = _start_run(deal)

    assert (run.applied_overrides["assumptions"]["mgmt_fee_target_pct"]
            == cfg.MGMT_FEE_TARGET_PCT)


def test_the_target_is_the_top_of_the_benchmark_band():
    """Replaces this file's earlier
    `test_mgmt_fee_target_is_not_the_benchmark_midpoint`, which pinned the
    old 5%.

    6% is the band's HIGH end, and that is the whole underwriting
    argument: a CIM omitting its management fee is the common case, and
    the conservative read of an omission is the most expensive credible
    number, not a comfortable middle one. Operator's call 2026-08-04.

    The midpoint assertion survives for the same reason it was written:
    a derived `(low + high) / 2` looks like a tidy-up and silently
    re-underwrites every deal with an understated or missing fee.
    """
    low, high = cfg.EXPENSE_BENCHMARKS["mgmt_fee_pct"]

    assert cfg.MGMT_FEE_TARGET_PCT == high
    assert cfg.MGMT_FEE_TARGET_PCT != (low + high) / 2


# ── The documents — the surfaces the result object cannot prove ──────

def _documents_for(tmp_path, **kwargs):
    """One real end-to-end run, read back off disk.

    The workbook's grid colors and the max-offer captions are Category 1
    targets that leave `AnalysisResult`'s own fields untouched, so nothing
    short of opening the rendered files can show them moving.
    """
    from tests.test_characterization import _docx_content, _xlsx_content

    result = AnalysisResult(pdf_path="rt.pdf", cim_data=stabilized_deal())
    run_analysis(result, output_dir=str(tmp_path), **kwargs)
    return (_docx_content(result.memo_path),
            _xlsx_content(result.excel_path))


def _recommendation(base_irr: float) -> str:
    """Section 10 rendered on its own, with every gate passing.

    Called directly rather than through a pipeline run because the
    threshold sentence is only REACHABLE when no gate failed and none is
    TBD — a failed gate short-circuits to DECLINE and never reads the IRR
    at all. Both characterization fixtures that reach the memo have a
    failing gate, so an end-to-end run cannot exercise this branch.
    """
    from docx import Document

    from output.memo_writer import _add_section_10

    doc = Document()
    _add_section_10(doc, gate_results=[{"gate": 1, "name": "g",
                                        "result": "PASS"}],
                    scenario_results={"base": {"irr": base_irr}},
                    max_offer={}, risk_analysis={}, cim_data=None)
    return "\n".join(p.text for p in doc.paragraphs)


def test_memo_recommendation_threshold_follows_the_gate(monkeypatch):
    """"meets the 10% IRR target" was a string literal sitting beside a
    `>= 0.10` comparison — two copies of the gate, neither reading it. A
    12% deal clears the default and misses a 15% gate, so BOTH the
    verdict and the number in the sentence have to move."""
    text = _recommendation(0.12)
    assert "RECOMMENDATION: PURSUE" in text
    assert "meet the 10% IRR target" in text

    monkeypatch.setitem(cfg.GATES, "min_irr_5yr", 0.15)
    text = _recommendation(0.12)

    assert "RECOMMENDATION: PURSUE CONTINGENT ON" in text
    assert "base case IRR is below 15% target" in text
    assert "10%" not in text


def test_sensitivity_legend_follows_the_gate_and_the_strong_band(
        tmp_path, monkeypatch):
    """The workbook legend spelled out "≥ 12% IRR" / "10-12% IRR" /
    "< 10% IRR" as three literals describing thresholds held as two
    others. All five are now one pair."""
    monkeypatch.setitem(cfg.GATES, "min_irr_5yr", 0.08)
    monkeypatch.setattr(cfg, "IRR_STRONG_THRESHOLD", 0.20)
    _, workbook = _documents_for(tmp_path)

    legend = [c["v"] for c in workbook["Sensitivity"].values()
              if isinstance(c["v"], str) and "IRR" in c["v"]]

    assert "  ≥ 20% IRR" in legend
    assert "  8-20% IRR" in legend
    assert "  < 8% IRR" in legend


def test_sensitivity_colors_follow_the_gate(tmp_path, monkeypatch):
    """The fills, not just the caption. A gate above every IRR in the
    grid must leave no green and no yellow cell in it — proof the
    comparison reads config and not the old `0.12` / `0.10`."""
    monkeypatch.setitem(cfg.GATES, "min_irr_5yr", 0.90)
    monkeypatch.setattr(cfg, "IRR_STRONG_THRESHOLD", 0.95)
    _, workbook = _documents_for(tmp_path)

    fills = {c.get("fill") for c in workbook["Sensitivity"].values()
             if isinstance(c["v"], float)}

    assert "00C6EFCE" not in fills          # green — none can clear 95%
    assert "00FFEB9C" not in fills          # yellow — none can clear 90%
    assert "00FFC7CE" in fills              # red — every cell


def test_max_offer_captions_quote_the_target_actually_solved_for(tmp_path):
    """The captions must name the target the SOLVER used, not a constant
    that happens to match it today.

    Driven through the per-deal `solver_target_irr`, which is the one
    route that genuinely moves the solver right now: `solve_max_price`
    binds `SOLVER_TARGET_IRR` as a default ARGUMENT, frozen at import, so
    patching config cannot reach it. That frozen binding is a known
    defect and item T Category 3 owns it — this test is written against
    the wiring as it is, and will keep passing once Category 3 unfreezes
    it.
    """
    memo, workbook = _documents_for(tmp_path, solver_target_irr=0.14)
    text = "\n".join(memo["paragraphs"])

    assert "Maximum Offer Price (for 14% Base Case IRR)" in text
    assert "At a target 14% base case unlevered IRR" in text
    assert not any("10% Base Case IRR" in p for p in memo["paragraphs"])

    captions = [c["v"] for c in workbook["Max Offer"].values()
                if isinstance(c["v"], str) and "Max Price" in c["v"]]
    assert captions and all("10%" not in c for c in captions)


def test_max_offer_caption_falls_back_to_the_config_target(mock_cim_data,
                                                           monkeypatch):
    """The `.get()` defaults behind those captions were the literal 0.10.
    A max-offer dict with no `target_irr` — the only case the default
    fires — must read config, or the fallback quietly re-hard-codes the
    number the rest of this move just removed."""
    from docx import Document

    from output.memo_writer import _add_section_1

    monkeypatch.setattr(cfg, "SOLVER_TARGET_IRR", 0.14)
    doc = Document()
    _add_section_1(doc, mock_cim_data, gate_results=[], scenario_results={},
                   max_offer={"max_price": 1_000_000})
    text = "\n".join(p.text for p in doc.paragraphs)

    assert "Maximum Offer Price (for 14% Base Case IRR)" in text
