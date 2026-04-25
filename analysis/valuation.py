"""
Section 6 — Scenario NOI Forecast, IRR / MOIC Calculation.

Builds Bear / Base / Bull 5-year unlevered DCF models.
All returns are computed on an all-equity (unlevered) basis.
"""

import numpy_financial as npf
from config import SCENARIO_DEFAULTS
from registry import ScenarioType, clamp_expense_ratio


def run_scenarios(adjusted_ttm_noi: float, asking_price: float,
                  nrsf: float, capex: float = 0,
                  custom_scenarios: dict = None,
                  expense_ratio: float = None) -> dict:
    """
    Run Bear / Base / Bull 5-year unlevered return scenarios.

    Args:
        adjusted_ttm_noi: analyst-adjusted trailing 12-month NOI
        asking_price: total acquisition price
        nrsf: net rentable square feet
        capex: estimated capital expenditure at acquisition
        custom_scenarios: optional override of SCENARIO_DEFAULTS
        expense_ratio: actual OpEx/Revenue ratio from financial analysis
                       (falls back to 0.40 if not provided)

    Returns:
        dict keyed by scenario name, each containing:
            - noi_projection: 5-year NOI series
            - exit_value: terminal value
            - cash_flows: annual cash flow series for IRR
            - irr: 5-year unlevered IRR
            - moic: multiple on invested capital
            - yield_on_cost: Year 1 NOI / total basis
    """
    scenarios = custom_scenarios or SCENARIO_DEFAULTS
    total_basis = asking_price + capex

    results = {}
    for name, params in scenarios.items():
        result = _run_single_scenario(
            scenario_name=name,
            ttm_noi=adjusted_ttm_noi,
            total_basis=total_basis,
            asking_price=asking_price,
            capex=capex,
            nrsf=nrsf,
            params=params,
            expense_ratio=expense_ratio,
        )
        results[name] = result

    return results


def _run_single_scenario(scenario_name: str, ttm_noi: float,
                         total_basis: float, asking_price: float,
                         capex: float, nrsf: float,
                         params: dict,
                         expense_ratio: float = None) -> dict:
    """Compute a single scenario's 5-year returns."""

    # Year 0 = TTM NOI (base)
    yr0_noi = ttm_noi

    # Year 1 NOI = TTM * (1 + yr1_noi_bump)
    yr1_noi = yr0_noi * (1 + params["yr1_noi_bump"])

    # Revenue growth: Yr 1-3 at one rate, Yr 4-5 at another
    # We apply revenue growth to NOI for simplicity in unlevered model
    # (expenses grow at exp_growth rate)
    noi_series = [yr1_noi]
    exp_growth = params["exp_growth"]
    rev_growth_1_3 = params["rev_cagr_yr1_3"]
    rev_growth_4_5 = params["rev_cagr_yr4_5"]

    # Build 5-year NOI projection
    # NOI_t = Revenue_t - Expenses_t
    # We model revenue and expense growth separately
    # Starting from Year 1 NOI, approximate:
    # Revenue grows at rev_cagr, expenses grow at exp_growth
    # NOI margin shifts accordingly

    est_expense_ratio = clamp_expense_ratio(expense_ratio)
    yr1_revenue = yr1_noi / (1 - est_expense_ratio)
    yr1_expenses = yr1_revenue * est_expense_ratio

    rev_series = [yr1_revenue]
    exp_series = [yr1_expenses]

    for yr in range(2, 6):
        rev_growth = rev_growth_1_3 if yr <= 3 else rev_growth_4_5
        new_rev = rev_series[-1] * (1 + rev_growth)
        new_exp = exp_series[-1] * (1 + exp_growth)
        rev_series.append(new_rev)
        exp_series.append(new_exp)
        noi_series.append(new_rev - new_exp)

    # Exit value = Year 5 NOI / exit cap rate
    exit_cap = params["exit_cap"]
    yr5_noi = noi_series[-1]
    exit_value = yr5_noi / exit_cap

    # Entry cap rate (on adjusted TTM NOI)
    entry_cap = ttm_noi / asking_price if asking_price > 0 else 0

    # Enforce exit cap >= entry cap in base and bear cases
    if scenario_name in (ScenarioType.BASE, ScenarioType.BEAR) and exit_cap < entry_cap:
        exit_cap = entry_cap
        exit_value = yr5_noi / exit_cap

    # Cash flows for IRR: Year 0 = negative total basis, Years 1-4 = NOI, Year 5 = NOI + exit
    cash_flows = [-total_basis]
    for i, noi in enumerate(noi_series):
        if i == len(noi_series) - 1:
            cash_flows.append(noi + exit_value)
        else:
            cash_flows.append(noi)

    # IRR
    try:
        irr = npf.irr(cash_flows)
        if irr is None or irr != irr:  # NaN check
            irr = None
    except (ValueError, FloatingPointError):
        irr = None

    # MOIC
    total_return = sum(cash_flows[1:])
    moic = total_return / total_basis if total_basis > 0 else None

    # Yield on cost
    yoc = yr1_noi / total_basis if total_basis > 0 else None

    return {
        "scenario": scenario_name,
        "params": params,
        "yr0_noi": yr0_noi,
        "noi_projection": noi_series,
        "revenue_projection": rev_series,
        "expense_projection": exp_series,
        "exit_cap": exit_cap,
        "entry_cap": entry_cap,
        "exit_value": exit_value,
        "cash_flows": cash_flows,
        "irr": irr,
        "moic": moic,
        "yield_on_cost": yoc,
        "total_basis": total_basis,
        "asking_price": asking_price,
        "capex": capex,
        "noi_per_sf": [n / nrsf for n in noi_series] if nrsf else [],
    }
