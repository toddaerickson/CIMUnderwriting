"""
Item E1 — the debt layer.

Pure functions over a frozen `DebtTerms`. No Django import, no config
mutation, nothing read at import time — the same contract
`analysis/checks.py` and the Sources & Uses block in
`model/returns_model.py` already keep, so this runs identically from the
web app, the CLI and a test.

Three jobs, in the order a lender does them:

1. **Size the loan** — `size_loan` takes the MINIMUM of the LTV, DSCR and
   debt-yield caps and reports which one bound. Never LTV alone; a loan
   sized on value while its coverage tests fail is the first item on the
   design doc's list of common errors.
2. **Amortize it** — `amortization_schedule` rolls the balance forward
   MONTHLY and aggregates to annual debt service, because an annual
   approximation misprices the interest/principal split and the payoff
   balance, and the payoff is what the exit year actually pays.
3. **Charge for it** — origination at close, exit fee on the payoff.

`build_debt_schedule` composes all three and returns the dict item E3
will hand to `build_sources_uses` (`senior_debt`, `financing_costs`) and
to the levered cash flows.

**Wired by item E3a.** While E1 shipped, nothing outside `tests/`
imported this module and an AST test asserted so — that was the proof no
published unlevered number had moved. `model.levered` now consumes it
through `model.returns_model`, so the guard has done its job and is
retired. The unlevered screen is still untouched by the loan, but the
reason changed: financing costs stay OUT of `total_basis` by design
(item E3a's decision, recorded in CLAUDE.md), not because the debt layer
is unreachable.

Numeric authority: `docs/levered-waterfall-design.md` oracles 4 and 5,
reproduced to the cent in the test module.
"""

import logging
import math
from dataclasses import asdict, dataclass, fields

import config as cfg

logger = logging.getLogger("cim_analyst")

MONTHS_PER_YEAR = 12

#: Balances below this are zero — a payoff of $0.004 is float noise, not
#: money, and `fully_amortized` has to be able to say so.
BALANCE_TOLERANCE = 0.005

CONSTRAINT_LTV = "ltv"
CONSTRAINT_DSCR = "dscr"
CONSTRAINT_DEBT_YIELD = "debt_yield"

CONSTRAINT_LABELS = {
    CONSTRAINT_LTV: "Max LTV",
    CONSTRAINT_DSCR: "Min DSCR",
    CONSTRAINT_DEBT_YIELD: "Min Debt Yield",
}

NOI_BASIS_YEAR_1 = "year_1"
NOI_BASIS_STABILIZED = "stabilized"

NOI_BASIS_LABELS = {
    NOI_BASIS_YEAR_1: "Year 1",
    NOI_BASIS_STABILIZED: "Stabilized",
}

_INT_FIELDS = ("amort_years", "io_months", "term_years")
_FLOAT_FIELDS = ("rate", "index_rate", "spread", "max_ltv", "min_dscr",
                 "min_debt_yield", "orig_fee_pct", "exit_fee_pct")


@dataclass(frozen=True)
class DebtTerms:
    """One senior loan. Frozen: sizing reads it from several places and a
    term that changed underneath a schedule would be unattributable.

    Threshold semantics differ by direction, and the difference matters:

    * `max_ltv` is a **cap**. Zero means no debt, and it always applies.
    * `min_dscr` and `min_debt_yield` are **coverage floors**. Zero or
      None means the lender does not impose that test, so it contributes
      no cap — NOT that the loan is zero. Reading a missing covenant as a
      zero ceiling would silently refuse debt on every deal that omits
      one.
    """

    # These defaults MIRROR config.DEBT_TERMS and are a fallback for
    # direct construction only — `resolve_debt_terms` is the blessed path
    # and reads config. Duplicated values drift, so
    # `test_dataclass_defaults_do_not_drift_from_config` pins every one of
    # them to config in CI. `rate` is the deliberate exception: it stays
    # None here so a bare DebtTerms RAISES instead of quietly pricing a
    # loan at whatever config happened to say.
    rate: float = None
    index_rate: float = None
    spread: float = None
    amort_years: int = 25
    io_months: int = 0
    term_years: int = 10
    max_ltv: float = 0.65
    min_dscr: float = 1.25
    min_debt_yield: float = 0.10
    orig_fee_pct: float = 0.01
    exit_fee_pct: float = 0.0
    loan_type: str = "senior_fixed"

    def __post_init__(self):
        """Reject terms that have no meaning before they reach the math.

        `amort_years=0` is the one that matters: it would otherwise fall
        through `monthly_payment`'s degenerate branch and produce a
        sizing constant of 1200%/yr, which silently sizes a loan two
        orders of magnitude too small rather than failing. `term_years=0`
        would silently switch off both the full-IO test and the maturity
        warning. Neither is a loan; both are caught here rather than
        producing a confident wrong number downstream.

        NaN is rejected explicitly. Every comparison against NaN is False,
        so it slips past a `< 0` guard untouched and then poisons the
        arithmetic downstream WITHOUT raising: a NaN `min_dscr` produced a
        DSCR cap of NaN, `max(0.0, nan)` returned 0.0, and `size_loan`
        answered "this deal supports no debt, bound by DSCR" — a confident
        sentence about a number nobody supplied.
        """
        for name in ("rate", "index_rate", "spread", "max_ltv", "min_dscr",
                     "min_debt_yield", "orig_fee_pct", "exit_fee_pct",
                     "amort_years", "io_months", "term_years"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(
                    f"{name} must be a finite number, got {value!r} — NaN "
                    "passes every ordinary guard and then produces a "
                    "confident wrong answer instead of an error.")

        if not self.amort_years or int(self.amort_years) <= 0:
            raise ValueError(
                f"amort_years must be positive, got {self.amort_years!r} — "
                "a loan with no amortization term has no defined payment.")
        if not self.term_years or int(self.term_years) <= 0:
            raise ValueError(
                f"term_years must be positive, got {self.term_years!r} — "
                "a loan with no term has no maturity date to test against.")
        if int(self.io_months) < 0:
            raise ValueError(f"io_months cannot be negative, got "
                             f"{self.io_months!r}")
        # The rate trio is sign-checked with the rest: a negative rate was
        # accepted while every sibling field rejected one, and it produced
        # a plausible-looking payment on a loan that pays you to hold it.
        for name in ("rate", "index_rate", "spread", "max_ltv", "min_dscr",
                     "min_debt_yield", "orig_fee_pct", "exit_fee_pct"):
            value = getattr(self, name)
            if value is not None and float(value) < 0:
                raise ValueError(f"{name} cannot be negative, got {value!r}")

        # The percent-vs-decimal typo, flagged in E1's review and deferred
        # to item E3a because E3a adds the first live override call sites.
        # Every field below is a DECIMAL fraction, so `rate=6.5` means
        # 650%/yr — and it used to construct cleanly, pricing a $6.5M loan
        # at $3,520,833/mo against a correct $43,888/mo. Eighty times
        # wrong, silently, in a payment that feeds every levered return.
        #
        # `min_dscr` is deliberately absent: it is a coverage RATIO, and
        # 1.25x is the market term this repo underwrites to.
        #
        # This is the model layer's backstop, not the boundary itself —
        # `webapp.forms` owns the whole-number-percent conversion, as it
        # does for every other percentage on the assumptions page. A
        # backstop is still worth having: the CLI, a stored override row
        # and any programmatic caller never pass through the form.
        for name in ("rate", "index_rate", "spread", "max_ltv",
                     "min_debt_yield", "orig_fee_pct", "exit_fee_pct"):
            value = getattr(self, name)
            if value is not None and float(value) > 1.0:
                raise ValueError(
                    f"{name}={value!r} is greater than 1.0, and these are "
                    f"DECIMAL fractions — {value!r} means "
                    f"{float(value):.0%}. If you meant "
                    f"{float(value) / 100:g}, pass that. Accepting it "
                    "would price a loan nobody offered and report the "
                    "result without complaint.")

    def all_in_rate(self) -> float:
        """The annual rate actually charged.

        Two ways to say it: an all-in `rate`, or `index_rate` + `spread`
        for floating paper. An explicit `rate` wins.

        Anything unresolvable raises. Both halves of the floating pair are
        REQUIRED — treating a missing index as zero would price a
        spread-only loan at 2.25% and a missing spread at the bare index,
        and either is a confident wrong number rather than an error. A 0%
        loan produces a spectacular levered IRR and no complaint anywhere,
        which is the worst failure mode available here.
        """
        if self.rate is not None:
            return float(self.rate)
        if self.index_rate is None or self.spread is None:
            raise ValueError(
                "DebtTerms has no resolvable rate — set `rate`, or set BOTH "
                f"`index_rate` and `spread` (got index_rate="
                f"{self.index_rate!r}, spread={self.spread!r}). Treating "
                "either half as zero would price a loan that nobody offered."
            )
        return float(self.index_rate) + float(self.spread)

    @property
    def interest_only_for_term(self) -> bool:
        """True when the loan never makes an amortizing payment."""
        return self.io_months >= self.term_years * MONTHS_PER_YEAR


def displayed_rate(terms: dict):
    """`all_in_rate` for a DISPLAY surface reading a PERSISTED run.

    Same formula as `DebtTerms.all_in_rate`, one difference that is the
    whole reason it exists: this returns None where that RAISES. Raising
    is right when a rate is about to price a loan — a spread-only loan
    quoted at the bare index is a confident wrong number. It is wrong
    when the results page, the memo or the workbook is rendering a run
    that was stored months ago: a display surface that 500s on a stored
    payload tells the analyst nothing and loses the rest of the page.

    Takes the plain dict `build_debt_schedule` persists under `terms`,
    not the frozen dataclass, because that is what comes back out of
    JSONB. Lives HERE, beside the authoritative formula, so the three
    surfaces cannot drift from it or from each other (audit finding).
    """
    terms = terms or {}
    if terms.get("rate") is not None:
        return float(terms["rate"])
    index, spread = terms.get("index_rate"), terms.get("spread")
    if index is None or spread is None:
        return None
    return float(index) + float(spread)


def binding_constraint_label(debt: dict) -> str:
    """Which covenant sized the loan, in this module's own words.

    One lookup for all three display surfaces — a second copy of the
    label table is how "Min DSCR" and "DSCR" end up on two pages
    describing the same loan.
    """
    key = (debt or {}).get("binding_constraint")
    return CONSTRAINT_LABELS.get(key, key or "N/A")


def resolve_debt_terms(overrides: dict = None) -> DebtTerms:
    """Partial override → fully resolved terms.

    Same contract as `analysis.valuation.resolve_transaction_costs` and
    `model.returns_model.resolve_capital_structure`: omitting a key means
    "use the default", never "zero". Pass an explicit 0 to mean zero.

    **Switching to floating rate is a MODE change, not one more key.**
    Config seeds a fixed `rate`, and that seed is not something an
    `index_rate`/`spread` override can displace by the ordinary merge
    rule — the merge only overwrites keys the caller named, so the fixed
    rate would survive and `all_in_rate` would short-circuit on it,
    silently returning the default and discarding the floating terms the
    caller asked for. So supplying either half of the floating pair
    WITHOUT an explicit `rate` clears the seeded one. Naming `rate` and a
    floating pair together is contradictory; `rate` wins and we say so.

    Config is read at CALL time, never bound at import, so a test or a
    future settings path that rebinds `config.DEBT_TERMS` is seen.
    """
    resolved = dict(cfg.DEBT_TERMS)
    known = {f.name for f in fields(DebtTerms)}
    overrides = overrides or {}

    for key, value in overrides.items():
        if key not in known:
            # An override row written by a future version must not take
            # down a run on an older one.
            logger.warning("unknown debt term %r ignored", key)
            continue
        if value not in (None, ""):
            resolved[key] = value

    asked_floating = any(overrides.get(k) not in (None, "")
                         for k in ("index_rate", "spread"))
    named_fixed = overrides.get("rate") not in (None, "")
    if asked_floating and not named_fixed:
        resolved["rate"] = None
    elif asked_floating and named_fixed:
        logger.warning(
            "debt terms name both a fixed rate (%s) and floating terms "
            "(index_rate=%s, spread=%s) — using the fixed rate",
            resolved["rate"], resolved.get("index_rate"),
            resolved.get("spread"))

    unknown = set(resolved) - known
    if unknown:
        logger.warning("config.DEBT_TERMS has unknown keys %s — ignored",
                       sorted(unknown))
        for key in unknown:
            resolved.pop(key)

    for key in _INT_FIELDS:
        value = resolved.get(key)
        if value is not None:
            # Finiteness FIRST. `int(float('inf'))` raises OverflowError,
            # not this module's ValueError, so an infinite override
            # escaped the clean error contract every other bad input
            # here obeys — and `DebtTerms.__post_init__`'s own finite
            # check never got the chance to run. Flagged in E1's review
            # and deferred to this item because E3a adds the first live
            # override call sites (`engine.run_analysis(debt_terms=...)`
            # and the stored per-deal override).
            if not math.isfinite(float(value)):
                raise ValueError(
                    f"{key} must be a finite number, got {value!r} — NaN "
                    "passes every ordinary guard and infinity raises an "
                    "OverflowError from int(), neither of which reads as "
                    "a bad loan term.")
            # Reject rather than truncate, for the same reason
            # `amortization_schedule` rejects a fractional hold: int(25.9)
            # silently re-prices the loan on a shorter schedule.
            if float(value) != int(value):
                raise ValueError(
                    f"{key} must be a whole number, got {value!r} — "
                    "truncating it would quietly re-price the loan.")
            resolved[key] = int(value)
    for key in _FLOAT_FIELDS:
        if resolved.get(key) is not None:
            resolved[key] = float(resolved[key])
    return DebtTerms(**resolved)


def monthly_payment(principal: float, annual_rate: float,
                    amort_years: int) -> float:
    """Level monthly payment fully amortizing `principal` over
    `amort_years`.

    A zero rate is straight-line principal, not a division by zero.
    """
    principal = float(principal or 0.0)
    if principal <= 0:
        return 0.0
    periods = int(amort_years or 0) * MONTHS_PER_YEAR
    if periods <= 0:
        raise ValueError(
            f"amort_years must be positive, got {amort_years!r} — returning "
            "the whole principal as a monthly payment would size a loan two "
            "orders of magnitude too small without saying so.")
    rate = float(annual_rate) / MONTHS_PER_YEAR
    if rate == 0:
        return principal / periods
    return principal * rate / (1 - (1 + rate) ** -periods)


def sizing_constant(terms: DebtTerms) -> float:
    """Annual debt service per $1 of loan, used for the DSCR test.

    The payment the loan actually has to cover. A loan that is
    interest-only for its whole term never makes an amortizing payment,
    so testing one would understate what it supports. A loan with a
    PARTIAL IO period does make that payment before maturity, so it is
    tested on the amortizing constant — sizing partial IO on its IO
    payment is the design doc's "max-leverage sizing with no covenant
    headroom", and on the oracle-5 fixture it would lend $7.38M where
    $6.33M is covered.
    """
    rate = terms.all_in_rate()
    if terms.interest_only_for_term:
        return rate
    return monthly_payment(1.0, rate, terms.amort_years) * MONTHS_PER_YEAR


def size_loan(price: float, y1_noi: float, terms: DebtTerms, *,
              stabilized_noi: float = None) -> dict:
    """Loan proceeds = min(LTV cap, DSCR cap, debt-yield cap).

    Every NOI basis supplied is tested, and the minimum across ALL of
    them wins. `stabilized_noi` defaults to `y1_noi`, which collapses to
    the single-basis case. When they differ the loan sizes off the weaker
    test — a value-add deal underwritten to a rich stabilized NOI still
    only borrows what its in-place NOI covers, which is what a bank does
    and is the "DSCR tested on the wrong NOI basis" error designed out.

    Returns the loan, the binding constraint and the NOI basis that bound
    it (`None` for LTV, which has no NOI basis), every cap that was
    considered, and the resulting LTV / debt yield / DSCR at the sized
    loan. Caps floor at zero: negative NOI supports no debt, it does not
    support negative debt.

    The DSCR here is `sizing_dscr` — measured against the SIZING constant,
    which is the covenant the lender tests. On a partial-IO loan that is
    deliberately NOT the ratio you get by dividing Year 1 NOI by Year 1
    debt service, because Year 1 pays interest only. Both numbers are
    real and they are different; naming this one `dscr` invited a reader
    to check it against the debt service in the same dict and conclude one
    of them was wrong. `build_debt_schedule` reports the actual Year 1
    coverage separately as `dscr_year_1`.
    """
    price = float(price or 0.0)
    y1_noi = float(y1_noi or 0.0)
    for label, value in (("price", price), ("y1_noi", y1_noi),
                         ("stabilized_noi", stabilized_noi)):
        if value is not None and not math.isfinite(float(value)):
            raise ValueError(
                f"{label} must be a finite number, got {value!r} — a NaN "
                "here sizes a loan of 0 and reports a binding covenant, "
                "which reads as an answer rather than a bad input.")
    constant = sizing_constant(terms)

    constraints = [{
        "key": CONSTRAINT_LTV,
        "label": CONSTRAINT_LABELS[CONSTRAINT_LTV],
        "basis": None,
        "amount": max(0.0, price * float(terms.max_ltv or 0.0)),
    }]

    bases = [(NOI_BASIS_YEAR_1, y1_noi)]
    if stabilized_noi is not None and float(stabilized_noi) != y1_noi:
        bases.append((NOI_BASIS_STABILIZED, float(stabilized_noi)))

    for basis, noi in bases:
        # A zero/None floor is "no covenant", so it contributes no cap.
        if terms.min_dscr and constant > 0:
            constraints.append({
                "key": CONSTRAINT_DSCR,
                "label": CONSTRAINT_LABELS[CONSTRAINT_DSCR],
                "basis": basis,
                "amount": max(0.0, (noi / float(terms.min_dscr)) / constant),
            })
        if terms.min_debt_yield:
            constraints.append({
                "key": CONSTRAINT_DEBT_YIELD,
                "label": CONSTRAINT_LABELS[CONSTRAINT_DEBT_YIELD],
                "basis": basis,
                "amount": max(0.0, noi / float(terms.min_debt_yield)),
            })

    binding = min(constraints, key=lambda c: c["amount"])
    loan = binding["amount"]

    annual_ds = loan * constant
    return {
        "loan": loan,
        "binding_constraint": binding["key"],
        "binding_basis": binding["basis"],
        "constraints": constraints,
        "sizing_constant": constant,
        "ltv": (loan / price) if price > 0 else None,
        "sizing_dscr": (y1_noi / annual_ds) if annual_ds > 0 else None,
        "debt_yield": (y1_noi / loan) if loan > 0 else None,
    }


def amortization_schedule(loan: float, terms: DebtTerms, *,
                          hold_years: int) -> dict:
    """Monthly roll-forward aggregated to annual debt service + payoff.

    Monthly and not annual on purpose: an annual approximation gets both
    the interest/principal split and the payoff balance wrong, and the
    payoff is what the exit year pays. The amortizing payment is computed
    on the full `amort_years` and is NOT re-amortized over the months
    remaining after an IO period — that is the market convention (the
    amortization schedule is a term-sheet number independent of IO) and
    it is what reproduces oracle 4's payoff. The loan therefore balloons.

    Two conditions are reported rather than swallowed:

    * `matures_before_exit` — the hold runs past `term_years`, so the
      balloon comes due before the sale. That is a refinancing, which E1
      does not model; the schedule keeps amortizing past the maturity
      date and says so instead of pretending the date is not there.
    * `fully_amortized` — the balance reached zero inside the hold, after
      which there is nothing left to pay.
    """
    loan = max(0.0, float(loan or 0.0))
    if hold_years != int(hold_years):
        # int() would truncate 5.9 to 5 and quietly drop eleven months of
        # debt service — and, if the loan matured in year 6, suppress the
        # maturity warning too.
        raise ValueError(
            f"hold_years must be a whole number of years, got "
            f"{hold_years!r}; the schedule is annual.")
    hold_years = int(hold_years)
    if hold_years < 1:
        # Otherwise every series comes back empty and `payoff_balance`
        # silently equals the loan — a schedule that says the debt was
        # never serviced and is repaid in full.
        raise ValueError(f"hold_years must be at least 1, got {hold_years!r}")
    rate = terms.all_in_rate()
    monthly_rate = rate / MONTHS_PER_YEAR

    payment = monthly_payment(loan, rate, terms.amort_years)
    io_payment = loan * monthly_rate

    balance = loan
    annual_debt_service, annual_interest = [], []
    annual_principal, ending_balances = [], []
    ds_ytd = interest_ytd = principal_ytd = 0.0

    for month in range(1, hold_years * MONTHS_PER_YEAR + 1):
        interest = balance * monthly_rate
        if month <= terms.io_months:
            principal = 0.0
        else:
            # Capped at the balance so the final payment cannot overshoot
            # into a negative balance, and floored at zero so a payment
            # short of the interest cannot silently negatively amortize.
            principal = min(payment - interest, balance)
            principal = max(0.0, principal)
        balance -= principal

        ds_ytd += interest + principal
        interest_ytd += interest
        principal_ytd += principal

        if month % MONTHS_PER_YEAR == 0:
            annual_debt_service.append(ds_ytd)
            annual_interest.append(interest_ytd)
            annual_principal.append(principal_ytd)
            ending_balances.append(balance)
            ds_ytd = interest_ytd = principal_ytd = 0.0

    matures_before_exit = hold_years > terms.term_years
    if matures_before_exit:
        logger.warning(
            "loan matures in year %s but the hold runs %s years — the balloon "
            "is due before the sale. E1 does not model the refinancing; the "
            "schedule shown amortizes past maturity.",
            terms.term_years, hold_years)

    return {
        "loan": loan,
        "hold_years": hold_years,
        "rate": rate,
        "monthly_payment": payment,
        "io_monthly_payment": io_payment,
        "annual_debt_service": annual_debt_service,
        "annual_interest": annual_interest,
        "annual_principal": annual_principal,
        "ending_balances": ending_balances,
        "payoff_balance": balance,
        "matures_before_exit": matures_before_exit,
        "fully_amortized": loan > 0 and balance <= BALANCE_TOLERANCE,
    }


def build_debt_schedule(price: float, y1_noi: float, terms: DebtTerms, *,
                        hold_years: int, stabilized_noi: float = None) -> dict:
    """Size, amortize and price the fees in one call.

    `financing_costs` is the close-dated total that item E3 hands to
    `build_sources_uses` — origination only. The exit fee is paid at sale
    out of proceeds, so it is NOT a use of funds; putting it there would
    inflate the basis and understate the return at both ends.

    **`financing_costs` is NOT in the unlevered basis, and must not
    become so.** This paragraph used to prescribe the opposite: E1
    measured that handing the fee to `build_sources_uses` breaks
    `analysis.checks.sources_uses_ties` by exactly `financing_costs` —
    the fee lands in Total Uses while
    `analysis.valuation.project_cash_flows` computes
    `total_basis = price + capex + acquisition_cost + reserve`, which has
    no financing term — and prescribed threading a zero-defaulted
    financing term through the projection, the scenario engine, both
    solvers and the value-add model.

    **The operator reversed that on 2026-08-01 (item E3a).** An
    origination fee inside `total_basis` moves the primary 10% unlevered
    IRR screen the moment a deal names a loan, and an unlevered return
    charged a financing fee is not an unlevered return. So the TIE moved
    instead — `Uses == total_basis + financing_costs`, checked to the
    cent — and `project_cash_flows` was never touched. E1's measurement
    still stands; only the side of the equation it is written on changed.
    `test_financing_costs_break_the_basis_tie_until_e3_extends_it` was
    deleted with the route it pinned; the new identity has tests on both
    sides. Item E4 inherits an unlevered engine that behaves exactly as it
    always did — do not re-thread the fee into it.

    Two coverage ratios come back and they are allowed to disagree:
    `sizing_dscr` is the covenant the loan was sized against (the
    amortizing constant), while `dscr_year_1` is Year 1 NOI over the debt
    service actually scheduled for Year 1. On a partial-IO loan Year 1
    pays interest only, so the actual ratio is the more generous of the
    two. Reporting only the first next to a schedule showing the second
    is what makes a reader think the model contradicts itself.
    """
    sized = size_loan(price, y1_noi, terms, stabilized_noi=stabilized_noi)
    schedule = amortization_schedule(sized["loan"], terms,
                                     hold_years=hold_years)

    origination_fee = sized["loan"] * float(terms.orig_fee_pct or 0.0)
    exit_fee = schedule["payoff_balance"] * float(terms.exit_fee_pct or 0.0)

    first_year_ds = (schedule["annual_debt_service"][0]
                     if schedule["annual_debt_service"] else 0.0)

    return {
        **sized,
        **schedule,
        # A plain dict, not the frozen dataclass — the same fix E2 made to
        # `run_waterfall` after finding it the hard way.
        # `webapp.services.json_safe` falls back to `str(obj)` on anything
        # it does not recognise, so the object persisted to JSONB as the
        # string "DebtTerms(rate=0.0625, ...)": unqueryable, and a consumer
        # reading `["terms"]["rate"]` got "string indices must be
        # integers". It degraded silently rather than raising. Item E3a is
        # what first persists this payload, so the fix lands with it.
        # The caller that passed `terms` in still has the object;
        # `DebtTerms(**result["terms"])` rebuilds it.
        "terms": asdict(terms),
        "origination_fee": origination_fee,
        "exit_fee": exit_fee,
        "financing_costs": origination_fee,
        "dscr_year_1": (float(y1_noi) / first_year_ds
                        if first_year_ds > 0 else None),
    }
