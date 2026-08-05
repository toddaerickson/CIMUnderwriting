"""Item B — transaction costs + variable hold period.

Three kinds of test live here, and the distinction matters:

1. **Zero-cost regression pins.** Values captured from the pre-refactor code
   and asserted to 1e-9 with both cost percentages at 0. These are the proof
   that collapsing three duplicated projection loops into one moved no number
   on its own. If one of these fails, the refactor changed behaviour it was
   not supposed to change — do not "re-pin" it, find out why.
2. **Hand-computed oracles.** Built from a degenerate flat case where the
   right answer is derivable on paper (below), never copied from output.
3. **Identity checks.** IRR is not closed-form, so instead of pinning a
   number we assert the thing that defines it: NPV at the returned rate is 0.

The flat oracle case, used throughout:

    ttm_noi 1,000,000 · price 10,000,000 · capex 0 · expense_ratio 0.50
    yr1_noi_bump 0 · both rev CAGRs 0 · exp_growth 0 · exit_cap 0.10

    → NOI is 1,000,000 flat in every year, entry cap == exit cap == 10%,
      exit value == 10,000,000 == price.

    With zero costs you buy at 10M, collect 1M a year and sell for 10M, so the
    unlevered IRR is EXACTLY 10% for any hold length, and MOIC is 1 + N/10.
    That is a hand computation, not an observation.
"""

import numpy_financial as npf
import pytest

from config import SCENARIO_DEFAULTS, SOLVER_TOLERANCE

from analysis.valuation import (COERCED_SCENARIOS, project_cash_flows,
                                resolve_market_cap, run_scenarios)
from model.returns_model import build_returns_model
from model.solver import solve_max_price
from registry import ScenarioType

NO_COSTS = {"acquisition_closing_pct": 0.0, "disposition_cost_pct": 0.0}

#: The exit caps that used to live in `config.SCENARIO_DEFAULTS`, retired
#: when the cap became derived from a market anchor. They stay here to hold
#: the pins below at their pre-refactor values: the pins prove the ONE
#: projection loop still computes what three loops used to, and that loop
#: did not change — only where its exit cap comes from did. Feeding it the
#: cap the retired constant supplied is what keeps the proof exact.
RETIRED_EXIT_CAPS = {ScenarioType.BEAR: 0.085,
                     ScenarioType.BASE: 0.075,
                     ScenarioType.BULL: 0.065}

#: A market anchor chosen so the BASE scenario's derived cap lands exactly
#: on the retired 0.075 at the default five-year hold:
#:     0.07125 + 0 bp spread + 7.5 bp/yr × 5 yrs = 0.0750
#: The sensitivity grid and the solver are both base-centred, so this keeps
#: their pins pre-refactor too. Bear and bull deliberately do NOT reconcile
#: to the same anchor — their retired caps were ±100 bp flat, with no drift
#: term at all — which is why the nine scenario pins below go through
#: `project_cash_flows` directly rather than through `run_scenarios`.
PIN_MARKET_CAP = resolve_market_cap(market_cap=0.075 - 5 * 7.5 / 10_000)

FLAT_PARAMS = {
    "yr1_noi_bump": 0.0,
    "stabilized_occ": 0.88,
    "rev_cagr_yr1_3": 0.0,
    "rev_cagr_yr4_5": 0.0,
    "exp_growth": 0.0,
}
FLAT_EXIT_CAP = 0.10
FLAT_NOI = 1_000_000
FLAT_PRICE = 10_000_000


def flat(**kw):
    """The hand-computable case; kw overrides any argument."""
    args = dict(ttm_noi=FLAT_NOI, price=FLAT_PRICE, capex=0,
                params=FLAT_PARAMS, hold_years=5, expense_ratio=0.50,
                costs=NO_COSTS, exit_cap=FLAT_EXIT_CAP)
    args.update(kw)
    return project_cash_flows(**args)


# ── 1. Zero-cost regression pins ─────────────────────────────────────

# (adjusted_ttm_noi, asking_price, nrsf, capex, expense_ratio)
PIN_CASES = {
    "a": (300_000, 4_000_000, 50_000, 0, None),
    "b": (400_000, 4_000_000, 50_000, 250_000, 0.42),
    "c": (200_000, 4_000_000, 50_000, 0, 0.55),
}

# case.scenario → (irr, moic, yield_on_cost) as computed by the three
# pre-refactor loops. Captured 2026-07-31 at commit bb15311.
PINNED = {
    "a.bear": (0.057632365279054465, 1.2773413202398887, 0.075),
    "a.base": (0.10519693532523267, 1.554979879244531, 0.07875),
    "a.bull": (0.16130019821730457, 1.949064503988461, 0.0825),
    "b.bear": (0.08726235649532366, 1.4294407123204855, 0.09411764705882353),
    "b.base": (0.11464420030490241, 1.5909707989709934, 0.0988235294117647),
    "b.bull": (0.22445474183730885, 2.4494561623858946, 0.1035294117647059),
    "c.bear": (-0.04219592535418426, 0.8262409080857838, 0.05),
    "c.base": (0.005900117323867526, 1.0266757380979163, 0.0525),
    "c.bull": (0.06286347640163803, 1.3177164219858968, 0.05500000000000001),
}


@pytest.mark.parametrize("key", sorted(PINNED))
def test_zero_cost_scenarios_reproduce_pre_refactor_exactly(key):
    """The refactor is a no-op when costs are off. 1e-9, not approx.

    Fed through `project_cash_flows` with the retired exit-cap constants
    rather than through `run_scenarios`, because `run_scenarios` now
    DERIVES the cap and no single market anchor reproduces all three
    retired ones. What these pin is the projection arithmetic, which is
    unchanged — they still reproduce to 1e-12, so re-baselining them
    would have thrown away a live guard rather than recorded a real move.
    """
    case, scen = key.split(".")
    st = ScenarioType(scen)
    noi, price, nrsf, capex, er = PIN_CASES[case]
    result = project_cash_flows(
        ttm_noi=noi, price=price, capex=capex,
        params=SCENARIO_DEFAULTS[st], expense_ratio=er, costs=NO_COSTS,
        coerce_exit_cap=st in COERCED_SCENARIOS,
        exit_cap=RETIRED_EXIT_CAPS[st])
    irr, moic, yoc = PINNED[key]
    assert result["irr"] == pytest.approx(irr, abs=1e-9)
    assert result["moic"] == pytest.approx(moic, abs=1e-9)
    assert result["yield_on_cost"] == pytest.approx(yoc, abs=1e-9)


def test_zero_cost_sensitivity_grid_reproduces_pre_refactor_exactly():
    """The grid is the loop that never coerced the exit cap — it still
    must not, or every cell below entry cap collapses onto one value."""
    grid = build_returns_model(
        adjusted_ttm_noi=300_000, asking_price=4_000_000, nrsf=50_000,
        capex=0, transaction_costs=NO_COSTS, market_cap=PIN_MARKET_CAP,
    )["sensitivity"]["irr_grid"]
    assert grid[0][0] == pytest.approx(0.15878606011486252, abs=1e-9)
    assert grid[4][4] == pytest.approx(0.10519693532523267, abs=1e-9)
    assert grid[8][8] == pytest.approx(0.059934021666702364, abs=1e-9)


#: Re-baselined by item T Category 3, which gave all three solvers ONE
#: bracket (`config.SOLVER_BOUNDS`) at the wider of the two they carried
#: — the dear end moved from a 3% implied entry cap to 2%.
#:
#: Bisection stops at the first price whose IRR is within
#: `SOLVER_TOLERANCE` of the target, so the price it lands on depends on
#: where its midpoints fall, and a different opening bracket walks a
#: different sequence to a different point in the SAME 10bp-wide band.
#: The pins moved 0.15% and 0.43%; both prices then and now sit inside
#: the tolerance, which is the property actually worth pinning and is
#: asserted below alongside the number.
_SOLVER_PINS = [
    # capex,   max_price,   the pre-Category-3 value this replaces
    (0,        4_083_984.375,  4_089_843.75),
    (200_000,  3_609_375.0,    3_625_000.0),
]


@pytest.mark.parametrize("capex,max_price,superseded", _SOLVER_PINS)
def test_zero_cost_solver_reproduces_pre_refactor_exactly(capex, max_price,
                                                          superseded):
    result = solve_max_price(adjusted_ttm_noi=300_000, capex=capex,
                             transaction_costs=NO_COSTS,
                             market_cap=PIN_MARKET_CAP)
    assert result["max_price"] == pytest.approx(max_price, abs=1e-6)

    # The bracket-independent half, and the reason re-baselining this pin
    # does not throw the guard away: whatever price the search lands on,
    # it has to be a price that actually achieves the target. A solver
    # returning its own ceiling passes the number above only if someone
    # copies the ceiling into it; it can never pass this.
    assert result["converged"] is True
    assert result["achieved_irr"] == pytest.approx(result["target_irr"],
                                                   abs=SOLVER_TOLERANCE)
    assert abs(max_price - superseded) / superseded < 0.01


# ── 2. Hand-computed oracles: hold period ────────────────────────────

@pytest.mark.parametrize("hold", [1, 3, 5, 7, 10])
def test_flat_case_irr_is_exactly_ten_percent_at_any_hold(hold):
    """Buy at a 10 cap, flat NOI, sell at a 10 cap, no costs → 10% for
    every hold length. Any deviation is an arithmetic error, not rounding."""
    r = flat(hold_years=hold)
    assert r["irr"] == pytest.approx(0.10, abs=1e-9)
    assert r["moic"] == pytest.approx(1 + hold / 10, abs=1e-9)


@pytest.mark.parametrize("hold", [1, 3, 5, 7, 10])
def test_series_lengths_follow_hold_years(hold):
    r = flat(hold_years=hold)
    assert len(r["noi"]) == hold
    assert len(r["revenue"]) == hold
    assert len(r["expenses"]) == hold
    assert len(r["cash_flows"]) == hold + 1


def test_default_hold_is_five_years():
    from config import DEFAULT_HOLD_YEARS
    assert DEFAULT_HOLD_YEARS == 5
    assert len(run_scenarios(adjusted_ttm_noi=300_000,
                             asking_price=4_000_000,
                             nrsf=50_000)[ScenarioType.BASE]
               ["noi_projection"]) == 5


def test_second_growth_band_applies_year_four_onward():
    """`rev_cagr_yr4_5` is the year-4-ONWARD rate, not a year-4-and-5 rate.
    A 10-year hold must keep growing at it rather than falling off the end
    of the band. Revenue: 2,000,000 flat through yr 3, then +10%/yr."""
    params = dict(FLAT_PARAMS, rev_cagr_yr1_3=0.0, rev_cagr_yr4_5=0.10)
    rev = flat(params=params, hold_years=10)["revenue"]
    for yr in range(3):
        assert rev[yr] == pytest.approx(2_000_000, abs=1e-6)
    for yr in range(3, 10):
        assert rev[yr] == pytest.approx(
            2_000_000 * 1.10 ** (yr - 2), abs=1e-6)


def test_first_growth_band_applies_through_year_three():
    params = dict(FLAT_PARAMS, rev_cagr_yr1_3=0.05, rev_cagr_yr4_5=0.0)
    rev = flat(params=params, hold_years=5)["revenue"]
    assert rev[1] == pytest.approx(2_000_000 * 1.05, abs=1e-6)
    assert rev[2] == pytest.approx(2_000_000 * 1.05 ** 2, abs=1e-6)
    assert rev[3] == pytest.approx(2_000_000 * 1.05 ** 2, abs=1e-6)
    assert rev[4] == pytest.approx(2_000_000 * 1.05 ** 2, abs=1e-6)


# ── 2b. Hand-computed oracles: costs ─────────────────────────────────

COSTS = {"acquisition_closing_pct": 0.01, "disposition_cost_pct": 0.015}


def test_acquisition_cost_enters_the_basis():
    r = flat(costs=COSTS)
    assert r["acquisition_cost"] == pytest.approx(100_000, abs=1e-6)
    assert r["total_basis"] == pytest.approx(10_100_000, abs=1e-6)
    assert r["cash_flows"][0] == pytest.approx(-10_100_000, abs=1e-6)


def test_acquisition_cost_is_a_percent_of_price_not_of_basis():
    """CapEx is not a closing-cost base — 1% of a 10M price is 100,000 even
    when 2M of CapEx rides along."""
    r = flat(capex=2_000_000, costs=COSTS)
    assert r["acquisition_cost"] == pytest.approx(100_000, abs=1e-6)
    assert r["total_basis"] == pytest.approx(12_100_000, abs=1e-6)


def test_disposition_cost_comes_out_of_exit_proceeds():
    r = flat(costs=COSTS)
    assert r["exit_value"] == pytest.approx(10_000_000, abs=1e-6)
    assert r["disposition_cost"] == pytest.approx(150_000, abs=1e-6)
    assert r["net_exit_proceeds"] == pytest.approx(9_850_000, abs=1e-6)
    assert r["cash_flows"][-1] == pytest.approx(1_000_000 + 9_850_000, abs=1e-6)


def test_moic_denominator_is_the_cost_inclusive_basis():
    """(5 × 1,000,000 + 9,850,000) / 10,100,000."""
    r = flat(costs=COSTS)
    assert r["moic"] == pytest.approx(14_850_000 / 10_100_000, abs=1e-12)
    assert r["yield_on_cost"] == pytest.approx(
        1_000_000 / 10_100_000, abs=1e-12)


def test_irr_satisfies_its_own_definition_with_costs_on():
    """IRR has no closed form here, so assert what defines it: the cash
    flows discounted at the returned rate net to zero."""
    r = flat(costs=COSTS)
    assert npf.npv(r["irr"], r["cash_flows"]) == pytest.approx(0, abs=1e-4)


def test_costs_reduce_irr():
    assert flat(costs=COSTS)["irr"] < flat(costs=NO_COSTS)["irr"]


def test_cost_drag_is_tens_of_basis_points():
    """The scoped estimate was 30-60 bps on a typical deal. This pins the
    order of magnitude, so a unit error (1.0 vs 0.01) cannot pass."""
    drag = flat(costs=NO_COSTS)["irr"] - flat(costs=COSTS)["irr"]
    assert 0.002 < drag < 0.012


def test_config_defaults_are_one_and_one_point_five_percent():
    from config import TRANSACTION_COSTS
    assert TRANSACTION_COSTS["acquisition_closing_pct"] == 0.010
    assert TRANSACTION_COSTS["disposition_cost_pct"] == 0.015


def test_scenarios_use_config_costs_by_default():
    """Omitting transaction_costs must not silently mean zero costs."""
    from config import TRANSACTION_COSTS
    default = run_scenarios(adjusted_ttm_noi=300_000, asking_price=4_000_000,
                            nrsf=50_000)[ScenarioType.BASE]
    explicit = run_scenarios(adjusted_ttm_noi=300_000, asking_price=4_000_000,
                             nrsf=50_000,
                             transaction_costs=TRANSACTION_COSTS,
                             )[ScenarioType.BASE]
    assert default["irr"] == pytest.approx(explicit["irr"], abs=1e-12)
    assert default["irr"] < PINNED["a.base"][0]


# ── 3. Solver behaviour with costs ───────────────────────────────────

def test_solver_round_trips_through_the_dcf_with_costs_applied():
    """The solved price, re-run forward through the same engine, returns
    the target. This is the check that closing costs were computed INSIDE
    the bisection target rather than bolted on after it."""
    target = 0.10
    solved = solve_max_price(adjusted_ttm_noi=300_000, capex=100_000,
                             target_irr=target, transaction_costs=COSTS)
    assert solved["converged"] is True
    forward = run_scenarios(
        adjusted_ttm_noi=300_000, asking_price=solved["max_price"],
        nrsf=50_000, capex=100_000, transaction_costs=COSTS,
    )[ScenarioType.BASE]
    assert forward["irr"] == pytest.approx(target, abs=0.001)


def test_solver_total_basis_includes_acquisition_costs():
    solved = solve_max_price(adjusted_ttm_noi=300_000, capex=100_000,
                             transaction_costs=COSTS)
    expected = (solved["max_price"] * 1.01) + 100_000
    assert solved["total_basis"] == pytest.approx(expected, abs=1.0)


@pytest.mark.parametrize("costlier", [
    {"acquisition_closing_pct": 0.03, "disposition_cost_pct": 0.015},
    {"acquisition_closing_pct": 0.01, "disposition_cost_pct": 0.040},
])
def test_higher_costs_lower_the_max_price(costlier):
    """Monotone in both cost percentages — the property bisection needs."""
    base = solve_max_price(adjusted_ttm_noi=300_000,
                           transaction_costs=COSTS)["max_price"]
    worse = solve_max_price(adjusted_ttm_noi=300_000,
                            transaction_costs=costlier)["max_price"]
    assert worse < base


def test_solver_honours_hold_years():
    """Proof the solver is not still pinned to five years internally.

    Deliberately uses a strong-growth parameter set: at the config
    defaults a 3- vs 10-year hold moves base IRR only ~30 bps, which is
    INSIDE the solver's own 0.001 convergence tolerance, so both holds
    legitimately land on the same bisection midpoint. Compounding 8%
    revenue growth against 2% expense growth makes a longer hold worth
    materially more, which is what makes the property observable."""
    growth = dict(FLAT_PARAMS, rev_cagr_yr1_3=0.08, rev_cagr_yr4_5=0.08,
                  exp_growth=0.02)
    prices = [solve_max_price(adjusted_ttm_noi=300_000, custom_params=growth,
                              hold_years=h, transaction_costs=COSTS,
                              market_cap=PIN_MARKET_CAP)["max_price"]
              for h in (1, 3, 5, 10)]
    assert prices == sorted(prices), prices
    assert prices[-1] > prices[0] * 2


def test_projection_length_reaches_the_solver():
    """The unit-level fact behind the test above: the same price prices
    differently at different holds."""
    irrs = {h: project_cash_flows(
                ttm_noi=300_000, price=3_957_031.25, capex=0,
                params=SCENARIO_DEFAULTS[ScenarioType.BASE],
                hold_years=h, costs=COSTS,
                exit_cap=RETIRED_EXIT_CAPS[ScenarioType.BASE])["irr"]
            for h in (3, 5, 10)}
    assert len(set(irrs.values())) == 3


# ── 4. The coercion policy the three loops used to disagree about ────

def test_scenario_engine_still_coerces_base_and_bear_only():
    coerced = run_scenarios(adjusted_ttm_noi=400_000, asking_price=4_000_000,
                            nrsf=50_000, transaction_costs=NO_COSTS)
    assert coerced[ScenarioType.BASE]["exit_cap_coerced"] is True
    assert coerced[ScenarioType.BEAR]["exit_cap_coerced"] is True
    assert coerced[ScenarioType.BULL]["exit_cap_coerced"] is False


def test_projection_can_be_told_not_to_coerce():
    """The sensitivity grid depends on this: an exit-cap axis is useless if
    every cell below entry cap is silently raised to it."""
    kw = dict(ttm_noi=400_000, price=4_000_000, capex=0,
              params=FLAT_PARAMS, exit_cap=0.05, hold_years=5,
              expense_ratio=0.40, costs=NO_COSTS)
    assert project_cash_flows(**kw, coerce_exit_cap=True)["exit_cap"] == 0.10
    assert project_cash_flows(**kw, coerce_exit_cap=False)["exit_cap"] == 0.05


# ── 5. The value-add engine carries the same costs and hold ──────────

class _FakeUnit:
    def __init__(self):
        self.count, self.sf, self.rate = 100, 100.0, 1.00
        self.climate_controlled = False
        self.label = "10x10"


class _FakeCIM:
    """Minimum surface `run_value_add_scenarios` touches."""
    def __init__(self):
        self.nrsf = 50_000
        self.physical_occupancy = 0.70
        self.market_rent_psf = 1.20
        self.in_place_avg_rent_psf = 1.00
        self.ttm_total_expenses = 250_000
        self.unit_mix = [_FakeUnit()]
        self.ttm_noi = 300_000
        self.asking_price = 4_000_000


VA_FIN = {"expense_analysis": {"total_adjusted_expenses": 250_000}}


def _va(hold=None, costs=None):
    from model.value_add_model import run_value_add_scenarios
    return run_value_add_scenarios(
        cim_data=_FakeCIM(), financial_analysis=VA_FIN,
        asking_price=4_000_000, capex=0,
        hold_years=hold, transaction_costs=costs,
    )[ScenarioType.BASE]


@pytest.mark.parametrize("hold", [1, 3, 5, 10])
def test_value_add_engine_honours_hold_years(hold):
    r = _va(hold=hold, costs=COSTS)
    assert len(r["annual_noi"]) == hold
    assert len(r["monthly_noi"]) == hold * 12
    assert len(r["cash_flows"]) == hold + 1


def test_value_add_engine_applies_transaction_costs():
    """The VA model is a different engine, but it must publish IRRs on the
    same basis — otherwise `va_max_offer` stays overstated beside a
    static max offer that is not, and the two are not comparable."""
    with_costs, without = _va(costs=COSTS), _va(costs=NO_COSTS)
    assert with_costs["total_basis"] == pytest.approx(4_040_000, abs=1e-6)
    assert without["total_basis"] == pytest.approx(4_000_000, abs=1e-6)
    assert with_costs["acquisition_cost"] == pytest.approx(40_000, abs=1e-6)
    assert with_costs["disposition_cost"] == pytest.approx(
        with_costs["exit_value"] * 0.015, abs=1e-6)
    assert with_costs["irr"] < without["irr"]


def test_value_add_solver_carries_costs():
    from model.solver import solve_max_price_value_add
    cheap = solve_max_price_value_add(
        cim_data=_FakeCIM(), financial_analysis=VA_FIN,
        transaction_costs=NO_COSTS)["max_price"]
    dear = solve_max_price_value_add(
        cim_data=_FakeCIM(), financial_analysis=VA_FIN,
        transaction_costs=COSTS)["max_price"]
    assert dear < cheap


# ── 6. Config, settings registry and the XLSM template ───────────────

def test_transaction_costs_are_editable_from_the_settings_registry():
    from webapp.forms import override_key_registry
    reg = override_key_registry()
    for name in ("acquisition_closing_pct", "disposition_cost_pct"):
        entry = reg[f"TRANSACTION_COSTS.{name}"]
        assert entry["pct"] is True and entry["kind"] == "scalar"
    # Bound by value at import in the model modules, so a config patch
    # could never reach them — per-deal only, and deliberately absent.
    assert "DEFAULT_HOLD_YEARS" not in reg


def test_settings_registry_is_derived_from_config_not_hardcoded():
    """No-drift guard: adding a cost key to config.py must surface it."""
    from config import TRANSACTION_COSTS
    from webapp.forms import override_key_registry
    reg = override_key_registry()
    assert {f"TRANSACTION_COSTS.{k}" for k in TRANSACTION_COSTS} <= set(reg)


class _FakeSheet(dict):
    """openpyxl worksheet stand-in — the writers under test only assign
    by cell reference, and the real .xlsm template is not in the repo."""


def test_template_sale_month_follows_hold_years():
    from output.template_writer import _write_property_description
    ws = _FakeSheet()
    cim = _FakeCIM()
    cim.property_name = cim.address = cim.city = ""
    cim.acreage = cim.year_built = None
    _write_property_description(ws, cim, hold_years=7)
    assert ws["D182"] == 84


def test_template_acquisition_cost_lands_in_the_acquisition_block():
    """The .xlsm used to compute its purchase-side outlay as price+capex
    only, so its IRR disagreed with the memo and the .xlsx — which report
    a cost-inclusive basis — on every deal. Row 24 is inside the
    template's own ACQUISITION COST block (K27 = SUM(K23:K26))."""
    from output.template_writer import _write_investment_cf
    ws = _FakeSheet()
    cim = _FakeCIM()
    cim.capex_estimate = 0
    _write_investment_cf(ws, cim, costs={"acquisition_closing_pct": 0.01,
                                         "disposition_cost_pct": 0.015})
    assert ws["K23"] == 4_000_000
    assert ws["K24"] == pytest.approx(40_000, abs=1e-6)
    assert ws["B24"] == "Acquisition Closing Costs"


def test_template_selling_cost_is_wired_not_hardcoded():
    """K182 was a hardcoded 3.5%. F254 — what the scope contract pointed
    at — is the GP disposition FEE and correctly stays 0; writing the
    broker cost there would have double-counted against this cell."""
    from output.template_writer import _write_reversion
    ws = _FakeSheet()
    _write_reversion(ws, _FakeCIM(), {"adjusted_ttm_noi":
                                      {"analyst_adjusted_noi": 300_000}},
                     costs={"acquisition_closing_pct": 0.01,
                            "disposition_cost_pct": 0.02})
    assert ws["K182"] == 0.02


# ── 6b. The IRR gate must not claim a hold it did not measure ────────

@pytest.mark.parametrize("hold", [3, 5, 10])
def test_irr_gate_label_follows_the_hold(mock_cim_data, hold):
    from analysis.filters import evaluate_gates

    scenarios = run_scenarios(adjusted_ttm_noi=300_000,
                              asking_price=4_000_000, nrsf=50_000,
                              hold_years=hold)
    gate4 = next(g for g in evaluate_gates(mock_cim_data, scenarios, None)
                 if g["gate"] == 4)
    assert gate4["name"].startswith(f"{hold}-Yr")


def test_irr_gate_config_key_is_not_renamed():
    """`min_irr_5yr` reads oddly with a variable hold, but stored
    ConfigOverride rows reference it by name — renaming orphans them."""
    from config import GATES
    from webapp.forms import override_key_registry
    assert "min_irr_5yr" in GATES
    assert "GATES.min_irr_5yr" in override_key_registry()


# ── 6c. Every surface labels the hold it actually measured ───────────

@pytest.mark.parametrize("hold", [3, 5, 10])
def test_results_page_labels_the_hold_it_measured(hold):
    """The primary results screen. It used to hardcode "5-Year IRR", so a
    3- or 10-year run displayed a correct number under a wrong label —
    an analyst comparing deals would misread the annualization basis."""
    from webapp.results import returns_context

    ctx = returns_context({"scenario_results": run_scenarios(
        adjusted_ttm_noi=300_000, asking_price=4_000_000, nrsf=50_000,
        hold_years=hold)})
    labels = [r["label"] for r in ctx["scenario_rows"]]
    assert f"{hold}-Year IRR" in labels
    assert f"{hold}-Year MOIC" in labels


def test_results_page_falls_back_to_the_config_default_not_a_literal():
    """A run stored before item B carries neither hold_years nor a
    projection; it was always five years. The fallback must READ
    config.DEFAULT_HOLD_YEARS rather than repeat the number."""
    from config import DEFAULT_HOLD_YEARS
    from webapp.results import returns_context

    ctx = returns_context({"scenario_results": {"base": {"irr": 0.1}}})
    assert f"{DEFAULT_HOLD_YEARS}-Year IRR" in [r["label"]
                                                for r in ctx["scenario_rows"]]


def test_excel_va_projection_is_not_truncated_at_five_years():
    """`min(len(annual_noi), 5)` silently dropped years 6-10."""
    import inspect

    from output import excel_writer
    src = inspect.getsource(excel_writer)
    assert "min(len(annual_noi), 5)" not in src


# ── 7. No duplicated projection loop survives ────────────────────────

def test_only_one_projection_loop_exists():
    """Acceptance criterion from the scope contract: the hardcoded
    five-year band is gone from every module."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    hits = [p for d in ("analysis", "model")
            for p in (root / d).glob("*.py")
            if "range(2, 6)" in p.read_text()]
    assert hits == []
