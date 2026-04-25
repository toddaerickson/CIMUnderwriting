"""
Section 3 — Property Description & Replacement Cost Analysis.

Compiles property physical characteristics and computes estimated
replacement cost for comparison against asking price.
"""

from config import REPLACEMENT_COST, FACILITY_TYPES


def analyze_physical(cim_data) -> dict:
    """
    Produce property description and replacement cost analysis.

    Returns dict with:
        - property_profile: key physical attributes
        - replacement_cost: detailed cost build-up
        - price_vs_replacement: comparison metrics
    """
    profile = _build_profile(cim_data)
    repl = _compute_replacement_cost(cim_data)
    comparison = _compare_to_asking(cim_data, repl)

    return {
        "property_profile": profile,
        "replacement_cost": repl,
        "price_vs_replacement": comparison,
    }


def _build_profile(cim_data) -> dict:
    """Compile property physical characteristics."""
    return {
        "property_name": cim_data.property_name or "TBD",
        "address": cim_data.address or "TBD",
        "city_state": f"{cim_data.city or 'TBD'}, {cim_data.state or 'TBD'}",
        "year_built": cim_data.year_built,
        "year_expanded": cim_data.year_expanded,
        "acreage": cim_data.acreage,
        "nrsf": cim_data.nrsf,
        "total_units": cim_data.total_units,
        "cc_pct": cim_data.cc_pct,
        "cc_sf": cim_data.cc_sf,
        "non_cc_sf": cim_data.non_cc_sf,
        "physical_occupancy": cim_data.physical_occupancy,
        "economic_occupancy": cim_data.economic_occupancy,
        "age_narrative": _age_narrative(cim_data.year_built),
        "condition_note": "TBD — requires site visit for physical condition assessment.",
    }


def _age_narrative(year_built) -> str:
    if year_built is None:
        return "Year built not available."
    import datetime
    age = datetime.date.today().year - year_built
    if age <= 5:
        return f"Built {year_built} ({age} years old) — modern construction, minimal deferred maintenance expected."
    if age <= 15:
        return f"Built {year_built} ({age} years old) — mid-life asset, normal wear expected."
    if age <= 30:
        return f"Built {year_built} ({age} years old) — aging asset, inspect for deferred maintenance."
    return f"Built {year_built} ({age} years old) — significant age, budget for capital improvements."


def _compute_replacement_cost(cim_data) -> dict:
    """Estimate replacement cost from facility-type SF breakdowns.

    If facility-type fields (ss_driveup_sf, brv_enclosed_sf, etc.) are
    populated, uses those directly.  Otherwise falls back to the legacy
    cc_pct / non_cc_sf split (self-storage only).
    """
    nrsf = cim_data.nrsf
    if not nrsf:
        return {
            "estimable": False,
            "note": "NRSF not available — cannot estimate replacement cost.",
        }

    soft_pct = sum(REPLACEMENT_COST["soft_cost_pct"]) / 2
    dev_pct = sum(REPLACEMENT_COST["dev_profit_pct"]) / 2

    # ── Determine SF per facility type ──────────────────────────
    # Map: (hard_key, site_key, display_name) → SF
    type_sf_map = {
        "ss_driveup":   cim_data.ss_driveup_sf,
        "ss_enclosed":  cim_data.ss_enclosed_sf,
        "brv_enclosed": cim_data.brv_enclosed_sf,
        "brv_covered":  cim_data.brv_covered_sf,
        "brv_open":     cim_data.brv_open_sf,
    }
    has_typed_sf = any(v is not None and v > 0 for v in type_sf_map.values())

    if not has_typed_sf:
        # Legacy fallback: derive from cc_pct
        cc_pct = cim_data.cc_pct or 0.0
        type_sf_map = {
            "ss_driveup":   nrsf * (1.0 - cc_pct),
            "ss_enclosed":  nrsf * cc_pct,
            "brv_enclosed": 0,
            "brv_covered":  0,
            "brv_open":     0,
        }

    # ── Build cost for each facility type ───────────────────────
    type_details = []
    total_hard = 0.0
    total_site = 0.0
    total_sf = 0.0

    for hard_key, site_key, display_name in FACILITY_TYPES:
        short_key = hard_key.replace("_per_sf", "")
        sf = type_sf_map.get(short_key, 0) or 0
        if sf <= 0:
            continue

        hard_rate = sum(REPLACEMENT_COST[hard_key]) / 2
        site_rate = sum(REPLACEMENT_COST[site_key]) / 2
        hard_cost = sf * hard_rate
        site_cost = sf * site_rate

        total_hard += hard_cost
        total_site += site_cost
        total_sf += sf

        type_details.append({
            "type": display_name,
            "sf": sf,
            "hard_rate": hard_rate,
            "hard_cost": hard_cost,
            "site_rate": site_rate,
            "site_cost": site_cost,
        })

    subtotal = total_hard + total_site
    soft_costs = subtotal * soft_pct
    tdc_before_profit = subtotal + soft_costs
    dev_profit = tdc_before_profit * dev_pct
    total_replacement = tdc_before_profit + dev_profit

    # Legacy fields for backward compatibility
    cc_pct = cim_data.cc_pct or 0.0
    cc_sf = nrsf * cc_pct
    non_cc_sf = nrsf * (1.0 - cc_pct)

    return {
        "estimable": True,
        "nrsf": nrsf,
        "cc_sf": cc_sf,
        "non_cc_sf": non_cc_sf,
        "non_cc_rate": sum(REPLACEMENT_COST["non_cc_per_sf"]) / 2,
        "cc_rate": sum(REPLACEMENT_COST["cc_per_sf"]) / 2,
        "non_cc_cost": non_cc_sf * sum(REPLACEMENT_COST["non_cc_per_sf"]) / 2,
        "cc_cost": cc_sf * sum(REPLACEMENT_COST["cc_per_sf"]) / 2,
        "hard_cost": total_hard,
        "site_work_rate": total_site / total_sf if total_sf else 0,
        "site_work": total_site,
        "subtotal": subtotal,
        "soft_cost_pct": soft_pct,
        "soft_costs": soft_costs,
        "tdc_before_profit": tdc_before_profit,
        "dev_profit_pct": dev_pct,
        "dev_profit": dev_profit,
        "total_replacement": total_replacement,
        "replacement_per_sf": total_replacement / nrsf if nrsf else None,
        "facility_type_details": type_details,
    }


def _compare_to_asking(cim_data, replacement: dict) -> dict:
    """Compare asking price to estimated replacement cost."""
    asking = cim_data.asking_price
    nrsf = cim_data.nrsf

    if not asking or not replacement.get("estimable"):
        return {
            "comparable": False,
            "note": "Cannot compare — asking price or replacement cost not available.",
        }

    total_repl = replacement["total_replacement"]
    discount = (total_repl - asking) / total_repl

    return {
        "comparable": True,
        "asking_price": asking,
        "asking_per_sf": asking / nrsf if nrsf else None,
        "replacement_cost": total_repl,
        "replacement_per_sf": total_repl / nrsf if nrsf else None,
        "discount_to_replacement": discount,
        "passes_gate": asking <= total_repl,
        "narrative": (
            f"Asking price of ${asking:,.0f} (${asking/nrsf:.0f}/SF) represents a "
            f"{abs(discount):.1%} {'discount to' if discount > 0 else 'premium over'} "
            f"estimated replacement cost of ${total_repl:,.0f} (${total_repl/nrsf:.0f}/SF)."
        ) if nrsf else "Comparison available but NRSF missing for per-SF metrics.",
    }
