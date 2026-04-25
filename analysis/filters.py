"""
Section 1 — Go / No-Go Gate Evaluation.

Evaluates 7 binary gates against hard-coded thresholds.
Each gate returns PASS, FAIL, or TBD (data missing).
"""

from config import GATES, TOP_50_MSAS
from registry import ScenarioType


def evaluate_gates(cim_data, scenario_results=None, va_results=None,
                   source_log=None) -> list[dict]:
    """
    Run all 7 screening gates.

    Args:
        cim_data: CIMData instance from parser
        scenario_results: dict with base-case IRR (may be None on first pass)
        va_results: dict with value-add scenario results (may be None)
        source_log: dict mapping field names to tier/source info (from enrichment)

    Returns:
        list of gate result dicts
    """
    source_log = source_log or {}
    gates = []

    # Gate 1: Population density (3-mile ≥ 50,000)
    pop = cim_data.population_3mi
    pop_source = source_log.get("population_3mi", {}).get("source", "")
    gates.append({
        "gate": 1,
        "name": "Population (3-mi ≥ 50K)",
        "threshold": f"≥ {GATES['population_3mi']:,}",
        "actual": f"{pop:,}" if pop else "N/A",
        "result": _eval(pop, GATES["population_3mi"], ">=") if pop else "TBD",
        "note": "" if pop else "Population data not found in CIM — verify manually",
        "source": pop_source if pop_source else None,
    })

    # Gate 2: No lease-up risk (occupancy ≥ 85%)
    occ = cim_data.physical_occupancy
    gates.append({
        "gate": 2,
        "name": "Occupancy ≥ 85%",
        "threshold": f"≥ {GATES['min_occupancy']:.0%}",
        "actual": f"{occ:.1%}" if occ else "N/A",
        "result": _eval(occ, GATES["min_occupancy"], ">=") if occ else "TBD",
        "note": "" if (occ and occ >= GATES["min_occupancy"]) else
                "Lease-up risk — occupancy below threshold" if occ else
                "Occupancy not found in CIM",
    })

    # Gate 3: Price ≤ Replacement cost
    replacement = _estimate_replacement_cost(cim_data)
    asking = cim_data.asking_price
    if asking and replacement:
        passes = asking <= replacement
        gates.append({
            "gate": 3,
            "name": "Price ≤ Replacement Cost",
            "threshold": f"≤ ${replacement:,.0f}",
            "actual": f"${asking:,.0f}",
            "result": "PASS" if passes else "FAIL",
            "note": f"Asking ${asking/cim_data.nrsf:.0f}/SF vs replacement ${replacement/cim_data.nrsf:.0f}/SF"
                    if cim_data.nrsf else "",
        })
    else:
        gates.append({
            "gate": 3,
            "name": "Price ≤ Replacement Cost",
            "threshold": "≤ replacement cost",
            "actual": "N/A",
            "result": "TBD",
            "note": "Insufficient data to estimate replacement cost",
        })

    # Gate 4: 5-Year unlevered IRR ≥ 10%
    # Use VA IRR if value-add model was run, otherwise static
    base_irr = None
    va_irr = None
    irr_source = "static"
    if va_results and ScenarioType.BASE in va_results:
        va_irr = va_results[ScenarioType.BASE].get("irr")
    if scenario_results and ScenarioType.BASE in scenario_results:
        base_irr = scenario_results[ScenarioType.BASE].get("irr")

    # Prefer VA IRR for the gate check when available
    gate_irr = va_irr if va_irr is not None else base_irr
    if va_irr is not None:
        irr_source = "value-add"

    irr_display = f"{gate_irr:.1%}" if gate_irr else "N/A"
    if va_irr is not None and base_irr is not None:
        irr_display = f"{va_irr:.1%} VA ({base_irr:.1%} static)"

    gates.append({
        "gate": 4,
        "name": "5-Yr Unlevered IRR ≥ 10%",
        "threshold": f"≥ {GATES['min_irr_5yr']:.0%}",
        "actual": irr_display,
        "result": _eval(gate_irr, GATES["min_irr_5yr"], ">=") if gate_irr else "TBD",
        "note": f"Using {irr_source} model" if (gate_irr and gate_irr >= GATES["min_irr_5yr"]) else
                f"Below target IRR ({irr_source} model)" if gate_irr else
                "Pending scenario analysis",
    })

    # Gate 5: No oversupply flag
    supply = cim_data.new_supply_mentions
    gates.append({
        "gate": 5,
        "name": "No Oversupply Flag",
        "threshold": "No material new supply",
        "actual": "See notes" if supply else "N/A",
        "result": "TBD",
        "note": f"Supply mentions: {supply[:200]}" if supply else
                "No supply data found — verify pipeline manually",
    })

    # Gate 6: NOI step-up ≤ 15% (CIM Yr1 vs TTM)
    ttm_noi = cim_data.ttm_noi
    yr1_noi = cim_data.cim_yr1_noi
    if ttm_noi and yr1_noi and ttm_noi > 0:
        step_up = (yr1_noi - ttm_noi) / ttm_noi
        passes = step_up <= GATES["max_noi_step_up"]
        gates.append({
            "gate": 6,
            "name": "NOI Step-Up ≤ 15%",
            "threshold": f"≤ {GATES['max_noi_step_up']:.0%}",
            "actual": f"{step_up:.1%}",
            "result": "PASS" if passes else "FAIL",
            "note": "" if passes else
                    f"CIM Yr1 NOI is {step_up:.1%} above TTM — aggressive pro forma",
        })
    else:
        gates.append({
            "gate": 6,
            "name": "NOI Step-Up ≤ 15%",
            "threshold": f"≤ {GATES['max_noi_step_up']:.0%}",
            "actual": "N/A",
            "result": "TBD",
            "note": "TTM and/or CIM Yr1 NOI not extracted",
        })

    # Gate 7: Major city / Top-50 MSA
    msa = cim_data.msa or cim_data.city or ""
    msa_match = any(m.lower() in msa.lower() for m in TOP_50_MSAS) if msa else False
    gates.append({
        "gate": 7,
        "name": "Major City / Top-50 MSA",
        "threshold": "Top-50 MSA",
        "actual": msa if msa else "N/A",
        "result": "PASS" if msa_match else "TBD",
        "note": "" if msa_match else "MSA not identified or not in top-50 — verify manually",
    })

    return gates


def summarize_gates(gates: list[dict]) -> dict:
    """Summarize gate results."""
    passed = sum(1 for g in gates if g["result"] == "PASS")
    failed = sum(1 for g in gates if g["result"] == "FAIL")
    tbd = sum(1 for g in gates if g["result"] == "TBD")

    if failed > 0:
        recommendation = "DECLINE"
    elif tbd > 0:
        recommendation = "PURSUE CONTINGENT ON"
    else:
        recommendation = "PURSUE"

    return {
        "passed": passed,
        "failed": failed,
        "tbd": tbd,
        "total": len(gates),
        "recommendation": recommendation,
        "failed_gates": [g for g in gates if g["result"] == "FAIL"],
        "tbd_gates": [g for g in gates if g["result"] == "TBD"],
    }


def _eval(value, threshold, op: str) -> str:
    """Evaluate a gate condition."""
    if value is None:
        return "TBD"
    if op == ">=":
        return "PASS" if value >= threshold else "FAIL"
    elif op == "<=":
        return "PASS" if value <= threshold else "FAIL"
    return "TBD"


def _estimate_replacement_cost(cim_data) -> float | None:
    """Estimate replacement cost from facility-type SF breakdowns.

    Uses typed SF fields if available, otherwise falls back to cc_pct split.
    """
    from config import REPLACEMENT_COST, FACILITY_TYPES

    nrsf = cim_data.nrsf
    if not nrsf:
        return None

    # Check for facility-type SF fields
    type_sf_map = {
        "ss_driveup":   cim_data.ss_driveup_sf,
        "ss_enclosed":  cim_data.ss_enclosed_sf,
        "brv_enclosed": cim_data.brv_enclosed_sf,
        "brv_covered":  cim_data.brv_covered_sf,
        "brv_open":     cim_data.brv_open_sf,
    }
    has_typed_sf = any(v is not None and v > 0 for v in type_sf_map.values())

    if not has_typed_sf:
        cc_pct = cim_data.cc_pct or 0.0
        type_sf_map = {
            "ss_driveup":   nrsf * (1.0 - cc_pct),
            "ss_enclosed":  nrsf * cc_pct,
            "brv_enclosed": 0,
            "brv_covered":  0,
            "brv_open":     0,
        }

    total_hard = 0.0
    total_site = 0.0
    for hard_key, site_key, _ in FACILITY_TYPES:
        short_key = hard_key.replace("_per_sf", "")
        sf = type_sf_map.get(short_key, 0) or 0
        if sf <= 0:
            continue
        total_hard += sf * sum(REPLACEMENT_COST[hard_key]) / 2
        total_site += sf * sum(REPLACEMENT_COST[site_key]) / 2

    subtotal = total_hard + total_site
    soft_pct = sum(REPLACEMENT_COST["soft_cost_pct"]) / 2
    dev_pct = sum(REPLACEMENT_COST["dev_profit_pct"]) / 2

    total = subtotal * (1 + soft_pct) * (1 + dev_pct)
    return total
