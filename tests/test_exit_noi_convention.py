"""EXIT_NOI_CONVENTION — design decision 5, settled 2026-08-10.

The exit used to be trailing by construction and forward only in the
design doc's oracle, with CLAUDE.md calling the judgment "deferred, not
settled". It is now a named config convention, default "trailing" so no
published number moves, implemented in BOTH exit engines — the static
DCF (`analysis.valuation.project_cash_flows`) and the value-add model —
through the one selector `resolve_exit_noi`.

The forward expectations below are DERIVED FROM THE RESULT'S OWN SERIES,
never asserted as "~3%": revenue and expenses grow at different rates
and NOI is their difference, so the true forward step is ~+2.2% on the
base params and ~+0.4% on bear (expense growth outruns revenue growth) —
a single-rate step would overstate both.
"""
import pytest

import config as cfg
from analysis.valuation import project_cash_flows, resolve_exit_noi
from registry import ScenarioType

BASE_PARAMS = cfg.SCENARIO_DEFAULTS[ScenarioType.BASE]
BEAR_PARAMS = cfg.SCENARIO_DEFAULTS[ScenarioType.BEAR]


def _project(params=BASE_PARAMS, hold_years=5, **kwargs):
    kwargs.setdefault("exit_cap", 0.065)
    kwargs.setdefault("coerce_exit_cap", False)
    return project_cash_flows(650_000, 10_000_000, 0, dict(params),
                              hold_years=hold_years, **kwargs)


def _forward_noi(result, params, hold_years):
    """The forward step, recomputed from the result's own series with the
    loop's own banding rule (`yr <= 3` picks the early band)."""
    next_year = hold_years + 1
    rev_growth = (params["rev_cagr_yr1_3"] if next_year <= 3
                  else params["rev_cagr_yr4_5"])
    return (result["revenue"][-1] * (1 + rev_growth)
            - result["expenses"][-1] * (1 + params["exp_growth"]))


def test_trailing_is_the_default_and_capitalizes_the_terminal_year():
    assert cfg.EXIT_NOI_CONVENTION == "trailing"
    result = _project()
    assert result["exit_value"] == pytest.approx(
        result["noi"][-1] / result["exit_cap"])


def test_forward_capitalizes_one_more_step_of_the_two_series(monkeypatch):
    monkeypatch.setattr(cfg, "EXIT_NOI_CONVENTION", "forward")
    result = _project()
    expected = _forward_noi(result, BASE_PARAMS, 5)
    assert result["exit_value"] == pytest.approx(
        expected / result["exit_cap"])
    # And it is genuinely a different exit than trailing.
    assert expected != pytest.approx(result["noi"][-1])


def test_forward_is_not_a_single_rate_step_on_bear_params(monkeypatch):
    """Bear grows expenses faster than revenue, so forward NOI must come
    in BELOW noi * (1 + rev_growth). This is the assertion that fails if
    the forward step ever degrades to NOI x (1 + g)."""
    monkeypatch.setattr(cfg, "EXIT_NOI_CONVENTION", "forward")
    result = _project(params=BEAR_PARAMS)
    forward = result["exit_value"] * result["exit_cap"]
    single_rate = result["noi"][-1] * (1 + BEAR_PARAMS["rev_cagr_yr4_5"])
    assert forward < single_rate
    assert forward == pytest.approx(_forward_noi(result, BEAR_PARAMS, 5))


def test_forward_uses_the_early_band_on_a_short_hold(monkeypatch):
    """On a 2-year hold the forward year is year 3, still inside the
    early band — the banding rule is the LOOP's own, not 'the post-ladder
    rate'."""
    monkeypatch.setattr(cfg, "EXIT_NOI_CONVENTION", "forward")
    result = _project(hold_years=2)
    assert result["exit_value"] == pytest.approx(
        _forward_noi(result, BASE_PARAMS, 2) / result["exit_cap"])


def test_an_unknown_convention_raises(monkeypatch):
    """A misspelled convention silently priced as trailing would be a
    wrong exit on every deal — it raises instead."""
    monkeypatch.setattr(cfg, "EXIT_NOI_CONVENTION", "year6")
    with pytest.raises(ValueError, match="EXIT_NOI_CONVENTION"):
        _project()


def test_the_convention_is_read_at_call_time_not_frozen_at_import():
    """The monkeypatch tests above only prove anything because the read
    happens per call; this pins it explicitly, same trap and same test
    shape as SOLVER_TARGET_IRR."""
    assert resolve_exit_noi(1.0, 2.0) == 1.0
    try:
        cfg.EXIT_NOI_CONVENTION = "forward"
        assert resolve_exit_noi(1.0, 2.0) == 2.0
    finally:
        cfg.EXIT_NOI_CONVENTION = "trailing"


def test_both_engines_report_the_noi_they_capitalized(monkeypatch):
    """Review finding: the selector's result was consumed and dropped.

    Under "forward" the capitalized NOI is one step PAST the hold, so it
    appears in no NOI series any surface prints — an exit value shown
    beside the year-N row would not divide to the exit cap shown under
    it, and the true figure would be recoverable only by multiplying
    back. Both engines now return it, with the convention beside it so a
    reader knows which year it is.
    """
    from model.value_add_model import _run_single_va_scenario

    va_kwargs = dict(
        name=ScenarioType.BASE,
        params=dict(cfg.VALUE_ADD_SCENARIOS[ScenarioType.BASE]),
        in_place_rent_psf=10.0, market_rent_psf=14.0, nrsf=50_000,
        current_occ=0.80, monthly_expenses_start=20_000.0,
        asking_price=10_000_000, capex=250_000, hold_years=5,
        market_cap={"market_cap": 0.06, "source": "analyst"},
    )

    for convention in ("trailing", "forward"):
        monkeypatch.setattr(cfg, "EXIT_NOI_CONVENTION", convention)
        static = _project()
        va = _run_single_va_scenario(**va_kwargs)
        for result in (static, va):
            assert result["exit_noi_convention"] == convention
            # The reported figure is the one the exit value was built
            # from — that tie is the whole point of returning it.
            assert result["exit_value"] == pytest.approx(
                result["exit_noi"] / result["exit_cap"])

    # And under forward it is genuinely NOT the last projected year, so
    # a surface printing that row instead would be printing the wrong
    # number.
    monkeypatch.setattr(cfg, "EXIT_NOI_CONVENTION", "forward")
    forward = _project()
    assert forward["exit_noi"] != pytest.approx(forward["noi"][-1])


def test_the_workbook_and_memo_name_the_forward_year(monkeypatch):
    """The label has to move with the number. Under trailing both
    surfaces are byte-identical to what they always printed (which is
    why the characterization snapshots do not move); under forward they
    say which year they capitalized."""
    from output.excel_writer import _exit_noi_label

    trailing_scen = {"exit_noi_convention": "trailing"}
    assert _exit_noi_label(trailing_scen, 5) == "Year 5 NOI"

    forward_scen = {"exit_noi_convention": "forward"}
    assert _exit_noi_label(forward_scen, 5) == "Year 6 NOI (forward)"


def test_the_value_add_engine_flips_with_the_convention(monkeypatch):
    """One run, one convention (operator, 2026-08-10): the VA engine's
    exit must move with the same config name the static DCF reads.

    The forward expectation is an independent closed-form oracle: with
    the hold past stabilization, month m's rent is target x (1+g_m)^(m -
    months_to_stab) at target occupancy, and expenses grow at their own
    monthly rate — so year N+1's NOI is a geometric sum the test can
    compute without the engine.
    """
    from model.value_add_model import _run_single_va_scenario

    params = dict(cfg.VALUE_ADD_SCENARIOS[ScenarioType.BASE])
    kwargs = dict(
        name=ScenarioType.BASE, params=params,
        in_place_rent_psf=10.0, market_rent_psf=14.0, nrsf=50_000,
        current_occ=0.80, monthly_expenses_start=20_000.0,
        asking_price=10_000_000, capex=250_000, hold_years=5,
        market_cap={"market_cap": 0.06, "source": "analyst"},
    )

    trailing = _run_single_va_scenario(**kwargs)
    monkeypatch.setattr(cfg, "EXIT_NOI_CONVENTION", "forward")
    forward = _run_single_va_scenario(**kwargs)

    # Trailing: the final hold year's own NOI, exactly as before.
    assert trailing["exit_value"] == pytest.approx(
        trailing["annual_noi"][-1] / trailing["exit_cap"])

    # Forward: the next 12 months of the same projection.
    months_to_stab = int(params["months_to_stabilize"])
    hold_months = 60
    rent_gap = 14.0 - 10.0
    target_rent = 10.0 + rent_gap * params["rent_growth_to_market"]
    g_rev = (1 + params["post_stabilize_rev_growth"]) ** (1 / 12) - 1
    g_exp = (1 + params["expense_growth"]) ** (1 / 12) - 1
    expected_forward_noi = sum(
        (target_rent * (1 + g_rev) ** (m - months_to_stab)
         * 50_000 * params["target_occupancy"])
        - 20_000.0 * (1 + g_exp) ** m
        for m in range(hold_months, hold_months + 12))
    assert forward["exit_value"] == pytest.approx(
        expected_forward_noi / forward["exit_cap"])

    # The hold-period series themselves must NOT move with the
    # convention — only the exit does.
    assert forward["annual_noi"] == trailing["annual_noi"]
    assert len(forward["monthly_noi"]) == hold_months
