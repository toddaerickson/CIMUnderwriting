"""
Template Writer — populate the XLSM underwriting template with CIM data
extracted by the analysis pipeline.

Writes only to INPUT cells (not formulas). The user opens the .xlsm in
Excel and formulas recalculate automatically.

Place your .xlsm template in the project root and set TEMPLATE_FILENAME below
(or override via the UW_TEMPLATE_PATH environment variable).
"""

import logging
import os
import shutil
from datetime import datetime

import openpyxl

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
    # Row 155: Bank/Merchant fees — % of EGR, skip (not in our categories)
    # Row 156: Miscellaneous — skip
    # Row 157: Management fee — % of EGR
    "insurance":    (158, False),
    "property_tax": (159, False),
    "cap_reserve":  (164, False),
}


def generate_template(
    cim_data,
    financial_analysis: dict,
    scenario_results: dict = None,
    max_offer: dict = None,
    output_dir: str = ".",
    property_name: str = "",
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

    Returns:
        Path to the generated .xlsm file
    """
    if not os.path.exists(TEMPLATE_PATH):
        raise FileNotFoundError(f"Template not found: {TEMPLATE_PATH}")

    # Build output filename
    safe_name = _safe_filename(property_name or cim_data.property_name or "Deal")
    out_path = os.path.join(output_dir, f"UW_{safe_name}.xlsm")

    # Copy template
    shutil.copy2(TEMPLATE_PATH, out_path)

    # Open with VBA preservation
    wb = openpyxl.load_workbook(out_path, keep_vba=True)
    ws = wb["Underwriting"]
    ws_summary = wb["Summary"]

    # Populate sections
    _write_property_description(ws, cim_data)
    _write_investment_cf(ws, cim_data)
    _write_financing_defaults(ws)
    _write_growth_rates(ws)
    _write_stabilization(ws, cim_data)
    _write_unit_mix(ws, cim_data)
    _write_other_income(ws, cim_data)
    _write_vacancy(ws, cim_data)
    _write_opex(ws, cim_data, financial_analysis)
    _write_capex(ws, cim_data)
    _write_reversion(ws, cim_data, financial_analysis)
    _write_waterfall(ws)
    _write_summary_notes(ws_summary, cim_data)

    wb.save(out_path)
    wb.close()

    logger.info("  Template: %s", out_path)
    return out_path


# ── Property Description (rows 7-18) ─────────────────────────────────

def _write_property_description(ws, cim_data):
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
    today = datetime.now()
    if today.month == 12:
        start = datetime(today.year + 1, 1, 1)
    else:
        start = datetime(today.year, today.month + 1, 1)
    ws["F17"] = start

    # Sale month: 60 (5-year hold)
    ws["D182"] = 60


# ── Investment Cash Flows (rows 20-47) ───────────────────────────────

def _write_investment_cf(ws, cim_data):
    """Fill purchase price and capex."""
    if cim_data.asking_price:
        ws["K23"] = cim_data.asking_price

    capex = cim_data.capex_estimate or 0
    if capex > 0:
        ws["B30"] = "Deferred Maintenance"
        ws["K30"] = capex
        ws["E30"] = 1
        ws["F30"] = 6


# ── Financing Defaults ────────────────────────────────────────────────

def _write_financing_defaults(ws):
    """Set reasonable debt assumptions.

    Defaults to 0% LTC (all equity) since the user hasn't specified
    leverage. They can adjust in Excel.
    """
    # LTC = 0 means no debt — formulas handle this
    ws["H64"] = 0       # Senior loan LTC
    ws["H65"] = 0       # Junior loan LTC (named range for K65)
    # Loan terms are reasonable defaults already in template
    ws["F73"] = 60      # Term (months)
    ws["G73"] = 12      # IO period
    ws["H73"] = 360     # Amort
    ws["I73"] = 0.065   # Rate


# ── Growth Rates (rows 100-106) ──────────────────────────────────────

def _write_growth_rates(ws):
    """Set annual growth assumptions.

    Year 1 = 0% (in-place), years 2+ = 3%.
    """
    # Rows: 101=in-place rent, 102=stabilized rent, 103=other income,
    #        104=OpEx, 105=property tax, 106=capex
    for row in range(101, 107):
        ws.cell(row=row, column=3).value = 0       # Year 1: 0%
        ws.cell(row=row, column=4).value = 0.03    # Year 2
        ws.cell(row=row, column=5).value = 0.03    # Year 3
        ws.cell(row=row, column=6).value = 0.03    # Year 4
        ws.cell(row=row, column=7).value = 0.03    # Year 5
        ws.cell(row=row, column=8).value = 0.03    # Year 6


# ── Stabilization ────────────────────────────────────────────────────

def _write_stabilization(ws, cim_data):
    """Set stabilization timing."""
    occ = cim_data.physical_occupancy or 0.90

    if occ >= 0.88:
        # Already stabilized — month 1
        ws["K101"] = 1   # Begin
        ws["K102"] = 1   # Complete
    else:
        # Value-add — 24 month stabilization
        ws["K101"] = 1
        ws["K102"] = 24


# ── Unit Mix (rows 111-131) ──────────────────────────────────────────

def _write_unit_mix(ws, cim_data):
    """Populate unit mix rows from CIMData.unit_mix."""
    units = cim_data.unit_mix or []

    # Clear all unit mix rows first (rows 111-131)
    for row in range(UNIT_MIX_START_ROW, UNIT_MIX_END_ROW + 1):
        ws.cell(row=row, column=2).value = "[Unit Type]"  # B: label
        ws.cell(row=row, column=3).value = 0              # C: count
        ws.cell(row=row, column=4).value = 0              # D: avg SF
        ws.cell(row=row, column=5).value = 1              # E: % stabilized
        ws.cell(row=row, column=7).value = 0              # G: in-place rent/unit/mo
        ws.cell(row=row, column=9).value = 0              # I: stabilized rent/unit/mo
        ws.cell(row=row, column=13).value = "Non-Climate"  # M: type

    # Fill with actual data
    occ = cim_data.physical_occupancy or 0.90
    is_stabilized = occ >= 0.88

    for i, unit in enumerate(units):
        if i >= (UNIT_MIX_END_ROW - UNIT_MIX_START_ROW + 1):
            logger.warning("Unit mix has %d types but template has %d slots",
                           len(units), UNIT_MIX_END_ROW - UNIT_MIX_START_ROW + 1)
            break

        row = UNIT_MIX_START_ROW + i
        label = unit.size_label or f"Type {i+1}"

        ws.cell(row=row, column=2).value = label                      # B: label
        ws.cell(row=row, column=3).value = unit.count or 0            # C: count
        ws.cell(row=row, column=4).value = unit.sf or 0               # D: avg SF
        ws.cell(row=row, column=5).value = 1 if is_stabilized else 0  # E: % stabilized
        ws.cell(row=row, column=7).value = unit.rate or 0             # G: in-place $/unit/mo
        # I column: stabilized rent — use in-place for now, user adjusts
        # Note: I{row} has formula =+G{row} in template for rows that had
        # data. For overwritten rows we set explicitly.
        ws.cell(row=row, column=9).value = unit.rate or 0             # I: stabilized $/unit/mo

        # Climate type
        if unit.climate_controlled:
            ws.cell(row=row, column=13).value = "Climate"
        else:
            ws.cell(row=row, column=13).value = "Non-Climate"


# ── Other Income (rows 137-143) ──────────────────────────────────────

def _write_other_income(ws, cim_data):
    """Populate other income lines."""
    other = cim_data.other_income or 0

    # Clear defaults
    for row in [138, 139, 140, 141, 142, 143]:
        ws.cell(row=row, column=7).value = 0    # G: in-place
        ws.cell(row=row, column=9).value = 0    # I: stabilized

    if other > 0:
        # Put all other income into "Miscellaneous" parking row
        # as annual amount (template divides by 12 in monthly calcs)
        ws["B142"] = "Other Income"
        ws["G142"] = other
        ws["I142"] = other


# ── Vacancy (rows 146-147) ───────────────────────────────────────────

def _write_vacancy(ws, cim_data):
    """Set vacancy and credit loss assumptions."""
    occ = cim_data.physical_occupancy or 0.90

    # In-place vacancy
    in_place_vacancy = max(0, 1.0 - occ)
    ws["G146"] = round(in_place_vacancy, 4)

    # Stabilized vacancy — target 10% for stabilized properties
    if occ >= 0.88:
        ws["I146"] = 0.10
    else:
        ws["I146"] = 0.10  # Target after stabilization

    # Credit loss
    ws["G147"] = 0.01
    ws["I147"] = 0.01


# ── Operating Expenses (rows 150-159, 164) ───────────────────────────

def _write_opex(ws, cim_data, financial_analysis: dict):
    """
    Populate OpEx from CIM data and analyst adjustments.

    In-Place column (G): CIM actual $/SF/year
    Stabilized column (I): analyst-adjusted $/SF/year
    """
    nrsf = cim_data.nrsf or 1
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
        if cim_value is not None:
            in_place_psf = cim_value / nrsf
        else:
            in_place_psf = 0

        # Stabilized: analyst-adjusted as $/SF/year
        if adjusted_value is not None:
            stabilized_psf = adjusted_value / nrsf
        else:
            stabilized_psf = in_place_psf

        ws.cell(row=row, column=7).value = round(in_place_psf, 2)   # G: in-place
        ws.cell(row=row, column=9).value = round(stabilized_psf, 2)  # I: stabilized

    # Management fee — % of EGR (row 157)
    mgmt_pct = cim_data.mgmt_fee_pct or 0.06
    ws["G157"] = mgmt_pct
    ws["I157"] = mgmt_pct

    # Bank/merchant fees — small % of EGR (row 155)
    ws["G155"] = 0.01
    ws["I155"] = 0.0125


# ── Capital Expenditures (row 164) ───────────────────────────────────

def _write_capex(ws, cim_data):
    """Set capital reserve assumption."""
    # Capital reserves $/SF/year — leave at template default or set
    # In-place: 0 (current owner may not be reserving)
    # Stabilized: $0.15/SF (conservative)
    ws["G164"] = 0
    ws["I164"] = 0.15


# ── Reversion / Sale Assumptions ─────────────────────────────────────

def _write_reversion(ws, cim_data, financial_analysis: dict):
    """Set cap rate and sale assumptions."""
    noi = financial_analysis.get("adjusted_ttm_noi", {}).get("analyst_adjusted_noi")
    price = cim_data.asking_price

    # Entry cap rate
    if noi and price and price > 0:
        entry_cap = noi / price
    else:
        entry_cap = 0.065

    ws["K180"] = round(entry_cap, 4)     # Market cap rate today
    # K181 is formula = K180 + 0.005 (terminal cap = entry + 50bps)
    ws["K182"] = 0.035                   # Selling costs


# ── Distribution Waterfall (rows 251-261) ────────────────────────────

def _write_waterfall(ws):
    """
    Set fund structure defaults. Override via environment variables:
      GP_NAME, GP_EQUITY_SHARE, GP_AM_FEE_RATE, GP_PROMOTE_PCT
    """
    gp_name = os.environ.get("GP_NAME", "GP")
    gp_equity = float(os.environ.get("GP_EQUITY_SHARE", "0.06"))
    am_fee = float(os.environ.get("GP_AM_FEE_RATE", "0.01"))
    promote = float(os.environ.get("GP_PROMOTE_PCT", "0.20"))

    # GP equity share
    ws["H59"] = gp_equity

    # GP name
    ws["C253"] = gp_name
    ws["C254"] = "LP Group"

    # GP fees
    ws["F253"] = 0        # Acquisition fee
    ws["F254"] = 0        # Disposition fee

    # Asset management fee
    ws["G253"] = "% of LP Equity"
    ws["G254"] = am_fee
    ws["I254"] = "Yes"    # Fees accrue

    # Include GP fees in analysis
    ws["I251"] = "Yes"

    # Promote tiers
    ws["I259"] = promote   # 2nd tier promote
    ws["I260"] = promote   # 3rd tier promote
    ws["I261"] = promote   # 4th tier promote

    # Pref return: H258 is formula = IF(H64>0, 0.08, IF(H64=0, 0.06, "n/a"))
    # This auto-sets 8% with debt, 6% without — leave as formula


# ── Summary Sheet Notes ──────────────────────────────────────────────

def _write_summary_notes(ws, cim_data):
    """Clear deal-specific strengths/weaknesses for user to fill."""
    for row in range(6, 11):
        ws.cell(row=row, column=6).value = ""   # F6:F10 = strengths
    for row in range(12, 17):
        ws.cell(row=row, column=6).value = ""   # F12:F16 = weaknesses

    # Label headers remain
    ws["F5"] = "STRENGTHS"
    ws["F11"] = "WEAKNESSES"


# ── Helpers ──────────────────────────────────────────────────────────

def _safe_filename(name: str) -> str:
    """Convert property name to safe filename."""
    # Remove characters that aren't safe for filenames
    safe = "".join(c for c in name if c.isalnum() or c in (" ", "-", "_"))
    return safe.strip().replace(" ", "_")[:60]
