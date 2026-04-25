"""
Section 8 — Risk Identification.

Systematically identifies and categorizes risks based on
CIM data and analysis outputs.
"""

from registry import ScenarioType


def identify_risks(cim_data, gate_results: list, financial_analysis: dict,
                   scenario_results: dict) -> dict:
    """
    Identify and categorize investment risks.

    Returns:
        - risks: list of risk items with severity and mitigation
        - why_deal_could_fail: top 3-5 reasons
        - risk_rating: overall risk assessment
    """
    risks = []

    # Market risks
    risks.extend(_market_risks(cim_data))

    # Financial risks
    risks.extend(_financial_risks(cim_data, financial_analysis))

    # Operational risks
    risks.extend(_operational_risks(cim_data))

    # Gate-driven risks
    risks.extend(_gate_risks(gate_results))

    # Valuation risks
    risks.extend(_valuation_risks(cim_data, scenario_results))

    # Sort by severity
    severity_order = {"High": 0, "Medium": 1, "Low": 2}
    risks.sort(key=lambda r: severity_order.get(r.get("severity", "Low"), 3))

    # Build "Why This Deal Could Fail"
    why_fail = [r for r in risks if r["severity"] == "High"][:5]
    if len(why_fail) < 3:
        why_fail.extend([r for r in risks if r["severity"] == "Medium"][:3 - len(why_fail)])

    # Overall risk rating
    high_count = sum(1 for r in risks if r["severity"] == "High")
    if high_count >= 3:
        rating = "High"
    elif high_count >= 1:
        rating = "Moderate-High"
    else:
        rating = "Moderate"

    return {
        "risks": risks,
        "why_deal_could_fail": why_fail,
        "risk_count": {"high": high_count,
                       "medium": sum(1 for r in risks if r["severity"] == "Medium"),
                       "low": sum(1 for r in risks if r["severity"] == "Low")},
        "risk_rating": rating,
    }


def _market_risks(cim_data) -> list:
    risks = []

    pop = cim_data.population_3mi
    if pop and pop < 75_000:
        risks.append({
            "category": "Market",
            "risk": "Limited trade area population",
            "description": f"3-mile population of {pop:,} is below preferred density.",
            "severity": "Medium" if pop >= 50_000 else "High",
            "mitigation": "Verify limited competition and strong market share.",
        })

    if cim_data.new_supply_mentions:
        risks.append({
            "category": "Market",
            "risk": "New supply pipeline",
            "description": "CIM mentions new supply or construction in the trade area.",
            "severity": "High",
            "mitigation": "Verify timing, size, and proximity of new developments. "
                          "Assess impact on occupancy and street rates.",
        })

    return risks


def _financial_risks(cim_data, fin) -> list:
    risks = []

    # NOI step-up risk
    ttm = cim_data.ttm_noi
    yr1 = cim_data.cim_yr1_noi
    if ttm and yr1 and ttm > 0:
        step_up = (yr1 - ttm) / ttm
        if step_up > 0.15:
            risks.append({
                "category": "Financial",
                "risk": "Aggressive CIM pro forma",
                "description": f"CIM Year 1 NOI is {step_up:.1%} above TTM — "
                               f"exceeds 15% step-up threshold.",
                "severity": "High",
                "mitigation": "Use analyst-adjusted NOI (anchored to TTM) for underwriting. "
                              "Verify specific drivers of projected growth.",
            })

    # Expense ratio risk
    ratio_check = fin.get("expense_ratio_check", {})
    flags = ratio_check.get("flags", [])
    for flag in flags:
        risks.append({
            "category": "Financial",
            "risk": "Potentially understated expenses",
            "description": flag,
            "severity": "Medium",
            "mitigation": "Analyst-adjusted expenses used in underwriting.",
        })

    # Adjustments made
    adjustments = fin.get("adjustments", [])
    if len(adjustments) >= 3:
        risks.append({
            "category": "Financial",
            "risk": "Multiple expense adjustments required",
            "description": f"{len(adjustments)} expense lines adjusted — CIM may understate costs.",
            "severity": "Medium",
            "mitigation": "Request actual T-12 P&L from seller for verification.",
        })

    return risks


def _operational_risks(cim_data) -> list:
    risks = []

    occ = cim_data.physical_occupancy
    if occ and occ < 0.85:
        risks.append({
            "category": "Operational",
            "risk": "Below-stabilized occupancy",
            "description": f"Physical occupancy at {occ:.1%} — lease-up risk.",
            "severity": "High",
            "mitigation": "Budget for extended lease-up period. Assess marketing spend required.",
        })
    elif occ and occ > 0.95:
        risks.append({
            "category": "Operational",
            "risk": "Over-occupied — potential rate suppression",
            "description": f"Occupancy at {occ:.1%} may indicate below-market rents.",
            "severity": "Low",
            "mitigation": "Implement ECRI program to push rates. Some churn acceptable.",
        })

    year_built = cim_data.year_built
    if year_built:
        import datetime
        age = datetime.date.today().year - year_built
        if age > 25:
            risks.append({
                "category": "Operational",
                "risk": "Aging physical plant",
                "description": f"Property is {age} years old — potential deferred maintenance.",
                "severity": "Medium",
                "mitigation": "Conduct thorough property condition assessment. "
                              "Budget adequate capital reserves.",
            })

    return risks


def _gate_risks(gate_results: list) -> list:
    risks = []
    for gate in gate_results:
        if gate["result"] == "FAIL":
            risks.append({
                "category": "Screening Gate",
                "risk": f"Failed Gate {gate['gate']}: {gate['name']}",
                "description": f"Actual: {gate['actual']} vs threshold: {gate['threshold']}. "
                               f"{gate.get('note', '')}",
                "severity": "High",
                "mitigation": "This gate failure requires specific justification to proceed.",
            })
        elif gate["result"] == "TBD":
            risks.append({
                "category": "Data Gap",
                "risk": f"Unverified Gate {gate['gate']}: {gate['name']}",
                "description": gate.get("note", "Data not available for verification."),
                "severity": "Medium",
                "mitigation": "Manual verification required before final recommendation.",
            })
    return risks


def _valuation_risks(cim_data, scenario_results: dict) -> list:
    risks = []

    if not scenario_results:
        return risks

    base = scenario_results.get(ScenarioType.BASE, {})
    bear = scenario_results.get(ScenarioType.BEAR, {})

    # Bear case IRR risk
    bear_irr = bear.get("irr")
    if bear_irr is not None and bear_irr < 0.05:
        risks.append({
            "category": "Valuation",
            "risk": "Weak bear-case returns",
            "description": f"Bear case IRR of {bear_irr:.1%} — limited downside protection.",
            "severity": "High",
            "mitigation": "Negotiate purchase price down to improve bear-case floor.",
        })

    # Narrow IRR spread
    base_irr = base.get("irr")
    if base_irr and bear_irr:
        spread = base_irr - bear_irr
        if spread > 0.08:
            risks.append({
                "category": "Valuation",
                "risk": "Wide scenario spread",
                "description": f"Base-to-bear IRR spread of {spread:.1%} — high uncertainty.",
                "severity": "Medium",
                "mitigation": "Focus diligence on assumptions driving bear case.",
            })

    # Price per SF vs replacement
    if cim_data.price_per_sf and cim_data.nrsf:
        from analysis.filters import _estimate_replacement_cost
        repl = _estimate_replacement_cost(cim_data)
        if repl:
            repl_per_sf = repl / cim_data.nrsf
            if cim_data.price_per_sf > repl_per_sf:
                premium = (cim_data.price_per_sf - repl_per_sf) / repl_per_sf
                risks.append({
                    "category": "Valuation",
                    "risk": "Premium to replacement cost",
                    "description": f"Asking ${cim_data.price_per_sf:.0f}/SF vs "
                                   f"replacement ${repl_per_sf:.0f}/SF ({premium:.1%} premium).",
                    "severity": "High" if premium > 0.15 else "Medium",
                    "mitigation": "Negotiate price below replacement cost or "
                                  "demonstrate irreplaceable location/market value.",
                })

    return risks
