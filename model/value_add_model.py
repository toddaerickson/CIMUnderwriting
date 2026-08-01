"""
Value-Add Model — Monthly cash flow engine for lease-up / rent-push deals.

When a property triggers value-add criteria (sub-85% occupancy, in-place
rents below market, etc.), this model replaces the static DCF with a
monthly cash flow projection that models:
  - Rent ramp from in-place to market rate over stabilization period
  - Occupancy compression from current to target
  - Expenses growing monthly
  - Exit at Year 5 forward NOI / exit cap

Returns Bear / Base / Bull scenarios with IRR, MOIC, yield-on-cost,
stabilized NOI, development spread, and monthly detail.
"""

import numpy_financial as npf
from analysis.valuation import resolve_hold_years, resolve_transaction_costs
from config import VALUE_ADD_SCENARIOS, VALUE_ADD_TRIGGERS
from registry import ScenarioType


def detect_value_add(cim_data) -> bool:
    """
    Determine if this deal should use the value-add model.

    Triggers:
      1. Physical occupancy below threshold (default 85%)
      2. In-place rent/SF significantly below market_rent_psf (10%+ gap)
    """
    occ = cim_data.physical_occupancy
    if occ is not None and occ < VALUE_ADD_TRIGGERS["max_occupancy"]:
        return True

    # Check rent gap if market data available
    if cim_data.market_rent_psf and cim_data.unit_mix:
        in_place = _compute_in_place_rent_psf(cim_data)
        if in_place and in_place > 0:
            gap = (cim_data.market_rent_psf - in_place) / in_place
            if gap >= VALUE_ADD_TRIGGERS["min_rent_gap_pct"]:
                return True

    return False


def run_value_add_scenarios(cim_data, financial_analysis: dict,
                            asking_price: float, capex: float = 0,
                            custom_scenarios: dict = None,
                            hold_years: int = None,
                            transaction_costs: dict = None,
                            reserve: float = 0.0) -> dict:
    """
    Run Bear / Base / Bull value-add scenarios with monthly cash flows.

    Args:
        cim_data: parsed CIM data
        financial_analysis: output from analyze_financials()
        asking_price: total acquisition price
        capex: estimated capital expenditure
        custom_scenarios: optional override of VALUE_ADD_SCENARIOS
        hold_years: hold period in years (default config.DEFAULT_HOLD_YEARS)
        transaction_costs: override of config.TRANSACTION_COSTS
        reserve: upfront operating reserve in dollars (item D). Carried
            here for the same reason transaction costs are: a VA basis
            that excludes the reserve while the static basis includes it
            makes the two IRRs non-comparable.

    Returns:
        dict keyed by scenario name, each containing:
            - monthly_noi: 60-element list of monthly NOI
            - annual_noi: 5-element list of annual NOI
            - annual_revenue: 5-element list
            - annual_expenses: 5-element list
            - stabilized_noi: NOI at stabilization
            - months_to_stabilize: from params
            - cash_flows: annual cash flows for IRR (Year 0..5)
            - irr, moic, yield_on_cost
            - exit_value, entry_cap, exit_cap
            - development_spread: stabilized yield minus exit cap
    """
    scenarios = custom_scenarios or VALUE_ADD_SCENARIOS

    # Compute starting metrics from CIM data
    in_place_rent_psf = _compute_in_place_rent_psf(cim_data)
    market_rent_psf = cim_data.market_rent_psf or in_place_rent_psf
    current_occ = cim_data.physical_occupancy or 0.80
    nrsf = cim_data.nrsf or 1

    # Get adjusted expenses from financial analysis
    adj_expenses = financial_analysis.get("expense_analysis", {}).get(
        "total_adjusted_expenses", 0)
    if not adj_expenses:
        # Fallback: use CIM total expenses
        adj_expenses = cim_data.ttm_total_expenses or 0

    # Monthly starting expense (annual / 12)
    monthly_expenses_start = adj_expenses / 12

    results = {}
    for name, params in scenarios.items():
        result = _run_single_va_scenario(
            name=name,
            params=params,
            nrsf=nrsf,
            in_place_rent_psf=in_place_rent_psf,
            market_rent_psf=market_rent_psf,
            current_occ=current_occ,
            monthly_expenses_start=monthly_expenses_start,
            asking_price=asking_price,
            capex=capex,
            hold_years=hold_years,
            costs=transaction_costs,
            reserve=reserve,
        )
        results[name] = result

    return results


def _run_single_va_scenario(name: str, params: dict,
                             nrsf: float,
                             in_place_rent_psf: float,
                             market_rent_psf: float,
                             current_occ: float,
                             monthly_expenses_start: float,
                             asking_price: float,
                             capex: float,
                             hold_years: int = None,
                             costs: dict = None,
                             reserve: float = 0.0) -> dict:
    """Compute a single value-add scenario with monthly granularity.

    This is a genuinely different engine from `analysis.valuation.
    project_cash_flows` — monthly, with a lease-up ramp — so it is not
    folded into it. It does share the hold period and the transaction-cost
    arithmetic, because publishing a VA IRR net of costs beside a static
    IRR gross of them would just relocate the defect item B exists to fix.
    """

    hold_years = resolve_hold_years(hold_years)
    costs = resolve_transaction_costs(costs)
    hold_months = hold_years * 12
    reserve = float(reserve or 0.0)
    total_basis = (asking_price + capex + reserve
                   + asking_price * costs["acquisition_closing_pct"])

    months_to_stab = int(params["months_to_stabilize"])
    target_occ = params["target_occupancy"]
    rent_capture = params["rent_growth_to_market"]
    exit_cap = params["exit_cap"]
    expense_growth_annual = params["expense_growth"]
    post_stab_rev_growth = params["post_stabilize_rev_growth"]

    # Target rent = in-place + (gap * capture fraction)
    rent_gap = market_rent_psf - in_place_rent_psf
    target_rent_psf = in_place_rent_psf + (rent_gap * rent_capture)

    # Monthly expense growth rate
    monthly_exp_growth = (1 + expense_growth_annual) ** (1 / 12) - 1

    # Build the monthly projection across the hold
    monthly_revenue = []
    monthly_expenses = []
    monthly_noi = []

    for month in range(hold_months):
        # Rent ramp: linear from in-place to target over stabilization period
        if month < months_to_stab:
            frac = month / months_to_stab
            rent_psf = in_place_rent_psf + (target_rent_psf - in_place_rent_psf) * frac
            occ = current_occ + (target_occ - current_occ) * frac
        else:
            # Post-stabilization: target rent grows at post_stab rate
            months_past_stab = month - months_to_stab
            monthly_post_stab_growth = (1 + post_stab_rev_growth) ** (1 / 12) - 1
            rent_psf = target_rent_psf * (1 + monthly_post_stab_growth) ** months_past_stab
            occ = target_occ

        rev = rent_psf * nrsf * occ
        exp = monthly_expenses_start * (1 + monthly_exp_growth) ** month

        monthly_revenue.append(rev)
        monthly_expenses.append(exp)
        monthly_noi.append(rev - exp)

    # Annualize: sum months into years
    annual_revenue = []
    annual_expenses = []
    annual_noi = []
    for yr in range(hold_years):
        start = yr * 12
        end = start + 12
        annual_revenue.append(sum(monthly_revenue[start:end]))
        annual_expenses.append(sum(monthly_expenses[start:end]))
        annual_noi.append(sum(monthly_noi[start:end]))

    # Stabilized NOI (first full year at stabilization)
    stab_month = int(min(months_to_stab, hold_months - 1))
    # Use the 12 months centered around stabilization for stabilized NOI
    stab_start = max(0, stab_month)
    stab_end = min(hold_months, stab_start + 12)
    stabilized_annual_noi = sum(monthly_noi[stab_start:stab_end])
    if stab_end - stab_start < 12:
        stabilized_annual_noi = stabilized_annual_noi * 12 / (stab_end - stab_start)

    # Exit value = forward NOI (final full year) / exit cap
    yr5_noi = annual_noi[-1]
    exit_value = yr5_noi / exit_cap if exit_cap > 0 else 0

    # Entry cap = Year 1 NOI / asking price
    entry_cap = annual_noi[0] / asking_price if asking_price > 0 else 0

    # Enforce exit cap >= entry cap for base and bear
    if name in (ScenarioType.BASE, ScenarioType.BEAR) and exit_cap < entry_cap:
        exit_cap = entry_cap
        exit_value = yr5_noi / exit_cap if exit_cap > 0 else 0

    # Disposition costs come out of gross exit value, same rule the static
    # DCF applies (analysis.valuation.project_cash_flows).
    disposition_cost = exit_value * costs["disposition_cost_pct"]
    net_exit_proceeds = exit_value - disposition_cost

    # Cash flows: Year 0 = -total_basis, interim years = NOI, final year =
    # NOI + net sale proceeds.
    cash_flows = [-total_basis]
    for i, noi in enumerate(annual_noi):
        if i == len(annual_noi) - 1:
            cash_flows.append(noi + net_exit_proceeds)
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

    # Yield on cost (stabilized NOI / total basis)
    yoc = stabilized_annual_noi / total_basis if total_basis > 0 else None

    # Development spread = stabilized yield - exit cap
    dev_spread = (yoc - exit_cap) if (yoc and exit_cap) else None

    return {
        "scenario": name,
        "params": params,
        "monthly_noi": monthly_noi,
        "monthly_revenue": monthly_revenue,
        "monthly_expenses": monthly_expenses,
        "annual_noi": annual_noi,
        "annual_revenue": annual_revenue,
        "annual_expenses": annual_expenses,
        "stabilized_noi": stabilized_annual_noi,
        "months_to_stabilize": months_to_stab,
        "in_place_rent_psf": in_place_rent_psf,
        "target_rent_psf": target_rent_psf,
        "market_rent_psf": market_rent_psf,
        "current_occupancy": current_occ,
        "target_occupancy": target_occ,
        "cash_flows": cash_flows,
        "exit_value": exit_value,
        "disposition_cost": disposition_cost,
        "net_exit_proceeds": net_exit_proceeds,
        "acquisition_cost": asking_price * costs["acquisition_closing_pct"],
        "transaction_costs": costs,
        "reserve": reserve,
        "hold_years": hold_years,
        "entry_cap": entry_cap,
        "exit_cap": exit_cap,
        "irr": irr,
        "moic": moic,
        "yield_on_cost": yoc,
        "development_spread": dev_spread,
        "total_basis": total_basis,
        "asking_price": asking_price,
        "capex": capex,
        "noi_per_sf": [n / nrsf for n in annual_noi] if nrsf else [],
    }


def compute_va_irr_at_price(cim_data, financial_analysis: dict,
                             price: float, capex: float,
                             params: dict,
                             hold_years: int = None,
                             costs: dict = None,
                             reserve: float = 0.0) -> float | None:
    """
    Compute VA IRR at a given purchase price.
    Used by the bisection solver, so acquisition closing costs must be
    derived from `price` inside this call rather than added afterwards.
    `capex` arrives already resolved for this price — the solver owns the
    %-of-price basis, since only it knows which price is being tried.
    """
    in_place_rent_psf = _compute_in_place_rent_psf(cim_data)
    market_rent_psf = cim_data.market_rent_psf or in_place_rent_psf
    current_occ = cim_data.physical_occupancy or 0.80
    nrsf = cim_data.nrsf or 1

    adj_expenses = financial_analysis.get("expense_analysis", {}).get(
        "total_adjusted_expenses", 0)
    if not adj_expenses:
        adj_expenses = cim_data.ttm_total_expenses or 0
    monthly_expenses_start = adj_expenses / 12

    result = _run_single_va_scenario(
        name=ScenarioType.BASE,
        params=params,
        nrsf=nrsf,
        in_place_rent_psf=in_place_rent_psf,
        market_rent_psf=market_rent_psf,
        current_occ=current_occ,
        monthly_expenses_start=monthly_expenses_start,
        asking_price=price,
        capex=capex,
        hold_years=hold_years,
        costs=costs,
        reserve=reserve,
    )
    return result.get("irr")


def _compute_in_place_rent_psf(cim_data) -> float:
    """
    Compute weighted-average in-place rent per SF per month from unit mix.

    Falls back to GPR / (NRSF * 12 * occupancy) if no unit mix.

    The analyst override (CIMData.in_place_avg_rent_psf) wins first, same
    coalesce rule analysis/rent_analysis._in_place_rent_psf already
    applies — this helper used to skip it, so a saved override reached
    the ECRI risk flag but not detect_value_add/run_value_add_scenarios/
    compute_va_irr_at_price/the memo/the Excel, which could print two
    different in-place rents for the same deal (one section reads the
    override, the rest re-derive the unadjusted unit-mix average).
    """
    override = getattr(cim_data, "in_place_avg_rent_psf", None)
    if override is not None:
        return override
    if cim_data.unit_mix:
        total_sf = 0
        total_rent = 0
        for unit in cim_data.unit_mix:
            sf = unit.sf or 0
            count = unit.count or 0
            rate = unit.rate or 0
            if sf > 0 and count > 0:
                total_sf += sf * count
                total_rent += rate * count
        if total_sf > 0:
            return total_rent / total_sf

    # Fallback from GPR
    nrsf = cim_data.nrsf or 1
    gpr = cim_data.ttm_gpr
    occ = cim_data.physical_occupancy or 0.85
    if gpr:
        return gpr / (nrsf * 12)

    # Last resort: use EGR adjusted for vacancy
    egr = cim_data.ttm_egr
    if egr:
        return egr / (nrsf * 12 * occ)

    return 0.0
