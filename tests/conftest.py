"""Shared test fixtures for CIM Analyst tests."""

import pytest
from dataclasses import dataclass, field
from typing import Optional
from context import AnalysisContext


@pytest.fixture(autouse=True)
def _isolate_comp_db(tmp_path, monkeypatch):
    """Point the comp DB at a scratch file for EVERY test, always.

    `CompDatabase.__init__` calls `os.makedirs` + `CREATE TABLE IF NOT EXISTS`,
    so it fabricates a schema at whatever path it resolves without complaint —
    and `COMP_DB_PATH` derives from `config.__file__`, i.e. the tree the code
    runs from. A test that reaches `save_analysis` without redirecting it
    therefore writes real rows into the developer's live `data/cim_comps.db`,
    where they become comps for subsequent runs. `tests/test_characterization.py`
    documents having been bitten by exactly that: "a run feeds the next run's
    assumptions: the thin fixture's adjusted NOI moved 368,395 -> 305,595
    between two identical invocations, purely because the first invocation had
    added itself as a comp."

    That was previously guarded per-module and by ~15 hand-written monkeypatch
    lines, which is a discipline every new test file has to remember. This makes
    it structural. Patch `data.comp_db.COMP_DB_PATH`, NOT `config.COMP_DB_PATH`:
    `data/comp_db.py` binds the name at import, so patching config never reaches
    it. Modules that set their own path still win — they run after this.
    """
    monkeypatch.setattr("data.comp_db.COMP_DB_PATH",
                        str(tmp_path / "isolated_comps.db"), raising=False)


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
