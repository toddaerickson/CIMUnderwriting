"""Tests for the central registry module."""

import pytest
from registry import (
    ScenarioType, EXPENSE_CATEGORIES, EXPENSE_KEYS,
    EXPENSE_DISPLAY_MAP, EXPENSE_KEYWORD_MAP,
    clamp_expense_ratio, STANDARD_SIZE_BUCKETS,
)


def test_scenario_type_str_equality():
    """ScenarioType values should equal their string counterparts."""
    assert ScenarioType.BEAR == "bear"
    assert ScenarioType.BASE == "base"
    assert ScenarioType.BULL == "bull"


def test_scenario_type_title():
    """ScenarioType .title() should produce capitalized names."""
    assert ScenarioType.BEAR.title() == "Bear"
    assert ScenarioType.BASE.title() == "Base"
    assert ScenarioType.BULL.title() == "Bull"


def test_expense_categories_count():
    """Should have 8 standard expense categories."""
    assert len(EXPENSE_CATEGORIES) == 8


def test_expense_keys_match_categories():
    """EXPENSE_KEYS should be derived from EXPENSE_CATEGORIES."""
    assert len(EXPENSE_KEYS) == len(EXPENSE_CATEGORIES)
    for cat in EXPENSE_CATEGORIES:
        assert cat.key in EXPENSE_KEYS


def test_expense_display_map_complete():
    """Every key should have a display name."""
    for key in EXPENSE_KEYS:
        assert key in EXPENSE_DISPLAY_MAP
        assert len(EXPENSE_DISPLAY_MAP[key]) > 0


def test_clamp_expense_ratio_default():
    """None input should return the default ratio."""
    result = clamp_expense_ratio(None)
    assert result == 0.40


def test_clamp_expense_ratio_low():
    """Too-low ratio should be clamped to floor."""
    assert clamp_expense_ratio(0.10) == 0.25


def test_clamp_expense_ratio_high():
    """Too-high ratio should be clamped to ceiling."""
    assert clamp_expense_ratio(0.90) == 0.65


def test_clamp_expense_ratio_passthrough():
    """In-range ratio should pass through unchanged."""
    assert clamp_expense_ratio(0.42) == 0.42


def test_standard_size_buckets():
    """Size buckets should have reasonable SF values."""
    assert STANDARD_SIZE_BUCKETS["5x5"] == 25
    assert STANDARD_SIZE_BUCKETS["10x10"] == 100
    assert STANDARD_SIZE_BUCKETS["10x20"] == 200
    assert all(v > 0 for v in STANDARD_SIZE_BUCKETS.values())
