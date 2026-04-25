"""
Central Registry — single source of truth for repeated constants.

Eliminates duplicated string constants, keyword lists, and magic numbers
that were previously scattered across 8+ files.
"""

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

DEFAULT_EXPENSE_RATIO = 0.40
EXPENSE_RATIO_CLAMP = (0.25, 0.65)


def clamp_expense_ratio(ratio: float | None) -> float:
    """Apply default and clamp to the expense ratio.

    Used in valuation.py, returns_model.py, and solver.py to avoid
    duplicating the same 2-line pattern.
    """
    r = ratio if ratio is not None else DEFAULT_EXPENSE_RATIO
    lo, hi = EXPENSE_RATIO_CLAMP
    return max(lo, min(hi, r))


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
