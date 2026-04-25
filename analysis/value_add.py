"""
Section 7 — Operational Improvement Identification.

Identifies value-add opportunities based on the gap between
current operations and benchmark performance.
"""

from config import EXPENSE_BENCHMARKS


def identify_value_add(cim_data, financial_analysis: dict, rent_analysis: dict = None) -> dict:
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
    expense_ops = _expense_opportunities(cim_data, financial_analysis)
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


def _revenue_opportunities(cim_data, fin) -> list:
    ops = []
    nrsf = cim_data.nrsf or 0

    # Occupancy upside
    occ = cim_data.physical_occupancy
    if occ and occ < 0.93:
        target = 0.93
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

    # Rate management / ECRI
    if occ and occ >= 0.88:
        ops.append({
            "category": "Revenue Management / ECRI",
            "description": "Implement systematic existing-customer rate increases (ECRI) "
                           "targeting 8-10% annual increases for tenants > 6 months.",
            "est_annual_impact": (fin.get("income_summary", {}).get("egr", 0) or 0) * 0.03,
            "timeline": "Immediate",
            "risk": "Low — industry standard practice",
        })

    # Other income enhancement
    other_inc = cim_data.other_income or 0
    rev = fin.get("income_summary", {}).get("total_revenue", 0) or 0
    if rev > 0 and other_inc / rev < 0.05:
        ops.append({
            "category": "Ancillary Revenue",
            "description": "Add/expand tenant insurance program, late fees, admin fees, "
                           "and merchandise sales to target 5-8% of revenue.",
            "est_annual_impact": rev * 0.03,
            "timeline": "3-6 months",
            "risk": "Low",
        })

    return ops


def _expense_opportunities(cim_data, fin) -> list:
    ops = []
    expense_analysis = fin.get("expense_analysis", {})
    lines = expense_analysis.get("lines", [])

    for line in lines:
        if line.get("flag") == "ABOVE RANGE" and line.get("cim_value"):
            bench_high = line["benchmark_range"][1]
            nrsf = cim_data.nrsf or 1
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
    if mgmt_pct and mgmt_pct > 0.05:
        egr = fin.get("income_summary", {}).get("egr", 0) or 0
        savings = egr * (mgmt_pct - 0.05)
        if savings > 0:
            ops.append({
                "category": "Management Fee Reduction",
                "description": f"Renegotiate management fee from {mgmt_pct:.1%} to 5% of EGR.",
                "est_annual_impact": savings,
                "timeline": "At acquisition",
                "risk": "Low — standard market rate",
            })

    return ops


def _capex_opportunities(cim_data) -> list:
    items = []
    year_built = cim_data.year_built
    nrsf = cim_data.nrsf or 0

    if year_built:
        import datetime
        age = datetime.date.today().year - year_built
        if age > 20:
            items.append({
                "item": "Roof Replacement / Repair",
                "description": f"Property is {age} years old — inspect roof condition.",
                "est_cost_range": f"${nrsf * 1.50:,.0f} - ${nrsf * 3.00:,.0f}" if nrsf else "TBD",
                "priority": "High" if age > 30 else "Medium",
            })
        if age > 15:
            items.append({
                "item": "LED Lighting Upgrade",
                "description": "Convert to LED lighting for energy savings.",
                "est_cost_range": f"${nrsf * 0.30:,.0f} - ${nrsf * 0.75:,.0f}" if nrsf else "TBD",
                "priority": "Medium",
            })
        if age > 10:
            items.append({
                "item": "Security System Upgrade",
                "description": "Upgrade cameras, access control, and gate systems.",
                "est_cost_range": f"${15_000:,.0f} - ${50_000:,.0f}",
                "priority": "Medium",
            })

    items.append({
        "item": "Signage & Curb Appeal",
        "description": "Evaluate signage visibility and property aesthetics.",
        "est_cost_range": "$5,000 - $25,000",
        "priority": "Low",
    })

    items.append({
        "item": "Website & Digital Presence",
        "description": "Optimize online listings, website, and SEO.",
        "est_cost_range": "$2,000 - $10,000",
        "priority": "Medium",
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
