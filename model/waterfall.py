"""
Item E2 — the single-tier LP waterfall.

Pure functions over a frozen `WaterfallTerms`. No Django import, no
config mutation, nothing read at import time — the same contract
`analysis/checks.py`, `model/debt.py` and the Sources & Uses block in
`model/returns_model.py` already keep, so this runs identically from the
web app, the CLI and a test.

**One tier, and the tier count is not a parameter** (operator decision,
2026-07-29). The GP invests alongside the LPs, both earn a preferred
return on unreturned capital, and above that the GP takes a promoted
interest in the residual. No catch-up, no clawback, no second hurdle.
`docs/levered-waterfall-design.md` §B proves that single hurdle is
deterministic — a forward accrual loop, no solver — because an
accrual account rolled at `(1 + pref)` and drawn down by distributions
IS the pref-rate IRR hurdle.

The order of operations inside one period, which is where waterfalls go
wrong:

1. **Accrue** on the unreturned capital at the START of the period.
   Period 0 does not accrue; capital contributed at close first earns at
   period 1.
2. **Call** any capital for this period. It joins the accrual base and
   starts earning next period.
3. **Distribute** — tier 1 (return of capital and preferred return, GP
   and LP pari passu) until it is current, then the residual, on which
   the GP earns its promote. No promote is paid in any period where
   tier 1 is not current, so every promoted dollar follows a full
   return of capital and preferred return *as of that period*.

**That is not the same as a clawback, and the difference is reported.**
Capital called AFTER a promote has been paid re-opens tier 1 with the
promote already gone. The operator's fund terms have no clawback (LPA
question 6, answered), so the GP keeps it — that is the correct model of
the actual terms, not a defect in them. What would be a defect is
leaving it to be inferred from a `tier1_current: False` sitting beside a
positive promote, so the result reports `unrecovered_promote` — what a
clawback would have recovered, capped at the shortfall the deal actually
ends with — and logs it. On a single-asset deal the residual normally
arises only at sale, after which no capital can be called; a refinance
distribution followed by a follow-on call is the shape that opens it.

**Wired by item E3a.** While E2 shipped, nothing outside `tests/`
imported this module and an AST test asserted so — the proof no published
unlevered number had moved. `model.levered` now builds the contribution
and distribution series and calls `run_waterfall`, so that guard is
retired. E3b carries the results page, memo and Excel surfaces.

Numeric authority: `docs/levered-waterfall-design.md` oracles 1-3,
reproduced to the cent in the test module. Five LPA questions are still
open (`docs/scoped-backlog.md` item E); each ships as a named parameter
carrying a documented default, and `assumption_stamp` in the result
carries the resolved set so no LP net IRR is ever displayed without it.
"""

import logging
import math
from collections.abc import Mapping, Sized
from dataclasses import asdict, dataclass, fields

import numpy_financial as npf

import config as cfg

logger = logging.getLogger("cim_analyst")

#: Claims below this are zero — a residual pref of $0.004 is float noise,
#: not money, and "is tier 1 current?" has to be answerable.
BALANCE_TOLERANCE = 0.005

# ── Conventions ─────────────────────────────────────────────────────
# Each of these is one of the five open LPA questions. The defaults are
# the ones the scope contract names; the alternatives are real
# conventions that change the number, which is why an unrecognised value
# raises instead of falling back.

COMPOUNDING_ANNUAL = "annual"
COMPOUNDING_SIMPLE = "simple"
COMPOUNDING_LABELS = {
    COMPOUNDING_ANNUAL: "Annually compounded",
    COMPOUNDING_SIMPLE: "Simple",
}

ORDERING_ROC_FIRST = "roc_first"
ORDERING_PREF_FIRST = "pref_first"
ORDERING_LABELS = {
    ORDERING_ROC_FIRST: "Return of capital first",
    ORDERING_PREF_FIRST: "Preferred return first",
}

ACCRUAL_BASE_CONTRIBUTED = "contributed"
ACCRUAL_BASE_COMMITTED = "committed"
ACCRUAL_BASE_LABELS = {
    ACCRUAL_BASE_CONTRIBUTED: "Contributed / unreturned capital",
    ACCRUAL_BASE_COMMITTED: "Committed capital",
}

AM_FEE_ABOVE_WATERFALL = "above_waterfall"
AM_FEE_NETTED_FROM_LP = "netted_from_lp"
AM_FEE_LABELS = {
    AM_FEE_ABOVE_WATERFALL: "Above the waterfall (deal expense)",
    AM_FEE_NETTED_FROM_LP: "Netted from LP distributions",
}

_FLOAT_FIELDS = ("pref_rate", "promote_split", "gp_coinvest_pct")
_STR_FIELDS = ("pref_compounding", "ordering", "accrual_base",
               "am_fee_treatment")


@dataclass(frozen=True)
class WaterfallTerms:
    """One single-hurdle waterfall. Frozen for the same reason
    `DebtTerms` is: the terms are read from several places inside one
    run, and a term that changed underneath a distribution schedule
    would be unattributable.

    Every string field is a convention with a live alternative in the
    market. The ones this module does not implement raise at
    construction rather than silently resolving to the default — the
    design doc's own fixture returns GP promote of 68,465 / 72,000 /
    81,600 depending only on which convention is in force, so a silent
    substitution is a confident wrong LP net IRR.
    """

    # These defaults MIRROR config and a duplicated value drifts, so
    # `test_dataclass_defaults_do_not_drift_from_config` pins every one
    # of them in CI. `gp_coinvest_pct` mirrors `config.GP_COINVEST_PCT`
    # — the capital block, not `WATERFALL_TERMS`, because the Sources &
    # Uses stack already owns that number and two copies diverge.
    pref_rate: float = 0.08
    pref_compounding: str = COMPOUNDING_ANNUAL
    ordering: str = ORDERING_ROC_FIRST
    promote_split: float = 0.20
    gp_coinvest_pct: float = 0.10
    accrual_base: str = ACCRUAL_BASE_CONTRIBUTED
    am_fee_treatment: str = AM_FEE_ABOVE_WATERFALL
    catch_up: bool = False

    def __post_init__(self):
        """Reject terms that have no meaning before they reach the math.

        The numeric fields are COERCED to float here, not merely checked.
        A frozen dataclass built directly — `WaterfallTerms(pref_rate=
        "0.08")` — otherwise carries a string past every guard and then
        fails much later with "Unknown format code '%' for object of type
        'str'", an error naming neither the field nor the caller.
        `resolve_waterfall_terms` already coerces; direct construction is
        the path E3's tests and the CLI will take.

        Booleans are rejected rather than coerced. `bool` is a subclass
        of `int`, so `pref_rate=True` would otherwise sail through as a
        100% preferred return.

        NaN is rejected explicitly, for the reason `DebtTerms` records:
        every comparison against NaN is False, so it walks past a
        range guard untouched and then poisons the arithmetic WITHOUT
        raising — a NaN `pref_rate` produces a NaN accrual, a NaN
        balance, a residual of NaN, and a promote of NaN reported as a
        number.
        """
        for name in _FLOAT_FIELDS:
            value = getattr(self, name)
            if isinstance(value, bool):
                raise ValueError(
                    f"{name} must be a number, got {value!r} — bool is a "
                    "subclass of int, so True would be read as 1.0.")
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ValueError(
                    f"{name} must be a number, got {value!r}") from None
            if not math.isfinite(value):
                raise ValueError(
                    f"{name} must be a finite number, got {value!r} — NaN "
                    "passes every ordinary guard and then produces a "
                    "confident wrong answer instead of an error.")
            object.__setattr__(self, name, value)
        for name in _STR_FIELDS:
            object.__setattr__(self, name, str(getattr(self, name)))

        # The bound is the same [0, 1) as the two split fields, and it is
        # here for the same reason they have one: this codebase displays
        # percentages as whole numbers and stores them as decimals
        # (webapp.forms owns that boundary), so `pref_rate=8` meaning 8%
        # is the live mistake. Unbounded, it accrued $8,000,000 of
        # preferred return on $1,000,000 of capital in year one and said
        # nothing, while `promote_split=20` on the same form correctly
        # raised.
        if not 0.0 <= self.pref_rate < 1.0:
            raise ValueError(
                f"pref_rate must be in [0, 1), got {self.pref_rate!r} — "
                "rates are DECIMAL fractions here (0.08, not 8). A "
                "negative preferred return pays the LP for waiting; a "
                "whole-number percent accrues a hundredfold.")
        if not 0.0 <= float(self.promote_split) < 1.0:
            raise ValueError(
                f"promote_split must be in [0, 1), got "
                f"{self.promote_split!r}; 1.0 hands the GP the entire "
                "residual and leaves the LP its pref and nothing else.")
        if not 0.0 <= float(self.gp_coinvest_pct) < 1.0:
            raise ValueError(
                f"gp_coinvest_pct must be in [0, 1), got "
                f"{self.gp_coinvest_pct!r}; at 1.0 there is no LP and no "
                "waterfall to run.")

        if self.pref_compounding not in COMPOUNDING_LABELS:
            raise ValueError(
                f"pref_compounding must be one of "
                f"{sorted(COMPOUNDING_LABELS)}, got "
                f"{self.pref_compounding!r}. Simple and compounded differ "
                "by ~19% of GP promote on the design doc's fixture, so "
                "this does not fall back to a default.")
        if self.ordering not in ORDERING_LABELS:
            raise ValueError(
                f"ordering must be one of {sorted(ORDERING_LABELS)}, got "
                f"{self.ordering!r}. Under a simple pref it is the "
                "difference between $81,600 and $72,000 of promote on the "
                "design doc's fixture.")

        if self.accrual_base == ACCRUAL_BASE_COMMITTED:
            raise NotImplementedError(
                "accrual_base='committed' is not implemented — the pref "
                "would accrue on capital not yet called, which needs a "
                "commitment schedule this model does not carry. Open LPA "
                "question 2; the shipped default is "
                f"{ACCRUAL_BASE_CONTRIBUTED!r}.")
        if self.accrual_base != ACCRUAL_BASE_CONTRIBUTED:
            raise ValueError(
                f"accrual_base must be one of {sorted(ACCRUAL_BASE_LABELS)}, "
                f"got {self.accrual_base!r}")

        if self.am_fee_treatment == AM_FEE_NETTED_FROM_LP:
            raise NotImplementedError(
                "am_fee_treatment='netted_from_lp' is not implemented — the "
                "shipped default charges the asset-management fee above the "
                "waterfall, as a deal expense that reduces distributable "
                "cash before this function is called. Open LPA question 4.")
        if self.am_fee_treatment != AM_FEE_ABOVE_WATERFALL:
            raise ValueError(
                f"am_fee_treatment must be one of "
                f"{sorted(AM_FEE_LABELS)}, got {self.am_fee_treatment!r}")

        # Coerced HERE and not in `resolve_waterfall_terms`, so both
        # construction paths agree. They did not: resolve coerced, direct
        # construction did not, and `WaterfallTerms(catch_up="False")`
        # raised "a GP catch-up tier is not supported" at a caller asking
        # for exactly the opposite — the failure `_coerce_bool`'s own
        # docstring names. Direct construction is the path the CLI and
        # E3's tests take.
        object.__setattr__(self, "catch_up", _coerce_bool(self.catch_up))
        if self.catch_up:
            raise NotImplementedError(
                "a GP catch-up tier is not supported — the operator scoped "
                "this waterfall to ONE tier (docs/scoped-backlog.md item "
                "E). Without a catch-up the GP never recovers pref "
                "leakage, and the promote applies to the residual only.")


_FALSEY_STRINGS = {"", "0", "false", "no", "off"}
_TRUTHY_STRINGS = {"1", "true", "yes", "on"}


def _coerce_bool(value):
    """A stored override or a form field arrives as a string, and
    `bool("False")` is True — which would fail a run with "a GP catch-up
    tier is not supported" on terms that explicitly asked for none.
    """
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _FALSEY_STRINGS:
            return False
        if text in _TRUTHY_STRINGS:
            return True
        raise ValueError(
            f"catch_up must be a boolean, got {value!r} — bool() would read "
            "any non-empty string as True.")
    return bool(value)


def resolve_waterfall_terms(overrides: dict = None,
                            capital_structure: dict = None) -> WaterfallTerms:
    """Partial override → fully resolved terms.

    Same contract as `model.debt.resolve_debt_terms` and
    `model.returns_model.resolve_capital_structure`: omitting a key means
    "use the default", never "zero". Pass an explicit 0 to mean zero.

    Unknown **keys** are logged and ignored, so a stored override row
    written by a future version cannot take down a run on an older one.
    Unknown **values** for the four convention fields raise instead —
    the split is deliberate and argued in `WaterfallTerms.__post_init__`:
    a wrong basis label costs a rounding difference, a wrong pref
    convention re-prices the promote.

    **`capital_structure` is how E3 keeps the co-invest honest, and
    omitting it is a real defect, not a convenience.** GP co-invest is a
    PER-DEAL assumption: `webapp.forms` puts it on the assumptions page,
    `model.returns_model.resolve_capital_structure` resolves it, and
    `engine.py` hands that resolved value to `build_sources_uses`. Seeding
    only from `config.GP_COINVEST_PCT` would make a deal edited to 25%
    print a Sources & Uses stack split 25/75 and an LP net IRR computed
    on 10/90 — two numbers on one page, derived from different equity,
    neither flagged. So pass the deal's resolved capital structure and
    this reads `capital_structure["gp_coinvest_pct"]`; the config scalar
    is the fallback for the CLI and for tests, not the normal path.
    An explicit `gp_coinvest_pct` override still wins over both.

    It is deliberately NOT a key in `config.WATERFALL_TERMS` — the
    capital block already owns it, and a second copy diverges.

    Config is read at CALL time, never bound at import, so a test or a
    future settings path that rebinds it is seen.
    """
    resolved = dict(cfg.WATERFALL_TERMS)
    coinvest = (capital_structure or {}).get("gp_coinvest_pct")
    resolved["gp_coinvest_pct"] = (cfg.GP_COINVEST_PCT
                                   if coinvest in (None, "") else coinvest)
    known = {f.name for f in fields(WaterfallTerms)}

    for key, value in (overrides or {}).items():
        if key not in known:
            logger.warning("unknown waterfall term %r ignored", key)
            continue
        if value not in (None, ""):
            resolved[key] = value

    unknown = set(resolved) - known
    if unknown:
        logger.warning("config.WATERFALL_TERMS has unknown keys %s — ignored",
                       sorted(unknown))
        for key in unknown:
            resolved.pop(key)

    for key in _FLOAT_FIELDS:
        if resolved.get(key) is not None:
            resolved[key] = float(resolved[key])
    for key in _STR_FIELDS:
        if resolved.get(key) is not None:
            resolved[key] = str(resolved[key])
    # `catch_up` is deliberately NOT coerced here — `WaterfallTerms`
    # does it, so the direct-construction path gets the same treatment.
    return WaterfallTerms(**resolved)


def _as_amounts(label, series):
    """One period-indexed money series → a list of floats.

    Strict about what counts as a series, because the loose reading is
    silently wrong rather than loudly wrong: `"123"` iterates to THREE
    periods of $1, $2 and $3, and a dict iterates its KEYS, so
    `{1: "a", 2: "b"}` becomes distributions of $1 and $2. Neither
    raises anything on its own. A generator is refused too — it has no
    length to check the two series against each other with, and it is
    consumed by the first pass.

    `None` and `""` entries are refused rather than read as zero. A
    missing period in a cash-flow series is missing data, and quietly
    calling it "distributed nothing" is the swallowed error this
    codebase's rules forbid — especially beside a NaN in the same slot,
    which does raise.
    """
    if isinstance(series, (str, bytes, Mapping)) or not isinstance(series,
                                                                   Sized):
        raise TypeError(
            f"{label} must be a period-indexed sequence of numbers (index 0 "
            f"= close), got {type(series).__name__}: {series!r}")

    amounts = []
    for period, value in enumerate(series):
        if isinstance(value, bool) or value is None or isinstance(
                value, (str, bytes)):
            raise ValueError(
                f"{label}[{period}] must be a number, got {value!r} — a "
                "missing period is missing data, not a zero.")
        try:
            amount = float(value)
        except (TypeError, ValueError):
            raise ValueError(
                f"{label}[{period}] must be a number, got {value!r}") from None
        if not math.isfinite(amount):
            raise ValueError(
                f"{label}[{period}] must be a finite number, got {value!r} — "
                "a NaN here produces a NaN promote reported as a number.")
        if amount < 0:
            raise ValueError(
                f"{label}[{period}] cannot be negative, got {value!r}. A "
                "negative distribution is a capital call and a negative "
                "contribution is a distribution; netting them silently "
                "corrupts the accrual base. See the plan doc's E3 handoff "
                "note: a debt-service shortfall is a reserve draw or a "
                "capital call, not a negative distribution.")
        amounts.append(amount)
    return amounts


def _align_series(contributions, distributions):
    """Two period-indexed series of the same length, period 0 = close.

    A bare number for `contributions` is shorthand for "all equity at
    close" — unambiguous, because a scalar cannot be misaligned — and is
    what E3 passes from item D's `total_equity`. Two sequences of
    DIFFERENT lengths raise rather than being padded: padding a 5-element
    distribution list against a 1-element contribution list would align
    year 1's cash to period 0 and silently delete a year of preferred
    return.
    """
    dists = _as_amounts("distributions", distributions)
    if not dists:
        raise ValueError("distributions is empty — there is nothing to "
                         "distribute and no period to distribute it in.")

    if isinstance(contributions, bool):
        raise ValueError(
            f"contributions must be a number or a sequence, got "
            f"{contributions!r} — bool is a subclass of int, so True would "
            "fund the deal with $1.")
    if isinstance(contributions, (int, float)):
        contribs = [float(contributions)] + [0.0] * (len(dists) - 1)
        if not math.isfinite(contribs[0]) or contribs[0] < 0:
            raise ValueError(
                f"contributions must be a finite, non-negative number, got "
                f"{contributions!r}")
        # The shorthand takes its period count from `distributions`, so it
        # cannot notice a series that starts at YEAR 1 instead of at
        # close — and that is the series the rest of the pipeline hands
        # out. `project_cash_flows` returns `cash_flows[0]` as the
        # negative basis, so the obvious `cash_flows[1:]` is exactly
        # hold_years long and lands year 1's cash at period 0, where it
        # is distributed before any pref has accrued. Measured on a
        # 5-year, $4.7M-equity deal: LP IRR 14.1563% against the correct
        # 11.2437%, and $102,308 of extra promote, with nothing said.
        #
        # There is no way to tell the two readings apart from the values,
        # so the shorthand refuses the ambiguous one. Cash genuinely
        # distributed at close is rare and stays expressible — pass
        # `contributions` as an explicit period-indexed list.
        if dists[0] > 0:
            raise ValueError(
                f"distributions[0] is {dists[0]:,.2f}, but period 0 is the "
                "CLOSE date and the scalar `contributions` shorthand cannot "
                "tell a close-date distribution from a series that starts at "
                "year 1. If this series starts at year 1, prepend 0.0 for "
                "close. If cash really was distributed at close, pass "
                "`contributions` as an explicit period-indexed list.")
    else:
        contribs = _as_amounts("contributions", contributions)
        if len(contribs) != len(dists):
            raise ValueError(
                f"contributions has {len(contribs)} periods and "
                f"distributions has {len(dists)} — they are period-indexed "
                "and must align. Pass a single number for contributions if "
                "all equity is funded at close.")
    return contribs, dists


def _irr(cash_flows):
    """IRR, or None when it does not converge.

    Never NaN: `webapp.services.json_safe` exists because
    `json.dumps(nan)` is invalid JSON that Postgres JSONB rejects, and
    E3 persists these results. Same handling as
    `analysis.valuation.project_cash_flows`.
    """
    try:
        irr = npf.irr(cash_flows)
    except (ValueError, FloatingPointError):
        return None
    if irr is None or irr != irr:                    # NaN check
        return None
    return float(irr)


#: What the `status` on a stamp row means. Three states, not two, because
#: "we read the LPA and it says this" and "this cannot move a dollar given
#: something else we read" are different claims, and collapsing them would
#: let a moot question borrow a confirmation it never received.
STATUS_CONFIRMED = "confirmed"   # the LPA was read on this question
STATUS_MOOT = "moot"             # cannot change the number, given a confirmation
STATUS_OPEN = "open"             # still a build default standing in for the LPA


def _stamp_status(key, terms):
    """`confirmed` / `moot` / `open` for one stamp row.

    Reads `config.LPA_CONFIRMED` live rather than at import, so a run
    under `_patched_config` sees the same answer the rest of the model
    does. Unknown keys fall through to `open` — the safe direction, since
    a convention that quietly inherited someone else's confirmation would
    print as settled while still being a guess.
    """
    if key in (getattr(cfg, "LPA_CONFIRMED", None) or {}):
        return STATUS_CONFIRMED
    # `ordering` only moves a dollar when the pref is simple. Once the
    # compounding question is CONFIRMED to be compounding, ROC-before-pref
    # versus pref-first is arithmetically inert — so it is moot, not
    # confirmed. Nobody read the LPA's ordering clause; it just stopped
    # mattering. If compounding is merely defaulted, this stays open.
    if (key == "ordering"
            and terms.pref_compounding != COMPOUNDING_SIMPLE
            and "pref_compounding" in (getattr(cfg, "LPA_CONFIRMED", None) or {})):
        return STATUS_MOOT
    return STATUS_OPEN


def assumption_stamp(terms: WaterfallTerms) -> list:
    """The five LPA questions, how this run answered them, and whether
    anyone has actually read the document on each.

    The scope contract's rule: "Do not let an LP net IRR leave the
    building without its stamp." Each row carries a `status` —
    `confirmed` (the LPA was read; `config.LPA_CONFIRMED` records when),
    `moot` (cannot move the number given a confirmation elsewhere), or
    `open` (a build default standing in for the document). A figure whose
    stamp is all-`open` is a labeled assumption; one with confirmations is
    that much closer to decision-grade, and the reader is entitled to see
    which.
    """
    rows = [
        {"key": "pref_compounding",
         "question": "Pref simple or compounded, and at what frequency",
         "value": terms.pref_compounding,
         "label": f"{terms.pref_rate:.2%} pref, "
                  f"{COMPOUNDING_LABELS[terms.pref_compounding].lower()}"},
        {"key": "accrual_base",
         "question": "Accrual base: contributed/unreturned or committed",
         "value": terms.accrual_base,
         "label": ACCRUAL_BASE_LABELS[terms.accrual_base]},
        {"key": "ordering",
         "question": "Return of capital before pref, or pref first",
         "value": terms.ordering,
         "label": ORDERING_LABELS[terms.ordering]
                  + ("" if terms.pref_compounding == COMPOUNDING_SIMPLE
                     else " (presentation only: the pref compounds, so no "
                          "dollar moves)")},
        {"key": "am_fee_treatment",
         "question": "AM fee above the waterfall or netted from LP",
         "value": terms.am_fee_treatment,
         # No rate and no base, because this module does not charge the
         # fee and inventing a config key it never reads would be a
         # constant that goes stale. E3 charges it upstream and must
         # extend this row with the rate and the base it used — see the
         # plan doc's handoff note. Saying so is better than a label that
         # reads complete.
         "label": AM_FEE_LABELS[terms.am_fee_treatment]
                  + " — rate and base set by the caller, not this module"},
        {"key": "promote_basis",
         "question": "Promote on 100% of residual or LP-attributable only",
         "value": "lp_attributable",
         "label": f"{terms.promote_split:.0%} promote on the "
                  f"LP-attributable residual "
                  f"({1 - terms.gp_coinvest_pct:.0%} of it)"},
    ]
    for row in rows:
        row["status"] = _stamp_status(row["key"], terms)
        if row["status"] == STATUS_CONFIRMED:
            row["confirmed_on"] = cfg.LPA_CONFIRMED[row["key"]]
    return rows


def run_waterfall(contributions, distributions,
                  terms: WaterfallTerms = None) -> dict:
    """Distribute `distributions` against `contributions` through one
    preferred-return tier and a promoted residual.

    Both series are period-indexed with period 0 at close; a bare number
    for `contributions` means all equity is funded then. Amounts are
    TOTAL equity and TOTAL distributable cash — the GP's co-invest share
    is carved out of them by `terms.gp_coinvest_pct`, never added to
    them.

    **GP and LP ride tier 1 pari passu, and the split is exact.** Both
    accrue at the same rate on the same proportion of every
    contribution, so their claims stay in the fixed ratio
    `gp_coinvest_pct : 1 - gp_coinvest_pct` for as long as tier-1
    dollars are also split pro rata — which they are. So the accrual is
    rolled once, in aggregate, and each tier-1 payment is split by that
    ratio. Two separately rolled accounts give identical numbers with
    twice the arithmetic to audit.

    **The promote is charged on the LP-attributable residual only.** The
    LP receives `(1-c) x R x (1-x)`, the GP `c x R + (1-c) x R x x`.
    Charging the promote on 100% of the residual would pay the GP a
    promote on its own co-invested capital.

    Returns a dict, not the `WaterfallResult` dataclass the design doc
    names: every other builder in the model layer returns one, and the
    memo writer, Excel writer, Django templates and
    `webapp.services.json_safe` all consume dicts.
    """
    terms = terms or resolve_waterfall_terms()
    contribs, dists = _align_series(contributions, distributions)

    gp_share = float(terms.gp_coinvest_pct)
    lp_share = 1.0 - gp_share
    pref_rate = float(terms.pref_rate)
    compounded = terms.pref_compounding == COMPOUNDING_ANNUAL

    if sum(contribs) <= 0 and sum(dists) > 0:
        # Not an error — the arithmetic is consistent — but a promote on
        # a deal where nobody funded equity is a profit split, not this
        # structure, and it should not pass unremarked.
        logger.warning(
            "waterfall run with no contributed capital: the preferred "
            "return has a base of zero, so every dollar distributed is "
            "residual and carries the promote.")

    balance = 0.0          # compounded: capital + capitalized pref, one claim
    capital = 0.0          # unreturned capital (a memo under compounding)
    unpaid_pref = 0.0      # simple only; compounded pref lives in `balance`

    rows, lp_flows, gp_flows = [], [], []

    for period, (contribution, cash) in enumerate(zip(contribs, dists)):
        # 1. Accrue for the period just elapsed, on the unreturned
        #    capital at its START. Period 0 has no elapsed period, so
        #    capital contributed at close first earns at period 1.
        accrued = 0.0
        if period > 0:
            accrued = (balance if compounded else capital) * pref_rate
            if compounded:
                balance += accrued
            else:
                unpaid_pref += accrued

        # 2. Capital called this period joins the base and starts
        #    earning next period.
        capital += contribution
        if compounded:
            balance += contribution

        # 3. Tier 1 — return of capital and preferred return.
        if compounded:
            # One balance, one claim: the design doc's proof that a
            # compounded pref IS the IRR hurdle. The capital/pref split
            # below is presentation only — it exists so the memo can
            # print a Return-of-Capital row and a Preferred-Return row,
            # and no LP or GP dollar depends on which way it falls.
            #
            # It still follows `ordering`, because the memo rows are read
            # beside the assumption stamp. Applying pref first under a
            # stated "return of capital first" printed $0.00 of capital
            # returned for four consecutive years under a convention
            # saying capital comes first, which an auditor cannot
            # reconcile even though every dollar was right.
            tier1 = min(cash, balance)
            if terms.ordering == ORDERING_ROC_FIRST:
                capital_returned = min(tier1, capital)
                pref_paid = tier1 - capital_returned
            else:
                pref_paid = min(tier1, max(0.0, balance - capital))
                capital_returned = tier1 - pref_paid
            balance -= tier1
            capital = max(0.0, capital - capital_returned)
            cash -= tier1
            tier1_current = balance <= BALANCE_TOLERANCE
        elif terms.ordering == ORDERING_ROC_FIRST:
            capital_returned = min(cash, capital)
            capital -= capital_returned
            cash -= capital_returned
            pref_paid = min(cash, unpaid_pref)
            unpaid_pref -= pref_paid
            cash -= pref_paid
            tier1 = capital_returned + pref_paid
            tier1_current = (capital <= BALANCE_TOLERANCE
                             and unpaid_pref <= BALANCE_TOLERANCE)
        else:
            pref_paid = min(cash, unpaid_pref)
            unpaid_pref -= pref_paid
            cash -= pref_paid
            capital_returned = min(cash, capital)
            capital -= capital_returned
            cash -= capital_returned
            tier1 = capital_returned + pref_paid
            tier1_current = (capital <= BALANCE_TOLERANCE
                             and unpaid_pref <= BALANCE_TOLERANCE)

        # 4. Residual, and the promote on the LP's share of it. No
        #    promote is paid in any period where tier 1 is not current.
        #    A later capital call re-opens tier 1 and the promote is not
        #    recovered — no clawback, per the fund terms — which
        #    `interim_promote` reports rather than hides.
        residual = cash
        if residual > BALANCE_TOLERANCE and not tier1_current:
            # Unreachable: tier 1 is paid with min(), so cash survives
            # only once the claim it was measured against reached zero.
            # Logged rather than dropped — mislabeling dollars is bad,
            # losing them is worse.
            logger.error(
                "period %s left $%.2f undistributed with tier 1 still "
                "outstanding — reporting it as residual", period, residual)

        lp_residual = residual * lp_share
        gp_residual = residual - lp_residual
        promote = lp_residual * float(terms.promote_split)

        lp_contribution = contribution * lp_share
        gp_contribution = contribution - lp_contribution
        lp_distribution = tier1 * lp_share + lp_residual - promote
        gp_distribution = tier1 * gp_share + gp_residual + promote

        lp_flows.append(lp_distribution - lp_contribution)
        gp_flows.append(gp_distribution - gp_contribution)

        rows.append({
            "period": period,
            "contribution": contribution,
            "lp_contribution": lp_contribution,
            "gp_contribution": gp_contribution,
            "distribution": dists[period],
            "pref_accrued": accrued,
            "pref_paid": pref_paid,
            "capital_returned": capital_returned,
            "tier1_paid": tier1,
            "residual": residual,
            "gp_promote": promote,
            "lp_distribution": lp_distribution,
            "gp_distribution": gp_distribution,
            "ending_unreturned_capital": capital,
            "ending_unpaid_pref": (max(0.0, balance - capital) if compounded
                                   else unpaid_pref),
            "ending_balance": balance if compounded else capital + unpaid_pref,
            "tier1_current": tier1_current,
        })

    lp_contributed = sum(r["lp_contribution"] for r in rows)
    gp_contributed = sum(r["gp_contribution"] for r in rows)
    lp_distributed = sum(r["lp_distribution"] for r in rows)
    gp_distributed = sum(r["gp_distribution"] for r in rows)
    ending_pref = max(0.0, balance - capital) if compounded else unpaid_pref

    # What a clawback would have recovered and these terms do not:
    # promote paid before a later capital call, capped at the tier-1
    # shortfall the deal actually ends with.
    #
    # The cap is the whole point. Without it the flag fired on a deal
    # that took a call, re-cleared tier 1 in full and returned the LP
    # 1.78x — a clawback caveat on the memo of a deal where nothing was
    # owed. A clawback only ever recovers up to the ending shortfall, so
    # that is the honest number, and it is zero exactly when the LP is
    # whole.
    shortfall = max(0.0, capital) + max(0.0, ending_pref)
    last_call = max((r["period"] for r in rows
                     if r["contribution"] > 0), default=-1)
    promote_before_last_call = sum(r["gp_promote"] for r in rows
                                   if r["period"] < last_call)
    unrecovered_promote = min(promote_before_last_call, shortfall)
    if unrecovered_promote > BALANCE_TOLERANCE:
        logger.warning(
            "$%.2f of promote was paid before capital was called in period "
            "%s, and the deal ends $%.2f short of returning capital plus "
            "preferred return. These fund terms carry no clawback, so the GP "
            "keeps it.", unrecovered_promote, last_call, shortfall)

    return {
        # A dict, not the frozen dataclass. `webapp.services.json_safe`
        # falls back to `str(obj)` on anything it does not recognise, so
        # the object persisted to JSONB as the string
        # "WaterfallTerms(pref_rate=0.08, ...)" — unqueryable, and a
        # consumer reading `["terms"]["pref_rate"]` got "string indices
        # must be integers". The caller that passed `terms` in still has
        # the object; `WaterfallTerms(**result["terms"])` rebuilds it.
        "terms": asdict(terms),
        "periods": rows,
        "assumption_stamp": assumption_stamp(terms),
        "total_contributions": sum(contribs),
        "total_distributions": sum(dists),
        "lp": {
            "contributions": lp_contributed,
            "distributions": lp_distributed,
            "cash_flows": lp_flows,
            "irr": _irr(lp_flows),
            "moic": (lp_distributed / lp_contributed
                     if lp_contributed > 0 else None),
        },
        "gp": {
            "contributions": gp_contributed,
            "distributions": gp_distributed,
            "cash_flows": gp_flows,
            "irr": _irr(gp_flows),
            "moic": (gp_distributed / gp_contributed
                     if gp_contributed > 0 else None),
            "promote": sum(r["gp_promote"] for r in rows),
        },
        # What tier 1 never got paid. Nonzero means the deal did not
        # return capital plus pref, so the promote is zero and the LP is
        # short — state it rather than letting a 0% promote read as
        # "there was nothing above the hurdle".
        "unreturned_capital": capital,
        "unpaid_pref": ending_pref,
        "tier1_current": (capital <= BALANCE_TOLERANCE
                          and ending_pref <= BALANCE_TOLERANCE),
        # Nonzero means the GP was promoted on a period that a later
        # capital call re-opened AND the deal ends short. Not
        # recoverable — these fund terms have no clawback — so any
        # consumer showing the promote must show this beside it. Zero
        # when the LP ends whole, which is the common case.
        "unrecovered_promote": unrecovered_promote,
    }
