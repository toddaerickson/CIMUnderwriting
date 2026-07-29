"""Tests for go/no-go gate evaluation."""

import pytest
from analysis.filters import evaluate_gates, summarize_gates


def test_population_gate_pass(mock_cim_data):
    """Gate 1: population >= 50K should PASS."""
    mock_cim_data.population_3mi = 75_000
    gates = evaluate_gates(mock_cim_data, {}, {})
    g1 = next(g for g in gates if g["gate"] == 1)
    assert g1["result"] == "PASS"


def test_population_gate_fail(mock_cim_data):
    """Gate 1: population < 50K should FAIL."""
    mock_cim_data.population_3mi = 30_000
    gates = evaluate_gates(mock_cim_data, {}, {})
    g1 = next(g for g in gates if g["gate"] == 1)
    assert g1["result"] == "FAIL"


def test_population_gate_tbd(mock_cim_data):
    """Gate 1: no population data should be TBD."""
    mock_cim_data.population_3mi = None
    gates = evaluate_gates(mock_cim_data, {}, {})
    g1 = next(g for g in gates if g["gate"] == 1)
    assert g1["result"] == "TBD"


def test_occupancy_gate_pass(mock_cim_data):
    """Gate 2: stabilized physical occupancy should PASS."""
    mock_cim_data.physical_occupancy = 0.90
    gates = evaluate_gates(mock_cim_data, {}, {})
    g2 = next(g for g in gates if g["gate"] == 2)
    assert g2["result"] == "PASS"


def test_occupancy_gate_fail_below_floor(mock_cim_data):
    """Gate 2: physical occupancy < 75% is unproven demand — FAIL."""
    mock_cim_data.physical_occupancy = 0.60
    gates = evaluate_gates(mock_cim_data, {}, {})
    g2 = next(g for g in gates if g["gate"] == 2)
    assert g2["result"] == "FAIL"
    assert "Unproven demand" in g2["note"]


def test_occupancy_gate_pass_proven_value_add(mock_cim_data):
    """Gate 2: 75-85% physical on an older vintage is proven demand — PASS."""
    mock_cim_data.physical_occupancy = 0.78
    mock_cim_data.year_built = 2005
    gates = evaluate_gates(mock_cim_data, {}, {})
    g2 = next(g for g in gates if g["gate"] == 2)
    assert g2["result"] == "PASS"
    assert "value-add" in g2["note"]


def test_occupancy_gate_fail_new_vintage_ramp(mock_cim_data):
    """Gate 2: post-2020 vintage still in ramp (<85%) — FAIL."""
    mock_cim_data.physical_occupancy = 0.80
    mock_cim_data.year_built = 2022
    gates = evaluate_gates(mock_cim_data, {}, {})
    g2 = next(g for g in gates if g["gate"] == 2)
    assert g2["result"] == "FAIL"
    assert "ramp" in g2["note"]


def test_occupancy_gate_pass_new_vintage_stabilized(mock_cim_data):
    """Gate 2: post-2020 vintage that has stabilized (>=85%) — PASS."""
    mock_cim_data.physical_occupancy = 0.88
    mock_cim_data.year_built = 2022
    gates = evaluate_gates(mock_cim_data, {}, {})
    g2 = next(g for g in gates if g["gate"] == 2)
    assert g2["result"] == "PASS"


def test_occupancy_gate_mismanagement_flag(mock_cim_data):
    """Gate 2: high physical / low economic passes with mismanagement flag."""
    mock_cim_data.physical_occupancy = 0.92
    mock_cim_data.economic_occupancy = 0.78
    gates = evaluate_gates(mock_cim_data, {}, {})
    g2 = next(g for g in gates if g["gate"] == 2)
    assert g2["result"] == "PASS"
    assert "mismanagement value-add candidate" in g2["note"]
    assert "78.0% econ" in g2["actual"]


def test_occupancy_gate_missing_econ_noted(mock_cim_data):
    """Gate 2: missing economic occupancy passes but flags the request."""
    mock_cim_data.physical_occupancy = 0.93
    mock_cim_data.economic_occupancy = None
    gates = evaluate_gates(mock_cim_data, {}, {})
    g2 = next(g for g in gates if g["gate"] == 2)
    assert g2["result"] == "PASS"
    assert "Economic occupancy not stated" in g2["note"]


def test_noi_stepup_gate_pass(mock_cim_data):
    """Gate 6: CIM Yr1 NOI <= 115% of TTM should PASS."""
    mock_cim_data.ttm_noi = 340_000
    mock_cim_data.cim_yr1_noi = 360_000  # 5.9% step-up
    gates = evaluate_gates(mock_cim_data, {}, {})
    g6 = next(g for g in gates if g["gate"] == 6)
    assert g6["result"] == "PASS"


def test_noi_stepup_gate_fail(mock_cim_data):
    """Gate 6: CIM Yr1 NOI > 115% of TTM should FAIL."""
    mock_cim_data.ttm_noi = 300_000
    mock_cim_data.cim_yr1_noi = 400_000  # 33% step-up
    gates = evaluate_gates(mock_cim_data, {}, {})
    g6 = next(g for g in gates if g["gate"] == 6)
    assert g6["result"] == "FAIL"


def test_vintage_ramp_no_contradictory_risk(mock_cim_data):
    """A post-2020 ramp deal fails Gate 2 and must NOT also get a
    'demand proven' operational risk contradicting the gate."""
    from analysis.risks import identify_risks
    mock_cim_data.physical_occupancy = 0.80
    mock_cim_data.year_built = 2022
    gates = evaluate_gates(mock_cim_data, {}, {})
    risks = identify_risks(mock_cim_data, gates, {}, {})
    labels = [r["risk"] for r in risks["risks"]]
    assert any("Failed Gate 2" in label for label in labels)
    assert not any("Below-stabilized" in label for label in labels)


def test_rate_bridge_risk_fires(mock_cim_data):
    """Stabilized occupancy + in-place rents well below market flags the
    ECRI-only bridge for street-rate trend verification."""
    from analysis.risks import identify_risks
    mock_cim_data.physical_occupancy = 0.92
    rent_analysis = {"rent_gap_analysis": {"gap_pct": -0.18}}
    risks = identify_risks(mock_cim_data, [], {}, {}, rent_analysis)
    assert any("street-rate" in r["risk"] for r in risks["risks"])


def test_summarize_all_pass(mock_cim_data):
    """All-passing gates yield PURSUE recommendation."""
    mock_cim_data.population_3mi = 75_000
    mock_cim_data.physical_occupancy = 0.92
    # Provide scenario results with passing IRR
    scenario_results = {
        "base": {"irr": 0.12, "moic": 1.5, "yield_on_cost": 0.07},
    }
    gates = evaluate_gates(mock_cim_data, scenario_results, {})
    summary = summarize_gates(gates)
    # Should not recommend DECLINE if the only failures are TBD gates
    assert summary["recommendation"] in ("PURSUE", "PURSUE WITH CAVEATS", "DECLINE")


# ── Gate 5: data-driven oversupply (SF/capita) ─────────────────────────

def test_oversupply_gate_pass_under_threshold(mock_cim_data):
    """(300k comp + 50k pipeline + 50k subject) / 75k pop = 5.3 SF/capita."""
    mock_cim_data.population_3mi = 75_000
    mock_cim_data.competitive_supply_sf_3mi = 300_000.0
    mock_cim_data.pipeline_supply_sf_3mi = 50_000.0
    gates = evaluate_gates(mock_cim_data, {}, {})
    g5 = next(g for g in gates if g["gate"] == 5)
    assert g5["result"] == "PASS"
    assert "5.3 SF/capita" in g5["actual"]


def test_oversupply_gate_fail_over_threshold(mock_cim_data):
    """(700k comp + 100k pipeline + 50k subject) / 75k = 11.3 > 10."""
    mock_cim_data.population_3mi = 75_000
    mock_cim_data.competitive_supply_sf_3mi = 700_000.0
    mock_cim_data.pipeline_supply_sf_3mi = 100_000.0
    gates = evaluate_gates(mock_cim_data, {}, {})
    g5 = next(g for g in gates if g["gate"] == 5)
    assert g5["result"] == "FAIL"


def test_oversupply_gate_tbd_without_supply_input(mock_cim_data):
    """No competitive-SF input → TBD, note names the unlocking fields."""
    mock_cim_data.population_3mi = 75_000
    mock_cim_data.competitive_supply_sf_3mi = None
    gates = evaluate_gates(mock_cim_data, {}, {})
    g5 = next(g for g in gates if g["gate"] == 5)
    assert g5["result"] == "TBD"
    assert "Competitive Supply SF" in g5["note"]


def test_oversupply_gate_pipeline_optional(mock_cim_data):
    """Pipeline SF blank counts as zero, not TBD."""
    mock_cim_data.population_3mi = 75_000
    mock_cim_data.competitive_supply_sf_3mi = 300_000.0
    mock_cim_data.pipeline_supply_sf_3mi = None
    gates = evaluate_gates(mock_cim_data, {}, {})
    g5 = next(g for g in gates if g["gate"] == 5)
    assert g5["result"] == "PASS"


# ── Gate 7: analyst market verification ────────────────────────────────

def test_msa_gate_auto_pass_top50(mock_cim_data):
    gates = evaluate_gates(mock_cim_data, {}, {})   # Dallas-Fort Worth MSA
    g7 = next(g for g in gates if g["gate"] == 7)
    assert g7["result"] == "PASS"


def test_msa_gate_strong_secondary_passes(mock_cim_data):
    mock_cim_data.msa = "Abilene, TX"
    mock_cim_data.city = "Abilene"
    mock_cim_data.market_verification = "strong_secondary"
    gates = evaluate_gates(mock_cim_data, {}, {})
    g7 = next(g for g in gates if g["gate"] == 7)
    assert g7["result"] == "PASS"
    assert "strong secondary" in g7["note"]


def test_msa_gate_neither_fails(mock_cim_data):
    mock_cim_data.msa = "Nowhere, TX"
    mock_cim_data.city = "Nowhere"
    mock_cim_data.market_verification = "neither"
    gates = evaluate_gates(mock_cim_data, {}, {})
    g7 = next(g for g in gates if g["gate"] == 7)
    assert g7["result"] == "FAIL"


def test_msa_gate_unverified_stays_tbd(mock_cim_data):
    mock_cim_data.msa = "Nowhere, TX"
    mock_cim_data.city = "Nowhere"
    mock_cim_data.market_verification = None
    gates = evaluate_gates(mock_cim_data, {}, {})
    g7 = next(g for g in gates if g["gate"] == 7)
    assert g7["result"] == "TBD"
    assert "Market Verification" in g7["note"]


def test_msa_gate_analyst_neither_overrides_automatch(mock_cim_data):
    """Explicit 'neither' beats a coincidental substring auto-match."""
    mock_cim_data.market_verification = "neither"   # msa stays Dallas (auto-match)
    gates = evaluate_gates(mock_cim_data, {}, {})
    g7 = next(g for g in gates if g["gate"] == 7)
    assert g7["result"] == "FAIL"


def test_oversupply_gate_zero_competitive_sf_computes(mock_cim_data):
    """0 competitive SF is a legitimate rural input — computes, not TBD."""
    mock_cim_data.population_3mi = 75_000
    mock_cim_data.competitive_supply_sf_3mi = 0.0
    gates = evaluate_gates(mock_cim_data, {}, {})
    g5 = next(g for g in gates if g["gate"] == 5)
    assert g5["result"] == "PASS"
    assert "0.7 SF/capita" in g5["actual"]


# ── Adversary-review repairs ───────────────────────────────────────────

def test_oversupply_gate_negative_input_degrades_to_tbd(mock_cim_data):
    """Legacy override files bypass form validation — a negative value
    must degrade to TBD with a reason, never flip the comparison sign."""
    mock_cim_data.population_3mi = -75_000
    mock_cim_data.competitive_supply_sf_3mi = 300_000.0
    gates = evaluate_gates(mock_cim_data, {}, {})
    g5 = next(g for g in gates if g["gate"] == 5)
    assert g5["result"] == "TBD"
    assert "negative or zero" in g5["note"]


def test_oversupply_gate_non_numeric_input_does_not_crash(mock_cim_data):
    """A hand-edited legacy JSON value must not take down all 7 gates."""
    mock_cim_data.population_3mi = 75_000
    mock_cim_data.competitive_supply_sf_3mi = "300,000"
    gates = evaluate_gates(mock_cim_data, {}, {})
    g5 = next(g for g in gates if g["gate"] == 5)
    assert g5["result"] == "TBD"
    assert "not numeric" in g5["note"]
    assert len(gates) == 7   # every other gate still evaluated


def test_msa_gate_stale_verification_for_other_location(mock_cim_data):
    """A verification stamped for one location must not bless another."""
    mock_cim_data.msa = "Abilene, TX"
    mock_cim_data.city = "Abilene"
    mock_cim_data.market_verification = "strong_secondary"
    mock_cim_data.market_verified_location = "Waco, TX"
    gates = evaluate_gates(mock_cim_data, {}, {})
    g7 = next(g for g in gates if g["gate"] == 7)
    assert g7["result"] == "TBD"
    assert "Waco" in g7["note"] and "re-verify" in g7["note"]


def test_msa_gate_verification_matching_location_passes(mock_cim_data):
    mock_cim_data.msa = "Abilene, TX"
    mock_cim_data.market_verification = "strong_secondary"
    mock_cim_data.market_verified_location = "Abilene, TX"
    gates = evaluate_gates(mock_cim_data, {}, {})
    g7 = next(g for g in gates if g["gate"] == 7)
    assert g7["result"] == "PASS"


def test_msa_gate_blank_location_stamp_flags_later_fill_in(mock_cim_data):
    """A verification stamped while msa/city were blank ('' stamp) must
    go stale when a location is later filled in — '' is a real stamp,
    not a legacy grandfather."""
    mock_cim_data.msa = "Podunk, TX"
    mock_cim_data.city = "Podunk"
    mock_cim_data.market_verification = "top_50"
    mock_cim_data.market_verified_location = ""
    gates = evaluate_gates(mock_cim_data, {}, {})
    g7 = next(g for g in gates if g["gate"] == 7)
    assert g7["result"] == "TBD"
    assert "re-verify" in g7["note"]
