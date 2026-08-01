"""
Bisection solver — find maximum purchase price for target unlevered IRR.

Method: Bisection search on purchase price.
Price ↑ → IRR ↓ (monotonic), so bisection converges reliably.
Convergence to 0.1% IRR precision in ~20 iterations.

Includes both static DCF solver and value-add solver.
"""

from analysis.valuation import (COERCED_SCENARIOS, project_cash_flows,
                                resolve_exit_cap, resolve_market_cap,
                                resolve_transaction_costs)
from config import (SCENARIO_DEFAULTS, SOLVER_TARGET_IRR, SOLVER_TOLERANCE,
                    SOLVER_MAX_ITERATIONS, VALUE_ADD_SCENARIOS)
from registry import ScenarioType


def solve_max_price(adjusted_ttm_noi: float,
                    capex: float = 0,
                    target_irr: float = SOLVER_TARGET_IRR,
                    scenario: str = "base",
                    custom_params: dict = None,
                    expense_ratio: float = None,
                    hold_years: int = None,
                    transaction_costs: dict = None,
                    reserve: float = 0.0,
                    capex_pct_of_price: float = None,
                    market_cap: dict = None) -> dict:
    """
    Find the maximum purchase price that delivers the target IRR.

    Acquisition closing costs scale with the price being solved for, so
    they are computed INSIDE the target function rather than added to the
    answer afterwards. Bisection stays valid: IRR is still monotone
    decreasing in price once costs are proportional to it.

    Args:
        adjusted_ttm_noi: analyst-adjusted TTM NOI
        capex: estimated capital expenditure, in dollars
        target_irr: target unlevered IRR over the hold (default 10%)
        scenario: which scenario params to use
        custom_params: optional override of scenario params
        expense_ratio: actual OpEx/Revenue ratio from financial analysis
        hold_years: hold period in years (default config.DEFAULT_HOLD_YEARS)
        transaction_costs: override of config.TRANSACTION_COSTS
        reserve: upfront operating reserve (item D). Fixed dollars — none
            of its bases depend on price — so it shifts the answer without
            affecting monotonicity.
        capex_pct_of_price: set when CapEx was entered as a percentage of
            price (item H). CapEx then MOVES with the price being solved
            for, exactly like closing costs, and holding it at the
            asking-price dollars would price a different deal than the one
            the answer describes. `total_basis` stays
            `price × (1 + acq% + capex%) + reserve` — strictly increasing
            in price, so bisection remains valid.

    Returns:
        - max_price: maximum purchase price
        - implied_entry_cap: TTM NOI / max_price
        - achieved_irr: actual IRR at max_price (should be ≈ target)
        - capex: CapEx at max_price (resolved, when it scales with price)
        - total_basis: max_price + capex + closing costs + reserve
        - iterations: number of bisection iterations
        - converged: bool
    """
    params = custom_params or SCENARIO_DEFAULTS.get(scenario, SCENARIO_DEFAULTS[ScenarioType.BASE])
    costs = resolve_transaction_costs(transaction_costs)
    reserve = float(reserve or 0.0)

    # Resolved ONCE, outside the bisection loop. Unlike a percentage-of-price
    # CapEx, the exit cap does not move with price — it is a property of the
    # asset's class, age and hold — so re-resolving per iteration would cost
    # 50 lookups to return the same number. The entry-cap coercion inside
    # project_cash_flows still moves with price; that is deliberate and is
    # what makes the objective piecewise (see the convergence note above).
    mc = market_cap or resolve_market_cap()
    exit_cap = resolve_exit_cap(mc["market_cap"], scenario,
                                hold_years)["exit_cap"]

    def capex_at(price: float) -> float:
        return (price * capex_pct_of_price if capex_pct_of_price
                else (capex or 0.0))

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
        mid_capex = capex_at(mid)

        irr = project_cash_flows(
            ttm_noi=adjusted_ttm_noi, price=mid, capex=mid_capex, params=params,
            hold_years=hold_years, expense_ratio=expense_ratio, costs=costs,
            reserve=reserve,
            coerce_exit_cap=scenario in COERCED_SCENARIOS,
            exit_cap=exit_cap,
        )["irr"] if (mid + mid_capex) > 0 else None

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
    acquisition_cost = (best_price * costs["acquisition_closing_pct"]
                        if best_price else None)
    solved_capex = capex_at(best_price) if best_price else (capex or 0.0)

    return {
        "max_price": best_price,
        "implied_entry_cap": implied_cap,
        "achieved_irr": best_irr,
        "target_irr": target_irr,
        "iterations": iterations,
        "converged": converged,
        "capex": solved_capex,
        "acquisition_cost": acquisition_cost,
        "transaction_costs": costs,
        "reserve": reserve,
        "total_basis": (best_price + solved_capex + acquisition_cost + reserve)
                       if best_price else None,
    }


def solve_max_price_value_add(cim_data, financial_analysis: dict,
                               capex: float = 0,
                               target_irr: float = SOLVER_TARGET_IRR,
                               scenario: str = "base",
                               hold_years: int = None,
                               transaction_costs: dict = None,
                               reserve: float = 0.0,
                               capex_pct_of_price: float = None,
                               market_cap: dict = None) -> dict:
    """
    Find the maximum purchase price for target IRR using the value-add model.

    Same bisection approach as solve_max_price, but uses the VA monthly
    cash flow engine instead of the static DCF. Both solvers carry the
    same hold period, transaction costs, reserve and CapEx basis — a VA
    max offer computed on a different basis than the static one is not
    comparable to it, which is the whole point of showing them together.
    """
    from model.value_add_model import compute_va_irr_at_price

    params = VALUE_ADD_SCENARIOS.get(scenario, VALUE_ADD_SCENARIOS[ScenarioType.BASE])
    costs = resolve_transaction_costs(transaction_costs)
    reserve = float(reserve or 0.0)

    def capex_at(price: float) -> float:
        return (price * capex_pct_of_price if capex_pct_of_price
                else (capex or 0.0))

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

        irr = compute_va_irr_at_price(cim_data, financial_analysis, mid,
                                      capex_at(mid), params,
                                      hold_years=hold_years, costs=costs,
                                      reserve=reserve, market_cap=market_cap)

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
    va_acquisition_cost = (best_price * costs["acquisition_closing_pct"]
                           if best_price else None)
    solved_capex = capex_at(best_price) if best_price else (capex or 0.0)

    return {
        "max_price": best_price,
        "implied_entry_cap": implied_cap,
        "achieved_irr": best_irr,
        "target_irr": target_irr,
        "iterations": iterations,
        "converged": converged,
        "capex": solved_capex,
        "acquisition_cost": va_acquisition_cost,
        "transaction_costs": costs,
        "reserve": reserve,
        "total_basis": (best_price + solved_capex + va_acquisition_cost
                        + reserve) if best_price else None,
        "model_type": "value_add",
    }
