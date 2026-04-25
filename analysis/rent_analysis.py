"""
Section 5 — Unit Mix & Rent Analysis.

Analyzes the property's unit mix, average rents by type,
and gap to market / comp rents where available.
"""


def analyze_rents(cim_data, comp_db=None) -> dict:
    """
    Produce unit mix and rent analysis.

    Returns:
        - unit_mix_summary: breakdown by unit type
        - weighted_avg_rent: blended $/SF/mo
        - rent_gap_analysis: comparison to comps if available
        - revenue_concentration: largest unit type contribution
    """
    unit_mix = cim_data.unit_mix
    nrsf = cim_data.nrsf

    if not unit_mix:
        return {
            "unit_mix_summary": [],
            "data_available": False,
            "weighted_avg_rent": None,
            "rent_per_sf": _estimate_rent_per_sf(cim_data),
            "rent_gap_analysis": _placeholder_rent_gap(),
            "revenue_concentration": None,
            "narrative": "Unit mix not extracted from CIM — manual review required.",
        }

    # Analyze each unit type
    summary = []
    total_sf = 0
    total_monthly_rev = 0
    max_rev_type = None
    max_rev = 0

    for unit in unit_mix:
        sf = unit.sf or (unit.width * unit.depth if unit.width and unit.depth else None)
        if sf and unit.count:
            type_sf = sf * unit.count
            type_rev = unit.rate * unit.count if unit.rate else 0
            rate_per_sf = unit.rate / sf if (unit.rate and sf) else None

            total_sf += type_sf
            total_monthly_rev += type_rev

            if type_rev > max_rev:
                max_rev = type_rev
                max_rev_type = unit.size_label

            summary.append({
                "size_label": unit.size_label,
                "unit_sf": sf,
                "count": unit.count,
                "total_sf": type_sf,
                "monthly_rate": unit.rate,
                "rate_per_sf": rate_per_sf,
                "annual_revenue": type_rev * 12,
                "pct_of_total_sf": None,  # computed below
                "climate_controlled": unit.climate_controlled,
            })

    # Compute percentages
    for s in summary:
        if total_sf > 0:
            s["pct_of_total_sf"] = s["total_sf"] / total_sf

    # Weighted average rent per SF
    wavg_rent_per_sf = total_monthly_rev / total_sf if total_sf else None

    return {
        "unit_mix_summary": summary,
        "data_available": True,
        "total_sf_from_mix": total_sf,
        "total_monthly_revenue": total_monthly_rev,
        "weighted_avg_rent_per_sf_mo": wavg_rent_per_sf,
        "weighted_avg_rent_per_sf_yr": wavg_rent_per_sf * 12 if wavg_rent_per_sf else None,
        "rent_gap_analysis": _analyze_rent_gap(cim_data, wavg_rent_per_sf, comp_db),
        "revenue_concentration": {
            "largest_type": max_rev_type,
            "pct_of_revenue": max_rev / total_monthly_rev if total_monthly_rev else None,
        },
        "narrative": _rent_narrative(summary, wavg_rent_per_sf),
    }


def _estimate_rent_per_sf(cim_data) -> dict:
    """Estimate average rent per SF from total revenue and NRSF."""
    if cim_data.ttm_total_revenue and cim_data.nrsf:
        annual_per_sf = cim_data.ttm_total_revenue / cim_data.nrsf
        monthly_per_sf = annual_per_sf / 12
        return {
            "estimated": True,
            "annual_per_sf": annual_per_sf,
            "monthly_per_sf": monthly_per_sf,
        }
    return {"estimated": False}


def _analyze_rent_gap(cim_data, wavg_rent_per_sf, comp_db=None) -> dict:
    """Compare property rents to comp/market rents if available.

    Source hierarchy:
      1. market_rent_psf (override or rent survey)
      2. CIM comp_data
      3. Comp database historical rents
      4. No data — manual review required
    """
    # Tier 1/2: market_rent_psf (from override or rent survey)
    if cim_data.market_rent_psf and wavg_rent_per_sf:
        market = cim_data.market_rent_psf
        gap = (wavg_rent_per_sf - market) / market if market > 0 else None
        return {
            "comps_available": True,
            "comp_rents_extracted": True,
            "avg_comp_rent_per_sf": market,
            "subject_rent_per_sf": wavg_rent_per_sf,
            "gap_pct": gap,
            "source": "market_rent_psf (override or rent survey)",
            "narrative": (
                f"Subject average rent of ${wavg_rent_per_sf:.2f}/SF/mo is "
                f"{'above' if gap and gap > 0 else 'below'} the market average of "
                f"${market:.2f}/SF/mo by {abs(gap):.1%}."
            ) if gap is not None else "Rent gap calculation not possible.",
        }

    # Tier 1: CIM comp data
    comps = cim_data.comp_data
    if comps:
        comp_rents = []
        for comp in comps:
            if isinstance(comp, dict) and comp.get("rate_per_sf"):
                comp_rents.append(comp["rate_per_sf"])

        if comp_rents:
            avg_comp_rent = sum(comp_rents) / len(comp_rents)
            gap = None
            if wavg_rent_per_sf:
                gap = (wavg_rent_per_sf - avg_comp_rent) / avg_comp_rent

            return {
                "comps_available": True,
                "comp_rents_extracted": True,
                "avg_comp_rent_per_sf": avg_comp_rent,
                "subject_rent_per_sf": wavg_rent_per_sf,
                "gap_pct": gap,
                "source": "CIM comp data",
                "narrative": (
                    f"Subject average rent of ${wavg_rent_per_sf:.2f}/SF/mo is "
                    f"{'above' if gap and gap > 0 else 'below'} the comp average of "
                    f"${avg_comp_rent:.2f}/SF/mo by {abs(gap):.1%}."
                ) if gap else "Rent gap calculation not possible.",
            }

    # Tier 3: Comp database historical rents
    if comp_db and wavg_rent_per_sf:
        state = (cim_data.state or "").upper().strip()
        db_result = comp_db.query_rent_comps(state=state or None)
        if db_result:
            db_avg = db_result["avg_rent_per_sf_mo"]
            gap = (wavg_rent_per_sf - db_avg) / db_avg if db_avg > 0 else None
            return {
                "comps_available": True,
                "comp_rents_extracted": True,
                "avg_comp_rent_per_sf": db_avg,
                "subject_rent_per_sf": wavg_rent_per_sf,
                "gap_pct": gap,
                "source": f"comp_db (N={db_result['comp_count']}, {state or 'all'})",
                "narrative": (
                    f"Subject average rent of ${wavg_rent_per_sf:.2f}/SF/mo is "
                    f"{'above' if gap and gap > 0 else 'below'} the historical comp "
                    f"average of ${db_avg:.2f}/SF/mo by {abs(gap):.1%} "
                    f"(N={db_result['comp_count']})."
                ) if gap is not None else "Rent gap calculation not possible.",
            }

    return {
        "comps_available": False,
        "narrative": "Comp data not extracted — manual rent comparison required.",
    }


def _placeholder_rent_gap() -> dict:
    return {
        "comps_available": False,
        "narrative": "Rent gap analysis requires unit mix and comp data — not available.",
    }


def _rent_narrative(summary: list, wavg: float | None) -> str:
    if not summary:
        return "No unit mix data available for narrative."

    n_types = len(summary)
    total_units = sum(s["count"] for s in summary)
    cc_types = sum(1 for s in summary if s.get("climate_controlled"))

    parts = [f"Property offers {n_types} unit types totaling {total_units} units."]
    if wavg:
        parts.append(f"Weighted average rent of ${wavg:.2f}/SF/month.")
    if cc_types:
        parts.append(f"{cc_types} unit type(s) are climate-controlled.")

    return " ".join(parts)
