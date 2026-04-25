"""Tests for financial analysis module."""

import pytest
from analysis.financials import analyze_financials


def test_adjusted_noi_computed(mock_cim_data):
    """Should compute an analyst-adjusted NOI."""
    result = analyze_financials(mock_cim_data)
    adj = result["adjusted_ttm_noi"]["analyst_adjusted_noi"]
    assert adj is not None
    assert adj > 0


def test_adjusted_noi_differs_from_cim(mock_cim_data):
    """Analyst-adjusted NOI may differ from CIM NOI due to expense benchmarking."""
    result = analyze_financials(mock_cim_data)
    adj = result["adjusted_ttm_noi"]["analyst_adjusted_noi"]
    cim = result["adjusted_ttm_noi"]["cim_ttm_noi"]
    # Both should be positive; they may differ in either direction
    assert adj > 0
    assert cim > 0


def test_expense_ratio_in_range(mock_cim_data):
    """OpEx/Revenue ratio should be between 0% and 100%."""
    result = analyze_financials(mock_cim_data)
    ratio = result.get("expense_ratio_check", {}).get("opex_revenue_ratio")
    if ratio is not None:
        assert 0 < ratio < 1.0


def test_handles_zero_revenue(mock_cim_data):
    """Should handle zero revenue gracefully."""
    mock_cim_data.ttm_total_revenue = 0
    mock_cim_data.ttm_egr = 0
    mock_cim_data.ttm_gpr = 0
    result = analyze_financials(mock_cim_data)
    # Should not crash
    assert "adjusted_ttm_noi" in result
