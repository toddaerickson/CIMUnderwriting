"""NRSF and unit-count extraction — extract/parser._parse_size_occupancy.

The LAYOUTS below are the real ones, drawn from the 45 CIMs in CIMs2/ via
pdf_reader.extract_pdf: the glued `RentableSF`/`#ofUnits` tokens, the `±`
markers, the `Number of Stories 1 Net Rentable SF ±N` run-together and the
per-capita prose are all reproduced exactly, because those are what the
parser keys on. Every FIGURE is invented, per the convention stated in
test_parser_pricing.py: a building's size is a commercial term of a live
deal and the parser cannot tell a real five-digit number from a made-up one.

What this replaced required a unit word after the NRSF label, so the bare
`NRSF: N` and `N NRSF` forms — how the entire MNET family states it — never
matched: 25 of 45 corpus files had no usable NRSF and were refused by
require_underwritable. Worse, the value-first pattern read the number BEFORE
the label, so `Number of Stories 1 Net Rentable SF ±N` booked nrsf = 1 and
ran end-to-end dividing every $/SF benchmark by 1, while `([\\d,]+)\\s*units`
read the same line's NRSF into total_units — the two fields swapped values.
"""
import pytest

from extract.parser import (
    MAX_PLAUSIBLE_NRSF,
    MAX_PLAUSIBLE_UNITS,
    MIN_PLAUSIBLE_NRSF,
    CIMData,
    _parse_size_occupancy,
)


def parse(text):
    data = CIMData()
    _parse_size_occupancy(text, data)
    return data


def nrsf(text):
    return parse(text).nrsf


def units(text):
    return parse(text).total_units


# ── the bare label forms that never matched ──────────────────────────

@pytest.mark.parametrize("text,expect", [
    ("NRSF: 84,375", 84_375),
    ("• 84,375 NRSF", 84_375),
    ("controlled units for a total of 84,375 NRSF. It also", 84_375),
    ("RentableSF 48,762SF", 48_762),          # the glued MNET summary cell
    ("Rentable Square Feet 48,762 Roof R-Panel Metal", 48_762),
    ("Total NRSF 45,680 Square Feet", 45_680),
    ("NRSF 140,845 Effective Gross Income $570,807", 140_845),
    ("Comprised of 434 units and 48,762 rentable-square-feet across", 48_762),
    ("the Property comprises 45,680 rentable square feet", 45_680),
])
def test_nrsf_label_forms_are_read(text, expect):
    assert nrsf(text) == expect


def test_plus_minus_is_tolerated():
    """`Net Rentable SF ±45,755` is the corpus's own marker; the old
    `[~≈]*` tolerated the two lookalikes and not the character actually
    used."""
    assert nrsf("Net Rentable SF ±45,755") == 45_755


# ── the story-count swap ─────────────────────────────────────────────

def test_the_number_before_the_label_is_not_the_building():
    """`... Stories 1 Net Rentable SF ±45,755` — the old value-first pattern
    captured the 1 and this line's real value went unread because of the ±.
    nrsf = 1 then RAN, dividing every $/SF benchmark by 1."""
    text = ("Year Built 2018 Year Expanded 2023 Number of Buildings 4 "
            "Number of Stories 1 Net Rentable SF ±45,755 Total Units 362")
    assert nrsf(text) == 45_755


def test_the_swap_case_lands_both_fields():
    """Same line: nrsf took the story count while total_units took the NRSF.
    Both fields asserted together so the swap cannot half-return."""
    d = parse("Number of Stories 1 Net Rentable SF ±45,755 Total Units 362")
    assert d.nrsf == 45_755
    assert d.total_units == 362


def test_a_story_count_alone_yields_none_not_one():
    """The plausibility floor, stated as its own case so it cannot be removed
    silently: when the only match is the story count, the answer is None —
    which require_underwritable refuses with the right reason — never 1."""
    assert nrsf("Number of Stories 1 Net Rentable SF") is None
    assert MIN_PLAUSIBLE_NRSF > 1


# ── rates are not sizes ──────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "NRSF per capita within 3 miles is 7.13 NRSF. It",
    "TX NRSF/Person: 7.74",
    "GPR/NRSF $12.42 $12.42 $14.28",
    "RENTAL INCOME/NRSF $10.05 $10.05 $12.14",
    "Price/RentableSF $71.78",
    "TOTAL OPERATING EXPENSES $ / SQ FT 4.63",
])
def test_rate_lines_contribute_nothing(text):
    assert nrsf(text) is None


# ── rank beats document order ────────────────────────────────────────

def test_a_labeled_total_outranks_a_cover_flourish():
    """Kerrville states `66,198 NRSF` on the cover and `Total NRSF 45,680
    Square Feet` in the summary table, and the unit-mix TOTALS row agrees
    with the table. The labeled statement wins regardless of order."""
    text = ("SELF-STORAGE INVESTMENT | 66,198 NRSF\n"
            "66,198 NRSF\n"
            "Total NRSF 45,680 Square Feet")
    assert nrsf(text) == 45_680


def test_two_labeled_totals_that_disagree_refuse():
    """Two rank-1 statements of different sizes is a portfolio deck quoting
    per-property figures, or a broken read. Refuse rather than guess."""
    text = "Total NRSF 45,680 Square Feet\nNRSF: 84,375"
    assert nrsf(text) is None


def test_agreeing_statements_are_not_a_disagreement():
    text = "NRSF: 84,375\nNet Rentable SF ±84,375\n84,375 NRSF"
    assert nrsf(text) == 84_375


def test_a_label_that_is_not_rentable_is_not_the_building():
    """`Warehouse SF ±N` sits on the same spec line as the real figure. If a
    bare SF label could match, its value would disagree with the subject's
    and refuse the deck."""
    assert nrsf("Net Rentable SF ±52,300 Warehouse SF ±6,100") == 52_300


# ── sale-comp spec cards ─────────────────────────────────────────────

def test_comp_cards_cannot_refuse_the_subjects_own_statement():
    """The MNET comps section prints one `GrossSF: ... RentableSF: ...` card
    per facility — six of them under a subject stated three times. Their
    RentableSF is some OTHER building's; without the demotion the
    disagreement rule would refuse the whole deck."""
    text = ("Net Rentable Square Feet 71,204\n"
            "Net Rentable Square Feet 71,204\n"
            "GrossSF: 64,850SF RentableSF: 61,607 SF\n"
            "GrossSF: 113,428SF RentableSF: 96,413 SF\n"
            "GrossSF: 163,200SF RentableSF: 122,400 SF")
    assert nrsf(text) == 71_204


def test_a_subject_only_card_still_reads():
    """Demoted, not excluded: a deck whose only size statement is its own
    spec card keeps its figure."""
    assert nrsf("LotSize: 5.03Acres RentableSF: 51,204SF") == 51_204


def test_cards_alone_that_disagree_still_refuse():
    text = ("GrossSF: 64,850SF RentableSF: 61,607 SF\n"
            "GrossSF: 113,428SF RentableSF: 96,413 SF")
    assert nrsf(text) is None


def test_a_card_wrapped_across_two_lines_is_still_a_card():
    """One deck's cards put `GrossSF` and `RentableSF` on separate text
    lines. The demotion carries one line forward, or the card's per-facility
    figures out-vote the subject's own statement into a refusal."""
    text = ("• 36,214 Net Rentable Square Feet\n"
            "Net Rentable Square Feet 36,214\n"
            "Lot Size: 3Acres GrossSF: 4,079SF\n"
            "RentableSF: 3,671 SF\n"
            "Lot Size: 6.62 Acres GrossSF: 40,000SF\n"
            "RentableSF: 35,000SF")
    assert nrsf(text) == 36_214


# ── a projection is not the facility ─────────────────────────────────

@pytest.mark.parametrize("text", [
    "• 5,400 NRSF expansion underway",
    "1. ProForma includes rent from the newly approved NRSF "
    "(1 building with 5400 NRSF Drive -Up Self -Storage)",
])
def test_an_expansion_figure_is_not_the_building(text):
    """HASTINGS states its expansion's size and never its own: booking the
    5,400 makes a ~37,000 SF facility one-seventh its size. None is right —
    require_underwritable refuses and the analyst enters it by hand."""
    assert nrsf(text) is None


@pytest.mark.parametrize("text", [
    "City, State, Zip Spicewood, TX Combined Future Unit Count 84-96 "
    "(incl 40 in-place + 12 pad-ready)",
    # the text layer drops the ligature (`Poten al` = `Potential`), so no
    # vocabulary word survives — the range lookbehind does the refusing
    "Street Address 355 Exeter Rd Poten al 40-56 Storage Units",
    "Embedded upside includes the opportunity to develop up to 12 units.",
    "• Recent Expansion At The North Property – 44 Units Added In August 2025",
])
def test_a_future_unit_count_is_not_the_unit_count(text):
    assert units(text) is None


def test_a_range_top_is_not_the_unit_count():
    """`40-56 units, doubling the asset's long-term earning capacity` — the
    one projection line whose segment carries NO qualifier word at all. The
    value-first lookbehind refuses the range's upper bound instead: a range
    is a pitch, and its top is nobody's unit count."""
    assert units("40-56 units, doubling the asset's long-term earning "
                 "capacity. Permits will") is None


def test_the_projection_refusal_is_scoped_to_the_bullet_segment():
    """A portfolio deck states its real total and its expansion pitch on ONE
    line, bullet-separated. Line-scoped refusal would throw away the total."""
    text = ("Total Net Rentable Square Feet 106,914 • Room for Future "
            "Expansion Opportunities at Both Locations")
    assert nrsf(text) == 106_914


# ── plausibility band ────────────────────────────────────────────────

def test_the_band_is_a_band():
    assert nrsf("NRSF: 120") is None
    assert nrsf("NRSF: 12,000,000,000") is None
    assert MIN_PLAUSIBLE_NRSF <= 5_400          # smallest real facility seen
    assert MAX_PLAUSIBLE_NRSF >= 410_467        # largest real facility seen


# ── total units ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expect", [
    ("Total Units 362 Foundation Slab-on-Grade Concrete", 362),
    ("#of Units 434", 434),
    ("#ofUnits 434", 434),                      # the glued MNET header cell
    ("Unit Count: 96", 96),
    ("Comprised of 434 units and 48,762 rentable-square-feet across", 434),
    ("359 storage units", 359),
    ("16 units", 16),                           # a real hangar deck is tiny
])
def test_unit_labels_are_read(text, expect):
    assert units(text) == expect


@pytest.mark.parametrize("text", [
    "2PersonUnits 32.1% 32.9% 33.4%",            # demographics table
    "2025EstimateTotalOccupiedUnits 26,416 41,804 52,454",
    "OCCUPIED UNITS 376",                        # occupied ≠ total
    "includes 5 uncovered parking spaces near the units",
])
def test_lookalike_unit_rows_are_refused(text):
    assert units(text) is None


def test_an_sf_figure_cannot_be_a_unit_count():
    """Lone Star booked 84,375 "units" — an SF figure — and Wichita 55,736.
    The cap refuses anything bigger than a real facility's unit count."""
    assert units("units for a total of 84,375 NRSF") is None
    assert MAX_PLAUSIBLE_UNITS < 45_755


def test_the_units_label_outranks_a_value_first_read():
    """`Net Rentable SF ±45,755 Total Units 362`: value-first sees
    `45,755 Total Units`. The label-first 362 must win by rank — the band
    alone cannot save a mid-band wrong value."""
    assert units("Net Rentable SF ±4,575 Total Units 362") == 362


def test_a_subtype_count_does_not_refuse_the_total():
    """One deck states `Number of Units: N total` and, under the SAME label,
    `Number of Units: M non-climate controlled ... units`. Read flat those
    are two rank-1 statements that disagree, and the deck refuses over a row
    that never claimed to be the total."""
    text = ("Number of Units: 242 total\n"
            "Number of Units: 69 non-climate controlled self-storage units, "
            "All sized 10x20")
    assert units(text) == 242


def test_a_subtype_only_deck_still_reads():
    """Demoted, not dropped — the spec card's bargain."""
    assert units("Number of Units: 69 climate controlled") == 69


@pytest.mark.parametrize("distant", [
    # Crowley: the subtype match begins at offset 30 past the value match —
    # the window's exact edge, so this line is the measured ceiling.
    "Constructed in 2016, the property consists of 379 units and offers "
    "a balanced mix of climate-controlled",
    # Kerrville: an amenity sentence with the vocabulary 70 characters out.
    "across 379 units. The facility offers a well-balanced mix of "
    "drive-up non-climate and climate-controlled storage",
])
def test_a_distant_subtype_mention_does_not_demote_the_count(distant):
    """The qualifier demotes only when it sits ON the value. Both layouts
    put climate-controlled vocabulary after a line's genuine count at a
    distance — amenity prose, not a breakdown row. A window wide enough to
    reach it would demote the real count out of rank 2, and this fixture is
    built to catch exactly that: two bare prose counts disagreeing must
    refuse, and a wrongly demoted candidate leaves a lone survivor that
    wins instead."""
    assert units("A total of 412 storage units\n" + distant) is None


def test_the_same_layout_agreeing_still_reads():
    """The refusal above belongs to the disagreement, not the layouts."""
    assert units("A total of 379 storage units\n"
                 "across 379 units. The facility offers a well-balanced mix "
                 "of drive-up non-climate and climate-controlled storage") == 379


def test_units_disagreement_refuses():
    assert units("Total Units 362\nUnit Count: 719") is None


def test_nothing_stated_yields_none():
    d = parse("SELF STORAGE OFFERING\nCall broker for details.")
    assert d.nrsf is None
    assert d.total_units is None


# ── the fields around the rewrite still parse ────────────────────────

def test_occupancy_and_cc_are_untouched():
    d = parse("Physical Occupancy: 91.5%\nEconomic Occupancy: 78.2%\n"
              "35% climate-controlled\nNRSF: 45,680")
    assert d.physical_occupancy == pytest.approx(0.915)
    assert d.economic_occupancy == pytest.approx(0.782)
    assert d.cc_pct == pytest.approx(0.35)
    assert d.nrsf == 45_680


# ── occupancy: the basis is the datum ────────────────────────────────
# Same convention as above: the LAYOUTS are the corpus's own — the
# stat-block dates, the slash duals, the glued labels, the banner — and
# every FIGURE is invented. An occupancy is a commercial term of a live
# deal like any other.

def test_an_economic_figure_never_lands_in_physical():
    """The old optional `(?:physical\\s+)?` prefix meant an economic-only
    deck filled BOTH fields with the same number."""
    d = parse("ECONOMIC OCCUPANCY: 64%")
    assert d.physical_occupancy is None
    assert d.economic_occupancy == pytest.approx(0.64)


def test_the_stat_block_reads_sf_physical_over_units_and_economic():
    """The three-line stat block states every basis; SF-physical is the
    demand gate's number and the golden labels' choice."""
    d = parse("PHYSICAL OCCUPANCY (SQ. FT.): 83%\n"
              "PHYSICAL OCCUPANCY (UNITS): 77%\n"
              "ECONOMIC OCCUPANCY: 71%")
    assert d.physical_occupancy == pytest.approx(0.83)
    assert d.economic_occupancy == pytest.approx(0.71)


@pytest.mark.parametrize("text,field,expect", [
    ("PHYSICAL OCCUPANCY (SF) AS OF APRIL 30, 2026 87.60%",
     "physical_occupancy", 0.876),
    ("CURRENT PHYSICAL OCCUPANCY (SF) AS OF YEAR END 2025 68.31%",
     "physical_occupancy", 0.6831),
    ("CURRENT ECONOMIC OCCUPANCY THRU MAY 31, 2026 23.05%",
     "economic_occupancy", 0.2305),
    ("Economic Occupancy(%) 67.47%", "economic_occupancy", 0.6747),
])
def test_the_stat_block_dates_are_crossed(text, field, expect):
    """This family parsed to None wholesale before — the old pattern could
    cross neither the basis parenthetical nor the AS OF/THRU date. `YEAR
    END 2025` must not trip the projection veto, which wants a digit
    directly after "year"."""
    assert getattr(parse(text), field) == pytest.approx(expect)


def test_the_economic_stat_block_fills_economic_only():
    assert parse("CURRENT ECONOMIC OCCUPANCY THRU MAY 31, 2026 23.05%"
                 ).physical_occupancy is None


def test_a_label_does_not_adopt_the_next_lines_number():
    d = parse("Occupancy\n35.5%")
    assert d.physical_occupancy is None
    assert d.economic_occupancy is None


def test_slash_dual_labels_bind_positionally():
    """Read flat, the physical label alone binds the FIRST value and books
    the economic figure as physical at rank 1."""
    d = parse("Economic Occupancy / Physical Occupancy "
              "(March 31, 2026) 51% / 78%")
    assert d.physical_occupancy == pytest.approx(0.78)
    assert d.economic_occupancy == pytest.approx(0.51)


def test_the_sf_unit_dual_prefers_the_sf_figure():
    assert parse("SF Occupancy / Unit Occupancy 93.0% / 90.2%"
                 ).physical_occupancy == pytest.approx(0.93)


def test_bare_section_figures_that_disagree_refuse():
    """Per-section stat rows (an RV/boat section beside the main one) are
    bare-tier candidates: two values, no basis, no winner."""
    d = parse("Occupied Tenants: 281 Occupancy: 91.15%\nOccupancy: 64.29%")
    assert d.physical_occupancy is None


@pytest.mark.parametrize("text,expect", [
    ("OCCUPANCY 47%", 0.47),
    ("Occupancy: ±63% total occupancy (expansion is in lease up)", 0.63),
    ("Occupancy at Sale: 71.4% Occupied", 0.714),
    ("614 Units 53% Total Occupancy", 0.53),
    ("OCCUPANCY 100.00%", 1.0),
])
def test_a_bare_occupancy_is_still_read_as_physical_only(text, expect):
    """The unqualified number a broker quotes is almost always physical
    (CLAUDE.md); it is never read as economic."""
    d = parse(text)
    assert d.physical_occupancy == pytest.approx(expect)
    assert d.economic_occupancy is None


def test_an_explicit_basis_beats_a_bare_stat():
    d = parse("OCCUPANCY 21.08%\n"
              "Currently 47% Physically Occupied as of July 1")
    assert d.physical_occupancy == pytest.approx(0.47)


@pytest.mark.parametrize("text,field,expect", [
    ("the Property was 91.3% physically occupied by square footage",
     "physical_occupancy", 0.913),
    ("68% Physical Occupancy", "physical_occupancy", 0.68),
    ("57% Economic Occupancy", "economic_occupancy", 0.57),
    ("Currently 22% Economically Occupied on a Trailing 6 Months",
     "economic_occupancy", 0.22),
])
def test_percent_before_the_words_reads(text, field, expect):
    assert getattr(parse(text), field) == pytest.approx(expect)


def test_the_word_physical_outranks_the_sf_basis_label():
    """One deck attaches "physical" to its unit figure and quotes the SF
    basis under a basis-only label; the golden labels take the word as the
    broker's own claim of basis."""
    d = parse("Currently Sits at 64.07% Physical Occupancy\n"
              "Square Foot Occupancy 71.18%\n"
              "Unit Occupancy 64.07%\n"
              "Economic Occupancy 68.31%")
    assert d.physical_occupancy == pytest.approx(0.6407)
    assert d.economic_occupancy == pytest.approx(0.6831)


def test_sf_outranks_units_and_a_unit_only_deck_still_reads():
    assert parse("Square Foot Occupancy 51%\nUnit Occupancy 47%"
                 ).physical_occupancy == pytest.approx(0.51)
    assert parse("Unit Occupancy 93%"
                 ).physical_occupancy == pytest.approx(0.93)


def test_a_parenthetical_on_a_bare_label_is_tolerated_not_classified():
    """`Occupancy (Units) N%` still reads at the last-resort tier — the
    shared tail crosses the parenthetical — and any explicit basis
    outranks it. (The bare tier does NOT classify its own parens: every
    corpus `(UNITS)` sits on a `PHYSICAL OCCUPANCY` label.)"""
    assert parse("Occupancy (Units) 42.9%"
                 ).physical_occupancy == pytest.approx(0.429)
    assert parse("Occupancy (Units) 42.9%\nSquare Foot Occupancy 51.0%"
                 ).physical_occupancy == pytest.approx(0.51)


@pytest.mark.parametrize("text", [
    "Year 1 19% Economic Occupancy 71%",
    "EXPECTED PHYSICAL OCCUPANCY (SF) AS OF YEAR 4 93.00%",
])
def test_a_projection_year_is_vetoed(text):
    d = parse(text)
    assert d.physical_occupancy is None
    assert d.economic_occupancy is None


@pytest.mark.parametrize("text", [
    "Physical Occupancy (%) 23.00% 61.00% 82.00% 91.00% 91.00%",
    "Economic Occupancy 83% 83% 83% 83% 83%",
    "SUBJECT OCCUPANCY 88.2% 95.0% 100.0% 100.0%",
])
def test_a_years_row_contributes_nothing(text):
    d = parse(text)
    assert d.physical_occupancy is None
    assert d.economic_occupancy is None


def test_a_two_value_statement_row_reads_the_current_column():
    """`Economic Occupancy N% M%` is Current beside Year 1 — a deck whose
    ONLY economic figure is its operating statement still reads."""
    assert parse("Economic Occupancy 71.69% 89.07%"
                 ).economic_occupancy == pytest.approx(0.7169)


def test_a_headline_single_beats_the_statement_row():
    """One deck states both. Read flat they are two rank-1 values that
    disagree and refuse a plainly-stated number; the statement row is
    demoted, not dropped."""
    d = parse("Economic Occupancy 84%\nEconomic Occupancy 83.62% 88.00%")
    assert d.economic_occupancy == pytest.approx(0.84)


def test_per_property_figures_at_the_same_rank_refuse():
    """A portfolio deck quoting each property's occupancy under the same
    label is three rank-1 statements of "the" occupancy; no winner."""
    d = parse("Current Physical Occupancy 61.86%\n"
              "Current Physical Occupancy 79.09%\n"
              "Current Physical Occupancy 25.22%")
    assert d.physical_occupancy is None


def test_a_stated_zero_survives():
    """Design decision 9: a stated 0% is an honestly-reported pre-lease-up
    asset — the demand gate must refuse it for the right reason, so the
    parser must not drop it as falsy."""
    assert parse("Physical Occupancy: 0%").physical_occupancy == 0.0


def test_economic_may_exceed_one_but_physical_may_not():
    assert parse("Economic Occupancy 104.9%"
                 ).economic_occupancy == pytest.approx(1.049)
    assert parse("Physical Occupancy 104%").physical_occupancy is None


@pytest.mark.parametrize("text,expect", [
    ("PhysicalOccupancy 92%", 0.92),
    ("Total PhysicalOccupancy 56%", 0.56),
])
def test_glued_labels_read(text, expect):
    assert parse(text).physical_occupancy == pytest.approx(expect)


def test_vacancy_is_not_occupancy():
    d = parse("Physical Vacancy 14.2%")
    assert d.physical_occupancy is None
    assert d.economic_occupancy is None


@pytest.mark.parametrize("text", [
    "Owner Occupied 473 52.98% 4,169 67.19% 7,286 70.23%",
    "Occupancy: 24,150 SF: 57%, 40,200 SF: 0% (just completed)",
    "at 92% stabilized occupancy",
    "consistently averaged between 94-100% occupied for the last 10 years",
])
def test_lookalikes_contribute_nothing(text):
    d = parse(text)
    assert d.physical_occupancy is None
    assert d.economic_occupancy is None


def test_a_per_property_banner_is_not_the_offering():
    """`<Town> - N% Physical Occupancy / M% Economic Occupancy / …` quotes
    one property of a portfolio; the offering states no blended figure."""
    d = parse("Biloxi - 91% Physical Occupancy / 82% Economic Occupancy "
              "/ Room to Trailer rentals")
    assert d.physical_occupancy is None
    assert d.economic_occupancy is None

# ── occupancy: a pro-forma basis is not an in-place figure ───────────
# The Huntsville CIM (MHP Brokerage) states its entire P&L at
# `Occupancy 85% (Pro Forma)` while its offering page states physical
# occupancy of 76%, and heads the result "Net Income (Actual)". Read
# flat, those are two bare rank-6 candidates that disagree, so the
# ranked regime REFUSED the deal's occupancy outright and
# `require_underwritable` turned away a CIM that states the number
# plainly. Vetoing the pro-forma figure resolves the real one AND keeps
# the projection, which is the input the blocking check needs.
#
# The layout below is the real one; unlike the rest of this module the
# FIGURES are real too, because the whole point is a document that
# states two occupancies and means different things by them.

HUNTSVILLE_PL = ("OCCUPANCY: 76% property\n"
                 "522 Units $60,137 $721,644 Occupancy 85% (Pro Forma)")


def test_a_proforma_occupancy_does_not_become_physical():
    d = parse(HUNTSVILLE_PL)
    assert d.physical_occupancy == pytest.approx(0.76)


def test_the_proforma_occupancy_is_kept_as_the_income_basis():
    d = parse(HUNTSVILLE_PL)
    assert d.income_basis_occupancy == pytest.approx(0.85)


def test_the_veto_is_what_resolves_the_stated_figure():
    """Without the P&L line the 76% already reads; the regression this
    guards is the pro-forma line ARRIVING and refusing it. Asserted as a
    pair so a future change cannot satisfy one half by dropping both."""
    assert parse("OCCUPANCY: 76% property").physical_occupancy == \
        pytest.approx(0.76)
    assert parse("OCCUPANCY: 76% property").income_basis_occupancy is None


def test_a_proforma_occupancy_without_money_is_dropped_not_kept():
    """The money on the line is what makes the figure a claim about the
    income. A pro-forma occupancy stated alone is just a projection, and
    booking it as an income basis would invent the connection."""
    d = parse("Stabilized Occupancy 92% (Pro Forma) at year end")
    assert d.physical_occupancy is None
    assert d.income_basis_occupancy is None


def test_an_economic_proforma_figure_does_not_land_in_physical():
    d = parse("Economic Occupancy 88% (Pro Forma) $721,644")
    assert d.physical_occupancy is None
    assert d.economic_occupancy is None
    assert d.income_basis_occupancy == pytest.approx(0.88)


def test_a_plain_deck_is_untouched_by_the_proforma_veto():
    """Ten of the 15 local decks state `PRO FORMA END OF YEAR N NOI $X`
    beside an honest trailing statement. That marker is nowhere near an
    occupancy, so nothing about those decks may move."""
    d = parse("PHYSICAL OCCUPANCY (SQ. FT.): 91.5%\n"
              "Economic Occupancy 78.2%\n"
              "PRO FORMA END OF YEAR 2 NOI $755,651")
    assert d.physical_occupancy == pytest.approx(0.915)
    assert d.economic_occupancy == pytest.approx(0.782)
    assert d.income_basis_occupancy is None
