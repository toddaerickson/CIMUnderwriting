"""Tests for the bisection solvers — unlevered (item B) and levered (E4)."""

import pytest

import config as cfg
from model.solver import (_monotonicity_warning, solve_max_price,
                          solve_max_price_levered)
from model.waterfall import WaterfallTerms
from registry import ScenarioType


def test_solver_converges():
    """Solver should converge within tolerance."""
    result = solve_max_price(adjusted_ttm_noi=300_000)
    assert result["converged"] is True
    assert result["max_price"] > 0


def test_solver_achieves_target_irr():
    """Achieved IRR should be within 0.5% of 10% target."""
    result = solve_max_price(adjusted_ttm_noi=300_000)
    assert abs(result["achieved_irr"] - 0.10) < 0.005


def test_solver_max_price_reasonable():
    """Max price should imply a cap rate between 3% and 20%."""
    result = solve_max_price(adjusted_ttm_noi=300_000)
    cap = result["implied_entry_cap"]
    assert 0.03 < cap < 0.20


def test_solver_capex_reduces_price():
    """Adding CapEx should reduce the max purchase price."""
    no_capex = solve_max_price(adjusted_ttm_noi=300_000, capex=0)
    with_capex = solve_max_price(adjusted_ttm_noi=300_000, capex=200_000)
    assert with_capex["max_price"] < no_capex["max_price"]


def test_solver_total_basis_includes_capex_and_closing_costs():
    """Total basis = max_price + capex + acquisition closing costs.

    Closing costs joined this identity in item B; before that the basis
    was price + capex and every solved IRR was overstated by their
    omission. `tests/test_transaction_costs.py` owns the cost oracles."""
    capex = 100_000
    result = solve_max_price(adjusted_ttm_noi=300_000, capex=capex)
    expected = result["max_price"] + capex + result["acquisition_cost"]
    assert abs(result["total_basis"] - expected) < 1
    assert result["acquisition_cost"] > 0


# ── Item E4: the levered solver ─────────────────────────────────────


def test_levered_solver_converges_on_the_lp_net_target():
    result = solve_max_price_levered(adjusted_ttm_noi=300_000)
    assert result["converged"] is True
    assert result["max_price"] > 0
    assert result["lp_net_irr"] == pytest.approx(cfg.SOLVER_TARGET_LP_NET_IRR,
                                                 abs=0.005)
    # `achieved_irr` is the same number under the name the shared writers
    # read; both must be present and agree, or the Excel tab and the memo
    # would render different figures from one payload.
    assert result["achieved_irr"] == result["lp_net_irr"]


def test_levered_solved_price_reproduces_through_the_production_stack():
    """THE item E4 acceptance test: re-run the solved price FORWARD.

    The solver assembles the stack itself, so a bug in that assembly
    would be invisible to any test that only asks the solver to check its
    own work. This re-prices the solved answer through
    `build_returns_model` — the path the results page, memo and workbook
    actually use — and requires the LP net IRR to come back the same.

    Exact equality, not a tolerance: both routes call the same builders
    with the same arguments, so any daylight between them is a real
    divergence rather than float drift. It reproduces bit-for-bit today.
    """
    from model.returns_model import build_returns_model

    solved = solve_max_price_levered(adjusted_ttm_noi=300_000)
    model = build_returns_model(adjusted_ttm_noi=300_000,
                                asking_price=solved["max_price"],
                                nrsf=50_000, capex=0)
    base = model["levered"][ScenarioType.BASE]

    assert base["lp_net_irr"] == solved["lp_net_irr"]
    assert model["debt"]["loan"] == solved["senior_debt"]
    assert model["sources_uses"]["total_equity"] == solved["total_equity"]
    assert model["sources_uses"]["total_uses"] == solved["total_uses"]


def test_levered_solver_sources_uses_tie_holds_at_the_solved_price():
    """`Total Uses == total_basis + financing_costs`, to the cent.

    The same identity `analysis.checks.sources_uses_ties` enforces on
    every run. Financing costs stay OUT of the unlevered basis (CLAUDE.md
    key design decision 3), so the solved payload has to report both
    sides — a consumer that subtracts debt from `total_basis` instead of
    `total_uses` gets the wrong equity.
    """
    solved = solve_max_price_levered(adjusted_ttm_noi=300_000, capex=150_000)
    assert solved["total_uses"] == pytest.approx(
        solved["total_basis"] + solved["financing_costs"], abs=0.01)
    assert solved["total_equity"] == pytest.approx(
        solved["total_uses"] - solved["senior_debt"], abs=0.01)
    assert solved["financing_costs"] > 0


def test_levered_solver_capex_reduces_price():
    no_capex = solve_max_price_levered(adjusted_ttm_noi=300_000, capex=0)
    with_capex = solve_max_price_levered(adjusted_ttm_noi=300_000,
                                         capex=200_000)
    assert with_capex["max_price"] < no_capex["max_price"]


def test_levered_solver_carries_its_assumption_stamp():
    """No LP net IRR leaves the building without its stamp — and a max
    offer PRICED off an LP net IRR inherits the rule (CLAUDE.md key
    design decision 7). Six LPA questions each move this price."""
    solved = solve_max_price_levered(adjusted_ttm_noi=300_000)
    stamp = solved["assumption_stamp"]
    assert {row["key"] for row in stamp} == {
        "pref_compounding", "accrual_base", "ordering", "am_fee_treatment",
        "promote_basis", "catch_up"}
    # The AM-fee row must name the rate and base actually charged;
    # without them "net" is unqualified.
    am_fee = next(r for r in stamp if r["key"] == "am_fee_treatment")
    assert am_fee["rate"] == cfg.AM_FEE_PCT
    assert am_fee["base"] == cfg.AM_FEE_BASE


def test_levered_solver_reads_its_target_at_call_time(monkeypatch):
    """Rebinding config must be seen — the solver must not freeze the
    target as a default argument at import. The unlevered pair used to be
    the exception to that rule and no longer is; its equivalent lives in
    `tests/test_config_single_source.py`."""
    monkeypatch.setattr(cfg, "SOLVER_TARGET_LP_NET_IRR", 0.20)
    solved = solve_max_price_levered(adjusted_ttm_noi=300_000)
    assert solved["target_irr"] == 0.20
    assert solved["lp_net_irr"] == pytest.approx(0.20, abs=0.005)


def test_levered_solver_target_is_a_higher_bar_than_the_unlevered_one():
    """A 15% LP NET target prices lower than a 10% unlevered one on
    config defaults. Not a law of nature — leverage can be accretive —
    but on this repo's terms the loan constant sits above the yield on
    cost, so it must not silently invert."""
    unlevered = solve_max_price(adjusted_ttm_noi=300_000)
    levered = solve_max_price_levered(adjusted_ttm_noi=300_000)
    assert levered["max_price"] < unlevered["max_price"]


def test_levered_solver_honours_the_deals_gp_coinvest():
    """The co-invest must reach BOTH the capital stack and the waterfall.

    Resolved independently they diverge, which is the defect
    `resolve_waterfall_terms` documents: a stack split 25/75 beside a
    promote computed on config's 10/90, neither flagged. The stamp is
    where that divergence would show, so the stamp is what this asserts.
    """
    solved = solve_max_price_levered(adjusted_ttm_noi=300_000,
                                     gp_coinvest_pct=0.25)
    promote_row = next(r for r in solved["assumption_stamp"]
                       if r["key"] == "promote_basis")
    assert "25%" in promote_row["label"]      # the deal's, not config's 10%


def test_levered_max_price_is_invariant_to_gp_coinvest_under_this_promote():
    """The solved price does NOT move with GP co-invest, and that is
    arithmetic rather than a bug — worth pinning because every reader
    expects the opposite.

    Under the shipped basis the promote comes off the top and the
    remainder splits pro rata, so with `s = 1 - gp_coinvest_pct`:

        lp_contribution = s x contribution
        lp_distribution = s x (tier1 + residual x (1 - promote_split))

    Every LP flow carries the same factor `s`, and IRR is scale
    invariant, so it cancels. The LP earns the same RATE on a smaller
    cheque, and the price that clears 15% is the same price.

    This is specific to that basis. `split_then_promote` charges
    `promote_split x residual` regardless of `s` while the LP funds it
    out of `s x residual`, so `s` stops factoring out and the price
    falls — asserted below, because a property that holds under one
    convention only says something if the other is shown to break it.
    """
    at_10 = solve_max_price_levered(adjusted_ttm_noi=300_000,
                                    gp_coinvest_pct=0.10)
    at_25 = solve_max_price_levered(adjusted_ttm_noi=300_000,
                                    gp_coinvest_pct=0.25)
    assert at_10["max_price"] == at_25["max_price"]
    assert at_10["lp_net_irr"] == pytest.approx(at_25["lp_net_irr"], abs=1e-12)
    # Total equity is GP + LP, so it is invariant too; what moves is only
    # how that equity is split, which the Sources & Uses block owns.
    assert at_10["total_equity"] == at_25["total_equity"]

    # The alternative basis, exercised through the solver rather than
    # merely supported by it — an unexercised branch is how a convention
    # one setting away rots.
    def on_top(coinvest):
        # The co-invest goes in the TERMS as well as the kwarg: an
        # explicit `waterfall_terms` bypasses `resolve_waterfall_terms`,
        # so config's 10/90 would split the promote beside a 25/75 stack
        # — the exact divergence the solver's own comment warns about,
        # and it silently flattens this test to an equality.
        terms = WaterfallTerms(**dict(cfg.WATERFALL_TERMS,
                                      pref_rate=cfg.PREF_RATE_LEVERED,
                                      gp_coinvest_pct=coinvest,
                                      promote_basis="split_then_promote"))
        return solve_max_price_levered(adjusted_ttm_noi=300_000,
                                       gp_coinvest_pct=coinvest,
                                       waterfall_terms=terms)

    was_10, was_25 = on_top(0.10), on_top(0.25)
    assert was_25["max_price"] < was_10["max_price"]
    assert was_10["max_price"] - was_25["max_price"] > 25_000
    # Both still hit the target — it is the PRICE that gave way, which is
    # what a solver is for. Compared against the TARGET rather than
    # against each other, and at the solver's own documented 0.1%
    # precision (decision 8): two bisections that stop at different
    # prices land on slightly different sides of 15%, and a tolerance
    # tighter than the solver's would be testing the bisection's
    # rounding rather than this convention.
    for solved in (was_10, was_25):
        assert solved["lp_net_irr"] == pytest.approx(solved["target_irr"],
                                                     abs=1e-3)


# ── Monotonicity: the assumption bisection rests on ──────────────────


def test_monotonicity_warning_is_silent_on_a_decreasing_objective():
    assert _monotonicity_warning(
        [(1_000_000, 0.30), (2_000_000, 0.20), (3_000_000, 0.10)]) is None
    # Unsolvable prices carry no information about monotonicity.
    assert _monotonicity_warning(
        [(1_000_000, 0.30), (2_000_000, None), (3_000_000, 0.10)]) is None
    assert _monotonicity_warning([]) is None


def test_monotonicity_warning_fires_when_irr_rises_with_price():
    """The backstop has to actually fire, or it is decoration.

    Samples are checked in PRICE order, not call order — bisection walks
    the bracket out of order, so an inversion between two prices it
    visited many iterations apart still has to be caught.
    """
    warning = _monotonicity_warning(
        [(3_000_000, 0.20), (1_000_000, 0.10), (2_000_000, 0.15)])
    assert warning is not None
    assert "RISES with price" in warning
    assert "exit-cap floor" in warning


def test_levered_objective_is_monotone_over_the_underwriting_range():
    """A must-never-break invariant as a red-test CI gate, not a comment.

    Bisection is only valid if LP net IRR falls as price rises. The
    exit-cap floor pushes the other way (see `model/solver.py`), so this
    sweeps the objective across the coerced region and out the far side
    and requires every step to be strictly decreasing. If a future change
    to the exit cap, the debt terms or the waterfall breaks that, the
    solved max price becomes untrustworthy and this test says so.
    """
    from analysis.valuation import (project_cash_flows, resolve_exit_cap,
                                    resolve_market_cap,
                                    resolve_transaction_costs)
    from config import SCENARIO_DEFAULTS
    from model.debt import build_debt_schedule, resolve_debt_terms
    from model.levered import build_levered_returns
    from model.returns_model import build_sources_uses
    from model.waterfall import resolve_waterfall_terms

    ttm = 300_000
    params = SCENARIO_DEFAULTS[ScenarioType.BASE]
    costs = resolve_transaction_costs(None)
    terms = resolve_debt_terms()
    wf = resolve_waterfall_terms(
        capital_structure={"gp_coinvest_pct": cfg.GP_COINVEST_PCT})
    exit_cap = resolve_exit_cap(resolve_market_cap()["market_cap"],
                                ScenarioType.BASE, None)["exit_cap"]

    def lp_net_irr_at(price):
        projection = project_cash_flows(
            ttm_noi=ttm, price=price, capex=0, params=params, costs=costs,
            coerce_exit_cap=True, exit_cap=exit_cap)
        debt = build_debt_schedule(price=price, y1_noi=projection["noi"][0],
                                   terms=terms,
                                   hold_years=projection["hold_years"])
        stack = build_sources_uses(
            price=price, capex=0,
            acquisition_cost=projection["acquisition_cost"], reserve=0.0,
            financing_costs=debt["financing_costs"],
            senior_debt=debt["loan"], gp_coinvest_pct=cfg.GP_COINVEST_PCT)
        return build_levered_returns(projection, sources_uses=stack,
                                     debt=debt,
                                     waterfall_terms=wf)["lp_net_irr"]

    # $1.5M-$8M spans the coerced region (the floor releases near $3.8M
    # on these params) and the ordinary region beyond it.
    prices = [1_500_000 + i * 325_000 for i in range(21)]
    irrs = [lp_net_irr_at(p) for p in prices]
    assert all(v is not None for v in irrs)

    coerced = project_cash_flows(
        ttm_noi=ttm, price=prices[0], capex=0, params=params, costs=costs,
        coerce_exit_cap=True, exit_cap=exit_cap)["exit_cap_coerced"]
    assert coerced, ("the sweep no longer covers the coerced region, so it "
                     "is not testing the case it exists for")

    for (low_p, low_v), (high_p, high_v) in zip(zip(prices, irrs),
                                                zip(prices[1:], irrs[1:])):
        assert high_v < low_v, (
            f"LP net IRR rose from {low_v:.4%} at ${low_p:,.0f} to "
            f"{high_v:.4%} at ${high_p:,.0f} — the bisection in "
            f"solve_max_price_levered is no longer valid")


def test_levered_solver_reports_the_coerced_region_without_warning_on_it():
    """`coerced_region` is DATA and fires on ordinary deals;
    `monotonicity_warning` is the caveat and must stay quiet unless an
    inversion was actually observed. Conflating them trains the reader to
    ignore the badge."""
    solved = solve_max_price_levered(adjusted_ttm_noi=300_000)
    assert solved["coerced_region"] is True
    assert solved["monotonicity_warning"] is None
