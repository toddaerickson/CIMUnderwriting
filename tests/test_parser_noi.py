"""TTM NOI column discipline — extract/parser._noi_candidates.

The LAYOUTS below are the real ones, drawn from the 45 CIMs in CIMs2/ via
pdf_reader.extract_pdf: the three-line header pdfplumber splits Katy's
`T3 (JAN - MAR 2026)` into, Starkville's `CURRENT T-6`, Hammond's
`T-3 T-3 (ADJ)`, the `$ 109,556 $1.02SF` per-SF glue and Butler's
two-column page glue are all reproduced exactly, because those are what
the parser keys on. Every FIGURE is invented, per the convention stated in
test_parser_pricing.py: a property's NOI is a commercial term of a live
deal and the parser cannot tell a real six-digit number from a made-up one.

What this replaced took the first regex hit document-wide and scored, over
30 labeled decks, 18 correct / 6 WRONG / 3 hallucinated — and none of the
six were digit errors. Every test below names the deck it is holding shut.
The whole-corpus score is 28 correct / 14 refused / 0 wrong / 0
hallucinated; the three misses are named in the PR body.
"""
import pytest

from extract.parser import (
    MIN_PLAUSIBLE_NOI_FIGURE,
    CIMData,
    _noi_candidates,
    _parse_financials,
    _pick_ranked,
)

PAGE = "\n" + "=" * 60 + "\n"


def noi(text):
    """What the parser books as ttm_noi for this text, or None."""
    return _pick_ranked(_noi_candidates(text))


# ── tier 1: a statement column the header NAMES as trailing ──────────

def test_the_named_trailing_column_is_read_not_the_first_one():
    """Coors states its trailing column SECOND. Reading position rather
    than the header is how a pro forma gets booked as history."""
    text = ("PRO FORMA T-12 ACTUAL\n"
            "Net Operating Income $612,400 $471,530\n")
    assert noi(text) == 471_530


def test_a_year_three_column_is_never_taken_as_trailing():
    """Decatur: the old rule booked its year-three pro forma $341,022 over
    an actual trailing LOSS, and Norman its year three over $774,964."""
    text = ("CURRENT YEAR 1 YEAR 3\n"
            "Net Operating Income $88,140 $210,500 $341,022\n")
    assert noi(text) == 88_140


def test_a_statement_with_two_trailing_columns_and_no_tiebreak_refuses():
    """Decatur's other page heads `SELLER ANNUALIZED T7 SELLER ANNUALIZED
    T1`. A statement that cannot say which column is the actual has not
    stated one."""
    text = ("SELLER ANNUALIZED T7 SELLER ANNUALIZED T1\n"
            "Net Operating Income $132,994 $249,950\n")
    assert noi(text) is None


def test_a_header_naming_no_trailing_column_refuses():
    """Belton heads three end-of-year pro formas. Its hallucinated
    $77,037.29 came from the middle one."""
    text = ("PRO FORMA YEAR ONE PRO FORMA YEAR TWO PRO FORMA YEAR THREE\n"
            "Net Operating Income $60,120 $77,037 $94,880\n")
    assert noi(text) is None


def test_a_run_of_calendar_years_is_a_run_of_columns():
    """Belton's other layout: `2024 2025 2026` names THREE trailing
    columns, so it refuses. It reached one column — and so a value —
    while the `CURRENT T-6` continuation rule collapsed the run."""
    text = ("2024 2025 2026\n"
            "Net Operating Income $60,120 $77,037 $94,880\n")
    assert noi(text) is None


def test_a_lone_calendar_year_heads_a_trailing_column():
    """LaGrange heads its trailing column `2025`, beside MARKET ADJUSTED
    and PRO FORMA. Counting those four digits as money rejected the
    header, and with it the deck that states its basis most plainly."""
    text = ("A-PRIME SELF STORAGE 2025 MARKET ADJUSTED PRO FORMA\n"
            "Net Operating Income $318,400 $362,900 $410,250\n")
    assert noi(text) == 318_400


# ── column boundaries: where one header word ends and the next begins ─

def test_two_trailing_words_in_a_row_are_one_column_label():
    """Starkville heads its single column `CURRENT T-6`. Read as two
    columns it no longer matches the row's one figure."""
    text = ("CURRENT T-6 PRO FORMA\n"
            "Net Operating Income $156,128 $204,900\n")
    assert noi(text) == 156_128


def test_an_adjustment_marker_opens_a_second_trailing_column():
    """Hammond's `T-3 T-3 (ADJ)` and Dallas's `T-12 Actual T-12 Broker
    Adjusted` are two real columns each, differing only by the broker's
    hand — so the continuation rule must not merge them."""
    text = ("T-3 T-3 (ADJ) PRO FORMA\n"
            "Net Operating Income $117,779 $193,603 $240,000\n")
    assert noi(text) == 117_779


def test_the_unadjusted_trailing_column_beats_the_brokers_own():
    """US Storage Dallas: the old rule took `T-12 Broker Adjusted`
    ($193,603) over the `T-12 Actual` standing beside it ($117,779)."""
    text = ("T-12 Actual T-12 Broker Adjusted Pro Forma (Year 3)\n"
            "Net Operating Income $117,779 $193,603 $301,400\n")
    assert noi(text) == 117_779


def test_a_broker_adjusted_column_is_demoted_not_dropped():
    """Kerrville states `ADJ. T - 12` and no unadjusted trailing column
    anywhere in the deck, and the golden label takes it."""
    text = ("REVENUE ADJ. T - 12 $/SF\n"
            "NET OPERATING INCOME 251,859 $5.51\n")
    assert noi(text) == 251_859


def test_a_parenthesised_period_is_a_date_stamp_not_a_column():
    """`Pro Forma (Year 3)` is ONE column. Counting the parenthetical made
    Dallas's header declare four columns against three figures, no header
    matched, and the label tier's broker-adjusted headline won."""
    text = ("T-12 Actual Pro Forma (Year 3)\n"
            "Net Operating Income $117,779 $301,400\n")
    assert noi(text) == 117_779


def test_a_closing_parenthesis_counts_as_much_as_an_opening_one():
    """Katy's `T3 (JAN - MAR 2026)` arrives split across text lines, so
    the year that stamps the column is on a line of its own and only its
    trailing `)` marks it."""
    text = ("T3 (JAN - MAR\n"
            "2026) PRO FORMA\n"
            "Net Operating Income $204,880 $260,000\n")
    assert noi(text) == 204_880


# ── which line may be a header at all ────────────────────────────────

def test_a_line_that_prices_a_period_is_not_a_header():
    """`extract.tables.find_header`'s rule, applied here: headers name
    periods, they do not price them."""
    text = ("T-12 $412,000 PRO FORMA $530,000\n"
            "Net Operating Income $117,779 $301,400\n")
    assert noi(text) is None


def test_an_assumptions_row_with_percent_values_is_not_a_header():
    """Kerrville prints `Year 1 17%` up the same page as its statement.
    Read as headers, those rows bury its one real column under five
    phantom years."""
    text = ("Year 1 17% Year 2 4%\n"
            "REVENUE ADJ. T - 12 $/SF\n"
            "NET OPERATING INCOME 251,859 $5.51\n")
    assert noi(text) == 251_859


def test_the_nearest_matching_header_governs():
    """2026 Abilene declares the same three columns over its INCOME block
    and again over its EXPENSES block. Widening past the nearer one reads
    a row under the wrong header."""
    text = ("CURRENT YEAR 1 YEAR 3\n"
            "Total Income $402,110 $455,000 $520,000\n"
            "CURRENT YEAR 1 YEAR 3\n"
            "Net Operating Income $241,491 $290,000 $355,000\n")
    assert noi(text) == 241_491


def test_marketing_prose_is_not_a_period_header():
    """Mesa and Spicewood are land decks that state no financials. `paired
    with an adjacent 10-acre` ends a word in `t` before a number, and
    `With all entitlements in place,` carries `in place` — both read as
    period headers and both produced a hallucinated trailing NOI."""
    text = ("The site is paired with an adjacent 10-acre parcel.\n"
            "With all entitlements in place, delivery is 2027.\n"
            "NOI: $1,684,438\n")
    assert noi(text) is None


def test_a_single_figure_row_needs_a_header_that_declares_a_per_sf_column():
    """A one-column row carries no ordinal information, so the count match
    that vouches for every other row proves nothing and one stray period
    word above would carry it. The per-SF column is the deck's own
    evidence that this is a statement rather than a paragraph."""
    bare = ("Deliveries are current across the submarket.\n"
            "NET OPERATING INCOME 251,859\n")
    assert noi(bare) is None


# ── tier 3 and 4: a single figure whose LABEL carries its basis ───────

@pytest.mark.parametrize("line,expect", [
    ("TTM NOI $370,770", 370_770),
    ("IN-PLACE NOI $370,770", 370_770),
    ("NOI (Current) $212,361", 212_361),
    ("NOI (CURRENT T-6) $156,128", 156_128),
    ("NOI (Trailing 12 MO) $471,530", 471_530),
    ("Current NOI of $212,361", 212_361),
    ("SELLER ANNUALIZED T1 THRU MAY 31, 2026 NOI $249,950", 249_950),
])
def test_a_trailing_qualified_label_is_read(line, expect):
    assert noi(line) == expect


@pytest.mark.parametrize("line", [
    "PRO FORMA END OF YEAR 3 NOI $341,022",
    "NOI (Year One) $210,500",
    "Stabilized NOI $1,684,438",
    "Projected NOI $530,000",
    "Year 5 NOI $612,400",
])
def test_a_projection_qualified_label_yields_nothing(line):
    assert noi(line) is None


def test_an_unqualified_noi_yields_no_candidate_at_any_tier():
    """The rule that refuses the pro-forma-only decks — Triple T, Belton,
    Spicewood, the Mesa land, Butler. They state no trailing actual, so
    the honest read is None and require_underwritable asks the analyst."""
    assert noi("NOI: $1,684,438") is None
    assert noi("Net Operating Income $398,917") is None


def test_a_calendar_year_label_ranks_below_an_explicit_trailing_one():
    """Coors states both, and its 2025 is the FORWARD year. Ranked level
    rather than ordered by appearance, because the deck prints the year
    figure first."""
    text = "NOI (2025) $523,895\nNOI (Trailing 12 MO) $471,530\n"
    assert noi(text) == 471_530


# ── the statement tiers outrank the label tiers ──────────────────────

def test_a_statement_column_beats_a_headline_label_that_disagrees():
    """Hastings headlines `IN-PLACE NOI $370,770` against a statement
    current column of $200,770; 2026 Abilene's exec summary says
    $212,361 against its own statement's $241,491, which the golden label
    calls an internal conflict and resolves to the statement."""
    text = ("IN-PLACE NOI $370,770" + PAGE +
            "CURRENT PRO FORMA\n"
            "Net Operating Income $200,770 $410,000\n")
    assert noi(text) == 200_770


def test_a_header_governs_only_the_page_it_is_printed_on():
    """The same deck prints its trailing statement on one page and its
    five-year cash flow on the next, whose first column is year one."""
    text = ("T-12 PRO FORMA\n"
            "Net Operating Income $117,779 $301,400\n" + PAGE +
            "YEAR 1 YEAR 2\n"
            "Net Operating Income $301,400 $340,000\n")
    assert noi(text) == 117_779


# ── what counts as a figure ──────────────────────────────────────────

@pytest.mark.parametrize("row", [
    "Net Operating Income $(20,974) $341,022",
    "Net Operating Income -$20,974 $341,022",
])
def test_both_accounting_negatives_are_read_as_losses(row):
    """Decatur's trailing NOI is a LOSS and Affordable McKinney's is
    written the other way round — `$(20,974)` puts the sign inside the
    dollar sign and `-$20,974` outside. A test reading only the character
    before the digits sees a `$` in the second form and books a trailing
    loss as a profit, on the one kind of deck a screen must not get
    backwards."""
    assert noi("T-12 PRO FORMA\n" + row + "\n") == -20_974


def test_a_per_sf_column_is_not_a_money_column():
    """Half the corpus prints `$ 109,556 $1.02SF`. Counted as a column it
    puts every figure after it under the wrong header."""
    text = ("T-12 PRO FORMA\n"
            "Net Operating Income $ 109,556 $1.02SF $180,000 $1.68SF\n")
    assert noi(text) == 109_556


def test_a_percentage_on_a_financial_row_is_not_a_money_column():
    text = ("T-12 PRO FORMA\n"
            "Net Operating Income $109,556 62% $180,000 68%\n")
    assert noi(text) == 109_556


def test_a_figure_under_the_plausibility_floor_is_not_an_noi():
    """Little Rock booked 362 — the UNIT COUNT — off a
    `Current NOI Year 2 NOI` header row. Every per-SF figure in the corpus
    is under $14 and every stated NOI is over $20,000."""
    assert MIN_PLAUSIBLE_NOI_FIGURE == 1_000
    assert noi("Current NOI 362") is None
    assert noi("TTM NOI $362") is None


def test_the_label_may_not_reach_across_a_newline_for_its_figure():
    """HASTINGS booked $5,600,000 — the LIST PRICE — because `NOI[:\\s]*`
    let `\\s` cross a newline from a bare `SALE PRICE NOI` header row."""
    text = "SALE PRICE NOI\n$5,600,000 $370,770\n"
    assert noi(text) is None


def test_an_noi_mid_line_is_not_a_statement_row():
    """Butler's two-column page glue: `Effective Gross Income $250,449 Net
    Operating Income $398,917` — the NOI belongs to a different block, and
    it is the exact line the old first-match rule read as this deck's
    trailing NOI."""
    text = ("T-12 PRO FORMA\n"
            "Effective Gross Income $250,449 Net Operating Income $398,917\n")
    assert noi(text) is None


# ── the field the rest of the model reads ────────────────────────────

def test_the_resolved_value_reaches_ttm_noi():
    data = CIMData()
    _parse_financials("T-12 PRO FORMA\n"
                      "Net Operating Income $117,779 $301,400\n", [], data)
    assert data.ttm_noi == 117_779


def test_a_refusal_leaves_ttm_noi_none_for_require_underwritable():
    """Decision 9's contract: the honest read is None, and
    `analysis.fills.require_underwritable` turns it into a refusal the
    analyst answers by hand — never a screen built on an invented past."""
    data = CIMData()
    _parse_financials("NOI: $1,684,438\n", [], data)
    assert data.ttm_noi is None


def test_revenue_minus_expenses_no_longer_back_fills_a_refused_noi():
    """The `_compute_derived` fallback fired exactly when the ranked
    candidates refused, and both its operands are still read by
    unqualified document-wide first-match regexes — so on a pro-forma-only
    deck it reconstructed a projection's NOI and handed it to the slot
    decision 9 guards."""
    from extract.parser import _compute_derived
    data = CIMData()
    data.ttm_total_revenue, data.ttm_total_expenses = 530_000.0, 188_600.0
    _compute_derived(data)
    assert data.ttm_noi is None
