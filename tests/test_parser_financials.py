"""Financial-field extraction (`extract.parser._parse_financials`).

First test home for the financial side of the parser — until now only
location extraction had one. Seeded with `ttm_months`, the reporting-
period field item A's deferred TTM-annualization check waited on.

The rule under test is decision 1 (parser tolerance) applied to a field
whose absence is load-bearing: extract what the CIM states, return None
for what it does not, and never invent a 12 — an annualization check run
against an assumed full year flags nothing.
"""
from extract.parser import CIMData, _parse_financials


def _parse(text: str) -> CIMData:
    data = CIMData()
    _parse_financials(text, [], data)
    return data


def test_t_dash_annualized_form():
    assert _parse("Financials shown are T-9 annualized.").ttm_months == 9


def test_months_ending_form():
    data = _parse("Based on 9 months ending June 30, 2025.")
    assert data.ttm_months == 9


def test_months_ended_form():
    assert _parse("For the 6 months ended March 31.").ttm_months == 6


def test_trailing_n_month_form():
    assert _parse("Trailing 9-month revenue of $1,200,000.").ttm_months == 9


def test_a_full_trailing_twelve_is_stored_as_twelve():
    """Twelve is data, not a default: a CIM that SAYS 'trailing 12-month'
    gets a 12 the check can pass on, which is different from the None an
    unstated basis gets."""
    assert _parse("Trailing 12-month NOI: $650,000").ttm_months == 12


def test_unstated_basis_is_none_never_twelve():
    data = _parse("TTM NOI: $650,000. Total revenue: $1,000,000.")
    assert data.ttm_months is None


def test_more_than_twelve_months_is_not_stored():
    """'24 months ended' is not a trailing-twelve-month basis; claiming a
    ttm_months from it would misdescribe the figure the field
    qualifies."""
    assert _parse("Audited results for the 24 months ended.").ttm_months is None


def test_other_financials_still_parse_alongside():
    data = _parse("TTM NOI: $650,000 (T-9 annualized). "
                  "Total revenue: $1,000,000.")
    assert data.ttm_noi == 650_000
    assert data.ttm_months == 9
    assert data.ttm_total_revenue == 1_000_000
