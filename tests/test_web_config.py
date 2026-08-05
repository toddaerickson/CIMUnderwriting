"""Phase 5A: ConfigOverride model, resolution, patching, settings + comps pages."""
import copy
import datetime

import pytest

import config as cfg


@pytest.fixture
def operator(client, django_user_model):
    user = django_user_model.objects.create_user(username="op", password="x")
    client.force_login(user)
    return user


@pytest.fixture
def deals_dir(tmp_path, settings):
    d = tmp_path / "deals"
    d.mkdir()
    settings.CIM_DEALS_DIR = str(d)
    return d


@pytest.mark.django_db
def test_config_override_defaults():
    from webapp.models import ConfigOverride

    row = ConfigOverride.objects.create(
        key="GATES.min_irr_5yr", value=0.12,
        effective_date=datetime.date(2026, 7, 1))
    assert row.asset_type == ""          # global scope by default
    assert row.note == ""
    assert row.created_at is not None


@pytest.mark.django_db
def test_analysis_run_applied_overrides_default():
    from webapp.models import AnalysisRun, Deal

    deal = Deal.objects.create(deal_id="x", property_name="X")
    run = AnalysisRun.objects.create(deal=deal)
    assert run.applied_overrides is None


def test_asset_types_matches_detect_asset_type():
    """No-drift guard: the scope dropdown's choices are exactly the values
    detect_asset_type can return."""
    from webapp.services import ASSET_TYPES, detect_asset_type

    class FakeCim:
        brv_enclosed_sf = None
        brv_covered_sf = None
        brv_open_sf = None
        cc_pct = None

    assert detect_asset_type(FakeCim()) in ASSET_TYPES
    FakeCim.cc_pct = 0.8
    assert detect_asset_type(FakeCim()) in ASSET_TYPES
    FakeCim.brv_open_sf = 10_000
    assert detect_asset_type(FakeCim()) in ASSET_TYPES
    assert len(ASSET_TYPES) == 3


def test_registry_derives_from_config():
    from webapp.forms import override_key_registry

    reg = override_key_registry()
    # spot checks across every group
    assert reg["GATES.min_irr_5yr"] == {
        "group": "Gates", "kind": "scalar", "pct": True, "int": False,
        "label": "Min Irr 5Yr", "bounds": (0.0, 1.0)}
    assert reg["GATES.population_3mi"]["int"] is True
    assert reg["GATES.population_3mi"]["pct"] is False
    # SF/capita is a count-like threshold — must never display as a percent
    assert reg["GATES.max_sf_per_capita"]["int"] is True
    assert reg["GATES.max_sf_per_capita"]["pct"] is False
    assert reg["EXPENSE_BENCHMARKS.property_tax"]["kind"] == "range"
    assert reg["EXPENSE_BENCHMARKS.property_tax"]["pct"] is False
    assert reg["EXPENSE_BENCHMARKS.mgmt_fee_pct"]["pct"] is True
    assert reg["REPLACEMENT_COST.soft_cost_pct"]["pct"] is True
    assert reg["SCENARIO_DEFAULTS.base.rev_cagr_yr1_3"]["kind"] == "scalar"
    # Market cap is a three-level key (dict.class.band) whose middle level
    # is a display string with a space in it — the only registry key of
    # that shape, so it is the one that proves the splitter handles it.
    assert reg["MARKET_CAP_RATES.Self Storage.mid"]["kind"] == "scalar"
    assert reg["MARKET_CAP_RATES.Self Storage.mid"]["pct"] is True
    assert reg["VALUE_ADD_SCENARIOS.bull.months_to_stabilize"]["pct"] is False
    assert reg["VALUE_ADD_TRIGGERS.max_occupancy"]["pct"] is True
    assert reg["SOLVER_TARGET_IRR"]["pct"] is True
    # legacy aliases and derived keys are NOT offered
    for alias in ("non_cc_per_sf", "cc_per_sf", "site_work_per_sf"):
        assert f"REPLACEMENT_COST.{alias}" not in reg
    assert "EXPENSE_BENCHMARKS.total_opex" not in reg   # recomputed per state
    # every registry key resolves against the live config module
    from webapp.forms import dotted_get
    for key in reg:
        dotted_get(cfg, key)          # raises KeyError/AttributeError on drift


def test_parse_and_format_override_values():
    from django.forms import ValidationError

    from webapp.forms import format_override_value, parse_override_value

    assert parse_override_value("GATES.min_irr_5yr", "12") == 0.12
    assert parse_override_value("GATES.population_3mi", "60000") == 60000
    # the displayed format must always be re-enterable (round-trip)
    assert parse_override_value("GATES.population_3mi", "60,000") == 60000
    assert parse_override_value("EXPENSE_BENCHMARKS.property_tax",
                                "1.40, 2.60") == [1.4, 2.6]
    assert parse_override_value("EXPENSE_BENCHMARKS.mgmt_fee_pct",
                                "4, 7") == [0.04, 0.07]
    assert parse_override_value(
        "VALUE_ADD_SCENARIOS.bull.months_to_stabilize", "18") == 18
    with pytest.raises(ValidationError):
        parse_override_value("GATES.min_irr_5yr", "1, 2")     # scalar key
    with pytest.raises(ValidationError):
        parse_override_value("EXPENSE_BENCHMARKS.property_tax", "5")  # range key
    with pytest.raises(ValidationError):
        parse_override_value("EXPENSE_BENCHMARKS.property_tax", "3, 1")  # low > high
    with pytest.raises(ValidationError):
        parse_override_value("GATES.min_irr_5yr", "abc")

    assert format_override_value("GATES.min_irr_5yr", 0.12) == "12%"
    assert format_override_value("EXPENSE_BENCHMARKS.property_tax",
                                 [1.4, 2.6]) == "1.4 – 2.6"
    assert format_override_value("EXPENSE_BENCHMARKS.mgmt_fee_pct",
                                 [0.04, 0.07]) == "4% – 7%"
    assert format_override_value("GATES.population_3mi", 60000) == "60000"


@pytest.mark.django_db
def test_config_override_form_round_trip():
    from webapp.forms import ConfigOverrideForm
    from webapp.models import ConfigOverride

    form = ConfigOverrideForm({
        "key": "GATES.min_irr_5yr", "value": "12", "asset_type": "",
        "effective_date": "2026-07-01", "note": "tighten"})
    assert form.is_valid(), form.errors
    row = form.save()
    assert ConfigOverride.objects.get(pk=row.pk).value == 0.12

    bad = ConfigOverrideForm({
        "key": "GATES.nope", "value": "1",
        "asset_type": "", "effective_date": "2026-07-01"})
    assert not bad.is_valid()


@pytest.mark.django_db
def test_resolution_precedence():
    from webapp.models import ConfigOverride
    from webapp.services import resolve_config_overrides

    d = datetime.date
    # global, older
    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.11,
                                  effective_date=d(2026, 1, 1))
    # global, newer — wins over older global
    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.12,
                                  effective_date=d(2026, 6, 1))
    # asset-specific with an EARLIER date — still beats any global
    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.14,
                                  asset_type="Boat & RV Storage",
                                  effective_date=d(2026, 2, 1))
    # future-dated — inert until due
    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.20,
                                  effective_date=d(2027, 1, 1))
    today = d(2026, 7, 27)
    assert resolve_config_overrides("", today) == {"GATES.min_irr_5yr": 0.12}
    assert resolve_config_overrides("Self Storage", today) == {
        "GATES.min_irr_5yr": 0.12}
    assert resolve_config_overrides("Boat & RV Storage", today) == {
        "GATES.min_irr_5yr": 0.14}
    # same key+scope+date: higher pk wins
    a = ConfigOverride.objects.create(key="GATES.population_3mi", value=55000,
                                      effective_date=d(2026, 6, 1))
    b = ConfigOverride.objects.create(key="GATES.population_3mi", value=60000,
                                      effective_date=d(2026, 6, 1))
    assert b.pk > a.pk
    assert resolve_config_overrides("", today)["GATES.population_3mi"] == 60000


def test_build_config_patch_shapes_and_unknown_keys(caplog):
    from registry import ScenarioType
    from webapp.services import build_config_patch

    deltas = {
        "GATES.min_irr_5yr": 0.12,
        "EXPENSE_BENCHMARKS.property_tax": [1.4, 2.6],
        "SCENARIO_DEFAULTS.base.rev_cagr_yr1_3": 0.07,
        "MARKET_CAP_RATES.Self Storage.mid": 0.061,
        "SOLVER_TARGET_IRR": 0.12,
        "GATES.retired_key_from_2025": 1.0,      # unknown → skipped, warned
    }
    patch, solver, skipped = build_config_patch(deltas)
    assert solver == 0.12
    assert patch["GATES"] == {"min_irr_5yr": 0.12}
    assert patch["EXPENSE_BENCHMARKS"] == {"property_tax": [1.4, 2.6]}
    assert patch["SCENARIO_DEFAULTS"] == {
        ScenarioType.BASE: {"rev_cagr_yr1_3": 0.07}}
    assert patch["MARKET_CAP_RATES"] == {"Self Storage": {"mid": 0.061}}
    assert skipped == ["GATES.retired_key_from_2025"]
    assert "retired_key_from_2025" in caplog.text


def test_patched_config_mutates_in_place_and_restores():
    # Importers bound these dict OBJECTS at import time — the patch must
    # be visible through those bindings, then fully restored.
    from analysis.filters import GATES as bound_gates
    from analysis.physical import REPLACEMENT_COST as bound_rc
    from analysis.valuation import MARKET_CAP_RATES as bound_mcr
    from analysis.valuation import SCENARIO_DEFAULTS as bound_scen
    from registry import ScenarioType
    from webapp.services import _patched_config

    orig_irr = bound_gates["min_irr_5yr"]
    orig_growth = bound_scen[ScenarioType.BASE]["rev_cagr_yr1_3"]
    orig_mcr = bound_mcr["Self Storage"]["mid"]
    orig_rc = bound_rc["ss_driveup_per_sf"]
    orig_alias = bound_rc["non_cc_per_sf"]
    patch = {
        "GATES": {"min_irr_5yr": 0.12, "not_a_key": 9},
        "SCENARIO_DEFAULTS": {ScenarioType.BASE: {"rev_cagr_yr1_3": 0.07}},
        "MARKET_CAP_RATES": {"Self Storage": {"mid": 0.061}},
        "REPLACEMENT_COST": {"ss_driveup_per_sf": [100, 120]},
    }
    with _patched_config(patch):
        assert bound_gates["min_irr_5yr"] == 0.12
        assert "not_a_key" not in bound_gates
        assert bound_scen[ScenarioType.BASE]["rev_cagr_yr1_3"] == 0.07
        # This is the mutation `resolve_run_market_cap` must NOT read: it
        # is another deal's table for as long as that deal holds the lock.
        assert bound_mcr["Self Storage"]["mid"] == 0.061
        assert tuple(bound_rc["ss_driveup_per_sf"]) == (100, 120)
        # legacy alias synced (analysis/physical.py:151-154 reads it)
        assert tuple(bound_rc["non_cc_per_sf"]) == (100, 120)
    assert bound_gates["min_irr_5yr"] == orig_irr
    assert bound_scen[ScenarioType.BASE]["rev_cagr_yr1_3"] == orig_growth
    assert bound_mcr["Self Storage"]["mid"] == orig_mcr
    assert bound_rc["ss_driveup_per_sf"] == orig_rc
    assert bound_rc["non_cc_per_sf"] == orig_alias


def test_patched_config_restores_on_exception():
    from analysis.filters import GATES as bound_gates
    from webapp.services import _patched_config

    orig = bound_gates["min_irr_5yr"]
    with pytest.raises(RuntimeError):
        with _patched_config({"GATES": {"min_irr_5yr": 0.5}}):
            raise RuntimeError("boom")
    assert bound_gates["min_irr_5yr"] == orig


@pytest.mark.django_db
def test_effective_config_never_mutates_module():
    from webapp.models import ConfigOverride
    from webapp.services import effective_config

    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.13,
                                  effective_date=datetime.date(2026, 1, 1))
    ConfigOverride.objects.create(key="SOLVER_TARGET_IRR", value=0.12,
                                  effective_date=datetime.date(2026, 1, 1))
    eff = effective_config("")
    assert eff["GATES"]["min_irr_5yr"] == 0.13
    assert eff["SOLVER_TARGET_IRR"] == 0.12
    assert cfg.GATES["min_irr_5yr"] == 0.10          # module untouched
    assert cfg.SOLVER_TARGET_IRR == 0.10
    eff["GATES"]["min_irr_5yr"] = 0.99               # caller-owned copy
    assert cfg.GATES["min_irr_5yr"] == 0.10


@pytest.mark.django_db
def test_worker_applies_global_overrides_and_stamps_run(deals_dir, monkeypatch):
    """A global ConfigOverride reaches the engine (patched GATES visible
    through the import-time binding DURING the run), per-deal solver
    override beats the global one, and the run row records both."""
    import datetime as dt

    from tests.test_web_runs import _make_extracted_deal

    from webapp.models import ConfigOverride

    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.13,
                                  effective_date=dt.date(2026, 1, 1))
    ConfigOverride.objects.create(key="SOLVER_TARGET_IRR", value=0.12,
                                  effective_date=dt.date(2026, 1, 1))
    # a global scenario delta that must be DROPPED (per-deal section wins
    # wholesale)
    ConfigOverride.objects.create(key="SCENARIO_DEFAULTS.base.exit_cap",
                                  value=0.07,
                                  effective_date=dt.date(2026, 1, 1))
    seen = {}

    def _fake(result, progress=None, output_dir=None, custom_scenarios=None,
              custom_va_scenarios=None, solver_target_irr=None, enrich=False,
              expense_line_overrides=None, hold_years=None,
              transaction_costs=None, capital_structure=None,
              market_cap_rate=None, market_cap=None,
              debt_terms=None, waterfall_terms=None,
              am_fee_pct=None,
              mgmt_fee_target_pct=None):
        from analysis.filters import GATES
        seen["min_irr_during_run"] = GATES["min_irr_5yr"]
        seen["solver_target_irr"] = solver_target_irr
        seen["custom_scenarios"] = custom_scenarios
        result.gate_results = []
        result.gate_summary = {"passed": 0, "failed": 0, "tbd": 0, "total": 0,
                               "recommendation": "PURSUE",
                               "failed_gates": [], "tbd_gates": []}
        return result

    monkeypatch.setattr("webapp.services.run_analysis", _fake)
    deal = _make_extracted_deal(deals_dir)
    per_deal_scen = {"base": {"exit_cap": 0.08}}
    deal.assumption_overrides = {"solver_target_irr": 0.15,   # per-deal wins
                                 "scenario_overrides": per_deal_scen}
    deal.save()
    from tests.test_web_runs import _start_run
    run = _start_run(deal)

    assert seen["min_irr_during_run"] == 0.13
    assert seen["solver_target_irr"] == 0.15
    assert seen["custom_scenarios"] == per_deal_scen
    # stamp records ONLY what applied: the scenario delta was dropped
    # (per-deal section wins wholesale) and so was the global solver row
    # (per-deal solver_target_irr won); nothing was unknown
    assert run.applied_overrides["config"] == {"GATES.min_irr_5yr": 0.13}
    assert run.applied_overrides["config_skipped"] == []
    assert run.applied_overrides["assumptions"]["solver_target_irr"] == 0.15
    from analysis.filters import GATES
    assert GATES["min_irr_5yr"] == 0.10          # restored after the run


@pytest.mark.django_db
def test_worker_stamps_global_solver_without_per_deal_override(deals_dir,
                                                              monkeypatch):
    """With NO per-deal solver override, the global SOLVER_TARGET_IRR row
    both reaches the engine and is stamped under "config"."""
    import datetime as dt

    from tests.test_web_runs import _make_extracted_deal
    from webapp.models import ConfigOverride

    ConfigOverride.objects.create(key="SOLVER_TARGET_IRR", value=0.12,
                                  effective_date=dt.date(2026, 1, 1))
    seen = {}

    def _fake(result, progress=None, output_dir=None, custom_scenarios=None,
              custom_va_scenarios=None, solver_target_irr=None, enrich=False,
              expense_line_overrides=None, hold_years=None,
              transaction_costs=None, capital_structure=None,
              market_cap_rate=None, market_cap=None,
              debt_terms=None, waterfall_terms=None,
              am_fee_pct=None,
              mgmt_fee_target_pct=None):
        seen["solver_target_irr"] = solver_target_irr
        result.gate_results = []
        result.gate_summary = {"passed": 0, "failed": 0, "tbd": 0, "total": 0,
                               "recommendation": "PURSUE",
                               "failed_gates": [], "tbd_gates": []}
        return result

    monkeypatch.setattr("webapp.services.run_analysis", _fake)
    deal = _make_extracted_deal(deals_dir)
    from tests.test_web_runs import _start_run
    run = _start_run(deal)

    assert seen["solver_target_irr"] == 0.12
    assert run.applied_overrides["config"] == {"SOLVER_TARGET_IRR": 0.12}


def _capture_run_kwargs(monkeypatch, seen):
    def _fake(result, progress=None, output_dir=None, custom_scenarios=None,
              custom_va_scenarios=None, solver_target_irr=None, enrich=False,
              expense_line_overrides=None, hold_years=None,
              transaction_costs=None, capital_structure=None,
              market_cap_rate=None, market_cap=None,
              debt_terms=None, waterfall_terms=None, am_fee_pct=None,
              mgmt_fee_target_pct=None):
        seen["hold_years"] = hold_years
        seen["transaction_costs"] = transaction_costs
        seen["market_cap_rate"] = market_cap_rate
        seen["market_cap"] = market_cap
        seen["custom_scenarios"] = custom_scenarios
        seen["debt_terms"] = debt_terms
        seen["waterfall_terms"] = waterfall_terms
        seen["am_fee_pct"] = am_fee_pct
        result.gate_results = []
        result.gate_summary = {"passed": 0, "failed": 0, "tbd": 0, "total": 0,
                               "recommendation": "PURSUE",
                               "failed_gates": [], "tbd_gates": []}
        return result

    monkeypatch.setattr("webapp.services.run_analysis", _fake)


@pytest.mark.django_db
def test_hold_and_costs_are_stamped_even_at_the_defaults(deals_dir,
                                                         monkeypatch):
    """Item B changed every published IRR, so a run that sat on the
    defaults must still SAY what it used. Deltas everywhere else; the
    resolved values here — otherwise an old run is indistinguishable from
    a new one rather than self-describing."""
    from config import DEFAULT_HOLD_YEARS, TRANSACTION_COSTS
    from tests.test_web_runs import _make_extracted_deal, _start_run

    seen = {}
    _capture_run_kwargs(monkeypatch, seen)
    run = _start_run(_make_extracted_deal(deals_dir))

    stamped = run.applied_overrides["assumptions"]
    assert stamped["hold_years"] == DEFAULT_HOLD_YEARS
    assert stamped["transaction_costs"] == dict(TRANSACTION_COSTS)
    # and the engine was handed exactly what was stamped
    assert seen["hold_years"] == stamped["hold_years"]
    assert seen["transaction_costs"] == stamped["transaction_costs"]
    # Same rule for the market cap: it drives every exit value, so a run
    # sitting on the table default must still say which cell it used.
    assert stamped["market_cap"]["source"] == "table"
    assert stamped["market_cap"]["market_cap"] == \
        cfg.MARKET_CAP_RATES[stamped["market_cap"]["asset_class"]][
            stamped["market_cap"]["age_band"]]
    # The engine is handed the resolved DICT, not its rate. Passing the
    # rate re-entered resolve_market_cap's analyst-override branch and
    # relabelled every table lookup as analyst-entered, which silently
    # disabled the unknown-vintage check (review finding, PR #31).
    assert seen["market_cap"] == stamped["market_cap"]
    assert seen["market_cap_rate"] is None
    assert stamped["ignored_assumptions"] == {}


@pytest.mark.django_db
def test_a_retired_exit_cap_override_is_recorded_as_ignored(deals_dir,
                                                            monkeypatch):
    """A deal saved before the exit cap became derived still carries an
    `exit_cap` in its stored scenario sections. The pipeline ignores it —
    but silently dropping a number the analyst typed is the failure this
    repo keeps catching in review, so the run says which ones it dropped.
    """
    from tests.test_web_runs import _make_extracted_deal, _start_run

    seen = {}
    _capture_run_kwargs(monkeypatch, seen)
    deal = _make_extracted_deal(deals_dir)
    deal.assumption_overrides = {
        "scenario_overrides": {"base": {"exit_cap": 0.081,
                                        "rev_cagr_yr1_3": 0.03}},
        "va_scenario_overrides": {"bull": {"exit_cap": 0.055}},
    }
    deal.save()
    run = _start_run(deal)

    assert run.applied_overrides["assumptions"]["ignored_assumptions"] == {
        "scenario_overrides.base.exit_cap": 0.081,
        "va_scenario_overrides.bull.exit_cap": 0.055,
    }
    # the sections themselves still reach the engine, minus nothing — the
    # retired key is inert there, not stripped
    assert seen["custom_scenarios"]["base"]["rev_cagr_yr1_3"] == 0.03


@pytest.mark.django_db
def test_per_deal_costs_beat_the_global_override_row(deals_dir, monkeypatch):
    """Precedence: config.py default ← global ConfigOverride ← per-deal.
    Both cost keys are exercised — one overridden globally and then again
    per-deal, one overridden globally only."""
    import datetime as dt

    from tests.test_web_runs import _make_extracted_deal, _start_run
    from webapp.models import ConfigOverride

    ConfigOverride.objects.create(key="TRANSACTION_COSTS.acquisition_closing_pct",
                                  value=0.02, effective_date=dt.date(2026, 1, 1))
    ConfigOverride.objects.create(key="TRANSACTION_COSTS.disposition_cost_pct",
                                  value=0.03, effective_date=dt.date(2026, 1, 1))
    seen = {}
    _capture_run_kwargs(monkeypatch, seen)

    deal = _make_extracted_deal(deals_dir)
    deal.assumption_overrides = {
        "hold_years": 8,
        "transaction_costs": {"acquisition_closing_pct": 0.045},
    }
    deal.save()
    run = _start_run(deal)

    assert seen["hold_years"] == 8
    assert seen["transaction_costs"] == {"acquisition_closing_pct": 0.045,
                                         "disposition_cost_pct": 0.03}
    assert (run.applied_overrides["assumptions"]["transaction_costs"]
            == seen["transaction_costs"])
    # The config stamp must not claim the global 0.02 applied — the run
    # used the per-deal 0.045. Same rule as SOLVER_TARGET_IRR, but
    # key-level: the disposition row DID apply and must stay.
    assert run.applied_overrides["config"] == {
        "TRANSACTION_COSTS.disposition_cost_pct": 0.03}


@pytest.mark.django_db
def test_costs_resolve_from_pristine_config_during_a_concurrent_run(deals_dir,
                                                                    monkeypatch):
    """CROSS-DEAL ISOLATION GATE — do not delete.

    TRANSACTION_COSTS is in _PATCHED_DICTS, so the live config dict is
    mutated in place for as long as one deal's run holds _ANALYSIS_LOCK.
    The worker resolves costs BEFORE taking that lock, so resolving off
    the live dict let one deal's percentages leak into a second deal that
    never opted into them — and the unconditional stamp then reported the
    wrong numbers as if they were correct.
    """
    from config import TRANSACTION_COSTS
    from webapp.services import _patched_config, resolve_run_transaction_costs

    pristine = dict(TRANSACTION_COSTS)
    foreign = {"TRANSACTION_COSTS": {"acquisition_closing_pct": 0.09,
                                     "disposition_cost_pct": 0.09}}
    with _patched_config(foreign):          # another deal's run, mid-flight
        leaked = resolve_run_transaction_costs({}, {})
    assert leaked == pristine

    # A deal that DOES carry its own delta still resolves it correctly
    # while the foreign patch is live.
    with _patched_config(foreign):
        mine = resolve_run_transaction_costs(
            {}, {"transaction_costs": {"acquisition_closing_pct": 0.03}})
    assert mine == {"acquisition_closing_pct": 0.03,
                    "disposition_cost_pct": pristine["disposition_cost_pct"]}


def test_market_cap_resolves_from_pristine_config_during_a_concurrent_run():
    """CROSS-DEAL ISOLATION GATE — do not delete.

    MARKET_CAP_RATES is in _PATCHED_DICTS, so the live table is mutated in
    place for as long as one deal's run holds _ANALYSIS_LOCK. The worker
    resolves the market cap BEFORE taking that lock, so resolving off the
    live table would let one deal's cap rates price a second deal's exit —
    and since the resolved value is stamped unconditionally, the run
    record would report the leak as if it were correct.

    This is the same defect the transaction-cost gate above pins, and it
    is worse here: the market cap drives every exit value in the run, so
    a leak moves every published IRR rather than a few basis points.
    """
    class _Cim:
        brv_enclosed_sf = brv_covered_sf = brv_open_sf = None
        cc_pct = None
        year_built = 2015

    from webapp.services import _patched_config, resolve_run_market_cap

    pristine = copy.deepcopy(cfg.MARKET_CAP_RATES)
    foreign = {"MARKET_CAP_RATES": {"Self Storage": {"mid": 0.0999,
                                                     "old": 0.0999}}}
    with _patched_config(foreign):          # another deal's run, mid-flight
        leaked = resolve_run_market_cap({}, {}, _Cim())
    assert leaked["market_cap"] == pristine["Self Storage"][leaked["age_band"]]
    assert leaked["market_cap"] != 0.0999

    # This deal's OWN global delta still resolves while the foreign patch
    # is live — isolation must not mean ignoring the run's own overrides.
    with _patched_config(foreign):
        mine = resolve_run_market_cap(
            {"MARKET_CAP_RATES": {"Self Storage": {"mid": 0.0501}}},
            {}, _Cim())
    assert mine["market_cap"] == 0.0501

    # And a per-deal analyst rate beats both.
    with _patched_config(foreign):
        typed = resolve_run_market_cap({}, {"market_cap_rate": 0.042}, _Cim())
    assert typed["market_cap"] == 0.042
    assert typed["source"] == "analyst"


def test_a_market_cap_patch_merges_one_band_not_the_whole_class_row():
    """A settings override names one cell (class × band). Replacing the
    class row with a bare float would leave resolve_market_cap reading a
    band out of a number."""
    from webapp.services import _patched_config, build_config_patch

    patch, _, _ = build_config_patch({"MARKET_CAP_RATES.Self Storage.mid":
                                      0.0501})
    before = copy.deepcopy(cfg.MARKET_CAP_RATES["Self Storage"])
    with _patched_config(patch):
        row = cfg.MARKET_CAP_RATES["Self Storage"]
        assert row["mid"] == 0.0501
        assert set(row) == set(before)                 # no bands dropped
        assert row["new"] == before["new"]             # siblings untouched
    assert cfg.MARKET_CAP_RATES["Self Storage"] == before


@pytest.mark.django_db
def test_assumptions_baseline_reflects_global_override(deals_dir):
    """With a global scenario override active, the form's initial shows
    the EFFECTIVE value, and saving the form untouched produces NO
    per-deal scenario delta (baseline == effective, not config.py).
    post arg is a QueryDict: parse_unit_mix calls .getlist (review)."""
    from django.http import QueryDict

    from tests.test_web_runs import _make_extracted_deal

    from webapp import services
    from webapp.forms import build_initial, build_overrides, AssumptionsForm
    from webapp.models import ConfigOverride

    ConfigOverride.objects.create(
        key="SCENARIO_DEFAULTS.base.rev_cagr_yr1_3", value=0.07,
        effective_date=datetime.date(2026, 1, 1))
    deal = _make_extracted_deal(deals_dir)
    eff = services.effective_config(deal.asset_type)

    initial = build_initial(deal, eff)
    assert initial["scen_base_rev_cagr_yr1_3"] == 7.0    # 0.07 shown as 7

    form = AssumptionsForm(initial)                      # resubmit as-is
    assert form.is_valid(), form.errors
    out = build_overrides(form.cleaned_data, QueryDict(), deal, eff)
    assert "scenario_overrides" not in out               # no spurious delta


@pytest.mark.django_db
def test_assumptions_explicit_change_still_persists(deals_dir):
    from tests.test_web_runs import _make_extracted_deal

    from webapp import services
    from webapp.forms import build_initial, build_overrides, AssumptionsForm

    from django.http import QueryDict

    deal = _make_extracted_deal(deals_dir)
    eff = services.effective_config(deal.asset_type)
    initial = build_initial(deal, eff)
    initial["scen_base_rev_cagr_yr1_3"] = 8.0            # user edits to 8%
    form = AssumptionsForm(initial)
    assert form.is_valid(), form.errors
    out = build_overrides(form.cleaned_data, QueryDict(), deal, eff)
    assert out["scenario_overrides"]["base"]["rev_cagr_yr1_3"] == 0.08


# ── Phase 5A: comps browser ──────────────────────────────────────────

@pytest.fixture
def comp_db(tmp_path, monkeypatch):
    """Scratch comp DB with three rows (data.comp_db binds COMP_DB_PATH
    at import — patch the module attribute, same as test_web_runs)."""
    path = str(tmp_path / "comps.db")
    monkeypatch.setattr("data.comp_db.COMP_DB_PATH", path)
    import sqlite3

    from data.comp_db import CompDatabase
    db = CompDatabase()          # creates schema
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO properties (property_name, city, state, nrsf,"
            " total_units, occupancy, adjusted_noi, revenue_per_sf,"
            " noi_per_sf, analysis_date, pdf_filename)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [("Alpha Storage", "Belton", "TX", 45000, 350, 0.92, 250000,
              9.3, 5.6, "2026-07-01", "alpha.pdf"),
             ("Bravo Storage", "Denver", "CO", 62000, 480, 0.88, 400000,
              10.1, 6.5, "2026-06-15", "bravo.pdf"),
             ("Small Lockers", "Waco", "TX", 18000, 190, 0.95, 90000,
              8.8, 5.0, "2026-05-20", "small.pdf")])
    return db


@pytest.mark.django_db
def test_comps_page_lists_and_filters(client, operator, comp_db):
    resp = client.get("/comps/")
    content = resp.content.decode()
    assert resp.status_code == 200
    assert "Alpha Storage" in content and "Bravo Storage" in content
    assert "3 comps" in content

    content = client.get("/comps/?state=TX").content.decode()
    assert "Alpha Storage" in content
    assert "Bravo Storage" not in content

    content = client.get("/comps/?state=TX&min_nrsf=40000").content.decode()
    assert "Alpha Storage" in content
    assert "Small Lockers" not in content


@pytest.mark.django_db
def test_comps_csv_export(client, operator, comp_db):
    resp = client.get("/comps/?state=TX&format=csv")
    assert resp["Content-Type"].startswith("text/csv")
    body = resp.content.decode()
    assert body.splitlines()[0].startswith("property_name,")
    assert "Alpha Storage" in body and "Bravo Storage" not in body


@pytest.mark.django_db
def test_comps_page_empty_db(client, operator, tmp_path, monkeypatch):
    monkeypatch.setattr("data.comp_db.COMP_DB_PATH", str(tmp_path / "e.db"))
    resp = client.get("/comps/")
    assert resp.status_code == 200
    assert b"No comps yet" in resp.content


# ── Phase 5A: settings page ──────────────────────────────────────────

@pytest.mark.django_db
def test_settings_page_add_list_delete(client, operator):
    from webapp.models import ConfigOverride

    resp = client.post("/settings/", {
        "key": "GATES.min_irr_5yr", "value": "12", "asset_type": "",
        "effective_date": "2026-07-01", "note": "tighten"})
    assert resp.status_code == 302
    row = ConfigOverride.objects.get()
    assert row.value == 0.12

    content = client.get("/settings/").content.decode()
    assert "GATES.min_irr_5yr" in content
    assert "12%" in content                       # display units
    assert "tighten" in content

    resp = client.post(f"/settings/overrides/{row.pk}/delete/")
    assert resp.status_code == 302
    assert ConfigOverride.objects.count() == 0


@pytest.mark.django_db
def test_settings_page_rejects_bad_value(client, operator):
    from webapp.models import ConfigOverride

    resp = client.post("/settings/", {
        "key": "EXPENSE_BENCHMARKS.property_tax", "value": "5",
        "asset_type": "", "effective_date": "2026-07-01"})
    assert resp.status_code == 200                # re-rendered with errors
    assert b"two numbers" in resp.content
    assert ConfigOverride.objects.count() == 0


@pytest.mark.django_db
def test_settings_status_badges(client, operator):
    import datetime as dt

    from django.utils import timezone

    from webapp.models import ConfigOverride

    today = timezone.localdate()
    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.11,
                                  effective_date=today - dt.timedelta(days=90))
    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.12,
                                  effective_date=today - dt.timedelta(days=1))
    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.15,
                                  effective_date=today + dt.timedelta(days=30))
    ConfigOverride.objects.create(key="GATES.retired_key", value=1,
                                  effective_date=today)
    content = client.get("/settings/").content.decode()
    assert content.count("superseded") == 1
    assert content.count("scheduled") == 1
    assert "unknown key" in content


@pytest.mark.django_db
def test_settings_effective_preview_by_asset_type(client, operator):
    import datetime as dt

    from webapp.models import ConfigOverride

    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.14,
                                  asset_type="Boat & RV Storage",
                                  effective_date=dt.date(2026, 1, 1))
    default_view = client.get("/settings/").content.decode()
    brv = client.get("/settings/?asset_type=Boat+%26+RV+Storage").content.decode()
    # Substring presence alone is self-fulfilling: the override row's
    # "14%" renders in the Overrides table of BOTH responses (review
    # finding). Assert the scope-sensitive part: the BRV preview adds
    # one more "14%" (its effective cell) than the global preview.
    assert brv.count("14%") == default_view.count("14%") + 1
    # and only the BRV preview marks the key changed
    assert brv.count("font-semibold text-accent-700") == \
        default_view.count("font-semibold text-accent-700") + 1


# ── Settings value bounds ────────────────────────────────────────────
#
# The value box was an unbounded CharField. "-5" under Solver Target IRR
# parsed to -0.05, saved without complaint, and the bisection solver ran
# on it. These pin the bound AND the two things a blanket "no negatives"
# would have broken: a bear case with shrinking revenue, and the 0%
# target #44 deliberately made survivable.

def test_every_registry_key_carries_bounds():
    from webapp.forms import override_key_registry

    reg = override_key_registry()
    assert reg, "registry is empty — the rest of this module proves nothing"
    for key, spec in reg.items():
        assert "bounds" in spec, f"{key} has no bounds"
        lo, hi = spec["bounds"]
        assert (lo, hi) != (None, None), f"{key} is bounded on neither side"


def test_every_default_sits_inside_its_own_bounds():
    """THE guard on the bounds table, and the reason it can be derived
    rather than enumerated: config.py is read live, so a key added with a
    default its shape would refuse fails here the moment it lands —
    before anyone discovers it by being unable to override the value."""
    from webapp.forms import bounds_display, dotted_get, override_key_registry

    for key, spec in override_key_registry().items():
        raw = dotted_get(cfg, key)
        vals = list(raw) if isinstance(raw, (list, tuple)) else [raw]
        lo, hi = spec["bounds"]
        for v in vals:
            assert (lo is None or v >= lo) and (hi is None or v <= hi), (
                f"{key} default {v!r} is outside its own bounds "
                f"({bounds_display(spec)})")


def test_a_negative_solver_target_irr_is_refused():
    """The reported defect, verbatim: '-5' used to become -0.05."""
    from django import forms as djf
    from webapp.forms import parse_override_value

    with pytest.raises(djf.ValidationError) as e:
        parse_override_value("SOLVER_TARGET_IRR", "-5")
    assert "between 0% and 100%" in str(e.value)


def test_a_zero_solver_target_irr_still_saves():
    """#44 made a 0% target survive the truthiness guards; a bound of
    (0, 1] would have quietly undone that. Asserting the parse alone
    would pass with this whole feature deleted, so the bound itself is
    asserted — that is the thing a future tightening would break."""
    from webapp.forms import override_key_registry, parse_override_value

    assert override_key_registry()["SOLVER_TARGET_IRR"]["bounds"] == (0.0, 1.0)
    assert parse_override_value("SOLVER_TARGET_IRR", "0") == 0.0


def test_a_share_above_one_hundred_percent_is_refused():
    from django import forms as djf
    from webapp.forms import parse_override_value

    with pytest.raises(djf.ValidationError):
        parse_override_value("GATES.min_physical_occupancy", "150")
    with pytest.raises(djf.ValidationError):
        parse_override_value("VALUE_ADD_SCENARIOS.base.target_occupancy", "120")


def test_a_bear_case_may_still_run_negative_growth():
    """The case a blanket sign check would have broken. Revenue shrinking
    in the downside is a real underwrite, not a typo."""
    from webapp.forms import parse_override_value

    assert parse_override_value(
        "SCENARIO_DEFAULTS.bear.rev_cagr_yr1_3", "-2") == -0.02
    assert parse_override_value(
        "SCENARIO_DEFAULTS.bear.yr1_noi_bump", "-5") == -0.05
    assert parse_override_value(
        "VALUE_ADD_SCENARIOS.bear.post_stabilize_rev_growth", "-1") == -0.01
    # but an occupancy in the same dict is a share, and is not negotiable
    from django import forms as djf
    with pytest.raises(djf.ValidationError):
        parse_override_value("SCENARIO_DEFAULTS.bear.stabilized_occ", "-5")


def test_a_range_endpoint_outside_bounds_is_refused():
    from django import forms as djf
    from webapp.forms import parse_override_value

    # low end negative — the high end alone would have passed
    with pytest.raises(djf.ValidationError):
        parse_override_value("EXPENSE_BENCHMARKS.property_tax", "-1, 2.5")
    # a percentage range over 100%
    with pytest.raises(djf.ValidationError):
        parse_override_value("EXPENSE_BENCHMARKS.opex_revenue_ratio", "35, 150")
    # and the ordinary case still parses
    assert parse_override_value(
        "EXPENSE_BENCHMARKS.property_tax", "1.4, 2.6") == [1.4, 2.6]


def test_dollar_benchmarks_stay_open_above_but_not_below():
    """Deliberate: bounding a $/NRSF benchmark at a round number would be
    re-underwriting through the validator. Only the sign is nonsense —
    so the open upper bound is asserted directly (the parse alone would
    pass with the feature deleted) and the closed lower one is proved by
    a refusal."""
    from django import forms as djf
    from webapp.forms import override_key_registry, parse_override_value

    assert override_key_registry()[
        "REPLACEMENT_COST.ss_driveup_per_sf"]["bounds"] == (0.0, None)
    assert parse_override_value(
        "REPLACEMENT_COST.ss_driveup_per_sf", "500, 900") == [500.0, 900.0]
    with pytest.raises(djf.ValidationError):
        parse_override_value("REPLACEMENT_COST.ss_driveup_per_sf", "-1, 900")


def test_a_zero_market_cap_rate_is_refused():
    """valuation.py:265 reads `exit_noi / exit_cap if exit_cap > 0 else 0`
    — a 0% cap does not raise, it prints an exit value of zero. So the
    cap-rate bound is the one that excludes zero."""
    from django import forms as djf
    from webapp.forms import parse_override_value

    with pytest.raises(djf.ValidationError):
        parse_override_value("MARKET_CAP_RATES.Self Storage.mid", "0")
    assert parse_override_value("MARKET_CAP_RATES.Self Storage.mid", "6.1") \
        == 0.061


def test_months_to_stabilize_cannot_outlast_the_longest_hold():
    from django import forms as djf
    from webapp.forms import parse_override_value

    ceiling = cfg.HOLD_YEARS_RANGE[1] * 12
    assert parse_override_value(
        "VALUE_ADD_SCENARIOS.base.months_to_stabilize", str(ceiling)) == ceiling
    with pytest.raises(djf.ValidationError):
        parse_override_value("VALUE_ADD_SCENARIOS.base.months_to_stabilize",
                             str(ceiling + 1))


def test_a_vintage_year_is_bounded_as_a_year():
    from django import forms as djf
    from webapp.forms import parse_override_value

    assert parse_override_value("GATES.unproven_vintage_year", "2021") == 2021
    with pytest.raises(djf.ValidationError):
        parse_override_value("GATES.unproven_vintage_year", "21")


def test_a_negative_population_gate_is_refused():
    from django import forms as djf
    from webapp.forms import parse_override_value

    with pytest.raises(djf.ValidationError):
        parse_override_value("GATES.population_3mi", "-50000")
    assert parse_override_value("POPULATION_TIERS.preferred_density",
                                "80000") == 80000


@pytest.mark.django_db
def test_settings_page_refuses_an_out_of_range_value(client, operator):
    """The whole path, not just the parser: the POST is re-rendered with
    the error and no row is written."""
    from webapp.models import ConfigOverride

    resp = client.post("/settings/", {
        "key": "SOLVER_TARGET_IRR", "value": "-5",
        "asset_type": "", "effective_date": "2026-07-01"})
    assert resp.status_code == 200
    assert b"must be between 0% and 100%" in resp.content
    assert ConfigOverride.objects.count() == 0


@pytest.mark.django_db
def test_a_stored_out_of_range_row_is_badged(client, operator):
    """Bounds at the form cannot reach a row saved before they existed.
    Such a row still resolves into runs, so the page says so rather than
    rendering it as an ordinary number."""
    from django.utils import timezone

    from webapp.models import ConfigOverride

    ConfigOverride.objects.create(key="SOLVER_TARGET_IRR", value=-0.05,
                                  effective_date=timezone.localdate())
    content = client.get("/settings/").content.decode()
    assert "out of range" in content

    ConfigOverride.objects.all().delete()
    ConfigOverride.objects.create(key="SOLVER_TARGET_IRR", value=0.12,
                                  effective_date=timezone.localdate())
    assert "out of range" not in client.get("/settings/").content.decode()


@pytest.mark.django_db
def test_the_effective_preview_shows_each_key_its_allowed_range(client,
                                                                operator):
    import re

    from webapp.forms import override_key_registry

    n = len(override_key_registry())
    content = client.get("/settings/").content.decode()
    # Assert the PAIRING, not either half. "Accepts …" alone appears on
    # the key picker's options too, so a substring check passes with this
    # hover deleted (measured: it did); "decoration-dotted" alone passes
    # with the title deleted. The page carries the string twice per key —
    # once per surface — and both counts are pinned.
    paired = re.findall(r'title="Accepts [^"]+"\s+class="decoration-dotted',
                        content)
    assert len(paired) == n
    assert content.count('title="Accepts ') == 2 * n
    assert 'title="Accepts between 0% and 100%"' in content
    assert 'title="Accepts at least 0"' in content       # $/SF, open above


@pytest.mark.django_db
def test_the_key_picker_carries_each_range_as_an_option_tooltip(client,
                                                                operator):
    """The bound has to be reachable BEFORE the value box is typed into;
    otherwise the only channel is the rejection message."""
    from webapp.forms import ConfigOverrideForm, override_key_registry

    html = str(ConfigOverrideForm()["key"])
    assert 'title="Accepts between 0% and 100%"' in html
    assert 'title="Accepts at least 0"' in html          # $/SF, open above
    assert html.count('title="Accepts ') == len(override_key_registry())
    # and it renders on the real page, not just in isolation
    assert 'title="Accepts ' in client.get("/settings/").content.decode()


@pytest.mark.django_db
def test_a_superseded_out_of_range_row_is_not_told_it_reaches_runs(client,
                                                                   operator):
    """A red 'still applies to runs' chip on a row the precedence rules
    already retired is the page contradicting itself."""
    import datetime as dt

    from django.utils import timezone

    from webapp.models import ConfigOverride

    today = timezone.localdate()
    ConfigOverride.objects.create(key="SOLVER_TARGET_IRR", value=-0.05,
                                  effective_date=today - dt.timedelta(days=2))
    ConfigOverride.objects.create(key="SOLVER_TARGET_IRR", value=0.12,
                                  effective_date=today - dt.timedelta(days=1))
    content = client.get("/settings/").content.decode()
    assert content.count("out of range") == 1
    assert "Superseded, so it does not reach a run" in content
    assert "It reaches runs today" not in content


@pytest.mark.django_db
def test_the_out_of_range_note_branches_on_all_four_statuses(client, operator):
    """A scheduled row has not taken effect either — resolve_config_overrides
    filters `effective_date__lte` — so a note that only special-cased
    'superseded' told a scheduled row 'it does reach runs', which is false.
    Review finding on PR #45, caught by two agents independently."""
    import datetime as dt

    from django.utils import timezone

    from webapp.models import ConfigOverride

    today = timezone.localdate()
    ConfigOverride.objects.create(key="SOLVER_TARGET_IRR", value=-0.05,
                                  effective_date=today + dt.timedelta(days=30))
    content = client.get("/settings/").content.decode()
    assert "out of range" in content
    assert "not yet" in content
    assert "It reaches runs today" not in content
    assert "does not reach a run" not in content

    # and the active row, which genuinely does reach runs, says so
    ConfigOverride.objects.all().delete()
    ConfigOverride.objects.create(key="SOLVER_TARGET_IRR", value=-0.05,
                                  effective_date=today)
    content = client.get("/settings/").content.decode()
    assert "It reaches runs today" in content
    assert "not yet" not in content


def test_every_registry_kind_matches_the_shape_of_its_config_value():
    """`kind` is what `parse_override_value` branches on, and getting it
    wrong is silent in the worst way. A (low, high) pair registered as a
    scalar accepts a single number and stores a float where the model
    unpacks a pair — `analysis.value_add._pct_band` raises TypeError on
    that, `engine` catches it, and the entire value-add section vanishes
    from the memo with nothing on screen saying why.

    Derived from the live config rather than a list of known range keys:
    a survivor of exactly this mutation is what prompted the test, and a
    hand-listed version would only have covered the two keys that already
    existed."""
    from webapp.forms import dotted_get, override_key_registry

    for key, spec in override_key_registry().items():
        value = dotted_get(cfg, key)
        is_pair = isinstance(value, (list, tuple))
        assert (spec["kind"] == "range") == is_pair, (
            f"{key} is registered kind={spec['kind']!r} but its config "
            f"value is {value!r}")
