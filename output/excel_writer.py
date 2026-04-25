"""
Generates the SS Returns Model as a .xlsx file using openpyxl.

Tabs:
  1. Inputs — purchase price, NRSF, scenario assumptions (yellow = editable)
  2. Base Case — 5-year P&L, exit calc, IRR, MOIC
  3. Bear Case — same structure
  4. Bull Case — same structure
  5. Sensitivity — IRR sensitivity table (price × exit cap)
  6. Max Offer — solved max price and derivation
"""

import os
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, numbers
from registry import ScenarioType
from openpyxl.utils import get_column_letter


# Style constants
HEADER_FONT = Font(name="Calibri", bold=True, size=11, color="FFFFFF")
HEADER_FILL = PatternFill(start_color="003366", end_color="003366", fill_type="solid")
INPUT_FILL = PatternFill(start_color="FFFFCC", end_color="FFFFCC", fill_type="solid")
LABEL_FONT = Font(name="Calibri", size=11)
VALUE_FONT = Font(name="Calibri", size=11)
BOLD_FONT = Font(name="Calibri", bold=True, size=11)
PCT_FORMAT = "0.0%"
CURRENCY_FORMAT = '#,##0'
CURRENCY_FULL = '$#,##0'
MULTIPLE_FORMAT = '0.00"x"'
THIN_BORDER = Border(
    bottom=Side(style="thin", color="999999"),
)


def generate_excel(property_name: str, cim_data, financial_analysis: dict,
                   scenario_results: dict, sensitivity: dict,
                   max_offer: dict, va_results: dict = None,
                   va_max_offer: dict = None, output_dir: str = ".") -> str:
    """
    Generate the SS Returns Model .xlsx.

    Returns: path to generated file.
    """
    wb = Workbook()
    safe_name = _safe_filename(property_name or "Unknown_Property")

    # Tab 1: Inputs
    _build_inputs_tab(wb.active, cim_data, financial_analysis)
    wb.active.title = "Inputs"

    # Tabs 2-4: Scenario cases (static)
    for scen_name in ScenarioType:
        ws = wb.create_sheet(title=f"{scen_name.title()} Case")
        scen = scenario_results.get(scen_name, {})
        _build_scenario_tab(ws, scen_name, scen, cim_data)

    # Tab 5: Value-Add (if applicable)
    if va_results:
        ws_va = wb.create_sheet(title="Value-Add")
        _build_value_add_tab(ws_va, va_results, va_max_offer or {}, cim_data)

    # Tab 6: Sensitivity
    ws_sens = wb.create_sheet(title="Sensitivity")
    _build_sensitivity_tab(ws_sens, sensitivity)

    # Tab 7: Max Offer
    ws_max = wb.create_sheet(title="Max Offer")
    _build_max_offer_tab(ws_max, max_offer, cim_data)

    filename = f"SS_Returns_Model_{safe_name}.xlsx"
    filepath = os.path.join(output_dir, filename)
    wb.save(filepath)
    return filepath


# ── Tab Builders ────────────────────────────────────────────────────

def _build_inputs_tab(ws, cim_data, fin):
    """Build the Inputs tab with editable assumption cells."""
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 20

    row = 1
    row = _write_section_header(ws, row, "Property Information", cols=2)

    inputs = [
        ("Property Name", cim_data.property_name or "TBD", None),
        ("Address", cim_data.address or "TBD", None),
        ("City, State", f"{cim_data.city or 'TBD'}, {cim_data.state or 'TBD'}", None),
        ("Asking Price", cim_data.asking_price, CURRENCY_FULL),
        ("NRSF", cim_data.nrsf, CURRENCY_FORMAT),
        ("Total Units", cim_data.total_units, CURRENCY_FORMAT),
        ("Physical Occupancy", cim_data.physical_occupancy, PCT_FORMAT),
        ("CC %", cim_data.cc_pct, PCT_FORMAT),
        ("Year Built", cim_data.year_built, None),
        ("Price / SF", cim_data.price_per_sf, '$#,##0.00'),
    ]

    for label, val, fmt in inputs:
        row = _write_input_row(ws, row, label, val, fmt, editable=True)

    row += 1
    row = _write_section_header(ws, row, "Financial Summary", cols=2)

    adj_noi = fin.get("adjusted_ttm_noi", {})
    fin_inputs = [
        ("TTM NOI (CIM)", cim_data.ttm_noi, CURRENCY_FULL),
        ("Analyst-Adjusted TTM NOI", adj_noi.get("analyst_adjusted_noi"), CURRENCY_FULL),
        ("TTM Total Revenue", cim_data.ttm_total_revenue, CURRENCY_FULL),
        ("TTM Total Expenses", cim_data.ttm_total_expenses, CURRENCY_FULL),
        ("TTM GPR", cim_data.ttm_gpr, CURRENCY_FULL),
        ("TTM EGR", cim_data.ttm_egr, CURRENCY_FULL),
        ("Other Income", cim_data.other_income, CURRENCY_FULL),
    ]

    for label, val, fmt in fin_inputs:
        row = _write_input_row(ws, row, label, val, fmt)

    row += 1
    row = _write_section_header(ws, row, "Scenario Assumptions", cols=2)

    from config import SCENARIO_DEFAULTS
    for scen_name in ScenarioType:
        params = SCENARIO_DEFAULTS[scen_name]
        ws.cell(row=row, column=1, value=f"── {scen_name.title()} Case ──").font = BOLD_FONT
        row += 1
        for key, val in params.items():
            label = key.replace("_", " ").title()
            fmt = PCT_FORMAT if isinstance(val, float) and val < 1 else None
            row = _write_input_row(ws, row, f"  {label}", val, fmt, editable=True)
        row += 1


def _build_scenario_tab(ws, scen_name: str, scen: dict, cim_data):
    """Build a single scenario tab with 5-year P&L."""
    ws.column_dimensions["A"].width = 28
    for i in range(2, 9):
        ws.column_dimensions[get_column_letter(i)].width = 16

    row = 1
    row = _write_section_header(ws, row, f"{scen_name.title()} Case — 5-Year Unlevered Returns", cols=7)

    # Key metrics
    metrics = [
        ("Total Basis", scen.get("total_basis"), CURRENCY_FULL),
        ("Asking Price", scen.get("asking_price"), CURRENCY_FULL),
        ("CapEx", scen.get("capex"), CURRENCY_FULL),
        ("Entry Cap Rate", scen.get("entry_cap"), PCT_FORMAT),
        ("Exit Cap Rate", scen.get("exit_cap"), PCT_FORMAT),
    ]
    for label, val, fmt in metrics:
        row = _write_input_row(ws, row, label, val, fmt)

    row += 1

    # Year headers
    noi_proj = scen.get("noi_projection", [])
    rev_proj = scen.get("revenue_projection", [])
    exp_proj = scen.get("expense_projection", [])
    years = min(len(noi_proj), 5)

    ws.cell(row=row, column=1, value="").font = BOLD_FONT
    for yr in range(years):
        col = yr + 2
        cell = ws.cell(row=row, column=col, value=f"Year {yr + 1}")
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center")
    row += 1

    # Revenue projection
    if rev_proj:
        ws.cell(row=row, column=1, value="Revenue").font = BOLD_FONT
        for i, val in enumerate(rev_proj[:years]):
            ws.cell(row=row, column=i + 2, value=val).number_format = CURRENCY_FULL
        row += 1

    # Expense projection
    if exp_proj:
        ws.cell(row=row, column=1, value="Expenses").font = BOLD_FONT
        for i, val in enumerate(exp_proj[:years]):
            ws.cell(row=row, column=i + 2, value=val).number_format = CURRENCY_FULL
        row += 1

    # NOI projection
    ws.cell(row=row, column=1, value="Net Operating Income").font = BOLD_FONT
    for i, val in enumerate(noi_proj[:years]):
        cell = ws.cell(row=row, column=i + 2, value=val)
        cell.number_format = CURRENCY_FULL
        cell.font = BOLD_FONT
    row += 1

    # NOI per SF
    noi_per_sf = scen.get("noi_per_sf", [])
    if noi_per_sf:
        ws.cell(row=row, column=1, value="NOI / SF").font = LABEL_FONT
        for i, val in enumerate(noi_per_sf[:years]):
            ws.cell(row=row, column=i + 2, value=val).number_format = '$#,##0.00'
        row += 1

    row += 1

    # Exit & Returns
    row = _write_section_header(ws, row, "Exit & Returns", cols=2)
    exit_items = [
        ("Year 5 NOI", noi_proj[-1] if noi_proj else None, CURRENCY_FULL),
        ("Exit Cap Rate", scen.get("exit_cap"), PCT_FORMAT),
        ("Exit Value", scen.get("exit_value"), CURRENCY_FULL),
    ]
    for label, val, fmt in exit_items:
        row = _write_input_row(ws, row, label, val, fmt)

    row += 1
    return_items = [
        ("5-Year Unlevered IRR", scen.get("irr"), PCT_FORMAT),
        ("5-Year MOIC", scen.get("moic"), MULTIPLE_FORMAT),
        ("Year 1 Yield on Cost", scen.get("yield_on_cost"), PCT_FORMAT),
    ]
    for label, val, fmt in return_items:
        cell_a = ws.cell(row=row, column=1, value=label)
        cell_a.font = BOLD_FONT
        cell_b = ws.cell(row=row, column=2, value=val)
        if fmt and val is not None:
            cell_b.number_format = fmt
        cell_b.font = BOLD_FONT
        row += 1

    row += 1

    # Cash flows
    row = _write_section_header(ws, row, "Cash Flow Summary", cols=7)
    cfs = scen.get("cash_flows", [])
    ws.cell(row=row, column=1, value="").font = BOLD_FONT
    cf_labels = ["Year 0 (Invest)"] + [f"Year {i+1}" for i in range(len(cfs) - 1)]
    for i, label in enumerate(cf_labels):
        cell = ws.cell(row=row, column=i + 1 + 1, value=label)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center")
    # Extend column 1 header
    cell = ws.cell(row=row, column=1, value="")
    cell.fill = HEADER_FILL
    row += 1

    ws.cell(row=row, column=1, value="Cash Flow").font = BOLD_FONT
    for i, cf in enumerate(cfs):
        ws.cell(row=row, column=i + 2, value=cf).number_format = CURRENCY_FULL
    row += 1


def _build_sensitivity_tab(ws, sensitivity: dict):
    """Build IRR sensitivity table."""
    ws.column_dimensions["A"].width = 18
    for i in range(2, 15):
        ws.column_dimensions[get_column_letter(i)].width = 12

    row = 1
    row = _write_section_header(ws, row, "IRR Sensitivity: Purchase Price vs Exit Cap Rate", cols=10)
    row += 1

    price_labels = sensitivity.get("price_labels", [])
    price_values = sensitivity.get("price_values", [])
    cap_labels = sensitivity.get("cap_labels", [])
    grid = sensitivity.get("irr_grid", [])

    if not grid:
        ws.cell(row=row, column=1, value="Insufficient data for sensitivity analysis.")
        return

    # Header row: exit cap labels
    ws.cell(row=row, column=1, value="Price \\ Exit Cap").font = BOLD_FONT
    for j, cap_label in enumerate(cap_labels):
        cell = ws.cell(row=row, column=j + 2, value=cap_label)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center")
    row += 1

    # Data rows
    green_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    red_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    yellow_fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")

    for i, price_label in enumerate(price_labels):
        price_val = price_values[i] if i < len(price_values) else 0
        ws.cell(row=row, column=1, value=f"{price_label} (${price_val:,.0f})").font = BOLD_FONT

        if i < len(grid):
            for j, irr in enumerate(grid[i]):
                cell = ws.cell(row=row, column=j + 2)
                if irr is not None:
                    cell.value = irr
                    cell.number_format = PCT_FORMAT
                    # Color code
                    if irr >= 0.12:
                        cell.fill = green_fill
                    elif irr >= 0.10:
                        cell.fill = yellow_fill
                    else:
                        cell.fill = red_fill
                else:
                    cell.value = "N/A"
                cell.alignment = Alignment(horizontal="center")
        row += 1

    row += 2
    ws.cell(row=row, column=1, value="Legend:").font = BOLD_FONT
    row += 1
    c = ws.cell(row=row, column=1, value="  ≥ 12% IRR")
    c.fill = green_fill
    row += 1
    c = ws.cell(row=row, column=1, value="  10-12% IRR")
    c.fill = yellow_fill
    row += 1
    c = ws.cell(row=row, column=1, value="  < 10% IRR")
    c.fill = red_fill


def _build_max_offer_tab(ws, max_offer: dict, cim_data):
    """Build Max Offer derivation tab."""
    ws.column_dimensions["A"].width = 35
    ws.column_dimensions["B"].width = 20

    row = 1
    row = _write_section_header(ws, row, "Maximum Offer Price Derivation", cols=2)
    row += 1

    items = [
        ("Target IRR", max_offer.get("target_irr"), PCT_FORMAT),
        ("Solver Converged", "Yes" if max_offer.get("converged") else "No", None),
        ("Iterations", max_offer.get("iterations"), None),
        ("", None, None),
        ("Maximum Purchase Price", max_offer.get("max_price"), CURRENCY_FULL),
        ("Implied Entry Cap Rate", max_offer.get("implied_entry_cap"), PCT_FORMAT),
        ("CapEx Budget", max_offer.get("capex"), CURRENCY_FULL),
        ("Total Basis at Max Price", max_offer.get("total_basis"), CURRENCY_FULL),
        ("Achieved IRR", max_offer.get("achieved_irr"), PCT_FORMAT),
    ]

    for label, val, fmt in items:
        if not label:
            row += 1
            continue
        cell_a = ws.cell(row=row, column=1, value=label)
        cell_a.font = BOLD_FONT if "Maximum" in label or "Achieved" in label else LABEL_FONT
        cell_b = ws.cell(row=row, column=2, value=val)
        if fmt and val is not None and not isinstance(val, str):
            cell_b.number_format = fmt
        cell_b.font = BOLD_FONT if "Maximum" in label or "Achieved" in label else VALUE_FONT
        row += 1

    # Comparison to asking
    if cim_data.asking_price and max_offer.get("max_price"):
        row += 1
        asking = cim_data.asking_price
        mp = max_offer["max_price"]
        discount = (asking - mp) / asking if asking else 0

        row = _write_section_header(ws, row, "Comparison to Asking", cols=2)
        comp_items = [
            ("Asking Price", asking, CURRENCY_FULL),
            ("Max Offer Price", mp, CURRENCY_FULL),
            ("Discount to Asking", discount, PCT_FORMAT),
            ("Dollar Difference", asking - mp, CURRENCY_FULL),
        ]
        for label, val, fmt in comp_items:
            ws.cell(row=row, column=1, value=label).font = LABEL_FONT
            cell = ws.cell(row=row, column=2, value=val)
            if fmt and val is not None:
                cell.number_format = fmt
            row += 1


def _build_value_add_tab(ws, va_results: dict, va_max_offer: dict, cim_data):
    """Build Value-Add tab with annual summary across all three scenarios."""
    ws.column_dimensions["A"].width = 28
    for i in range(2, 9):
        ws.column_dimensions[get_column_letter(i)].width = 16

    green_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")

    row = 1
    row = _write_section_header(ws, row, "Value-Add Model — Scenario Comparison", cols=4)
    row += 1

    # Key assumptions
    base = va_results.get(ScenarioType.BASE, {})
    row = _write_section_header(ws, row, "Deal Overview", cols=4)
    overview = [
        ("Asking Price", base.get("asking_price"), CURRENCY_FULL),
        ("CapEx", base.get("capex"), CURRENCY_FULL),
        ("Total Basis", base.get("total_basis"), CURRENCY_FULL),
        ("Current Occupancy", base.get("current_occupancy"), PCT_FORMAT),
        ("In-Place Rent/SF/Mo", base.get("in_place_rent_psf"), '$#,##0.00'),
        ("Market Rent/SF/Mo", base.get("market_rent_psf"), '$#,##0.00'),
    ]
    for label, val, fmt in overview:
        row = _write_input_row(ws, row, label, val, fmt)
    row += 1

    # Scenario comparison header
    ws.cell(row=row, column=1, value="").font = BOLD_FONT
    for j, scen_name in enumerate(("Bear", "Base", "Bull")):
        cell = ws.cell(row=row, column=j + 2, value=scen_name)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center")
    row += 1

    # Scenario metrics
    metric_rows = [
        ("Target Occupancy", "target_occupancy", PCT_FORMAT),
        ("Months to Stabilize", "months_to_stabilize", '#,##0'),
        ("Target Rent/SF/Mo", "target_rent_psf", '$#,##0.00'),
        ("Stabilized NOI", "stabilized_noi", CURRENCY_FULL),
        ("Entry Cap Rate", "entry_cap", PCT_FORMAT),
        ("Exit Cap Rate", "exit_cap", PCT_FORMAT),
        ("Exit Value", "exit_value", CURRENCY_FULL),
        ("", None, None),
        ("5-Year Unlevered IRR", "irr", PCT_FORMAT),
        ("5-Year MOIC", "moic", MULTIPLE_FORMAT),
        ("Stabilized Yield/Cost", "yield_on_cost", PCT_FORMAT),
        ("Development Spread", "development_spread", PCT_FORMAT),
    ]

    for label, key, fmt in metric_rows:
        if not label:
            row += 1
            continue
        is_return = key in ("irr", "moic", "yield_on_cost", "development_spread")
        ws.cell(row=row, column=1, value=label).font = BOLD_FONT if is_return else LABEL_FONT
        for j, scen_name in enumerate(ScenarioType):
            scen = va_results.get(scen_name, {})
            val = scen.get(key)
            cell = ws.cell(row=row, column=j + 2, value=val)
            if fmt and val is not None:
                cell.number_format = fmt
            cell.font = BOLD_FONT if is_return else VALUE_FONT
            cell.alignment = Alignment(horizontal="center")
        row += 1

    row += 1

    # Annual NOI projection for base case
    row = _write_section_header(ws, row, "Base Case — Annual Projection", cols=7)
    annual_noi = base.get("annual_noi", [])
    annual_rev = base.get("annual_revenue", [])
    annual_exp = base.get("annual_expenses", [])
    years = min(len(annual_noi), 5)

    ws.cell(row=row, column=1, value="").font = BOLD_FONT
    for yr in range(years):
        cell = ws.cell(row=row, column=yr + 2, value=f"Year {yr + 1}")
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center")
    row += 1

    if annual_rev:
        ws.cell(row=row, column=1, value="Revenue").font = LABEL_FONT
        for i, val in enumerate(annual_rev[:years]):
            ws.cell(row=row, column=i + 2, value=val).number_format = CURRENCY_FULL
        row += 1

    if annual_exp:
        ws.cell(row=row, column=1, value="Expenses").font = LABEL_FONT
        for i, val in enumerate(annual_exp[:years]):
            ws.cell(row=row, column=i + 2, value=val).number_format = CURRENCY_FULL
        row += 1

    ws.cell(row=row, column=1, value="Net Operating Income").font = BOLD_FONT
    for i, val in enumerate(annual_noi[:years]):
        cell = ws.cell(row=row, column=i + 2, value=val)
        cell.number_format = CURRENCY_FULL
        cell.font = BOLD_FONT
    row += 1

    noi_per_sf = base.get("noi_per_sf", [])
    if noi_per_sf:
        ws.cell(row=row, column=1, value="NOI / SF").font = LABEL_FONT
        for i, val in enumerate(noi_per_sf[:years]):
            ws.cell(row=row, column=i + 2, value=val).number_format = '$#,##0.00'
        row += 1

    row += 1

    # Cash flows
    row = _write_section_header(ws, row, "Base Case — Cash Flow Summary", cols=7)
    cfs = base.get("cash_flows", [])
    ws.cell(row=row, column=1, value="").font = BOLD_FONT
    cf_labels = ["Year 0 (Invest)"] + [f"Year {i+1}" for i in range(len(cfs) - 1)]
    for i, label in enumerate(cf_labels):
        cell = ws.cell(row=row, column=i + 2, value=label)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center")
    cell = ws.cell(row=row, column=1, value="")
    cell.fill = HEADER_FILL
    row += 1

    ws.cell(row=row, column=1, value="Cash Flow").font = BOLD_FONT
    for i, cf in enumerate(cfs):
        ws.cell(row=row, column=i + 2, value=cf).number_format = CURRENCY_FULL
    row += 1

    row += 1

    # VA Max offer
    if va_max_offer and va_max_offer.get("max_price"):
        row = _write_section_header(ws, row, "Value-Add Max Offer Price", cols=2)
        va_items = [
            ("Max Price (10% VA IRR)", va_max_offer.get("max_price"), CURRENCY_FULL),
            ("Implied Entry Cap", va_max_offer.get("implied_entry_cap"), PCT_FORMAT),
            ("Achieved IRR", va_max_offer.get("achieved_irr"), PCT_FORMAT),
        ]
        for label, val, fmt in va_items:
            cell_a = ws.cell(row=row, column=1, value=label)
            cell_a.font = BOLD_FONT
            cell_b = ws.cell(row=row, column=2, value=val)
            if fmt and val is not None:
                cell_b.number_format = fmt
            cell_b.font = BOLD_FONT
            row += 1


# ── Helpers ─────────────────────────────────────────────────────────

def _write_section_header(ws, row: int, title: str, cols: int = 2) -> int:
    """Write a section header row and return next row."""
    for col in range(1, cols + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    ws.cell(row=row, column=1, value=title)
    return row + 1


def _write_input_row(ws, row: int, label: str, value, fmt=None, editable=False) -> int:
    """Write a label/value row. Yellow fill if editable."""
    ws.cell(row=row, column=1, value=label).font = LABEL_FONT
    cell = ws.cell(row=row, column=2, value=value)
    cell.font = VALUE_FONT
    if fmt and value is not None and not isinstance(value, str):
        cell.number_format = fmt
    if editable:
        cell.fill = INPUT_FILL
    return row + 1


def _safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in name).strip().replace(" ", "_")
