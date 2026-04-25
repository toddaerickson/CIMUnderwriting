"""Tests for AnalysisContext and pipeline snapshots."""

import pytest
from context import AnalysisContext


def test_context_default_values():
    """Fresh context should have safe defaults."""
    ctx = AnalysisContext()
    assert ctx.adjusted_noi is None
    assert ctx.expense_ratio is None
    assert ctx.asking_price == 0
    assert ctx.nrsf == 1
    assert ctx.property_name == "Unknown_Property"


def test_context_properties(sample_context):
    """Context properties should delegate to cim_data."""
    assert sample_context.asking_price == 5_000_000
    assert sample_context.nrsf == 50_000
    assert sample_context.property_name == "Test Storage"


def test_context_adjusted_noi(sample_context):
    """adjusted_noi should come from financial_analysis dict."""
    assert sample_context.adjusted_noi == 320_000


def test_snapshot_and_diff(sample_context):
    """Snapshot should capture state; diff should detect changes."""
    sample_context.snapshot("before")
    sample_context.cim_data.asking_price = 6_000_000
    sample_context.cim_data.physical_occupancy = 0.95
    changes = sample_context.diff_snapshot("before")
    assert "asking_price" in changes
    assert changes["asking_price"] == (5_000_000, 6_000_000)
    assert "physical_occupancy" in changes


def test_diff_no_changes(sample_context):
    """Diff with no mutations should return empty dict."""
    sample_context.snapshot("before")
    changes = sample_context.diff_snapshot("before")
    assert changes == {}


def test_diff_missing_snapshot(sample_context):
    """Diff against nonexistent snapshot returns empty dict."""
    changes = sample_context.diff_snapshot("nonexistent")
    assert changes == {}
