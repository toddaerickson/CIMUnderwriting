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
                  levered_max_offer: dict = None,
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
                   levered, debt, levered_max_offer)

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
                   levered=None, debt=None, levered_max_offer=None):
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
    if max_offer or levered_max_offer:
        doc.add_heading("Maximum Offer Price", level=2)
    if max_offer:
        doc.add_paragraph(
            f"At a target {max_offer.get('target_irr', 0.10):.0%} base case unlevered IRR, "
            f"the maximum offer price is {_fmt_currency(max_offer.get('max_price'))} "
            f"(implied entry cap: {_fmt_pct(max_offer.get('implied_entry_cap'))})."
        )
    _add_levered_max_offer(doc, levered_max_offer)


def _add_levered_max_offer(doc, levered_max_offer):
    """The levered max offer (item E4), beside the unlevered one.

    Beside and not instead of: the unlevered figure is the price the
    primary 10% gate is measured against, and the two answer different
    questions — "what can the property carry" versus "what can the fund
    pay and still clear its LP net target". A memo showing only the
    second would leave the primary screen with no price of its own.

    Silent when the deal priced no loan, for the same reason
    `_add_levered_returns` returns early: a block of N/A reads as a
    failed calculation rather than an absent one.
    """
    offer = levered_max_offer or {}
    if not offer.get("max_price"):
        return

    doc.add_paragraph(
        f"At a target {offer.get('target_irr', 0.15):.0%} LP NET IRR — after "
        f"debt service, the asset-management fee and the GP promote — the "
        f"maximum offer price is {_fmt_currency(offer.get('max_price'))} "
        f"(implied entry cap: {_fmt_pct(offer.get('implied_entry_cap'))}). "
        f"At that price the deal supports "
        f"{_fmt_currency(offer.get('senior_debt'))} of senior debt "
        f"({_fmt_pct(offer.get('ltv'))} LTV, bound by "
        f"{_binding_label(offer)}) and requires "
        f"{_fmt_currency(offer.get('total_equity'))} of equity.")

    # The bisection assumes LP net IRR falls as price rises. It does over
    # every range measured (see model/solver.py), but an observed
    # inversion means this price may be the wrong root, and a memo must
    # not print a suspect number as a clean one.
    if offer.get("monotonicity_warning"):
        doc.add_paragraph(f"WARNING: {offer['monotonicity_warning']}")
    if not offer.get("converged"):
        doc.add_paragraph(
            "WARNING: the levered solver did not converge to its target "
            "within the iteration budget — treat this price as indicative.")

    # Same rule as every other LP net figure in this memo: no LP net IRR
    # without its assumption stamp (CLAUDE.md key design decision 7). A
    # price derived FROM an LP net IRR inherits the requirement.
    stamp = offer.get("assumption_stamp") or []
    if stamp:
        doc.add_paragraph(
            "This price is computed on the levered assumption set stated "
            "under Levered Returns above: "
            + "; ".join(row.get("label", "") for row in stamp) + ".")


def _binding_label(offer) -> str:
    from model.debt import CONSTRAINT_LABELS
    key = (offer or {}).get("binding_constraint")
    return CONSTRAINT_LABELS.get(key, key or "n/a")


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


# ── LP-Facing Investor Summary (item G) ─────────────────────────────
#
# A condensation of the IC memo for readers outside the firm. It is a
# SECOND RENDERING, never a second computation: every figure is read off
# the same result dicts `generate_memo` receives, so the two documents
# cannot disagree about a deal. Nothing below calls the projection, the
# solver or the waterfall.
#
# **Two pages is the design constraint, not an aspiration.** python-docx
# does not paginate — page count is decided by Word at render time — so
# "it fits" is enforced two ways instead of hoped for:
#   1. A FIXED section list with hard caps (below). Three thesis bullets,
#      three risks, truncated strings. A deal cannot add a section, so the
#      only variable left is string length, and that is capped.
#   2. `tests/test_investor_summary.py` estimates rendered height from the
#      document body against this page geometry and asserts each page fits.
#      It is a calibrated estimate, not a real render — see that test's
#      docstring for why a true page count would mean adding a headless
#      office suite as a dependency.
#
# **Distribution is gated, the build is not.** A document aimed at
# prospective investors edges toward securities marketing, which sits
# behind the operator's General Counsel gate. `_SUMMARY_LEGEND` states
# that on the page itself; do not remove it, and route wording past GC
# before this leaves the firm.

_SUMMARY_PAGE_MARGIN_IN = 0.7
_SUMMARY_BODY_PT = 10

_SUMMARY_MAX_THESIS_BULLETS = 3
_SUMMARY_MAX_RISKS = 3
_SUMMARY_MAX_NAME_CHARS = 70
_SUMMARY_MAX_BULLET_CHARS = 190
_SUMMARY_MAX_RISK_CHARS = 150

# Risk register severities, most severe first. `identify_risks` emits
# title case and `_add_section_8` prints it unchanged, so this orders the
# same vocabulary rather than inventing a second one.
_SUMMARY_SEVERITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}

_SUMMARY_LEGEND = (
    "Prepared by CIM Analyst from the seller's Confidential Information "
    "Memorandum supplemented by benchmark assumptions. Figures are "
    "underwriting estimates, not results, and are subject to due "
    "diligence. Past or projected performance is not a guarantee of "
    "future results. This document is for internal and prospect "
    "discussion only. It is not an offer to sell or a solicitation of an "
    "offer to buy any security, and it is not investment advice."
)


def generate_investor_summary(property_name: str, cim_data,
                              market_analysis: dict, physical_analysis: dict,
                              scenario_results: dict, risk_analysis: dict,
                              gate_results: list = None,
                              sources_uses: dict = None,
                              levered: dict = None, debt: dict = None,
                              output_dir: str = ".") -> str:
    """Generate the 2-page LP-facing investor summary .docx.

    Degrades cleanly rather than raising: a deal with no scenarios, no
    Sources & Uses or no levered layer still produces a document, with
    the sections that have no data omitted instead of printed as a wall
    of N/A. That matters because this is the artifact most likely to be
    generated from a thin early-look CIM.

    Returns: path to generated file.
    """
    doc = Document()

    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(_SUMMARY_BODY_PT)

    for section in doc.sections:
        section.top_margin = Inches(_SUMMARY_PAGE_MARGIN_IN)
        section.bottom_margin = Inches(_SUMMARY_PAGE_MARGIN_IN)
        section.left_margin = Inches(_SUMMARY_PAGE_MARGIN_IN)
        section.right_margin = Inches(_SUMMARY_PAGE_MARGIN_IN)

    safe_name = _safe_filename(property_name or "Unknown_Property")
    filepath = os.path.join(output_dir, f"Investor_Summary_{safe_name}.docx")

    # ── Page 1 ──────────────────────────────────────────────────
    _summary_header(doc, cim_data, property_name)
    _summary_thesis(doc, cim_data, physical_analysis, scenario_results,
                    levered)
    _summary_key_metrics(doc, cim_data, scenario_results, sources_uses,
                         levered)
    _summary_capital_stack(doc, sources_uses)

    doc.add_page_break()

    # ── Page 2 ──────────────────────────────────────────────────
    _summary_scenarios(doc, scenario_results, levered)
    _summary_market(doc, market_analysis, gate_results)
    _summary_risks(doc, risk_analysis)
    _summary_legend(doc)

    doc.save(filepath)
    return filepath


def _truncate(text: str, limit: int) -> str:
    """Hard cap with an ellipsis. The page budget is only real if the
    strings that feed it are bounded."""
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _summary_header(doc, cim_data, property_name):
    heading = doc.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = heading.add_run(
        _truncate(property_name or cim_data.property_name or "Storage Asset",
                  _SUMMARY_MAX_NAME_CHARS))
    run.bold = True
    run.font.size = Pt(18)

    where = ", ".join(p for p in (cim_data.city, cim_data.state) if p)
    line = " · ".join(p for p in (cim_data.address, where) if p)
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub.add_run(line or "Location TBD").font.size = Pt(11)

    slot = doc.add_paragraph()
    slot.alignment = WD_ALIGN_PARAGRAPH.CENTER
    slot_run = slot.add_run("[ Property photograph — insert before use ]")
    slot_run.italic = True
    slot_run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)


def _summary_thesis(doc, cim_data, physical_analysis, scenario_results,
                    levered):
    bullets = _summary_thesis_bullets(cim_data, physical_analysis,
                                      scenario_results, levered)
    if not bullets:
        return
    doc.add_heading("Investment Thesis", level=2)
    for bullet in bullets:
        doc.add_paragraph(_truncate(bullet, _SUMMARY_MAX_BULLET_CHARS),
                          style="List Bullet")


def _summary_thesis_bullets(cim_data, physical_analysis, scenario_results,
                            levered) -> list:
    """Up to three thesis bullets, each a restatement of a COMPUTED fact.

    Deliberately not prose generation. Every candidate below is gated on
    a value the pipeline already produced and prints that value, so the
    thesis cannot claim something the model did not find. The order is
    the priority order — basis first, because price against replacement
    cost is the operator's anchor — and the first three that qualify win.
    """
    base = (scenario_results or {}).get("base") or {}
    lev_base = (levered or {}).get("base") or {}
    out = []

    pvr = (physical_analysis or {}).get("price_vs_replacement") or {}
    discount = pvr.get("discount_to_replacement")
    if pvr.get("comparable") and discount is not None and discount > 0:
        out.append(
            f"Basis: asking {_fmt_currency(pvr.get('asking_price'))} "
            f"({_fmt_currency(pvr.get('asking_per_sf'))}/SF) is "
            f"{_fmt_pct(discount)} below the "
            f"{_fmt_currency(pvr.get('replacement_cost'))} replacement-cost "
            f"estimate — below the cost to build the competition.")

    phys, econ = cim_data.physical_occupancy, cim_data.economic_occupancy
    spread_flag = cfg.GATES.get("econ_phys_spread_flag")
    if phys and econ and spread_flag and (phys - econ) >= spread_flag:
        out.append(
            f"Operational upside: {_fmt_pct(phys)} physical against "
            f"{_fmt_pct(econ)} economic occupancy — a "
            f"{(phys - econ) * 100:.0f}-point spread that is collected "
            f"rent left on the table, not absent demand.")

    if lev_base.get("lp_net_irr") is not None:
        out.append(
            f"Returns: {_fmt_pct(lev_base['lp_net_irr'])} LP net IRR and "
            f"{_fmt_x(lev_base.get('lp_moic'))} net multiple in the base "
            f"case, after debt service, asset-management fee and promote.")
    elif base.get("irr") is not None:
        out.append(
            f"Returns: {_fmt_pct(base['irr'])} unlevered IRR and "
            f"{_fmt_x(base.get('moic'))} multiple in the base case, net of "
            f"acquisition and disposition costs.")

    if base.get("yield_on_cost") is not None:
        out.append(
            f"Going-in yield: {_fmt_pct(base['yield_on_cost'])} Year-1 yield "
            f"on total cost against a {_fmt_pct(base.get('entry_cap'))} "
            f"entry cap.")

    return out[:_SUMMARY_MAX_THESIS_BULLETS]


def _summary_key_metrics(doc, cim_data, scenario_results, sources_uses,
                         levered):
    base = (scenario_results or {}).get("base") or {}
    lev_base = (levered or {}).get("base") or {}

    rows = [
        ("Asking Price", _fmt_currency(cim_data.asking_price)),
        ("Price / SF", _fmt_currency(cim_data.price_per_sf)),
        ("NRSF", _fmt_number(cim_data.nrsf, suffix=" SF")),
        ("Entry Cap", _fmt_pct(base.get("entry_cap"))),
        ("Exit Cap (Base)", _fmt_pct(base.get("exit_cap"))),
        ("Yr-1 Yield on Cost", _fmt_pct(base.get("yield_on_cost"))),
        ("Unlevered IRR / MOIC",
         f"{_fmt_pct(base.get('irr'))} / {_fmt_x(base.get('moic'))}"),
    ]
    # The levered pair is the fund's actual bar, so it goes on page 1 when
    # it exists — and is omitted, not printed as N/A, when it does not.
    if lev_base.get("lp_net_irr") is not None:
        rows.append(("LP Net IRR / MOIC",
                     f"{_fmt_pct(lev_base.get('lp_net_irr'))} / "
                     f"{_fmt_x(lev_base.get('lp_moic'))}"))
    if sources_uses:
        rows.append(("Equity Required",
                     _fmt_currency(sources_uses.get("total_equity"))))

    doc.add_heading("Key Metrics", level=2)
    table = doc.add_table(rows=len(rows), cols=2)
    table.style = "Light Grid Accent 1"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, (label, value) in enumerate(rows):
        cells = table.rows[i].cells
        cells[0].text = label
        cells[1].text = value


def _summary_capital_stack(doc, sources_uses):
    """Condensed Sources & Uses — totals and the equity split only.

    The IC memo's full line-item tables are what page 2 does not have
    room for; the numbers here are the same `build_sources_uses` output,
    just fewer of them.
    """
    if not sources_uses:
        return
    doc.add_heading("Capital Stack", level=2)
    rows = [
        ("Total Uses", sources_uses.get("total_uses")),
        ("Senior Debt", sources_uses.get("senior_debt")),
        ("Total Equity", sources_uses.get("total_equity")),
        (f"— GP Co-Invest ({_fmt_pct(sources_uses.get('gp_coinvest_pct'))})",
         sources_uses.get("gp_equity")),
        ("— LP Equity", sources_uses.get("lp_equity")),
    ]
    table = doc.add_table(rows=len(rows), cols=2)
    table.style = "Light Grid Accent 1"
    for i, (label, amount) in enumerate(rows):
        cells = table.rows[i].cells
        cells[0].text = label
        cells[1].text = _fmt_currency(amount)


def _summary_scenarios(doc, scenario_results, levered):
    if not scenario_results:
        return
    hold = _hold_years(scenario_results)
    doc.add_heading(f"{hold}-Year Scenario Returns", level=2)

    rows = [("Yr-1 Yield on Cost", "yield_on_cost", _fmt_pct, scenario_results),
            ("Exit Cap", "exit_cap", _fmt_pct, scenario_results),
            ("Unlevered IRR", "irr", _fmt_pct, scenario_results),
            ("Unlevered MOIC", "moic", _fmt_x, scenario_results)]
    if (levered or {}).get("base", {}).get("lp_net_irr") is not None:
        rows += [("LP Net IRR", "lp_net_irr", _fmt_pct, levered),
                 ("LP Net MOIC", "lp_moic", _fmt_x, levered)]

    table = doc.add_table(rows=len(rows) + 1, cols=4)
    table.style = "Light Grid Accent 1"
    header = table.rows[0].cells
    header[0].text = "Metric"
    for i, scen in enumerate(("bear", "base", "bull"), start=1):
        header[i].text = scen.title()
    for r, (label, key, fmt, source) in enumerate(rows, start=1):
        cells = table.rows[r].cells
        cells[0].text = label
        for i, scen in enumerate(("bear", "base", "bull"), start=1):
            cells[i].text = fmt((source.get(scen) or {}).get(key))


def _summary_market(doc, market_analysis, gate_results):
    """Three facts, read from the market analysis and the gate register.

    SF-per-capita is taken from gate 5's own `actual` string rather than
    recomputed from the supply inputs — the gate is where that arithmetic
    lives, and a second copy would be free to disagree with the screen.
    """
    demos = (market_analysis or {}).get("demographics") or {}
    msa = (market_analysis or {}).get("msa_info") or {}
    by_gate = {g.get("gate"): g for g in (gate_results or [])}

    rows = [("Population (3-mile)", _fmt_number(demos.get("population_3mi"))),
            ("Median HHI (3-mile)",
             _fmt_currency(demos.get("median_hhi_3mi")))]
    oversupply = by_gate.get(5)
    if oversupply:
        rows.append(("Supply",
                     f"{oversupply.get('actual')} (equilibrium ~7-8)"))
    rows.append(("Market", (msa.get("msa") or "MSA not identified")
                 + (" — top-50 MSA" if msa.get("is_top_50") else "")))

    doc.add_heading("Market Snapshot", level=2)
    table = doc.add_table(rows=len(rows), cols=2)
    table.style = "Light Grid Accent 1"
    for i, (label, value) in enumerate(rows):
        cells = table.rows[i].cells
        cells[0].text = label
        cells[1].text = str(value)


def _summary_risks(doc, risk_analysis):
    """The three most severe risks, and a count of what was left out.

    Printing three without saying how many exist would read as "there are
    three risks", which is the one thing a condensation must not imply.
    """
    risks = list((risk_analysis or {}).get("risks") or [])
    if not risks:
        return
    risks.sort(key=lambda r: _SUMMARY_SEVERITY_ORDER.get(
        r.get("severity"), len(_SUMMARY_SEVERITY_ORDER)))
    shown = risks[:_SUMMARY_MAX_RISKS]

    doc.add_heading("Principal Risks", level=2)
    table = doc.add_table(rows=len(shown) + 1, cols=3)
    table.style = "Light Grid Accent 1"
    header = table.rows[0].cells
    header[0].text = "Risk"
    header[1].text = "Severity"
    header[2].text = "Mitigation"
    for i, risk in enumerate(shown, start=1):
        cells = table.rows[i].cells
        cells[0].text = _truncate(risk.get("risk"), _SUMMARY_MAX_RISK_CHARS)
        cells[1].text = risk.get("severity") or ""
        cells[2].text = _truncate(risk.get("mitigation"),
                                  _SUMMARY_MAX_RISK_CHARS)

    remaining = len(risks) - len(shown)
    if remaining > 0:
        note = doc.add_paragraph(
            f"{len(shown)} of {len(risks)} identified risks shown, most "
            f"severe first. The full risk register is in the investment "
            f"memo.")
        note.runs[0].italic = True


def _summary_legend(doc):
    doc.add_paragraph()
    legend = doc.add_paragraph()
    run = legend.add_run(_SUMMARY_LEGEND)
    run.italic = True
    run.font.size = Pt(8)
    run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)


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
    """Sanitize and CAP. The cap is not cosmetic: most filesystems stop at
    255 bytes, and a CIM whose property name runs long enough produced a
    path that `Document.save` could not open — an OSError that surfaced
    as a failed memo with no obvious cause. `output/template_writer.py`
    has always truncated at 60; this matches it rather than inventing a
    second limit. Caught by the investor summary's maximal-content test
    (item G), but `generate_memo` had the same defect."""
    safe = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_"
                   for c in name).strip().replace(" ", "_")
    return safe[:60]
