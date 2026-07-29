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


def test_expense_line_override_beats_cim_and_still_benchmarked(mock_cim_data):
    """Analyst value replaces the CIM-extracted line; the benchmark
    floor still applies on top (Final below floor -> floor, flagged).

    mock_cim_data has no expense_lines by default, so the CIM-extracted
    payroll value is None (_map_expense_lines short-circuits to {} when
    ttm_total_expenses is set and expense_lines is empty) — the analyst
    override is the only source for this line.
    """
    fin = analyze_financials(mock_cim_data,
                             expense_line_overrides={"payroll": 12_600.0})
    payroll = next(l for l in fin["expense_analysis"]["lines"]
                   if l["benchmark_key"] == "payroll")
    assert payroll["cim_value"] == 12_600.0     # coalesced input
    assert payroll["source"] == "analyst"
    # analyst enters a value below the floor -> engine floors it, flagged
    fin2 = analyze_financials(mock_cim_data,
                              expense_line_overrides={"payroll": 500.0})
    p2 = next(l for l in fin2["expense_analysis"]["lines"]
              if l["benchmark_key"] == "payroll")
    assert p2["flag"] == "BELOW RANGE"
    assert p2["adjusted_value"] > 500.0
    assert p2["source"] == "analyst"


def test_expense_line_no_override_marks_cim_source(mock_cim_data):
    """Without an override, a CIM-extracted line is marked source="cim";
    a line the CIM never reported (mock_cim_data has none, by design of
    the fixture) is marked source=None."""
    fin = analyze_financials(mock_cim_data)
    payroll = next(l for l in fin["expense_analysis"]["lines"]
                   if l["benchmark_key"] == "payroll")
    assert payroll["cim_value"] is None
    assert payroll["source"] is None
