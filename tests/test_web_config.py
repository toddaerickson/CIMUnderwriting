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
    from gui.deal_manager import ASSET_TYPES, detect_asset_type

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
        "label": "Min Irr 5Yr"}
    assert reg["GATES.population_3mi"]["int"] is True
    assert reg["GATES.population_3mi"]["pct"] is False
    assert reg["EXPENSE_BENCHMARKS.property_tax"]["kind"] == "range"
    assert reg["EXPENSE_BENCHMARKS.property_tax"]["pct"] is False
    assert reg["EXPENSE_BENCHMARKS.mgmt_fee_pct"]["pct"] is True
    assert reg["REPLACEMENT_COST.soft_cost_pct"]["pct"] is True
    assert reg["SCENARIO_DEFAULTS.base.exit_cap"]["kind"] == "scalar"
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
        "SCENARIO_DEFAULTS.base.exit_cap": 0.07,
        "SOLVER_TARGET_IRR": 0.12,
        "GATES.retired_key_from_2025": 1.0,      # unknown → skipped, warned
    }
    patch, solver, skipped = build_config_patch(deltas)
    assert solver == 0.12
    assert patch["GATES"] == {"min_irr_5yr": 0.12}
    assert patch["EXPENSE_BENCHMARKS"] == {"property_tax": [1.4, 2.6]}
    assert patch["SCENARIO_DEFAULTS"] == {ScenarioType.BASE: {"exit_cap": 0.07}}
    assert skipped == ["GATES.retired_key_from_2025"]
    assert "retired_key_from_2025" in caplog.text


def test_patched_config_mutates_in_place_and_restores():
    # Importers bound these dict OBJECTS at import time — the patch must
    # be visible through those bindings, then fully restored.
    from analysis.filters import GATES as bound_gates
    from analysis.physical import REPLACEMENT_COST as bound_rc
    from analysis.valuation import SCENARIO_DEFAULTS as bound_scen
    from registry import ScenarioType
    from webapp.services import _patched_config

    orig_irr = bound_gates["min_irr_5yr"]
    orig_exit = bound_scen[ScenarioType.BASE]["exit_cap"]
    orig_rc = bound_rc["ss_driveup_per_sf"]
    orig_alias = bound_rc["non_cc_per_sf"]
    patch = {
        "GATES": {"min_irr_5yr": 0.12, "not_a_key": 9},
        "SCENARIO_DEFAULTS": {ScenarioType.BASE: {"exit_cap": 0.07}},
        "REPLACEMENT_COST": {"ss_driveup_per_sf": [100, 120]},
    }
    with _patched_config(patch):
        assert bound_gates["min_irr_5yr"] == 0.12
        assert "not_a_key" not in bound_gates
        assert bound_scen[ScenarioType.BASE]["exit_cap"] == 0.07
        assert tuple(bound_rc["ss_driveup_per_sf"]) == (100, 120)
        # legacy alias synced (analysis/physical.py:151-154 reads it)
        assert tuple(bound_rc["non_cc_per_sf"]) == (100, 120)
    assert bound_gates["min_irr_5yr"] == orig_irr
    assert bound_scen[ScenarioType.BASE]["exit_cap"] == orig_exit
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
    # wholesale) and an unknown key that must land in config_skipped
    ConfigOverride.objects.create(key="SCENARIO_DEFAULTS.base.exit_cap",
                                  value=0.07,
                                  effective_date=dt.date(2026, 1, 1))
    seen = {}

    def _fake(result, progress=None, output_dir=None, custom_scenarios=None,
              custom_va_scenarios=None, solver_target_irr=None):
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
    # (per-deal section wins wholesale), nothing was unknown
    assert run.applied_overrides["config"] == {
        "GATES.min_irr_5yr": 0.13, "SOLVER_TARGET_IRR": 0.12}
    assert run.applied_overrides["config_skipped"] == []
    assert run.applied_overrides["assumptions"]["solver_target_irr"] == 0.15
    from analysis.filters import GATES
    assert GATES["min_irr_5yr"] == 0.10          # restored after the run
