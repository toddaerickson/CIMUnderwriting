"""
Section 1 — Go / No-Go Gate Evaluation.

Evaluates 7 binary gates against hard-coded thresholds.
Each gate returns PASS, FAIL, or TBD (data missing).
"""

from config import GATES, TOP_50_MSAS
from registry import ScenarioType


def sf_per_capita_inputs(cim_data):
    """Raw SF/capita components — single formula source for both the
    gate-5 note (which needs the individual figures) and the public
    sf_per_capita() summary.

    Returns (comp_sf, pipe_sf, subject_sf, pop, sf_pc, input_problem).
    sf_pc is None when inputs are missing or invalid; input_problem is
    the human reason ("" when simply not yet entered).
    """
    comp_sf = None
    pipe_sf = None
    subject_sf = None
    pop = None
    sf_pc = None
    input_problem = ""
    # Values can arrive from legacy JSON override files and old snapshots
    # that bypass form validation — a bad value must degrade THIS gate to
    # TBD with a reason, never crash the run or flip the comparison sign.
    try:
        comp_sf = getattr(cim_data, "competitive_supply_sf_3mi", None)
        comp_sf = None if comp_sf is None else float(comp_sf)
        pipe_sf = float(getattr(cim_data, "pipeline_supply_sf_3mi", None) or 0)
        subject_sf = float(cim_data.nrsf or 0)
        pop = cim_data.population_3mi
        pop = None if pop is None else float(pop)
        if comp_sf is not None and pop is not None:
            if pop <= 0 or comp_sf < 0 or pipe_sf < 0 or subject_sf < 0:
                input_problem = ("supply/population input negative or zero — "
                                 "fix the value in assumptions")
            else:
                sf_pc = (comp_sf + pipe_sf + subject_sf) / pop
    except (TypeError, ValueError):
        input_problem = "supply/population input not numeric — fix the value"
    return comp_sf, pipe_sf, subject_sf, pop, sf_pc, input_problem


def sf_per_capita(cim_data):
    """(value, problem): value None when inputs missing/invalid; problem
    is the human reason, and is never blank when the value is None.

    Gate 5 reads sf_per_capita_inputs() directly and writes its own TBD
    note naming the unlocking fields; this wrapper feeds the assumptions
    strip, whose only affordance is the dash's tooltip. That tooltip used
    to be the empty string, so an analyst looking at a `—` beside a
    populated NRSF and population had nothing telling them the formula
    also needs competitive supply — and would reasonably read the dash as
    a failure to compute rather than as an input not yet entered.
    """
    comp_sf, _, _, pop, sf_pc, input_problem = sf_per_capita_inputs(cim_data)
    if sf_pc is not None or input_problem:
        return sf_pc, input_problem
    missing = []
    if comp_sf is None:
        missing.append("competitive supply SF within 3 miles")
    if pop is None:
        missing.append("3-mile population")
    if not missing:
        return None, ""
    return None, ("Not computable yet — SF/capita is (competitive + "
                  "pipeline + subject NRSF) / 3-mile population, and this "
                  "deal has not been given " + " or ".join(missing) +
                  ". Enter it under Size & Demographics.")


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
    # Same hardening, and for the same reason, as sf_per_capita_inputs above:
    # the value can arrive from a legacy JSON override or an old snapshot that
    # bypassed form validation, and a negative or non-numeric one must degrade
    # THIS gate to TBD with a reason rather than crash the run or flip the
    # comparison sign. Note the gate is read as a VERDICT — a population the
    # parser never actually found in the CIM has failed real deals here, so
    # "N/A + verify manually" is the honest output, not a number.
    pop = cim_data.population_3mi
    pop_problem = ""
    try:
        pop = None if pop is None else float(pop)
        if pop is not None and pop < 0:
            pop, pop_problem = None, "population is negative — fix the value in assumptions"
    except (TypeError, ValueError):
        pop, pop_problem = None, "population is not numeric — fix the value in assumptions"
    # Through `origin_for`, not a direct lookup: the entry records where ONE
    # number came from, and an analyst who typed a population over the
    # measured one leaves it describing a figure this gate is no longer
    # reading. Crediting the Census with an analyst's number is the same
    # class of defect as the register's, on the same value.
    from extract.enrichment import origin_for
    pop_entry = origin_for(source_log, "population_3mi",
                           cim_data.population_3mi) or {}
    pop_source = pop_entry.get("source", "")
    gates.append({
        "gate": 1,
        "name": "Population (3-mi ≥ 50K)",
        "threshold": f"≥ {GATES['population_3mi']:,}",
        "actual": f"{pop:,.0f}" if pop else "N/A",
        "result": _eval(pop, GATES["population_3mi"], ">=") if pop else "TBD",
        "note": pop_problem or ("" if pop else "Population data not found in CIM — verify manually"),
        "source": pop_source if pop_source else None,
    })

    # Gate 2: No unproven demand
    # FAIL only when demand itself is unproven: sub-75% physical occupancy,
    # or a post-2020 vintage that has never stabilized. High-physical /
    # low-economic deals PASS with a mismanagement value-add flag.
    occ = cim_data.physical_occupancy
    econ = cim_data.economic_occupancy
    vintage = cim_data.year_built
    floor = GATES["min_physical_occupancy"]
    stabilized = GATES["stabilized_occupancy"]

    if occ is None:
        occ_result = "TBD"
        occ_note = "Occupancy not found in CIM"
    elif occ < floor:
        occ_result = "FAIL"
        occ_note = f"Unproven demand — physical occupancy below {floor:.0%} floor"
    elif vintage and vintage >= GATES["unproven_vintage_year"] and occ < stabilized:
        occ_result = "FAIL"
        occ_note = (f"Unproven demand — {vintage} vintage still in ramp at "
                    f"{occ:.1%}; facility has never demonstrated stabilized demand")
    else:
        occ_result = "PASS"
        notes = []
        if econ is not None and occ - econ >= GATES["econ_phys_spread_flag"]:
            notes.append(f"{(occ - econ) * 100:.0f}-pt economic/physical spread — "
                         "mismanagement value-add candidate")
        elif econ is None:
            notes.append("Economic occupancy not stated — request it; a single "
                         "quoted occupancy figure is almost always physical")
        if occ < stabilized:
            notes.append(f"Sub-{stabilized:.0%} physical but demand proven — "
                         "underwrite as value-add lease-up")
        occ_note = ". ".join(notes)

    occ_actual = "N/A"
    if occ is not None:
        occ_actual = f"{occ:.1%} phys"
        if econ is not None:
            occ_actual += f" / {econ:.1%} econ"

    gates.append({
        "gate": 2,
        "name": "No Unproven Demand",
        "threshold": f"Phys ≥ {floor:.0%}; no post-{GATES['unproven_vintage_year'] - 1} ramp",
        "actual": occ_actual,
        "result": occ_result,
        "note": occ_note,
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

    # Gate 4: unlevered IRR over the hold ≥ 10%
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

    # The hold is variable now, so the gate must not claim "5-Yr" when it
    # measured something else. The config KEY stays `min_irr_5yr`: stored
    # ConfigOverride rows reference it by name and renaming it would
    # orphan them (they would be logged as unknown and skipped).
    hold = None
    for source in (va_results, scenario_results):
        if source and ScenarioType.BASE in source:
            hold = (source[ScenarioType.BASE].get("hold_years")
                    or len(source[ScenarioType.BASE].get("noi_projection")
                           or source[ScenarioType.BASE].get("annual_noi") or [])
                    or None)
            if hold:
                break

    gates.append({
        "gate": 4,
        "name": (f"{hold}-Yr Unlevered IRR ≥ {GATES['min_irr_5yr']:.0%}"
                 if hold else f"Unlevered IRR ≥ {GATES['min_irr_5yr']:.0%}"),
        "threshold": f"≥ {GATES['min_irr_5yr']:.0%}",
        "actual": irr_display,
        "result": _eval(gate_irr, GATES["min_irr_5yr"], ">=") if gate_irr else "TBD",
        "note": f"Using {irr_source} model" if (gate_irr and gate_irr >= GATES["min_irr_5yr"]) else
                f"Below target IRR ({irr_source} model)" if gate_irr else
                "Pending scenario analysis",
    })

    # Gate 5: No oversupply flag — data-driven when the analyst enters
    # competitive supply; SF/capita = (subject + competitive + pipeline
    # NRSF within 3 mi) / 3-mi population. Without those inputs the gate
    # stays TBD and says exactly which fields unlock it.
    supply = cim_data.new_supply_mentions
    limit = GATES["max_sf_per_capita"]
    comp_sf, pipe_sf, subject_sf, pop, sf_pc, input_problem = (
        sf_per_capita_inputs(cim_data))
    if sf_pc is not None:
        note = (f"({comp_sf:,.0f} competitive + {pipe_sf:,.0f} pipeline + "
                f"{subject_sf:,.0f} subject SF) / {pop:,.0f} pop = "
                f"{sf_pc:.1f} SF/capita (equilibrium ~7-8)")
        if supply:
            note += f". Supply mentions: {supply[:120]}"
        gates.append({
            "gate": 5,
            "name": "No Oversupply Flag",
            "threshold": f"≤ {limit:.0f} SF/capita",
            "actual": f"{sf_pc:.1f} SF/capita",
            "result": "PASS" if sf_pc <= limit else "FAIL",
            "note": note,
        })
    else:
        note = (f"Supply mentions: {supply[:160]}. " if supply else "")
        note += (input_problem or
                 "Enter Competitive Supply SF (3-mi) — and population — "
                 "in assumptions to compute SF/capita")
        gates.append({
            "gate": 5,
            "name": "No Oversupply Flag",
            "threshold": f"≤ {limit:.0f} SF/capita",
            "actual": "See notes" if supply else "N/A",
            "result": "TBD",
            "note": note,
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

    # Gate 7: Major city / Top-50 MSA — the criteria allow "Top-50 MSA or
    # strong secondary market". Auto-PASS on a top-50 substring match;
    # otherwise the analyst's recorded verification resolves the gate.
    msa = cim_data.msa or cim_data.city or ""
    msa_match = any(m.lower() in msa.lower() for m in TOP_50_MSAS) if msa else False
    verification = getattr(cim_data, "market_verification", None) or ""
    # A verification recorded for a different msa/city is stale — a later
    # location edit must not inherit a pass it never earned. Legacy
    # verifications without a stamped location are grandfathered.
    verified_for = getattr(cim_data, "market_verified_location", None)
    # "" is a real stamp (verified while location was blank) — only None
    # means "no stamp" (legacy grandfather). Truthiness here would let a
    # blank-location verification bless any later msa fill-in.
    if verification and verified_for is not None and \
            verified_for.strip().lower() != msa.strip().lower():
        verification = ""
        stale_note = (f"Market verification was recorded for "
                      f"“{verified_for}” but location is now “{msa}” — "
                      f"re-verify in assumptions")
    else:
        stale_note = ""
    # Explicit analyst verification outranks the auto-match: a coincidental
    # substring hit (e.g. a "Dallas" 60 miles out) must not silently
    # override a recorded "neither" verdict.
    if verification == "top_50":
        result, note = "PASS", "Analyst-verified: Top-50 MSA"
    elif verification == "strong_secondary":
        result, note = "PASS", "Analyst-verified: strong secondary market"
    elif verification == "neither":
        result, note = "FAIL", "Analyst-verified: neither Top-50 nor strong secondary"
    elif msa_match:
        result, note = "PASS", ""
    else:
        result, note = "TBD", (stale_note or
                               "MSA not auto-matched to top-50 — set Market "
                               "Verification in assumptions to resolve")
    gates.append({
        "gate": 7,
        "name": "Major City / Top-50 MSA",
        "threshold": "Top-50 MSA or strong secondary",
        "actual": msa if msa else "N/A",
        "result": result,
        "note": note,
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
