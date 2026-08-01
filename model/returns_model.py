"""
Unlevered DCF model — Bear / Base / Bull over a variable hold period.

This module wraps the valuation scenario engine and provides
structured output for the Excel writer. The projection itself lives in
`analysis.valuation.project_cash_flows` — the sensitivity grid below used
to carry its own copy of that loop.

It also owns the capital stack: `build_sources_uses` (what the deal costs
and where the money comes from) and `resolve_capital_amount` (an amount
entered on some basis → dollars). Both are pure and Django-free, so the
assumptions form, the engine, the memo and the Excel writer read one
definition.
"""

import logging

import config as cfg
from analysis.valuation import project_cash_flows, run_scenarios
from registry import ScenarioType

logger = logging.getLogger("cim_analyst")

# ── Capital amounts entered on a basis ──────────────────────────────
# CapEx accepts all four. The operating reserve accepts dollars and
# $/NRSF only: a reserve is months of operating expense, not a share of
# value, so a percentage-of-price reserve is a category error.

BASIS_AMOUNT = "amount"
BASIS_PER_SF = "per_sf"
BASIS_PER_UNIT = "per_unit"
BASIS_PCT_PRICE = "pct_price"

#: Kept short on purpose — these render inside a selector that shares a
#: table cell with the number it qualifies, so a long label would push the
#: input it belongs to off the row.
BASIS_LABELS = {
    BASIS_AMOUNT: "$ total",
    BASIS_PER_SF: "$/NRSF",
    BASIS_PER_UNIT: "$/unit",
    BASIS_PCT_PRICE: "% of price",
}
CAPEX_BASES = (BASIS_AMOUNT, BASIS_PER_SF, BASIS_PER_UNIT, BASIS_PCT_PRICE)
RESERVE_BASES = (BASIS_AMOUNT, BASIS_PER_SF)

#: basis → the driver keyword it multiplies. `pct_price` is a decimal
#: fraction of price, so it multiplies price the same way the others
#: multiply a physical count.
_BASIS_DRIVER = {BASIS_PER_SF: "nrsf", BASIS_PER_UNIT: "units",
                 BASIS_PCT_PRICE: "price"}


def resolve_capital_structure(overrides: dict = None) -> dict:
    """Partial capital-structure override → the full resolved set.

    Same contract as `analysis.valuation.resolve_transaction_costs`:
    omitting a key means "use the default", never "zero". An unrecognised
    basis is logged and replaced with `amount` rather than raising — a
    stored override written by a future version must not take down a run
    on an older one.

    Config is read at CALL time, and these are plain scalars rather than a
    dict in `webapp.services._PATCHED_DICTS`, so there is no live dict for
    a concurrent run to have mutated underneath us (config.py explains
    why at length).
    """
    resolved = {
        "capex_basis": cfg.DEFAULT_CAPEX_BASIS,
        "operating_reserve": cfg.DEFAULT_OPERATING_RESERVE,
        "operating_reserve_basis": cfg.DEFAULT_OPERATING_RESERVE_BASIS,
        "gp_coinvest_pct": cfg.GP_COINVEST_PCT,
    }
    for key in resolved:
        value = (overrides or {}).get(key)
        if value not in (None, ""):
            resolved[key] = value
    resolved["operating_reserve"] = float(resolved["operating_reserve"] or 0.0)
    resolved["gp_coinvest_pct"] = float(resolved["gp_coinvest_pct"] or 0.0)
    for key, allowed in (("capex_basis", CAPEX_BASES),
                         ("operating_reserve_basis", RESERVE_BASES)):
        if resolved[key] not in allowed:
            logger.warning("%s=%r is not one of %s — using %s",
                           key, resolved[key], allowed, BASIS_AMOUNT)
            resolved[key] = BASIS_AMOUNT
    return resolved


def resolve_capital_amount(value, basis=None, *, nrsf=None, units=None,
                           price=None) -> float:
    """An amount entered on `basis` → dollars.

    Units are canonical, matching the rest of the model layer: dollars for
    `amount` / `per_sf` / `per_unit`, and a DECIMAL fraction for
    `pct_price` (0.02, not 2). `webapp.forms` owns the whole-number-percent
    boundary, as it does for every other percentage on the page.

    `amount` is the default and reproduces the historical behavior of
    every caller exactly, so a deal that never touches a basis selector
    sees no change.

    A basis whose driver is missing or zero returns **0.0 with a warning**,
    not the raw number: `$0.50/SF` on a deal with no stated NRSF is not
    fifty cents, and inventing a figure of the wrong magnitude is worse
    than reporting nothing. The assumptions form rejects that combination
    before it can reach here (webapp.forms.AssumptionsForm.clean), so this
    is the belt to that suspenders — the CLI and stored overrides do not
    pass through the form.
    """
    if value in (None, ""):
        return 0.0
    amount = float(value)
    basis = basis or BASIS_AMOUNT
    if basis == BASIS_AMOUNT:
        return amount
    driver_name = _BASIS_DRIVER.get(basis)
    if driver_name is None:
        logger.warning("unknown capital basis %r — reading %s as dollars",
                       basis, amount)
        return amount
    driver = {"nrsf": nrsf, "units": units, "price": price}[driver_name]
    if not driver:
        logger.warning("capital basis %r needs %s, which is missing or zero "
                       "— contributing $0 rather than a fabricated amount",
                       basis, driver_name)
        return 0.0
    return amount * float(driver)


# ── Sources & Uses ──────────────────────────────────────────────────

#: Uses must tie to the DCF basis to the cent. Anything looser would let a
#: real disagreement hide inside the tolerance; anything tighter trips on
#: float representation of ordinary dollar arithmetic.
SOURCES_USES_TOLERANCE = 0.01


def build_sources_uses(price: float, capex: float = 0.0, *,
                       acquisition_cost: float = 0.0,
                       reserve: float = 0.0,
                       financing_costs: float = 0.0,
                       senior_debt: float = 0.0,
                       gp_coinvest_pct: float = None) -> dict:
    """What the deal costs and where the money comes from.

    Uses: purchase price · acquisition closing costs · upfront CapEx ·
    operating reserve · financing costs. Sources: senior debt · GP
    co-invest · LP equity.

    **Equity is the plug**: `total_equity = total_uses − senior_debt`,
    then split by `gp_coinvest_pct`. That ordering is what makes the block
    correct the day item E1 sizes a loan — debt DISPLACES equity, it does
    not add to uses. Financing costs and senior debt are 0 until E1 and
    are carried as real parameters, not omitted, so E1 wires values into
    an existing schema instead of reshaping this one.

    `total_uses` equals the DCF's `total_basis` by construction while
    financing costs are zero; `analysis.checks.sources_uses_ties` asserts
    it on every run so E1 cannot break the tie silently.
    """
    if gp_coinvest_pct is None:
        # Read at CALL time, never bound at import: config scalars are
        # rebindable by tests and by a future settings path, and a
        # module-level default would freeze the value at first import.
        gp_coinvest_pct = cfg.GP_COINVEST_PCT
    gp_coinvest_pct = float(gp_coinvest_pct or 0.0)

    uses = [
        {"key": "price", "label": "Purchase Price", "amount": float(price or 0.0)},
        {"key": "acquisition_cost", "label": "Acquisition Closing Costs",
         "amount": float(acquisition_cost or 0.0)},
        {"key": "capex", "label": "Upfront CapEx", "amount": float(capex or 0.0)},
        {"key": "reserve", "label": "Operating Reserve",
         "amount": float(reserve or 0.0)},
        {"key": "financing_costs", "label": "Financing Costs",
         "amount": float(financing_costs or 0.0)},
    ]
    total_uses = sum(u["amount"] for u in uses)

    senior_debt = float(senior_debt or 0.0)
    total_equity = total_uses - senior_debt
    gp_equity = total_equity * gp_coinvest_pct
    lp_equity = total_equity - gp_equity

    sources = [
        {"key": "senior_debt", "label": "Senior Debt", "amount": senior_debt},
        {"key": "gp_equity",
         "label": f"GP Co-Invest ({gp_coinvest_pct:.0%} of equity)",
         "amount": gp_equity},
        {"key": "lp_equity", "label": "LP Equity", "amount": lp_equity},
    ]
    total_sources = sum(s["amount"] for s in sources)

    return {
        "uses": uses,
        "sources": sources,
        "total_uses": total_uses,
        "total_sources": total_sources,
        "total_equity": total_equity,
        "gp_equity": gp_equity,
        "lp_equity": lp_equity,
        "senior_debt": senior_debt,
        "gp_coinvest_pct": gp_coinvest_pct,
        "ltv": (senior_debt / total_uses) if total_uses else None,
        "balanced": abs(total_uses - total_sources) <= SOURCES_USES_TOLERANCE,
        "delta": total_uses - total_sources,
    }


def build_returns_model(adjusted_ttm_noi: float, asking_price: float,
                        nrsf: float, capex: float = 0,
                        custom_scenarios: dict = None,
                        expense_ratio: float = None,
                        hold_years: int = None,
                        transaction_costs: dict = None,
                        reserve: float = 0.0,
                        gp_coinvest_pct: float = None) -> dict:
    """
    Build complete returns model for all three scenarios.

    Returns:
        - scenarios: dict with bear/base/bull full results
        - summary_table: condensed comparison table
        - sensitivity: IRR sensitivity to price and exit cap
        - sources_uses: capital stack, tied to the scenarios' total_basis

    `sources_uses` is built HERE rather than in the engine so its
    acquisition-cost line is the figure the projection actually used, not
    a second computation of the same percentage.
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
        reserve=reserve,
    )

    summary = _build_summary_table(scenarios)
    sensitivity = _build_sensitivity(
        adjusted_ttm_noi, asking_price, capex, nrsf,
        expense_ratio=expense_ratio,
        custom_scenarios=custom_scenarios,
        hold_years=hold_years,
        transaction_costs=transaction_costs,
        reserve=reserve,
    )

    # Every scenario shares one price, CapEx, reserve and cost set, so
    # they all carry the same acquisition cost and the same total_basis —
    # one capital stack describes the deal, not three.
    any_scenario = next((s for s in scenarios.values() if isinstance(s, dict)),
                        {})
    sources_uses = build_sources_uses(
        price=asking_price,
        capex=capex,
        acquisition_cost=any_scenario.get("acquisition_cost") or 0.0,
        reserve=reserve,
        gp_coinvest_pct=gp_coinvest_pct,
    )

    return {
        "scenarios": scenarios,
        "summary_table": summary,
        "sensitivity": sensitivity,
        "sources_uses": sources_uses,
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
                       transaction_costs: dict = None,
                       reserve: float = 0.0) -> dict:
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
                reserve=reserve,
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
