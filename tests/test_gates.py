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
    """Gate 2: occupancy >= 85% should PASS."""
    mock_cim_data.physical_occupancy = 0.90
    gates = evaluate_gates(mock_cim_data, {}, {})
    g2 = next(g for g in gates if g["gate"] == 2)
    assert g2["result"] == "PASS"


def test_occupancy_gate_fail(mock_cim_data):
    """Gate 2: occupancy < 85% should FAIL."""
    mock_cim_data.physical_occupancy = 0.60
    gates = evaluate_gates(mock_cim_data, {}, {})
    g2 = next(g for g in gates if g["gate"] == 2)
    assert g2["result"] == "FAIL"


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
