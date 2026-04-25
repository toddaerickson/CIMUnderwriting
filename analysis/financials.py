"""
Section 4 — Historical Financial Review & Expense Benchmarking.

This is the most critical analysis module. It normalizes the CIM's
income/expense data, benchmarks each line against $/NRSF standards,
and produces an analyst-adjusted NOI that is the anchor for all
return calculations.

The program never trusts CIM expenses at face value.
"""

from config import (EXPENSE_BENCHMARKS, STATE_PROPERTY_TAX_MULTIPLIER,
                    STATE_PROPERTY_TAX_FORMULAS, get_regional_benchmarks)
from registry import EXPENSE_CATEGORIES, EXPENSE_KEYWORD_MAP, EXPENSE_KEYS


def analyze_financials(cim_data, comp_db=None) -> dict:
    """
    Produce financial analysis with benchmarked expenses and adjusted NOI.

    Returns:
        - income_summary: normalized revenue build-up
        - expense_analysis: each line with $/NRSF, benchmark, and flag
        - adjustments: list of analyst adjustments applied
        - adjusted_ttm_noi: conservative reunderwritten NOI
        - expense_ratio_check: OpEx/Revenue ratio analysis
    """
    nrsf = cim_data.nrsf or 1  # prevent division by zero
    state = (cim_data.state or "").upper().strip()
    cc_pct = cim_data.cc_pct
    income = _build_income_summary(cim_data)
    expenses = _analyze_expenses(cim_data, nrsf, income.get("egr", 0), state,
                                 comp_db=comp_db, cc_pct=cc_pct)
    adjustments = expenses["adjustments"]
    adjusted_noi = _compute_adjusted_noi(income, expenses, cim_data)

    return {
        "income_summary": income,
        "expense_analysis": expenses,
        "adjustments": adjustments,
        "adjusted_ttm_noi": adjusted_noi,
        "expense_ratio_check": _expense_ratio_check(expenses, income, nrsf, state),
        "benchmark_source": expenses.get("benchmark_source", "static"),
    }


def _build_income_summary(cim_data) -> dict:
    """Normalize income statement."""
    gpr = cim_data.ttm_gpr
    egr = cim_data.ttm_egr
    total_rev = cim_data.ttm_total_revenue
    other_inc = cim_data.other_income or 0

    # Try to reconstruct if missing
    if egr is None and gpr and cim_data.physical_occupancy:
        egr = gpr * cim_data.physical_occupancy

    if total_rev is None and egr:
        total_rev = egr + other_inc

    vacancy_pct = None
    if gpr and egr and gpr > 0:
        vacancy_pct = 1.0 - (egr / gpr)

    return {
        "gpr": gpr,
        "vacancy_pct": vacancy_pct,
        "vacancy_loss": (gpr - egr) if (gpr and egr) else None,
        "egr": egr,
        "other_income": other_inc,
        "total_revenue": total_rev,
    }


def _analyze_expenses(cim_data, nrsf: float, egr: float, state: str = "",
                      comp_db=None, cc_pct: float = None) -> dict:
    """
    Analyze each expense line against benchmarks.

    For each line: compute $/NRSF, compare to benchmark range,
    flag if below range, and compute adjusted value.

    Benchmark sourcing hierarchy:
      1. Comp database (Tier 3) — if enough historical comps
      2. Regional config benchmarks (Tier 4a)
      3. National config benchmarks (Tier 4b)

    Property tax uses income-based formula when available.
    """
    lines = []
    adjustments = []
    total_cim_expenses = 0
    total_adjusted_expenses = 0

    # Get benchmarks: try comp DB first, then regional, then national
    benchmarks, benchmark_source = _get_benchmarks(state, nrsf, cc_pct, comp_db)

    # State property tax: prefer income-based formula, fall back to $/SF benchmark
    ptax_mult = STATE_PROPERTY_TAX_MULTIPLIER.get(state, 1.0)
    ptax_formula = STATE_PROPERTY_TAX_FORMULAS.get(state)

    # Map CIM expense lines to benchmark categories
    expense_map = _map_expense_lines(cim_data)

    for cat in EXPENSE_CATEGORIES:
        category = cat.display_name
        benchmark_key = cat.key
        cim_value = expense_map.get(benchmark_key)
        per_nrsf = cim_value / nrsf if cim_value else None

        # ── Property Tax: use income-based formula when available ──
        if benchmark_key == "property_tax" and ptax_formula and cim_data.ttm_noi:
            formula_tax = _compute_formula_property_tax(cim_data.ttm_noi, ptax_formula)
            formula_per_sf = formula_tax / nrsf if nrsf else 0

            adjusted = cim_value
            flag = None

            if cim_value is not None:
                # Use max(CIM actual, formula estimate) — conservative
                if cim_value >= formula_tax:
                    flag = "ABOVE FORMULA"
                    # Keep CIM value — conservative
                else:
                    flag = "BELOW FORMULA"
                    adjusted = formula_tax
                    adjustments.append(
                        f"{category}: CIM ${cim_value:,.0f} (${per_nrsf:.2f}/SF) below "
                        f"income-based estimate ${formula_tax:,.0f} (${formula_per_sf:.2f}/SF). "
                        f"Formula: (NOI/${ptax_formula['cap_rate']:.0%} cap) × "
                        f"{ptax_formula['assessment_ratio']:.0%} assess × "
                        f"{ptax_formula['tax_rate']:.1%} rate. Adjusted to formula."
                    )
            else:
                adjusted = formula_tax
                flag = "FORMULA"

            if cim_value:
                total_cim_expenses += cim_value
            total_adjusted_expenses += (adjusted or formula_tax)

            lines.append({
                "category": category,
                "benchmark_key": benchmark_key,
                "cim_value": cim_value,
                "per_nrsf": per_nrsf,
                "benchmark_range": (formula_per_sf, formula_per_sf),
                "benchmark_mid": formula_per_sf,
                "formula_tax": formula_tax,
                "adjusted_value": adjusted or formula_tax,
                "flag": flag,
            })
            continue

        # ── All other expenses: $/SF benchmark approach ──
        bench_low, bench_high = benchmarks[benchmark_key]

        # Apply state multiplier to property tax benchmarks (non-formula states)
        if benchmark_key == "property_tax" and ptax_mult != 1.0:
            bench_low = round(bench_low * ptax_mult, 2)
            bench_high = round(bench_high * ptax_mult, 2)

        bench_mid = (bench_low + bench_high) / 2

        # Determine if adjustment needed
        adjusted = cim_value
        flag = None

        if per_nrsf is not None:
            if per_nrsf < bench_low:
                flag = "BELOW RANGE"
                adjusted = bench_low * nrsf
                adjustments.append(
                    f"{category}: CIM ${per_nrsf:.2f}/SF below benchmark range "
                    f"(${bench_low:.2f}-${bench_high:.2f}). Adjusted to range floor ${bench_low:.2f}/SF."
                )
            elif per_nrsf > bench_high:
                flag = "ABOVE RANGE"
                # Keep CIM value if above range — conservative
            else:
                flag = "IN RANGE"

        if cim_value:
            total_cim_expenses += cim_value
        total_adjusted_expenses += (adjusted or bench_low * nrsf)

        lines.append({
            "category": category,
            "benchmark_key": benchmark_key,
            "cim_value": cim_value,
            "per_nrsf": per_nrsf,
            "benchmark_range": (bench_low, bench_high),
            "benchmark_mid": bench_mid,
            "adjusted_value": adjusted or bench_low * nrsf,
            "flag": flag,
        })

    # Management fee (% of EGR)
    mgmt_pct = cim_data.mgmt_fee_pct
    mgmt_low, mgmt_high = EXPENSE_BENCHMARKS["mgmt_fee_pct"]
    mgmt_mid = (mgmt_low + mgmt_high) / 2

    mgmt_value = egr * mgmt_pct if (egr and mgmt_pct) else None
    mgmt_adjusted = mgmt_value

    if mgmt_pct is not None:
        if mgmt_pct < mgmt_low:
            mgmt_adjusted = egr * 0.05 if egr else None  # Adjust to 5%
            adjustments.append(
                f"Management Fee: CIM {mgmt_pct:.1%} below minimum {mgmt_low:.0%}. "
                f"Adjusted to 5% of EGR."
            )
        elif mgmt_pct > mgmt_high:
            pass  # Keep CIM value
    elif egr:
        mgmt_adjusted = egr * 0.05
        adjustments.append("Management Fee: Not found in CIM. Assumed 5% of EGR.")

    if mgmt_value:
        total_cim_expenses += mgmt_value
    if mgmt_adjusted:
        total_adjusted_expenses += mgmt_adjusted

    lines.append({
        "category": "Management Fee",
        "benchmark_key": "mgmt_fee_pct",
        "cim_value": mgmt_value,
        "cim_pct": mgmt_pct,
        "benchmark_range_pct": (mgmt_low, mgmt_high),
        "adjusted_value": mgmt_adjusted,
        "adjusted_pct": 0.05 if (mgmt_pct and mgmt_pct < mgmt_low) else mgmt_pct,
        "flag": "BELOW RANGE" if (mgmt_pct and mgmt_pct < mgmt_low) else
                "ABOVE RANGE" if (mgmt_pct and mgmt_pct > mgmt_high) else
                "IN RANGE" if mgmt_pct else "NOT FOUND",
    })

    return {
        "lines": lines,
        "total_cim_expenses": total_cim_expenses,
        "total_adjusted_expenses": total_adjusted_expenses,
        "adjustments": adjustments,
        "benchmark_source": benchmark_source,
    }


def _get_benchmarks(state: str, nrsf: float, cc_pct: float,
                    comp_db=None) -> tuple[dict, str]:
    """
    Resolve expense benchmarks using the tier hierarchy.

    Returns:
        (benchmarks_dict, source_string) where benchmarks_dict has same
        keys as EXPENSE_BENCHMARKS with (low, high) tuples.

    Tier 3 (comp DB): use p25 as low, p75 as high for each category.
    Tier 4a (regional): state-adjusted benchmarks from config.
    Tier 4b (national): static EXPENSE_BENCHMARKS from config.
    """
    # Map comp DB category names (display) to benchmark keys
    comp_category_map = {cat.display_name: cat.key for cat in EXPENSE_CATEGORIES}

    # Start with static benchmarks as base
    if state:
        static_benchmarks = get_regional_benchmarks(state)
        static_source = f"regional ({state})"
    else:
        static_benchmarks = dict(EXPENSE_BENCHMARKS)
        static_source = "national"

    # Try comp DB
    if comp_db:
        comp_result = comp_db.query_expense_benchmarks(
            state=state, nrsf=nrsf, cc_pct=cc_pct)

        if comp_result and comp_result.get("categories"):
            comp_count = comp_result["comp_count"]
            comp_cats = comp_result["categories"]

            # Overlay comp DB ranges onto static benchmarks
            merged = dict(static_benchmarks)
            categories_from_db = []

            for cat_name, bench_key in comp_category_map.items():
                if cat_name in comp_cats:
                    cat_data = comp_cats[cat_name]
                    merged[bench_key] = (
                        round(cat_data["p25"], 2),
                        round(cat_data["p75"], 2),
                    )
                    categories_from_db.append(bench_key)

            if categories_from_db:
                # Recompute total_opex from merged values
                total_low = sum(merged[k][0] for k in EXPENSE_KEYS)
                total_high = sum(merged[k][1] for k in EXPENSE_KEYS)
                merged["total_opex"] = (round(total_low, 2), round(total_high, 2))

                source = (f"comp_db (N={comp_count}, {state or 'all'}; "
                          f"{len(categories_from_db)} categories) + {static_source}")
                return merged, source

    return static_benchmarks, static_source


def _map_expense_lines(cim_data) -> dict:
    """Map CIM expense line items to benchmark categories."""
    mapped = {}
    keyword_map = EXPENSE_KEYWORD_MAP

    # Use CIM total expenses as fallback
    if cim_data.ttm_total_expenses and not cim_data.expense_lines:
        return mapped

    for line in cim_data.expense_lines:
        label = line.label.lower()
        value = line.t12 or line.t3 or line.cim_yr1

        if value is None:
            continue

        for cat_key, keywords in keyword_map.items():
            if any(kw in label for kw in keywords):
                if cat_key not in mapped:
                    mapped[cat_key] = value
                else:
                    mapped[cat_key] += value  # Accumulate if multiple lines match
                break

    return mapped


def _compute_adjusted_noi(income: dict, expenses: dict, cim_data) -> dict:
    """Compute analyst-adjusted NOI."""
    total_rev = income.get("total_revenue")
    cim_noi = cim_data.ttm_noi
    adjusted_expenses = expenses["total_adjusted_expenses"]
    cim_expenses = expenses["total_cim_expenses"]

    adjusted_noi = None
    if total_rev:
        adjusted_noi = total_rev - adjusted_expenses

    # If no parsed revenue, fall back to CIM NOI minus adjustment delta
    if adjusted_noi is None and cim_noi and cim_expenses > 0:
        expense_delta = adjusted_expenses - cim_expenses
        adjusted_noi = cim_noi - expense_delta

    # Ultimate fallback: use CIM NOI as-is
    if adjusted_noi is None:
        adjusted_noi = cim_noi

    nrsf = cim_data.nrsf or 1

    return {
        "cim_ttm_noi": cim_noi,
        "analyst_adjusted_noi": adjusted_noi,
        "adjustment_delta": (adjusted_expenses - cim_expenses) if cim_expenses else None,
        "adjusted_noi_per_sf": adjusted_noi / nrsf if adjusted_noi else None,
        "narrative": _noi_narrative(cim_noi, adjusted_noi),
    }


def _noi_narrative(cim_noi, adjusted_noi) -> str:
    if cim_noi is None and adjusted_noi is None:
        return "Unable to compute NOI — insufficient financial data in CIM."
    if cim_noi and adjusted_noi and cim_noi != adjusted_noi:
        delta = cim_noi - adjusted_noi
        return (
            f"CIM TTM NOI of ${cim_noi:,.0f} adjusted down by ${delta:,.0f} to "
            f"${adjusted_noi:,.0f} after expense benchmarking. The analyst-adjusted "
            f"figure is used as the base for all return calculations."
        )
    if adjusted_noi:
        return f"Analyst-adjusted TTM NOI: ${adjusted_noi:,.0f}."
    return f"CIM TTM NOI: ${cim_noi:,.0f} (no adjustments applied)."


def _expense_ratio_check(expenses: dict, income: dict, nrsf: float, state: str = "") -> dict:
    """Check overall expense ratios against benchmarks."""
    total_rev = income.get("total_revenue", 0)
    adj_exp = expenses["total_adjusted_expenses"]

    opex_per_nrsf = adj_exp / nrsf if nrsf else None
    opex_ratio = adj_exp / total_rev if total_rev else None

    benchmarks = get_regional_benchmarks(state) if state else EXPENSE_BENCHMARKS
    bench_opex = benchmarks["total_opex"]
    bench_ratio = benchmarks["opex_revenue_ratio"]

    flags = []
    if opex_per_nrsf and opex_per_nrsf < bench_opex[0]:
        flags.append(f"Total OpEx ${opex_per_nrsf:.2f}/SF below minimum benchmark ${bench_opex[0]:.2f}/SF")
    if opex_ratio and opex_ratio < bench_ratio[0]:
        flags.append(f"OpEx/Revenue ratio {opex_ratio:.1%} below minimum benchmark {bench_ratio[0]:.0%}")

    return {
        "total_adjusted_opex": adj_exp,
        "opex_per_nrsf": opex_per_nrsf,
        "opex_revenue_ratio": opex_ratio,
        "benchmark_opex_range": bench_opex,
        "benchmark_ratio_range": bench_ratio,
        "flags": flags,
    }


def _compute_formula_property_tax(noi: float, formula: dict) -> float:
    """
    Compute property tax using income-based formula.

    Formula:
        1. Estimated Value = NOI / cap_rate
        2. Assessed Value  = Estimated Value × assessment_ratio
        3. Tax             = Assessed Value × tax_rate
    """
    estimated_value = noi / formula["cap_rate"]
    assessed_value = estimated_value * formula["assessment_ratio"]
    return assessed_value * formula["tax_rate"]
