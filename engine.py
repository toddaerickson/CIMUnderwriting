"""
Callable analysis pipeline — the web↔pipeline boundary.

Mirrors run.py's main() but returns all intermediate results
instead of printing to terminal.
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable

logger = logging.getLogger("cim_analyst.engine")


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
    # Capital stack (model.returns_model.build_sources_uses)
    sources_uses: dict = field(default_factory=dict)
    # The market cap this run priced the exit off, with its class, age band
    # and source. Every published exit cap derives from it, so it is carried
    # on the result rather than re-derived by each writer.
    market_cap: dict = field(default_factory=dict)
    # Levered lens (item E3a). `debt` is the one sized loan; `levered` is
    # per-scenario equity cash flow + LP waterfall. The UNLEVERED screen
    # above stays primary — financing costs are deliberately absent from
    # every `total_basis`, so nothing in `scenario_results` moves when a
    # deal carries debt.
    debt: dict = field(default_factory=dict)
    levered: dict = field(default_factory=dict)
    # Solver
    max_offer: dict = field(default_factory=dict)
    va_max_offer: dict = field(default_factory=dict)
    # The levered max offer (item E4): the price at which the deal still
    # clears the fund's LP NET IRR target, after debt service, the AM fee
    # and the promote. A SECOND LENS beside `max_offer`, never a
    # replacement — `max_offer` is the price the primary unlevered gate is
    # measured against, and the two answer different questions.
    levered_max_offer: dict = field(default_factory=dict)
    # Value-add & risks
    value_add: dict = field(default_factory=dict)
    risk_analysis: dict = field(default_factory=dict)
    # Model error-check register (analysis/checks.py), JSON-safe rows
    checks: list = field(default_factory=list)
    check_summary: dict = field(default_factory=dict)
    # Outputs
    memo_path: str = ""
    excel_path: str = ""
    template_path: str = ""
    summary_path: str = ""
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


#: Demographic fields the Census enrichment can fill — the re-enrichment
#: gate in run_analysis() only calls out when one of these is still None.
ENRICHABLE_FIELDS = ("population_1mi", "population_3mi", "population_5mi",
                     "median_hhi_3mi")


def run_analysis(result: AnalysisResult, progress: Callable = None,
                  output_dir: str = None,
                  custom_scenarios: dict = None,
                  custom_va_scenarios: dict = None,
                  solver_target_irr: float = None,
                  enrich: bool = False,
                  expense_line_overrides: dict = None,
                  hold_years: int = None,
                  transaction_costs: dict = None,
                  capital_structure: dict = None,
                  market_cap_rate: float = None,
                  market_cap: dict = None,
                  debt_terms: dict = None,
                  waterfall_terms: dict = None,
                  am_fee_pct: float = None) -> AnalysisResult:
    """
    Run full analysis pipeline on an already-extracted CIMData.

    Args:
        result: AnalysisResult from extract_pdf_data()
        progress: callable(step, total, message)
        custom_scenarios: per-analysis Bear/Base/Bull overrides
        custom_va_scenarios: per-analysis value-add scenario overrides
        solver_target_irr: per-analysis max-offer IRR target; None keeps
            the config.SOLVER_TARGET_IRR default (solver binds it as a
            function default at import, so a parameter is the only seam)
        enrich: re-run Census enrichment before analyzing. Extraction-time
            enrichment ran before the analyst could correct address/city/
            state in the assumptions form, so a fixed address never got a
            second chance at geocoding. Tier-1 precedence makes the re-run
            safe: CIM/analyst values always win; Census only fills gaps.
            Off by default so the CLI and tests stay network-free.
        expense_line_overrides: dict[benchmark_key, float] of analyst-
            entered expense line values (dense model view). Beats the
            CIM-extracted value for the same line; the benchmark
            adjustment still applies on top. None everywhere the CLI
            path runs, so run.py behavior is unchanged.
        hold_years: hold period in years; None keeps
            config.DEFAULT_HOLD_YEARS. Drives the static DCF, the
            sensitivity grid, both solvers and the value-add engine —
            they must not be given different holds.
        transaction_costs: per-analysis override of
            config.TRANSACTION_COSTS. A partial dict is merged onto the
            defaults; a missing key means "default", never zero.
        capital_structure: per-analysis capital-stack inputs (item D) —
            `capex_basis`, `operating_reserve`,
            `operating_reserve_basis`, `gp_coinvest_pct`. A partial dict
            is merged onto the config defaults. Passed as a parameter
            rather than patched into config for the reason spelled out in
            config.py: a patched dict is shared mutable state across
            concurrent runs.
        market_cap_rate: the analyst's market cap off the assumptions
            form, as a decimal, or None to look the asset's class and age
            band up in config.MARKET_CAP_RATES. Every exit cap in the run
            derives from whichever it is.
        market_cap: an ALREADY-RESOLVED anchor dict from
            `analysis.valuation.resolve_market_cap`, used as-is. For a
            caller that had to resolve before entering the analysis lock;
            passing its rate through `market_cap_rate` instead would
            re-enter the resolver's analyst-override branch and relabel a
            table lookup as analyst-entered. Wins over `market_cap_rate`.
        debt_terms: per-analysis override of config.DEBT_TERMS (item
            E3a). A partial dict is merged onto the defaults. The levered
            lens is ON by default, so None means "size the loan at the
            config terms", not "run this deal unlevered".
        waterfall_terms: per-analysis override of config.WATERFALL_TERMS.
            `gp_coinvest_pct` is NOT read from here — it comes from
            `capital_structure`, so the capital stack and the waterfall
            cannot disagree about whose equity it is.
        am_fee_pct: per-analysis annual management fee rate; None keeps
            config.AM_FEE_PCT. Charged above the waterfall, on invested
            equity measured at the start of each period.

    Returns:
        Updated AnalysisResult with all analysis fields populated
    """
    cim_data = result.cim_data

    def _progress(step, total, msg):
        if progress:
            progress(step, total, msg)

    from data.comp_db import CompDatabase
    comp_db = CompDatabase()

    if enrich and any(getattr(cim_data, f, None) is None
                      for f in ENRICHABLE_FIELDS):
        try:
            from extract.enrichment import enrich_cim_data
            enrichment = enrich_cim_data(cim_data, comp_db=comp_db)
            result.enrichment = enrichment
            for err in enrichment.errors:
                msg = f"Enrichment: {err}"
                if msg not in result.errors:
                    result.errors.append(msg)
        except Exception as e:
            result.errors.append(f"Enrichment failed: {e}")

    from analysis.valuation import resolve_market_cap
    from registry import detect_asset_type

    # Step 1: Financial analysis
    _progress(1, 9, "Analyzing financials...")
    from analysis.financials import analyze_financials
    result.financial_analysis = analyze_financials(
        cim_data, comp_db=comp_db,
        expense_line_overrides=expense_line_overrides)
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
    from model.returns_model import (BASIS_AMOUNT, BASIS_PCT_PRICE,
                                     resolve_capital_amount,
                                     resolve_capital_structure)

    capital = resolve_capital_structure(capital_structure)
    asking = cim_data.asking_price or 0
    nrsf = cim_data.nrsf or 1

    # CapEx and the reserve are entered on a basis (item D / H). Resolve
    # to dollars ONCE, here, and hand dollars to everything downstream —
    # the scenario engine, the VA engine, the solvers, the memo and the
    # Excel writer all read the same figure rather than each re-deriving
    # it from a rate. `cim_data.nrsf` is passed raw, NOT the `or 1`
    # fallback above: a $/SF rate times a fabricated 1 SF is a fabricated
    # dollar amount, and resolve_capital_amount's job is to refuse that.
    capex = resolve_capital_amount(
        cim_data.capex_estimate, capital["capex_basis"],
        nrsf=cim_data.nrsf, units=cim_data.total_units, price=asking)
    reserve = resolve_capital_amount(
        capital["operating_reserve"], capital["operating_reserve_basis"],
        nrsf=cim_data.nrsf, units=cim_data.total_units, price=asking)
    # Only a percentage-of-price CapEx moves with the price the solver is
    # trying; every other basis is fixed dollars by the time we get here.
    capex_pct_of_price = (float(cim_data.capex_estimate or 0.0)
                          if capital["capex_basis"] == BASIS_PCT_PRICE
                          else None)

    # A rate whose driver went missing resolves to $0 (see
    # resolve_capital_amount). The assumptions form refuses that
    # combination, but a saved basis outlives the value it was validated
    # against: re-extracting a deal rewrites cim_json, so a re-parse that
    # loses NRSF turns a valid "$0.50/SF CapEx" into $0 on the NEXT run
    # with nothing on screen to say so. A quiet $0 line in the capital
    # stack is exactly the "empty state hiding a real failure" this
    # pipeline is not allowed to produce, so it becomes a run warning.
    for label, entered, basis, resolved in (
            ("CapEx", cim_data.capex_estimate, capital["capex_basis"], capex),
            ("Operating reserve", capital["operating_reserve"],
             capital["operating_reserve_basis"], reserve)):
        if basis != BASIS_AMOUNT and entered and not resolved:
            result.errors.append(
                f"{label} was entered on a '{basis}' basis but resolved to "
                f"$0 — the figure it multiplies (NRSF, unit count or asking "
                f"price) is missing from this deal, so it is NOT in the "
                f"basis. Re-enter it in dollars or restore the missing field.")

    # The exit cap is derived from the asset, so the market anchor is
    # resolved ONCE — same discipline as CapEx and the reserve above. Every
    # consumer gets the same dict, which is what keeps the memo, the .xlsx,
    # the .xlsm, the sensitivity grid and both solvers on one cap.
    #
    # A caller that already resolved it passes the DICT, not the rate.
    # webapp.services does: it must resolve before taking the analysis lock
    # (off the pristine table), and re-resolving here from its rate would
    # look like an analyst override to `resolve_market_cap`, whose "an
    # explicit market_cap always wins" branch cannot tell a typed rate from
    # a resolved one. That stamped `source: "analyst"` on every web run and
    # silently disabled the unknown-vintage finding in the check register,
    # which is gated on `source == "table"` (review finding, PR #31).
    if not market_cap:
        market_cap = resolve_market_cap(
            detect_asset_type(cim_data), cim_data.year_built,
            market_cap=market_cap_rate)
    result.market_cap = market_cap

    # Bound here, not only inside the branch: the template writer reads
    # them at step 9 and a deal that never sized (no NOI, or no price)
    # would otherwise raise NameError instead of writing a workbook.
    resolved_debt_terms = None
    resolved_waterfall_terms = None

    if result.adjusted_noi and asking > 0:
        from model.debt import resolve_debt_terms
        from model.returns_model import build_returns_model
        from model.waterfall import resolve_waterfall_terms

        # The waterfall's co-invest comes from the DEAL, never from
        # config: `resolve_capital_structure` already resolved it above
        # and `build_sources_uses` splits the equity by it. Resolved
        # without `capital_structure=`, a deal edited to 25% would print
        # a stack split 25/75 beside an LP net IRR computed on 10/90.
        resolved_debt_terms = resolve_debt_terms(debt_terms)
        resolved_waterfall_terms = resolve_waterfall_terms(
            waterfall_terms, capital_structure=capital)

        model = build_returns_model(
            adjusted_ttm_noi=result.adjusted_noi,
            asking_price=asking,
            nrsf=nrsf,
            capex=capex,
            custom_scenarios=custom_scenarios,
            expense_ratio=result.expense_ratio,
            hold_years=hold_years,
            transaction_costs=transaction_costs,
            reserve=reserve,
            gp_coinvest_pct=capital["gp_coinvest_pct"],
            capex_pct_of_price=capex_pct_of_price,
            market_cap=market_cap,
            debt_terms=resolved_debt_terms,
            waterfall_terms=resolved_waterfall_terms,
            am_fee_pct=am_fee_pct,
        )
        result.scenario_results = model["scenarios"]
        result.sensitivity = model["sensitivity"]
        result.sources_uses = model["sources_uses"]
        result.debt = model["debt"]
        result.levered = model["levered"]

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
                hold_years=hold_years,
                transaction_costs=transaction_costs,
                reserve=reserve,
                market_cap=market_cap,
            )

        # Step 7: Max price solver
        _progress(7, 9, "Solving for maximum offer price...")
        from model.solver import (solve_max_price, solve_max_price_levered,
                                  solve_max_price_value_add)
        # Shared by all three solvers. `target_irr` is deliberately NOT in
        # here — it is the UNLEVERED target and the levered solver takes a
        # different one under a different name, so it is added per call.
        solver_kwargs = {"hold_years": hold_years,
                         "transaction_costs": transaction_costs,
                         "reserve": reserve,
                         "capex_pct_of_price": capex_pct_of_price,
                         "market_cap": market_cap}
        unlevered_kwargs = dict(solver_kwargs)
        if solver_target_irr:
            unlevered_kwargs["target_irr"] = solver_target_irr
        result.max_offer = solve_max_price(
            adjusted_ttm_noi=result.adjusted_noi,
            capex=capex,
            expense_ratio=result.expense_ratio,
            **unlevered_kwargs,
        )

        # The levered max offer (item E4). It takes the DEAL's resolved
        # levered assumption set — the same objects `build_returns_model`
        # was handed above — so the price it solves for is priced on the
        # terms the results page shows. Re-resolving them here from config
        # would answer for a deal nobody is looking at.
        #
        # `solver_target_irr` is deliberately NOT forwarded: it is the
        # UNLEVERED target (10% by default, per-deal editable), and this
        # solver targets LP net (15%). One number cannot be both, and
        # passing the unlevered target here would quietly re-price the
        # levered answer whenever an analyst edited the unlevered one.
        result.levered_max_offer = solve_max_price_levered(
            adjusted_ttm_noi=result.adjusted_noi,
            capex=capex,
            expense_ratio=result.expense_ratio,
            debt_terms=resolved_debt_terms,
            waterfall_terms=resolved_waterfall_terms,
            am_fee_pct=am_fee_pct,
            gp_coinvest_pct=capital["gp_coinvest_pct"],
            **solver_kwargs,
        )
        if result.va_results:
            result.va_max_offer = solve_max_price_value_add(
                cim_data=cim_data,
                financial_analysis=result.financial_analysis,
                capex=capex,
                # `unlevered_kwargs`, not `solver_kwargs`: the VA solver
                # targets the same unlevered IRR as `solve_max_price` and
                # must keep honouring a per-deal `solver_target_irr`.
                **unlevered_kwargs,
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
        result.scenario_results, result.rent_analysis)

    # Model error-check register — evaluated ONCE here, then handed to every
    # output surface. The memo, the Excel Checks sheet and the results page
    # must report the same findings, not three independent evaluations of
    # slightly different inputs.
    from analysis import checks as model_checks
    _check_results = model_checks.run_checks(model_checks.input_from_cim(
        cim_data, result.financial_analysis, result.physical_analysis,
        result.scenario_results, result.sources_uses,
        va_results=result.va_results, market_cap=result.market_cap,
        debt=result.debt))
    result.checks = model_checks.to_dicts(_check_results)
    result.check_summary = model_checks.summarize(_check_results)

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
        checks=result.checks,
        sources_uses=result.sources_uses,
        # The levered lens (item E3b). Both writers degrade cleanly when
        # these are empty — a deal with no NOI or no asking price prices
        # no loan, and the memo and workbook must still build.
        levered=result.levered,
        debt=result.debt,
        levered_max_offer=result.levered_max_offer,
        output_dir=output_dir,
    )

    # LP-facing 2-page condensation (item G). Reads the SAME result dicts
    # the memo just rendered — it is a second rendering, never a second
    # computation, so the two documents cannot disagree about a deal.
    # Wrapped like the template writer because a formatting failure in an
    # external-facing extra must not cost the analyst the IC memo and the
    # model that already succeeded.
    try:
        from output.memo_writer import generate_investor_summary
        result.summary_path = generate_investor_summary(
            property_name=property_name,
            cim_data=cim_data,
            market_analysis=result.market_analysis,
            physical_analysis=result.physical_analysis,
            scenario_results=result.scenario_results,
            risk_analysis=result.risk_analysis,
            gate_results=result.gate_results,
            sources_uses=result.sources_uses,
            levered=result.levered,
            output_dir=output_dir,
        )
    except Exception as e:
        result.errors.append(f"Investor summary generation failed: {e}")

    result.excel_path = generate_excel(
        property_name=property_name,
        cim_data=cim_data,
        financial_analysis=result.financial_analysis,
        scenario_results=result.scenario_results,
        sensitivity=result.sensitivity,
        max_offer=result.max_offer,
        va_results=result.va_results,
        va_max_offer=result.va_max_offer,
        checks=result.checks,
        sources_uses=result.sources_uses,
        levered=result.levered,
        debt=result.debt,
        levered_max_offer=result.levered_max_offer,
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
            hold_years=hold_years,
            transaction_costs=transaction_costs,
            capex=capex,
            # Resolved ONCE above and handed down, never re-resolved here
            # — `resolve_waterfall_terms` needs the deal's capital
            # structure for `gp_coinvest_pct`, and a second resolution
            # without it prints a waterfall the run never used. These are
            # None only when the deal never sized (no NOI or no price),
            # in which case the writer falls back to config defaults.
            debt_terms=resolved_debt_terms,
            waterfall_terms=resolved_waterfall_terms,
            am_fee_pct=am_fee_pct,
            sources_uses=result.sources_uses,
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
