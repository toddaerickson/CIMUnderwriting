"""
Bisection solver — maximum purchase price for a target return.

Method: Bisection search on purchase price.
Price ↑ → IRR ↓ (monotonic), so bisection converges reliably.
Convergence to 0.1% IRR precision in ~20 iterations.

Three solvers, all bisecting the same axis:

* `solve_max_price` — target UNLEVERED IRR (default 10%). The primary
  screen's number, and it stays financing-free.
* `solve_max_price_value_add` — the same, through the VA monthly engine.
* `solve_max_price_levered` — target LP NET IRR (default 15%), item E4.
  A SECOND LENS beside the unlevered answer, never a replacement
  (operator's call, 2026-08-01): the unlevered max offer is what the
  primary gate is measured against, and deleting it to show only a
  levered figure would leave the screen with no price anchor of its own.

**On monotonicity, which item E4 was told to confirm before trusting the
bisection.** The levered chain is price → loan → equity → waterfall, and
each link is signed:

* NOI is grown from TTM NOI and does not depend on price at all.
* The loan is `min(LTV cap, DSCR cap, debt-yield cap)`. Only the LTV cap
  moves with price, and it moves UP, so the loan is non-decreasing in
  price and flat once a coverage test binds.
* Equity is the plug: `uses − loan`, and
  `d(equity)/d(price) ≥ 1 − max_ltv × (1 − orig_fee_pct)`. At the
  config maximum (65% LTV, 1% origination) that floor is ≈ 0.36 > 0, so
  equity STRICTLY increases with price. Leverage never outruns the price
  it is levering.
* Every distribution weakly falls: interim years pay
  `NOI − debt service − AM fee` with both deductions rising in price, and
  the exit year pays `net exit − payoff − exit fee` with the payoff
  rising.

More equity in, weakly less cash out ⇒ LP net IRR strictly decreases in
price. Bisection is valid.

**One term works the other way, and it is worth writing down because it
looks fatal and is not.** When `coerce_exit_cap` is in force (base and
bear), the exit cap is floored at the ENTRY cap, `ttm_noi / price`, which
FALLS as price rises. So inside that region a higher price buys a lower
exit cap and a HIGHER exit value — rising by `NOI_exit / ttm_noi` per
dollar of price (`NOI_exit` per `config.EXIT_NOI_CONVENTION`; under the
trailing default that is the terminal year's own NOI, ≈ 1.14 on a 5-year
hold at 3% growth and ≈ 2.84 at 20%; under forward it is one growth step
larger, still price-independent, so nothing below changes shape). That
is far more than the ≈ 1.01 the basis rises by, which reads like a
guaranteed inversion.

It is not, because the two effects are not measured at the same date.
The extra basis is paid at close; the extra exit value arrives at the end
of the hold and is discounted at the deal's own IRR. And the region where
the floor binds hardest is the CHEAP end, which is exactly where the IRR
is highest and the discount harshest. The two move together. Measured on
this repo's builders — 300k TTM NOI, price swept $1.5M–$12M, revenue
growth swept 3%/8%/12%/15%/20%, base scenario with coercion on — the LP
net IRR is strictly decreasing at every step and there are ZERO
inversions. At 20% growth the exit value climbs from $4.3M to $10.8M
across the coerced region while LP net IRR falls monotonically from
76.1% to 48.2%.

That is an empirical result over the range this repo underwrites, NOT a
proof for all inputs. So the solver keeps the receipts: every price it
evaluates is retained, and `_monotonicity_warning` scans the samples for
an inversion afterwards. Two separate signals come back, and the
difference between them matters:

* `coerced_region` — the exit-cap floor bound somewhere in the search.
  Ordinary and common (it binds on the default fixture), so it is DATA,
  not a caveat. Nothing should render a warning off it, or every deal
  gets one.
* `monotonicity_warning` — an inversion was actually observed. None on
  every case measured above. This is the one that means the answer is
  suspect, and it is what the results page, memo and workbook caveat on.

Reporting is not fixing: a genuine fix is a bracketing sweep, and it
would belong to BOTH solvers rather than this one. But a labelled suspect
number beats a confident wrong one, and an unlabelled clean number here
is the honest reading of the evidence.
"""

import logging

import config as cfg
from analysis.valuation import (COERCED_SCENARIOS, project_cash_flows,
                                resolve_exit_cap, resolve_market_cap,
                                resolve_transaction_costs)
from config import (SCENARIO_DEFAULTS, SOLVER_TOLERANCE,
                    SOLVER_MAX_ITERATIONS, VALUE_ADD_SCENARIOS)
from registry import ScenarioType

logger = logging.getLogger("cim_analyst")


def resolve_target_irr(target_irr=None) -> float:
    """THE resolver for the UNLEVERED solvers' target IRR.

    Both solvers took `target_irr: float = SOLVER_TARGET_IRR` as a default
    ARGUMENT, which Python evaluates once at import — so the value froze at
    whatever config held when `model.solver` was first imported, and a
    settings override could only ever reach it by being threaded in as a
    parameter. That worked (the web app does thread it), but it left the
    CLI and every direct caller reading a value config could no longer
    change, and it is the pattern `solve_max_price_levered` already
    documents as the one to avoid.

    `is None`, never truthiness: a 0.0 target is a coherent question ("the
    price at which this merely breaks even") and `or` would answer a
    different one.
    """
    if target_irr is None or target_irr == "":
        return cfg.SOLVER_TARGET_IRR
    return float(target_irr)

def solver_price_bracket(ttm_noi: float) -> tuple[float, float]:
    """THE price bracket every solver in this module bisects between.

    All three searched the same axis and each carried its own copy of the
    bracket, and two of the copies disagreed: static and levered stopped
    at a 3% implied entry cap, value-add went to 2%. `config.SOLVER_BOUNDS`
    now states it once, at the wider 2% — the argument for which end wins,
    and the measurement behind it, are recorded there.

    Read through `cfg.` at call time, never bound at import: a bracket
    frozen at first import of this module is exactly the defect
    `resolve_target_irr` above exists to undo.

    Returns dollars. Below a positive TTM NOI an implied cap rate means
    nothing, so the fallback is a raw dollar window rather than a
    division that would return zero or flip the bracket's ends.
    """
    bounds = cfg.SOLVER_BOUNDS
    if ttm_noi and ttm_noi > 0:
        return (ttm_noi / bounds["cheap_entry_cap"],
                ttm_noi / bounds["dear_entry_cap"])
    return (float(bounds["zero_noi_low_price"]),
            float(bounds["zero_noi_high_price"]))


def _warn_if_truncated(solver: str, price, converged: bool, achieved,
                       low: float, high: float, target: float) -> None:
    """Say so when the answer is the BRACKET rather than the deal.

    Bisection cannot report a root outside the window it was given: if the
    price that hits the target sits above `high`, every iteration pushes
    `low` up and the loop ends holding `high` — a number, in exactly the
    shape of an answer, at an IRR nowhere near the target.

    Measured on the `value_add` fixture at 30% of stabilized adjusted NOI
    (see `config.SOLVER_BOUNDS`): a 3% dear cap returned its own ceiling
    to the dollar, at 13.48% against a 10% target, $799,773 light.
    Widening to 2% fixed that case and only that case — a fixed bracket
    still binds somewhere, so the condition is reported rather than
    assumed away.

    Reporting, not fixing: the real fix is a bracketing sweep, and it
    belongs to all three solvers at once. `converged` has always been in
    the returned dict and no surface reads it, which is why silence here
    was indistinguishable from an answer.

    `low` and `high` must be the OPENING bracket. The loops narrow their
    own copies to a hair's width, so the converged pair sits against the
    ceiling on every run and would make this fire on all of them.
    """
    if converged or price is None or achieved is None:
        return
    span = high - low
    at_edge = span > 0 and (abs(price - high) <= span * 1e-9
                            or abs(price - low) <= span * 1e-9)
    if not at_edge:
        return
    edge = "ceiling" if abs(price - high) < abs(price - low) else "floor"
    # Pre-formatted: %-style logging has no thousands separator, and an
    # unpunctuated eight-digit price in a warning is unreadable.
    logger.warning(
        "%s: the answer is the search %s, not the deal — $%s achieves %s "
        "against a %s target, so the price that hits the target lies "
        "outside the bracket $%s-$%s. Treat this max offer as a bound: "
        "widen config.SOLVER_BOUNDS, or read it as 'at least this'.",
        solver, edge, f"{price:,.0f}", f"{achieved:.4%}", f"{target:.4%}",
        f"{low:,.0f}", f"{high:,.0f}")


#: An IRR rise this small across a price step is float noise on a
#: converged bracket, not the coerced-region inversion. The real pocket
#: moves the exit value by ~16% of price, which is orders of magnitude
#: above this.
MONOTONICITY_EPSILON = 1e-6


def solve_max_price(adjusted_ttm_noi: float,
                    capex: float = 0,
                    target_irr: float = None,
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
    target_irr = resolve_target_irr(target_irr)
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

    # Low: very cheap → high IRR. High: very expensive → low/negative IRR.
    # The loop MUTATES low and high as it narrows, so the opening bracket
    # is kept separately — `_warn_if_truncated` asks whether the answer is
    # the ORIGINAL ceiling, and the narrowed pair always sits against it.
    bracket = solver_price_bracket(adjusted_ttm_noi)
    low, high = bracket

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

    _warn_if_truncated("unlevered max-offer solver", best_price, converged,
                       best_irr, *bracket, target_irr)

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
                               target_irr: float = None,
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

    target_irr = resolve_target_irr(target_irr)
    params = VALUE_ADD_SCENARIOS.get(scenario, VALUE_ADD_SCENARIOS[ScenarioType.BASE])
    costs = resolve_transaction_costs(transaction_costs)
    reserve = float(reserve or 0.0)

    def capex_at(price: float) -> float:
        return (price * capex_pct_of_price if capex_pct_of_price
                else (capex or 0.0))

    # Estimate NOI for bounds — use adjusted or CIM. NOT `or 100_000`
    # (item T Category 4): that literal did not widen the search, it
    # RELOCATED it, to the window a $100k-NOI property would be priced
    # in — $500k to $5M regardless of the asset. `solver_price_bracket`
    # already handles a missing NOI by falling through to its declared
    # dollar window, which is the honest answer to "price bracket for a
    # deal whose NOI we do not know", and the other two solvers carry no
    # such literal.
    adj_noi = financial_analysis.get("adjusted_ttm_noi", {}).get("analyst_adjusted_noi")
    ttm_noi = adj_noi or cim_data.ttm_noi

    bracket = solver_price_bracket(ttm_noi)      # kept — the loop mutates
    low, high = bracket

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
                                      reserve=reserve, market_cap=market_cap,
                                      scenario=scenario)

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

    _warn_if_truncated("value-add max-offer solver", best_price, converged,
                       best_irr, *bracket, target_irr)

    # `ttm_noi` joins the guard now that it can legitimately be None: an
    # implied entry cap needs a numerator, and reporting one derived from
    # a fabricated NOI was the whole point of deleting the literal above.
    implied_cap = (ttm_noi / best_price
                   if ttm_noi and best_price and best_price > 0 else None)
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


def _monotonicity_warning(samples: list) -> str:
    """A price step where a HIGHER price produced a HIGHER LP net IRR.

    `samples` is every `(price, irr)` the bisection actually evaluated,
    in call order. Sorting by price and scanning adjacent pairs tests the
    assumption bisection rests on, using the points already computed —
    no extra projections.

    Returns None when the samples are consistent with a decreasing
    objective, which is the ordinary case.
    """
    priced = sorted((p, irr) for p, irr in samples if irr is not None)
    for (low_price, low_irr), (high_price, high_irr) in zip(priced,
                                                            priced[1:]):
        if high_irr > low_irr + MONOTONICITY_EPSILON:
            return (
                f"LP net IRR RISES with price between "
                f"${low_price:,.0f} ({low_irr:.4%}) and ${high_price:,.0f} "
                f"({high_irr:.4%}), so the objective is not monotone over "
                "the searched range and bisection may have converged on "
                "the wrong root. The usual cause is the exit-cap floor: "
                "below a certain price the exit cap is coerced up to the "
                "entry cap, and raising the price lowers it again. Treat "
                "this max price as indicative and check the exit-cap "
                "assumption.")
    return None


def solve_max_price_levered(adjusted_ttm_noi: float,
                            capex: float = 0,
                            target_lp_irr: float = None,
                            scenario: str = ScenarioType.BASE,
                            custom_params: dict = None,
                            expense_ratio: float = None,
                            hold_years: int = None,
                            transaction_costs: dict = None,
                            reserve: float = 0.0,
                            capex_pct_of_price: float = None,
                            market_cap: dict = None,
                            debt_terms=None,
                            waterfall_terms=None,
                            am_fee_pct: float = None,
                            am_fee_base: str = None,
                            gp_coinvest_pct: float = None) -> dict:
    """Item E4 — the maximum price that still clears a target LP NET IRR.

    The levered twin of `solve_max_price`. Where that one stops at the
    property's unlevered return, this one carries the price the whole way
    down the stack the deal is actually underwritten on: the loan sized
    at that price, the equity the loan leaves behind, the AM fee charged
    on it, and the promote paid out of what is left.

    **It calls the production builders; it does not reimplement them.**
    `project_cash_flows` → `build_debt_schedule` → `build_sources_uses` →
    `build_levered_returns` is exactly the chain `build_returns_model`
    runs for the results page, in the same order with the same
    arguments. A private copy of that assembly is how the sensitivity
    grid and the two solvers drifted from the scenario engine before item
    B collapsed them, and the levered stack has four moving parts where
    the unlevered one had a single loop.

    **The loan is re-sized at every candidate price, and that is the
    difference between this and the unlevered solver.** It is also not in
    tension with "the loan is sized ONCE, off the base case": that rule
    holds sizing fixed across bear/base/bull at ONE price, so the bear
    case cannot be handed a smaller loan and have its own downside
    flattened. Here the price itself is the variable being solved, and a
    lender asked to fund a different price writes a different loan.
    Holding the asking-price loan fixed while the price moved would
    report the LTV of a deal nobody is bidding.

    Args:
        adjusted_ttm_noi: analyst-adjusted TTM NOI
        capex: capital expenditure at acquisition, in dollars
        target_lp_irr: target LP net IRR; None →
            `config.SOLVER_TARGET_LP_NET_IRR` (15%), read at CALL time
        scenario: which scenario's params and exit cap to solve on.
            Defaults to BASE — a max offer justified by the bull case is
            not a max offer.
        debt_terms / waterfall_terms / am_fee_pct / am_fee_base /
            gp_coinvest_pct: the levered assumption set. Each defaults to
            its config value; pass the DEAL's resolved set so the solved
            price is computed on the same terms the results page shows.

    Returns `solve_max_price`'s keys, plus:
        - lp_net_irr / lp_moic: achieved at `max_price`
        - senior_debt / financing_costs / total_equity: the stack there
        - binding_constraint: which covenant sized the loan
        - coerced_region: the exit-cap floor bound somewhere in the search
        - monotonicity_warning: None, or why this answer is suspect
    """
    from model.debt import build_debt_schedule, resolve_debt_terms
    from model.levered import build_levered_returns
    from model.returns_model import build_sources_uses
    from model.waterfall import resolve_waterfall_terms

    # Read at CALL time, never bound as a default argument: config is
    # rebindable by tests and by the settings path, and a module-level
    # default would freeze the value at first import. The unlevered
    # solvers now follow the same rule via `resolve_target_irr`; this
    # comment used to note that they did not.
    target_lp_irr = float(target_lp_irr if target_lp_irr not in (None, "")
                          else cfg.SOLVER_TARGET_LP_NET_IRR)
    params = custom_params or SCENARIO_DEFAULTS.get(
        scenario, SCENARIO_DEFAULTS[ScenarioType.BASE])
    costs = resolve_transaction_costs(transaction_costs)
    reserve = float(reserve or 0.0)
    debt_terms = debt_terms or resolve_debt_terms()

    # ONE co-invest share, resolved before the loop and handed to BOTH
    # the capital stack and the waterfall. Resolving them independently
    # is the defect `resolve_waterfall_terms` documents at length: a deal
    # edited to 25% would size its equity 25/75 and split its promote on
    # config's 10/90, with neither number flagged.
    coinvest = (cfg.GP_COINVEST_PCT if gp_coinvest_pct in (None, "")
                else float(gp_coinvest_pct))
    if waterfall_terms is None:
        waterfall_terms = resolve_waterfall_terms(
            capital_structure={"gp_coinvest_pct": coinvest})

    # Resolved ONCE, outside the loop, for the reason `solve_max_price`
    # records: the exit cap is a property of the asset's class, age and
    # hold, not of the price being solved for. The entry-cap COERCION
    # still moves with price inside `project_cash_flows` — that is the
    # non-monotone pocket this function reports on.
    mc = market_cap or resolve_market_cap()
    exit_cap = resolve_exit_cap(mc["market_cap"], scenario,
                                hold_years)["exit_cap"]
    coerce = scenario in COERCED_SCENARIOS

    def capex_at(price: float) -> float:
        return (price * capex_pct_of_price if capex_pct_of_price
                else (capex or 0.0))

    def stack_at(price: float) -> dict:
        """The full levered stack at one price — the production path."""
        projection = project_cash_flows(
            ttm_noi=adjusted_ttm_noi, price=price, capex=capex_at(price),
            params=params, hold_years=hold_years,
            expense_ratio=expense_ratio, costs=costs, reserve=reserve,
            coerce_exit_cap=coerce, exit_cap=exit_cap,
        )
        debt = build_debt_schedule(
            price=price, y1_noi=projection["noi"][0], terms=debt_terms,
            hold_years=projection["hold_years"],
        )
        sources_uses = build_sources_uses(
            price=price, capex=capex_at(price),
            acquisition_cost=projection["acquisition_cost"],
            reserve=reserve, financing_costs=debt["financing_costs"],
            senior_debt=debt["loan"], gp_coinvest_pct=coinvest,
        )
        levered = build_levered_returns(
            projection, sources_uses=sources_uses, debt=debt,
            waterfall_terms=waterfall_terms, am_fee_pct=am_fee_pct,
            am_fee_base=am_fee_base,
        )
        return {"projection": projection, "debt": debt,
                "sources_uses": sources_uses, "levered": levered}

    # The same bracket as both unlevered solvers, and now literally the
    # same code: a 20% cap is cheap enough that any structure clears the
    # target, `dear_entry_cap` dear enough that none does.
    bracket = solver_price_bracket(adjusted_ttm_noi)   # kept — loop mutates
    low, high = bracket

    best_price = best_irr = best_stack = None
    samples = []
    coerced_region = False
    iterations = 0
    converged = False

    for i in range(SOLVER_MAX_ITERATIONS):
        iterations = i + 1
        mid = (low + high) / 2
        if (mid + capex_at(mid)) <= 0:
            high = mid
            continue

        stack = stack_at(mid)
        irr = stack["levered"]["lp_net_irr"]
        coerced_region = coerced_region or bool(
            stack["projection"]["exit_cap_coerced"])
        samples.append((mid, irr))

        if irr is None:
            # No convergent LP IRR at this price — too dear. Narrowing
            # from the top matches the unlevered solver, and keeps the
            # bracket on the side where an answer exists.
            high = mid
            continue

        best_price, best_irr, best_stack = mid, irr, stack

        if abs(irr - target_lp_irr) < SOLVER_TOLERANCE:
            converged = True
            break
        if irr > target_lp_irr:
            low = mid          # cheap enough to beat the target — pay more
        else:
            high = mid         # too dear

    warning = _monotonicity_warning(samples)
    if warning:
        logger.warning("levered max-offer solver: %s", warning)
    _warn_if_truncated("levered max-offer solver", best_price, converged,
                       best_irr, *bracket, target_lp_irr)

    if best_price is None:
        # Every candidate failed to produce an LP IRR. Report it rather
        # than returning a price of None dressed as an answer — the
        # results page and the memo both branch on `max_price`.
        # The OPENING bracket, not `low`/`high` — those have been narrowed
        # to a hair's width by the loop, so the old message named a range
        # the search had long since left.
        logger.warning(
            "levered max-offer solver found no price with a convergent LP "
            "net IRR between $%.0f and $%.0f — reporting no answer rather "
            "than a bound.", *bracket)
        # EVERY key the success branch returns, or none of them. A branch
        # that omits keys makes `levered_max_offer` a different shape
        # depending on whether it found an answer, so a consumer reading
        # `offer["exit_cap"]` works on most deals and KeyErrors on the
        # ones that failed to solve — the worst possible distribution of
        # a crash. Today all three consumers gate on `max_price` first,
        # which is why this is fragility rather than a live bug; keeping
        # the shapes identical means the next consumer cannot reopen it.
        return {
            "max_price": None, "implied_entry_cap": None,
            "achieved_irr": None, "lp_net_irr": None, "lp_moic": None,
            "target_irr": target_lp_irr, "iterations": iterations,
            "converged": False, "capex": capex_at(0.0),
            "acquisition_cost": None, "transaction_costs": costs,
            "reserve": reserve, "total_basis": None, "total_uses": None,
            "senior_debt": None, "financing_costs": None,
            "total_equity": None, "ltv": None, "binding_constraint": None,
            "unlevered_irr": None, "exit_cap": None,
            "exit_cap_coerced": False,
            "coerced_region": coerced_region,
            "monotonicity_warning": warning,
            "assumption_stamp": [],
            "model_type": "levered",
        }

    projection = best_stack["projection"]
    debt = best_stack["debt"]
    sources_uses = best_stack["sources_uses"]
    levered = best_stack["levered"]
    solved_capex = capex_at(best_price)

    return {
        "max_price": best_price,
        "implied_entry_cap": (adjusted_ttm_noi / best_price
                              if best_price > 0 else None),
        # `achieved_irr` carries the LP net IRR under the name every
        # existing consumer already reads (the Excel max-offer tab, the
        # memo, `webapp.results`), so the levered block renders through
        # the same helpers. `lp_net_irr` is the same number under the
        # name that says what it is — a reader must never have to guess
        # whether an "IRR" on this block is levered.
        "achieved_irr": best_irr,
        "lp_net_irr": best_irr,
        "lp_moic": levered["lp_moic"],
        "target_irr": target_lp_irr,
        "iterations": iterations,
        "converged": converged,
        "capex": solved_capex,
        "acquisition_cost": projection["acquisition_cost"],
        "transaction_costs": costs,
        "reserve": reserve,
        # The UNLEVERED basis, on the same definition `solve_max_price`
        # and `project_cash_flows` use — financing costs stay out of it
        # (CLAUDE.md key design decision 3). Total Uses at this price is
        # `total_basis + financing_costs`, and both are returned so a
        # consumer never has to reconstruct either.
        "total_basis": projection["total_basis"],
        "total_uses": sources_uses["total_uses"],
        "senior_debt": sources_uses["senior_debt"],
        "financing_costs": sources_uses["financing_costs"],
        "total_equity": sources_uses["total_equity"],
        "ltv": sources_uses["ltv"],
        "binding_constraint": debt["binding_constraint"],
        "unlevered_irr": projection["irr"],
        "exit_cap": projection["exit_cap"],
        "exit_cap_coerced": projection["exit_cap_coerced"],
        "coerced_region": coerced_region,
        "monotonicity_warning": warning,
        # No LP net IRR leaves the building without its stamp — the same
        # rule the results page, memo and workbook already enforce for
        # the levered lens (CLAUDE.md key design decision 7). A max OFFER
        # priced off an LP net IRR is no different.
        "assumption_stamp": levered["assumption_stamp"],
        "model_type": "levered",
    }
