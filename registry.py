"""
Central Registry — single source of truth for repeated constants.

Eliminates duplicated string constants, keyword lists, and magic numbers
that were previously scattered across 8+ files.
"""

import datetime
import logging
import math
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("cim_analyst")


# ── Scenario Names ─────────────────────────────────────────────────

class ScenarioType(str, Enum):
    """
    Scenario names. Extends str so they work as dict keys and
    compare equal to plain strings (e.g., ScenarioType.BASE == "base").
    """
    BEAR = "bear"
    BASE = "base"
    BULL = "bull"


# ── Expense Categories ─────────────────────────────────────────────

@dataclass(frozen=True)
class ExpenseCategoryDef:
    """Definition of a single expense category."""
    key: str                # config/DB key: "property_tax"
    display_name: str       # human-readable: "Property Taxes"
    parse_keywords: tuple   # keywords for CIM label matching


EXPENSE_CATEGORIES = [
    ExpenseCategoryDef(
        "property_tax", "Property Taxes",
        ("property tax", "real estate tax", "taxes")),
    ExpenseCategoryDef(
        "insurance", "Insurance",
        ("insurance",)),
    ExpenseCategoryDef(
        "utilities", "Utilities",
        ("utilit", "electric", "water", "gas")),
    ExpenseCategoryDef(
        "repairs", "Repairs & Maintenance",
        ("repair", "maintenance", "r&m")),
    ExpenseCategoryDef(
        "advertising", "Advertising",
        ("advertis", "marketing")),
    ExpenseCategoryDef(
        "payroll", "Payroll",
        ("payroll", "salary", "wages", "personnel", "labor")),
    ExpenseCategoryDef(
        "ga", "General & Administrative",
        ("general", "admin", "g&a", "office")),
    ExpenseCategoryDef(
        "cap_reserve", "Capital Reserve",
        ("reserve", "replacement", "capex")),
]

# Convenience lookups derived from the single source list
EXPENSE_KEYS = [c.key for c in EXPENSE_CATEGORIES]
EXPENSE_DISPLAY_MAP = {c.key: c.display_name for c in EXPENSE_CATEGORIES}
EXPENSE_KEYWORD_MAP = {c.key: list(c.parse_keywords) for c in EXPENSE_CATEGORIES}


# ── Expense Ratio Defaults ─────────────────────────────────────────

#: Hard limits on the DERIVED clamp, which is the one thing the
#: settings page cannot bound for itself.
#:
#: `_bounds_for` bounds each editable field on its own shape, and both
#: inputs to the clamp are shares in (0, 1) — individually legal at every
#: value. Their SUM is not: `opex_revenue_ratio` high of 0.90 plus a
#: `clamp_tolerance` of 0.10 is a clamp ceiling of exactly 1.0, and
#: `analysis/valuation.py` then computes `yr1_noi / (1 - ratio)` on a
#: deal whose expenses reach revenue — a ZeroDivisionError, a 500, and
#: two settings edits that each passed validation to get there.
#:
#: This is a defect the derivation INTRODUCED. The clamp used to be the
#: constant `(0.25, 0.65)`, which no operator could reach at all; making
#: it follow the band is what put a composed value in reach.
#:
#: 0.95 rather than something closer to 1: at a 95% expense ratio the
#: implied revenue is twenty times NOI, which is already far past any
#: credible deal, so nothing real is being clipped. The floor at 0.0 is
#: the same argument from the other side — a band low under the tolerance
#: derives a NEGATIVE clamp floor, which would let a deal be underwritten
#: on expenses below zero.
EXPENSE_RATIO_LIMITS = (0.0, 0.95)

#: Messages already emitted, so a misconfiguration is reported once
#: rather than once per evaluation.
#:
#: `expense_ratio_clamp` is called from `clamp_expense_ratio`, which
#: `project_cash_flows` calls on EVERY projection — up to 50 bisection
#: iterations in each of three solvers, plus 81 sensitivity cells, plus
#: three scenarios. An unthrottled warning is ~200 identical lines for
#: one deal, which buries the one line anybody needed to read.
#:
#: Keyed on the MESSAGE, so a different misconfiguration still speaks
#: up. It does NOT re-warn for a combination already reported in this
#: process, even if the setting was corrected and broken again the same
#: way — the entry never expires. That is deliberate (the condition
#: describes config, not a deal, so repeating it says nothing new) and
#: it is stated here because an earlier version of this comment claimed
#: the opposite, which an audit caught by testing the claim.
#:
#: A race between threads costs a duplicate line, the harmless direction.
_WARNED = set()


def _warn_once(message: str) -> None:
    if message not in _WARNED:
        _WARNED.add(message)
        logger.warning("%s", message)


def _finite(value):
    """-> float, or None if `value` is not a usable finite number.

    The settings form rejects both cases, so this only fires on a row
    that reached the database another way — an older schema, a hand-run
    UPDATE, a fixture. `build_config_patch` applies a stored value
    without re-checking its bounds, so the form is not the last line of
    defence it looks like.

    NaN is the one worth naming: it is not caught by any comparison
    (`nan > 0` and `nan < 0` are both False, and `min`/`max` pass it
    straight through), so it would flow into the projection, out through
    `json_safe` as a null, and surface as an empty cell rather than as a
    problem.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def expense_ratio_clamp() -> tuple[float, float]:
    """The (low, high) a stated OpEx/Revenue ratio is believed within.

    DERIVED from `EXPENSE_BENCHMARKS["opex_revenue_ratio"]` widened by
    `EXPENSE_RATIO["clamp_tolerance"]`, rather than restated — see the
    argument beside `config.EXPENSE_RATIO`. It used to be the module
    constant `EXPENSE_RATIO_CLAMP = (0.25, 0.65)`, which is what the
    band ± 0.10 evaluates to today.

    Config is imported INSIDE the function on purpose: `config.py`
    imports `ScenarioType` from this module, so a module-level import
    here is a cycle. It also happens to be what makes the value live —
    `EXPENSE_BENCHMARKS` is a `_PATCHED_DICTS` entry mutated in place for
    the duration of one run, and only a call-time read can see the patch.
    """
    import config as cfg

    floor, ceiling = EXPENSE_RATIO_LIMITS

    # The two inputs are VALIDATED, not trusted, and that is what makes
    # the ordering argument below a fact rather than a hope. An audit
    # broke the previous version with `clamp_tolerance = -0.5`: a
    # negative tolerance NARROWS the band instead of widening it, so
    # (0.35, 0.55) derived (0.85, 0.05) — inverted, and with both
    # endpoints inside the limits, so the bound below saw nothing wrong
    # and no warning fired. `clamp_expense_ratio` then returned a
    # constant 0.85 for every input, having stopped reading its argument
    # entirely. Silent, and wrong in the expensive direction.
    #
    # Only reachable off-form — `_bounds_for` refuses a negative
    # tolerance and a NaN — but `build_config_patch` applies a STORED
    # override without re-checking its bounds, so a row written by an
    # older version, a fixture or a hand-run UPDATE arrives here
    # unvalidated. The house rule for stored overrides is this one:
    # log and carry on, never take a run down (`resolve_capital_structure`
    # settles an unknown basis the same way).
    band = cfg.EXPENSE_BENCHMARKS["opex_revenue_ratio"]
    try:
        low, high = (_finite(band[0]), _finite(band[1]))
    except (TypeError, KeyError, IndexError, ValueError):
        low = high = None
    if low is None or high is None or low > high:
        # No credible band to widen. Falling back to the LIMITS rather
        # than to some invented pair: the limits are the widest interval
        # that keeps `1 - ratio` positive, so the clamp stops clipping
        # instead of clipping to a number nobody chose.
        _warn_once(
            f"EXPENSE_BENCHMARKS['opex_revenue_ratio'] is {band!r}, which "
            f"is not a usable (low, high) pair — the OpEx/revenue clamp "
            f"falls back to {EXPENSE_RATIO_LIMITS} and clips nothing.")
        return (floor, ceiling)

    tolerance = _finite(cfg.EXPENSE_RATIO["clamp_tolerance"])
    if tolerance is None or tolerance < 0:
        _warn_once(
            f"EXPENSE_RATIO['clamp_tolerance'] is "
            f"{cfg.EXPENSE_RATIO['clamp_tolerance']!r}; a tolerance widens "
            f"the band and cannot be negative or non-numeric — using 0, so "
            f"the clamp is the benchmark band itself.")
        tolerance = 0.0

    # Rounded because the subtraction is not exact in binary: 0.35 − 0.10
    # is 0.24999999999999997, and a clamp floor a quintillionth below the
    # value it is supposed to be makes `clamp(0.10) == 0.25` false. Ten
    # decimals is finer than any input can be — the settings form rounds
    # a stored override to six — and coarser than float noise.
    derived = (round(low - tolerance, 10), round(high + tolerance, 10))

    # The composed value is bounded here because nothing upstream can
    # bound it — see EXPENSE_RATIO_LIMITS. Logged rather than raised: a
    # stored override must not take a run down, and the settings page
    # cannot cross-validate two rows an operator edits months apart. The
    # repo already settles this the same way for the density tiers — the
    # guard is that the OUTPUT stays coherent, not that the inputs are
    # policed.
    #
    # BOTH ends go through the same clamp, and that is the whole
    # correctness argument. Bounding only the high end — which is what
    # the first version of this did — leaves the low end free to climb
    # past the ceiling: a band of (1.0, 1.0) with a zero tolerance
    # derived (1.0, 1.0) and bounded it to (1.0, 0.95), an INVERTED pair.
    # `max(lo, min(hi, r))` then returns `lo` for every input, so the
    # clamp stopped reading its own argument and handed back 1.0 — the
    # exact ZeroDivisionError the limits exist to prevent, now reached
    # THROUGH the guard. Clamping both ends with one monotone function
    # cannot invert them, and `derived` is now ordered BY CONSTRUCTION —
    # the block above rejects a band with low > high and a negative
    # tolerance, which were the only two ways the pair could arrive out
    # of order. A monotone map preserves that order.
    bounded = tuple(min(max(v, floor), ceiling) for v in derived)
    if bounded != derived:
        _warn_once(
            "the OpEx/revenue clamp derived from the benchmark band and "
            f"EXPENSE_RATIO['clamp_tolerance'] is {derived}, outside the "
            f"limits {EXPENSE_RATIO_LIMITS} that keep `1 - ratio` a "
            f"positive number — using {bounded}. Narrow the "
            "opex_revenue_ratio band or the tolerance.")
    return bounded


def clamp_expense_ratio(ratio: float | None) -> float:
    """Apply default and clamp to the expense ratio.

    `None` means the financials produced no ratio at all, not zero — a
    property with no expenses is not what a missing figure describes. It
    resolves to `EXPENSE_RATIO["default"]` and is then clamped like any
    other value: the clamp is the range the model believes, and a default
    outside it would be a number the model does not believe in.

    Called from `analysis.valuation.project_cash_flows`, which is the ONE
    projection — so this is the single point where the assumed expense
    load enters every scenario, every sensitivity cell and every solver
    iteration.
    """
    import config as cfg

    r = ratio if ratio is not None else cfg.EXPENSE_RATIO["default"]
    lo, hi = expense_ratio_clamp()
    return max(lo, min(hi, r))


# ── Asset Class ────────────────────────────────────────────────────

#: The exact strings detect_asset_type can return — single source for the
#: settings editor's scope dropdown, the ConfigOverride scope key, and the
#: MARKET_CAP_RATES rows. Lives here rather than in webapp.services so the
#: Django-free engine can classify an asset too; webapp.services re-exports
#: it, and tests/test_web_config.py guards against drift.
ASSET_TYPES = (
    "Self Storage",
    "Climate-Controlled Self Storage",
    "Boat & RV Storage",
)

#: What an asset is when nothing in the CIM says otherwise. Detection reads
#: brv_*_sf and cc_pct, neither of which the parser reliably fills, so this
#: is reached by ABSENCE of evidence — worth remembering wherever the class
#: drives a number.
DEFAULT_ASSET_TYPE = ASSET_TYPES[0]


def detect_asset_type(cim_data) -> str:
    """Determine asset type from CIM data fields."""
    brv_sf = sum(filter(None, [
        getattr(cim_data, "brv_enclosed_sf", None),
        getattr(cim_data, "brv_covered_sf", None),
        getattr(cim_data, "brv_open_sf", None),
    ]))
    if brv_sf > 0:
        return "Boat & RV Storage"

    cc_pct = getattr(cim_data, "cc_pct", None)
    if cc_pct is not None and cc_pct > 0.5:
        return "Climate-Controlled Self Storage"

    return DEFAULT_ASSET_TYPE


# ── Asset Age ──────────────────────────────────────────────────────

#: Age bands, in order, as (exclusive upper bound in years, key). The
#: ladder comes from the narrative in analysis/physical.py, which was the
#: only place it existed; the exit-cap table keys off these, so it is now
#: load-bearing and belongs in one place. `physical.py`, `risks.py` and
#: `value_add.py` each inlined `date.today().year - year_built`.
AGE_BANDS = ((6, "new"), (16, "mid"), (31, "aging"), (None, "old"))
AGE_BAND_LABELS = {
    "new":   "0-5 yrs — modern construction",
    "mid":   "6-15 yrs — mid-life asset",
    "aging": "16-30 yrs — aging asset",
    "old":   "31+ yrs — significant age",
}


def asset_age(year_built, as_of=None) -> int | None:
    """Age in years, or None if the vintage is unknown.

    `as_of` takes a date (or a year) so a re-run reproduces the age the
    original run used. Defaulting to today is right for a live analysis
    and wrong for re-deriving one from a stored result, which is why the
    caller can pin it.
    """
    if not year_built:
        return None
    if as_of is None:
        year = datetime.date.today().year
    else:
        year = getattr(as_of, "year", as_of)
    return max(0, int(year) - int(year_built))


def age_band(year_built, as_of=None) -> str | None:
    """-> one of AGE_BAND_LABELS, or None when the vintage is unknown.

    None is deliberate: an unknown vintage must not silently land in a
    band and price the exit off a guess. Callers decide what to do with
    it (the exit-cap resolver falls back to the oldest band and says so).
    """
    age = asset_age(year_built, as_of)
    if age is None:
        return None
    for upper, key in AGE_BANDS:
        if upper is None or age < upper:
            return key
    return AGE_BANDS[-1][1]


# ── Unit Size Buckets ──────────────────────────────────────────────

# Standard self-storage unit sizes and their approximate SF
STANDARD_SIZE_BUCKETS = {
    "5x5":   25,
    "5x10":  50,
    "5x15":  75,
    "10x10": 100,
    "10x15": 150,
    "10x20": 200,
    "10x25": 250,
    "10x30": 300,
}

# Match tolerance: unit SF must be within this % of standard to assign bucket
SIZE_BUCKET_TOLERANCE = 0.20
