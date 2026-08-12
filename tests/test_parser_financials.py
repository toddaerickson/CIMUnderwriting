"""Financial-field extraction (`extract.parser._parse_financials`).

First test home for the financial side of the parser — until now only
location extraction had one. Seeded with `ttm_months`, the reporting-
period field item A's deferred TTM-annualization check waited on.

The rule under test is decision 1 (parser tolerance) applied to a field
whose absence is load-bearing: extract what the CIM states, return None
for what it does not, and never invent a 12 — an annualization check run
against an assumed full year flags nothing.

**A wrong month count is worse than a missing one**, which is what the
three guards below exist for. The first version of this extractor read
"T3 Annualized Revenue" — the momentum figure this codebase models as
its own field — as a three-month TTM basis, on documents that stated a
trailing-twelve basis outright. That is not a missed extraction; it is
the register printing a false statement about what the CIM said and the
check firing on a deal with nothing wrong with it. Each class below is
one of those guards, and each has a case that was WRONG before.
"""
from extract.parser import CIMData, _parse_financials


def _months(text: str):
    data = CIMData()
    _parse_financials(text, [], data)
    return data.ttm_months


# ── What a stated basis looks like ──────────────────────────────────

def test_t_dash_annualized_form():
    assert _months("Financials shown are T-9 annualized.") == 9


def test_months_ending_form():
    assert _months("Financials are based on 9 months ending June 30, 2025.") == 9


def test_months_ended_form():
    assert _months("Operating results for the 6 months ended March 31.") == 6


def test_trailing_n_month_form():
    assert _months("Trailing 9-month revenue of $1,200,000.") == 9


def test_a_full_trailing_twelve_is_stored_as_twelve():
    """Twelve is data, not a default: a CIM that SAYS 'trailing 12-month'
    gets a 12 the check can pass on, which is different from the None an
    unstated basis gets."""
    assert _months("Trailing 12-month NOI: $650,000") == 12


def test_a_basis_stated_beside_the_figures_it_qualifies():
    assert _months("TTM NOI: $650,000 (T-9 annualized). "
                   "Total revenue: $1,000,000.") == 9


def test_unstated_basis_is_none_never_twelve():
    assert _months("TTM NOI: $650,000. Total revenue: $1,000,000.") is None


# ── Guard 1: the count must qualify the FINANCIALS ──────────────────

def test_a_month_count_in_a_rent_trend_sentence_is_not_a_ttm_basis():
    """WAS WRONG: stored 6. A street-rate window is a different figure
    over a different period; reading a TTM basis out of it describes
    something the sentence never claimed."""
    assert _months("Street rates rose in the 6 months ended June 30.") is None


def test_a_lease_term_in_months_is_not_a_ttm_basis():
    assert _months("Lease terms average 8 months. TTM NOI: $650,000.") is None


def test_an_unanchored_window_does_not_override_a_stated_basis():
    """WAS WRONG: stored 6, because the rent sentence came first in the
    text and pattern order decided the answer."""
    assert _months("Street rates rose 4% in the 6 months ended June 30. "
                   "Trailing 12-month NOI: $650,000.") == 12


# ── Guard 2: the T-3 momentum collision ─────────────────────────────

def test_t3_annualized_revenue_is_the_momentum_figure_not_a_basis():
    """WAS WRONG: stored 3 on a deal whose CIM states trailing twelve
    outright. T3-beside-T12 is routine presentation in these documents,
    so this misfired on ordinary healthy deals — the register recorded
    ttm_months=3 with provenance 'cim', and the check reported a
    seasonality risk that the document never claimed."""
    assert _months("Trailing 12-month NOI: $650,000. "
                   "T3 Annualized Revenue: $1,250,000.") == 12


def test_trailing_three_month_revenue_is_also_the_momentum_figure():
    """WAS WRONG: stored 3."""
    assert _months("Trailing 3-month revenue momentum vs trailing "
                   "12-month NOI of $650,000.") == 12


# ── Guard 3: conflict means the parser does not know ────────────────

def test_two_different_stated_bases_yield_none_rather_than_a_winner():
    """Pattern priority picking a winner is exactly how the T3 collision
    beat an explicit trailing-twelve statement. Disagreement is not a tie
    to break — it is the parser not knowing, which is what None means and
    what the assumptions page exists to resolve."""
    assert _months("NOI is T-9 annualized. Revenue reflects 6 months "
                   "ended June 30.") is None


def test_more_than_twelve_months_is_not_stored():
    """'24 months ended' is not a trailing-twelve-month basis; claiming a
    ttm_months from it would misdescribe the figure the field
    qualifies."""
    assert _months("Audited results for the 24 months ended.") is None


# ── The rest of the financial parse still works ─────────────────────

def test_other_financials_still_parse_alongside():
    data = CIMData()
    _parse_financials("TTM NOI: $650,000 (T-9 annualized). "
                      "Total revenue: $1,000,000.", [], data)
    assert data.ttm_noi == 650_000
    assert data.ttm_months == 9
    assert data.ttm_total_revenue == 1_000_000
