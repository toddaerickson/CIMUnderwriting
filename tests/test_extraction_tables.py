"""Column roles: which period does a number belong to?

Every test here runs on a synthetic table in `tests/fixtures/table_shapes.py`
whose SHAPE was measured on a real CIM. The bug each one pins is named in that
file beside the fixture; what is asserted here is the behaviour.

The one number worth carrying into this file: across 227 tables in seven CIMs,
**no table anywhere orders its periods most-recent-last**. The replaced code
read `values[-1]` as the trailing twelve, so it was not occasionally wrong —
of the 116 lines that carried a figure, exactly ONE comes out of the new rule
unchanged.
"""
import pytest

from extract import tables as T
from extract.parser import CIMData, _parse_financial_tables
from tests.fixtures.table_shapes import (
    COLUMBUS_OFFSET, COLUMBUS_TAX_PANEL, DALLAS_BARE_INTEGER_YEARS,
    DALLAS_REPEATED_STATEMENT, KERRVILLE_FRAGMENT_PER_SF,
    KERRVILLE_FRAGMENT_PROJECTION, SYNTHETIC_T3, WICHITA_INSURANCE_INCOME,
    WICHITA_ASSUMPTION_PANEL, WICHITA_GROWTH_FOOTNOTES, WICHITA_INTERLEAVED,
    wrap,
)


def _lines(*tables):
    """Parse tables into `{label: FinancialLine}` plus the refusal log."""
    data = CIMData()
    _parse_financial_tables(list(tables), data)
    by_label = {ln.label: ln for ln in data.income_lines + data.expense_lines}
    return by_label, data.unmapped_financial_lines


# ── Cells keep their position ────────────────────────────────────────

def test_a_row_yields_one_cell_per_source_column():
    """The prerequisite for every header rule.

    The replaced code appended only parseable values, so `values[i]` stopped
    corresponding to column `i` the moment a `$0.20SF` or a `37.14%` cell
    appeared — which is most rows in the corpus. A compacted list cannot be
    mapped by a header no matter how good the header rule is.
    """
    row = WICHITA_INTERLEAVED[1]
    cells = T.parse_row(row)
    assert len(cells) == len(row)
    assert cells[2].kind == T.KIND_MONEY and cells[2].value == 500_000
    assert cells[3].kind == T.KIND_PER_SF and cells[3].value is None


def test_a_per_sf_cell_never_yields_a_dollar_value():
    """`$5.58SF` is a rate, and the corpus books three of them as dollars
    today — a $4.63 "total operating expenses" against a real $211,591."""
    assert T.parse_cell("$5.58SF").kind == T.KIND_PER_SF
    assert T.parse_cell("$5.58SF").value is None
    assert T.parse_cell("10.15/SF").value is None
    # But a label ENDING in those two letters is not a per-SF figure.
    assert T.parse_cell("Total NRSF 45,000").kind == T.KIND_MONEY


def test_a_percent_and_an_amount_in_one_cell_yield_the_amount():
    """Under-segmented tables merge them. The percent is a ratio the row
    already states in dollars; taking it substitutes a rate for a figure."""
    cell = T.parse_cell("37.14% $ (222,391)")
    assert cell.kind == T.KIND_MONEY
    assert cell.value == -222_391


def test_an_accounting_negative_survives_a_dollar_sign_outside_the_paren():
    """`$ (222,391)` is how the corpus writes it, and a pattern expecting
    `(` first misses it and books a credit as a charge."""
    assert T.parse_cell("$ (222,391)").value == -222_391
    assert T.parse_cell("$(208,139)").value == -208_139
    assert T.parse_cell("$ 208,139").value == 208_139


def test_a_lone_comma_is_not_a_number():
    """`[\\d,]+` matches it and then parses the empty string. Found by
    running the first draft of this module over the corpus."""
    assert T.parse_cell(",").kind == T.KIND_TEXT
    assert T.parse_cell(",").value is None


# ── The inversion ────────────────────────────────────────────────────

def test_current_becomes_t12_and_year_one_becomes_the_pro_forma():
    """The headline. Six periods, Current first — `t12` must be the CURRENT
    column, not the last one."""
    by_label, unmapped = _lines(wrap(WICHITA_INTERLEAVED, page=10))
    taxes = by_label["Real Estate Taxes"]
    assert taxes.t12 == 70_000        # Current, not Year 5's 88,000
    assert taxes.cim_yr1 == 80_000    # Year 1, not Current
    assert unmapped == []


def test_no_t3_is_invented_from_the_second_to_last_column():
    """`t3` MEANS trailing-three. It held year 4 of a five-year pro forma,
    and `analysis.financials` reads it as the fallback the moment `t12` is
    absent — so a wrong `t3` is not inert."""
    by_label, _ = _lines(wrap(WICHITA_INTERLEAVED, page=10))
    assert by_label["Real Estate Taxes"].t3 is None


def test_a_footnote_marker_in_the_label_does_not_cost_the_row():
    """`Effective Gross Income1` carries a marker that parses as the number
    1. Column 0 is the label and is never data — scanning it pushes this row
    to seven money cells against six periods and refuses a perfect match."""
    by_label, unmapped = _lines(wrap(WICHITA_INTERLEAVED, page=10))
    assert by_label["Effective Gross Income1"].t12 == 350_000
    assert unmapped == []


def test_a_falling_expense_line_maps_the_same_way():
    """Marketing declines across this pro forma, so the inversion overstated
    some lines and understated others. A uniform direction of error would
    have been easier to notice and is not what the corpus does."""
    by_label, _ = _lines(wrap(COLUMBUS_OFFSET, page=8))
    marketing = by_label["Marketing & Advertising"]
    assert marketing.t12 == 47_000     # Current — the HIGH end here
    assert marketing.cim_yr1 == 33_000


# ── Ordinal, not positional ──────────────────────────────────────────

def test_periods_match_by_order_when_the_header_and_data_indices_differ():
    """`CURRENT` sits at header index 1; its money sits at data index 2.
    Index equality loses all 52 lines of that CIM — more than every keyword
    refinement combined."""
    roles = T.find_header(COLUMBUS_OFFSET)
    assert roles[1] == T.ROLE_CURRENT
    row = COLUMBUS_OFFSET[1]
    assert T.parse_row(row)[1].kind == T.KIND_BLANK
    assert T.parse_row(row)[2].value == 20_000
    assert T.assign_periods(roles, row)[T.ROLE_CURRENT] == 20_000


def test_a_declared_per_sf_column_is_skipped_by_name_not_by_stride():
    """When per-SF figures print bare they parse as money, so the row
    carries twice the period count. The header states WHERE those columns
    are, so nothing has to be inferred from the spacing."""
    header = ['INCOME', 'Current', 'PER SF', 'Year 1', 'PER SF']
    roles = T.find_header([header, ['x', '1', '2', '3', '4']])
    row = ['Real Estate Taxes', '70,000', '0.70', '80,000', '0.80']
    assert T.assign_periods(roles, row) == {
        T.ROLE_CURRENT: 70_000, "year_1": 80_000}


def test_bare_integer_periods_are_read_beside_a_spelled_out_anchor():
    """`Year | T-12 Broker Adjusted | 1 | 2 | 3 | 4 | 5` — fourteen lines
    that a `year N` pattern alone cannot see."""
    by_label, unmapped = _lines(wrap(DALLAS_BARE_INTEGER_YEARS, page=13))
    assert by_label["Real Estate Taxes"].t12 == 72_000
    assert by_label["Real Estate Taxes"].cim_yr1 == 74_000
    assert unmapped == []


def test_a_bare_integer_alone_is_not_a_period():
    """Without a spelled-out neighbour, a `5` in a units column would
    become year five and hand the row a period map it never declared."""
    assert T.header_roles(['Units', '1', '2', '3']) == {}


def test_an_explicit_t3_column_lands_on_t3():
    """No CIM in the corpus states one. The role exists because a column
    the mapper cannot NAME breaks the count and refuses the whole row."""
    by_label, unmapped = _lines(wrap(SYNTHETIC_T3, page=4))
    payroll = by_label["Payroll"]
    assert (payroll.t3, payroll.t12, payroll.cim_yr1) == (66_000, 64_000,
                                                          68_000)
    assert unmapped == []


# ── Refusal, not invention ───────────────────────────────────────────

def test_a_footnote_row_is_not_mistaken_for_a_header():
    """`Current to Year 1` matches a period pattern. A row whose UNROLED
    cells price something is never a header — headers name periods, they do
    not price them.

    The qualifier is the whole rule: `Year 1` and the bare `1` of a
    `1 | 2 | 3` header both parse as money on their own digits, so a flat
    "no money anywhere" test found ZERO headers in all 34 line-bearing
    tables of the corpus.
    """
    assert T.find_header(WICHITA_GROWTH_FOOTNOTES) is None


def test_a_two_panel_assumptions_block_yields_no_expense():
    """The worst number the old code produced, and it was not an inversion.

    `Management Fee` is in column 0 of a two-panel layout; its value is the
    5.00% RATE beside it. The dollar figure further right belongs to
    `Year 1 RE Tax` — a different line, a different panel, a different year.
    The old rule booked that figure as a management-fee EXPENSE, where it
    accumulated on top of the real one.
    """
    by_label, unmapped = _lines(wrap(WICHITA_ASSUMPTION_PANEL, page=11))
    fee = by_label["Management Fee"]
    assert (fee.t12, fee.t3, fee.cim_yr1) == (None, None, None)
    assert [r["label"] for r in unmapped] == ["Management Fee"]
    assert unmapped[0]["page"] == 11


def test_a_key_value_tax_panel_yields_no_second_property_tax_line():
    """`Year 1 Real Estate Tax` matches the expense keywords and states a
    real dollar figure — but the block declares no periods, and the old code
    booked it as a second property-tax line ACCUMULATING on the first."""
    by_label, unmapped = _lines(wrap(COLUMBUS_TAX_PANEL, page=24))
    assert by_label["Year 1 Real Estate Tax"].t12 is None
    assert len(unmapped) == 1


def test_a_headerless_fragment_refuses_rather_than_taking_the_last_column():
    """pdfplumber split this page one row per table, so the header is not
    in scope. `$4.63` is dollars-per-SF and the old `values[-1]` rule booked
    it as this property's total operating expenses."""
    by_label, unmapped = _lines(wrap(KERRVILLE_FRAGMENT_PER_SF, page=12))
    assert by_label["TOTAL OPERATING EXPENSES"].t12 is None
    assert unmapped[0]["reason"] == "no period header on this table"


def test_a_headerless_projection_fragment_refuses_its_final_year():
    """Same page, the five-year strip. `242,076` is Year 5 and was being
    read as the trailing twelve."""
    by_label, unmapped = _lines(wrap(KERRVILLE_FRAGMENT_PROJECTION, page=13))
    assert by_label["TOTAL OPERATING EXPENSES"].t12 is None
    assert len(unmapped) == 1


def test_a_partial_row_is_refused_rather_than_left_aligned():
    """Three money cells under six periods identify no period at all.
    Zipping them onto the first three is the guess this module exists to
    stop; decision 9's lesson is that a wrong number costs more than a
    missing one."""
    roles = T.find_header(WICHITA_INTERLEAVED)
    partial = ['Real Estate Taxes', '', '$ 70,000', '', '', '$ 80,000',
               '', '', '$ 82,000']
    assert T.assign_periods(roles, partial) is None


def test_a_refused_line_still_appears_so_the_run_can_warn_about_it():
    """A dropped line is not neutral. `analysis.financials` books an absent
    expense category at the benchmark FLOOR x NRSF — the LOW end — so a
    missing number RAISES adjusted NOI and pushes the 10% IRR gate toward
    PASS, while Appendix A narrates it as "the CIM did not state a value".
    The refusal log is what lets `engine.run_analysis` say otherwise."""
    by_label, unmapped = _lines(wrap(COLUMBUS_TAX_PANEL, page=24))
    assert "Year 1 Real Estate Tax" in by_label
    assert unmapped and set(unmapped[0]) == {"label", "page", "reason"}


# ── The report is diagnostics, not a field ───────────────────────────

def test_the_refusal_log_does_not_count_against_extraction_confidence():
    """An EMPTY log is the good outcome. Counting it as a field would make
    a clean parse report one more missing field than a broken one."""
    clean = CIMData()
    _parse_financial_tables([wrap(WICHITA_INTERLEAVED, page=10)], clean)
    dirty = CIMData()
    _parse_financial_tables([wrap(COLUMBUS_TAX_PANEL, page=24)], dirty)
    assert clean.unmapped_financial_lines == []
    assert dirty.unmapped_financial_lines
    assert (clean.extraction_report()["total_fields"]
            == dirty.extraction_report()["total_fields"])
    assert "unmapped_financial_lines" not in clean.extraction_report()["missing"]


@pytest.mark.parametrize("rows", [WICHITA_INTERLEAVED, COLUMBUS_OFFSET,
                                  DALLAS_BARE_INTEGER_YEARS])
def test_every_mapped_shape_puts_current_before_year_one(rows):
    """The corpus-wide property, asserted on each shape rather than stated
    once in prose: no table anywhere runs most-recent-LAST."""
    roles = T.find_header(rows)
    sequence = T.period_sequence(roles)
    assert sequence[0] == T.ROLE_CURRENT
    assert sequence[1] == "year_1"


# ── An "income" label is never an expense ────────────────────────────

def _routed(*tables):
    """Parse tables and report which list each label landed in."""
    data = CIMData()
    _parse_financial_tables(list(tables), data)
    return ({ln.label for ln in data.income_lines},
            {ln.label for ln in data.expense_lines})


def test_insurance_income_is_not_an_insurance_expense():
    """The money bug. `expense_keywords` has a bare "insurance" and
    `income_keywords` had no bare "income", so this row matched the
    expense list only and was ADDED to the insurance expense —
    52,674 of pure income on the Wichita CIM.
    """
    income, expense = _routed(wrap(WICHITA_INSURANCE_INCOME))

    assert "Insurance Income" in income
    assert "Insurance Income" not in expense
    # The real expense lines beside it are untouched.
    assert {"Insurance", "Tenant Insurance Expense"} <= expense


def test_noi_lines_leave_expense_lines_too():
    """`Net Operating Income` reached expense_lines through "net
    operating". It maps to no benchmark category, so it moved no money —
    but fixing insurance alone would leave the next reader to work out
    why a NOI line is filed under costs.
    """
    income, expense = _routed(wrap(WICHITA_INSURANCE_INCOME))

    assert "Net Operating Income" in income
    assert "Net Operating Income" not in expense


def test_a_reclassified_line_keeps_its_value_rather_than_being_dropped():
    """Routed, not discarded: nothing downstream PRICES `income_lines`, so
    this cannot move a number, and keeping the line keeps the extraction
    report's count honest."""
    data = CIMData()
    _parse_financial_tables([wrap(WICHITA_INSURANCE_INCOME)], data)

    line = next(ln for ln in data.income_lines if ln.label == "Insurance Income")
    assert line.t12 == 26_337


# ── Statement identity ───────────────────────────────────────────────

def test_each_table_is_its_own_statement():
    """The identity `analysis.financials._map_expense_lines` reconciles
    on. Dallas prints one property's statement on two pages; without a
    per-table ordinal the two are indistinguishable from two real
    properties and their figures were summed.
    """
    data = CIMData()
    _parse_financial_tables(
        [wrap(DALLAS_REPEATED_STATEMENT, page=12),
         wrap(DALLAS_REPEATED_STATEMENT, page=13)], data)

    taxes = [ln for ln in data.expense_lines if ln.label == "Property Taxes"]
    assert len(taxes) == 2
    assert taxes[0].statement != taxes[1].statement
    assert [ln.page for ln in taxes] == [12, 13]


def test_lines_of_one_table_share_a_statement_id():
    """Two different costs on the SAME statement must group together —
    that is the case the accumulation was always right for."""
    data = CIMData()
    _parse_financial_tables([wrap(DALLAS_REPEATED_STATEMENT, page=12)], data)

    ids = {ln.statement for ln in data.expense_lines}
    assert len(ids) == 1


def test_a_section_header_saying_income_is_not_promoted_to_a_line():
    """The trap in the rule above, pinned because it fires silently.

    `WICHITA_INTERLEAVED` opens with a lone `INCOME` cell naming the
    section. It matches no income OR expense keyword, so the gate has
    always skipped it — but a bare `"income" in label` test placed BEFORE
    the gate promotes it to a financial line, which then fails period
    assignment and files a refusal against a table that parsed perfectly.
    The failure is invisible in the numbers and shows up only as a run
    warning nobody can reproduce.
    """
    data = CIMData()
    _parse_financial_tables([wrap(WICHITA_INTERLEAVED, page=10)], data)

    labels = {ln.label for ln in data.income_lines + data.expense_lines}
    assert "INCOME" not in labels
    assert data.unmapped_financial_lines == []
