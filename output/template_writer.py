"""
Template Writer — populate the XLSM underwriting template with CIM data
extracted by the analysis pipeline.

Writes only to INPUT cells (not formulas). The user opens the .xlsm in
Excel and formulas recalculate automatically.

Place your .xlsm template in the project root and set TEMPLATE_FILENAME below
(or override via the UW_TEMPLATE_PATH environment variable).

## The template never decides a value (item E3b)

Every number written below reads from the run's resolved assumption set
(config + ConfigOverride + per-deal overrides) or from the run's computed
results. It used to assert its own: 6.5% debt at 360-month amortization
against a model running 6.25% over 25 years, a 6%-GP / 20%-promote
waterfall read from environment variables, a flat 3% growth ladder, a
0.10 stabilized vacancy against a 0.88 stabilized-occupancy assumption.
Two deliverables quoting different terms on the same deal is the failure
mode the transparency audit raised, and after item E3a wired levered
returns on by default it shipped on every deal.

`tests/test_template_writer.py::test_no_numeric_literals_in_write_paths`
enforces this with an AST walk, not by inspection: no numeric literal may
appear on the VALUE side of a cell write. The walk FOLLOWS LOCAL NAMES,
so parking a literal in a variable one line above the write does not
evade it — that hole was found in review and the gate now has its own
test proving it fails when it should. Structural constants — a column
index, an at-closing zero, the template's own year-1 convention — are
named once below, which is what makes them reviewable.

## What does not reconcile — SETTLED, and disclosed in the workbook

The XLSM computes its own returns from these inputs, and two of its
conventions are not ours. Neither is reachable from an input cell, so
the writer stamps them into the workbook itself
(`_write_divergence_disclosures`) as well as here:

1. **The pref is an IRR hurdle** (H257 "IRR Hurdle"; H258 feeds tiers
   2-4 through `=+H258`). `model.waterfall` runs an ACCRUAL account on
   contributed/unreturned capital. Same 8%, different construction, so
   the promote dollars differ. Writing `pref_rate` into H258 makes the
   two agree on the RATE, which is as far as an input cell reaches. A
   future edit of the TEMPLATE could swap the hurdle formula for an
   accrual — that is an XLSM edit, not a writer change; until someone
   makes it, the divergence is permanent and disclosed.
2. **The AM fee is charged on LP equity** (H254 = `K60*G254/12`, and
   K60 is LP equity). `config.AM_FEE_BASE` is `invested_equity` —
   GP + LP — so at a 10% GP co-invest the workbook's fee runs ~10%
   light. The dropdown has no invested-equity option. Grossing the rate
   up to 1.11% would make the dollars tie while printing a fee rate the
   fund does not charge, so the true rate is written and the gap is
   disclosed — REAFFIRMED by the operator 2026-08-10 when this residue
   was settled. (The gross-up could not even tie in general: the model's
   fee base rolls forward on a capital call — `model/levered.py` — where
   the workbook's K60 is fixed at close, so a single compensated rate
   reproduces the model's dollars only on deals that never call.)

Settled 2026-08-10; until then both lines read "item T's to reconcile"
and T closed without them. Do not "fix" either by editing a value into
agreement — that trades a visible discrepancy for a hidden one.
"""

import logging
import os
import shutil
from datetime import datetime

import openpyxl

import config as cfg
from analysis.financials import resolve_mgmt_fee_target
from output import safe_filename
from model.debt import MONTHS_PER_YEAR
from registry import ScenarioType

logger = logging.getLogger(__name__)

# Path to the blank template (.xlsm with macros)
# Override via environment variable or place your template in the project root.
TEMPLATE_FILENAME = "template_uw.xlsm"
TEMPLATE_PATH = os.environ.get(
    "UW_TEMPLATE_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), TEMPLATE_FILENAME),
)

# Underwriting sheet unit mix rows: 111-131 (21 slots)
UNIT_MIX_START_ROW = 111
UNIT_MIX_END_ROW = 131

# OpEx row mapping: benchmark_key → (row, is_pct_of_egr)
# is_pct_of_egr=True means input is a percentage; False means $/SF/year
OPEX_ROW_MAP = {
    "repairs":      (150, False),
    "payroll":      (151, False),
    "ga":           (152, False),
    "advertising":  (153, False),
    "utilities":    (154, False),
    # Row 155: Bank/Merchant fees — % of EGR, written from config
    # Row 156: Miscellaneous — skip
    # Row 157: Management fee — % of EGR
    "insurance":    (158, False),
    "property_tax": (159, False),
    "cap_reserve":  (164, False),
}

# ── Structural constants ─────────────────────────────────────────────
# Not assumptions — the template's own schema. Named so the AST gate can
# be absolute ("no numeric literal on the value side of a write") and so
# a reviewer can tell a layout fact from an underwriting opinion.

_COL_IN_PLACE = 7        # G — the in-place column on every paired row
_COL_STABILIZED = 9      # I — the stabilized column
_COL_PROMOTE = 9         # I — promote share on the tier rows

# Unit-mix columns.
_COL_UNIT_LABEL = 2          # B
_COL_UNIT_COUNT = 3          # C
_COL_UNIT_SF = 4             # D
_COL_UNIT_STABILIZED_PCT = 5  # E
_COL_UNIT_CLIMATE = 13       # M

# Rounding precision, in decimal places. Not assumptions — they keep
# float noise out of a cell and nothing else. Rates carry more digits
# than dollars because 6 dp on a cap rate is a hundredth of a basis
# point, where 2 dp on a dollar is a cent.
_DOLLARS_DP = 2
_PERCENT_DP = 4
_RATE_DP = 6

# Growth ladder: rows 101-106, years 1-6 across columns C..H.
_GROWTH_FIRST_COL = 3                    # C
_GROWTH_YEARS = 6                        # C..H
_REVENUE_GROWTH_ROWS = (101, 102, 103)   # in-place rent, stabilized rent, other income
_EXPENSE_GROWTH_ROWS = (104, 105, 106)   # OpEx ex-taxes, property taxes, CapEx
# B107 in the workbook reads "Growth is assumed to begin month 13", so the
# year-1 column is the template's in-place year and carries no growth.
# That is the template's convention, not our assumption — the app expresses
# year 1 through `yr1_noi_bump`, which has no cell here.
_GROWTH_BEGINS_YEAR = 2
_NO_GROWTH = 0
# `rev_cagr_yr1_3` applies through year 3 and `rev_cagr_yr4_5` year 4
# onward — the banding `analysis.valuation.project_cash_flows` uses.
_REV_CAGR_BAND_END_YEAR = 3

_STABILIZATION_BEGIN_MONTH = 1     # K101 — lease-up starts immediately

# Costs incurred at closing: start month 0, spread over 0 months.
_AT_CLOSING_START_MONTH = 0
_AT_CLOSING_DURATION = 0

# Blank unit-mix row: {column: value}. "% stabilized" of 1 means fully
# stabilized.
_UNIT_STABILIZED = 1
_UNIT_NOT_STABILIZED = 0
_NO_UNITS = 0
_NO_RENT = 0
_BLANK_UNIT_ROW = {
    _COL_UNIT_LABEL: "[Unit Type]",
    _COL_UNIT_COUNT: _NO_UNITS,
    _COL_UNIT_SF: _NO_UNITS,
    _COL_UNIT_STABILIZED_PCT: _UNIT_STABILIZED,
    _COL_IN_PLACE: _NO_RENT,
    _COL_STABILIZED: _NO_RENT,
    _COL_UNIT_CLIMATE: "Non-Climate",
}
_OTHER_INCOME_ROWS = (138, 139, 140, 141, 142, 143)
_NO_INCOME = 0

# Absence-of-data fallbacks. A CIM that reports no CapEx, no other
# income and no expense line for a category gets zero, not a guess.
_NO_CAPEX = 0
_NO_EXPENSE = 0

# We model ONE senior loan. Mezz/junior paper is out of scope (scoped
# backlog item D), so H65 stays flat zero rather than reading a term.
_NO_JUNIOR_DEBT = 0
_NO_DEBT = 0

# The current owner's reserve is whatever the CIM shows, and CIMs do not
# show one; the stabilized reserve below it is the underwriting number.
_NO_IN_PLACE_CAPITAL_RESERVE = 0

# Waterfall block. We charge no GP acquisition or disposition fee, so the
# promote structure is pref + promote only. (The disposition COST — the
# broker — is K182, a different cell; writing it here would double-count
# the sale. Recorded in `_write_reversion`.)
_NO_GP_FEE = 0
_PROMOTE_TIER_ROWS = (259, 260, 261)   # 2nd/3rd/4th tier; H259-261 chain to H258
_YES = "Yes"
# The only non-EGR option the H254 formula understands. See the module
# docstring: the label is honest about the workbook, and the workbook
# disagrees with `config.AM_FEE_BASE`.
_AM_FEE_BASIS_LABEL = "% of LP Equity"

_SUMMARY_NOTE_COL = 6              # F
_SUMMARY_STRENGTH_ROWS = range(6, 11)
_SUMMARY_WEAKNESS_ROWS = range(12, 17)

# Divergence disclosures — two rows immediately below the waterfall
# block (which ends at the tier rows, 259-261). These cells must be
# BLANK in the shipped template: the writer cannot verify that here
# (the workbook is proprietary, gitignored, absent in CI), so
# `test_real_template_still_has_the_cells_the_stub_claims` asserts
# their emptiness and FAILS on the first machine that has the real
# file if the guess was wrong — a wrong cell would silently overwrite
# a label or formula, which is exactly what that test exists to catch.
_DISCLOSURE_PREF_CELL = "B263"
_DISCLOSURE_AM_FEE_CELL = "B264"

# The strings are static on purpose: interpolating the deal's own rates
# would put values in prose where no test reconciles them. Direction and
# mechanism are what the reader needs; the rates live in their cells.
_PREF_DISCLOSURE = (
    "Note: this workbook's pref (H257) is an IRR hurdle; the app's "
    "waterfall accrues the pref on unreturned capital. Same rate (H258), "
    "different construction, so promote dollars differ from the memo. "
    "Not reachable from an input cell; disclosed here instead."
)
_AM_FEE_DISCLOSURE = (
    "Note: H254 charges the AM fee on LP equity (K60); the app charges "
    "invested equity (GP + LP), so this workbook's fee runs light by "
    "roughly the GP co-invest share. G254 is the fund's true rate, not "
    "a grossed-up one (operator decision 2026-08-10)."
)

# Benchmark bands are (low, high) tuples. `_BAND_HIGH` went with the
# management-fee cell when it started reading `resolve_mgmt_fee_target`
# instead of the band's top end.
_BAND_LOW = 0


def generate_template(
    cim_data,
    financial_analysis: dict,
    scenario_results: dict = None,
    max_offer: dict = None,
    output_dir: str = ".",
    property_name: str = "",
    hold_years: int = None,
    transaction_costs: dict = None,
    capex: float = None,
    debt_terms=None,
    waterfall_terms=None,
    am_fee_pct: float = None,
    sources_uses: dict = None,
    mgmt_fee_target_pct: float = None,
) -> str:
    """
    Copy the XLSM template and populate input cells with CIM data.

    Args:
        cim_data: CIMData dataclass with extracted property data
        financial_analysis: dict from analyze_financials()
        scenario_results: dict with bear/base/bull scenario results
        max_offer: dict with max price solver results
        output_dir: directory to write the output file
        property_name: display name for the property
        hold_years: hold period; drives the template's sale month (D182)
        transaction_costs: override of config.TRANSACTION_COSTS; the
            disposition percentage drives the template's cost of sale
            (K182), which was hardcoded at 3.5%
        capex: CapEx already resolved to DOLLARS (item H). None falls
            back to reading `cim_data.capex_estimate` as dollars, which
            is right for every caller that has no basis selector — but
            wrong for a rate, so the engine passes the resolved figure
        debt_terms: an already-RESOLVED `model.debt.DebtTerms`, not an
            override dict. None resolves `config.DEBT_TERMS`.
        waterfall_terms: an already-RESOLVED
            `model.waterfall.WaterfallTerms`, not an override dict. None
            resolves `config.WATERFALL_TERMS`.

            Both take resolved objects deliberately. Re-resolving an
            override dict here would need the deal's capital structure to
            get `gp_coinvest_pct` right, and getting it wrong is silent:
            a deal edited to 25% co-invest would print a Sources & Uses
            stack split 25/75 in the .xlsx and a 10/90 waterfall in the
            .xlsm. `resolve_waterfall_terms` argues the same point at
            greater length. The engine resolves once and hands the result
            to every writer.
        am_fee_pct: resolved annual management fee rate. None uses
            `config.AM_FEE_PCT`.
        sources_uses: the run's Sources & Uses block. Supplies the loan's
            share of total uses for the template's LTC cell (H64); without
            it the workbook is written all-equity.

    Returns:
        Path to the generated .xlsm file
    """
    from analysis.valuation import (resolve_hold_years,
                                    resolve_transaction_costs)
    from model.debt import resolve_debt_terms
    from model.waterfall import resolve_waterfall_terms

    hold_years = resolve_hold_years(hold_years)
    costs = resolve_transaction_costs(transaction_costs)
    # The CLI has no per-deal overrides, so config defaults are the whole
    # resolved set there. The web path always passes resolved objects.
    debt = debt_terms if debt_terms is not None else resolve_debt_terms()
    waterfall = (waterfall_terms if waterfall_terms is not None
                 else resolve_waterfall_terms())
    am_fee = cfg.AM_FEE_PCT if am_fee_pct is None else am_fee_pct
    params = _base_scenario_params(scenario_results)

    if not os.path.exists(TEMPLATE_PATH):
        raise FileNotFoundError(f"Template not found: {TEMPLATE_PATH}")

    # Build output filename
    safe_name = safe_filename(property_name or cim_data.property_name or "Deal")
    out_path = os.path.join(output_dir, f"UW_{safe_name}.xlsm")

    # Copy template
    shutil.copy2(TEMPLATE_PATH, out_path)

    # Open with VBA preservation
    wb = openpyxl.load_workbook(out_path, keep_vba=True)
    ws = wb["Underwriting"]
    ws_summary = wb["Summary"]

    # Populate sections
    _write_property_description(ws, cim_data, hold_years)
    _write_investment_cf(ws, cim_data, costs, capex)
    _write_financing(ws, debt, sources_uses)
    _write_growth_rates(ws, params)
    _write_stabilization(ws, cim_data, params)
    _write_unit_mix(ws, cim_data, params)
    _write_other_income(ws, cim_data)
    _write_vacancy(ws, cim_data, params)
    _write_opex(ws, cim_data, financial_analysis, mgmt_fee_target_pct)
    _write_capex(ws)
    _write_reversion(ws, cim_data, financial_analysis, costs, scenario_results)
    _write_waterfall(ws, waterfall, am_fee)
    _write_divergence_disclosures(ws)
    _write_summary_notes(ws_summary, cim_data)

    wb.save(out_path)
    wb.close()

    logger.info("  Template: %s", out_path)
    return out_path


# ── Resolved-assumption helpers ──────────────────────────────────────

def _base_scenario_params(scenario_results: dict = None) -> dict:
    """The base case's resolved scenario parameters.

    Prefers the RUN's own params over re-reading config: `scenario_results`
    carries the values the projection actually used, and re-resolving
    would answer with whatever config says now. Falls back to config only
    when no scenario ran — the CLI's degraded path and tests.
    """
    base = (scenario_results or {}).get(ScenarioType.BASE) or {}
    return base.get("params") or cfg.SCENARIO_DEFAULTS[ScenarioType.BASE]


def _physical_occupancy(cim_data) -> float:
    """Physical occupancy, or the config assumption with a warning.

    The audit's complaint was not the 0.90 itself but that it was silent:
    a deal whose CIM never states occupancy was underwritten at 90% and
    nothing anywhere said so.
    """
    occ = cim_data.physical_occupancy
    # `is not None`, not truthiness: `physical_occupancy` defaults to None
    # when the parser finds nothing, so None is genuinely "missing" — but
    # a CIM that STATES 0% is a fully vacant building, and `or` would
    # overwrite that fact with 90% while logging that it was absent. A
    # warning that misreports its own trigger is worse than the silence
    # this helper exists to end.
    if occ is not None:
        return float(occ)
    assumed = cfg.XLSM_TEMPLATE_INPUTS["assumed_physical_occupancy"]
    logger.warning(
        "Physical occupancy missing from CIM data — XLSM written against "
        "the assumed %.0f%% in config.XLSM_TEMPLATE_INPUTS. Verify before "
        "relying on the workbook's vacancy or stabilization timing.",
        assumed * 100)
    return float(assumed)


def _next_month_start(today: datetime) -> datetime:
    """First of the month after `today` — the template's analysis begin
    date. Calendar arithmetic, not an assumption, which is why the month
    and day components live here and not in a write path."""
    if today.month == 12:
        return datetime(today.year + 1, 1, 1)
    return datetime(today.year, today.month + 1, 1)


def _unit_label(unit, index: int) -> str:
    """The unit type's own label, or a 1-based positional stand-in."""
    return unit.size_label or f"Type {index + 1}"


def _vacancy_from(occupancy: float) -> float:
    """Vacancy is the complement of occupancy — a definition, not an
    assumption, which is why the arithmetic lives here and not in a
    write path."""
    return max(0.0, 1.0 - float(occupancy))


def _is_stabilized(occupancy: float, params: dict) -> bool:
    """Stabilized against the BASE case's stabilized-occupancy target.

    `config.GATES["stabilized_occupancy"]` (0.85) is a different question
    — the ramp test for post-2020 vintages — and reconciling the two is
    item T's, per scoped-backlog rule 3.
    """
    return float(occupancy) >= float(params["stabilized_occ"])


# ── Property Description (rows 7-18) ─────────────────────────────────

def _write_property_description(ws, cim_data, hold_years: int):
    """Fill property description section."""
    name = cim_data.property_name or ""
    ws["F8"] = name
    ws["K8"] = name
    ws["F9"] = cim_data.address or ""
    ws["K9"] = ""  # County — not extracted, user fills
    ws["F10"] = cim_data.city or ""
    ws["K10"] = ""  # Zip — not extracted, user fills

    if cim_data.acreage:
        ws["F11"] = cim_data.acreage

    # Buildings / stories — not reliably extracted, leave blank
    ws["K11"] = ""
    ws["K12"] = ""

    if cim_data.year_built:
        ws["F16"] = cim_data.year_built

    # Analysis begin date: first of next month
    ws["F17"] = _next_month_start(datetime.now())

    # Sale month — the hold period as the template expresses it.
    ws["D182"] = hold_years * MONTHS_PER_YEAR


# ── Investment Cash Flows (rows 20-47) ───────────────────────────────

def _write_investment_cf(ws, cim_data, costs: dict, capex: float = None):
    """Fill purchase price, acquisition closing costs and capex.

    Row 24 is a free line inside the template's own ACQUISITION COST
    block (K27 = SUM(K23:K26), which rolls into total project cost), so
    closing costs land where the template already totals them. Without
    this the .xlsm computed its purchase-side outlay as price + capex
    only, and its IRR disagreed with the memo and the .xlsx — which
    report a cost-inclusive basis — on every deal (review finding).

    No double count with the Title/Legal soft-cost rows: those are
    formulas off HARD costs (K33), a different base.
    """
    timing = cfg.XLSM_TEMPLATE_INPUTS
    if cim_data.asking_price:
        ws["K23"] = cim_data.asking_price
        acquisition_cost = (cim_data.asking_price
                            * costs["acquisition_closing_pct"])
        ws["B24"] = "Acquisition Closing Costs"
        ws["K24"] = round(acquisition_cost, _DOLLARS_DP)
        ws["E24"] = _AT_CLOSING_START_MONTH   # same timing as the price row
        ws["F24"] = _AT_CLOSING_DURATION

    # `capex` arrives resolved when the caller knows the basis; a bare
    # cim_data read would write 0.02 into a dollar cell for a deal whose
    # CapEx was entered as a percentage of price.
    capex = ((cim_data.capex_estimate or _NO_CAPEX) if capex is None
             else capex)
    if capex > 0:
        ws["B30"] = "Deferred Maintenance"
        ws["K30"] = capex
        ws["E30"] = timing["capex_start_month"]
        ws["F30"] = timing["capex_duration_months"]


# ── Financing (rows 64-73) ────────────────────────────────────────────

def _write_financing(ws, terms, sources_uses: dict = None):
    """Write the run's own loan into the template's debt block.

    **Why this is no longer all-equity.** The scoped backlog wrote its
    rule when leverage was opt-in per deal ("LTC stays 0, but the terms
    cells still carry the resolved values, so a user who flips LTC in
    Excel gets the terms the app would have used"). Item E3a reversed
    that on 2026-08-01: every deal is now sized at `config.DEBT_TERMS`
    and carries an LP net IRR, so the opted-out state that rule
    described no longer exists. Shipping an all-equity workbook beside a
    levered memo would leave exactly the contradiction this item exists
    to close, so H64 carries the run's actual leverage.

    `sources_uses["ltv"]` is loan / TOTAL USES — despite the key name,
    the denominator is the full cost stack, which is precisely what the
    template's H64 means (K64 = H64 * K55, and K55 is Total Uses). The
    workbook re-derives the dollar loan off its OWN K55, so the two agree
    on the loan only insofar as the two cost stacks agree; the K24
    closing-cost row above is what keeps them close.

    With no Sources & Uses (the CLI, or a deal that never sized) the
    block stays all-equity and the terms cells still carry the resolved
    loan, which is the backlog's original "flips LTC in Excel" case.
    """
    ltc = (sources_uses or {}).get("ltv")
    ws["H64"] = round(float(ltc), _RATE_DP) if ltc else _NO_DEBT
    ws["H65"] = _NO_JUNIOR_DEBT
    ws["F73"] = terms.term_years * MONTHS_PER_YEAR
    ws["G73"] = terms.io_months
    ws["H73"] = terms.amort_years * MONTHS_PER_YEAR
    ws["I73"] = terms.all_in_rate()


# ── Growth Rates (rows 100-106) ──────────────────────────────────────

def _write_growth_rates(ws, params: dict):
    """Write the base case's growth banding across years 1-6.

    Deliberate behavior change (scoped-backlog rule 4): this was a flat
    0%-then-3% ladder on all six rows regardless of scenario. Revenue
    rows now grow at the resolved revenue CAGR and expense rows at
    `exp_growth`, so the workbook and the projection answer the same
    question the same way.

    Year 1 stays flat — see `_GROWTH_BEGINS_YEAR`. The app expresses
    year 1 through `yr1_noi_bump`, which the template has no cell for.
    """
    rev_early = params["rev_cagr_yr1_3"]
    rev_late = params["rev_cagr_yr4_5"]
    exp_growth = params["exp_growth"]

    for row in _REVENUE_GROWTH_ROWS + _EXPENSE_GROWTH_ROWS:
        is_revenue = row in _REVENUE_GROWTH_ROWS
        for year in range(1, _GROWTH_YEARS + 1):
            if year < _GROWTH_BEGINS_YEAR:
                value = _NO_GROWTH
            elif not is_revenue:
                value = exp_growth
            elif year <= _REV_CAGR_BAND_END_YEAR:
                value = rev_early
            else:
                value = rev_late
            column = _GROWTH_FIRST_COL + year - 1
            ws.cell(row=row, column=column).value = value


# ── Stabilization ────────────────────────────────────────────────────

def _write_stabilization(ws, cim_data, params: dict):
    """Set stabilization timing."""
    occ = _physical_occupancy(cim_data)

    ws["K101"] = _STABILIZATION_BEGIN_MONTH
    if _is_stabilized(occ, params):
        # Already stabilized — complete in the month it begins.
        ws["K102"] = _STABILIZATION_BEGIN_MONTH
    else:
        ws["K102"] = cfg.VALUE_ADD_SCENARIOS[
            ScenarioType.BASE]["months_to_stabilize"]


# ── Unit Mix (rows 111-131) ──────────────────────────────────────────

def _write_unit_mix(ws, cim_data, params: dict):
    """Populate unit mix rows from CIMData.unit_mix."""
    units = cim_data.unit_mix or []

    # Clear all unit mix rows first (rows 111-131)
    for row in range(UNIT_MIX_START_ROW, UNIT_MIX_END_ROW + 1):
        for column, blank in _BLANK_UNIT_ROW.items():
            ws.cell(row=row, column=column).value = blank

    # Fill with actual data
    stabilized = _is_stabilized(_physical_occupancy(cim_data), params)

    for i, unit in enumerate(units):
        if i >= (UNIT_MIX_END_ROW - UNIT_MIX_START_ROW + 1):
            logger.warning("Unit mix has %d types but template has %d slots",
                           len(units), UNIT_MIX_END_ROW - UNIT_MIX_START_ROW + 1)
            break

        row = UNIT_MIX_START_ROW + i
        label = _unit_label(unit, i)
        rate = unit.rate or _BLANK_UNIT_ROW[_COL_IN_PLACE]

        ws.cell(row=row, column=_COL_UNIT_LABEL).value = label
        ws.cell(row=row, column=_COL_UNIT_COUNT).value = (
            unit.count or _BLANK_UNIT_ROW[_COL_UNIT_COUNT])
        ws.cell(row=row, column=_COL_UNIT_SF).value = (
            unit.sf or _BLANK_UNIT_ROW[_COL_UNIT_SF])
        ws.cell(row=row, column=_COL_UNIT_STABILIZED_PCT).value = (
            _UNIT_STABILIZED if stabilized else _UNIT_NOT_STABILIZED)
        ws.cell(row=row, column=_COL_IN_PLACE).value = rate
        # I column: stabilized rent — use in-place for now, user adjusts
        # Note: I{row} has formula =+G{row} in template for rows that had
        # data. For overwritten rows we set explicitly.
        ws.cell(row=row, column=_COL_STABILIZED).value = rate

        # Climate type
        if unit.climate_controlled:
            ws.cell(row=row, column=_COL_UNIT_CLIMATE).value = "Climate"
        else:
            ws.cell(row=row, column=_COL_UNIT_CLIMATE).value = "Non-Climate"


# ── Other Income (rows 137-143) ──────────────────────────────────────

def _write_other_income(ws, cim_data):
    """Populate other income lines."""
    other = cim_data.other_income or _NO_INCOME

    # Clear defaults
    for row in _OTHER_INCOME_ROWS:
        ws.cell(row=row, column=_COL_IN_PLACE).value = _NO_INCOME
        ws.cell(row=row, column=_COL_STABILIZED).value = _NO_INCOME

    if other > 0:
        # Put all other income into "Miscellaneous" parking row
        # as annual amount (template divides by 12 in monthly calcs)
        ws["B142"] = "Other Income"
        ws["G142"] = other
        ws["I142"] = other


# ── Vacancy (rows 146-147) ───────────────────────────────────────────

def _write_vacancy(ws, cim_data, params: dict):
    """Set vacancy and credit loss assumptions.

    Stabilized vacancy is the complement of the base case's
    `stabilized_occ`, not a standing 10%. At the 0.88 default that is
    0.12 — which is also the template's own shipped default, so the
    literal this replaced was the one value in the block that agreed with
    neither the workbook nor the model.
    """
    inputs = cfg.XLSM_TEMPLATE_INPUTS

    ws["G146"] = round(_vacancy_from(_physical_occupancy(cim_data)),
                       _PERCENT_DP)
    ws["I146"] = round(_vacancy_from(params["stabilized_occ"]), _PERCENT_DP)

    ws["G147"] = inputs["credit_loss_in_place"]
    ws["I147"] = inputs["credit_loss_stabilized"]


# ── Operating Expenses (rows 150-159, 164) ───────────────────────────

def _write_opex(ws, cim_data, financial_analysis: dict,
                mgmt_fee_target_pct=None):
    """
    Populate OpEx from CIM data and analyst adjustments.

    In-Place column (G): CIM actual $/SF/year
    Stabilized column (I): analyst-adjusted $/SF/year
    """
    # NOT `or 1` (item T Category 4). It was never the divide-by-zero
    # guard its old comment claimed: dividing a total dollar expense by
    # one square foot writes that total into a column headed "$/SF",
    # which is the fiction this item deletes rather than a safe default.
    # `require_underwritable` refuses a deal with no NRSF before either
    # caller reaches this writer, so the guard below is unreachable
    # belt-and-braces for a direct call.
    nrsf = cim_data.nrsf
    expense_analysis = financial_analysis.get("expense_analysis", {})
    expense_lines = expense_analysis.get("lines", [])

    # Build lookup: benchmark_key → expense line
    exp_lookup = {}
    for line in expense_lines:
        key = line.get("benchmark_key")
        if key:
            exp_lookup[key] = line

    for benchmark_key, (row, is_pct) in OPEX_ROW_MAP.items():
        line = exp_lookup.get(benchmark_key, {})
        cim_value = line.get("cim_value")
        adjusted_value = line.get("adjusted_value")

        # In-place: CIM actual as $/SF/year
        if cim_value is not None and nrsf:
            in_place_psf = cim_value / nrsf
        else:
            in_place_psf = _NO_EXPENSE

        # Stabilized: analyst-adjusted as $/SF/year
        if adjusted_value is not None and nrsf:
            stabilized_psf = adjusted_value / nrsf
        else:
            stabilized_psf = in_place_psf

        ws.cell(row=row, column=_COL_IN_PLACE).value = round(
            in_place_psf, _DOLLARS_DP)
        ws.cell(row=row, column=_COL_STABILIZED).value = round(
            stabilized_psf, _DOLLARS_DP)

    # Management fee — % of EGR (row 157). The CIM's own rate when it
    # states one; otherwise the resolved pro-forma target.
    #
    # This used to read the top of the benchmark band directly. Same
    # NUMBER as the target's default, but arriving by a second route, so
    # a per-deal `mgmt_fee_target_pct` moved the memo and the .xlsx while
    # this workbook — a real deliverable — kept writing 6%. Two output
    # files asserting different management fees on the same deal, with
    # nothing on screen saying so. It reads the ONE resolver now.
    #
    # `is not None` for the same reason as `_physical_occupancy`: a
    # self-managed property stating a 0% fee is data, not a blank.
    mgmt_pct = (cim_data.mgmt_fee_pct if cim_data.mgmt_fee_pct is not None
                else resolve_mgmt_fee_target(mgmt_fee_target_pct))
    ws["G157"] = mgmt_pct
    ws["I157"] = mgmt_pct

    # Bank/merchant fees — % of EGR (row 155)
    ws["G155"] = cfg.XLSM_TEMPLATE_INPUTS["bank_fee_pct_in_place"]
    ws["I155"] = cfg.XLSM_TEMPLATE_INPUTS["bank_fee_pct_stabilized"]


# ── Capital Expenditures (row 164) ───────────────────────────────────

def _write_capex(ws):
    """Set capital reserve assumption.

    In-place is zero because the CIM reports no reserve line; stabilized
    is the bottom of the benchmark band, which is the underwriting
    number and the value this replaced.
    """
    ws["G164"] = _NO_IN_PLACE_CAPITAL_RESERVE
    ws["I164"] = cfg.EXPENSE_BENCHMARKS["cap_reserve"][_BAND_LOW]


# ── Reversion / Sale Assumptions ─────────────────────────────────────

def _write_reversion(ws, cim_data, financial_analysis: dict, costs: dict,
                     scenario_results: dict = None):
    """Set cap rate and sale assumptions.

    K180 ("Market Cap Rate Today") and K181 ("Cap Rate at Sale") come from
    the run's resolved market anchor and base-case exit cap, so the .xlsm
    prints the same two rates as the memo and the .xlsx. They used to be
    the ENTRY cap and the workbook's own `= K180 + 0.005`, which made this
    a second underwriting model that disagreed with the Python one on
    every deal.

    Overwriting K181 replaces a formula with a value, deliberately: the
    terminal cap is no longer "entry + 50 bp", so a cell that keeps
    tracking K180 by that rule would drift away from the published exit
    the moment anyone edited K180. Nothing depends on K181 REMAINING a
    formula — `J224` (the interpolated cap at stabilization) and `K224`
    read its value, and `J224` still interpolates correctly between an
    anchor and a wider terminal cap, which is the drift model.

    **The thin-data path writes nothing rather than inventing an anchor.**
    It used to fall back to a 6.5% literal, and on a deal with no NOI and
    no price that 6.5% was the template deciding its own cap rate. Now
    K180 is written only from a resolved market anchor or a computed
    entry cap; with neither, both cells keep the workbook's own defaults
    — including K181's entry+50bp formula. That formula is the pre-#31
    contradiction, so it survives ONLY where there is no resolved exit
    cap for it to contradict, and a deal that reaches here produced no
    scenarios and no returns at all. The warning says so.
    """
    noi = financial_analysis.get("adjusted_ttm_noi", {}).get("analyst_adjusted_noi")
    price = cim_data.asking_price

    base = (scenario_results or {}).get(ScenarioType.BASE) or {}
    detail = base.get("exit_cap_detail") or {}
    market_cap = detail.get("market_cap")
    exit_cap = base.get("exit_cap")
    if market_cap is not None and exit_cap is not None:
        ws["K180"] = round(float(market_cap), _RATE_DP)
        ws["K181"] = round(float(exit_cap), _RATE_DP)
    elif noi and price and price > 0:
        # No scenario ran, so there is no resolved anchor. Write the entry
        # cap and leave K181's own formula alone rather than inventing a
        # terminal cap off a number that is not a market cap.
        ws["K180"] = round(noi / price, _PERCENT_DP)
    else:
        logger.warning(
            "No resolved market cap and no entry cap (NOI=%r, price=%r) — "
            "leaving the XLSM's own cap-rate defaults in place, including "
            "its entry+50bp terminal formula, which the model does not use.",
            noi, price)
    #
    # K182 is the template's cost of sale — the cell our disposition
    # assumption belongs in. (The scoped backlog pointed at F254 in the
    # waterfall block instead; that is the GP disposition FEE, part of the
    # promote structure, correctly left at 0 because we model no GP fees.
    # Writing the broker cost there would leave this 3.5% still charging
    # and double-count the sale.)
    ws["K182"] = round(costs["disposition_cost_pct"], _PERCENT_DP)


# ── Distribution Waterfall (rows 251-261) ────────────────────────────

def _write_waterfall(ws, terms, am_fee_pct: float):
    """Write the fund's resolved partnership terms.

    Replaces a GP_EQUITY_SHARE / GP_AM_FEE_RATE / GP_PROMOTE_PCT
    environment-variable block that defaulted to 6% GP equity against a
    `GP_COINVEST_PCT` of 10%. The env vars are deleted, not re-defaulted
    (scoped-backlog rule 2): an assumption nobody can find in config is
    not an auditable assumption.

    **Tier mapping.** The template has four hurdle rows, which is not
    four hurdles: H259/H260/H261 each chain to the row above
    (`=+H258`), so writing the pref rate into H258 sets all four to the
    same rate and the structure collapses to the single hurdle
    `model.waterfall` implements. The three promote cells therefore all
    take one `promote_split`. J259 = `I259+(1-I259)*$J$253` — promote
    plus the GP's pari-passu share of the residual — which is the same
    construction as ours, promote computed on the LP-attributable
    residual only.

    What the mapping does NOT fix is that H257 is labelled "IRR Hurdle"
    while our pref is an accrual account. Same rate, different math; see
    the module docstring.
    """
    ws["H59"] = terms.gp_coinvest_pct

    ws["C253"] = cfg.GP_ENTITY_NAME
    ws["C254"] = cfg.LP_ENTITY_NAME

    # GP fees — we charge neither.
    ws["F253"] = _NO_GP_FEE        # Acquisition fee
    ws["F254"] = _NO_GP_FEE        # Disposition fee

    # Asset management fee
    ws["G253"] = _AM_FEE_BASIS_LABEL
    ws["G254"] = am_fee_pct
    ws["I254"] = _YES              # Fees accrue

    # Include GP fees in analysis
    ws["I251"] = _YES

    # Preferred return. Overwrites `IF(H64>0, 0.08, IF(H64=0, 0.06,"n/a"))`
    # with a value — the same precedent as K181. That formula made the
    # pref depend on whether the deal was levered, which is not a term in
    # the LPA, and returned 6% for an all-equity case the model no longer
    # runs at all.
    ws["H258"] = terms.pref_rate

    # Promote tiers 2-4, all sitting at the single hurdle above.
    for row in _PROMOTE_TIER_ROWS:
        ws.cell(row=row, column=_COL_PROMOTE).value = terms.promote_split


def _write_divergence_disclosures(ws):
    """Stamp the two structural divergences INTO the workbook.

    Until 2026-08-10 they were recorded only in this module's docstring —
    the one place the analyst reading the workbook will never look. The
    module docstring carries the full reasoning; these two lines carry
    the direction and the mechanism to the reader who has only the file.
    Text only — the AST gate is numeric and untouched by design.
    """
    ws[_DISCLOSURE_PREF_CELL] = _PREF_DISCLOSURE
    ws[_DISCLOSURE_AM_FEE_CELL] = _AM_FEE_DISCLOSURE


# ── Summary Sheet Notes ──────────────────────────────────────────────

def _write_summary_notes(ws, cim_data):
    """Clear deal-specific strengths/weaknesses for user to fill."""
    for row in _SUMMARY_STRENGTH_ROWS:
        ws.cell(row=row, column=_SUMMARY_NOTE_COL).value = ""
    for row in _SUMMARY_WEAKNESS_ROWS:
        ws.cell(row=row, column=_SUMMARY_NOTE_COL).value = ""

    # Label headers remain
    ws["F5"] = "STRENGTHS"
    ws["F11"] = "WEAKNESSES"


# ── Helpers ──────────────────────────────────────────────────────────
