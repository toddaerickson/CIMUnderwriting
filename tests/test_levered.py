"""Item E3a — the levered seam (`model/levered.py`).

Every oracle here was derived from scratch in `Decimal`, importing
nothing from the repo, BEFORE `model/levered.py` existed — so these
assert an independently computed answer rather than a snapshot of the
implementation. The derivation cross-validates against E1: oracle A's
annual debt service (455,088.98) and payoff (5,616,658.65) reproduce
design-doc oracle 5 to the cent from an independent monthly roll-forward.

Three oracles, each exercising a path the others do not:

* **A** — debt yield binds, the deal never clears the pref, promote is
  zero, and leverage is DILUTIVE (7.1479% LP net against 7.3031%
  unlevered). A levered lens that always prints a bigger number than the
  unlevered screen has a bug; this is the regression that says so.
* **B** — LTV binds on a 24-month-IO loan, so the IO→amort transition is
  visible in the debt service, tier 1 clears and a promote is paid.
* **C** — levered cash flow is negative for four years: the funded
  reserve is drawn to zero, then capital is CALLED, and no period ever
  carries a negative distribution.

THE EXIT-NOI CONVENTION. `analysis.valuation.project_cash_flows`
capitalizes the terminal hold year's OWN NOI (trailing). The design doc
— and therefore `tests/test_debt.py`'s oracle 5 — capitalizes year 6
(forward), about 3% higher. Both are deliberate; see CLAUDE.md's
design-decisions block. These oracles are computed on the TRAILING
convention, because that is the projection the wiring actually consumes.
"""

import pytest

import config as cfg
from analysis.valuation import project_cash_flows
from model.debt import DebtTerms, build_debt_schedule
from model.levered import build_levered_returns
from model.returns_model import build_sources_uses
from model.waterfall import resolve_waterfall_terms

CENT = 0.005          # "to the cent" — half a cent of slack for float noise
BP = 1e-6             # IRRs to four decimal places, as a rate

COSTS = {"acquisition_closing_pct": 0.01, "disposition_cost_pct": 0.01}


def _params(growth):
    """A scenario parameter dict whose NOI grows at exactly `growth`.

    Revenue and expenses grow at the same rate, so the NOI margin holds
    and the series is `y1 * (1 + growth) ** k` — which is what the
    oracles were derived on. Both growth bands carry the same rate so a
    hold longer than three years does not change convention mid-stream.

    The exit cap is NOT in here. PR #31 made it an argument derived from
    a market anchor rather than a scenario parameter; these oracles pin
    it explicitly so the levered arithmetic is tested against a fixed
    exit, not against whatever the market table says this week.
    """
    return {"yr1_noi_bump": 0.0, "stabilized_occ": 0.88,
            "rev_cagr_yr1_3": growth, "rev_cagr_yr4_5": growth,
            "exp_growth": growth}


def _build(*, price, y1_noi, growth, exit_cap, terms, hold_years=5,
           reserve=0.0, gp_coinvest_pct=0.10, am_fee_pct=0.01):
    """Assemble one levered deal end to end, the way the engine does."""
    projection = project_cash_flows(
        y1_noi, price, 0.0, _params(growth),
        hold_years=hold_years, expense_ratio=0.40, costs=COSTS,
        reserve=reserve, exit_cap=exit_cap)
    debt = build_debt_schedule(price, projection["noi"][0], terms,
                               hold_years=hold_years)
    sources_uses = build_sources_uses(
        price=price, capex=0.0,
        acquisition_cost=projection["acquisition_cost"],
        reserve=reserve, financing_costs=debt["financing_costs"],
        senior_debt=debt["loan"], gp_coinvest_pct=gp_coinvest_pct)
    terms_w = resolve_waterfall_terms(
        capital_structure={"gp_coinvest_pct": gp_coinvest_pct})
    levered = build_levered_returns(
        projection, sources_uses=sources_uses, debt=debt,
        waterfall_terms=terms_w, am_fee_pct=am_fee_pct)
    return projection, debt, sources_uses, levered


# ── Oracle A: debt yield binds, the pref is never cleared ────────────

ORACLE_A = DebtTerms(rate=0.065, amort_years=30, io_months=0, max_ltv=0.65,
                     min_dscr=1.25, min_debt_yield=0.10, orig_fee_pct=0.01,
                     exit_fee_pct=0.0)


@pytest.fixture(scope="module")
def oracle_a():
    return _build(price=10_000_000, y1_noi=600_000, growth=0.03,
                  exit_cap=0.0625, terms=ORACLE_A)


def test_oracle_a_sizes_on_the_debt_yield(oracle_a):
    _, debt, _, _ = oracle_a
    assert debt["binding_constraint"] == "debt_yield"
    assert debt["loan"] == pytest.approx(6_000_000.00, abs=CENT)
    assert debt["financing_costs"] == pytest.approx(60_000.00, abs=CENT)
    assert debt["payoff_balance"] == pytest.approx(5_616_658.65, abs=CENT)
    assert debt["annual_debt_service"][0] == pytest.approx(455_088.98,
                                                           abs=CENT)


def test_oracle_a_stack_ties_to_the_basis_plus_financing(oracle_a):
    projection, debt, su, _ = oracle_a
    assert projection["total_basis"] == pytest.approx(10_100_000.00, abs=CENT)
    assert su["total_uses"] == pytest.approx(10_160_000.00, abs=CENT)
    assert su["total_uses"] == pytest.approx(
        projection["total_basis"] + debt["financing_costs"], abs=CENT)
    assert su["total_equity"] == pytest.approx(4_160_000.00, abs=CENT)


def test_oracle_a_levered_cash_flow(oracle_a):
    _, _, _, lev = oracle_a
    assert [r["am_fee"] for r in lev["years"]] == pytest.approx(
        [41_600.00] * 5, abs=CENT)
    assert [r["levered_cf"] for r in lev["years"]] == pytest.approx(
        [103_311.02, 121_311.02, 139_851.02, 158_947.22, 5_258_793.39],
        abs=CENT)
    assert lev["am_fee_total"] == pytest.approx(208_000.00, abs=CENT)


def test_oracle_a_never_clears_the_pref_so_no_promote(oracle_a):
    _, _, _, lev = oracle_a
    wf = lev["waterfall"]
    assert wf["tier1_current"] is False
    assert wf["unreturned_capital"] + wf["unpaid_pref"] == pytest.approx(
        225_455.71, abs=CENT)
    assert wf["gp"]["promote"] == pytest.approx(0.0, abs=CENT)
    assert lev["lp_net_irr"] == pytest.approx(0.071479, abs=BP)
    assert lev["lp_moic"] == pytest.approx(1.3900, abs=1e-4)


def test_oracle_a_leverage_is_dilutive_and_the_model_can_say_so(oracle_a):
    """The regression that catches a levered lens which only ever flatters.

    Oracle A borrows at a 7.585% constant against a deal yielding less
    than that on cost, so the LP's net return is BELOW the unlevered IRR.
    A model that cannot represent negative leverage is a model that will
    recommend debt on every deal.
    """
    projection, _, _, lev = oracle_a
    assert projection["irr"] == pytest.approx(0.073031, abs=BP)
    assert lev["lp_net_irr"] < projection["irr"]


# ── Oracle B: LTV binds, IO rolls to amortizing, a promote is paid ───

ORACLE_B = DebtTerms(rate=0.0625, amort_years=30, io_months=24, max_ltv=0.65,
                     min_dscr=1.25, min_debt_yield=0.10, orig_fee_pct=0.01,
                     exit_fee_pct=0.0)


@pytest.fixture(scope="module")
def oracle_b():
    return _build(price=10_000_000, y1_noi=750_000, growth=0.04,
                  exit_cap=0.0775, terms=ORACLE_B)


def test_oracle_b_io_rolls_to_an_amortizing_payment(oracle_b):
    """The design doc's named error, caught: "IO→amort transition payment
    not recomputed". Years 1-2 pay interest only on 6,500,000 at 6.25%;
    year 3 is the first full amortizing year."""
    _, debt, _, _ = oracle_b
    assert debt["binding_constraint"] == "ltv"
    assert debt["loan"] == pytest.approx(6_500_000.00, abs=CENT)
    assert debt["annual_debt_service"][:2] == pytest.approx(
        [406_250.00, 406_250.00], abs=CENT)
    assert debt["annual_debt_service"][2:] == pytest.approx(
        [480_259.42, 480_259.42, 480_259.42], abs=CENT)
    assert debt["payoff_balance"] == pytest.approx(6_256_487.17, abs=CENT)


def test_oracle_b_pays_a_promote_on_the_lp_attributable_residual(oracle_b):
    projection, _, su, lev = oracle_b
    assert su["total_equity"] == pytest.approx(3_665_000.00, abs=CENT)
    assert [r["levered_cf"] for r in lev["years"]] == pytest.approx(
        [307_100.00, 337_100.00, 294_290.58, 326_738.58, 5_311_997.08],
        abs=CENT)
    wf = lev["waterfall"]
    assert wf["tier1_current"] is True
    assert wf["gp"]["promote"] == pytest.approx(263_790.53, abs=CENT)
    assert lev["lp_net_irr"] == pytest.approx(0.129941, abs=BP)
    assert lev["lp_moic"] == pytest.approx(1.7146, abs=1e-4)
    assert wf["gp"]["irr"] == pytest.approx(0.224806, abs=BP)
    # Positive leverage here, unlike oracle A.
    assert projection["irr"] == pytest.approx(0.097899, abs=BP)
    assert lev["lp_net_irr"] > projection["irr"]


# ── Oracle C: the shortfall path — draw the reserve, then call ───────

ORACLE_C = DebtTerms(rate=0.075, amort_years=25, io_months=0, max_ltv=0.75,
                     min_dscr=0.0, min_debt_yield=0.0, orig_fee_pct=0.01,
                     exit_fee_pct=0.005)


@pytest.fixture(scope="module")
def oracle_c():
    return _build(price=10_000_000, y1_noi=400_000, growth=0.12,
                  exit_cap=0.0600, terms=ORACLE_C, reserve=150_000.0)


def test_oracle_c_draws_the_reserve_before_calling_capital(oracle_c):
    """Item D funds the reserve at close and puts it in `total_basis`, so
    a shortfall it covers is not a waterfall event at all — the period
    distributes 0 and nothing new is called. Only the remainder is a
    capital call."""
    _, _, _, lev = oracle_c
    year_1 = lev["years"][0]
    assert year_1["levered_cf"] == pytest.approx(-293_342.06, abs=CENT)
    assert year_1["reserve_drawn"] == pytest.approx(150_000.00, abs=CENT)
    assert year_1["capital_call"] == pytest.approx(143_342.06, abs=CENT)
    assert lev["reserve_funded"] == pytest.approx(150_000.00, abs=CENT)
    assert lev["reserve_remaining"] == pytest.approx(0.0, abs=CENT)
    # The reserve is exhausted in year 1, so years 2-4 call the full gap.
    assert [r["reserve_drawn"] for r in lev["years"][1:]] == pytest.approx(
        [0.0] * 4, abs=CENT)
    assert [r["capital_call"] for r in lev["years"][1:4]] == pytest.approx(
        [246_775.48, 195_483.24, 137_226.87], abs=CENT)
    # The exact total, not the sum of the four ROUNDED calls above — those
    # differ in the third decimal and summing them is how a "to the cent"
    # assertion quietly acquires a cent of error.
    assert lev["capital_calls_total"] == pytest.approx(722_827.6438, abs=CENT)


def test_oracle_c_never_distributes_a_negative_number(oracle_c):
    """E2's `_align_series` REJECTS a negative distribution, deliberately:
    netting one against the pref accrual pays the LP a reduced preferred
    return for the privilege of the deal losing money."""
    _, _, _, lev = oracle_c
    assert [r["distribution"] for r in lev["years"][:4]] == pytest.approx(
        [0.0] * 4, abs=CENT)
    assert all(d >= 0 for d in lev["distributions"])


def test_oracle_c_am_fee_follows_the_calls_one_period_behind(oracle_c):
    """The fee is 1% of equity outstanding at the START of the period, so
    year 2's base includes year 1's call and excludes year 2's. An
    end-of-period base would be a loop with no fixed point: a call raises
    the fee, which deepens the shortfall, which raises the call."""
    _, _, su, lev = oracle_c
    assert su["total_equity"] == pytest.approx(2_825_000.00, abs=CENT)
    assert [r["am_fee"] for r in lev["years"]] == pytest.approx(
        [28_250.00, 29_683.42, 32_151.18, 34_106.01, 35_478.28], abs=CENT)
    # Year 2's fee is exactly 1% of equity plus year 1's call.
    assert lev["years"][1]["am_fee"] == pytest.approx(
        0.01 * (2_825_000.00 + 143_342.06), abs=CENT)


def test_oracle_c_contributions_carry_the_calls_at_their_own_periods(oracle_c):
    _, _, _, lev = oracle_c
    assert lev["contributions"] == pytest.approx(
        [2_825_000.00, 143_342.06, 246_775.48, 195_483.24, 137_226.87, 0.0],
        abs=CENT)
    assert lev["lp_net_irr"] == pytest.approx(-0.009442, abs=BP)
    wf = lev["waterfall"]
    assert wf["gp"]["promote"] == pytest.approx(0.0, abs=CENT)
    # No promote was paid before the last call, so nothing is unrecovered.
    assert wf["unrecovered_promote"] == pytest.approx(0.0, abs=CENT)


def test_oracle_c_charges_the_exit_fee_at_sale_not_as_a_use_of_funds(oracle_c):
    """`build_debt_schedule` puts only origination in `financing_costs`.
    The exit fee is paid out of proceeds; carrying it as a use would
    inflate the basis and understate the return at both ends."""
    projection, debt, su, lev = oracle_c
    assert debt["exit_fee"] == pytest.approx(34_399.71, abs=CENT)
    assert su["total_uses"] == pytest.approx(
        projection["total_basis"] + debt["financing_costs"], abs=CENT)
    assert debt["exit_fee"] not in [u["amount"] for u in su["uses"]]


# ── The four traps E1 and E2 flagged for this item ───────────────────

def test_the_distribution_series_starts_at_period_zero(oracle_a):
    """Trap 3. E2's scalar-contribution shorthand cannot tell a period-0
    distribution from a series starting at year 1, and `cash_flows[1:]`
    is exactly `hold_years` long — the shape that measured LP IRR
    14.1563% against a correct 11.2437%. Both series are spelled out."""
    _, _, _, lev = oracle_a
    assert len(lev["distributions"]) == 6          # hold_years + 1
    assert len(lev["contributions"]) == 6
    assert lev["distributions"][0] == 0.0
    assert lev["contributions"][0] == pytest.approx(4_160_000.00, abs=CENT)


def test_the_waterfall_reads_the_deals_coinvest_not_the_config_default():
    """Trap 2. GP co-invest is per-deal. Called without
    `capital_structure`, `resolve_waterfall_terms` falls back to
    `config.GP_COINVEST_PCT`, and a deal edited to 25% would print a
    stack split 25/75 beside an LP net IRR computed on 10/90."""
    assert cfg.GP_COINVEST_PCT == 0.10, "fixture assumes the shipped default"
    _, _, su, lev = _build(price=10_000_000, y1_noi=750_000, growth=0.04,
                           exit_cap=0.0775, terms=ORACLE_B,
                           gp_coinvest_pct=0.25)
    assert su["gp_coinvest_pct"] == 0.25
    assert lev["waterfall"]["terms"]["gp_coinvest_pct"] == 0.25
    # The stack and the waterfall agree about whose equity it is.
    assert lev["waterfall"]["gp"]["contributions"] == pytest.approx(
        su["gp_equity"], abs=CENT)
    assert lev["waterfall"]["lp"]["contributions"] == pytest.approx(
        su["lp_equity"], abs=CENT)


def test_the_am_fee_stamp_row_carries_the_rate_and_the_base(oracle_a):
    """Trap 4. E2's stamp row says "rate and base set by the caller, not
    this module" because E2 does not charge the fee. E3a does, so the row
    must name what it charged — otherwise the stamp reads complete beside
    an LP *net* IRR while omitting the input that makes it net."""
    _, _, _, lev = oracle_a
    row = next(r for r in lev["assumption_stamp"]
               if r["key"] == "am_fee_treatment")
    assert "1.00%" in row["label"]
    assert "invested equity" in row["label"].lower()
    assert "set by the caller" not in row["label"]
    assert row["rate"] == pytest.approx(0.01)
    assert row["base"] == "invested_equity"
    # The other four questions still get answered.
    assert {r["key"] for r in lev["assumption_stamp"]} == {
        "pref_compounding", "accrual_base", "ordering", "am_fee_treatment",
        "promote_basis"}


def test_a_shortfall_is_reported_not_swallowed(oracle_c):
    """Trap 1's reporting half. A deal that called capital four times has
    to say so where a consumer cannot miss it."""
    _, _, _, lev = oracle_c
    assert lev["capital_calls_total"] > 0
    assert lev["called_capital_after_close"] is True


# ── The unlevered screen must not have moved ────────────────────────

def test_debt_does_not_touch_the_unlevered_projection():
    """The operator's decision on 2026-08-01: financing costs stay OUT of
    `total_basis`, so the primary 10% IRR screen is identical whether or
    not the deal carries a loan. E1's handoff prescribed the opposite;
    the tie moved instead of the projection."""
    unlevered = project_cash_flows(
        600_000, 10_000_000, 0.0, _params(0.03),
        hold_years=5, expense_ratio=0.40, costs=COSTS, exit_cap=0.0625)
    projection, debt, _, _ = _build(price=10_000_000, y1_noi=600_000,
                                    growth=0.03, exit_cap=0.0625,
                                    terms=ORACLE_A)
    assert debt["loan"] > 0, "fixture must actually carry debt"
    assert projection["total_basis"] == pytest.approx(
        unlevered["total_basis"], abs=CENT)
    assert projection["irr"] == pytest.approx(unlevered["irr"], abs=1e-12)
    assert projection["moic"] == pytest.approx(unlevered["moic"], abs=1e-12)
    assert projection["yield_on_cost"] == pytest.approx(
        unlevered["yield_on_cost"], abs=1e-12)


def test_the_exit_capitalizes_the_trailing_year_not_the_forward_year(oracle_a):
    """The convention CLAUDE.md now records. Year 5's own NOI is
    capitalized, NOT year 6 — the design doc and `tests/test_debt.py`'s
    oracle 5 use the forward convention and are about 3% higher. Neither
    is a bug; they are different conventions and the repo states both."""
    projection, _, _, _ = oracle_a
    trailing = 600_000 * 1.03 ** 4
    assert projection["noi"][-1] == pytest.approx(trailing, abs=CENT)
    assert projection["exit_value"] == pytest.approx(trailing / 0.0625,
                                                     abs=CENT)
    forward = 600_000 * 1.03 ** 5
    assert projection["exit_value"] < forward / 0.0625


# ── Edges ───────────────────────────────────────────────────────────

def test_a_deal_that_supports_no_debt_is_the_unlevered_deal():
    """Covenants can bind to nothing. A zero loan must not produce a
    divide-by-zero, a NaN or a promote — it produces the unlevered deal
    with an AM fee."""
    no_debt = DebtTerms(rate=0.065, amort_years=30, max_ltv=0.0,
                        min_dscr=1.25, min_debt_yield=0.10)
    projection, debt, su, lev = _build(price=10_000_000, y1_noi=600_000,
                                       growth=0.03, exit_cap=0.0625,
                                       terms=no_debt)
    assert debt["loan"] == 0.0
    assert debt["financing_costs"] == 0.0
    assert su["senior_debt"] == 0.0
    assert su["total_equity"] == pytest.approx(projection["total_basis"],
                                               abs=CENT)
    assert [r["debt_service"] for r in lev["years"]] == pytest.approx(
        [0.0] * 5, abs=CENT)
    assert lev["lp_net_irr"] is not None
    assert lev["capital_calls_total"] == pytest.approx(0.0, abs=CENT)


@pytest.mark.parametrize("hold_years", [1, 10])
def test_hold_period_extremes_stay_aligned(hold_years):
    """`hold_years` is editable 1-10. The two series must stay
    `hold_years + 1` long at both ends, with the exit in the last year."""
    _, debt, _, lev = _build(price=10_000_000, y1_noi=600_000, growth=0.03,
                             exit_cap=0.0625, terms=ORACLE_A,
                             hold_years=hold_years)
    assert len(lev["years"]) == hold_years
    assert len(lev["contributions"]) == hold_years + 1
    assert len(lev["distributions"]) == hold_years + 1
    assert len(debt["annual_debt_service"]) == hold_years
    # Only the final year carries the sale.
    assert lev["years"][-1]["exit_proceeds"] != 0
    assert all(r["exit_proceeds"] == 0 for r in lev["years"][:-1])


def test_an_unsupported_am_fee_base_raises_rather_than_defaulting():
    """The same discipline E2 applied to its four convention fields: a
    convention this module does not implement raises, because silently
    substituting one produces a confident wrong LP net IRR — which is the
    failure mode the whole item exists to remove. "1% of asset value" is
    a real convention and roughly 2.4x the equity base on this fixture,
    so defaulting it would be a quiet 2.4x error in the fee."""
    projection = project_cash_flows(
        600_000, 10_000_000, 0.0, _params(0.03),
        hold_years=5, expense_ratio=0.40, costs=COSTS, exit_cap=0.0625)
    debt = build_debt_schedule(10_000_000, 600_000, ORACLE_A, hold_years=5)
    su = build_sources_uses(price=10_000_000, capex=0.0,
                            acquisition_cost=projection["acquisition_cost"],
                            financing_costs=debt["financing_costs"],
                            senior_debt=debt["loan"], gp_coinvest_pct=0.10)
    with pytest.raises(ValueError, match="am_fee_base"):
        build_levered_returns(projection, sources_uses=su, debt=debt,
                              waterfall_terms=resolve_waterfall_terms(),
                              am_fee_base="asset_value")


def test_config_defaults_are_what_the_module_uses():
    """No second copy of the AM fee. `config.AM_FEE_PCT` is the source and
    a drifting mirror is the failure this pins."""
    assert cfg.AM_FEE_PCT == 0.01
    assert cfg.AM_FEE_BASE == "invested_equity"
    projection, _, _, lev = _build(price=10_000_000, y1_noi=600_000,
                                   growth=0.03, exit_cap=0.0625,
                                   terms=ORACLE_A, am_fee_pct=None)
    assert lev["am_fee_pct"] == cfg.AM_FEE_PCT
    assert lev["am_fee_base"] == cfg.AM_FEE_BASE
    assert [r["am_fee"] for r in lev["years"]] == pytest.approx(
        [41_600.00] * 5, abs=CENT)


# ── One loan, three scenarios ───────────────────────────────────────

def test_the_loan_is_sized_once_off_the_base_case():
    """Sizing per scenario would hand the bear case a SMALLER loan and
    flatten its own downside — the model would understate exactly the risk
    the bear case exists to show. A lender sizes on one underwriting."""
    from model.returns_model import build_returns_model
    from registry import ScenarioType

    model = build_returns_model(
        adjusted_ttm_noi=600_000, asking_price=10_000_000, nrsf=60_000,
        expense_ratio=0.40, debt_terms=ORACLE_A)

    assert set(model["levered"]) == set(model["scenarios"])
    loans = {name: lev["debt"]["loan"] for name, lev in model["levered"].items()}
    assert len(set(loans.values())) == 1, loans
    schedules = {name: tuple(lev["debt"]["annual_debt_service"])
                 for name, lev in model["levered"].items()}
    assert len(set(schedules.values())) == 1
    assert model["debt"]["binding_constraint"] == "debt_yield"

    # The bear case still produces a WORSE LP outcome than the bull — the
    # loan being constant is what lets the scenarios differ honestly.
    bear = model["levered"][ScenarioType.BEAR]["lp_net_irr"]
    bull = model["levered"][ScenarioType.BULL]["lp_net_irr"]
    assert bear is None or bull is None or bear < bull


def test_a_projection_with_no_noi_series_raises_instead_of_sizing_on_zero():
    """Found while wiring: the scenario API relabels `noi` as
    `noi_projection`, so reading only `noi` and defaulting to 0.0 sized
    the loan on a Year 1 NOI of zero — producing a $0 debt-yield-bound
    loan and a levered lens identical to the unlevered one, with nothing
    saying the input was missing. Both names are accepted; NEITHER
    raises."""
    from model.levered import noi_series

    assert noi_series({"noi": [1.0, 2.0]}) == [1.0, 2.0]
    assert noi_series({"noi_projection": [3.0]}) == [3.0]
    with pytest.raises(ValueError, match="no NOI series"):
        noi_series({"total_basis": 10_000_000})


def test_the_capital_stack_carries_the_sized_loan():
    """`build_sources_uses` gets the real `senior_debt` and
    `financing_costs`, so debt DISPLACES equity rather than inflating
    uses — item D's promise, now that a loan actually exists."""
    from model.returns_model import build_returns_model

    model = build_returns_model(
        adjusted_ttm_noi=600_000, asking_price=10_000_000, nrsf=60_000,
        expense_ratio=0.40, debt_terms=ORACLE_A)
    su, debt = model["sources_uses"], model["debt"]
    assert su["senior_debt"] == pytest.approx(debt["loan"], abs=CENT)
    assert su["financing_costs"] == pytest.approx(debt["financing_costs"],
                                                  abs=CENT)
    base = next(s for s in model["scenarios"].values() if isinstance(s, dict))
    assert su["total_uses"] == pytest.approx(
        base["total_basis"] + debt["financing_costs"], abs=CENT)
    assert su["total_uses"] == pytest.approx(su["total_sources"], abs=CENT)


def test_the_blocking_tie_check_passes_on_a_levered_run():
    """The check item A made blocking, on a deal that now carries debt."""
    from analysis import checks
    from model.returns_model import build_returns_model

    model = build_returns_model(
        adjusted_ttm_noi=600_000, asking_price=10_000_000, nrsf=60_000,
        expense_ratio=0.40, debt_terms=ORACLE_A)
    result = next(r for r in checks.run_checks(
        checks.CheckInput(scenarios=model["scenarios"],
                          sources_uses=model["sources_uses"]),
        only={"sources_uses_ties"}))
    assert result.status == checks.PASS, result.message


# ── Repairs from the E3a audit ──────────────────────────────────────

def test_each_scenario_gets_its_own_debt_dict_not_a_shared_alias():
    """One loan is sized for the whole deal and every scenario embeds it.
    Returning the object itself made all three the same dict, so the first
    consumer to annotate per-scenario debt data in place would corrupt the
    other two."""
    from model.returns_model import build_returns_model
    from registry import ScenarioType

    model = build_returns_model(
        adjusted_ttm_noi=600_000, asking_price=10_000_000, nrsf=60_000,
        expense_ratio=0.40, debt_terms=ORACLE_A)
    bear = model["levered"][ScenarioType.BEAR]["debt"]
    bull = model["levered"][ScenarioType.BULL]["debt"]
    assert bear is not bull
    assert bear["annual_debt_service"] is not bull["annual_debt_service"]
    # Same VALUES — the loan really is sized once.
    assert bear["loan"] == bull["loan"]
    # Mutating one leaves the other untouched.
    bear["loan"] = -1
    assert bull["loan"] != -1


def test_a_debt_payload_missing_its_payoff_raises():
    """Defaulting a missing payoff to 0.0 computes the exit as though the
    loan were forgiven at sale, and reports an LP net IRR that is too
    HIGH with no error anywhere. A zero VALUE is fine; a missing KEY is a
    broken payload."""
    projection = project_cash_flows(
        600_000, 10_000_000, 0.0, _params(0.03),
        hold_years=5, expense_ratio=0.40, costs=COSTS, exit_cap=0.0625)
    debt = build_debt_schedule(10_000_000, 600_000, ORACLE_A, hold_years=5)
    su = build_sources_uses(price=10_000_000, capex=0.0,
                            acquisition_cost=projection["acquisition_cost"],
                            financing_costs=debt["financing_costs"],
                            senior_debt=debt["loan"], gp_coinvest_pct=0.10)
    broken = {k: v for k, v in debt.items() if k != "payoff_balance"}
    with pytest.raises(ValueError, match="payoff_balance"):
        build_levered_returns(projection, sources_uses=su, debt=broken,
                              waterfall_terms=resolve_waterfall_terms())
    # A zero exit fee is legitimate and must still work.
    zeroed = {**debt, "exit_fee": 0.0}
    assert build_levered_returns(
        projection, sources_uses=su, debt=zeroed,
        waterfall_terms=resolve_waterfall_terms())["lp_net_irr"] is not None


def test_a_blank_am_fee_falls_back_to_config_instead_of_crashing():
    """An HTML form posts "" for a cleared numeric field. Every sibling
    resolver in this repo tests `not in (None, "")`; `is None` would make
    E3b's form field raise on `float("")` the day it lands."""
    projection = project_cash_flows(
        600_000, 10_000_000, 0.0, _params(0.03),
        hold_years=5, expense_ratio=0.40, costs=COSTS, exit_cap=0.0625)
    debt = build_debt_schedule(10_000_000, 600_000, ORACLE_A, hold_years=5)
    su = build_sources_uses(price=10_000_000, capex=0.0,
                            acquisition_cost=projection["acquisition_cost"],
                            financing_costs=debt["financing_costs"],
                            senior_debt=debt["loan"], gp_coinvest_pct=0.10)
    lev = build_levered_returns(projection, sources_uses=su, debt=debt,
                                waterfall_terms=resolve_waterfall_terms(),
                                am_fee_pct="", am_fee_base="")
    assert lev["am_fee_pct"] == cfg.AM_FEE_PCT
    assert lev["am_fee_base"] == cfg.AM_FEE_BASE


def test_sizing_raises_when_the_base_scenario_is_missing():
    """The loan is sized off the BASE case. Falling back to whichever
    scenario computed first could price the debt on the bull case's
    richer NOI, and `sources_uses_ties` cannot catch that — it checks the
    stack's internal arithmetic, not which NOI justified the loan."""
    from model.returns_model import build_returns_model
    from registry import ScenarioType

    real = build_returns_model(
        adjusted_ttm_noi=600_000, asking_price=10_000_000, nrsf=60_000,
        expense_ratio=0.40, debt_terms=ORACLE_A)
    assert isinstance(real["scenarios"][ScenarioType.BASE], dict)

    import model.returns_model as rm

    def _no_base(*args, **kwargs):
        # The key REMOVED, not set to None: `_build_summary_table` does
        # `scenarios.get(name, {})` and so tolerates an absent key while
        # crashing on an explicit None. Absent is therefore the shape
        # that actually reaches the debt-sizing fallback.
        scen = dict(real["scenarios"])
        scen.pop(ScenarioType.BASE)
        return scen

    original = rm.run_scenarios
    rm.run_scenarios = _no_base
    try:
        with pytest.raises(ValueError, match="base scenario is missing"):
            build_returns_model(
                adjusted_ttm_noi=600_000, asking_price=10_000_000,
                nrsf=60_000, expense_ratio=0.40, debt_terms=ORACLE_A)
    finally:
        rm.run_scenarios = original


def test_the_tie_check_catches_a_stack_built_without_the_debt_modules_fee():
    """The failure the self-referential version could not see. A caller
    that forgets `financing_costs=debt["financing_costs"]` produces a
    `total_uses` missing the fee AND a `financing_costs` of 0 — both wrong
    the same way, so `uses == basis + 0` reconciled and the check PASSED
    on a deal underfunded by the entire origination fee."""
    from analysis import checks
    from model.returns_model import build_returns_model

    model = build_returns_model(
        adjusted_ttm_noi=600_000, asking_price=10_000_000, nrsf=60_000,
        expense_ratio=0.40, debt_terms=ORACLE_A)
    debt = model["debt"]
    assert debt["financing_costs"] > 0

    base = next(s for s in model["scenarios"].values()
                if isinstance(s, dict))
    forgot_the_fee = build_sources_uses(
        price=10_000_000, capex=0.0,
        acquisition_cost=base["acquisition_cost"],
        senior_debt=debt["loan"], gp_coinvest_pct=0.10)   # no financing_costs

    # Self-consistent, and wrong: it ties to the DCF basis exactly.
    assert forgot_the_fee["total_uses"] == pytest.approx(
        base["total_basis"], abs=CENT)

    without_debt = next(r for r in checks.run_checks(
        checks.CheckInput(scenarios=model["scenarios"],
                          sources_uses=forgot_the_fee),
        only={"sources_uses_ties"}))
    assert without_debt.status == checks.PASS      # the old blind spot

    with_debt = next(r for r in checks.run_checks(
        checks.CheckInput(scenarios=model["scenarios"],
                          sources_uses=forgot_the_fee, debt=debt),
        only={"sources_uses_ties"}))
    assert with_debt.status == checks.FAIL
    assert with_debt.severity == checks.BLOCKING
    assert "financing costs" in with_debt.message


def test_a_loan_maturing_inside_the_hold_reaches_the_check_register():
    """Previously a `logger.warning` nobody reads, while the results page
    showed a levered IRR computed as though the loan amortized past its
    own maturity. Item E3a put a sized loan on every deal, so the
    condition went live."""
    from analysis import checks

    short_term = DebtTerms(rate=0.065, amort_years=30, term_years=3,
                           max_ltv=0.65, min_dscr=1.25, min_debt_yield=0.10)
    debt = build_debt_schedule(10_000_000, 600_000, short_term, hold_years=5)
    assert debt["matures_before_exit"] is True

    result = next(r for r in checks.run_checks(
        checks.CheckInput(debt=debt), only={"loan_matures_before_exit"}))
    assert result.status == checks.FAIL
    assert result.severity == checks.ADVISORY
    assert "matures in year 3" in result.message
    assert "5 years" in result.message

    ok = build_debt_schedule(10_000_000, 600_000, ORACLE_A, hold_years=5)
    passing = next(r for r in checks.run_checks(
        checks.CheckInput(debt=ok), only={"loan_matures_before_exit"}))
    assert passing.status == checks.PASS

    # A deal with no debt is skipped, not passed — we did not look.
    no_debt = build_debt_schedule(
        10_000_000, 600_000,
        DebtTerms(rate=0.065, amort_years=30, max_ltv=0.0, min_dscr=0.0,
                  min_debt_yield=0.0), hold_years=5)
    skipped = next(r for r in checks.run_checks(
        checks.CheckInput(debt=no_debt), only={"loan_matures_before_exit"}))
    assert skipped.status == checks.SKIPPED


def test_the_result_is_json_safe_all_the_way_down(oracle_a):
    """These results are persisted to Postgres JSONB.
    `webapp.services.json_safe` falls back to `str(obj)`, so a frozen
    dataclass anywhere in the payload persists as an unqueryable string —
    the bug E2 found in its own `terms` key. Debt terms are the same
    shape and would have repeated it."""
    import json

    from webapp.services import json_safe

    _, _, _, lev = oracle_a
    assert isinstance(lev["debt"]["terms"], dict)
    assert lev["debt"]["terms"]["rate"] == pytest.approx(0.065)
    assert isinstance(lev["waterfall"]["terms"], dict)
    encoded = json.dumps(json_safe(lev))
    assert "DebtTerms(" not in encoded
    assert "WaterfallTerms(" not in encoded
