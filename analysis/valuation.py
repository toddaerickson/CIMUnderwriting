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
import config as cfg
from config import (DEFAULT_HOLD_YEARS, EXIT_CAP_DRIFT_BPS,
                    EXIT_CAP_SCENARIO_SPREAD_BPS, HOLD_YEARS_RANGE,
                    MARKET_CAP_AS_OF, MARKET_CAP_RATES,
                    MARKET_CAP_UNKNOWN_AGE_BAND, SCENARIO_DEFAULTS,
                    TRANSACTION_COSTS)
from registry import (DEFAULT_ASSET_TYPE, ScenarioType, age_band,
                      clamp_expense_ratio)

logger = logging.getLogger("cim_analyst")

# The scenarios whose exit cap is floored at the entry cap. Bull is
# excluded on purpose — it is allowed to underwrite cap compression.
COERCED_SCENARIOS = (ScenarioType.BASE, ScenarioType.BEAR)


def resolve_transaction_costs(costs: dict = None, base: dict = None) -> dict:
    """Merge a partial cost override onto the config defaults.

    Omitting a key means "use the default", never "zero" — a silent zero
    here is exactly the overstated-IRR defect this module was changed to
    fix. Pass explicit zeros to model a genuinely cost-free round trip.

    `base` overrides where the defaults are read from. It exists because
    TRANSACTION_COSTS is in webapp.services._PATCHED_DICTS: the live dict
    is mutated in place for the duration of one deal's run, so any caller
    resolving OUTSIDE that run's lock must pass the pristine snapshot
    instead (webapp.services.resolve_run_transaction_costs). Callers
    reached from inside run_analysis are already serialized and can use
    the default.
    """
    resolved = dict(TRANSACTION_COSTS if base is None else base)
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


def resolve_exit_noi(trailing: float, forward: float) -> float:
    """Pick the NOI the exit capitalizes, per config.EXIT_NOI_CONVENTION.

    Both candidates are computed by the CALLER's own engine — the static
    DCF's forward step and the VA model's forward year are different
    arithmetic on purpose — so this helper only selects. Sharing the
    selection is what keeps the two engines agreeing on what the
    convention MEANS (decision 5, settled 2026-08-10: one config name
    governs every exit capitalization in a run) while each keeps its own
    projection.

    Reads `cfg.EXIT_NOI_CONVENTION` at CALL time via the module
    attribute — a scalar reached by `from config import ...` freezes at
    import, the trap SOLVER_TARGET_IRR already documented. An unknown
    value raises rather than defaulting: a misspelled convention
    silently priced as trailing would be a wrong exit on every deal.
    """
    convention = cfg.EXIT_NOI_CONVENTION
    if convention == "trailing":
        return trailing
    if convention == "forward":
        return forward
    raise ValueError(
        f"config.EXIT_NOI_CONVENTION must be 'trailing' or 'forward', "
        f"got {convention!r}")


def resolve_market_cap(asset_type: str = None, year_built=None, *,
                       market_cap: float = None, as_of=None,
                       base: dict = None,
                       asset_class_known: bool = None) -> dict:
    """The market cap for this asset's class and age band.

    An explicit `market_cap` (the analyst's, off the assumptions form)
    always wins; the table only supplies the starting point. Returns the
    band and the source alongside the rate so the caller can say WHY it
    landed where it did — this number drives every exit value, so a bare
    float would not be traceable.

    `base` is the pristine MARKET_CAP_RATES snapshot, for the same reason
    resolve_transaction_costs takes one: the table is in
    webapp.services._PATCHED_DICTS and the live dict carries another
    deal's values for as long as that deal holds the analysis lock.

    `asset_class_known` is the CALLER's answer to "did the CIM evidence
    this class, or is it the default reached by absence?" — the caller's,
    because the fallback happens up in `registry.classify_asset_type`,
    before the class arrives here as a bare string. It is reported
    alongside `age_band_known`, whose question this resolver CAN answer
    itself, so both halves of the table lookup that prices the exit carry
    provenance. `None` means the caller did not say; absence of an answer
    is not an answer, so nothing downstream may read it as "evidenced"
    (item T Category 4).
    """
    table = MARKET_CAP_RATES if base is None else base
    band = age_band(year_built, as_of)
    resolved_band = band or MARKET_CAP_UNKNOWN_AGE_BAND
    asset_class = asset_type if asset_type in table else DEFAULT_ASSET_TYPE
    table_rate = table.get(asset_class, {}).get(resolved_band)

    if market_cap is not None:
        return {"market_cap": float(market_cap), "source": "analyst",
                "asset_class": asset_class, "age_band": resolved_band,
                "age_band_known": band is not None,
                "asset_class_known": asset_class_known,
                "table_market_cap": table_rate, "as_of": MARKET_CAP_AS_OF}
    if table_rate is None:
        # Neither an analyst figure nor a table cell. Refuse rather than
        # invent one: every exit value in the run depends on it.
        raise ValueError(
            f"no market cap for class {asset_class!r} band {resolved_band!r}; "
            "enter one on the assumptions form")
    return {"market_cap": float(table_rate), "source": "table",
            "asset_class": asset_class, "age_band": resolved_band,
            "age_band_known": band is not None,
            "asset_class_known": asset_class_known,
            "table_market_cap": table_rate, "as_of": MARKET_CAP_AS_OF}


def describe_market_cap(detail: dict) -> str:
    """Where an anchor came from, in words — "table as of 2026-Q3", or
    "analyst-entered, overriding the 6.250% table rate as of 2026-Q3".

    One phrasing, used by the check register, the memo and the Excel
    derivation block. `as_of` dates the TABLE, and it is returned on both
    branches because `table_market_cap` is reported on both; printing it
    unconditionally beside the applied rate claimed a table vintage for an
    analyst's number that had no table basis (review finding, PR #31).
    Three copies of this sentence is how that gets fixed in one place and
    stays wrong in the other two.
    """
    d = detail or {}
    as_of = f" as of {d['as_of']}" if d.get("as_of") else ""
    if d.get("source") == "table":
        return f"table{as_of}"
    source = d.get("source") or "unknown"
    table_rate = d.get("table_market_cap")
    if table_rate is None:
        return f"{source}-entered"
    return (f"{source}-entered, overriding the {float(table_rate):.3%} "
            f"table rate{as_of}")


def resolve_exit_cap(market_cap: float, scenario, hold_years: int = None, *,
                     spread_bps: float = None, drift_bps: float = None) -> dict:
    """Exit cap for one scenario, with its components.

        exit_cap = market_cap + scenario_spread + drift_bps * hold_years

    Returns the parts, not just the total: this replaced a per-scenario
    constant the analyst could read straight off the settings page, so a
    derived number that cannot be decomposed would be a step backwards on
    auditability. `analysis.checks.market_exit_cap` renders these.

    The drift is per year of HOLD — the asset ages while owned. Age at
    acquisition is already priced by the band in `resolve_market_cap`.
    """
    years = resolve_hold_years(hold_years)
    spread = (EXIT_CAP_SCENARIO_SPREAD_BPS.get(scenario, 0.0)
              if spread_bps is None else float(spread_bps))
    drift = (EXIT_CAP_DRIFT_BPS.get(scenario, 0.0)
             if drift_bps is None else float(drift_bps))
    drift_total = drift * years
    exit_cap = float(market_cap) + (spread + drift_total) / 10_000.0
    return {
        "exit_cap": exit_cap,
        "market_cap": float(market_cap),
        "scenario_spread_bps": spread,
        "drift_bps_per_year": drift,
        "drift_total_bps": drift_total,
        "hold_years": years,
    }


def project_cash_flows(ttm_noi: float, price: float, capex: float,
                       params: dict, *,
                       hold_years: int = None,
                       expense_ratio: float = None,
                       costs: dict = None,
                       reserve: float = 0.0,
                       coerce_exit_cap: bool = True,
                       exit_cap: float = None,
                       exit_cap_detail: dict = None,
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
        reserve: upfront operating / working-capital reserve funded at
            close (item D). It is part of the equity check, so it enters
            `total_basis` — Sources & Uses could not otherwise tie to the
            DCF. It is NOT released at exit: crediting the balance back
            assumes the reserve was never needed, which is the assumption
            a reserve exists to hedge. Default 0, so nothing published
            moves until a deal names one.
        coerce_exit_cap: floor the exit cap at the entry cap. The scenario
            engine passes True for base/bear only and the solver matches
            it. The sensitivity grid passes False — its whole purpose is
            an exit-cap axis, and coercing collapses every cell below the
            entry cap onto one value.
        exit_cap: the resolved exit cap for this scenario, from
            `resolve_exit_cap`. Required — it used to be read off
            `params["exit_cap"]`, a free-standing constant that priced a
            2003 drive-up facility and a 2022 climate-controlled build
            identically. Callers resolve it once and pass it down.
        exit_cap_override: use this instead of `exit_cap` (the sensitivity
            grid sweeps it)
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

    def rev_growth_for(yr: int) -> float:
        """The band rate for year `yr` — ONE rule, used by the hold-year
        loop and the forward exit step below, so the forward year cannot
        drift onto its own banding."""
        return rev_growth_1_3 if yr <= 3 else rev_growth_4_5

    for yr in range(2, hold_years + 1):
        new_rev = rev_series[-1] * (1 + rev_growth_for(yr))
        new_exp = exp_series[-1] * (1 + exp_growth)
        rev_series.append(new_rev)
        exp_series.append(new_exp)
        noi_series.append(new_rev - new_exp)

    if exit_cap_override is not None:
        exit_cap = exit_cap_override
    elif exit_cap is None:
        # No silent fallback to a constant: the whole point of the change
        # is that an exit cap without an asset behind it is not a number
        # anyone should publish.
        raise ValueError("project_cash_flows needs exit_cap (see "
                         "resolve_exit_cap) or exit_cap_override")
    requested_exit_cap = exit_cap
    entry_cap = ttm_noi / price if price > 0 else 0

    # Exit NOI per config.EXIT_NOI_CONVENTION (decision 5, settled
    # 2026-08-10). Forward is one more step of the SAME two series —
    # year N+1 revenue at the band rate for year N+1, minus year N+1
    # expenses at exp_growth. NOT noi * (1 + g): the series grow at
    # different rates and NOI is their difference, so a single-rate step
    # overstates forward NOI whenever expense growth outruns revenue
    # growth (the bear case). Computed unconditionally so the branch on
    # the convention lives in ONE place, resolve_exit_noi.
    forward_noi = (rev_series[-1] * (1 + rev_growth_for(hold_years + 1))
                   - exp_series[-1] * (1 + exp_growth))
    exit_noi = resolve_exit_noi(noi_series[-1], forward_noi)

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
    reserve = float(reserve or 0.0)
    total_basis = price + capex + acquisition_cost + reserve

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
        # The NOI that was actually capitalized, and under which
        # convention. Both are returned because under "forward" the
        # capitalized figure is NOT in `noi` — it is one step past the
        # hold — and a surface printing exit value beside the last NOI
        # row would show a pair that does not tie to the printed exit
        # cap, with the true number recoverable only by dividing back.
        "exit_noi": exit_noi,
        "exit_noi_convention": cfg.EXIT_NOI_CONVENTION,
        "requested_exit_cap": requested_exit_cap,
        "exit_cap": exit_cap,
        "exit_cap_coerced": exit_cap_coerced,
        "exit_cap_detail": exit_cap_detail,
        "exit_value": exit_value,
        "disposition_cost": disposition_cost,
        "net_exit_proceeds": net_exit_proceeds,
        "acquisition_cost": acquisition_cost,
        "transaction_costs": costs,
        "reserve": reserve,
        "capex": capex,
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
                  transaction_costs: dict = None,
                  reserve: float = 0.0,
                  market_cap: dict = None) -> dict:
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
        reserve: upfront operating reserve, in dollars (see
                 project_cash_flows) — one figure for the whole deal, so
                 every scenario shares one total_basis

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
    mc = market_cap or resolve_market_cap()

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
            reserve=reserve,
            market_cap=mc,
        )
        for name, params in scenarios.items()
    }


def _run_single_scenario(scenario_name: str, ttm_noi: float,
                         asking_price: float, capex: float, nrsf: float,
                         params: dict,
                         expense_ratio: float = None,
                         hold_years: int = None,
                         transaction_costs: dict = None,
                         reserve: float = 0.0,
                         market_cap: dict = None) -> dict:
    """Label and reshape one canonical projection for the scenario API."""
    mc = market_cap or resolve_market_cap()
    cap = resolve_exit_cap(mc["market_cap"], scenario_name, hold_years)
    p = project_cash_flows(
        ttm_noi=ttm_noi,
        price=asking_price,
        capex=capex,
        params=params,
        hold_years=hold_years,
        expense_ratio=expense_ratio,
        costs=transaction_costs,
        reserve=reserve,
        coerce_exit_cap=scenario_name in COERCED_SCENARIOS,
        exit_cap=cap["exit_cap"],
        exit_cap_detail={**mc, **cap},
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
        "exit_cap_detail": p["exit_cap_detail"],
        "entry_cap": p["entry_cap"],
        "exit_noi": p["exit_noi"],
        "exit_noi_convention": p["exit_noi_convention"],
        "exit_value": p["exit_value"],
        "disposition_cost": p["disposition_cost"],
        "net_exit_proceeds": p["net_exit_proceeds"],
        "acquisition_cost": p["acquisition_cost"],
        "transaction_costs": p["transaction_costs"],
        "reserve": p["reserve"],
        "cash_flows": p["cash_flows"],
        "irr": p["irr"],
        "moic": p["moic"],
        "yield_on_cost": p["yield_on_cost"],
        "total_basis": p["total_basis"],
        "asking_price": asking_price,
        "capex": capex,
        "noi_per_sf": [n / nrsf for n in p["noi"]] if nrsf else [],
    }
