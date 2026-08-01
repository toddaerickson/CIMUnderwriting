"""Tests for bisection solver."""

import pytest
from model.solver import solve_max_price


def test_solver_converges():
    """Solver should converge within tolerance."""
    result = solve_max_price(adjusted_ttm_noi=300_000)
    assert result["converged"] is True
    assert result["max_price"] > 0


def test_solver_achieves_target_irr():
    """Achieved IRR should be within 0.5% of 10% target."""
    result = solve_max_price(adjusted_ttm_noi=300_000)
    assert abs(result["achieved_irr"] - 0.10) < 0.005


def test_solver_max_price_reasonable():
    """Max price should imply a cap rate between 3% and 20%."""
    result = solve_max_price(adjusted_ttm_noi=300_000)
    cap = result["implied_entry_cap"]
    assert 0.03 < cap < 0.20


def test_solver_capex_reduces_price():
    """Adding CapEx should reduce the max purchase price."""
    no_capex = solve_max_price(adjusted_ttm_noi=300_000, capex=0)
    with_capex = solve_max_price(adjusted_ttm_noi=300_000, capex=200_000)
    assert with_capex["max_price"] < no_capex["max_price"]


def test_solver_total_basis_includes_capex_and_closing_costs():
    """Total basis = max_price + capex + acquisition closing costs.

    Closing costs joined this identity in item B; before that the basis
    was price + capex and every solved IRR was overstated by their
    omission. `tests/test_transaction_costs.py` owns the cost oracles."""
    capex = 100_000
    result = solve_max_price(adjusted_ttm_noi=300_000, capex=capex)
    expected = result["max_price"] + capex + result["acquisition_cost"]
    assert abs(result["total_basis"] - expected) < 1
    assert result["acquisition_cost"] > 0
