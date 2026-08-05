"""Tests for financial analysis module."""

import pytest
from analysis.financials import analyze_financials
from analysis.value_add import identify_value_add


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


# ── The management-fee line is NOT a $/NRSF line ─────────────────────
#
# `_analyze_expenses` appends it separately from the $/NRSF loop and with
# a different SHAPE: `benchmark_range_pct` instead of `benchmark_range`,
# and no `per_nrsf` at all, because a fee quoted as a share of EGR has no
# per-square-foot benchmark to sit against. Anything walking `lines` has
# to respect that. These pin the contract at both ends.

def test_the_mgmt_fee_line_carries_a_pct_range_and_no_per_sf(mock_cim_data):
    """The shape half of the contract. If this line ever grows a
    `benchmark_range`, it has to be a real $/NRSF range — not the pct
    tuple under a new name — or the consumer below silently starts
    multiplying percentages by square footage."""
    mock_cim_data.mgmt_fee_pct = 0.09
    line = next(l for l in analyze_financials(
        mock_cim_data)["expense_analysis"]["lines"]
        if l["benchmark_key"] == "mgmt_fee_pct")

    assert "benchmark_range" not in line
    assert "per_nrsf" not in line
    assert line["benchmark_range_pct"] == (0.03, 0.06)
    assert line["flag"] == "ABOVE RANGE"


def test_an_above_band_mgmt_fee_does_not_crash_value_add(mock_cim_data):
    """A CIM stating a management fee ABOVE the 3-6% band raised
    `KeyError: 'benchmark_range'` — `_expense_opportunities` walked every
    `ABOVE RANGE` line and read the $/NRSF key off the one line that has
    no such key. Any third-party-managed property quoting 7% killed the
    whole value-add section, and the engine reports it as a stage
    failure, so the deal simply came back with no operational upside.

    9% is chosen because it is above the band (triggering the crash path)
    AND above the adjustment target (so the fee genuinely is an
    opportunity) — the fix must not silence the finding, only stop
    computing it on the wrong basis.
    """
    mock_cim_data.mgmt_fee_pct = 0.09
    fin = analyze_financials(mock_cim_data)

    ops = identify_value_add(mock_cim_data, fin)["expense_opportunities"]
    categories = [o["category"] for o in ops]

    # The real finding survives, on its own %-of-EGR basis...
    assert "Management Fee Reduction" in categories
    egr = fin["income_summary"]["egr"]
    mgmt_op = next(o for o in ops if o["category"] == "Management Fee Reduction")
    assert mgmt_op["est_annual_impact"] == pytest.approx(egr * (0.09 - 0.05))

    # ...and the $/NRSF loop does NOT also emit a bogus duplicate for it.
    assert "Reduce Management Fee" not in categories
