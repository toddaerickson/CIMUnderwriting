"""
Unlevered DCF model — Bear / Base / Bull over a variable hold period.

This module wraps the valuation scenario engine and provides
structured output for the Excel writer. The projection itself lives in
`analysis.valuation.project_cash_flows` — the sensitivity grid below used
to carry its own copy of that loop.
"""

from analysis.valuation import project_cash_flows, run_scenarios
from registry import ScenarioType


def build_returns_model(adjusted_ttm_noi: float, asking_price: float,
                        nrsf: float, capex: float = 0,
                        custom_scenarios: dict = None,
                        expense_ratio: float = None,
                        hold_years: int = None,
                        transaction_costs: dict = None) -> dict:
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
        hold_years=hold_years,
        transaction_costs=transaction_costs,
    )

    summary = _build_summary_table(scenarios)
    sensitivity = _build_sensitivity(
        adjusted_ttm_noi, asking_price, capex, nrsf,
        expense_ratio=expense_ratio,
        custom_scenarios=custom_scenarios,
        hold_years=hold_years,
        transaction_costs=transaction_costs,
    )

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
            "acquisition_cost": s.get("acquisition_cost"),
            "disposition_cost": s.get("disposition_cost"),
            "hold_years": s.get("hold_years"),
            "yr1_noi": s["noi_projection"][0] if s.get("noi_projection") else None,
            "yr5_noi": s["noi_projection"][-1] if s.get("noi_projection") else None,
        })
    return rows


def _build_sensitivity(ttm_noi: float, base_price: float,
                       capex: float, nrsf: float,
                       expense_ratio: float = None,
                       custom_scenarios: dict = None,
                       hold_years: int = None,
                       transaction_costs: dict = None) -> dict:
    """
    Build IRR sensitivity table.

    Rows: purchase price ±10% in 2.5% steps
    Cols: exit cap ±100bps in 25bps steps

    The exit cap is swept WITHOUT the entry-cap floor the base scenario
    applies. Coercing here would silently raise every cell below the entry
    cap to it, flattening the left of the grid and destroying the axis the
    table exists to show.
    """
    from config import SCENARIO_DEFAULTS

    # Per-deal scenario overrides drive the grid too. They did not before:
    # the grid always read SCENARIO_DEFAULTS, so its centre cell disagreed
    # with the headline base IRR the moment an analyst edited a scenario.
    # A partial dict falls back rather than raising — a KeyError here would
    # take down the whole run for a cosmetic table.
    defaults = custom_scenarios or SCENARIO_DEFAULTS
    base_params = defaults.get(ScenarioType.BASE) or SCENARIO_DEFAULTS[ScenarioType.BASE]

    # Price steps: -10% to +10% in 2.5% increments
    price_steps = [-0.10, -0.075, -0.05, -0.025, 0.0, 0.025, 0.05, 0.075, 0.10]
    prices = [base_price * (1 + s) for s in price_steps]
    price_labels = [f"{s:+.1%}" for s in price_steps]

    # Exit cap steps: -100bps to +100bps in 25bps increments
    base_exit_cap = base_params["exit_cap"]
    cap_offsets = [-0.0100, -0.0075, -0.0050, -0.0025, 0.0, 0.0025, 0.0050, 0.0075, 0.0100]
    exit_caps = [base_exit_cap + o for o in cap_offsets]
    cap_labels = [f"{c:.2%}" for c in exit_caps]

    grid = [
        [
            project_cash_flows(
                ttm_noi=ttm_noi, price=price, capex=capex,
                params=base_params,
                hold_years=hold_years,
                expense_ratio=expense_ratio,
                costs=transaction_costs,
                coerce_exit_cap=False,
                exit_cap_override=exit_cap,
            )["irr"] if (price + capex) > 0 else None
            for exit_cap in exit_caps
        ]
        for price in prices
    ]

    return {
        "price_labels": price_labels,
        "price_values": prices,
        "cap_labels": cap_labels,
        "cap_values": exit_caps,
        "irr_grid": grid,
        "base_price": base_price,
        "base_exit_cap": base_exit_cap,
    }
