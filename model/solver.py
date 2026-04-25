"""
Bisection solver — find maximum purchase price for target unlevered IRR.

Method: Bisection search on purchase price.
Price ↑ → IRR ↓ (monotonic), so bisection converges reliably.
Convergence to 0.1% IRR precision in ~20 iterations.

Includes both static DCF solver and value-add solver.
"""

import numpy_financial as npf
from config import (SCENARIO_DEFAULTS, SOLVER_TARGET_IRR, SOLVER_TOLERANCE,
                    SOLVER_MAX_ITERATIONS, VALUE_ADD_SCENARIOS)
from registry import ScenarioType


def solve_max_price(adjusted_ttm_noi: float,
                    capex: float = 0,
                    target_irr: float = SOLVER_TARGET_IRR,
                    scenario: str = "base",
                    custom_params: dict = None,
                    expense_ratio: float = None) -> dict:
    """
    Find the maximum purchase price that delivers the target IRR.

    Args:
        adjusted_ttm_noi: analyst-adjusted TTM NOI
        capex: estimated capital expenditure
        target_irr: target 5-year unlevered IRR (default 10%)
        scenario: which scenario params to use
        custom_params: optional override of scenario params
        expense_ratio: actual OpEx/Revenue ratio from financial analysis

    Returns:
        - max_price: maximum purchase price
        - implied_entry_cap: TTM NOI / max_price
        - achieved_irr: actual IRR at max_price (should be ≈ target)
        - iterations: number of bisection iterations
        - converged: bool
    """
    params = custom_params or SCENARIO_DEFAULTS.get(scenario, SCENARIO_DEFAULTS[ScenarioType.BASE])

    # Bounds
    # Low: very cheap → high IRR
    low = adjusted_ttm_noi / 0.20 if adjusted_ttm_noi > 0 else 100_000  # 20% cap
    # High: very expensive → low/negative IRR
    high = adjusted_ttm_noi / 0.03 if adjusted_ttm_noi > 0 else 50_000_000  # ~3% cap

    best_price = None
    best_irr = None
    iterations = 0
    converged = False

    for i in range(SOLVER_MAX_ITERATIONS):
        iterations = i + 1
        mid = (low + high) / 2

        irr = _compute_irr_at_price(adjusted_ttm_noi, mid, capex, params,
                                    expense_ratio=expense_ratio)

        if irr is None:
            # IRR computation failed — narrow the range
            high = mid
            continue

        if abs(irr - target_irr) < SOLVER_TOLERANCE:
            best_price = mid
            best_irr = irr
            converged = True
            break

        if irr > target_irr:
            # Price too low (returns too high) — increase price
            low = mid
        else:
            # Price too high (returns too low) — decrease price
            high = mid

        best_price = mid
        best_irr = irr

    implied_cap = adjusted_ttm_noi / best_price if best_price and best_price > 0 else None

    return {
        "max_price": best_price,
        "implied_entry_cap": implied_cap,
        "achieved_irr": best_irr,
        "target_irr": target_irr,
        "iterations": iterations,
        "converged": converged,
        "capex": capex,
        "total_basis": (best_price + capex) if best_price else None,
    }


def _compute_irr_at_price(ttm_noi: float, price: float,
                          capex: float, params: dict,
                          expense_ratio: float = None) -> float | None:
    """Compute 5-year unlevered IRR at a given purchase price."""
    total_basis = price + capex
    if total_basis <= 0:
        return None

    yr1_noi = ttm_noi * (1 + params["yr1_noi_bump"])

    # Build 5-year NOI projection — use actual expense ratio when available
    from registry import clamp_expense_ratio
    est_expense_ratio = clamp_expense_ratio(expense_ratio)
    yr1_revenue = yr1_noi / (1 - est_expense_ratio)
    yr1_expenses = yr1_revenue * est_expense_ratio

    rev = yr1_revenue
    exp = yr1_expenses
    noi_series = [yr1_noi]

    for yr in range(2, 6):
        rev_growth = params["rev_cagr_yr1_3"] if yr <= 3 else params["rev_cagr_yr4_5"]
        rev = rev * (1 + rev_growth)
        exp = exp * (1 + params["exp_growth"])
        noi_series.append(rev - exp)

    yr5_noi = noi_series[-1]
    exit_cap = params["exit_cap"]
    exit_value = yr5_noi / exit_cap if exit_cap > 0 else 0

    # Entry cap check — enforce exit cap ≥ entry cap for base/bear
    entry_cap = ttm_noi / price if price > 0 else 0
    if exit_cap < entry_cap:
        exit_cap = entry_cap
        exit_value = yr5_noi / exit_cap if exit_cap > 0 else 0

    cash_flows = [-total_basis]
    for i, noi in enumerate(noi_series):
        if i == len(noi_series) - 1:
            cash_flows.append(noi + exit_value)
        else:
            cash_flows.append(noi)

    try:
        irr = npf.irr(cash_flows)
        if irr is None or irr != irr:  # NaN check
            return None
        return irr
    except (ValueError, FloatingPointError):
        return None


def solve_max_price_value_add(cim_data, financial_analysis: dict,
                               capex: float = 0,
                               target_irr: float = SOLVER_TARGET_IRR,
                               scenario: str = "base") -> dict:
    """
    Find the maximum purchase price for target IRR using the value-add model.

    Same bisection approach as solve_max_price, but uses the VA monthly
    cash flow engine instead of the static DCF.
    """
    from model.value_add_model import compute_va_irr_at_price

    params = VALUE_ADD_SCENARIOS.get(scenario, VALUE_ADD_SCENARIOS[ScenarioType.BASE])

    # Estimate NOI for bounds — use adjusted or CIM
    adj_noi = financial_analysis.get("adjusted_ttm_noi", {}).get("analyst_adjusted_noi")
    ttm_noi = adj_noi or cim_data.ttm_noi or 100_000

    low = ttm_noi / 0.20 if ttm_noi > 0 else 100_000
    high = ttm_noi / 0.02 if ttm_noi > 0 else 50_000_000

    best_price = None
    best_irr = None
    iterations = 0
    converged = False

    for i in range(SOLVER_MAX_ITERATIONS):
        iterations = i + 1
        mid = (low + high) / 2

        irr = compute_va_irr_at_price(cim_data, financial_analysis, mid, capex, params)

        if irr is None:
            high = mid
            continue

        if abs(irr - target_irr) < SOLVER_TOLERANCE:
            best_price = mid
            best_irr = irr
            converged = True
            break

        if irr > target_irr:
            low = mid
        else:
            high = mid

        best_price = mid
        best_irr = irr

    implied_cap = ttm_noi / best_price if best_price and best_price > 0 else None

    return {
        "max_price": best_price,
        "implied_entry_cap": implied_cap,
        "achieved_irr": best_irr,
        "target_irr": target_irr,
        "iterations": iterations,
        "converged": converged,
        "capex": capex,
        "total_basis": (best_price + capex) if best_price else None,
        "model_type": "value_add",
    }
