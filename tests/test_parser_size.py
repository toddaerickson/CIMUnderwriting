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