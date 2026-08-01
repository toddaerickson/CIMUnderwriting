"""
Section 6 — Scenario NOI Forecast, IRR / MOIC Calculation.

Builds Bear / Base / Bull unlevered DCF models over a variable hold period.
All returns are computed on an all-equity (unlevered) basis, net of
round-trip transaction costs.

`project_cash_flows` is the ONE unlevered projection in this codebase. The
scenario engine below, the sensitivity grid in `model/returns_model.py` and
the bisection solver in `model/solver.py` all call it. They used to carry
three copies of the same loop, each hardcoding a five-year hold and each
omitting transaction costs; the copies had already drifted (see
`coerce_exit_cap`). The debt layer hangs off this seam too — do not
reintroduce a second loop.
"""

import logging

import numpy_financial as npf
from config import (DEFAULT_HOLD_YEARS, HOLD_YEARS_RANGE, SCENARIO_DEFAULTS,
                    TRANSACTION_COSTS)
from registry import ScenarioType, clamp_expense_ratio

logger = logging.getLogger("cim_analyst")

# The scenarios whose exit cap is floored at the entry cap. Bull is
# excluded on purpose — it is allowed to underwrite cap compression.
COERCED_SCENARIOS = (ScenarioType.BASE, ScenarioType.BEAR)


def resolve_transaction_costs(costs: dict = None) -> dict:
    """Merge a partial cost override onto the config defaults.

    Omitting a key means "use the default", never "zero" — a silent zero
    here is exactly the overstated-IRR defect this module was changed to
    fix. Pass explicit zeros to model a genuinely cost-free round trip.
    """
    resolved = dict(TRANSACTION_COSTS)
    for key in resolved:
        value = (costs or {}).get(key)
        if value is not None:
            resolved[key] = float(value)
    return resolved


def resolve_hold_years(hold_years: int = None) -> int:
    """Hold period in whole years, clamped to the supported range."""
    if hold_years in (None, ""):
        return DEFAULT_HOLD_YEARS
    years = int(hold_years)
    low, high = HOLD_YEARS_RANGE
    if not low <= years <= high:
        clamped = max(low, min(high, years))
        logger.warning("hold_years %s outside %s-%s — using %s",
                       years, low, high, clamped)
        return clamped
    return years


def project_cash_flows(ttm_noi: float, price: float, capex: float,
                       params: dict, *,
                       hold_years: int = None,
                       expense_ratio: float = None,
                       costs: dict = None,
                       coerce_exit_cap: bool = True,
                       exit_cap_override: float = None) -> dict:
    """Canonical unlevered projection: NOI series → cash flows → IRR/MOIC.

    Growth banding: `rev_cagr_yr1_3` applies through year 3 and
    `rev_cagr_yr4_5` applies year 4 ONWARD — so a 10-year hold grows years
    4-10 at the second rate rather than running off the end of the band.
    Expenses grow at `exp_growth` in every year. Year 1 revenue is backed
    out of Year 1 NOI using the expense ratio, so the two series are
    consistent by construction.

    Costs: acquisition closing costs are a percentage of PRICE (CapEx is
    not a closing-cost base) and enter the Year 0 outflow. Disposition
    costs come out of gross exit value. MOIC and yield-on-cost are
    therefore both computed on the cost-inclusive basis.

    Args:
        ttm_noi: analyst-adjusted trailing 12-month NOI
        price: purchase price
        capex: capital expenditure at acquisition
        params: scenario parameter dict (see config.SCENARIO_DEFAULTS)
        hold_years: years held; None → config.DEFAULT_HOLD_YEARS
        expense_ratio: actual OpEx/Revenue ratio; None → clamped default
        costs: partial or full override of config.TRANSACTION_COSTS
        coerce_exit_cap: floor the exit cap at the entry cap. The scenario
            engine passes True for base/bear only and the solver matches
            it. The sensitivity grid passes False — its whole purpose is
            an exit-cap axis, and coercing collapses every cell below the
            entry cap onto one value.
        exit_cap_override: use this exit cap instead of `params["exit_cap"]`
            (the sensitivity grid sweeps it)
    """
    hold_years = resolve_hold_years(hold_years)
    costs = resolve_transaction_costs(costs)

    yr1_noi = ttm_noi * (1 + params["yr1_noi_bump"])

    # Split Year 1 NOI into revenue and expenses so the two can grow at
    # different rates; the NOI margin shifts accordingly.
    est_expense_ratio = clamp_expense_ratio(expense_ratio)
    yr1_revenue = yr1_noi / (1 - est_expense_ratio)
    yr1_expenses = yr1_revenue * est_expense_ratio

    rev_series = [yr1_revenue]
    exp_series = [yr1_expenses]
    noi_series = [yr1_noi]

    rev_growth_1_3 = params["rev_cagr_yr1_3"]
    rev_growth_4_5 = params["rev_cagr_yr4_5"]
    exp_growth = params["exp_growth"]

    for yr in range(2, hold_years + 1):
        rev_growth = rev_growth_1_3 if yr <= 3 else rev_growth_4_5
        new_rev = rev_series[-1] * (1 + rev_growth)
        new_exp = exp_series[-1] * (1 + exp_growth)
        rev_series.append(new_rev)
        exp_series.append(new_exp)
        noi_series.append(new_rev - new_exp)

    exit_cap = (params["exit_cap"] if exit_cap_override is None
                else exit_cap_override)
    requested_exit_cap = exit_cap
    entry_cap = ttm_noi / price if price > 0 else 0
    exit_noi = noi_series[-1]

    # The coercion is RECORDED, not just applied: a run that swapped the
    # analyst's entered cap for a different one has to say so
    # (analysis.checks.exit_cap_coercion reads these keys).
    exit_cap_coerced = False
    if coerce_exit_cap and exit_cap < entry_cap:
        exit_cap = entry_cap
        exit_cap_coerced = True

    exit_value = exit_noi / exit_cap if exit_cap > 0 else 0
    disposition_cost = exit_value * costs["disposition_cost_pct"]
    net_exit_proceeds = exit_value - disposition_cost

    acquisition_cost = price * costs["acquisition_closing_pct"]
    total_basis = price + capex + acquisition_cost

    cash_flows = [-total_basis]
    for i, noi in enumerate(noi_series):
        last = i == len(noi_series) - 1
        cash_flows.append(noi + net_exit_proceeds if last else noi)

    try:
        irr = npf.irr(cash_flows)
        if irr is None or irr != irr:            # NaN check
            irr = None
    except (ValueError, FloatingPointError):
        irr = None

    return {
        "hold_years": hold_years,
        "revenue": rev_series,
        "expenses": exp_series,
        "noi": noi_series,
        "entry_cap": entry_cap,
        "requested_exit_cap": requested_exit_cap,
        "exit_cap": exit_cap,
        "exit_cap_coerced": exit_cap_coerced,
        "exit_value": exit_value,
        "disposition_cost": disposition_cost,
        "net_exit_proceeds": net_exit_proceeds,
        "acquisition_cost": acquisition_cost,
        "transaction_costs": costs,
        "total_basis": total_basis,
        "cash_flows": cash_flows,
        "irr": irr,
        "moic": (sum(cash_flows[1:]) / total_basis) if total_basis > 0 else None,
        "yield_on_cost": (yr1_noi / total_basis) if total_basis > 0 else None,
    }


def run_scenarios(adjusted_ttm_noi: float, asking_price: float,
                  nrsf: float, capex: float = 0,
                  custom_scenarios: dict = None,
                  expense_ratio: float = None,
                  hold_years: int = None,
                  transaction_costs: dict = None) -> dict:
    """
    Run Bear / Base / Bull unlevered return scenarios.

    Args:
        adjusted_ttm_noi: analyst-adjusted trailing 12-month NOI
        asking_price: total acquisition price
        nrsf: net rentable square feet
        capex: estimated capital expenditure at acquisition
        custom_scenarios: optional override of SCENARIO_DEFAULTS
        expense_ratio: actual OpEx/Revenue ratio from financial analysis
                       (falls back to 0.40 if not provided)
        hold_years: hold period in years (default config.DEFAULT_HOLD_YEARS)
        transaction_costs: override of config.TRANSACTION_COSTS

    Returns:
        dict keyed by scenario name, each containing:
            - noi_projection: NOI series over the hold
            - exit_value: gross terminal value (before disposition cost)
            - cash_flows: annual cash flow series for IRR
            - irr: unlevered IRR over the hold, net of transaction costs
            - moic: multiple on cost-inclusive invested capital
            - yield_on_cost: Year 1 NOI / total basis
    """
    scenarios = custom_scenarios or SCENARIO_DEFAULTS

    return {
        name: _run_single_scenario(
            scenario_name=name,
            ttm_noi=adjusted_ttm_noi,
            asking_price=asking_price,
            capex=capex,
            nrsf=nrsf,
            params=params,
            expense_ratio=expense_ratio,
            hold_years=hold_years,
            transaction_costs=transaction_costs,
        )
        for name, params in scenarios.items()
    }


def _run_single_scenario(scenario_name: str, ttm_noi: float,
                         asking_price: float, capex: float, nrsf: float,
                         params: dict,
                         expense_ratio: float = None,
                         hold_years: int = None,
                         transaction_costs: dict = None) -> dict:
    """Label and reshape one canonical projection for the scenario API."""
    p = project_cash_flows(
        ttm_noi=ttm_noi,
        price=asking_price,
        capex=capex,
        params=params,
        hold_years=hold_years,
        expense_ratio=expense_ratio,
        costs=transaction_costs,
        coerce_exit_cap=scenario_name in COERCED_SCENARIOS,
    )

    return {
        "scenario": scenario_name,
        "params": params,
        "yr0_noi": ttm_noi,
        "hold_years": p["hold_years"],
        "noi_projection": p["noi"],
        "revenue_projection": p["revenue"],
        "expense_projection": p["expenses"],
        "exit_cap": p["exit_cap"],
        "requested_exit_cap": p["requested_exit_cap"],
        "exit_cap_coerced": p["exit_cap_coerced"],
        "entry_cap": p["entry_cap"],
        "exit_value": p["exit_value"],
        "disposition_cost": p["disposition_cost"],
        "net_exit_proceeds": p["net_exit_proceeds"],
        "acquisition_cost": p["acquisition_cost"],
        "transaction_costs": p["transaction_costs"],
        "cash_flows": p["cash_flows"],
        "irr": p["irr"],
        "moic": p["moic"],
        "yield_on_cost": p["yield_on_cost"],
        "total_basis": p["total_basis"],
        "asking_price": asking_price,
        "capex": capex,
        "noi_per_sf": [n / nrsf for n in p["noi"]] if nrsf else [],
    }
