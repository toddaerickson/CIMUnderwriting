"""Item E3a — the levered seam: equity cash flow, AM fee, waterfall.

This is where `model.debt` and `model.waterfall` finally meet the
unlevered projection. Until this module existed, an AST test in each of
their test modules asserted that nothing outside `tests/` imported them,
which was the proof that E1 and E2 moved no published number. Wiring is
now the point, so those guards are retired and this module carries the
responsibility instead.

**It assembles; it does not compute the pieces.** Sizing belongs to
`model.debt`, distribution belongs to `model.waterfall`, and the NOI
series and exit proceeds belong to `analysis.valuation.project_cash_flows`.
Reproducing any of them here is how three copies of one loop drifted the
last time, which is the whole reason item B collapsed them.

**Financing costs are NOT in the unlevered basis.** E1's handoff
prescribed adding a `financing_costs` term to `project_cash_flows` so
`analysis.checks.sources_uses_ties` kept tying Uses to `total_basis`.
The operator reversed that on 2026-08-01: an origination fee in the
unlevered basis makes the primary 10% IRR screen move the moment a deal
names a loan, and an unlevered return charged a financing fee is not an
unlevered return. The TIE moved instead —
`Uses == total_basis + financing_costs` — and `project_cash_flows` is
untouched by this item.
"""

import logging

import config as cfg
from model.waterfall import (AM_FEE_LABELS, WaterfallTerms, assumption_stamp,
                             resolve_waterfall_terms, run_waterfall)

logger = logging.getLogger(__name__)

#: The only AM-fee base implemented. "committed equity" and "asset value"
#: are equally real conventions and roughly 2.4x apart on the plan's
#: oracle-A fixture, so an unrecognised base RAISES rather than falling
#: back — the same discipline `WaterfallTerms` applies to its four
#: convention fields, and for the same reason: silently substituting one
#: convention for another produces a confident wrong LP net IRR.
AM_FEE_BASE_INVESTED_EQUITY = "invested_equity"
AM_FEE_BASE_LABELS = {
    AM_FEE_BASE_INVESTED_EQUITY: "invested equity",
}

#: Sub-cent noise is not a shortfall. Without this, float error on a
#: levered CF that lands exactly on zero calls a capital call of
#: $0.0000001 and starts a pref accrual on it.
CASH_TOLERANCE = 0.005


def noi_series(projection: dict) -> list:
    """The NOI series, under either of the two names it travels under.

    `project_cash_flows` returns it as `noi`;
    `analysis.valuation._run_single_scenario` relabels the same list as
    `noi_projection` for the scenario API. Both are the projection this
    module levers, so both are accepted — and a projection carrying
    NEITHER raises rather than resolving to an empty series. That is not
    defensive padding: reading the wrong key silently sized a loan on a
    Year 1 NOI of 0.0, which produces a debt-yield-bound loan of $0 and a
    levered lens identical to the unlevered one, with nothing anywhere
    saying the input was missing.
    """
    for key in ("noi", "noi_projection"):
        series = projection.get(key)
        if series:
            return list(series)
    raise ValueError(
        "projection carries no NOI series under 'noi' or 'noi_projection' — "
        f"got keys {sorted(projection)[:12]}. Levering a projection whose "
        "NOI cannot be found would size a loan on zero and report it as a "
        "covenant result.")


def _fee_base(base):
    if base not in AM_FEE_BASE_LABELS:
        raise ValueError(
            f"am_fee_base={base!r} is not implemented — supported: "
            f"{sorted(AM_FEE_BASE_LABELS)}. '1% of committed equity', '1% of "
            "invested capital' and '1% of asset value' are all live "
            "conventions with materially different LP net IRRs, so this "
            "raises rather than quietly charging the default.")
    return base


def build_levered_returns(projection: dict, *, sources_uses: dict, debt: dict,
                          waterfall_terms: WaterfallTerms = None,
                          am_fee_pct: float = None,
                          am_fee_base: str = None) -> dict:
    """Levered equity cash flow → the LP waterfall → LP net IRR.

    Per year `t` in `1..N`: `NOI_t - debt_service_t - am_fee_t`, with the
    final year adding `net_exit_proceeds - payoff_balance - exit_fee`.
    `net_exit_proceeds` comes from the projection, so the disposition cost
    is the one the unlevered model already charged rather than a second
    computation of the same percentage.

    **The AM fee is measured on equity outstanding at the START of the
    period**, before that period's own capital call. That is not a
    rounding convention — it is what makes the fee computable. A shortfall
    triggers a call, the call raises invested equity, and an end-of-period
    base would raise the fee, which deepens the shortfall: a loop with no
    fixed point. It also matches `model.waterfall`, which accrues pref on
    the START-of-period balance and does not accrue at period 0.

    **"Invested equity" means cumulative CONTRIBUTED equity, not equity
    net of returns of capital**, and that is the second circularity this
    convention dodges. Reducing the base by interim returns of capital
    would require splitting each distribution into return-of-capital and
    profit — which is what `run_waterfall` does, using distributable cash
    that this fee has ALREADY been deducted from. Fee → distributable
    cash → waterfall → return of capital → fee base is a loop, and
    breaking it would mean iterating to a fixed point for a fee. On a
    single-asset deal the two definitions agree anyway: capital comes
    back at sale, in the final period, after that period's fee is struck.
    They diverge only on a deal that returns capital mid-hold from a
    refinance, which is out of scope here. Stated because it is a real
    convention choice, not an oversight.

    **A negative levered cash flow is never a negative distribution.**
    `run_waterfall` rejects one outright, because netting a shortfall
    against the pref accrual pays the LP a REDUCED preferred return for
    the privilege of the deal losing money. There are two correct
    treatments and this function chooses per period, not once:

    1. **Draw the operating reserve.** Item D funds it at close and puts
       it in `total_basis`, so a shortfall it covers is not a waterfall
       event at all — the period distributes 0 and nothing new is called.
    2. **Call capital** for whatever the reserve did not cover. That is a
       `contributions[t]` entry, which joins the accrual base and starts
       earning pref the FOLLOWING period.

    Args:
        projection: an `analysis.valuation.project_cash_flows` result.
            Supplies the NOI series, `net_exit_proceeds` and the funded
            `reserve` — read from here rather than passed separately, so
            the reserve the DCF put in the basis and the reserve this
            module can draw cannot diverge.
        sources_uses: a `model.returns_model.build_sources_uses` block.
            Supplies `total_equity`, which debt has already displaced.
        debt: a `model.debt.build_debt_schedule` result.
        waterfall_terms: resolved terms. **Resolve them with
            `capital_structure=` at the call site** — called without it,
            `resolve_waterfall_terms` falls back to
            `config.GP_COINVEST_PCT` while Sources & Uses uses the deal's
            own, and a deal edited to 25% prints a stack split 25/75
            beside an LP net IRR computed on 10/90.
        am_fee_pct: annual management fee rate; None → `config.AM_FEE_PCT`
        am_fee_base: what the fee is charged on; None → `config.AM_FEE_BASE`

    Returns a dict, not a dataclass: every other builder in the model
    layer returns one, and the memo writer, Excel writer, Django
    templates and `webapp.services.json_safe` all consume dicts.
    """
    terms = waterfall_terms or resolve_waterfall_terms()
    am_fee_pct = float(cfg.AM_FEE_PCT if am_fee_pct is None else am_fee_pct)
    am_fee_base = _fee_base(cfg.AM_FEE_BASE if am_fee_base is None
                            else am_fee_base)

    noi = noi_series(projection)
    hold_years = int(projection.get("hold_years") or len(noi))
    schedule = list(debt.get("annual_debt_service") or [])
    if len(schedule) != len(noi):
        # Misalignment here would silently pay a different year's debt
        # service against each year's NOI. Loud beats plausible.
        raise ValueError(
            f"debt schedule covers {len(schedule)} years but the projection "
            f"covers {len(noi)} — the two must be built on the same "
            "hold_years or every levered cash flow is off by a year.")

    net_exit = float(projection.get("net_exit_proceeds") or 0.0)
    payoff = float(debt.get("payoff_balance") or 0.0)
    exit_fee = float(debt.get("exit_fee") or 0.0)
    reserve_funded = float(projection.get("reserve") or 0.0)

    total_equity = float(sources_uses["total_equity"])

    reserve_balance = reserve_funded
    equity_outstanding = total_equity
    contributions = [total_equity] + [0.0] * hold_years
    distributions = [0.0] * (hold_years + 1)
    years = []

    for t in range(1, hold_years + 1):
        # START-of-period equity: includes every call made BEFORE this
        # period, excludes this period's own. See the docstring.
        am_fee = equity_outstanding * am_fee_pct
        debt_service = float(schedule[t - 1])
        exit_proceeds = (net_exit - payoff - exit_fee if t == hold_years
                         else 0.0)
        levered_cf = noi[t - 1] - debt_service - am_fee + exit_proceeds

        reserve_drawn = capital_call = 0.0
        if levered_cf < -CASH_TOLERANCE:
            shortfall = -levered_cf
            reserve_drawn = min(reserve_balance, shortfall)
            reserve_balance -= reserve_drawn
            capital_call = shortfall - reserve_drawn
            if capital_call > CASH_TOLERANCE:
                contributions[t] = capital_call
                equity_outstanding += capital_call
            else:
                capital_call = 0.0
            distribution = 0.0
        else:
            distribution = max(0.0, levered_cf)
            distributions[t] = distribution

        years.append({
            "year": t,
            "noi": noi[t - 1],
            "debt_service": debt_service,
            "am_fee": am_fee,
            "exit_proceeds": exit_proceeds,
            "levered_cf": levered_cf,
            "reserve_drawn": reserve_drawn,
            "capital_call": capital_call,
            "distribution": distribution,
            "equity_outstanding": equity_outstanding,
            # Year-by-year coverage on the schedule actually paid. The
            # sizing DSCR is a different number by design — see
            # `build_debt_schedule`.
            "dscr": (noi[t - 1] / debt_service) if debt_service > 0 else None,
        })

    waterfall = run_waterfall(contributions, distributions, terms)

    capital_calls_total = sum(contributions[1:])
    if capital_calls_total > CASH_TOLERANCE:
        logger.warning(
            "levered cash flow was negative in %d of %d years: $%.2f drawn "
            "from the operating reserve and $%.2f CALLED from investors "
            "after close. The called capital accrues preferred return from "
            "the following period.",
            sum(1 for r in years if r["capital_call"] > 0), hold_years,
            reserve_funded - reserve_balance, capital_calls_total)

    return {
        "years": years,
        "contributions": contributions,
        "distributions": distributions,
        "am_fee_pct": am_fee_pct,
        "am_fee_base": am_fee_base,
        "am_fee_total": sum(r["am_fee"] for r in years),
        "reserve_funded": reserve_funded,
        "reserve_drawn_total": reserve_funded - reserve_balance,
        "reserve_remaining": reserve_balance,
        "capital_calls_total": capital_calls_total,
        # A deal that called capital after close has to say so where a
        # consumer cannot miss it: it is a materially different LP story
        # from one that merely distributed less.
        "called_capital_after_close": capital_calls_total > CASH_TOLERANCE,
        "total_equity": total_equity,
        # `terms` is already a plain dict inside the debt payload (see
        # `build_debt_schedule`); carrying the whole block lets the
        # results page, memo and Excel read one object.
        "debt": debt,
        "waterfall": waterfall,
        "assumption_stamp": _stamp(terms, am_fee_pct, am_fee_base),
        # Lifted for consumers that want only the headline. `run_waterfall`
        # returns None rather than NaN on a non-converging IRR, because
        # json.dumps(nan) is invalid JSON and these results are persisted
        # to Postgres JSONB.
        "lp_net_irr": waterfall["lp"]["irr"],
        "lp_moic": waterfall["lp"]["moic"],
        "gp_promote": waterfall["gp"]["promote"],
    }


def _stamp(terms: WaterfallTerms, am_fee_pct: float, am_fee_base: str) -> list:
    """E2's assumption stamp, with the AM-fee row completed.

    E2 deliberately left that row carrying a treatment but no rate and no
    base, and said so in its own label: it does not charge the fee, and
    inventing a config key it never read would be a constant that goes
    stale. E3a charges it, so the row must name what it charged.
    Otherwise the stamp reads complete beside an LP *net* IRR while
    omitting the one input that makes it "net".
    """
    rows = []
    for row in assumption_stamp(terms):
        if row["key"] == "am_fee_treatment":
            row = {**row,
                   "rate": am_fee_pct,
                   "base": am_fee_base,
                   "label": (f"{AM_FEE_LABELS[terms.am_fee_treatment]} — "
                             f"{am_fee_pct:.2%} of "
                             f"{AM_FEE_BASE_LABELS[am_fee_base]}, measured "
                             "at the start of each period")}
        rows.append(row)
    return rows
