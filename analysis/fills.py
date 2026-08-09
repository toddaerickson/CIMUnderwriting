"""Assumption fill log — every value this run invented, in one place.

Item T Category 4. The audit that opened item T named two corrosive
patterns. The first was the duplicated constant, which Categories 1-3
closed. This module closes the second: the **silent fallback**, an
expression like `cim_data.physical_occupancy or 0.80` that answers a
missing input with a number nobody chose for this deal, and then prices
the deal on it with nothing on any surface saying so.

A fallback is not automatically wrong. Underwriting an unstated expense
line at its benchmark floor is defensible; underwriting it at the floor
*while the memo reads as though the CIM stated it* is not. So this item
does not delete the estimates — deleting them would be re-underwriting,
which item T's own scope excludes. It makes them **say their name**:

    field       the CIM input that had no value
    value_used  what the run used instead
    source_key  WHERE that substitute came from, from a closed vocabulary
    label       the sentence a reader sees, in the memo and on screen

Three fallbacks are deleted rather than logged, because no honest label
exists for them: `nrsf or 1`, `ttm_noi or 100_000`, and an assumed
physical occupancy. A one-square-foot property and a $100k NOI are not
conservative estimates, they are fictions that silently rescale every
$/SF benchmark and every solver bracket in the run. Occupancy diverges
from the other two in HOW it is checked — `is None`, not falsy, because
a stated 0% is an honestly-reported pre-lease-up asset that the demand
gate already refuses correctly, while absence is what used to render as
a TBD gate and proceed. `require_underwritable` refuses the deal
instead, in all three cases.

## Where a fill is recorded

At the site that substitutes, never by a second module re-deciding what
the first one would have done — that is the duplicated-constant defect
in a new costume. `analyze_financials` and `analyze_physical` return
their fills on the dict they already return; the value-add engine
publishes its own on each scenario. `collect()` gathers those, adds the
handful derivable from provenance the pipeline already publishes
(`market_cap["age_band_known"]` and `["asset_class_known"]`, both stamped
by the code that made the choice), and returns one ordered, de-duplicated
log.

That mirrors `analysis.checks`: evaluated ONCE at the engine, then handed
to every surface, so the memo, the workbook and the results page cannot
report three different sets. It is deliberately NOT part of the check
register — a check asks "is this input self-consistent?" and answers
pass/fail/skipped, while a fill asks "where did this number come from?"
and has no failure axis. Forcing one into the other's vocabulary would
make `skipped` mean two things.

## What is NOT a fill

Coalescing an absent optional to zero where zero IS the semantic
(`other_income or 0`), skipping an incomplete unit-mix row, or a
divide-by-zero guard whose output is discarded. None of those invent a
magnitude. Nor is a REFUSAL a fill: when a per-unit CapEx rate loses the
unit count it multiplies, `engine.run_analysis` warns and books nothing —
the value was declined, not substituted, and that is a run warning. (It
used to say NRSF; `require_underwritable` refuses that deal outright now,
so the surviving case is a driver with no such gate.)
"""

from dataclasses import asdict, dataclass, field

import config as cfg

# ── Source-key vocabulary ───────────────────────────────────────────
# Closed set. A fill whose source is not one of these is a fill nobody
# labelled, and `test_every_fill_source_key_is_declared` says so.

BENCHMARK_LOW = "benchmark_low"
STATE_TAX_FORMULA = "state_tax_formula"
MGMT_FEE_TARGET = "mgmt_fee_target"
STATE_ABSENT = "state_absent"
CC_PCT_ABSENT = "cc_pct_absent"
AGE_BAND_FALLBACK = "age_band_fallback"
ASSET_CLASS_DEFAULT = "asset_class_default"
EXPENSE_RATIO_DEFAULT = "expense_ratio_default"
MARKET_RENT_ABSENT = "market_rent_absent"
EXPENSES_ABSENT = "expenses_absent"

#: What each source key means, in one clause, for the memo's appendix
#: legend and the results-page column. The `label` on a Fill says what
#: happened to THAT field; this says what the source itself is.
#:
#: DECLARATION ORDER IS RENDER ORDER — provenance of the whole asset
#: first, then the income statement, then the value-add engine's own
#: inputs, which is the order a reader asks the questions in. Keeping the
#: order and the prose in ONE dict rather than a tuple beside a dict is
#: not tidiness: two structures listing the same keys is the
#: duplicated-constant defect this very item exists to close, and it
#: would need a test whose only job is to police the two for drift.
#: (`SOURCE_KEYS` below is derived FROM this dict for exactly that
#: reason — its count can never drift from this one's.)
SOURCE_LABELS = {
    ASSET_CLASS_DEFAULT: "config default (asset class)",
    AGE_BAND_FALLBACK: "config default (age band)",
    STATE_ABSENT: "national benchmarks",
    CC_PCT_ABSENT: "assumed 0% climate-controlled",
    STATE_TAX_FORMULA: "state income-based tax formula",
    MGMT_FEE_TARGET: "config MGMT_FEE_TARGET_PCT",
    BENCHMARK_LOW: "benchmark floor x NRSF",
    EXPENSE_RATIO_DEFAULT: "config EXPENSE_RATIO default",
    MARKET_RENT_ABSENT: "in-place rent",
    EXPENSES_ABSENT: "no expenses booked",
}

#: The closed vocabulary, DERIVED from the table above so it cannot
#: disagree with it.
SOURCE_KEYS = tuple(SOURCE_LABELS)

# ── Units ───────────────────────────────────────────────────────────
# `format_number` is the ONE scalar formatter. The memo, the workbook and
# the results page all reach it, so a value cannot print as "0.8" in one
# place and "80%" in another.
#
# The vocabulary is DELIBERATELY wider than the fill log itself uses: item
# T Category 6's assumption register shares it, and the two registers
# describe overlapping numbers — the same expense line is a `Fill` when it
# was substituted and an `Assumption` when it was not. Two unit tables
# would let one register print $/SF/yr where the other printed $/SF/mo
# for one number, which is the divergence this item exists to close. A
# unit no `Fill` currently carries is not dead code here; it is the shared
# vocabulary's other half.

UNIT_DOLLARS = "$"
UNIT_PSF = "$/SF"
UNIT_PSF_MO = "$/SF/mo"
UNIT_PSF_YR = "$/SF/yr"
UNIT_PCT = "%"
UNIT_SF = "SF"
UNIT_YEARS = "yr"
UNIT_MONTHS = "mo"
UNIT_BPS = "bps"
UNIT_COUNT = "#"
UNIT_TEXT = ""


@dataclass(frozen=True)
class Fill:
    """One value the run invented because the CIM did not supply it.

    `detail` carries the raw inputs behind the substitution, the same way
    `CheckResult.values` does, so a reader can trace the number to its
    formula without re-deriving anything. It is rendered — see
    `format_detail` and the workbook's Inputs tab. An unrendered trace
    would be the very thing this module is named after.
    """
    field: str
    value_used: object
    source_key: str
    label: str
    unit: str = UNIT_TEXT
    detail: dict = field(default_factory=dict)

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source_key, self.source_key)


class MissingUnderwritingInput(ValueError):
    """The deal is missing an input no default can honestly stand in for.

    A `ValueError` so `webapp.services._analysis_worker`'s existing
    `except Exception` records it as a failed run with the message as its
    reason — no new plumbing, and the message IS the on-screen copy.
    """


def _absent_or_zero(value) -> bool:
    """Missing when falsy.

    `extract.parser._parse_number` returns 0.0 when it cannot read a
    figure, AND that 0.0 counts as populated in the extraction report. An
    `is None` gate would wave a 0-SF property straight into every
    division this refuses.
    """
    return not value


def _absent_only(value) -> bool:
    """Missing only when absent — a stated zero is data.

    Occupancy diverges from the two fields above deliberately (Category 5
    decision 3). A 0% physical occupancy is an honestly-reported
    pre-lease-up asset, and `analysis/filters.py` already refuses it as
    unproven demand with that as the stated reason. Refusing it here
    instead would give a correct outcome the wrong explanation.
    """
    return value is None


#: The three inputs with no defensible fallback, each with the predicate
#: that decides whether THIS field is missing. NRSF divides every
#: benchmark in the expense analysis and multiplies every $/SF rate; TTM
#: NOI sets the solver's search bracket; the occupancy gain is what a
#: value-add deal's return IS. Substituting any of them does not make the
#: answer approximate, it makes it unrelated to the asset.
REQUIRED_UNDERWRITING_FIELDS = (
    ("NRSF", "nrsf", _absent_or_zero),
    ("TTM NOI", "ttm_noi", _absent_or_zero),
    ("Physical Occupancy", "physical_occupancy", _absent_only),
)


def require_underwritable(cim_data) -> None:
    """Raise `MissingUnderwritingInput` unless the deal can be priced.

    Each field carries its own missing-ness test rather than sharing one,
    because 0.0 means "unreadable" for NRSF and "empty building" for
    occupancy. See `_absent_or_zero` and `_absent_only`.
    """
    missing = [label for label, attr, is_missing in REQUIRED_UNDERWRITING_FIELDS
               if is_missing(getattr(cim_data, attr, None))]
    if not missing:
        return
    raise MissingUnderwritingInput(
        f"{' and '.join(missing)} missing from this deal, so it cannot be "
        f"underwritten — every $/SF benchmark divides by NRSF, the solver's "
        f"price bracket derives from TTM NOI, and an assumed occupancy "
        f"invents the very gain a value-add return is made of. Enter "
        f"{'it' if len(missing) == 1 else 'them'} on the Assumptions page "
        f"and re-run.")


def format_number(value, unit: str) -> str:
    """The one rendering of a number in either register, unit included.

    Shared with `analysis.assumptions` (item T Category 6) rather than
    reimplemented there — see the note above the unit table. A non-numeric
    value renders as itself, which is how an enum-valued assumption
    (`loan_type`, `accrual_base`) and a text-valued fill both survive a
    formatter written for magnitudes.
    """
    if value is None:
        return "—"
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return str(value)
    if unit == UNIT_DOLLARS:
        return f"${value:,.0f}"
    if unit == UNIT_PSF_MO:
        return f"${value:,.2f}/SF/mo"
    if unit == UNIT_PSF:
        return f"${value:,.2f}/SF"
    if unit == UNIT_PSF_YR:
        return f"${value:,.2f}/SF/yr"
    if unit == UNIT_PCT:
        # One decimal, EXCEPT where one decimal would round information
        # away. A 6.25% coupon and a 25bp sensitivity step both live in
        # this unit alongside a 75% occupancy, and `.1%` prints them
        # "6.2%" and "0.2%" — a disclosure that quietly restates the
        # number it is disclosing. Every value that survives `.1%`
        # unharmed still renders exactly as it did before, so no existing
        # surface moves.
        pct = value * 100
        return (f"{pct:.1f}%" if round(pct, 1) == round(pct, 6)
                else f"{pct:g}%")
    if unit == UNIT_SF:
        return f"{value:,.0f} SF"
    if unit == UNIT_YEARS:
        return f"{value:g} yr"
    if unit == UNIT_MONTHS:
        return f"{value:g} mo"
    if unit == UNIT_BPS:
        return f"{value:g} bps"
    if unit == UNIT_COUNT:
        return f"{value:,.0f}"
    return f"{value:,.4g}"


def format_value(fill: "Fill") -> str:
    """The one rendering of a fill's value, unit included.

    Takes a `Fill`, never a stored dict: every surface already calls
    `from_dicts` first because it needs `source_label` too, so a dict
    branch here would be an unexercised path that silently formats
    something else the day one is passed. Handed a dict, this raises.
    """
    return format_number(fill.value_used, fill.unit)


def to_dicts(fills) -> list[dict]:
    """JSON-safe rows for AnalysisRun.result_json."""
    return [asdict(f) for f in fills]


def from_dicts(rows) -> list[Fill]:
    """Inverse of `to_dicts`, for surfaces reading a stored run.

    Unknown keys are dropped rather than raising, and every optional
    field defaults — a run recorded before the next column existed must
    still render, or adding a column 500s the results page for every
    deal already in the database.
    """
    known = {"field", "value_used", "source_key", "label", "unit", "detail"}
    out = []
    for row in rows or []:
        kwargs = {k: v for k, v in (row or {}).items() if k in known}
        if "field" not in kwargs or "source_key" not in kwargs:
            continue
        kwargs.setdefault("value_used", None)
        kwargs.setdefault("label", "")
        out.append(Fill(**kwargs))
    return out


def format_detail(fill: "Fill") -> str:
    """The raw inputs behind a substitution, as one auditable string.

    The repo's standing rule is that every value a user sees is traceable
    to its formula, its source and its raw inputs, and that the trace is
    built in rather than bolted on. `label` carries the formula in prose;
    this carries the numbers it was computed from, for the workbook —
    the analyst's audit artifact, and the one surface with room for it.
    Without this the `detail` dict would be a field nobody renders, which
    is the failure this item is named after.
    """
    return "; ".join(f"{k}={v}" for k, v in (fill.detail or {}).items())


# ── Assembly ────────────────────────────────────────────────────────

def collect(*, cim_data=None, financial_analysis=None, physical_analysis=None,
            market_cap=None, va_results=None, expense_ratio=None) -> list[Fill]:
    """The run's whole fill log, ordered and de-duplicated.

    The analogue of `checks.input_from_cim` — ONE adapter, called once by
    the engine, so every surface reports the same set. Callers pass
    whatever they have; a missing section contributes nothing rather than
    raising, because the assumptions preview has only the financials.

    Every fill either comes from the stage that made the substitution or
    is read off provenance that stage already published. Nothing here
    re-decides what a fallback would have done.
    """
    found = []
    found += _rows_of(financial_analysis)
    found += _rows_of(physical_analysis)
    found += _value_add_rows(va_results)
    found += _provenance_rows(market_cap, expense_ratio)

    order = {key: i for i, key in enumerate(SOURCE_KEYS)}
    seen, out = set(), []
    for fill in found:
        key = (fill.field, fill.source_key)
        if key in seen:
            continue
        seen.add(key)
        out.append(fill)
    out.sort(key=lambda f: (order.get(f.source_key, len(order)), f.field))
    return out


def _rows_of(section) -> list:
    """Fills a completed analysis stage attached to its own output.

    Every stage publishes DICTS, because every one of these sections is
    persisted into `AnalysisRun.result_json`, so `from_dicts` is the one
    way back in — the same rule the value-add scenarios follow below.
    """
    return from_dicts((section or {}).get("fills"))


def _value_add_rows(va_results) -> list:
    """The VA engine resolves its inputs ONCE and every scenario is run
    off that one set, so each scenario carries the same `input_fills` and
    any of them answers for the run. `collect`'s de-duplication makes
    reading them all equivalent to reading one, and safe if that ever
    stops being true.

    `input_fills` is stored as dicts, because the scenario dict it rides
    on is persisted as JSON — so they come back through `from_dicts`.
    """
    rows = []
    for scenario in (va_results or {}).values():
        if isinstance(scenario, dict):
            rows += from_dicts(scenario.get("input_fills"))
    return rows


def _provenance_rows(market_cap, expense_ratio) -> list:
    """The fills whose stage publishes a provenance flag instead of a
    fill — read the flag, never re-run the decision behind it."""
    rows = []
    mc = market_cap or {}

    # The exit cap is the one number in the run that a table lookup
    # supplies outright, so both halves of that lookup — which row and
    # which column — need provenance. An analyst-entered cap stands on
    # its own and neither half moved it, which is exactly the condition
    # `checks._market_exit_cap` already uses for the vintage half.
    if mc.get("source") == "table":
        if mc.get("age_band_known") is False:
            rows.append(Fill(
                field="year_built", value_used=mc.get("age_band"),
                source_key=AGE_BAND_FALLBACK, unit=UNIT_TEXT,
                label=("Year built is not stated, so the exit cap was "
                       "anchored in the fallback age band rather than in the "
                       "asset's actual age."),
                detail={"market_cap": mc.get("market_cap"),
                        "asset_class": mc.get("asset_class")}))
        # `is False`, never falsy: `None` means the caller that resolved
        # the anchor did not answer the question, and "nobody said" is not
        # "the CIM did not evidence it". Logging on None would put a row
        # on every deal resolved by a path that predates the flag.
        if mc.get("asset_class_known") is False:
            rows.append(Fill(
                field="asset_class", value_used=mc.get("asset_class"),
                source_key=ASSET_CLASS_DEFAULT, unit=UNIT_TEXT,
                label=("Nothing in the CIM identifies the class — no boat/RV "
                       "square footage, no climate-controlled share — so the "
                       "exit cap came from the default row of the table."),
                detail={"market_cap": mc.get("market_cap"),
                        "age_band": mc.get("age_band")}))

    # The OpEx ratio the projection loads expenses at. `analyze_financials`
    # computes it from revenue; with no revenue there is nothing to
    # compute and `registry.clamp_expense_ratio` supplies the default —
    # inside `project_cash_flows`, which runs hundreds of times per deal,
    # so it is recorded here from the absence rather than from in there.
    #
    # The logged value comes from THAT function, called the same way the
    # projection calls it, not from `cfg.EXPENSE_RATIO["default"]` read
    # raw. `clamp_expense_ratio` clamps the default into the band derived
    # from the benchmark ratio, and `EXPENSE_RATIO` is settings-editable:
    # a stored override pushing the default outside that band would make
    # a raw read print a share the model never charged. The two coincide
    # on shipped config, which is exactly how this kind of bug survives.
    if expense_ratio is None:
        from registry import clamp_expense_ratio
        charged = clamp_expense_ratio(None)
        rows.append(Fill(
            field="opex_revenue_ratio", value_used=charged,
            source_key=EXPENSE_RATIO_DEFAULT, unit=UNIT_PCT,
            label=(f"The financials yielded no OpEx/Revenue ratio, so every "
                   f"projected year loads expenses at {charged:.1%} of "
                   f"revenue — the config default, clamped to the band the "
                   f"model believes."),
            detail={"config_default": cfg.EXPENSE_RATIO["default"],
                    "clamped_to": charged}))
    return rows
