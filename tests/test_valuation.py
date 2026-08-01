"""Tests for valuation scenario engine."""

import pytest
from analysis.valuation import resolve_market_cap, run_scenarios
from registry import ScenarioType

#: A market anchor passed explicitly wherever a test asserts on a cap
#: value, so the expected number is hand-computable from the rule rather
#: than being whatever `config.MARKET_CAP_RATES` happens to hold today —
#: that table is an operator-maintained starting point and will move.
PINNED_ANCHOR = resolve_market_cap(market_cap=0.0625)


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


def test_exit_cap_coercion_is_recorded_not_just_applied():
    """A 10% entry cap forces the base case's derived exit cap up. The
    scenario must say the cap it used is not the cap that was asked for —
    otherwise the returns are computed on a number nobody entered
    (analysis.checks.exit_cap_coercion reads these two keys).

    Base at a 6.25% anchor over the default five-year hold:
        6.25% + 0 bp scenario spread + 7.5 bp/yr × 5 yrs = 6.625%
    """
    coerced = run_scenarios(
        adjusted_ttm_noi=400_000,
        asking_price=4_000_000,       # 10% entry cap, above every exit cap
        nrsf=50_000,
        market_cap=PINNED_ANCHOR,
    )[ScenarioType.BASE]
    assert coerced["exit_cap_coerced"] is True
    assert coerced["requested_exit_cap"] == pytest.approx(0.06625, abs=1e-12)
    assert coerced["exit_cap"] == coerced["entry_cap"] == 0.10

    untouched = run_scenarios(
        adjusted_ttm_noi=200_000,
        asking_price=4_000_000,       # 5% entry cap, below every exit cap
        nrsf=50_000,
        market_cap=PINNED_ANCHOR,
    )[ScenarioType.BASE]
    assert untouched["exit_cap_coerced"] is False
    assert untouched["requested_exit_cap"] == untouched["exit_cap"] \
        == pytest.approx(0.06625, abs=1e-12)


def test_bull_exit_cap_is_never_coerced():
    """The exit >= entry rule is base/bear only — bull is allowed to
    underwrite cap compression, and must not be reported as coerced.

    Bull at a 6.25% anchor over five years:
        6.25% − 100 bp scenario spread + 5 bp/yr × 5 yrs = 5.50%
    """
    bull = run_scenarios(
        adjusted_ttm_noi=400_000,
        asking_price=4_000_000,
        nrsf=50_000,
        market_cap=PINNED_ANCHOR,
    )[ScenarioType.BULL]
    assert bull["exit_cap_coerced"] is False
    assert bull["exit_cap"] == bull["requested_exit_cap"] \
        == pytest.approx(0.055, abs=1e-12)


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
