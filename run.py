#!/usr/bin/env python3
"""
CIM Analyst — Entry Point

Analyzes a self-storage CIM (PDF) and produces:
  1. A completed investment memo (.docx)
  2. A returns model (.xlsx)
  3. A terminal summary with PASS/FAIL gates and recommendation
"""

import os
import sys
import glob
import logging

from log_config import setup_logging
from context import AnalysisContext

logger = logging.getLogger("cim_analyst")


# ══════════════════════════════════════════════════════════════════════
#  Pipeline stages — each receives and mutates an AnalysisContext
# ══════════════════════════════════════════════════════════════════════

def stage_extract(ctx: AnalysisContext):
    """[1/7] Extract raw text and tables from the PDF."""
    logger.info("\n[1/7] Extracting PDF text and tables...")
    from extract.pdf_reader import extract_pdf
    ctx.raw_pdf = extract_pdf(ctx.pdf_path)
    logger.info("  Pages: %d", ctx.raw_pdf['page_count'])
    logger.info("  Tables found: %d", len(ctx.raw_pdf['tables']))
    logger.info("  Text length: %s chars", f"{len(ctx.raw_pdf['text']):,}")


def stage_parse(ctx: AnalysisContext):
    """[2/7] Parse CIM data from extracted text, apply overrides, enrich."""
    logger.info("\n[2/7] Parsing CIM data...")
    from extract.parser import parse_cim
    ctx.cim_data = parse_cim(ctx.raw_pdf)
    ctx.snapshot("after_parse")

    report = ctx.cim_data.extraction_report()
    logger.info("  Extraction confidence: %s%%", report['confidence_pct'])
    logger.info("  Fields populated: %d/%d", report['populated'], report['total_fields'])
    if report["missing"]:
        logger.info("  Missing fields: %s", ', '.join(report['missing'][:10]))
        if len(report["missing"]) > 10:
            logger.info("    ... and %d more", len(report['missing']) - 10)
    logger.info("\n  *** Review extraction results. Claude Code can fill gaps ***")
    logger.info("  *** from the PDF content visible in context.            ***")

    # Manual overrides (JSON)
    _fill_manual_data(ctx.cim_data, ctx.pdf_path)
    ctx.snapshot("after_overrides")
    override_changes = ctx.diff_snapshot("after_parse")
    if override_changes:
        logger.info("  Overrides applied: %s", ", ".join(override_changes.keys()))

    report2 = ctx.cim_data.extraction_report()
    if report2['confidence_pct'] > report['confidence_pct']:
        logger.info("\n  After manual fill-in: %s%% confidence (%d/%d fields)",
                    report2['confidence_pct'], report2['populated'], report2['total_fields'])


def stage_enrich(ctx: AnalysisContext, comp_db):
    """[2b] External data enrichment — Census API, comp DB, rent survey."""
    logger.info("\n  Running data enrichment...")
    try:
        from extract.enrichment import enrich_cim_data
        ctx.enrichment = enrich_cim_data(ctx.cim_data, comp_db=comp_db)
        if ctx.enrichment.fields_enriched > 0:
            logger.info("  Enriched %d field(s) from external sources", ctx.enrichment.fields_enriched)
            for fname, info in ctx.enrichment.source_log.items():
                if info["tier"] == 2:
                    logger.info("    %s: %s (Census API)", fname, info['value'])
        if ctx.enrichment.geocode_success:
            logger.info("  Geocoded: lat=%s, lon=%s",
                        ctx.enrichment.source_log.get('lat', {}).get('value'),
                        ctx.enrichment.source_log.get('lon', {}).get('value'))
        for err in ctx.enrichment.errors:
            logger.info("  Note: %s", err)
    except Exception:
        logger.warning("  Enrichment skipped", exc_info=True)

    # Competitive rent survey (if market_rent_psf not set)
    if not ctx.cim_data.market_rent_psf and ctx.cim_data.city and ctx.cim_data.state:
        logger.info("\n  Running competitive rent survey...")
        try:
            from extract.rent_survey import run_rent_survey
            survey = run_rent_survey(
                city=ctx.cim_data.city,
                state=ctx.cim_data.state,
            )
            if survey.success and survey.market_rent_per_sf_mo:
                ctx.cim_data.market_rent_psf = round(survey.market_rent_per_sf_mo, 2)
                logger.info("  Market rent from %s: $%.2f/SF/mo (%d facilities)",
                            survey.source, survey.market_rent_per_sf_mo, survey.comp_count)
            else:
                logger.info("  Rent survey: %s", survey.error or 'no data returned')
                logger.info("  Set market_rent_psf in override JSON for accurate VA modeling.")
        except Exception:
            logger.warning("  Rent survey skipped", exc_info=True)
    elif ctx.cim_data.market_rent_psf:
        logger.info("\n  Market rent (from override): $%.2f/SF/mo", ctx.cim_data.market_rent_psf)

    ctx.snapshot("after_enrich")
    enrich_changes = ctx.diff_snapshot("after_overrides")
    if enrich_changes:
        logger.debug("  Enrichment changed fields: %s", ", ".join(enrich_changes.keys()))


def stage_analyze(ctx: AnalysisContext, comp_db):
    """[3/7] Run all analysis modules."""
    logger.info("\n[3/7] Running analysis modules...")

    from analysis.financials import analyze_financials
    from analysis.market import analyze_market
    from analysis.physical import analyze_physical
    from analysis.rent_analysis import analyze_rents
    from analysis.value_add import identify_value_add
    from analysis.risks import identify_risks

    ctx.financial_analysis = analyze_financials(ctx.cim_data, comp_db=comp_db)
    if ctx.adjusted_noi:
        logger.info("  Analyst-adjusted TTM NOI: $%s", f"{ctx.adjusted_noi:,.0f}")
    else:
        logger.warning("  Could not compute adjusted NOI")
    bench_src = ctx.financial_analysis.get("benchmark_source", "static")
    logger.info("  Benchmark source: %s", bench_src)

    ctx.market_analysis = analyze_market(ctx.cim_data)
    ctx.physical_analysis = analyze_physical(ctx.cim_data)
    ctx.rent_analysis = analyze_rents(ctx.cim_data, comp_db=comp_db)

    # Value-add ops and risks are computed after valuation (need scenarios),
    # but we store the functions for later use
    ctx._analyze_value_add = lambda: identify_value_add(ctx.cim_data, ctx.financial_analysis, ctx.rent_analysis)
    ctx._analyze_risks = lambda: identify_risks(ctx.cim_data, ctx.gate_results, ctx.financial_analysis, ctx.scenario_results)


def stage_valuate(ctx: AnalysisContext):
    """[4/7] Run valuation scenarios and [5/7] solve max offer prices."""
    logger.info("\n[4/7] Running valuation scenarios...")
    from model.returns_model import build_returns_model
    from model.solver import solve_max_price, solve_max_price_value_add
    from model.value_add_model import detect_value_add, run_value_add_scenarios

    if ctx.expense_ratio:
        logger.info("  Actual expense ratio: %.1f%% (used in DCF projections)",
                    ctx.expense_ratio * 100)
    else:
        logger.info("  Expense ratio: using 40%% default (actual not available)")

    if not (ctx.adjusted_noi and ctx.asking_price > 0):
        logger.warning("  Cannot run scenarios — missing NOI or asking price")
        logger.info("[5/7] Skipping max price solve (insufficient data)")
        return

    model = build_returns_model(
        adjusted_ttm_noi=ctx.adjusted_noi,
        asking_price=ctx.asking_price,
        nrsf=ctx.nrsf,
        capex=ctx.capex,
        expense_ratio=ctx.expense_ratio,
    )
    ctx.scenario_results = model["scenarios"]
    ctx.sensitivity = model["sensitivity"]

    # Log static scenario summary
    logger.info("  Static DCF Scenarios:")
    for name in ("bear", "base", "bull"):
        s = ctx.scenario_results.get(name, {})
        irr = s.get("irr")
        moic = s.get("moic")
        yoc = s.get("yield_on_cost")
        if irr and moic and yoc:
            logger.info("    %s — IRR: %.1f%%  MOIC: %.2fx  YoC: %.1f%%",
                        name.title().ljust(6), irr * 100, moic, yoc * 100)
        else:
            logger.info("    %s — computation failed", name.title().ljust(6))

    # Value-add model (if applicable)
    if detect_value_add(ctx.cim_data):
        logger.info("\n  Value-add deal detected — running VA model...")
        ctx.va_results = run_value_add_scenarios(
            cim_data=ctx.cim_data,
            financial_analysis=ctx.financial_analysis,
            asking_price=ctx.asking_price,
            capex=ctx.capex,
        )
        logger.info("  Value-Add Scenarios:")
        for name in ("bear", "base", "bull"):
            s = ctx.va_results.get(name, {})
            irr = s.get("irr")
            moic = s.get("moic")
            stab = s.get("stabilized_noi")
            if irr and moic and stab:
                logger.info("    %s — IRR: %.1f%%  MOIC: %.2fx  Stab NOI: $%s",
                            name.title().ljust(6), irr * 100, moic, f"{stab:,.0f}")
            else:
                logger.info("    %s — computation failed", name.title().ljust(6))

    # Solve max prices
    logger.info("\n[5/7] Solving for maximum offer price...")
    ctx.max_offer = solve_max_price(
        adjusted_ttm_noi=ctx.adjusted_noi,
        capex=ctx.capex,
        expense_ratio=ctx.expense_ratio,
    )
    mp = ctx.max_offer.get("max_price")
    if mp:
        logger.info("  Static max price: $%s (cap: %.1f%%)",
                    f"{mp:,.0f}", ctx.max_offer.get('implied_entry_cap', 0) * 100)
        if ctx.asking_price:
            discount = (ctx.asking_price - mp) / ctx.asking_price
            logger.info("  Discount to asking: %.1f%%", discount * 100)

    if ctx.va_results:
        ctx.va_max_offer = solve_max_price_value_add(
            cim_data=ctx.cim_data,
            financial_analysis=ctx.financial_analysis,
            capex=ctx.capex,
        )
        va_mp = ctx.va_max_offer.get("max_price")
        if va_mp:
            logger.info("  VA max price: $%s (cap: %.1f%%)",
                        f"{va_mp:,.0f}", ctx.va_max_offer.get('implied_entry_cap', 0) * 100)


def stage_gates_and_risks(ctx: AnalysisContext):
    """Evaluate go/no-go gates, value-add ops, and risks."""
    from analysis.filters import evaluate_gates, summarize_gates

    source_log = ctx.enrichment.source_log if ctx.enrichment else {}
    ctx.gate_results = evaluate_gates(ctx.cim_data, ctx.scenario_results, ctx.va_results,
                                      source_log=source_log)
    ctx.gate_summary = summarize_gates(ctx.gate_results)

    # Now that we have gate_results & scenario_results, compute value-add ops & risks
    if hasattr(ctx, '_analyze_value_add'):
        ctx.value_add_ops = ctx._analyze_value_add()
        del ctx._analyze_value_add
    if hasattr(ctx, '_analyze_risks'):
        ctx.risk_analysis = ctx._analyze_risks()
        del ctx._analyze_risks


def stage_output(ctx: AnalysisContext, comp_db):
    """[6/7] Generate output files and save to comp database."""
    logger.info("\n[6/7] Generating output files...")
    from output.memo_writer import generate_memo
    from output.excel_writer import generate_excel

    ctx.memo_path = generate_memo(
        property_name=ctx.property_name,
        cim_data=ctx.cim_data,
        gate_results=ctx.gate_results,
        market_analysis=ctx.market_analysis,
        physical_analysis=ctx.physical_analysis,
        financial_analysis=ctx.financial_analysis,
        rent_analysis=ctx.rent_analysis,
        scenario_results=ctx.scenario_results,
        value_add=ctx.value_add_ops,
        risk_analysis=ctx.risk_analysis,
        max_offer=ctx.max_offer,
        va_results=ctx.va_results,
        va_max_offer=ctx.va_max_offer,
        output_dir=ctx.output_dir,
    )
    logger.info("  Memo: %s", ctx.memo_path)

    ctx.excel_path = generate_excel(
        property_name=ctx.property_name,
        cim_data=ctx.cim_data,
        financial_analysis=ctx.financial_analysis,
        scenario_results=ctx.scenario_results,
        sensitivity=ctx.sensitivity,
        max_offer=ctx.max_offer,
        va_results=ctx.va_results,
        va_max_offer=ctx.va_max_offer,
        output_dir=ctx.output_dir,
    )
    logger.info("  Model: %s", ctx.excel_path)

    # Generate pre-filled underwriting template
    try:
        from output.template_writer import generate_template
        ctx.template_path = generate_template(
            cim_data=ctx.cim_data,
            financial_analysis=ctx.financial_analysis,
            scenario_results=ctx.scenario_results,
            max_offer=ctx.max_offer,
            output_dir=ctx.output_dir,
            property_name=ctx.property_name,
        )
        logger.info("  Template: %s", ctx.template_path)
    except Exception:
        logger.warning("  Template generation failed", exc_info=True)

    # Save to comp database
    try:
        pdf_filename = os.path.basename(ctx.pdf_path)
        prop_id = comp_db.save_analysis(
            cim_data=ctx.cim_data,
            financial_analysis=ctx.financial_analysis,
            rent_analysis=ctx.rent_analysis,
            pdf_filename=pdf_filename,
        )
        logger.info("  Saved to comp database (property #%d, %d total)",
                    prop_id, comp_db.get_comp_count())
    except Exception:
        logger.warning("  Comp DB save failed", exc_info=True)


# ══════════════════════════════════════════════════════════════════════
#  Main orchestrator
# ══════════════════════════════════════════════════════════════════════

def main():
    setup_logging()
    _print_banner()

    # Get PDF
    pdf_path = _get_pdf_path()
    if not pdf_path:
        logger.info("No PDF selected. Exiting.")
        sys.exit(1)

    logger.info("\nAnalyzing: %s", pdf_path)
    logger.info("=" * 60)

    # Initialize shared resources
    from data.comp_db import CompDatabase
    comp_db = CompDatabase()
    comp_count = comp_db.get_comp_count()
    if comp_count:
        logger.info("  Comp database: %d properties loaded", comp_count)

    # Build context and run pipeline
    ctx = AnalysisContext(pdf_path=pdf_path)

    stage_extract(ctx)
    stage_parse(ctx)
    stage_enrich(ctx, comp_db)
    stage_analyze(ctx, comp_db)
    stage_valuate(ctx)
    stage_gates_and_risks(ctx)
    stage_output(ctx, comp_db)

    # Terminal summary
    logger.info("\n[7/7] Analysis complete.")
    _print_summary(ctx)

    return ctx


# ══════════════════════════════════════════════════════════════════════
#  Terminal output
# ══════════════════════════════════════════════════════════════════════

def _print_banner():
    print()
    print("=" * 57)
    print("  CIM ANALYST — Self-Storage Investment Analysis")
    print("=" * 57)
    print()


def _print_summary(ctx: AnalysisContext):
    cim_data = ctx.cim_data
    gate_results = ctx.gate_results
    gate_summary = ctx.gate_summary
    scenario_results = ctx.scenario_results
    max_offer = ctx.max_offer
    va_results = ctx.va_results
    va_max_offer = ctx.va_max_offer
    physical_analysis = ctx.physical_analysis

    print()
    print("=" * 57)
    print("  CIM ANALYST — RESULTS SUMMARY")
    print("=" * 57)

    # Property info
    print(f"  Property: {cim_data.property_name or 'TBD'}")
    addr = cim_data.address or ""
    city_state = f"{cim_data.city or ''}, {cim_data.state or ''}".strip(", ")
    if addr or city_state:
        print(f"  Location: {addr} {city_state}".strip())
    if cim_data.asking_price:
        print(f"  Asking Price: ${cim_data.asking_price:,.0f}")
    if cim_data.nrsf:
        units_str = f" | Units: {cim_data.total_units}" if cim_data.total_units else ""
        occ_str = f" | Occupancy: {cim_data.physical_occupancy:.1%}" if cim_data.physical_occupancy else ""
        print(f"  NRSF: {cim_data.nrsf:,.0f} SF{units_str}{occ_str}")
    print("-" * 57)

    # Replacement cost estimate & assumptions
    repl = (physical_analysis or {}).get("replacement_cost", {})
    if repl.get("estimable"):
        print()
        print("  REPLACEMENT COST ESTIMATE")
        print("  " + "-" * 40)
        type_details = repl.get("facility_type_details", [])
        if type_details:
            for td in type_details:
                print(f"  {td['type']:<26s} {td['sf']:>10,.0f} SF × ${td['hard_rate']:>6.0f}/SF = ${td['hard_cost']:>12,.0f}")
                if td["site_cost"] > 0:
                    print(f"    {'Site work':<24s} {td['sf']:>10,.0f} SF × ${td['site_rate']:>6.0f}/SF = ${td['site_cost']:>12,.0f}")
        print(f"  {'Soft costs':<26s} {repl['soft_cost_pct']:.0%} of subtotal{' ':>13s} = ${repl['soft_costs']:>12,.0f}")
        print(f"  {'Developer profit':<26s} {repl['dev_profit_pct']:.0%} of TDC{' ':>17s} = ${repl['dev_profit']:>12,.0f}")
        print(f"  {'':->53s}")
        total = repl["total_replacement"]
        print(f"  {'TOTAL REPLACEMENT COST':<26s} {'':>22s} = ${total:>12,.0f}")
        if cim_data.nrsf:
            print(f"  {'Per SF':<26s} {'':>22s}   ${total / cim_data.nrsf:>10,.0f}/SF")
        comp = (physical_analysis or {}).get("price_vs_replacement", {})
        if comp.get("comparable"):
            d = comp["discount_to_replacement"]
            label = "discount" if d > 0 else "premium"
            print(f"  Asking vs. replacement: {abs(d):.1%} {label}")

    # Gates
    print()
    print("  GO / NO-GO GATES")
    print("  " + "-" * 40)
    for g in gate_results:
        symbol = {"PASS": "✓", "FAIL": "✗", "TBD": "?"}.get(g["result"], "?")
        result_str = g["result"]
        actual = g["actual"]
        print(f"  {g['gate']}. {g['name']:<30s} {symbol} {result_str:<5s} [{actual}]")

    # Static returns
    if scenario_results:
        print()
        print("  STATIC RETURNS (Unlevered, All-Equity)")
        print("  " + "-" * 40)
        print(f"  {'':20s} {'BEAR':>8s}  {'BASE':>8s}  {'BULL':>8s}")

        for label, key, fmt in [
            ("Yr1 Yield/Cost", "yield_on_cost", lambda v: f"{v:.1%}" if v else "N/A"),
            ("5-Yr MOIC", "moic", lambda v: f"{v:.2f}x" if v else "N/A"),
            ("5-Yr IRR", "irr", lambda v: f"{v:.1%}" if v else "N/A"),
        ]:
            vals = []
            for scen in ("bear", "base", "bull"):
                v = scenario_results.get(scen, {}).get(key)
                vals.append(fmt(v))
            print(f"  {label:<20s} {vals[0]:>8s}  {vals[1]:>8s}  {vals[2]:>8s}")

    # Value-add returns
    if va_results:
        print()
        print("  VALUE-ADD RETURNS (Unlevered, All-Equity)")
        print("  " + "-" * 40)
        print(f"  {'':20s} {'BEAR':>8s}  {'BASE':>8s}  {'BULL':>8s}")

        for label, key, fmt in [
            ("Stab Yield/Cost", "yield_on_cost", lambda v: f"{v:.1%}" if v else "N/A"),
            ("5-Yr MOIC", "moic", lambda v: f"{v:.2f}x" if v else "N/A"),
            ("5-Yr IRR", "irr", lambda v: f"{v:.1%}" if v else "N/A"),
            ("Dev Spread", "development_spread", lambda v: f"{v*100:.0f}bps" if v else "N/A"),
        ]:
            vals = []
            for scen in ("bear", "base", "bull"):
                v = va_results.get(scen, {}).get(key)
                vals.append(fmt(v))
            print(f"  {label:<20s} {vals[0]:>8s}  {vals[1]:>8s}  {vals[2]:>8s}")

    # Max offer
    if max_offer and max_offer.get("max_price"):
        mp = max_offer["max_price"]
        print()
        print(f"  MAX OFFER — STATIC (for 10% Base IRR): ${mp:,.0f}")
        if max_offer.get("implied_entry_cap"):
            print(f"  Implied Entry Cap: {max_offer['implied_entry_cap']:.1%} on Adjusted TTM NOI")
        if cim_data.asking_price:
            discount = (cim_data.asking_price - mp) / cim_data.asking_price
            print(f"  Discount to Asking: {discount:.1%}")

    if va_max_offer and va_max_offer.get("max_price"):
        va_mp = va_max_offer["max_price"]
        print()
        print(f"  MAX OFFER — VALUE-ADD (for 10% VA IRR): ${va_mp:,.0f}")
        if va_max_offer.get("implied_entry_cap"):
            print(f"  Implied Entry Cap: {va_max_offer['implied_entry_cap']:.1%}")
        if cim_data.asking_price:
            discount = (cim_data.asking_price - va_mp) / cim_data.asking_price
            print(f"  Discount to Asking: {discount:.1%}")

    # Recommendation
    print()
    rec = gate_summary["recommendation"]
    print(f"  RECOMMENDATION: {rec}")
    if gate_summary["failed_gates"]:
        for g in gate_summary["failed_gates"]:
            print(f"  - {g['name']}: {g.get('note', '')}")
    if gate_summary["tbd_gates"]:
        for g in gate_summary["tbd_gates"]:
            print(f"  - Verify: {g['name']}")

    # Output files
    print()
    print(f"  Output files:")
    print(f"  → {ctx.memo_path}")
    print(f"  → {ctx.excel_path}")
    if ctx.template_path:
        print(f"  → {ctx.template_path}")
    print("=" * 57)
    print()

    # Log key results for the audit file
    logger.debug("RESULTS: recommendation=%s, gates_passed=%d/%d",
                 rec, gate_summary.get("passed", 0), gate_summary.get("total", 0))


# ══════════════════════════════════════════════════════════════════════
#  File selection
# ══════════════════════════════════════════════════════════════════════

def _get_pdf_path() -> str | None:
    """Prompt user to select a PDF file (supports up to 100 files)."""
    # List PDFs in current directory
    pdfs = sorted(set(glob.glob("*.pdf") + glob.glob("*.PDF")))[:100]

    if not pdfs:
        print("No PDF files found in current directory.")
        print("Please place a CIM PDF in this directory and try again.")
        path = input("\nOr enter full path to PDF: ").strip()
        if path and os.path.isfile(path) and path.lower().endswith(".pdf"):
            return path
        return None

    PAGE_SIZE = 20
    total_pages = (len(pdfs) + PAGE_SIZE - 1) // PAGE_SIZE
    page = 0

    while True:
        start = page * PAGE_SIZE
        end = min(start + PAGE_SIZE, len(pdfs))

        print(f"\nPDF files found ({len(pdfs)} total):")
        for i in range(start, end):
            size_mb = os.path.getsize(pdfs[i]) / (1024 * 1024)
            print(f"  {i + 1:>3}. {pdfs[i]} ({size_mb:.1f} MB)")

        if total_pages > 1:
            print(f"\n  Page {page + 1}/{total_pages}", end="")
            nav = []
            if page > 0:
                nav.append("'p' = prev page")
            if page < total_pages - 1:
                nav.append("'n' = next page")
            if nav:
                print(f"  ({', '.join(nav)})")
            else:
                print()

        print()
        choice = input(f"Select file [1-{len(pdfs)}] or type filename: ").strip()

        if choice.lower() == 'n' and page < total_pages - 1:
            page += 1
            continue
        if choice.lower() == 'p' and page > 0:
            page -= 1
            continue

        # Try as number
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(pdfs):
                return pdfs[idx]
            else:
                print(f"Number out of range. Enter 1-{len(pdfs)}.")
                continue
        except ValueError:
            pass

        # Try as filename
        if os.path.isfile(choice) and choice.lower().endswith(".pdf"):
            return choice

        # Try adding .pdf
        if os.path.isfile(choice + ".pdf"):
            return choice + ".pdf"

        print(f"Invalid selection: {choice}")
        return None


# ══════════════════════════════════════════════════════════════════════
#  JSON-Based Override Loader
# ══════════════════════════════════════════════════════════════════════
# CIM formats vary wildly. When the parser can't extract key data,
# Claude Code reads the PDF, fills in a JSON override file, and
# run.py loads it at runtime. Override files are stored in
# overrides/ and can be deleted after analysis.

OVERRIDES_DIR = os.environ.get(
    "CIM_OVERRIDES_DIR",
    os.path.join(os.path.dirname(__file__) or ".", "overrides"),
)


def _fill_manual_data(cim_data, pdf_path: str):
    """Load JSON override file for this CIM if one exists."""
    import json
    from extract.parser import UnitType, FinancialLine

    # Look for override file matching the PDF name
    pdf_base = os.path.splitext(os.path.basename(pdf_path))[0]
    override_path = os.path.join(OVERRIDES_DIR, f"{pdf_base}.json")

    if not os.path.isfile(override_path):
        return

    with open(override_path, "r") as f:
        overrides = json.load(f)

    # Simple scalar fields — set directly on cim_data
    scalar_fields = [
        "property_name", "address", "city", "state", "msa",
        "year_built", "year_expanded", "acreage",
        "nrsf", "total_units", "cc_units", "non_cc_units",
        "cc_sf", "non_cc_sf", "cc_pct",
        "ss_driveup_sf", "ss_enclosed_sf",
        "brv_enclosed_sf", "brv_covered_sf", "brv_open_sf",
        "physical_occupancy", "economic_occupancy",
        "asking_price", "price_per_sf",
        "population_1mi", "population_3mi", "population_5mi",
        "median_hhi_3mi",
        "ttm_gpr", "ttm_egr", "ttm_total_revenue", "ttm_total_expenses",
        "ttm_noi", "cim_yr1_noi", "other_income",
        "mgmt_fee_pct", "capex_estimate", "new_supply_mentions",
        "market_rent_psf",
    ]
    for key in scalar_fields:
        if key in overrides:
            setattr(cim_data, key, overrides[key])

    # Unit mix — list of dicts → UnitType objects
    if "unit_mix" in overrides:
        cim_data.unit_mix = [
            UnitType(**u) for u in overrides["unit_mix"]
        ]

    # Income lines — list of dicts → FinancialLine objects
    if "income_lines" in overrides:
        cim_data.income_lines = [
            FinancialLine(**line) for line in overrides["income_lines"]
        ]

    # Expense lines — list of dicts → FinancialLine objects
    if "expense_lines" in overrides:
        cim_data.expense_lines = [
            FinancialLine(**line) for line in overrides["expense_lines"]
        ]

    logger.info("  Loaded overrides from: %s", override_path)


if __name__ == "__main__":
    main()
