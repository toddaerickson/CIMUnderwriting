"""
Callable analysis pipeline for the Streamlit GUI.

Mirrors run.py's main() but returns all intermediate results
instead of printing to terminal.
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable

logger = logging.getLogger("cim_analyst.gui")


@dataclass
class AnalysisResult:
    """Container for all analysis outputs."""
    # Input
    pdf_path: str = ""
    # Extraction
    cim_data: object = None
    extraction_report: dict = field(default_factory=dict)
    enrichment: object = None
    # Analysis modules
    financial_analysis: dict = field(default_factory=dict)
    market_analysis: dict = field(default_factory=dict)
    physical_analysis: dict = field(default_factory=dict)
    rent_analysis: dict = field(default_factory=dict)
    # Gates
    gate_results: list = field(default_factory=list)
    gate_summary: dict = field(default_factory=dict)
    # Scenarios
    scenario_results: dict = field(default_factory=dict)
    sensitivity: dict = field(default_factory=dict)
    va_results: dict = field(default_factory=dict)
    # Solver
    max_offer: dict = field(default_factory=dict)
    va_max_offer: dict = field(default_factory=dict)
    # Value-add & risks
    value_add: dict = field(default_factory=dict)
    risk_analysis: dict = field(default_factory=dict)
    # Outputs
    memo_path: str = ""
    excel_path: str = ""
    template_path: str = ""
    # Metadata
    errors: list = field(default_factory=list)
    adjusted_noi: Optional[float] = None
    expense_ratio: Optional[float] = None


def extract_pdf_data(pdf_path: str, cim_overrides: dict = None,
                     progress: Callable = None) -> AnalysisResult:
    """
    Run extraction + parsing + enrichment stages.

    Args:
        pdf_path: path to the CIM PDF file
        cim_overrides: dict of CIMData field overrides from GUI form
        progress: callable(step, total, message) for progress updates

    Returns:
        AnalysisResult with cim_data populated (analysis fields empty)
    """
    result = AnalysisResult(pdf_path=pdf_path)

    def _progress(step, total, msg):
        if progress:
            progress(step, total, msg)

    # Step 1: Extract PDF
    _progress(1, 4, "Extracting PDF text and tables...")
    from extract.pdf_reader import extract_pdf
    raw = extract_pdf(pdf_path)

    # Step 2: Parse CIM
    _progress(2, 4, "Parsing CIM data...")
    from extract.parser import parse_cim
    cim_data = parse_cim(raw)
    result.cim_data = cim_data
    result.extraction_report = cim_data.extraction_report()

    # Step 3: Apply manual overrides from GUI
    if cim_overrides:
        _apply_overrides(cim_data, cim_overrides)

    # Also load JSON override file if it exists
    from run import _fill_manual_data
    _fill_manual_data(cim_data, pdf_path)

    result.extraction_report = cim_data.extraction_report()

    # Step 4: Enrichment
    _progress(3, 4, "Running data enrichment...")
    try:
        from extract.enrichment import enrich_cim_data
        from data.comp_db import CompDatabase
        comp_db = CompDatabase()
        enrichment = enrich_cim_data(cim_data, comp_db=comp_db)
        result.enrichment = enrichment
    except Exception as e:
        result.errors.append(f"Enrichment skipped: {e}")

    # Rent survey
    _progress(4, 4, "Running rent survey...")
    if not cim_data.market_rent_psf and cim_data.city and cim_data.state:
        try:
            from extract.rent_survey import run_rent_survey
            survey = run_rent_survey(city=cim_data.city, state=cim_data.state)
            if survey.success and survey.market_rent_per_sf_mo:
                cim_data.market_rent_psf = round(survey.market_rent_per_sf_mo, 2)
        except Exception as e:
            result.errors.append(f"Rent survey skipped: {e}")

    return result


def run_analysis(result: AnalysisResult, progress: Callable = None,
                  output_dir: str = None,
                  custom_scenarios: dict = None,
                  custom_va_scenarios: dict = None) -> AnalysisResult:
    """
    Run full analysis pipeline on an already-extracted CIMData.

    Args:
        result: AnalysisResult from extract_pdf_data()
        progress: callable(step, total, message)
        custom_scenarios: per-analysis Bear/Base/Bull overrides
        custom_va_scenarios: per-analysis value-add scenario overrides

    Returns:
        Updated AnalysisResult with all analysis fields populated
    """
    cim_data = result.cim_data

    def _progress(step, total, msg):
        if progress:
            progress(step, total, msg)

    from data.comp_db import CompDatabase
    comp_db = CompDatabase()

    # Step 1: Financial analysis
    _progress(1, 9, "Analyzing financials...")
    from analysis.financials import analyze_financials
    result.financial_analysis = analyze_financials(cim_data, comp_db=comp_db)
    result.adjusted_noi = result.financial_analysis.get(
        "adjusted_ttm_noi", {}).get("analyst_adjusted_noi")
    result.expense_ratio = result.financial_analysis.get(
        "expense_ratio_check", {}).get("opex_revenue_ratio")

    # Step 2: Market analysis
    _progress(2, 9, "Analyzing market...")
    from analysis.market import analyze_market
    result.market_analysis = analyze_market(cim_data)

    # Step 3: Physical analysis
    _progress(3, 9, "Analyzing property & replacement cost...")
    from analysis.physical import analyze_physical
    result.physical_analysis = analyze_physical(cim_data)

    # Step 4: Rent analysis
    _progress(4, 9, "Analyzing rents...")
    from analysis.rent_analysis import analyze_rents
    result.rent_analysis = analyze_rents(cim_data, comp_db=comp_db)

    # Step 5: Scenario modeling
    _progress(5, 9, "Running Bear/Base/Bull scenarios...")
    asking = cim_data.asking_price or 0
    capex = cim_data.capex_estimate or 0
    nrsf = cim_data.nrsf or 1

    if result.adjusted_noi and asking > 0:
        from model.returns_model import build_returns_model
        model = build_returns_model(
            adjusted_ttm_noi=result.adjusted_noi,
            asking_price=asking,
            nrsf=nrsf,
            capex=capex,
            custom_scenarios=custom_scenarios,
            expense_ratio=result.expense_ratio,
        )
        result.scenario_results = model["scenarios"]
        result.sensitivity = model["sensitivity"]

        # Step 6: Value-add
        _progress(6, 9, "Checking value-add potential...")
        from model.value_add_model import detect_value_add, run_value_add_scenarios
        if detect_value_add(cim_data):
            result.va_results = run_value_add_scenarios(
                cim_data=cim_data,
                financial_analysis=result.financial_analysis,
                asking_price=asking,
                capex=capex,
                custom_scenarios=custom_va_scenarios,
            )

        # Step 7: Max price solver
        _progress(7, 9, "Solving for maximum offer price...")
        from model.solver import solve_max_price, solve_max_price_value_add
        result.max_offer = solve_max_price(
            adjusted_ttm_noi=result.adjusted_noi,
            capex=capex,
            expense_ratio=result.expense_ratio,
        )
        if result.va_results:
            result.va_max_offer = solve_max_price_value_add(
                cim_data=cim_data,
                financial_analysis=result.financial_analysis,
                capex=capex,
            )
    else:
        result.errors.append("Cannot run scenarios — missing NOI or asking price")

    # Step 8: Gates & risks
    _progress(8, 9, "Evaluating gates & risks...")
    from analysis.filters import evaluate_gates, summarize_gates
    from analysis.value_add import identify_value_add
    from analysis.risks import identify_risks

    source_log = result.enrichment.source_log if result.enrichment else {}
    result.gate_results = evaluate_gates(
        cim_data, result.scenario_results, result.va_results,
        source_log=source_log)
    result.gate_summary = summarize_gates(result.gate_results)
    result.value_add = identify_value_add(
        cim_data, result.financial_analysis, result.rent_analysis)
    result.risk_analysis = identify_risks(
        cim_data, result.gate_results, result.financial_analysis,
        result.scenario_results)

    # Step 9: Generate output files
    _progress(9, 9, "Generating memo & model...")
    if not output_dir:
        output_dir = os.path.dirname(result.pdf_path) or "."
    property_name = cim_data.property_name or "Unknown_Property"

    from output.memo_writer import generate_memo
    from output.excel_writer import generate_excel

    result.memo_path = generate_memo(
        property_name=property_name,
        cim_data=cim_data,
        gate_results=result.gate_results,
        market_analysis=result.market_analysis,
        physical_analysis=result.physical_analysis,
        financial_analysis=result.financial_analysis,
        rent_analysis=result.rent_analysis,
        scenario_results=result.scenario_results,
        value_add=result.value_add,
        risk_analysis=result.risk_analysis,
        max_offer=result.max_offer,
        va_results=result.va_results,
        va_max_offer=result.va_max_offer,
        output_dir=output_dir,
    )

    result.excel_path = generate_excel(
        property_name=property_name,
        cim_data=cim_data,
        financial_analysis=result.financial_analysis,
        scenario_results=result.scenario_results,
        sensitivity=result.sensitivity,
        max_offer=result.max_offer,
        va_results=result.va_results,
        va_max_offer=result.va_max_offer,
        output_dir=output_dir,
    )

    # Generate pre-filled underwriting template
    try:
        from output.template_writer import generate_template
        result.template_path = generate_template(
            cim_data=cim_data,
            financial_analysis=result.financial_analysis,
            scenario_results=result.scenario_results,
            max_offer=result.max_offer,
            output_dir=output_dir,
            property_name=property_name,
        )
    except Exception as e:
        result.errors.append(f"Template generation failed: {e}")

    # Save to comp database
    try:
        pdf_filename = os.path.basename(result.pdf_path)
        comp_db.save_analysis(
            cim_data=cim_data,
            financial_analysis=result.financial_analysis,
            rent_analysis=result.rent_analysis,
            pdf_filename=pdf_filename,
        )
    except Exception as e:
        result.errors.append(f"Comp DB save failed: {e}")

    return result


def run_full_pipeline(pdf_path: str, cim_overrides: dict = None,
                      progress: Callable = None,
                      output_dir: str = None) -> AnalysisResult:
    """
    Convenience: extract + analyze in one call.
    """
    result = extract_pdf_data(pdf_path, cim_overrides, progress=progress)
    return run_analysis(result, progress=progress, output_dir=output_dir)


def _apply_overrides(cim_data, overrides: dict):
    """Apply a dict of field overrides to a CIMData instance."""
    from extract.parser import UnitType, FinancialLine

    # Structured list fields
    if "unit_mix" in overrides:
        cim_data.unit_mix = [UnitType(**u) for u in overrides.pop("unit_mix")]
    if "income_lines" in overrides:
        cim_data.income_lines = [
            FinancialLine(**l) for l in overrides.pop("income_lines")]
    if "expense_lines" in overrides:
        cim_data.expense_lines = [
            FinancialLine(**l) for l in overrides.pop("expense_lines")]

    # Scalar fields
    for key, val in overrides.items():
        if val is not None and hasattr(cim_data, key):
            setattr(cim_data, key, val)
