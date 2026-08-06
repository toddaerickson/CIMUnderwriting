"""
Section 3 — Property Description & Replacement Cost Analysis.

Compiles property physical characteristics and computes estimated
replacement cost for comparison against asking price.
"""

from config import REPLACEMENT_COST, FACILITY_TYPES
from registry import age_band, asset_age
from analysis.fills import CC_PCT_ABSENT, Fill, UNIT_PCT, to_dicts


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
        # Item T Category 4. `analysis.filters._estimate_replacement_cost`
        # is a SECOND copy of the same build-up feeding gate 3, and it
        # deliberately records nothing: two copies logging the same
        # substitution would double-count it. This one is the log's
        # source, and a test pins that the two copies still agree.
        "fills": repl.get("fills", []),
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


#: Prose per age band. The BANDS themselves live in registry.AGE_BANDS —
#: this narrative used to carry its own copy of the 5/15/30 ladder, and
#: that ladder is now load-bearing (the exit-cap table keys off it), so a
#: second copy here would let the memo describe a "mid-life asset" while
#: the model priced it in the aging band.
_AGE_NARRATIVE = {
    "new":   "modern construction, minimal deferred maintenance expected.",
    "mid":   "mid-life asset, normal wear expected.",
    "aging": "aging asset, inspect for deferred maintenance.",
    "old":   "significant age, budget for capital improvements.",
}


def _age_narrative(year_built) -> str:
    band = age_band(year_built)
    if band is None:
        return "Year built not available."
    age = asset_age(year_built)
    return (f"Built {year_built} ({age} years old) — "
            f"{_AGE_NARRATIVE[band]}")


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

    fills = []

    if not has_typed_sf:
        # Legacy fallback: derive from cc_pct
        cc_pct = cim_data.cc_pct or 0.0
        # With no typed square footage AND no climate-controlled share,
        # the whole building is costed as drive-up at $55-85/SF when
        # climate-controlled space runs $90-130/SF. That understates
        # replacement cost, and replacement cost is what the asking price
        # is screened against — so the fallback pushes gate 3 toward FAIL
        # and an analyst reading the memo cannot see that it was a guess.
        if cim_data.cc_pct is None:
            fills.append(Fill(
                field="cc_pct", value_used=0.0,
                source_key=CC_PCT_ABSENT, unit=UNIT_PCT,
                label=("No facility-type SF either, so the whole building is "
                       "costed as drive-up. CC space costs more to build, so "
                       "replacement cost — what the asking price is screened "
                       "against — is understated if any exists."),
                detail={"nrsf": nrsf}))
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
        # DICTS, for the same reason `analysis/financials.py` gives: this
        # dict is persisted through `json_safe`, which stringifies a
        # dataclass rather than refusing it.
        "fills": to_dicts(fills),
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
