"""Population and median-HHI extraction — extract/parser._parse_demographics.

As in test_parser_pricing.py: the LAYOUTS are the real ones from the 45 CIMs
in CIMs2/ — column headings, the vintage-marker rows, the merged multi-panel
lines and the interleaved noise rows are reproduced exactly — while every
FIGURE is invented. Populations and household incomes identify a live deal's
trade area as surely as its price does.

What this replaced matched `3[\\s\\-]?mile[^:]*?[:\\s]*([\\d,]+)`, which reads
digits out of the COLUMN HEADINGS: against `POPULATION 3Miles 5Miles 10Miles`
it captured the 5 of `5Miles` as the 3-mile population. Measured over those 45
files it wrote a population_3mi under 1,000 on 21 of them.

Refusing is the designed outcome, not a shortfall. `extract.enrichment`
returns a tier-1 value the moment it is not None, permanently suppressing the
Census tier-2 lookup and stamping the number `CIM/override` in the source log,
so a wrong value here is strictly worse than no value.
"""
import pytest

from extract.parser import CIMData, _parse_demographics, _radius_columns


def demo(text):
    data = CIMData()
    _parse_demographics(text, data)
    return data


# ── which lines are column headings at all ───────────────────────────

@pytest.mark.parametrize("line,expect", [
    ("1 MILE 3 MILE 5 MILE", [1, 3, 5]),
    ("1Mile 3Miles 5Miles", [1, 3, 5]),                 # spaces stripped
    ("1 Mile Radius 3 Mile Radius 5 Mile Radius", [1, 3, 5]),
    ("1-MILE 3-MILE 5-MILE", [1, 3, 5]),
    ("DEMOGRAPHICS 1 MILE 3 MILE 5 MILE", [1, 3, 5]),
    ("2025 SUMMARY 1 MILES 5 MILES 10 MILES", [1, 5, 10]),
    ("POPULATION 0.3 MILES 0.5 MILES 1 MILE", [0.3, 0.5, 1]),
    ("HOUSEHOLDS & INCOME 5 MILES 10 MILES 15 MILES", [5, 10, 15]),
    # multi-panel: the first run is the panel whose rows start at column 0
    ("POPULATION 1Mile 3Miles 5Miles HOUSEHOLDSBYINCOME 1Mile 3Miles 5Miles",
     [1, 3, 5]),
    # ...and when the two panels abut with no label between them, the restart
    # to a smaller radius is the only thing marking the boundary. Without that
    # the run reads as six columns and every row is refused as under-wide.
    ("1 Mile 3 Miles 5 Miles 1 Mile 3 Miles 5 Miles", [1, 3, 5]),
])
def test_radius_headings_are_recognised(line, expect):
    assert _radius_columns(line) == expect


@pytest.mark.parametrize("line", [
    # prose that happens to quote distances
    "3 miles East of Bastrop Proper and 130 miles Northwest of Houston. With significant",
    "• Strong Demand Base: ~30K population within 3 miles and ~60K within 5 miles.",
    "• 1.3 Miles from H-E-B, 3.2 Miles from Walmart • On-Site Office, Concrete Drives",
    "Highway Access 0.01 mi. from OK-9 & 0.22 mi. from I-35 (N); 0.13 mi. from I-35 (S)",
    # rent-comp and supply tables
    "DISTANCE - DISTANCE ~1.75 MILES DISTANCE ~2.30 MILES DISTANCE ~7.15 MILES",
    "5 PUBLIC STORAGE 2901 MILES ROAD 99,833 3.3 MILES 99,833",
    # a licence number ending in MI
    "Lic. No. 6502432668 MI | Firm No. 6505432273 MI",
    # radii named without the word repeating: only one real token
    "3, 5 & 7 MILE DEMOGRAPHICS 3, 5 & 7 MILE DEMOGRAPHICS",
])
def test_non_headings_are_refused(line):
    """Adjacency is the discriminator: a real heading is a run of radius tokens
    with nothing but whitespace between them. Every line here has words in
    between, so none forms a run."""
    assert _radius_columns(line) == []


# ── the defect that failed a real deal ───────────────────────────────

def test_column_headings_are_never_read_as_values():
    """`POPULATION 3Miles 5Miles 10Miles` used to yield population_3mi = 5 and
    population_5mi = 10, captured from inside the headings. A deal whose true
    3-mile population clears gate 1 by 39% was failed on a 5."""
    text = ("POPULATION 3Miles 5Miles 10Miles\n"
            "2025 Estimate\n"
            "Total Population 61,400 92,700 128,300")
    d = demo(text)
    assert d.population_3mi == 61_400
    assert d.population_5mi == 92_700


def test_radii_map_by_value_not_by_position():
    """Second column is 5-mile here, not 3-mile."""
    d = demo("2025 SUMMARY 1 MILES 5 MILES 10 MILES\n"
             "Population 31,200 88,400 240,100")
    assert d.population_1mi == 31_200
    assert d.population_5mi == 88_400
    assert d.population_3mi is None


def test_a_deck_without_a_three_mile_column_refuses():
    """Several decks quote 1/5/10 or 0.3/0.5/1. None of those is a 3-mile
    figure, and the Census lookup is the right source for one."""
    assert demo("POPULATION 0.3 MILES 0.5 MILES 1 MILE\n"
                "TOTAL POPULATION 31 340 3,150").population_3mi is None


def test_small_populations_are_real_and_survive():
    """A 0.3-mile ring genuinely holds tens of people. No magnitude floor may
    be added here — it would reject a correct reading."""
    d = demo("POPULATION 0.3 MILES 0.5 MILES 1 MILE\n"
             "TOTAL POPULATION 31 340 3,150")
    assert d.population_1mi == 3_150


def test_a_stray_radius_token_in_a_data_row_is_ignored():
    """The Rockford deck leaks a `3 MILES` label into the middle of a data row.
    The old pattern matched it and then took the next line's first number —
    a households count — as the 3-mile population."""
    text = ("2030 SUMMARY 1 MILES 5 MILES 10 MILES\n"
            "Population 30,900 86,200 235,700 3 MILES\n"
            "Households 13,100 37,400 101,900")
    assert demo(text).population_3mi is None


# ── vintage ──────────────────────────────────────────────────────────

def test_an_estimate_beats_a_projection_in_the_heading():
    """Decks stack a current-year summary and a five-year projection under
    identical headings. Taking the projection overstates the trade area."""
    text = ("2024 SUMMARY 1-MILE 3-MILE 5-MILE\n"
            "Population 6,100 31,700 78,300\n"
            "2099 SUMMARY 1-MILE 3-MILE 5-MILE\n"
            "Population 6,400 33,900 81,100")
    assert demo(text).population_3mi == 31_700


def test_the_most_recent_past_vintage_wins_when_the_row_carries_it():
    text = ("POPULATION 1 MILE 3 MILE 5 MILE\n"
            "2000 Population 10,900 66,200 149,400\n"
            "2010 Population 12,700 92,100 194,800\n"
            "2024 Population 12,200 94,600 200,300\n"
            "2099 Population 11,800 92,400 197,100")
    assert demo(text).population_3mi == 94_600


def test_a_vintage_marker_row_dates_the_row_beneath_it():
    """The MNET decks put the marker on its own line above the figures."""
    text = ("POPULATION 1 Mile 3 Miles 5 Miles\n"
            "2099 Projection\n"
            "Total Population 310 14,800 42,300\n"
            "2024 Estimate\n"
            "Total Population 295 14,100 41,200")
    assert demo(text).population_3mi == 14_100


def test_only_projected_vintages_yields_nothing():
    """Refuse rather than book a forecast as the current population."""
    text = ("POPULATION 1 MILE 3 MILE 5 MILE\n"
            "2098 Population 11,900 92,700 198,100\n"
            "2099 Population 12,100 93,800 199,900")
    assert demo(text).population_3mi is None


def test_disagreeing_rows_at_one_vintage_refuse():
    """The Butler deck merges four panels onto single text lines, so three
    different Total Population rows resolve to the same vintage. That deck
    produced a 445,675 — a false GO on gate 1 — and refusing is the only
    honest read of an ambiguous layout."""
    text = ("POPULATION 3 Miles 5 Miles 7 Miles HOUSEHOLDS BY INCOME 3 Miles 5 Miles 7 Miles\n"
            "2024 Estimate 16,400 51,200 118,300 2024 Estimate Population Age 25+ 11,900 36,100 81,700\n"
            "Total Population 18,100 55,700 127,400 $250,000 or More 15.6% 14.2% 11.6%\n"
            "2024 Estimate $200,000-$249,999 6.1% 6.0% 5.0%\n"
            "Total Population 16,400 51,200 118,300 $150,000-$199,999 13.5% 14.1% 13.6%")
    assert demo(text).population_3mi is None


# ── which row is the population row ──────────────────────────────────

@pytest.mark.parametrize("row", [
    "Daytime Population 210 16,900 34,800",
    "2024 Estimate Population Age 25+ 11,900 36,100 81,700",
    "Population By Age 5.3 5.2 5.6",
    "Population 25+ by Education Level 4.1 4.4 4.9",
])
def test_rows_that_are_not_the_population_count_are_refused(row):
    assert demo(f"POPULATION 1 MILE 3 MILE 5 MILE\n{row}").population_3mi is None


def test_daytime_population_does_not_displace_the_resident_count():
    text = ("POPULATION 1 MILE 3 MILE 5 MILE\n"
            "2024 Estimate\n"
            "Total Population 12,200 94,600 200,300\n"
            "Daytime Population 210 16,900 34,800")
    assert demo(text).population_3mi == 94_600


def test_unrelated_rows_between_the_heading_and_the_data_are_skipped():
    """The Albuquerque offering summary interleaves the demographics block with
    ownership and financial lines."""
    text = ("DEMOGRAPHICS 1 MILE 3 MILE 5 MILE\n"
            "OWNERSHIP TYPE Fee Simple\n"
            "2024 Population 12,200 94,600 200,300\n"
            "FINANCIAL SUMMARY\n"
            "OFFERING PRICE $6,800,000")
    assert demo(text).population_3mi == 94_600


def test_a_partial_row_is_refused():
    """Fewer cells than columns means the mapping is unknown, not shifted."""
    assert demo("POPULATION 1 MILE 3 MILE 5 MILE\n"
                "Population 12,200 94,600").population_3mi is None


# ── median household income ──────────────────────────────────────────

def test_median_hhi_reads_the_three_mile_column():
    d = demo("DEMOGRAPHICS 1 MILE 3 MILE 5 MILE\n"
             "2024 Median HH Income $44,100 $61,300 $57,800")
    assert d.median_hhi_3mi == 61_300


def test_the_average_is_not_the_median():
    """Of the 17 documents the old pattern populated, 11 took the AVERAGE
    household income and every one of the 17 took the 1-mile column."""
    d = demo("DEMOGRAPHICS 1 MILE 3 MILE 5 MILE\n"
             "2024 Average HH Income $63,200 $79,400 $76,100")
    assert d.median_hhi_3mi is None


def test_median_hhi_refuses_when_there_is_no_three_mile_column():
    d = demo("2025 SUMMARY 1 MILES 5 MILES 10 MILES\n"
             "Median Household Income $71,900 $68,400 $62,100")
    assert d.median_hhi_3mi is None


def test_per_capita_income_is_not_the_median_hhi():
    d = demo("DEMOGRAPHICS 1 MILE 3 MILE 5 MILE\n"
             "Per Capita Income $41,200 $38,600 $35,900")
    assert d.median_hhi_3mi is None


def test_a_deck_with_no_demographics_table_yields_nothing():
    d = demo("SELF STORAGE OFFERING\nA growing population base supports demand.")
    assert (d.population_1mi, d.population_3mi,
            d.population_5mi, d.median_hhi_3mi) == (None, None, None, None)
