"""Revenue-side and expense-total column discipline — the four statement
totals `extract.parser._fin_candidates` reads beside the NOI.

Same regime and the same engine as `test_parser_noi.py`: ranked candidates
per line, the best rank must agree on ONE value, refuse over guess. The
LAYOUTS below are real, drawn from the 45 CIMs in CIMs2/ via
`pdf_reader.extract_pdf` — the MNET six-column pro forma with its OpEx
ratio printed ahead of each dollar column, the `Effective Gross Income1`
footnote marker, Coors's `2024TRAILING 12 MO` glue, Starkville's
`2 35,732.16` split number and Hastings's parenthesised cost column are
reproduced exactly, because those are what the parser keys on. Every
FIGURE is invented, per the convention stated in test_parser_pricing.py.

What this replaced took the first regex hit document-wide for each of the
four fields, and the failures were not digit errors:

  ttm_total_revenue   read on 1 of 45 decks — the corpus calls that row
                      `Effective Gross Income`, so the field feeding the
                      BLOCKING income-identity check was simply absent
  ttm_total_expenses  booked the OpEx RATIO as dollars on 5 decks
                      (`Total Operating Expenses 74.9% $ 326,145`)
  ttm_egr             booked a FOOTNOTE MARKER as the value on 3 decks
                      (`Effective Gross Income1 $ 435,701` -> $1.00) and
                      matched `EGI` inside `r-egi-on` on two more
  ttm_gpr             booked a column header's stray digit ($10.00,
                      Dallas)

The whole-corpus measure is the decks' own arithmetic — revenue minus
expenses equals the NOI printed on the line below. Before: 17 tie, 5
contradict, of 22 decks where all three fields were present. After: 20
tie, 0 contradict, of 20. The two decks that left the testable set are
Ocean Springs and Triple T, both of which now REFUSE where they used to
answer; the named costs are in the PR body.

A 16-mutant sweep over the new rules leaves ONE alive, and it is alive
because the thing it mutates does no work — the same survivor
test_parser_noi.py records, for the same reason:

- Dropping the `^` from the expense row pattern. `_fin_candidates`
  reaches every row pattern through `re.match`, which anchors at
  position 0 already, so Kerrville's mid-line
  `… 2.00% TOTAL OPERATING EXPENSES 211,591` is refused by the `.match`
  and not by the `^`. Kept for the reason #96 kept its twin: the anchor
  is what the pattern MEANS, and a reader who saw it missing would
  reasonably conclude mid-line totals were meant to be read.

Two mutants survived a first pass for a better reason — the fixtures
could not fire. Both are recorded in the tests themselves rather than
here (`test_egi_does_not_match_inside_the_word_region`), because a
fixture that cannot distinguish the rule from its absence is the failure
mode this sweep exists to catch, and the next person to widen one of
these tests needs to meet it where they will be standing.
"""
import pytest

from extract.parser import (
    _FIN_EGR_ROWS,
    _FIN_EXP_ROWS,
    _FIN_GPR_ROWS,
    _FIN_REV_ROWS,
    _FIN_EGR_LABELS,
    _FIN_GPR_LABELS,
    _fin_candidates,
    _fin_repair,
    _pick_ranked,
)

PAGE = "\n" + "=" * 60 + "\n"


def egr(text):
    return _pick_ranked(_fin_candidates(text, _FIN_EGR_ROWS, _FIN_EGR_LABELS))


def gpr(text):
    return _pick_ranked(_fin_candidates(text, _FIN_GPR_ROWS, _FIN_GPR_LABELS))


def rev(text):
    return _pick_ranked(_fin_candidates(text, _FIN_REV_ROWS, []))


def exp(text):
    return _pick_ranked(_fin_candidates(text, _FIN_EXP_ROWS, [], True))


# ── the ratio column, which is what the old rule actually read ───────

def test_the_opex_ratio_ahead_of_the_dollar_column_is_not_the_expense():
    """MNET prints `Total Operating Expenses 74.9% $ 326,145 $3.04SF` and
    the old pattern took the 74.9 — a $74.90 total operating expense on
    Wichita, Columbus, Premier, All Purpose and Decatur. The percent is
    dropped as a percent and the per-SF column as a per-SF column, so the
    row yields one figure per DOLLAR column and the header can be matched
    against their count."""
    text = ("EXPENSES Current Year 1\n"
            "Total Operating Expenses 36.8% $ 276,248 $3.94SF "
            "33.9% $ 287,496 $4.10SF\n")
    assert exp(text) == 276_248


def test_a_pro_forma_only_expense_statement_refuses():
    """Triple T heads its only expense statement `END-YEAR 1 / END-YEAR 3
    / PRO FORMA`. The old rule answered $157,805 — the END-YEAR 1 column,
    a projection — and it read as this deck's trailing actual."""
    text = ("EXPENSES % EGI END-YEAR 1 $ / SQ FT % EGI END-YEAR 3 $ / SQ FT "
            "% EGI PRO FORMA $ / SQ FT\n"
            "Total Operating Expenses $157,805 $2.78 $209,672 $3.69 "
            "$223,320 $3.93\n")
    assert exp(text) is None


# ── the footnote marker, in both of its disguises ────────────────────

def test_a_footnote_marker_is_not_the_value():
    """`Effective Gross Income1 $ 435,701` — the superscript lands in the
    text layer as a bare digit and the old pattern read it as the number.
    Three decks reported an effective gross income of $1.00, which made
    vacancy read as ~100% and satisfied the blocking `egr_le_gpr` check
    vacuously."""
    text = ("INCOME Current Year 1\n"
            "Effective Gross Income1 $ 435,701 $4.06SF $ 543,621 $5.07SF\n")
    assert egr(text) == 435_701


def test_a_footnote_marker_does_not_disqualify_the_row_either():
    """The same artifact costs the other way round: with the marker
    welded on, `income\\b` cannot match — `e` to `1` is no word boundary —
    so the real revenue row was skipped and the demoted `Effective Gross
    Rental Income` beside it answered instead. Columbus and All Purpose
    then contradicted their own statements by $76,270 and $34,305."""
    text = ("INCOME Current Year 1\n"
            "Effective Gross Rental Income $ 674,310 $9.62SF "
            "$ 761,266 $10.86SF\n"
            "Effective Gross Income1 $ 750,580 $10.71SF "
            "$ 847,371 $12.09SF\n")
    assert egr(text) == 750_580


def test_effective_gross_rental_income_answers_when_it_is_all_there_is():
    """Demoted, not dropped. Four decks stateS only the rental wording —
    Hastings among them, whose $449,510 its own statement confirms:
    $449,510 - $248,740 = the $200,770 NOI on the line below."""
    text = ("INCOME Current Year 1\n"
            "Effective Rental Income $449,510 $5.77 $517,857 $6.65\n")
    assert egr(text) == 449_510


def test_the_demoted_wording_does_not_answer_for_a_refused_preferred_one():
    """Snapbox FL heads two columns `ENDING JUNE 30, 2026 ENDING JUNE 30,
    2026`, so its `Effective Gross Income` row is ambiguous and yields
    nothing — and the `Effective Gross Rental Income` row on the other
    statement then answered $418,529, which is rental income BEFORE other
    income. The deck says $468,666: $468,666 − $406,315 = the $62,351 NOI
    it prints. Ambiguity at the preferred wording ends the read the same
    way ambiguity within a tier does, or the demotion is a route around a
    refusal — the same defect #96 deleted the derived-NOI fallback for.

    The refusal is the point. `require_underwritable` does not read this
    field, so a None here costs a fill-log entry and an analyst's typing;
    a plausible wrong number costs a blocking identity check that fails
    for a reason nobody can find.
    """
    text = ("INCOME ENDING JUNE 30, 2026 ENDING JUNE 30, 2026 END OF YEAR 3\n"
            "Effective Gross Income $396,964 $5.02SF $468,666 $5.93SF "
            "$1,457,838 $18.45SF\n"
            + PAGE +
            "INCOME T1 YEAR 1 YEAR 2\n"
            "Effective Gross Rental Income $418,529 $702,789 $1,070,206\n")
    assert egr(text) is None


def test_the_growth_row_is_not_the_revenue_row():
    """MNET heads its assumptions block `Effective Gross Income Growth`,
    sometimes with a paragraph glued on. The veto is on the LABEL, where
    the claim is, and not on the figures, where it only happens to be
    safe today."""
    text = ("INCOME Current Year 1\n"
            "Effective Gross Income Growth $ 111,111 $ 222,222\n")
    assert egr(text) is None


# ── the acronyms, which used to match inside English words ───────────

def test_egi_does_not_match_inside_the_word_region():
    """`EGR|EGI` carried no word boundaries, so it matched `REGIONAL MAP`,
    `the region.` and `a $2.5 billion regional economic development
    investment` — which is where Dallas's and Starkville's extracted
    effective gross income came from.

    The fixture has to EARN its failure, and the first version of this
    test did not: prose alone cannot fire the label tier, which needs a
    period qualifier within 44 characters and a figure within 40, so
    dropping the boundaries changed nothing and the mutation survived. A
    sentence carrying a year and a dollar figure around the `R-EGI-ONAL`
    is what actually distinguishes them, and it is an ordinary sentence
    for a market section to contain.

    UPPERCASE, and that is the second thing this fixture had to earn.
    These two acronym patterns are deliberately case-SENSITIVE — an
    `EGI` in capitals is an acronym and a lowercase one is inside a word
    — so a lowercase `regional` is refused by the case rule and cannot
    tell the boundaries apart from their absence. `REGIONAL MAP` is the
    real corpus line, and it is in capitals.
    """
    text = "2025 REGIONAL INVESTMENT SUMMARY $250,000\n"
    assert egr(text) is None


# ── the subtotals a loose middle would admit ─────────────────────────

def test_a_controllable_subtotal_is_not_the_expense_total():
    """Dallas prints `Total Controllable Expenses` and `Total
    Non-Controllable Expenses` directly above `Total Operating Expenses`.
    A middle loose enough to admit them yields three rank-1 candidates
    that disagree, and `_pick_ranked` then refuses a deck that stated its
    expenses plainly."""
    text = ("EXPENSES T-12 Actual T-12 Broker Adjusted\n"
            "Total Controllable Expenses $123,759 $65,006\n"
            "Total Non-Controllable Expenses $137,051 $119,980\n"
            "Total Operating Expenses $260,810 $184,986\n")
    assert exp(text) == 260_810


def test_total_other_income_is_not_total_revenue():
    """`Total Other Income` and `Total Rental Income` are each ONE
    component of the revenue total, not the total. Reading either as the
    total feeds the blocking identity check a number that cannot satisfy
    it."""
    text = ("INCOME T-12 Actual Year 1\n"
            "Total Other Income $18,400 $19,000\n")
    assert rev(text) is None


def test_total_operating_income_is_the_revenue_total():
    """Dallas's revenue row, and the deck confirms it:
    $378,589 - $260,810 = the $117,779 NOI it states."""
    text = ("INCOME T-12 Actual T-12 Broker Adjusted Pro Forma\n"
            "Total Operating Income $378,589 $378,589 $532,635\n")
    assert rev(text) == 378_589


# ── the sign, which is a presentation and not a claim ────────────────

def test_an_expense_printed_as_a_deduction_is_the_same_expense():
    """Hastings prints its expense total BOTH ways in one deck — once
    positive on the summary and once parenthesised in the cash flow.
    Those are one claim in two presentations, but two distinct numbers to
    `_pick_ranked`, which refused the deck for disagreeing with itself."""
    text = ("EXPENSES Current PER SF Year 1 PER SF\n"
            "Total Expenses $248,740 $3.19 $133,330 $1.71\n"
            + PAGE +
            "INCOME Current 2024 2025\n"
            "Total Expenses (248,740) (133,330) (134,747)\n")
    assert exp(text) == 248_740


def test_revenue_keeps_its_sign_where_the_expense_folds_it():
    """`absolute` is set on the expense total ALONE. A revenue or an NOI
    may genuinely be negative — Decatur's trailing NOI is a −$20,974 loss
    — and #96 exists partly to stop that one being read as a profit."""
    text = ("INCOME Current Year 1\n"
            "Total Revenue $(20,974) $410,628\n")
    assert rev(text) == -20_974


# ── the text-layer repairs ───────────────────────────────────────────

@pytest.mark.parametrize("raw,fixed", [
    # a year glued to the word after it — Coors
    ("INCOME 2024TRAILING 12 MO 2025", "INCOME 2024 TRAILING 12 MO 2025"),
    # a space inside the thousands separator — Starkville, Ocean Springs
    ("Gross Potential Rent 1 ,009,440.00", "Gross Potential Rent 1,009,440.00"),
    ("EFFECTIVE GROSS INCOME $ 1 ,029,705", "EFFECTIVE GROSS INCOME $ 1,029,705"),
    # a leading digit split off the rest — Starkville
    ("EFFECTIVE GROSS INCOME 2 35,732.16", "EFFECTIVE GROSS INCOME 235,732.16"),
    # …but two real columns are left alone. The joined leading group would
    # run to five digits, which is what the <=3 rule refuses.
    ("Total Occupied 12 345,678", "Total Occupied 12 345,678"),
    # a plain integer column beside another is not a split number
    ("Total / Wtd Avg 359 147", "Total / Wtd Avg 359 147"),
    # and neither is a decimal tail meeting the next column, which is the
    # shape that appears once the comma rule above has run
    ("GPR 1,009,440.00 1,009,440.00", "GPR 1,009,440.00 1,009,440.00"),
])
def test_the_text_layer_repairs_fix_only_what_they_name(raw, fixed):
    assert _fin_repair(raw) == fixed


def test_a_split_number_would_otherwise_change_the_column_count():
    """The split is not only a wrong VALUE — it is an extra figure, so the
    row no longer matches its header and the whole statement goes unread
    or, worse, matches a different header. Starkville reported a
    $1,009,440 gross potential rent as $9,440."""
    text = ("INCOME CURRENT T-6 YEAR 1 YEAR 2\n"
            "Gross Potential Rent 1 ,009,440.00 1 ,039,723.20 1 ,070,914.90\n")
    assert gpr(text) == 1_009_440


def test_the_glued_year_is_what_lets_the_named_column_be_found():
    """Coors, end to end. Unrepaired, `2024TRAILING` hides the trailing
    token, this header declares two columns against a three-figure row,
    and the search widens upward to a STRAY `TRAILING 12 MO` fragment —
    which declares its columns in a different order and hands back the
    2024 figure."""
    text = ("DISTRIBUTION OF EXPENSES\n"
            "TRAILING 12 MO\n"
            "EXPENSES 2024TRAILING 12 MO 2025\n"
            "Total Operating Expense $133,411 $160,878 $115,105\n")
    assert exp(text) == 160_878


# ── the house rule: disagreement refuses ─────────────────────────────

def test_two_properties_stating_different_totals_refuse():
    """Ocean Springs prints a statement per property and again combined,
    and the three disagree at rank 1. The same designed refusal #95 and
    #96 accepted for Wichita — the analyst enters the figure by hand
    rather than the parser picking whichever printed first."""
    text = ("EXPENSES TRAILING 12 MO ADJUSTED YEAR 1\n"
            "TOTAL EXPENSES $ 288,211 $5.20 $ 311,911 $5.63 $ 329,280 $5.94\n"
            + PAGE +
            "EXPENSES TRAILING 12 MO ADJUSTED YEAR 1\n"
            "TOTAL EXPENSES $ 436,037 $5.12 $ 432,448 $5.08 $ 464,250 $5.45\n")
    assert exp(text) is None


def test_a_mid_line_total_is_not_a_statement_row():
    """Kerrville glues two page columns into `Property Tax Growth Rate
    2.00% TOTAL OPERATING EXPENSES 211,591 $4.63`. The figure there
    happens to be right, and reading it is still refused: the anchor is
    what stops Butler's `Effective Gross Income $250,449 Net Operating
    Income $398,917` being read as either row. A named cost, not an
    oversight — this deck's expense total is now entered by hand."""
    text = ("Property Tax Growth Rate 2.00% "
            "TOTAL OPERATING EXPENSES 211,591 $4.63\n")
    assert exp(text) is None
