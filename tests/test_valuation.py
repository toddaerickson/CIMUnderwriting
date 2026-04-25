"""Tests for valuation scenario engine."""

import pytest
from analysis.valuation import run_scenarios
from registry import ScenarioType


def test_run_scenarios_returns_three():
    """run_scenarios produces bear/base/bull."""
    results = run_scenarios(
        adjusted_ttm_noi=300_000,
        asking_price=4_000_000,
        nrsf=50_000,
    )
    for scen in ScenarioType:
        assert scen in results, f"Missing scenario: {scen}"


def test_base_irr_positive_at_reasonable_price():
    """At a reasonable cap rate, base IRR should be positive."""
    results = run_scenarios(
        adjusted_ttm_noi=300_000,
        asking_price=4_000_000,  # ~7.5% cap
        nrsf=50_000,
    )
    base = results[ScenarioType.BASE]
    assert base["irr"] is not None
    assert base["irr"] > 0


def test_bear_irr_less_than_bull():
    """Bear IRR should be lower than bull IRR."""
    results = run_scenarios(
        adjusted_ttm_noi=300_000,
        asking_price=4_000_000,
        nrsf=50_000,
    )
    assert results[ScenarioType.BEAR]["irr"] < results[ScenarioType.BULL]["irr"]


def test_exit_cap_ge_entry_cap_base():
    """Base case should enforce exit cap >= entry cap."""
    results = run_scenarios(
        adjusted_ttm_noi=300_000,
        asking_price=4_000_000,
        nrsf=50_000,
    )
    base = results[ScenarioType.BASE]
    assert base["exit_cap"] >= base["entry_cap"]


def test_noi_projection_has_five_years():
    """Each scenario should have a 5-year NOI projection."""
    results = run_scenarios(
        adjusted_ttm_noi=300_000,
        asking_price=4_000_000,
        nrsf=50_000,
    )
    for scen in ScenarioType:
        assert len(results[scen]["noi_projection"]) == 5


def test_expense_ratio_affects_irr():
    """Different expense ratios should produce different IRRs."""
    irr_low = run_scenarios(
        adjusted_ttm_noi=300_000, asking_price=4_000_000,
        nrsf=50_000, expense_ratio=0.35,
    )[ScenarioType.BASE]["irr"]

    irr_high = run_scenarios(
        adjusted_ttm_noi=300_000, asking_price=4_000_000,
        nrsf=50_000, expense_ratio=0.55,
    )[ScenarioType.BASE]["irr"]

    assert irr_low > irr_high
