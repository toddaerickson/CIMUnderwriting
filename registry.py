"""
Central Registry — single source of truth for repeated constants.

Eliminates duplicated string constants, keyword lists, and magic numbers
that were previously scattered across 8+ files.
"""

import datetime
from dataclasses import dataclass
from enum import Enum


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

    low, high = cfg.EXPENSE_BENCHMARKS["opex_revenue_ratio"]
    tolerance = cfg.EXPENSE_RATIO["clamp_tolerance"]
    # Rounded because the subtraction is not exact in binary: 0.35 − 0.10
    # is 0.24999999999999997, and a clamp floor a quintillionth below the
    # value it is supposed to be makes `clamp(0.10) == 0.25` false. Ten
    # decimals is finer than any input can be — the settings form rounds
    # a stored override to six — and coarser than float noise.
    return (round(low - tolerance, 10), round(high + tolerance, 10))


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
