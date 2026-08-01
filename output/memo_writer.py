"""
Generates the SS Investment Memo as a .docx file using python-docx.

Follows the exact section structure of the SS Investment Memo Template.
"""

import os

import config as cfg
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT


def generate_memo(property_name: str, cim_data, gate_results: list,
                  market_analysis: dict, physical_analysis: dict,
                  financial_analysis: dict, rent_analysis: dict,
                  scenario_results: dict, value_add: dict,
                  risk_analysis: dict, max_offer: dict,
                  va_results: dict = None, va_max_offer: dict = None,
                  checks: list = None, sources_uses: dict = None,
                  levered: dict = None, debt: dict = None,
                  output_dir: str = ".") -> str:
    """
    Generate the SS Investment Memo .docx.

    Returns: path to generated file.
    """
    doc = Document()

    # Set default font
    style = doc.styles["Normal"]
    font = style.font
    font.name = "Calibri"
    font.size = Pt(11)

    safe_name = _safe_filename(property_name or "Unknown_Property")
    filename = f"SS_Investment_Memo_{safe_name}.docx"
    filepath = os.path.join(output_dir, filename)

    # ── Title Page ──────────────────────────────────────────────
    _add_title_page(doc, cim_data)

    # ── Section 1: Investment Summary ───────────────────────────
    _add_section_1(doc, cim_data, gate_results, scenario_results, max_offer,
                   checks)

    # ── Section 2: Market Overview ──────────────────────────────
    _add_section_2(doc, market_analysis)

    # ── Section 3: Property Description ─────────────────────────
    _add_section_3(doc, physical_analysis)

    # ── Section 4: Financial Analysis ───────────────────────────
    _add_section_4(doc, financial_analysis, cim_data)

    # ── Section 5: Unit Mix & Rent Analysis ─────────────────────
    _add_section_5(doc, rent_analysis)

    # ── Section 6: Valuation & Returns ──────────────────────────
    _add_section_6(doc, scenario_results, max_offer, sources_uses,
                   levered, debt)

    # ── Section 7: Value-Add Opportunities ──────────────────────
    _add_section_7(doc, value_add, va_results, va_max_offer)

    # ── Section 8: Risk Analysis ────────────────────────────────
    _add_section_8(doc, risk_analysis)

    # ── Section 9: Due Diligence Items ──────────────────────────
    _add_section_9(doc)

    # ── Section 10: Recommendation ──────────────────────────────
    _add_section_10(doc, gate_results, scenario_results, max_offer, risk_analysis, cim_data)

    doc.save(filepath)
    return filepath


# ── Section Builders ────────────────────────────────────────────────

def _hold_years(scenarios: dict, noi_key: str = "noi_projection") -> int:
    """Hold length these scenarios were computed on. Falls back to the
    config default rather than a literal 5, so the two cannot diverge."""
    for scen in (scenarios or {}).values():
        if isinstance(scen, dict):
            hold = scen.get("hold_years") or len(scen.get(noi_key) or [])
            if hold:
                return hold
    return cfg.DEFAULT_HOLD_YEARS


def _add_title_page(doc, cim_data):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("\n\n\nSELF-STORAGE INVESTMENT MEMO\n\n")
    run.bold = True
    run.font.size = Pt(24)
    run.font.color.rgb = RGBColor(0, 51, 102)

    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    name = cim_data.property_name or "Property Name TBD"
    run2 = p2.add_run(name)
    run2.bold = True
    run2.font.size = Pt(18)

    p3 = doc.add_paragraph()
    p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
    addr = cim_data.address or "Address TBD"
    city_state = f"{cim_data.city or 'City'}, {cim_data.state or 'ST'}"
    p3.add_run(f"{addr}\n{city_state}").font.size = Pt(14)

    p4 = doc.add_paragraph()
    p4.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p4.add_run("\n\nPrepared by CIM Analyst\nConfidential").font.size = Pt(11)

    doc.add_page_break()


def _add_section_1(doc, cim_data, gate_results, scenario_results, max_offer,
                   checks=None):
    doc.add_heading("1. Investment Summary", level=1)

    # Key metrics table
    table = doc.add_table(rows=1, cols=2)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text = "Metric"
    hdr[1].text = "Value"

    metrics = [
        ("Property", cim_data.property_name or "TBD"),
        ("Location", f"{cim_data.city or 'TBD'}, {cim_data.state or 'TBD'}"),
        ("Asking Price", _fmt_currency(cim_data.asking_price)),
        ("NRSF", _fmt_number(cim_data.nrsf, suffix=" SF")),
        ("Total Units", str(cim_data.total_units or "TBD")),
        ("Physical Occupancy", _fmt_pct(cim_data.physical_occupancy)),
        ("Economic Occupancy", _fmt_pct(cim_data.economic_occupancy)),
        ("Price / SF", _fmt_currency(cim_data.price_per_sf)),
        ("Year Built", str(cim_data.year_built or "TBD")),
    ]
    for label, val in metrics:
        row = table.add_row().cells
        row[0].text = label
        row[1].text = val

    _add_occupancy_spread_note(doc, cim_data)

    _add_model_checks(doc, checks)

    doc.add_paragraph()

    # Gate summary
    doc.add_heading("Screening Gates", level=2)
    gate_table = doc.add_table(rows=1, cols=4)
    gate_table.style = "Light Grid Accent 1"
    gh = gate_table.rows[0].cells
    gh[0].text = "Gate"
    gh[1].text = "Threshold"
    gh[2].text = "Actual"
    gh[3].text = "Result"

    for g in gate_results:
        row = gate_table.add_row().cells
        row[0].text = g["name"]
        row[1].text = str(g["threshold"])
        row[2].text = str(g["actual"])
        row[3].text = g["result"]

    doc.add_paragraph()

    # Returns snapshot
    if scenario_results:
        doc.add_heading("Returns Snapshot (Unlevered)", level=2)
        ret_table = doc.add_table(rows=1, cols=4)
        ret_table.style = "Light Grid Accent 1"
        rh = ret_table.rows[0].cells
        rh[0].text = "Metric"
        rh[1].text = "Bear"
        rh[2].text = "Base"
        rh[3].text = "Bull"

        ret_hold = _hold_years(scenario_results)
        for label, key in [("Yr1 Yield on Cost", "yield_on_cost"),
                           (f"{ret_hold}-Yr IRR", "irr"),
                           (f"{ret_hold}-Yr MOIC", "moic")]:
            row = ret_table.add_row().cells
            row[0].text = label
            for i, scen in enumerate(["bear", "base", "bull"]):
                val = scenario_results.get(scen, {}).get(key)
                if key == "moic":
                    row[i + 1].text = f"{val:.2f}x" if val else "N/A"
                else:
                    row[i + 1].text = _fmt_pct(val)

    # Max offer
    if max_offer:
        doc.add_paragraph()
        mp = max_offer.get("max_price")
        doc.add_paragraph(
            f"Maximum Offer Price (for {max_offer.get('target_irr', 0.10):.0%} "
            f"Base Case IRR): {_fmt_currency(mp)}"
        ).bold = True


def _add_model_checks(doc, checks):
    """Model error-check register, section 1. Findings only — the full
    register (passes and not-testable rows included) is the Excel Checks
    sheet. A memo that lists ten green checks buries the one red one."""
    if not checks:
        return

    failed = [c for c in checks if c.get("status") == "fail"]
    doc.add_paragraph()
    if not failed:
        tested = sum(1 for c in checks if c.get("status") == "pass")
        skipped = len(checks) - tested
        doc.add_paragraph(
            f"Model checks: {tested} of {len(checks)} integrity checks passed "
            f"and none were flagged"
            + (f"; {skipped} were not testable from the data supplied."
               if skipped else ".")
        )
        return

    doc.add_heading("Model Checks", level=2)
    doc.add_paragraph(
        f"{len(failed)} of {len(checks)} integrity checks flagged. Blocking "
        f"findings were accepted by the analyst with the discrepancy recorded; "
        f"advisory findings do not stop the model but change how its outputs "
        f"should be read."
    )
    table = doc.add_table(rows=1, cols=3)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text = "Check"
    hdr[1].text = "Severity"
    hdr[2].text = "Finding"
    for c in failed:
        row = table.add_row().cells
        row[0].text = c.get("label") or c.get("id") or ""
        row[1].text = (c.get("severity") or "").title()
        row[2].text = c.get("message") or ""


def _add_occupancy_spread_note(doc, cim_data):
    """Economic-vs-physical occupancy read — the fastest value-add screen
    and the most common place a CIM hides weakness."""
    from config import GATES

    phys = cim_data.physical_occupancy
    econ = cim_data.economic_occupancy
    if phys is None and econ is None:
        return

    doc.add_paragraph()
    if econ is None:
        doc.add_paragraph(
            "Occupancy check: the CIM quotes physical occupancy only. Request "
            "economic occupancy (collected rent vs gross potential at street "
            "rates) before proceeding — a broker quoting a single occupancy "
            "figure is almost always quoting physical, and the spread between "
            "the two is where CIMs hide weakness."
        )
        return
    if phys is None:
        doc.add_paragraph(
            f"Occupancy check: economic occupancy of {econ:.1%} stated without "
            f"physical occupancy — request the physical figure to compute the spread."
        )
        return

    spread = phys - econ
    if spread >= GATES["econ_phys_spread_flag"]:
        doc.add_paragraph(
            f"Occupancy check: economic occupancy trails physical by "
            f"{spread * 100:.0f} pts ({phys:.1%} physical vs {econ:.1%} economic). "
            f"A spread this wide signals revenue leakage — concessions, "
            f"delinquency, or in-place rents below street — and is the primary "
            f"mismanagement value-add screen. Decompose the spread from the "
            f"rent roll during diligence."
        )
    else:
        doc.add_paragraph(
            f"Occupancy check: economic occupancy of {econ:.1%} vs physical of "
            f"{phys:.1%} ({spread * 100:.0f}-pt spread) — within normal operating "
            f"range; upside must come from rate growth or expense control rather "
            f"than collections recovery."
        )


def _add_section_2(doc, market):
    doc.add_heading("2. Market Overview", level=1)

    demos = market.get("demographics", {})
    doc.add_heading("Demographics", level=2)
    doc.add_paragraph(demos.get("pop_narrative", "TBD"))
    doc.add_paragraph(demos.get("hhi_narrative", "TBD"))

    msa = market.get("msa_info", {})
    doc.add_heading("MSA Classification", level=2)
    doc.add_paragraph(msa.get("narrative", "TBD"))

    supply = market.get("supply_assessment", {})
    doc.add_heading("Supply Assessment", level=2)
    doc.add_paragraph(supply.get("narrative", "TBD"))

    demand = market.get("demand_drivers", {})
    positives = demand.get("positives", [])
    negatives = demand.get("negatives", [])
    if positives:
        doc.add_heading("Demand Positives", level=2)
        for p in positives:
            doc.add_paragraph(p, style="List Bullet")
    if negatives:
        doc.add_heading("Demand Concerns", level=2)
        for n in negatives:
            doc.add_paragraph(n, style="List Bullet")

    doc.add_paragraph(f"\nOverall Market Rating: {market.get('overall_rating', 'TBD')}")


def _add_section_3(doc, physical):
    doc.add_heading("3. Property Description", level=1)

    profile = physical.get("property_profile", {})
    for key, label in [("property_name", "Property"), ("address", "Address"),
                       ("city_state", "City/State"), ("year_built", "Year Built"),
                       ("acreage", "Acreage"), ("nrsf", "NRSF"),
                       ("total_units", "Total Units"), ("cc_pct", "Climate-Controlled %"),
                       ("physical_occupancy", "Physical Occupancy"),
                       ("economic_occupancy", "Economic Occupancy")]:
        val = profile.get(key)
        if val is not None:
            if isinstance(val, float) and val < 1:
                val = f"{val:.1%}"
            elif isinstance(val, float):
                val = f"{val:,.0f}"
            doc.add_paragraph(f"{label}: {val}")

    doc.add_paragraph(profile.get("age_narrative", ""))
    doc.add_paragraph(profile.get("condition_note", ""))

    # Replacement cost
    repl = physical.get("replacement_cost", {})
    if repl.get("estimable"):
        doc.add_heading("Replacement Cost Estimate", level=2)
        table = doc.add_table(rows=1, cols=2)
        table.style = "Light Grid Accent 1"
        table.rows[0].cells[0].text = "Component"
        table.rows[0].cells[1].text = "Cost"

        # Use facility-type detail rows if available, else legacy
        type_details = repl.get("facility_type_details", [])
        items = []
        if type_details:
            for td in type_details:
                items.append((f"{td['type']} Hard Cost ({td['sf']:,.0f} SF)", td["hard_cost"]))
                if td["site_cost"] > 0:
                    items.append((f"{td['type']} Site Work", td["site_cost"]))
        else:
            items.append(("Non-CC Hard Cost", repl.get("non_cc_cost")))
            items.append(("CC Hard Cost", repl.get("cc_cost")))
            items.append(("Site Work", repl.get("site_work")))
        items.extend([
            ("Soft Costs", repl.get("soft_costs")),
            ("Developer Profit", repl.get("dev_profit")),
            ("Total Replacement Cost", repl.get("total_replacement")),
        ])
        for label, val in items:
            row = table.add_row().cells
            row[0].text = label
            row[1].text = _fmt_currency(val)

        # Assumptions disclosure
        doc.add_heading("Replacement Cost Assumptions", level=3)
        type_details = repl.get("facility_type_details", [])
        if type_details:
            assumptions_table = doc.add_table(rows=1, cols=3)
            assumptions_table.style = "Light Grid Accent 1"
            assumptions_table.rows[0].cells[0].text = "Facility Type"
            assumptions_table.rows[0].cells[1].text = "Hard Cost $/SF"
            assumptions_table.rows[0].cells[2].text = "Site Work $/SF"
            for td in type_details:
                arow = assumptions_table.add_row().cells
                arow[0].text = td["type"]
                arow[1].text = f"${td['hard_rate']:,.0f}"
                arow[2].text = f"${td['site_rate']:,.0f}" if td["site_rate"] > 0 else "Incl."
        soft_pct = repl.get("soft_cost_pct", 0)
        dev_pct = repl.get("dev_profit_pct", 0)
        doc.add_paragraph(
            f"Soft costs assumed at {soft_pct:.0%} of hard + site costs. "
            f"Developer profit assumed at {dev_pct:.0%} of total development cost. "
            f"Hard cost rates represent midpoints of benchmark ranges based on "
            f"2025/2026 construction cost data for each facility type."
        )

    comp = physical.get("price_vs_replacement", {})
    if comp.get("narrative"):
        doc.add_paragraph()
        doc.add_paragraph(comp["narrative"])


def _add_section_4(doc, fin, cim_data):
    doc.add_heading("4. Financial Analysis", level=1)

    # Income summary
    income = fin.get("income_summary", {})
    doc.add_heading("Income Summary", level=2)
    for label, key in [("Gross Potential Rent", "gpr"), ("Vacancy", "vacancy_loss"),
                       ("Effective Gross Revenue", "egr"),
                       ("Other Income", "other_income"),
                       ("Total Revenue", "total_revenue")]:
        val = income.get(key)
        doc.add_paragraph(f"{label}: {_fmt_currency(val)}")

    # Expense analysis
    doc.add_heading("Expense Benchmarking", level=2)
    exp = fin.get("expense_analysis", {})
    lines = exp.get("lines", [])
    if lines:
        table = doc.add_table(rows=1, cols=5)
        table.style = "Light Grid Accent 1"
        hdrs = table.rows[0].cells
        hdrs[0].text = "Category"
        hdrs[1].text = "CIM Value"
        hdrs[2].text = "$/NRSF"
        hdrs[3].text = "Benchmark"
        hdrs[4].text = "Flag"

        for line in lines:
            row = table.add_row().cells
            category = line["category"]
            if line.get("source") == "analyst":
                category += " (analyst)"
            row[0].text = category
            row[1].text = _fmt_currency(line.get("cim_value"))
            pn = line.get("per_nrsf")
            row[2].text = f"${pn:.2f}" if pn else "N/A"
            br = line.get("benchmark_range")
            row[3].text = f"${br[0]:.2f}-${br[1]:.2f}" if br else "N/A"
            row[4].text = line.get("flag") or ""

    # Adjustments
    adjustments = fin.get("adjustments", [])
    if adjustments:
        doc.add_heading("Analyst Adjustments", level=2)
        for adj in adjustments:
            doc.add_paragraph(adj, style="List Bullet")

    # Adjusted NOI
    adj_noi = fin.get("adjusted_ttm_noi", {})
    doc.add_heading("Adjusted TTM NOI", level=2)
    doc.add_paragraph(adj_noi.get("narrative", "TBD"))


def _add_section_5(doc, rent):
    doc.add_heading("5. Unit Mix & Rent Analysis", level=1)
    doc.add_paragraph(rent.get("narrative", "Unit mix data not available."))

    summary = rent.get("unit_mix_summary", [])
    if summary:
        table = doc.add_table(rows=1, cols=5)
        table.style = "Light Grid Accent 1"
        hdrs = table.rows[0].cells
        hdrs[0].text = "Size"
        hdrs[1].text = "Count"
        hdrs[2].text = "SF"
        hdrs[3].text = "Rate/Mo"
        hdrs[4].text = "$/SF/Mo"

        for s in summary:
            row = table.add_row().cells
            row[0].text = s.get("size_label") or ""
            row[1].text = str(s.get("count") or "")
            row[2].text = f"{s.get('unit_sf') or 0:,.0f}"
            row[3].text = _fmt_currency(s.get("monthly_rate"))
            r = s.get("rate_per_sf")
            row[4].text = f"${r:.2f}" if r else "N/A"

    gap = rent.get("rent_gap_analysis", {})
    if gap.get("narrative"):
        doc.add_heading("Rent Gap to Market", level=2)
        doc.add_paragraph(gap["narrative"])


def _add_section_6(doc, scenario_results, max_offer, sources_uses=None,
                   levered=None, debt=None):
    doc.add_heading("6. Valuation & Returns", level=1)

    if not scenario_results:
        doc.add_paragraph("Scenario analysis not available — insufficient data.")
        return

    for scen_name in ("bear", "base", "bull"):
        s = scenario_results.get(scen_name, {})
        doc.add_heading(f"{scen_name.title()} Case", level=2)

        noi_proj = s.get("noi_projection", [])
        if noi_proj:
            table = doc.add_table(rows=2, cols=len(noi_proj) + 1)
            table.style = "Light Grid Accent 1"
            table.rows[0].cells[0].text = "Year"
            table.rows[1].cells[0].text = "NOI"
            for i, noi in enumerate(noi_proj):
                table.rows[0].cells[i + 1].text = f"Yr {i + 1}"
                table.rows[1].cells[i + 1].text = _fmt_currency(noi)

        hold = s.get("hold_years") or len(noi_proj) or cfg.DEFAULT_HOLD_YEARS
        doc.add_paragraph(f"Entry Cap: {_fmt_pct(s.get('entry_cap'))}")
        doc.add_paragraph(f"Exit Cap: {_fmt_cap(s.get('exit_cap'))}")
        # The exit cap is derived, not typed. Printing only the result
        # would make the memo less auditable than the constant it
        # replaced, so the terms go in beside it.
        derivation = _exit_cap_derivation(s)
        if derivation:
            doc.add_paragraph(f"  {derivation}")
        doc.add_paragraph(f"Exit Value (gross): {_fmt_currency(s.get('exit_value'))}")
        # Costs are stated, not folded silently into the return: an IRR
        # quoted net of costs the reader can't see is not auditable.
        doc.add_paragraph(
            f"Acquisition Closing Costs: {_fmt_currency(s.get('acquisition_cost'))}")
        doc.add_paragraph(
            f"Disposition Costs: {_fmt_currency(s.get('disposition_cost'))}")
        doc.add_paragraph(
            f"Net Sale Proceeds: {_fmt_currency(s.get('net_exit_proceeds'))}")
        doc.add_paragraph(f"{hold}-Year IRR (net of costs): {_fmt_pct(s.get('irr'))}")
        doc.add_paragraph(f"{hold}-Year MOIC: {s.get('moic', 0):.2f}x" if s.get("moic") else "MOIC: N/A")
        doc.add_paragraph(f"Yield on Cost: {_fmt_pct(s.get('yield_on_cost'))}")

    _add_sources_uses(doc, sources_uses)
    _add_levered_returns(doc, levered, debt, scenario_results)

    # Max offer
    if max_offer:
        doc.add_heading("Maximum Offer Price", level=2)
        doc.add_paragraph(
            f"At a target {max_offer.get('target_irr', 0.10):.0%} base case unlevered IRR, "
            f"the maximum offer price is {_fmt_currency(max_offer.get('max_price'))} "
            f"(implied entry cap: {_fmt_pct(max_offer.get('implied_entry_cap'))})."
        )


def _add_sources_uses(doc, sources_uses):
    """Capital stack under section 6 — what the deal costs and where the
    money comes from. Every figure is read off build_sources_uses; nothing
    is recomputed here, so the memo cannot state a basis the model did not
    use. Senior debt and financing costs read $0 until item E1 sizes a
    loan, and they are PRINTED as zero rather than omitted, so a reader
    can see this is an all-equity underwrite rather than guess."""
    if not sources_uses:
        return
    doc.add_heading("Sources & Uses", level=2)

    for title, lines, total_key in (
            ("Uses", sources_uses.get("uses"), "total_uses"),
            ("Sources", sources_uses.get("sources"), "total_sources")):
        rows = list(lines or [])
        if not rows:
            continue
        table = doc.add_table(rows=len(rows) + 2, cols=3)
        table.style = "Light Grid Accent 1"
        header = table.rows[0].cells
        header[0].text = title
        header[1].text = "Amount"
        header[2].text = "% of Total"
        total = sources_uses.get(total_key) or 0
        for i, line in enumerate(rows, start=1):
            amount = line.get("amount") or 0
            cells = table.rows[i].cells
            cells[0].text = line.get("label") or ""
            cells[1].text = _fmt_currency(amount)
            cells[2].text = _fmt_pct(amount / total) if total else "N/A"
        last = table.rows[len(rows) + 1].cells
        last[0].text = f"Total {title}"
        last[1].text = _fmt_currency(total)
        last[2].text = _fmt_pct(1.0) if total else "N/A"
        doc.add_paragraph()

    doc.add_paragraph(
        f"Total equity required: "
        f"{_fmt_currency(sources_uses.get('total_equity'))} "
        f"(GP co-invest {_fmt_pct(sources_uses.get('gp_coinvest_pct'))}: "
        f"{_fmt_currency(sources_uses.get('gp_equity'))}; LP "
        f"{_fmt_currency(sources_uses.get('lp_equity'))}).")
    if not sources_uses.get("balanced"):
        doc.add_paragraph(
            f"WARNING: Sources and Uses are out of balance by "
            f"{_fmt_currency(abs(sources_uses.get('delta') or 0))}. The "
            f"returns above are computed on the Uses figure.")


def _add_levered_returns(doc, levered, debt, scenario_results):
    """The levered second lens under section 6 (item E3b).

    A level-2 subsection, not a new numbered section: ten numbered
    sections are referenced from the recommendation text and the CLI
    summary, and renumbering them to insert a presentation block is churn
    with a real chance of an off-by-one.

    Every figure is read off the persisted `levered` / `debt` payload —
    nothing is recomputed, so the memo cannot state a return the model did
    not produce. Absent entirely on a deal that priced no loan, which is
    why this returns early instead of printing a table of N/A.
    """
    levered = levered or {}
    base = levered.get("base") or {}
    if not base:
        return
    debt = debt or {}
    terms = debt.get("terms") or {}
    hold = _hold_years(scenario_results)

    doc.add_heading("Levered Returns (LP Net)", level=2)

    from model.debt import binding_constraint_label, displayed_rate

    rate = displayed_rate(terms)
    doc.add_paragraph(
        f"Senior loan {_fmt_currency(debt.get('loan'))} at "
        f"{_fmt_pct(rate)}, {_fmt_number(terms.get('amort_years'))}-year "
        f"amortization, {_fmt_number(terms.get('io_months'))} months IO, "
        f"{_fmt_number(terms.get('term_years'))}-year term. Sized off the "
        f"base case at the lesser of LTV, DSCR and debt yield; bound by "
        f"{binding_constraint_label(debt)} at "
        f"{_fmt_pct(debt.get('ltv'))} LTV, "
        f"{_fmt_x(debt.get('dscr_year_1'))} Year-1 DSCR and "
        f"{_fmt_pct(debt.get('debt_yield'))} debt yield. The SAME loan is "
        f"carried through all three scenarios — sizing per scenario would "
        f"hand the bear case a smaller loan and flatten the downside it "
        f"exists to show.")

    rows = [("LP Net IRR", "lp_net_irr", _fmt_pct),
            ("LP MOIC", "lp_moic", _fmt_x),
            ("GP Promote", "gp_promote", _fmt_currency),
            ("AM Fee (total)", "am_fee_total", _fmt_currency),
            ("Equity Required", "total_equity", _fmt_currency)]
    table = doc.add_table(rows=len(rows) + 1, cols=4)
    table.style = "Light Grid Accent 1"
    header = table.rows[0].cells
    header[0].text = f"{hold}-Year Levered"
    for i, scen in enumerate(("bear", "base", "bull"), start=1):
        header[i].text = scen.title()
    for r, (label, key, fmt) in enumerate(rows, start=1):
        cells = table.rows[r].cells
        cells[0].text = label
        for i, scen in enumerate(("bear", "base", "bull"), start=1):
            cells[i].text = fmt((levered.get(scen) or {}).get(key))
    doc.add_paragraph()

    # Leverage is allowed to be dilutive, and on config defaults it often
    # is. Saying so in the memo stops a reader treating an LP net IRR
    # below the unlevered screen as an arithmetic error.
    dilutive = [scen.title() for scen in ("bear", "base", "bull")
                if (levered.get(scen) or {}).get("lp_net_irr") is not None
                and ((scenario_results or {}).get(scen) or {}).get("irr")
                is not None
                and (levered[scen]["lp_net_irr"]
                     < scenario_results[scen]["irr"])]
    if dilutive:
        doc.add_paragraph(
            f"Leverage is DILUTIVE in the {', '.join(dilutive)} case(s): LP "
            f"net IRR is below the unlevered IRR because the loan constant "
            f"sits above the deal's yield on cost. This is an outcome, not "
            f"an error.")
    if debt.get("matures_before_exit"):
        doc.add_paragraph(
            "WARNING: the loan matures before the hold ends. These figures "
            "amortize straight past maturity — no refinancing, rate reset "
            "or prepayment cost is modeled.")
    if base.get("called_capital_after_close"):
        doc.add_paragraph(
            f"WARNING: this deal calls "
            f"{_fmt_currency(base.get('capital_calls_total'))} of capital "
            f"after close "
            f"({_fmt_currency(base.get('reserve_drawn_total'))} covered by "
            f"the operating reserve). Called capital accrues preferred "
            f"return from the following period.")
    unrecovered = (base.get("waterfall") or {}).get("unrecovered_promote")
    if unrecovered:
        doc.add_paragraph(
            f"WARNING: {_fmt_currency(unrecovered)} of promote was paid "
            f"before a later capital call and the deal ends short of "
            f"capital plus preferred return. These fund terms carry no "
            f"clawback, so the GP keeps it.")

    # Not optional and not a footnote: five of these are open LPA
    # questions and each one moves the LP net IRR printed above.
    doc.add_heading("Levered Assumptions", level=3)
    for row in base.get("assumption_stamp") or []:
        doc.add_paragraph(f"{row.get('label')} — {row.get('question')}",
                          style="List Bullet")


def _add_section_7(doc, value_add, va_results=None, va_max_offer=None):
    doc.add_heading("7. Value-Add Opportunities", level=1)
    doc.add_paragraph(value_add.get("narrative", "No opportunities identified."))

    # Qualitative opportunities table
    opps = value_add.get("opportunities", [])
    if opps:
        table = doc.add_table(rows=1, cols=4)
        table.style = "Light Grid Accent 1"
        hdrs = table.rows[0].cells
        hdrs[0].text = "#"
        hdrs[1].text = "Opportunity"
        hdrs[2].text = "Est. Annual Impact"
        hdrs[3].text = "Timeline"

        for o in opps:
            row = table.add_row().cells
            row[0].text = str(o.get("priority") or "")
            row[1].text = o.get("opportunity") or ""
            impact = o.get("estimated_annual_impact") or 0
            row[2].text = _fmt_currency(impact) if impact else "TBD"
            row[3].text = o.get("timeline") or ""

    # Value-Add Financial Model (if available)
    if va_results:
        doc.add_heading("Value-Add Financial Model", level=2)

        base = va_results.get("base", {})
        in_place = base.get("in_place_rent_psf")
        market = base.get("market_rent_psf")
        current_occ = base.get("current_occupancy")
        target_occ = base.get("target_occupancy")

        if in_place and market:
            doc.add_paragraph(
                f"In-place rent of ${in_place:.2f}/SF/mo vs market of ${market:.2f}/SF/mo "
                f"represents a {((market - in_place) / in_place):.0%} rent gap. "
                f"Current occupancy of {_fmt_pct(current_occ)} with "
                f"target stabilization at {_fmt_pct(target_occ)}."
            )

        # VA scenario comparison table
        doc.add_heading("Value-Add Returns (Unlevered)", level=3)
        va_table = doc.add_table(rows=1, cols=4)
        va_table.style = "Light Grid Accent 1"
        vh = va_table.rows[0].cells
        vh[0].text = "Metric"
        vh[1].text = "Bear"
        vh[2].text = "Base"
        vh[3].text = "Bull"

        for label, key, fmt_fn in [
            ("Months to Stabilize", "months_to_stabilize",
             lambda v: str(int(v)) if v else "N/A"),
            ("Stabilized NOI", "stabilized_noi",
             lambda v: _fmt_currency(v)),
            ("Stabilized Yield/Cost", "yield_on_cost",
             lambda v: _fmt_pct(v)),
            ("Unlevered IRR (net of costs)", "irr",
             lambda v: _fmt_pct(v)),
            ("MOIC", "moic",
             lambda v: f"{v:.2f}x" if v else "N/A"),
            ("Development Spread", "development_spread",
             lambda v: f"{v*100:.0f} bps" if v else "N/A"),
        ]:
            row = va_table.add_row().cells
            row[0].text = label
            for i, scen in enumerate(["bear", "base", "bull"]):
                val = va_results.get(scen, {}).get(key)
                row[i + 1].text = fmt_fn(val) if val is not None else "N/A"

        # VA max offer
        if va_max_offer and va_max_offer.get("max_price"):
            doc.add_paragraph()
            doc.add_paragraph(
                f"Value-Add Maximum Offer Price (for 10% IRR): "
                f"{_fmt_currency(va_max_offer['max_price'])} "
                f"(implied entry cap: {_fmt_pct(va_max_offer.get('implied_entry_cap'))})"
            ).bold = True


def _add_section_8(doc, risk_analysis):
    doc.add_heading("8. Risk Analysis", level=1)

    # Why this deal could fail
    why_fail = risk_analysis.get("why_deal_could_fail", [])
    if why_fail:
        doc.add_heading("Why This Deal Could Fail", level=2)
        for r in why_fail:
            doc.add_paragraph(
                f"{r.get('risk', 'Unknown')}: {r.get('description', '')}",
                style="List Bullet",
            )

    # Full risk register
    risks = risk_analysis.get("risks", [])
    if risks:
        doc.add_heading("Risk Register", level=2)
        table = doc.add_table(rows=1, cols=4)
        table.style = "Light Grid Accent 1"
        hdrs = table.rows[0].cells
        hdrs[0].text = "Risk"
        hdrs[1].text = "Category"
        hdrs[2].text = "Severity"
        hdrs[3].text = "Mitigation"

        for r in risks:
            row = table.add_row().cells
            row[0].text = r.get("risk") or ""
            row[1].text = r.get("category") or ""
            row[2].text = r.get("severity") or ""
            row[3].text = r.get("mitigation") or ""

    doc.add_paragraph(f"\nOverall Risk Rating: {risk_analysis.get('risk_rating', 'TBD')}")


def _add_section_9(doc):
    doc.add_heading("9. Due Diligence Items", level=1)
    items = [
        "Obtain and review actual T-12 P&L (not broker pro forma)",
        "Verify 3-mile population and demographics via census data",
        "Confirm new supply pipeline with local planning/permitting records",
        "Conduct physical property inspection and condition assessment",
        "Obtain rent roll with move-in dates and rate history",
        "Reconcile economic vs physical occupancy from the rent roll "
        "(concessions, delinquency, below-street in-place rates)",
        "Verify competitor street-rate trend over trailing 6-12 months — "
        "confirm the in-place-to-market gap is closing from below (market "
        "rising), not from above (street rates falling)",
        "Verify property tax assessment and potential reassessment at sale price",
        "Review competitor rent survey (independent of CIM data)",
        "Confirm insurance quotes for the specific property",
        "Review lease/rental agreement terms",
        "Environmental Phase I assessment",
        "Title and survey review",
        "Verify zoning and entitlements",
    ]
    for item in items:
        doc.add_paragraph(item, style="List Bullet")


def _add_section_10(doc, gate_results, scenario_results, max_offer, risk_analysis, cim_data):
    doc.add_heading("10. Recommendation", level=1)

    # Determine recommendation
    failed = [g for g in gate_results if g["result"] == "FAIL"]
    tbd = [g for g in gate_results if g["result"] == "TBD"]
    base_irr = scenario_results.get("base", {}).get("irr") if scenario_results else None

    if failed:
        rec = "DECLINE"
        rationale = "One or more screening gates have failed:"
    elif tbd:
        rec = "PURSUE CONTINGENT ON"
        rationale = "Screening gates passed but the following require verification:"
    elif base_irr and base_irr >= 0.10:
        rec = "PURSUE"
        rationale = "All screening gates passed and base case returns meet the 10% IRR target."
    else:
        rec = "PURSUE CONTINGENT ON"
        rationale = "Screening gates passed but base case IRR is below 10% target."

    p = doc.add_paragraph()
    run = p.add_run(f"RECOMMENDATION: {rec}")
    run.bold = True
    run.font.size = Pt(14)
    run.font.color.rgb = (
        RGBColor(0, 128, 0) if rec == "PURSUE" else
        RGBColor(204, 102, 0) if "CONTINGENT" in rec else
        RGBColor(204, 0, 0)
    )

    doc.add_paragraph(rationale)

    if failed:
        for g in failed:
            doc.add_paragraph(
                f"Gate {g['gate']} ({g['name']}): {g.get('note', '')}",
                style="List Bullet",
            )

    if tbd:
        for g in tbd:
            doc.add_paragraph(
                f"Gate {g['gate']} ({g['name']}): {g.get('note', '')}",
                style="List Bullet",
            )

    # Pricing guidance
    if max_offer and max_offer.get("max_price") and cim_data.asking_price:
        mp = max_offer["max_price"]
        asking = cim_data.asking_price
        discount = (asking - mp) / asking if asking else 0
        doc.add_paragraph()
        doc.add_paragraph(
            f"Maximum Offer: {_fmt_currency(mp)} "
            f"({discount:.1%} discount to asking price of {_fmt_currency(asking)})"
        )

    doc.add_paragraph()
    doc.add_paragraph(
        "Note: This analysis is based on CIM-provided data supplemented by "
        "benchmark assumptions. All figures should be verified during due diligence."
    ).italic = True


# ── Formatting Helpers ──────────────────────────────────────────────

def _fmt_currency(val) -> str:
    if val is None:
        return "N/A"
    if abs(val) >= 1_000_000:
        return f"${val:,.0f}"
    return f"${val:,.0f}"


def _fmt_pct(val) -> str:
    if val is None:
        return "N/A"
    return f"{val:.1%}"


def _fmt_cap(val) -> str:
    """Cap rates to three places. The obsolescence drift is 5–10 bp/yr, so
    at `_fmt_pct`'s single decimal the printed build-up would not add up."""
    if val is None:
        return "N/A"
    return f"{val:.3%}"


def _exit_cap_derivation(scen: dict) -> str:
    """One sentence retracing a scenario's exit cap to its market anchor.

    Empty string when the scenario carries no derivation — a stored run
    from before the cap became derived must still render.
    """
    d = (scen or {}).get("exit_cap_detail") or {}
    if not d or d.get("market_cap") is None:
        return ""
    band = d.get("age_band") or "—"
    if d.get("age_band_known") is False:
        band += ", year built unknown"
    from analysis.valuation import describe_market_cap
    txt = (f"= {_fmt_cap(d['market_cap'])} market cap "
           f"({d.get('asset_class') or 'asset'}, {band}, "
           f"{describe_market_cap(d)})"
           f" {d.get('scenario_spread_bps', 0):+g} bp scenario spread"
           f" {d.get('drift_total_bps', 0):+g} bp obsolescence drift"
           f" ({d.get('drift_bps_per_year')} bp/yr × "
           f"{d.get('hold_years')} yrs)")
    if scen.get("exit_cap_coerced"):
        txt += (f" = {_fmt_cap(scen.get('requested_exit_cap'))}, then raised "
                f"to the entry cap to hold exit ≥ entry")
    return txt


def _fmt_number(val, suffix="") -> str:
    if val is None:
        return "N/A"
    return f"{val:,.0f}{suffix}"


def _fmt_x(val) -> str:
    """Multiples and coverage ratios. NOT `_fmt_number(v, "x")`: that
    rounds to whole numbers, which prints a 1.25x DSCR as "1x" and a
    1.39x MOIC as "1x" — the two decimals are the entire content."""
    if val is None:
        return "N/A"
    return f"{val:.2f}x"


def _safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in name).strip().replace(" ", "_")
