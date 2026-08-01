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
   the GP earns its promote. Paying no promote until tier 1 is current
   in every period is what removes the need for a clawback structurally
   rather than contractually.

**Nothing outside `tests/` imports this yet.** E2 ships the engine; E3
wires it into the assumptions form, results, memo and Excel.
`tests/test_waterfall.py` asserts that, which is the proof no published
unlevered number moved.

Numeric authority: `docs/levered-waterfall-design.md` oracles 1-3,
reproduced to the cent in the test module. Five LPA questions are still
open (`docs/scoped-backlog.md` item E); each ships as a named parameter
carrying a documented default, and `assumption_stamp` in the result
carries the resolved set so no LP net IRR is ever displayed without it.
"""

import logging
import math
from dataclasses import dataclass, fields

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

        NaN is rejected explicitly, for the reason `DebtTerms` records:
        every comparison against NaN is False, so it walks past a
        range guard untouched and then poisons the arithmetic WITHOUT
        raising — a NaN `pref_rate` produces a NaN accrual, a NaN
        balance, a residual of NaN, and a promote of NaN reported as a
        number.
        """
        for name in _FLOAT_FIELDS:
            value = getattr(self, name)
            if value is None or not math.isfinite(float(value)):
                raise ValueError(
                    f"{name} must be a finite number, got {value!r} — NaN "
                    "passes every ordinary guard and then produces a "
                    "confident wrong answer instead of an error.")

        if float(self.pref_rate) < 0:
            raise ValueError(
                f"pref_rate cannot be negative, got {self.pref_rate!r} — a "
                "negative preferred return pays the LP for waiting.")
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

        if self.catch_up:
            raise NotImplementedError(
                "a GP catch-up tier is not supported — the operator scoped "
                "this waterfall to ONE tier (docs/scoped-backlog.md item "
                "E). Without a catch-up the GP never recovers pref "
                "leakage, and the promote applies to the residual only.")


def resolve_waterfall_terms(overrides: dict = None) -> WaterfallTerms:
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

    `gp_coinvest_pct` is seeded from `config.GP_COINVEST_PCT`, the same
    scalar `model.returns_model.resolve_capital_structure` reads for the
    capital stack, so the waterfall and Sources & Uses cannot disagree
    about how much of the equity is the GP's. It is deliberately NOT a
    key in `config.WATERFALL_TERMS`.

    Config is read at CALL time, never bound at import, so a test or a
    future settings path that rebinds it is seen.
    """
    resolved = dict(cfg.WATERFALL_TERMS)
    resolved["gp_coinvest_pct"] = cfg.GP_COINVEST_PCT
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
    if "catch_up" in resolved:
        resolved["catch_up"] = bool(resolved["catch_up"])
    return WaterfallTerms(**resolved)


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
    if isinstance(distributions, (int, float)) or distributions is None:
        raise TypeError(
            "distributions must be a period-indexed sequence (index 0 = "
            f"close), got {distributions!r}")
    dists = [float(d or 0.0) for d in distributions]
    if not dists:
        raise ValueError("distributions is empty — there is nothing to "
                         "distribute and no period to distribute it in.")

    if isinstance(contributions, (int, float)):
        contribs = [float(contributions)] + [0.0] * (len(dists) - 1)
    else:
        contribs = [float(c or 0.0) for c in contributions]
        if len(contribs) != len(dists):
            raise ValueError(
                f"contributions has {len(contribs)} periods and "
                f"distributions has {len(dists)} — they are period-indexed "
                "and must align. Pass a single number for contributions if "
                "all equity is funded at close.")

    for label, series in (("contributions", contribs),
                          ("distributions", dists)):
        for period, value in enumerate(series):
            if not math.isfinite(value):
                raise ValueError(
                    f"{label}[{period}] must be a finite number, got "
                    f"{value!r} — a NaN here produces a NaN promote "
                    "reported as a number.")
            if value < 0:
                raise ValueError(
                    f"{label}[{period}] cannot be negative, got {value!r}. A "
                    "negative distribution is a capital call and a negative "
                    "contribution is a distribution; netting them silently "
                    "corrupts the accrual base.")
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


def assumption_stamp(terms: WaterfallTerms) -> list:
    """The five open LPA questions and how this run answered them.

    The scope contract's rule: "Do not let an LP net IRR leave the
    building without its stamp." Every one of these still changes the
    number, so a displayed LP net IRR is a labeled assumption, not a
    decision-grade figure, until the LPA is read.
    """
    return [
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
                     else " (no effect: the pref compounds)")},
        {"key": "am_fee_treatment",
         "question": "AM fee above the waterfall or netted from LP",
         "value": terms.am_fee_treatment,
         "label": AM_FEE_LABELS[terms.am_fee_treatment]},
        {"key": "promote_basis",
         "question": "Promote on 100% of residual or LP-attributable only",
         "value": "lp_attributable",
         "label": f"{terms.promote_split:.0%} promote on the "
                  f"LP-attributable residual "
                  f"({1 - terms.gp_coinvest_pct:.0%} of it)"},
    ]


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
            tier1 = min(cash, balance)
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
        #    promote is paid in any period where tier 1 is not current,
        #    which is what removes the need for a clawback structurally.
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

    return {
        "terms": terms,
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
    }
