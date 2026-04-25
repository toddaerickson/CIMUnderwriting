"""Shared test fixtures for CIM Analyst tests."""

import pytest
from dataclasses import dataclass, field
from typing import Optional
from context import AnalysisContext


@pytest.fixture
def mock_cim_data():
    """Minimal CIMData-like object for unit tests.

    Uses a simple namespace instead of importing CIMData to keep
    tests independent of parser internals.
    """
    from extract.parser import CIMData
    data = CIMData()
    data.property_name = "Test Storage"
    data.address = "123 Main St"
    data.city = "Dallas"
    data.state = "TX"
    data.msa = "Dallas-Fort Worth-Arlington, TX"
    data.nrsf = 50_000
    data.total_units = 400
    data.physical_occupancy = 0.90
    data.asking_price = 5_000_000
    data.ttm_gpr = 600_000
    data.ttm_egr = 550_000
    data.ttm_total_revenue = 560_000
    data.ttm_total_expenses = 220_000
    data.ttm_noi = 340_000
    data.cim_yr1_noi = 360_000
    data.capex_estimate = 50_000
    data.population_3mi = 75_000
    return data


@pytest.fixture
def base_financial_analysis():
    """Minimal financial analysis dict for tests."""
    return {
        "adjusted_ttm_noi": {
            "analyst_adjusted_noi": 320_000,
            "cim_noi": 340_000,
            "adjustment_notes": [],
        },
        "expense_ratio_check": {
            "opex_revenue_ratio": 0.40,
        },
        "expense_analysis": {
            "total_adjusted_expenses": 220_000,
        },
        "benchmark_source": "national",
    }


@pytest.fixture
def sample_context(mock_cim_data, base_financial_analysis):
    """AnalysisContext with CIM data and financial analysis pre-loaded."""
    ctx = AnalysisContext(pdf_path="/tmp/test.pdf")
    ctx.cim_data = mock_cim_data
    ctx.financial_analysis = base_financial_analysis
    return ctx
