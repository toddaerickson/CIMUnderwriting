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

A 16-mutant sweep over the new rules leaves THREE alive, and all three
are alive because the thing they mutate does no work — worth writing
down, because the natural response to a survivor is to add a test, and a
test that cannot fail is worse than the gap it was meant to close:

- Dropping the `^` from `_NOI_ROW_RE`. `_noi_candidates` reaches it
  through `re.match`, which anchors at position 0 already. Butler's
  mid-line NOI is refused by the `.match`, not by the `^`.
- Widening `_FIN_LABEL_GAP_RE`'s character classes to admit newlines.
  `\s` matches a newline and always did; what stops the label reaching
  across one is that `_noi_candidates` hands it a single LINE.
- Dropping the `SF`/`PSF` suffix test in `_fin_figures`. Every per-SF
  figure in the corpus is under $14 and the plausibility floor is
  $1,000, so no input distinguishes them. It is a redundant guard held
  on purpose: the floor is a measurement that could drift, the suffix is
  the deck's own statement of what the column is.
"""
import pytest

from extract.parser import (
    MIN_PLAUSIBLE_STATEMENT_FIGURE,
    CIMData,
    _noi_candidates,
    _fin_header_columns,
    _fin_header_line,
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
    """`T-12 ACTUAL CURRENT T-6` names two periods that have both already
    happened, both explicitly. A statement that cannot say which column is
    THE actual has not stated one, so it yields nothing rather than the
    first.

    This fixture used to read `T-12 ACTUAL 2025`, and that is now a
    DIFFERENT case with a different answer — see the test below. Two
    EXPLICIT trailing columns is the shape that still refuses, and the
    `CURRENT T-6` here is separated from `T-12 ACTUAL` by the word
    `ACTUAL`, which is what stops the continuation rule folding the two
    into one column.
    """
    text = ("T-12 ACTUAL CURRENT T-6\n"
            "Net Operating Income $132,994 $249,950\n")
    assert noi(text) is None


def test_an_explicit_trailing_column_outranks_a_bare_year_beside_it():
    """`2024 TRAILING 12 MO 2025` — Coors — reads the middle column.

    This REFINES #96, which folded a bare calendar year into 'trailing'
    and so refused this deck for stating three actuals. The deck states
    one: it named the middle column, and the years either side are the
    comparatives. Refusing here was not a safe default, because Coors then
    priced its expenses off a stray `TRAILING 12 MO` fragment on another
    line and took the 2024 column — $133,411 against the $160,878 its own
    statement puts under the column it labelled.

    The deck's own arithmetic is the witness that this is the right
    column, and it ties to the dollar across all three fields:
    $632,408 − $160,878 = $471,530, the NOI on the line below.
    """
    text = ("INCOME 2024 TRAILING 12 MO 2025\n"
            "Net Operating Income $448,341 $471,530 $523,895\n")
    assert noi(text) == 471_530


def test_bare_years_alone_still_refuse_with_no_explicit_column_to_prefer():
    """The other half of the same rule, and the reason the preference is
    not a licence to guess: with nothing but calendar years, Belton's
    `2024 2025 2026` names three historical columns and no way to choose,
    so the read still ends."""
    text = ("2024 2025 2026\n"
            "Net Operating Income $132,994 $249,950 $301,400\n")
    assert noi(text) is None


def test_an_ambiguous_actual_does_not_fall_through_to_the_brokers_column():
    """`T-12 CURRENT T-6 T-3 (ADJ)` is the shape that isolates the refusal
    from the demotion: two unadjusted trailing columns and exactly one
    adjusted. Refusing only the ambiguous TIER would walk down to the
    broker's adjusted figure and report it as the actual — a deck that
    could not say which of its two actuals is current answering with
    neither of them. Ambiguity at any tier ends the read."""
    text = ("T-12 CURRENT T-6 T-3 (ADJ)\n"
            "Net Operating Income $132,994 $249,950 $301,400\n")
    assert noi(text) is None


def test_the_brokers_column_outranks_bare_years_by_convention():
    """`2024 2025 T-3 (ADJ)` reads the broker's column, and this one is a
    CONVENTION rather than a measurement.

    No deck in the corpus states an adjusted trailing column beside bare
    calendar years, so nothing decides which of the two tiers should sit
    higher — swapping them moves not one number on any of the 45 decks,
    measured, not assumed. It is pinned here anyway: an order nothing
    tests is an order the next reader will reverse by accident while
    believing they preserved it, and then a deck that DOES state both
    shape will move silently.

    The order chosen is the one that keeps a column explicitly naming a
    trailing PERIOD above a column named only by a calendar year, which is
    the same instinct the tier above it follows.
    """
    text = ("2024 2025 T-3 (ADJ)\n"
            "Net Operating Income $132,994 $249,950 $301,400\n")
    assert noi(text) == 301_400


def test_a_header_whose_column_count_misses_the_row_refuses():
    """Decatur's `SELLER ANNUALIZED T7 SELLER ANNUALIZED T1` refuses by a
    different route, and which route it is matters when reading a
    refusal: the continuation rule folds `T1` into the column before it,
    so the header declares ONE column against the row's two figures and
    no header matches at all. The ambiguity branch above is never
    reached on this deck — measured, not assumed."""
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


# ── the header's shape, asserted directly ────────────────────────────
#
# A mutation sweep over these rules found four that a whole-text fixture
# cannot observe, because a SECOND guard refuses the same text for its own
# reason and the value never differs. Defence in depth is worth keeping,
# but a rule no test can see is a rule that can be deleted in silence, so
# the boundary rules are also asserted where they are decided.

@pytest.mark.parametrize("header,shape", [
    # a run of calendar years is a run of columns — Belton. They come
    # back as 'year', not 'trailing': a bare year is history, but a deck
    # that names one column `TRAILING 12 MO` beside them has said which of
    # its historical columns is THE actual, and `_fin_statement_candidate`
    # can only honour that if the two kinds stay apart. Belton names no
    # such column, so three of these still refuse.
    ("2024 2025 2026", [("year", False)] * 3),
    # two trailing words in a row are one label — Starkville
    ("CURRENT T-6 PRO FORMA", [("trailing", False), ("projection", False)]),
    # unless the broker's hand separates them — Hammond, Dallas
    ("T-3 T-3 (ADJ)", [("trailing", False), ("trailing", True)]),
    ("T-12 Actual T-12 Broker Adjusted",
     [("trailing", False), ("trailing", True)]),
    # a parenthesised period stamps the column beside it — Dallas, Rowlett
    ("Pro Forma (Year 3)", [("projection", False)]),
    ("T5 (JAN-MAY 2026) PRO FORMA",
     [("trailing", False), ("projection", False)]),
    # a lone year heads a trailing column — LaGrange. The two projections
    # come back flagged `adjusted`, which is imprecise and deliberately
    # left: the flag is read ONLY on trailing columns, so on a projection
    # it names nothing and can move no number. Asserted as it really is
    # rather than as it ought to read, because a test written to the
    # tidier shape would fail the day someone tightened the flag.
    ("2025 MARKET ADJUSTED PRO FORMA",
     [("year", False), ("projection", True), ("projection", True)]),
])
def test_a_header_declares_the_columns_the_corpus_shows_it_declaring(
        header, shape):
    assert _fin_header_columns(header) == shape


@pytest.mark.parametrize("line", [
    # marketing prose — Mesa: `adjacent 10-acre` ends a word in `t`
    # before a number, so a T-N token without a left word boundary fires
    # INSIDE the word
    "The site is paired with an adjacent 10-acre parcel.",
    # an assumptions row — Kerrville's `Year 1 17%` up the same page as
    # its one real column
    "Year 1 17% Year 2 4% Year 3 4%",
    # a row that PRICES its periods rather than naming them
    "T-12 $412,000 PRO FORMA $530,000",
])
def test_these_lines_are_not_period_headers(line):
    assert not _fin_header_line(line)


def test_prose_that_survives_the_header_test_is_refused_downstream():
    """Spicewood's `With all entitlements in place,` IS accepted here —
    `in place` is a genuine trailing word and this predicate has no way to
    know it is a sentence. Nothing rescues that line; what refuses the
    deck is the per-SF requirement on a one-figure row, one layer down.
    Recorded because the two guards read like belt and braces and they are
    not: on this line the braces are load-bearing alone."""
    prose = "With all entitlements in place, delivery is expected in 2027."
    assert _fin_header_line(prose)
    assert noi(prose + "\nNOI: $1,684,438\n") is None


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


@pytest.mark.parametrize("line", [
    "2026 PRO FORMA NOI $341,022",
    "PRO FORMA CURRENT NOI $341,022",
])
def test_a_projection_word_vetoes_a_span_that_also_reads_as_trailing(line):
    """The veto only bites where BOTH kinds of word sit in the span —
    everywhere else the label simply matches no basis and yields nothing
    on its own. So these two are the fixtures that hold it: strip the
    veto and `2026 PRO FORMA NOI` books a year-three figure at the
    calendar-year tier, which is Norman's defect exactly."""
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


def test_a_header_does_not_reach_forward_onto_the_next_page():
    """The sharper half of page-scoping: a later page whose own statement
    carries NO header must not borrow the previous page's. Reaching
    forward mints a SECOND rank-1 candidate that disagrees, and two
    disagreeing bests is a refusal — so the deck that stated its trailing
    NOI plainly on page one stops reporting one at all."""
    text = ("T-12 PRO FORMA\n"
            "Net Operating Income $117,779 $301,400\n" + PAGE +
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
    assert MIN_PLAUSIBLE_STATEMENT_FIGURE == 1_000
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
