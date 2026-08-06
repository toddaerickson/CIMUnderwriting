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

Two fallbacks are deleted rather than logged, because no honest label
exists for them: `nrsf or 1` and `ttm_noi or 100_000`. A one-square-foot
property and a $100k NOI are not conservative estimates, they are
fictions that silently rescale every $/SF benchmark and every solver
bracket in the run. `require_underwritable` refuses the deal instead.

## Where a fill is recorded

At the site that substitutes, never by a second module re-deciding what
the first one would have done — that is the duplicated-constant defect
in a new costume. `analyze_financials` and `analyze_physical` return
their fills on the dict they already return; the value-add engine
publishes its own on each scenario. `collect()` gathers those, adds the
handful derivable from provenance the pipeline already publishes
(`market_cap["age_band_known"]`, `registry.classify_asset_type`), and
returns one ordered, de-duplicated log.

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
magnitude. Nor is a REFUSAL a fill: when a $/SF CapEx rate loses the
NRSF it multiplies, `engine.run_analysis` warns and books nothing —
the value was declined, not substituted, and that is a run warning.
"""

from dataclasses import asdict, dataclass, field

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
OCCUPANCY_ABSENT = "occupancy_absent"
MARKET_RENT_ABSENT = "market_rent_absent"
EXPENSES_ABSENT = "expenses_absent"

#: Declaration order, which is also render order. Provenance of the
#: whole asset first, then the income statement, then the value-add
#: engine's own inputs — the order a reader asks the questions in.
SOURCE_KEYS = (
    ASSET_CLASS_DEFAULT,
    AGE_BAND_FALLBACK,
    STATE_ABSENT,
    CC_PCT_ABSENT,
    STATE_TAX_FORMULA,
    MGMT_FEE_TARGET,
    BENCHMARK_LOW,
    EXPENSE_RATIO_DEFAULT,
    OCCUPANCY_ABSENT,
    MARKET_RENT_ABSENT,
    EXPENSES_ABSENT,
)

#: What each source key means, in one clause, for the memo's appendix
#: legend and the results-page column. The `label` on a Fill says what
#: happened to THAT field; this says what the source itself is.
SOURCE_LABELS = {
    ASSET_CLASS_DEFAULT: "config default (asset class)",
    AGE_BAND_FALLBACK: "config default (age band)",
    STATE_ABSENT: "national benchmarks",
    CC_PCT_ABSENT: "assumed 0% climate-controlled",
    STATE_TAX_FORMULA: "state income-based tax formula",
    MGMT_FEE_TARGET: "config MGMT_FEE_TARGET_PCT",
    BENCHMARK_LOW: "benchmark floor x NRSF",
    EXPENSE_RATIO_DEFAULT: "config EXPENSE_RATIO default",
    OCCUPANCY_ABSENT: "value-add engine default",
    MARKET_RENT_ABSENT: "in-place rent",
    EXPENSES_ABSENT: "no expenses booked",
}

# ── Units ───────────────────────────────────────────────────────────
# `format_value` is the ONE formatter. The memo, the workbook and the
# results page all call it, so a fill cannot print as "0.8" in one place
# and "80%" in another.

UNIT_DOLLARS = "$"
UNIT_PSF_MO = "$/SF/mo"
UNIT_PCT = "%"
UNIT_TEXT = ""


@dataclass(frozen=True)
class Fill:
    """One value the run invented because the CIM did not supply it.

    `detail` carries the raw inputs behind the substitution, the same way
    `CheckResult.values` does, so a reader can trace the number to its
    formula without re-deriving anything.
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


#: The two inputs with no defensible fallback. NRSF divides every
#: benchmark in the expense analysis and multiplies every $/SF rate;
#: TTM NOI sets the solver's search bracket. Substituting either does not
#: make the answer approximate, it makes it unrelated to the asset.
REQUIRED_UNDERWRITING_FIELDS = (("NRSF", "nrsf"), ("TTM NOI", "ttm_noi"))


def require_underwritable(cim_data) -> None:
    """Raise `MissingUnderwritingInput` unless the deal can be priced.

    Falsy, not `is None`: `extract.parser._parse_number` returns 0.0 when
    it cannot read a figure, so an unparseable NRSF arrives as zero AND
    counts as populated in the extraction report. An `is None` gate would
    wave a 0-SF property straight into every division this refuses.
    """
    missing = [label for label, attr in REQUIRED_UNDERWRITING_FIELDS
               if not getattr(cim_data, attr, None)]
    if not missing:
        return
    subject = " and ".join(missing)
    verb = "is" if len(missing) == 1 else "are"
    raise MissingUnderwritingInput(
        f"{subject} {verb} missing from this deal, so it cannot be "
        f"underwritten — every $/SF benchmark and the solver's price "
        f"bracket are derived from {'it' if len(missing) == 1 else 'them'}. "
        f"Enter {'it' if len(missing) == 1 else 'them'} on the Assumptions "
        f"page and re-run.")


def format_value(fill) -> str:
    """The one rendering of a fill's value, unit included.

    Takes a `Fill` or the dict `to_dicts` produced, because the memo
    renders from the live objects and the results page renders from a
    stored run's JSON.
    """
    if isinstance(fill, dict):
        value, unit = fill.get("value_used"), fill.get("unit") or UNIT_TEXT
    else:
        value, unit = fill.value_used, fill.unit
    if value is None:
        return "—"
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return str(value)
    if unit == UNIT_DOLLARS:
        return f"${value:,.0f}"
    if unit == UNIT_PSF_MO:
        return f"${value:,.2f}/SF/mo"
    if unit == UNIT_PCT:
        return f"{value:.1%}"
    return f"{value:,.4g}"


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


def summarize(fills) -> dict:
    """Counts for the surfaces that name the log without listing it —
    memo section 1 and the LP summary's footer."""
    rows = list(fills or [])
    return {"total": len(rows),
            "fields": len({(r.get("field") if isinstance(r, dict)
                            else r.field) for r in rows})}


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
    found += _provenance_rows(cim_data, market_cap, expense_ratio)

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
    """Fills a completed analysis stage attached to its own output."""
    return list((section or {}).get("fills") or [])


def _value_add_rows(va_results) -> list:
    """The VA engine resolves its inputs ONCE and every scenario is run
    off that one set, so each scenario carries the same `input_fills` and
    any of them answers for the run. `collect`'s de-duplication makes
    reading them all equivalent to reading one, and safe if that ever
    stops being true."""
    rows = []
    for scenario in (va_results or {}).values():
        if isinstance(scenario, dict):
            rows += list(scenario.get("input_fills") or [])
    return rows


def _provenance_rows(cim_data, market_cap, expense_ratio) -> list:
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
                label=(f"Year built is not stated, so the exit cap was "
                       f"anchored in the {mc.get('age_band')!r} age band by "
                       f"fallback rather than from the asset's actual age."),
                detail={"market_cap": mc.get("market_cap"),
                        "asset_class": mc.get("asset_class")}))
        if cim_data is not None:
            from registry import classify_asset_type
            _, evidenced = classify_asset_type(cim_data)
            if not evidenced:
                rows.append(Fill(
                    field="asset_class", value_used=mc.get("asset_class"),
                    source_key=ASSET_CLASS_DEFAULT, unit=UNIT_TEXT,
                    label=(f"Nothing in the CIM identifies the asset class "
                           f"(no boat/RV square footage, no climate-controlled "
                           f"share), so it was classed as "
                           f"{mc.get('asset_class')!r} by default and the exit "
                           f"cap came from that row of the table."),
                    detail={"market_cap": mc.get("market_cap"),
                            "age_band": mc.get("age_band")}))

    # The OpEx ratio the projection loads expenses at. `analyze_financials`
    # computes it from revenue; with no revenue there is nothing to
    # compute and `registry.expense_ratio_clamp` supplies the default —
    # inside the projection, which runs hundreds of times per deal, so it
    # is recorded here from the absence rather than from in there.
    if expense_ratio is None:
        import config as cfg
        rows.append(Fill(
            field="opex_revenue_ratio", value_used=cfg.EXPENSE_RATIO["default"],
            source_key=EXPENSE_RATIO_DEFAULT, unit=UNIT_PCT,
            label=("No OpEx/Revenue ratio could be computed from the "
                   "financials, so every projected year loads expenses at "
                   "the config default share of revenue."),
            detail={"source": "config.EXPENSE_RATIO['default']"}))
    return rows
