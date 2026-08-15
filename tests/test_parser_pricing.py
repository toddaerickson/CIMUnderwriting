"""Asking-price extraction — extract/parser._parse_pricing.

The LAYOUTS below are the real ones, drawn from the 45 CIMs in CIMs2/ via
pdf_reader.extract_pdf: the labels, their punctuation, the glued-together
`ListingPrice`, and the header-row/next-line split are all reproduced exactly,
because those are what the parser keys on. Every FIGURE is invented. Unlike
test_parser_location.py — whose verbatim strings are city and street names —
an asking price is the commercial term of a live deal, and the parser cannot
tell a real seven-digit number from a made-up one.

What this replaced required `asking\\s+price|list\\s+price|offered at|purchase
price`. Measured over those 45 files it found a price on 15, of which two were
garbage (a rent-table cell and a street number), missed the six MNET decks
outright because their text layer renders `ListingPrice` with no space, and
booked one property of a three-property portfolio as the whole offering.
"""
import pytest

from extract.parser import MIN_PLAUSIBLE_ASKING_PRICE, CIMData, _parse_pricing


def price(text):
    data = CIMData()
    _parse_pricing(text, data)
    return data.asking_price


# ── labels that name the offering ────────────────────────────────────

@pytest.mark.parametrize("text,expect", [
    ("Asking Price $6,495,000", 6_495_000),
    ("Asking Price: $7,950,000", 7_950_000),
    ("OFFERING PRICE $6,800,000", 6_800_000),
    ("OFFERING PRICE: $6,900,000", 6_900_000),
    ("PURCHASE PRICE $13,000,000", 13_000_000),
    ("Purchase Price $7,500,000", 7_500_000),
    ("LIST PRICE: $4,650,000", 4_650_000),
    ("Sale Price $13,226,850", 13_226_850),
    ("SALE PRICE: $6,500,000", 6_500_000),
    ("Estimated Sale Price $3,000,000", 3_000_000),
    # The label is followed by its own trailing junk on the same text line.
    ("Sale Price $6,500,000 Lot Size: 13.81 Acres Price PSF: $10.81", 6_500_000),
    ("Asking Price $6,800,000 10x20 200 $160 $0.80", 6_800_000),
    ("Purchase Price: $1,300,000.00 ($11 per square foot)", 1_300_000),
])
def test_offering_labels_are_read(text, expect):
    assert price(text) == expect


def test_listing_price_survives_the_stripped_space():
    """The MNET text layer renders it glued. `list\\s+price` could not match,
    so six decks in the corpus fell through to asking_price = None and the
    engine booked a price of 0."""
    assert price("ListingPrice $4,500,000") == 4_500_000


# ── a rate is not a price ────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Price Per Unit $8,863",
    "PRICE PER UNIT $14,376",
    "Price/Unit: $24,007",
    "Price Per SqFt $56.61",
    "Price PSF $157.96",
    "Price/RentableSF $71.78",
    "Price Per SF $292.88",
    "Price PSF: $82.00± / Rentable SF",
    "Price per Square Foot: $42.24",
    "Price per Rentable Square Foot $69.90",
    "PRICE PER SQUARE FOOT $24.43",
    "Price per Rentable SF $79.31",
    "PRICE / ACRE: $470,673",
    "PRICE/ SF\nCAP RATE NOI",
])
def test_per_unit_of_measure_labels_are_refused(text):
    """Two CIMs in the corpus quote ONLY a per-SF price. Booking $69.90 as the
    offering is worse than refusing: extract.enrichment returns a tier-1 value
    the moment it is not None, so the number would stick AND be stamped
    'CIM/override'."""
    assert price(text) is None


# ── the label must be the line's subject, not a word inside prose ────

@pytest.mark.parametrize("text", [
    "• A median home price of roughly $340,000 has allowed nearly 60 percent of",
    "The median home price in Columbus is well below the national level,",
    "reassessed at the purchase price. Every expense assumption is",
    "Goodwill Price Allocation 25.00% Repairs and Maintenance 9,177 $0.20",
    "Home Price Art Galleries GolfCourses",
    "LOWER PRICE POINT COMPARED TO HUNTSVILLE, ITS SMALL-",
    "Property Price $/SF",
    "PROPERTY NAME ADDRESS PRICE PRICE/SF",
    # A specific label CAN appear mid-line with a plausible figure beside it.
    # Here the offering summary has collided with a unit-mix heading in the
    # text layer, and which table the figure belongs to is no longer legible —
    # so the label has to be the line's subject, not merely present in it.
    "Unit Type SF Sale Price $4,750,000",
])
def test_prose_and_column_headings_are_refused(text):
    assert price(text) is None


# ── the header-row / next-line form ──────────────────────────────────

@pytest.mark.parametrize("text,expect", [
    ("ListingPrice CapRate (Year One) #ofUnits\n$3,500,000 7.83% 434", 3_500_000),
    ("SALE PRICE NOI\n$5,600,000 $419,794", 5_600_000),
    ("OFFERING PRICE\n20,500,000", 20_500_000),        # no dollar sign
    ("PRICE\n5,000,000", 5_000_000),
])
def test_value_may_sit_in_the_row_beneath_the_label(text, expect):
    assert price(text) == expect


def test_a_label_carrying_its_own_figure_never_reads_the_next_line():
    """A same-line price wins outright; the row beneath is not consulted."""
    assert price("Asking Price $6,495,000\n99,000,000") == 6_495_000


def test_a_header_row_whose_only_figure_is_a_column_year_still_reads_beneath():
    """`LIST PRICE 2025 NOI STABILIZED MARGIN` is a header row, and 2025 names
    the NOI column rather than pricing anything. Falling through on 'no digits
    on the label line' would strand this deck unpriced."""
    text = "LIST PRICE 2025 NOI STABILIZED MARGIN\n$5,600,000 $419,794 42.1%"
    assert price(text) == 5_600_000


def test_a_wrapped_header_cell_between_label_and_values_is_stepped_over():
    """The same header can wrap: `(STABILIZED)` is the second half of the
    STABILIZED MARGIN column heading, not a row. Only a DIGIT-FREE line is
    stepped over, and only one — the first row carrying digits decides."""
    text = ("LIST PRICE 2025 NOI STABILIZED MARGIN\n"
            "(STABILIZED)\n"
            "$7,250,000 $411,079 5.6% 66.5%")
    assert price(text) == 7_250_000


def test_the_first_digit_bearing_row_decides_even_when_it_is_not_a_price():
    """Otherwise a label scans forward until something plausible turns up, and
    ends up owning a figure from a different table."""
    text = ("OFFERING PRICE: CONTACT BROKER FOR PRICING\n"
            "ADDRESS: 20603 CLAY RD\n"
            "$8,400,000")
    assert price(text) is None


@pytest.mark.parametrize("text", [
    "OFFERING PRICE: CONTACT VERSAL FOR PRICING\nADDRESS: 20603 CLAY RD",
    "PURCHASE PRICE\n11017 County Line Road",
    "Pricing Detail\n09",
    "PRICING DETAIL\nSUMMARY OPERATING DATA",
    "PRICE\n(YEAR TWO) (YEAR TWO)",
    "ASKING PRICE TOTAL NRSF\napproximately 7 acres. The additional acreage is not",
    "List Price\n$122,155 $336,068 $403,658",
    "List Price\n129",
    "PRICE AVAILABLE\n7%",
])
def test_next_line_junk_is_refused(text):
    """Every one of these is a real label sitting above a line that is not a
    price. The plausibility floor is what refuses them — a street number, a
    page number, an acreage and a rent-table row all parse as numbers."""
    assert price(text) is None


def test_the_plausibility_floor_is_the_rule_that_refuses_them():
    """Stated as its own case so the floor cannot be removed silently: the same
    layout yields a price once the figure is plausible."""
    assert price("List Price\n129") is None
    assert price("List Price\n1,290,000") == 1_290_000
    assert MIN_PLAUSIBLE_ASKING_PRICE > 129


# ── quoted in millions ───────────────────────────────────────────────

@pytest.mark.parametrize("text,expect", [
    ("Asking Price $4.5 million", 4_500_000),
    ("Asking Price $6.9MM", 6_900_000),
    ("Asking Price $7.25M", 7_250_000),
])
def test_millions_are_scaled(text, expect):
    assert price(text) == expect


def test_a_capitalised_word_after_the_number_is_not_a_millions_suffix():
    """The rule this replaces re-scanned a five-character window for `M` under
    IGNORECASE, so this line returned 675,000,000. Note the bug does NOT
    reproduce on `$450 Market Value`: there the pattern's own `(?:million|MM|M)`
    consumes the M of 'Market', leaving no M for the window to find. It needs a
    SECOND capital M just past the number, which is why this case is spelled
    out rather than left to a shorter string."""
    assert price("Asking Price $675 MSA Median Household Income") is None


# ── rank beats document order ────────────────────────────────────────

def test_a_portfolio_price_outranks_a_per_property_price_stated_first():
    """Bastrop Guardian is a three-property offering that quotes each property
    separately and the total once. Document order books one building as the
    deal — a 2.2x understatement that was live in the corpus."""
    text = ("Purchase Price: $1,600,000\n"
            "Purchase Price: $1,300,000.00\n"
            "• The purchase price for all three properties $3,500,000")
    assert price(text) == 3_500_000


def test_portfolio_label_outranks_a_per_sf_price_stated_first():
    text = "PRICE PER SQUARE FOOT $24.43\nPORTFOLIO PRICE $9,580,000 Combined Management"
    assert price(text) == 9_580_000


def test_a_specific_label_outranks_a_bare_one_stated_first():
    """MNET DECATUR prints a per-SF price above the real one; more generally a
    bare `PRICE` heading is consulted only after the specific labels fail."""
    text = "PRICE\n8,000,000\nOFFERING PRICE $12,400,000"
    assert price(text) == 12_400_000


def test_nothing_priced_yields_none():
    assert price("SELF STORAGE OFFERING\nCall broker for details.") is None
