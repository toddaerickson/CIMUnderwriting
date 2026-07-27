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
