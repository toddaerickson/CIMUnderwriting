"""Synthetic financial tables in the SHAPES seven real CIMs actually use.

The CIMs themselves are confidential broker packages and gitignored
(`.gitignore` line 21, `*.pdf`), so nothing here is copied from one. What IS
copied is the STRUCTURE — where the header sits, which columns interleave,
how the periods are spelled — because that is what `extract.tables` reads and
what every bug in it was made of. The numbers are invented and internally
consistent; `tests/test_parser_location.py` set the precedent of inlining
shape rather than source.

Each shape below names the CIM it was measured on and the specific failure it
reproduces. A shape with no failure attached does not belong here: this file
exists so the four bugs the corpus taught cannot come back silently, not to
be a gallery of table layouts.
"""

# ── Shape 1 — Current-first with a per-SF interleave (Wichita) ───────
#
# The headline inversion. Six periods run Current → Year 5, and the old
# positional guess took `values[-1]` for `t12`, i.e. the FINAL PROJECTION
# YEAR, and `values[0]` for `cim_yr1`, i.e. the actual. Both backwards, on
# every line in the corpus that carried a number.
#
# The `$n.nnSF` cells are why the guess could not simply be re-indexed: they
# drop out of a naive currency parse, so `values[i]` stops corresponding to
# column `i` and no header map can sit on top of the compacted list.
WICHITA_INTERLEAVED = [
    ['INCOME', '', 'Current', '', '', 'Year 1', '', '', 'Year 2', '',
     '', 'Year 3', '', '', 'Year 4', '', '', 'Year 5', ''],
    ['Gross Potential Rental Income', '', '$ 500,000', '$5.00SF',
     '', '$ 550,000', '$5.50SF', '', '$ 600,000', '$6.00SF',
     '', '$ 650,000', '$6.50SF', '', '$ 700,000', '$7.00SF',
     '', '$ 750,000', '$7.50SF'],
    ['Real Estate Taxes', '', '$ 70,000', '$0.70SF',
     '', '$ 80,000', '$0.80SF', '', '$ 82,000', '$0.82SF',
     '', '$ 84,000', '$0.84SF', '', '$ 86,000', '$0.86SF',
     '', '$ 88,000', '$0.88SF'],
    # Percent, amount and per-SF in one row: the amount is the figure, the
    # percent is a ratio the row already states in dollars. Also the
    # accounting negative with the `$` OUTSIDE the parenthesis.
    ['Physical Vacancy', '30.00%', '$ (150,000)', '$1.50SF',
     '20.00%', '$ (110,000)', '$1.10SF', '15.00%', '$ (90,000)', '$0.90SF',
     '12.00%', '$ (78,000)', '$0.78SF', '11.00%', '$ (77,000)', '$0.77SF',
     '10.00%', '$ (75,000)', '$0.75SF'],
    # A footnote marker in the LABEL parses as the number 1. Scanning column
    # zero pushes this row to seven money cells against six periods and
    # refuses a line that maps perfectly.
    ['Effective Gross Income1', '', '$ 350,000', '$3.50SF',
     '', '$ 440,000', '$4.40SF', '', '$ 510,000', '$5.10SF',
     '', '$ 572,000', '$5.72SF', '', '$ 623,000', '$6.23SF',
     '', '$ 675,000', '$6.75SF'],
]

#: The same CIM's assumptions panel, three pages later — TWO independent
#: two-column panels sharing one table. The label `Management Fee` sits in
#: column 0 and its value is the RATE in column 1; the dollar figure in
#: column 5 belongs to `Year 1 RE Tax`, a different line of a different
#: panel. The old code booked that $91,698-shaped figure as a management-fee
#: EXPENSE. Nothing here names a period, so the whole table must be refused.
WICHITA_ASSUMPTION_PANEL = [
    ['Expense Assumptions', '', '', 'Current RE Tax Expense', '', '$70,000'],
    ['Expense Growth', '2.50%', '', 'Year 1 Market/Assessed Val',
     '', '$3,000,000/$800,000'],
    ['Management Fee', '5.00%', '', 'Year 1 RE Tax', '', '$80,000'],
]

#: And its growth-rate footnote block, which is the header FALSE POSITIVE:
#: `Current to Year 1` matches a period pattern and would hand every row
#: below it a period map built from a footnote.
WICHITA_GROWTH_FOOTNOTES = [
    ['Gross Potential Rent Growth', '', '', 'Comments', '', ''],
    ['Current to Year 1', '9.63%', '', '1.', 'Pro Forma Taxes are increased',
     ''],
    ['Year 1 to Year 2', '10.06%', '', '', '', ''],
]

# ── Shape 2 — header/data index OFFSET (Columbus) ────────────────────
#
# `CURRENT` is at header index 1 and its money is at data index 2. Every one
# of that CIM's 52 lines fails under index equality, which is the intuitive
# design and the one the evidence rejects. Matching the header's period
# SEQUENCE against the row's money cells in order maps all of them.
COLUMBUS_OFFSET = [
    ['EXPENSES', 'CURRENT', '', '', 'YEAR 1', '', '', 'YEAR 2', '', '',
     'YEAR 3', '', '', 'YEAR 4', '', '', 'YEAR 5', '', ''],
    ['Insurance', '', '$20,000', '$0.20SF', '', '$20,500', '$0.21SF',
     '', '$21,000', '$0.21SF', '', '$21,500', '$0.22SF',
     '', '$22,000', '$0.22SF', '', '$22,500', '$0.23SF'],
    # Marketing FALLS across the pro forma, so the inversion overstated some
    # lines and understated others — a fixed sign of error would have been
    # easier to spot and is not what the corpus does.
    ['Marketing & Advertising', '', '$47,000', '$0.47SF',
     '', '$33,000', '$0.33SF', '', '$30,000', '$0.30SF',
     '', '$28,000', '$0.28SF', '', '$25,000', '$0.25SF',
     '', '$21,000', '$0.21SF'],
]

#: The same CIM's property-tax detail block: a key/value panel with no
#: periods at all. `Year 1 Real Estate Tax` matches the expense keywords, and
#: the old code booked it as a second property-tax line that ACCUMULATED on
#: top of the real one.
COLUMBUS_TAX_PANEL = [
    ['Current Market Value', '$3,600,000'],
    ['Current Assessed Value', '$1,280,000'],
    ['Current Tax Rate', '4.84%'],
    ['Year 1 Real Estate Tax', '$67,000'],
]

# ── Shape 3 — bare-integer period headers (Dallas) ───────────────────
#
# The periods are spelled `1 | 2 | 3 | 4 | 5` beside one spelled-out anchor.
# A `year N` pattern alone cannot see them. Requiring the spelled-out
# neighbour is what keeps a bare `5` in a units column from becoming a
# projection year.
DALLAS_BARE_INTEGER_YEARS = [
    ['Year', 'T-12 Broker Adjusted', '1', '2', '3', '4', '5'],
    ['Real Estate Taxes', '72,000', '74,000', '76,000', '78,000', '80,000',
     '82,000'],
    ['Insurance', '28,000', '28,700', '29,400', '30,100', '30,800', '31,500'],
]

# ── Shape 4 — headerless fragments (Kerrville) ───────────────────────
#
# pdfplumber split this page so that each ROW became its own single-row
# table, leaving the header in a table of its own. There is no header to
# read, so nothing may be assigned.
#
# The first fragment is why that refusal MATTERS rather than merely being
# correct: `$4.63` is dollars-per-SF, and the old `values[-1]` rule booked
# it as this property's total operating expenses. The second is a five-year
# pro forma whose last column is Year 5.
KERRVILLE_FRAGMENT_PER_SF = [
    ['TOTAL OPERATING EXPENSES', '211,591', '$4.63'],
]
KERRVILLE_FRAGMENT_PROJECTION = [
    ['TOTAL OPERATING EXPENSES', '220,464', '225,659', '230,989', '236,460',
     '242,076'],
]

# ── Shape 5 — an explicit T-3 column ─────────────────────────────────
#
# NOT measured: no CIM in the corpus states one. It is here because
# `FinancialLine.t3` exists and has always MEANT trailing-three, while what
# it HELD was column N−1 — year 4 of a five-year pro forma. A shape the
# mapper cannot name is a shape it refuses wholesale, so the role is
# exercised here rather than discovered on a live deal.
SYNTHETIC_T3 = [
    ['EXPENSES', 'T-3 Annualized', 'T-12', 'Year 1'],
    ['Payroll', '66,000', '64,000', '68,000'],
]


def wrap(rows, page=1):
    """One table in the shape `extract.pdf_reader.extract_pdf` returns."""
    return {"page": page, "data": rows}
