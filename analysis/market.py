"""
Section 2 — Market & Location Analysis.

Evaluates demographics, MSA strength, and supply/demand indicators
from CIM-extracted data.
"""


def analyze_market(cim_data) -> dict:
    """
    Produce market analysis section data.

    Returns dict with:
        - demographics: population / HHI summary
        - msa_info: MSA identification
        - supply_assessment: new supply risk narrative
        - demand_drivers: list of positives / negatives
        - overall_rating: "Strong" | "Moderate" | "Weak" | "TBD"
    """
    result = {
        "demographics": _assess_demographics(cim_data),
        "msa_info": _assess_msa(cim_data),
        "supply_assessment": _assess_supply(cim_data),
        "demand_drivers": _assess_demand(cim_data),
        "overall_rating": "TBD",
    }

    # Simple scoring
    score = 0
    demos = result["demographics"]
    if demos.get("pop_3mi_adequate"):
        score += 2
    if demos.get("hhi_adequate"):
        score += 1
    if result["msa_info"].get("is_top_50"):
        score += 2
    if not result["supply_assessment"].get("risk_flag"):
        score += 1

    if score >= 5:
        result["overall_rating"] = "Strong"
    elif score >= 3:
        result["overall_rating"] = "Moderate"
    elif score >= 1:
        result["overall_rating"] = "Weak"

    return result


def _assess_demographics(cim_data) -> dict:
    """Evaluate population and income metrics."""
    pop_1 = cim_data.population_1mi
    pop_3 = cim_data.population_3mi
    pop_5 = cim_data.population_5mi
    hhi = cim_data.median_hhi_3mi

    return {
        "population_1mi": pop_1,
        "population_3mi": pop_3,
        "population_5mi": pop_5,
        "median_hhi_3mi": hhi,
        "pop_3mi_adequate": pop_3 >= 50_000 if pop_3 else None,
        "hhi_adequate": hhi >= 50_000 if hhi else None,
        "pop_narrative": _pop_narrative(pop_3),
        "hhi_narrative": _hhi_narrative(hhi),
    }


def _pop_narrative(pop_3mi) -> str:
    if pop_3mi is None:
        return "3-mile population not available — requires manual verification."
    if pop_3mi >= 100_000:
        return f"Dense trade area with {pop_3mi:,} people within 3 miles — strong demand driver."
    if pop_3mi >= 50_000:
        return f"Adequate density with {pop_3mi:,} people within 3 miles — meets minimum threshold."
    return f"Thin trade area with only {pop_3mi:,} people within 3 miles — below 50,000 minimum."


def _hhi_narrative(hhi) -> str:
    if hhi is None:
        return "Median household income not available."
    if hhi >= 75_000:
        return f"Affluent trade area (${hhi:,.0f} median HHI) — supports premium pricing."
    if hhi >= 50_000:
        return f"Middle-income trade area (${hhi:,.0f} median HHI) — adequate purchasing power."
    return f"Lower-income trade area (${hhi:,.0f} median HHI) — may limit rent growth."


def _assess_msa(cim_data) -> dict:
    from config import TOP_50_MSAS

    msa = cim_data.msa or cim_data.city or ""
    is_top_50 = any(m.lower() in msa.lower() for m in TOP_50_MSAS) if msa else False

    return {
        "msa_name": msa or "Not identified",
        "is_top_50": is_top_50,
        "narrative": f"{msa} is a top-50 MSA — institutional-quality market." if is_top_50 else
                     f"{msa} — not identified as top-50 MSA. Verify market classification." if msa else
                     "MSA not identified in CIM — requires manual classification.",
    }


def _assess_supply(cim_data) -> dict:
    mentions = cim_data.new_supply_mentions
    return {
        "raw_mentions": mentions,
        "risk_flag": bool(mentions),
        "narrative": f"New supply references found in CIM: {mentions[:300]}" if mentions else
                     "No explicit new supply mentions found in CIM — verify independently.",
    }


def _assess_demand(cim_data) -> dict:
    positives = []
    negatives = []

    occ = cim_data.physical_occupancy
    if occ:
        if occ >= 0.90:
            positives.append(f"Strong occupancy at {occ:.1%} — demand exceeds supply.")
        elif occ >= 0.85:
            positives.append(f"Healthy occupancy at {occ:.1%} — stable demand.")
        else:
            negatives.append(f"Occupancy at {occ:.1%} — below stabilized threshold.")

    pop = cim_data.population_3mi
    if pop and pop >= 75_000:
        positives.append(f"Dense trade area ({pop:,} within 3 mi).")
    elif pop and pop < 50_000:
        negatives.append(f"Thin trade area ({pop:,} within 3 mi).")

    hhi = cim_data.median_hhi_3mi
    if hhi and hhi >= 65_000:
        positives.append(f"Above-average household income (${hhi:,.0f}).")
    elif hhi and hhi < 45_000:
        negatives.append(f"Below-average household income (${hhi:,.0f}).")

    return {
        "positives": positives,
        "negatives": negatives,
    }
