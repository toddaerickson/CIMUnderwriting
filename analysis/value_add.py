"""
Section 7 — Operational Improvement Identification.

Identifies value-add opportunities based on the gap between
current operations and benchmark performance.
"""

from analysis.financials import resolve_mgmt_fee_target
from config import GATES, RENOVATION_COST, VALUE_ADD_ASSUMPTIONS
from registry import asset_age

# Dicts, not scalars, and imported by NAME on purpose: `_patched_config`
# mutates these objects IN PLACE for the duration of one run, so a
# module-level binding still sees a ConfigOverride (same reason
# `analysis/market.py` records at its own import). A scalar pulled out of
# one of them at import time would NOT — read them by key at call time.
#
# `EXPENSE_BENCHMARKS` used to be imported here and never used. It is
# gone: an unused import of a settings-editable dict reads like this
# module benchmarks against it, and it does not.


def identify_value_add(cim_data, financial_analysis: dict,
                       rent_analysis: dict = None,
                       mgmt_fee_target_pct=None) -> dict:
    """
    Identify operational improvement opportunities.

    Args:
        cim_data: parsed CIM data
        financial_analysis: output from financials.analyze_financials()
        rent_analysis: output from rent_analysis module (optional)

    Returns:
        - revenue_opportunities: list of revenue enhancement ideas
        - expense_opportunities: list of expense reduction ideas
        - capex_items: physical improvement items
        - estimated_noi_uplift: rough $ estimate of total upside
    """
    revenue_ops = _revenue_opportunities(cim_data, financial_analysis)
    expense_ops = _expense_opportunities(cim_data, financial_analysis,
                                         mgmt_fee_target_pct)
    capex_items = _capex_opportunities(cim_data)

    total_uplift = sum(op.get("est_annual_impact", 0) for op in revenue_ops)
    total_uplift += sum(op.get("est_annual_impact", 0) for op in expense_ops)

    return {
        "revenue_opportunities": revenue_ops,
        "expense_opportunities": expense_ops,
        "capex_items": capex_items,
        "estimated_noi_uplift": total_uplift,
        "narrative": _improvements_narrative(revenue_ops, expense_ops, capex_items, total_uplift),
    }


def _pct_band(pair) -> str:
    """(0.08, 0.10) -> '8-10%'. Not `f"{lo:.0%}-{hi:.0%}"`, which reads
    '8%-10%' — the prose these land in predates the config move and there
    is no reason to change how it reads just because the numbers now come
    from somewhere else."""
    lo, hi = pair
    return f"{lo * 100:.0f}-{hi * 100:.0f}%"


def _revenue_opportunities(cim_data, fin) -> list:
    ops = []
    va = VALUE_ADD_ASSUMPTIONS

    # Occupancy upside
    occ = cim_data.physical_occupancy
    target = va["occupancy_target"]
    if occ and occ < target:
        occ_delta = target - occ
        rev = fin.get("income_summary", {}).get("total_revenue", 0) or 0
        if rev > 0:
            impact = rev * (occ_delta / occ) if occ > 0 else 0
            ops.append({
                "category": "Occupancy Improvement",
                "description": f"Increase physical occupancy from {occ:.1%} to {target:.0%} "
                               f"through improved marketing and ECRI program.",
                "est_annual_impact": impact,
                "timeline": "12-18 months",
                "risk": "Moderate — market dependent",
            })

    # Economic occupancy recovery (mismanagement)
    econ = cim_data.economic_occupancy
    if (occ is not None and econ is not None
            and occ - econ >= GATES["econ_phys_spread_flag"]):
        gpr = fin.get("income_summary", {}).get("gpr", 0) or 0
        if gpr > 0:
            # A haircut on the measured spread — the rest is assumed
            # structural. The sentence quotes the same key it books, so
            # a settings change cannot leave the prose claiming "half"
            # while the model credits something else.
            share = va["spread_recovery_share"]
            recoverable = gpr * (occ - econ) * share
            ops.append({
                "category": "Economic Occupancy Recovery",
                "description": f"Economic occupancy of {econ:.1%} trails physical of "
                               f"{occ:.1%} by {(occ - econ) * 100:.0f} pts. Burn off "
                               f"concessions, tighten collections, and reprice "
                               f"below-street in-place rents (assumes {share:.0%} of "
                               f"the spread is recoverable).",
                "est_annual_impact": recoverable,
                "timeline": "6-12 months",
                "risk": "Low-Moderate — controllable operations, not market dependent",
            })

    # Rate management / ECRI
    if occ and occ >= va["ecri_min_occupancy"]:
        band = _pct_band(va["ecri_increase_range"])
        tenure = va["ecri_tenant_tenure_months"]
        ops.append({
            "category": "Revenue Management / ECRI",
            "description": f"Implement systematic existing-customer rate increases (ECRI) "
                           f"targeting {band} annual increases for tenants > "
                           f"{tenure} months. Confirm street rates are flat-to-rising "
                           f"first — ECRI against falling street rates closes the "
                           f"in-place-to-market gap from above.",
            "est_annual_impact": (fin.get("income_summary", {}).get("egr", 0) or 0)
                                 * va["ecri_egr_uplift"],
            "timeline": "Immediate",
            "risk": "Low — industry standard practice",
        })

    # Other income enhancement
    other_inc = cim_data.other_income or 0
    rev = fin.get("income_summary", {}).get("total_revenue", 0) or 0
    if rev > 0 and other_inc / rev < va["ancillary_min_share"]:
        band = _pct_band(va["ancillary_target_share"])
        ops.append({
            "category": "Ancillary Revenue",
            "description": f"Add/expand tenant insurance program, late fees, admin fees, "
                           f"and merchandise sales to target {band} of revenue.",
            "est_annual_impact": rev * va["ancillary_revenue_uplift"],
            "timeline": "3-6 months",
            "risk": "Low",
        })

    return ops


def _expense_opportunities(cim_data, fin, mgmt_fee_target_pct=None) -> list:
    ops = []
    expense_analysis = fin.get("expense_analysis", {})
    lines = expense_analysis.get("lines", [])

    for line in lines:
        # `benchmark_range` is the $/NRSF band, and its ABSENCE is the
        # discriminator, not an accident: the management-fee line is
        # benchmarked as a share of EGR (`benchmark_range_pct`) and
        # carries no `per_nrsf` either, so `bench_high * nrsf` below would
        # be a percentage times a square footage. Reading the key blindly
        # raised KeyError on any CIM quoting a fee above the band and took
        # the whole value-add section down with it. The fee still gets its
        # own opportunity, computed on the right basis, further down.
        # `cim_data.nrsf` joins the conditions rather than defaulting to
        # 1 (item T Category 4): a saving sized as "CIM dollars minus a
        # $/SF band times one square foot" is the CIM figure back again,
        # dressed as an opportunity. No square footage, no $/SF saving.
        if (line.get("flag") == "ABOVE RANGE" and line.get("cim_value")
                and line.get("benchmark_range") and cim_data.nrsf):
            bench_high = line["benchmark_range"][1]
            nrsf = cim_data.nrsf
            savings = line["cim_value"] - (bench_high * nrsf)
            if savings > 0:
                ops.append({
                    "category": f"Reduce {line['category']}",
                    "description": f"{line['category']} at ${line['per_nrsf']:.2f}/SF is above "
                                   f"benchmark range (${line['benchmark_range'][0]:.2f}-"
                                   f"${line['benchmark_range'][1]:.2f}/SF). "
                                   f"Target top of range or below.",
                    "est_annual_impact": savings,
                    "timeline": "6-12 months",
                    "risk": "Moderate",
                })

    # Third-party management savings
    mgmt_pct = cim_data.mgmt_fee_pct
    mgmt_target = resolve_mgmt_fee_target(mgmt_fee_target_pct)
    if mgmt_pct and mgmt_pct > mgmt_target:
        egr = fin.get("income_summary", {}).get("egr", 0) or 0
        savings = egr * (mgmt_pct - mgmt_target)
        if savings > 0:
            ops.append({
                "category": "Management Fee Reduction",
                "description": f"Renegotiate management fee from {mgmt_pct:.1%} "
                               f"to {mgmt_target:.0%} of EGR.",
                "est_annual_impact": savings,
                "timeline": "At acquisition",
                "risk": "Low — standard market rate",
            })

    return ops


def _cost_range(spec: dict, nrsf: float) -> str:
    """A spec priced `per_sf` needs an NRSF to be worth anything; a spec
    priced as a flat `amount` does not. "TBD" on a missing NRSF is the
    pre-existing behaviour and stays — this line is a diligence prompt,
    not an underwriting input, so fabricating a square footage to fill it
    would be the fallback item T Category 4 exists to delete."""
    if "per_sf" in spec:
        if not nrsf:
            return "TBD"
        lo, hi = spec["per_sf"]
        return f"${nrsf * lo:,.0f} - ${nrsf * hi:,.0f}"
    lo, hi = spec["amount"]
    return f"${lo:,.0f} - ${hi:,.0f}"


def _capex_opportunities(cim_data) -> list:
    """Render `config.RENOVATION_COST` in declaration order. Age-gated
    specs need a vintage AND an age past their trigger; the rest are
    always listed. This was five hand-written branches whose triggers and
    costs were literals — the schedule is data now, so adding an item is
    a config edit and the age ladder is auditable in one place."""
    items = []
    age = asset_age(cim_data.year_built)      # None iff no vintage
    nrsf = cim_data.nrsf or 0

    for spec in RENOVATION_COST.values():
        min_age = spec.get("min_age")
        if min_age is not None:
            if age is None or age <= min_age:
                continue
        priority = spec["priority"]
        high_at = spec.get("high_priority_age")
        if high_at is not None and age is not None and age > high_at:
            priority = "High"
        items.append({
            "item": spec["item"],
            # `.format(age=age)` is a no-op on the specs with no
            # placeholder, so one call covers the table.
            "description": spec["description"].format(age=age),
            "est_cost_range": _cost_range(spec, nrsf),
            "priority": priority,
        })

    return items


def _improvements_narrative(rev_ops, exp_ops, capex_items, total_uplift) -> str:
    parts = []
    if rev_ops:
        parts.append(f"Identified {len(rev_ops)} revenue enhancement opportunit{'y' if len(rev_ops)==1 else 'ies'}.")
    if exp_ops:
        parts.append(f"Identified {len(exp_ops)} expense reduction opportunit{'y' if len(exp_ops)==1 else 'ies'}.")
    if total_uplift > 0:
        parts.append(f"Estimated total annual NOI uplift: ${total_uplift:,.0f}.")
    if capex_items:
        parts.append(f"{len(capex_items)} capital improvement item(s) identified for evaluation.")
    return " ".join(parts) if parts else "No specific improvement opportunities identified."
