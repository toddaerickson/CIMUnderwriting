"""One statement, counted once.

Every CIM in the corpus that runs to more than a few pages REPEATS its
operating statement — a single-property CIM restates it under a second pro
forma, a portfolio CIM states each property and again combined. The mapper
added every repeat to the same benchmark category, so the same dollar was
booked two or three times.

It is money, not display: the total flows to `total_adjusted_expenses` and
into adjusted NOI, and because the benchmark adjustment is
`max(CIM, benchmark)` (design decision 2) an INFLATED CIM value is kept
rather than clipped. Measured on the corpus before the fix:

    Dallas    property_tax  145,670  vs  72,835   2.00x  (SINGLE property)
    Dallas    insurance      73,068  vs  28,215
    Wichita   property_tax  155,514  vs  77,757   2.00x
    Wichita   insurance     122,110  vs  34,718
    Columbus  insurance      39,236  vs  19,618   2.00x

Dallas is the proof, because it owns one asset: nothing about two
properties can explain a property-tax line at exactly twice the truth.

The shapes below are those CIMs' shapes, reduced to the smallest table that
still reproduces the behaviour.
"""
import pytest

from analysis.financials import _map_expense_lines, _reconcile_statements
from extract.parser import CIMData, FinancialLine


def _cim(*lines):
    """A CIMData carrying nothing but the expense lines under test."""
    return CIMData(expense_lines=list(lines))


def _ln(label, value, statement):
    return FinancialLine(label=label, t12=value, statement=statement)


# ── The two shapes that reconcile ────────────────────────────────────

def test_the_same_statement_printed_twice_is_counted_once():
    """Dallas: pages 12 and 13 repeat ONE property's operating statement
    under different pro formas. The current column is identical in both."""
    cim = _cim(_ln("Property Taxes", 72_835, statement=0),
               _ln("Property Taxes", 72_835, statement=1))

    assert _map_expense_lines(cim)["property_tax"] == pytest.approx(72_835)


def test_per_property_statements_summing_to_a_combined_one_keep_the_whole():
    """Columbus: two properties at 8,497 and 11,121 plus the combined
    19,618. Adding all three books the portfolio twice."""
    cim = _cim(_ln("Insurance", 19_618, statement=0),
               _ln("Insurance", 8_497, statement=1),
               _ln("Insurance", 11_121, statement=2))

    assert _map_expense_lines(cim)["insurance"] == pytest.approx(19_618)


def test_the_combined_statement_is_found_by_arithmetic_not_by_page_order():
    """Wichita states the COMBINED figure on page 10 and its two property
    statements on pages 19 and 42 — the whole comes FIRST. Neither
    first-wins nor last-wins is correct, which is why the reconciliation
    is arithmetic and reads no page number at all.
    """
    whole_first = _cim(_ln("Property Taxes", 77_757, statement=0),
                       _ln("Property Taxes", 46_868, statement=1),
                       _ln("Property Taxes", 30_889, statement=2))
    whole_last = _cim(_ln("Property Taxes", 46_868, statement=0),
                      _ln("Property Taxes", 30_889, statement=1),
                      _ln("Property Taxes", 77_757, statement=2))

    for cim in (whole_first, whole_last):
        assert _map_expense_lines(cim)["property_tax"] == pytest.approx(77_757)


def test_wichita_parts_missing_the_whole_by_one_dollar_still_reconcile():
    """THE case a strict equality test loses, and the reason there is a
    tolerance at all.

    Rounding in the source document makes Wichita's parts miss its own
    combined statement by a dollar in BOTH directions:

        property tax   46,868 + 30,889 = 77,757  vs 77,757   exact
        insurance      17,316 +  9,942 = 27,258  vs 27,257   +1
        tenant ins      6,390 +  1,070 =  7,460  vs  7,461   -1

    A strict implementation refuses this CIM — the failure mode is silent
    and looks exactly like the fix working.
    """
    over = _cim(_ln("Insurance", 27_257, statement=0),
                _ln("Insurance", 17_316, statement=1),
                _ln("Insurance", 9_942, statement=2))
    under = _cim(_ln("Tenant Insurance Expense", 7_461, statement=0),
                 _ln("Tenant Insurance Expense", 6_390, statement=1),
                 _ln("Tenant Insurance Expense", 1_070, statement=2))

    assert _map_expense_lines(over)["insurance"] == pytest.approx(27_257)
    assert _map_expense_lines(under)["insurance"] == pytest.approx(7_461)


# ── The legitimate `+=`, kept ────────────────────────────────────────

def test_two_different_lines_of_one_statement_still_sum():
    """The accumulation was not wrong, only over-applied. `Insurance` and
    `Tenant Insurance Expense` on the SAME statement are two real costs
    and they genuinely add — this is the case the old `+=` existed for.
    """
    cim = _cim(_ln("Insurance", 27_257, statement=0),
               _ln("Tenant Insurance Expense", 7_461, statement=0))

    assert _map_expense_lines(cim)["insurance"] == pytest.approx(34_718)


def test_wichita_in_full_sums_within_and_reconciles_across():
    """Both rules at once, on the real numbers: three statements, each
    summing its own two insurance lines, whose per-property totals then
    reconcile to the combined one (23,706 + 11,012 = 34,718)."""
    cim = _cim(_ln("Insurance", 27_257, statement=0),
               _ln("Tenant Insurance Expense", 7_461, statement=0),
               _ln("Insurance", 17_316, statement=1),
               _ln("Tenant Insurance Expense", 6_390, statement=1),
               _ln("Insurance", 9_942, statement=2),
               _ln("Tenant Insurance Expense", 1_070, statement=2))

    assert _map_expense_lines(cim)["insurance"] == pytest.approx(34_718)


# ── The refusal, and where its boundary honestly sits ────────────────

def test_statements_that_do_not_reconcile_are_refused_not_guessed():
    """A refusal is not a fill (`analysis.fills`). The category is dropped
    and the reason is reported, rather than a number being chosen."""
    cim = _cim(_ln("Payroll", 50_000, statement=0),
               _ln("Payroll", 91_000, statement=1),
               _ln("Payroll", 12_000, statement=2))

    refusals = []
    mapped = _map_expense_lines(cim, refusals=refusals)

    assert "payroll" not in mapped
    assert [r["category"] for r in refusals] == ["payroll"]
    assert "50,000" in refusals[0]["reason"]


def test_exactly_two_disagreeing_statements_refuse_rather_than_choose():
    """The honest boundary, stated as a test so nobody 'fixes' it later.

    Given two statements at 46,868 and 30,889 and no combined figure,
    "one property stated twice with a typo" (take one) and "two properties
    whose sum is the portfolio" (add them) are indistinguishable from the
    numbers alone — and the two readings differ by the whole smaller
    statement. Refusing sends it to a human instead of to a coin flip.
    No corpus CIM reaches this branch.
    """
    cim = _cim(_ln("Property Taxes", 46_868, statement=0),
               _ln("Property Taxes", 30_889, statement=1))

    refusals = []
    assert "property_tax" not in _map_expense_lines(cim, refusals=refusals)
    assert refusals


def test_a_refusal_is_reported_and_not_silently_dropped():
    """The category vanishing is the DANGEROUS half: an absent category
    falls back to the benchmark floor x NRSF, which understates the
    expense and so overstates NOI. Silence here is anti-conservative.
    """
    cim = _cim(_ln("Payroll", 50_000, statement=0),
               _ln("Payroll", 91_000, statement=1),
               _ln("Payroll", 12_000, statement=2))

    refusals = []
    _map_expense_lines(cim, refusals=refusals)
    assert refusals and refusals[0]["reason"]


# ── Backward compatibility ───────────────────────────────────────────

def test_lines_with_no_statement_id_keep_todays_numbers():
    """A deal snapshotted before `statement` existed rehydrates with every
    line at `statement=None` (`webapp.services.cim_from_dict` drops
    unknown keys and defaults the rest). They land in ONE group and are
    summed within it — which is exactly the old behaviour, so a stored
    deal's expenses do not move until it is re-extracted.
    """
    cim = _cim(FinancialLine(label="Insurance", t12=19_618),
               FinancialLine(label="Insurance", t12=8_497),
               FinancialLine(label="Insurance", t12=11_121))

    assert _map_expense_lines(cim)["insurance"] == pytest.approx(39_236)


# ── The reconciler alone ─────────────────────────────────────────────

@pytest.mark.parametrize("values,expected", [
    ([72_835], 72_835),                          # one statement
    ([72_835, 72_835], 72_835),                  # exact repeat
    ([19_618, 8_497, 11_121], 19_618),           # parts and whole
    ([8_497, 11_121, 19_618], 19_618),           # order-independent
    ([27_257, 17_316, 9_942], 27_257),           # whole is a dollar light
])
def test_reconciler_accepts(values, expected):
    assert _reconcile_statements(list(values))[0] == pytest.approx(expected)


@pytest.mark.parametrize("values", [
    [46_868, 30_889],              # two, no whole — undecidable
    [50_000, 91_000, 12_000],      # three that neither match nor sum
])
def test_reconciler_refuses(values):
    value, reason = _reconcile_statements(list(values))
    assert value is None and reason
