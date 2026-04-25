"""
5-year unlevered DCF model — Bear / Base / Bull.

This module wraps the valuation scenario engine and provides
structured output for the Excel writer.
"""

from analysis.valuation import run_scenarios
from registry import ScenarioType


def build_returns_model(adjusted_ttm_noi: float, asking_price: float,
                        nrsf: float, capex: float = 0,
                        custom_scenarios: dict = None,
                        expense_ratio: float = None) -> dict:
    """
    Build complete returns model for all three scenarios.

    Returns:
        - scenarios: dict with bear/base/bull full results
        - summary_table: condensed comparison table
        - sensitivity: IRR sensitivity to price and exit cap
    """
    scenarios = run_scenarios(
        adjusted_ttm_noi=adjusted_ttm_noi,
        asking_price=asking_price,
        nrsf=nrsf,
        capex=capex,
        custom_scenarios=custom_scenarios,
        expense_ratio=expense_ratio,
    )

    summary = _build_summary_table(scenarios)
    sensitivity = _build_sensitivity(adjusted_ttm_noi, asking_price, capex, nrsf,
                                     expense_ratio=expense_ratio)

    return {
        "scenarios": scenarios,
        "summary_table": summary,
        "sensitivity": sensitivity,
    }


def _build_summary_table(scenarios: dict) -> list[dict]:
    """Build condensed comparison across scenarios."""
    rows = []
    for name in ScenarioType:
        s = scenarios.get(name, {})
        rows.append({
            "scenario": name.title(),
            "yr1_yoc": s.get("yield_on_cost"),
            "irr_5yr": s.get("irr"),
            "moic_5yr": s.get("moic"),
            "entry_cap": s.get("entry_cap"),
            "exit_cap": s.get("exit_cap"),
            "exit_value": s.get("exit_value"),
            "yr1_noi": s["noi_projection"][0] if s.get("noi_projection") else None,
            "yr5_noi": s["noi_projection"][-1] if s.get("noi_projection") else None,
        })
    return rows


def _build_sensitivity(ttm_noi: float, base_price: float,
                       capex: float, nrsf: float,
                       expense_ratio: float = None) -> dict:
    """
    Build IRR sensitivity table.

    Rows: purchase price ±10% in 2.5% steps
    Cols: exit cap ±100bps in 25bps steps
    """
    import numpy_financial as npf
    from config import SCENARIO_DEFAULTS

    base_params = SCENARIO_DEFAULTS[ScenarioType.BASE]

    # Price steps: -10% to +10% in 2.5% increments
    price_steps = [-0.10, -0.075, -0.05, -0.025, 0.0, 0.025, 0.05, 0.075, 0.10]
    prices = [base_price * (1 + s) for s in price_steps]
    price_labels = [f"{s:+.1%}" for s in price_steps]

    # Exit cap steps: -100bps to +100bps in 25bps increments
    base_exit_cap = base_params["exit_cap"]
    cap_offsets = [-0.0100, -0.0075, -0.0050, -0.0025, 0.0, 0.0025, 0.0050, 0.0075, 0.0100]
    exit_caps = [base_exit_cap + o for o in cap_offsets]
    cap_labels = [f"{c:.2%}" for c in exit_caps]

    # Build IRR grid
    grid = []
    for price in prices:
        row = []
        for exit_cap in exit_caps:
            irr = _compute_irr_for_sensitivity(
                ttm_noi, price, capex, base_params, exit_cap,
                expense_ratio=expense_ratio,
            )
            row.append(irr)
        grid.append(row)

    return {
        "price_labels": price_labels,
        "price_values": prices,
        "cap_labels": cap_labels,
        "cap_values": exit_caps,
        "irr_grid": grid,
        "base_price": base_price,
        "base_exit_cap": base_exit_cap,
    }


def _compute_irr_for_sensitivity(ttm_noi: float, price: float,
                                 capex: float, params: dict,
                                 exit_cap: float,
                                 expense_ratio: float = None) -> float | None:
    """Compute IRR for a single price/exit cap combination."""
    import numpy_financial as npf

    total_basis = price + capex
    if total_basis <= 0:
        return None

    yr1_noi = ttm_noi * (1 + params["yr1_noi_bump"])
    from registry import clamp_expense_ratio
    est_expense_ratio = clamp_expense_ratio(expense_ratio)
    yr1_revenue = yr1_noi / (1 - est_expense_ratio)
    yr1_expenses = yr1_revenue * est_expense_ratio

    rev = yr1_revenue
    exp = yr1_expenses
    noi_series = [yr1_noi]

    for yr in range(2, 6):
        rev_g = params["rev_cagr_yr1_3"] if yr <= 3 else params["rev_cagr_yr4_5"]
        rev = rev * (1 + rev_g)
        exp = exp * (1 + params["exp_growth"])
        noi_series.append(rev - exp)

    yr5_noi = noi_series[-1]
    exit_value = yr5_noi / exit_cap if exit_cap > 0 else 0

    cash_flows = [-total_basis]
    for i, noi in enumerate(noi_series):
        if i == len(noi_series) - 1:
            cash_flows.append(noi + exit_value)
        else:
            cash_flows.append(noi)

    try:
        irr = npf.irr(cash_flows)
        if irr is None or irr != irr:
            return None
        return irr
    except (ValueError, FloatingPointError):
        return None
