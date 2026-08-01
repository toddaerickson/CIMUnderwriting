"""City / state extraction — extract/location.py.

Every string in the "real cover text" tests is verbatim from a CIM in CIMs/,
captured via pdf_reader.extract_pdf. The PDFs themselves are gitignored, so the
text is inlined here to keep the cases runnable in CI.

Against those ten files the regex this replaced resolved 3 correctly, returned a
street line or a field label glued to the city twice, and missed four outright.
"""
import pytest

from extract.location import (
    Location,
    best_city_state,
    find_locations,
    locate,
    near_broker,
    tidy_city,
)


# ── city tidying ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expect", [
    ("Industry Drive Bastrop", "Bastrop"),          # ran into the street line
    ("East Pikes Peak Avenue Colorado Springs", "Colorado Springs"),
    ("State Belton", "Belton"),                     # ran into a field label
    ("YORK", "York"),                               # ALL-CAPS cover
    ("DECATU R", "Decatur"),                        # letter-split by the text layer
    ("McKinney", "McKinney"),                       # genuine CamelCase survives
    ("LaGrange", "LaGrange"),
    ("5485 Airport Hwy", ""),                       # pure street fragment
    ("", ""),
])
def test_tidy_city(raw, expect):
    assert tidy_city(raw) == expect


# ── real cover text, one case per CIM ────────────────────────────────

@pytest.mark.parametrize("line,city,state", [
    # Texoma 377 OM.pdf — no spaces anywhere in the address
    ("63CedarMillsRd,Gordonville,TX76245", "Gordonville", "TX"),
    # TX Maxwell rural B&C Storage — ALL CAPS
    ("5485 AIRPORT HWY. 21, MAXWELL, TX 78666", "Maxwell", "TX"),
    # Madisonville i-45 Storage Landing
    ("5541 Interstate 45 N., Madisonville, TX 77864", "Madisonville", "TX"),
    # DFW Lunkers Boat & RV — address sharing a line with metrics
    ("99,940 NRSF | 218 UNITS | 7447 FM 897, TELEPHONE, TX 75488",
     "Telephone", "TX"),
    # desoto self storage OM — state spelled out
    ("Desoto, Texas 75115", "Desoto", "TX"),
    # om_coloradospringsco — the street line runs into the city
    ("2418 East Pikes Peak Avenue Colorado Springs, CO 80909",
     "Colorado Springs", "CO"),
    # Belton Expo Storage — state spelled out, no ZIP on that line
    ("Belton, Texas 76513", "Belton", "TX"),
])
def test_real_cover_lines(line, city, state):
    assert Location(city, state, "") in [
        Location(loc.city, loc.state, "") for loc in find_locations(line)]


@pytest.mark.parametrize("line,city,state", [
    ("Buffalo, New York 14201", "Buffalo", "NY"),
    ("Raleigh, North Carolina 27601", "Raleigh", "NC"),
    ("Providence, Rhode Island 02901", "Providence", "RI"),
    ("Charleston, West Virginia 25301", "Charleston", "WV"),
])
def test_two_word_state_names_spelled_out(line, city, state):
    """Squeezing all whitespace out of the capture before the STATES lookup turns
    'New York' into 'newyork', which is not a key — every two-word state then
    fails the ST_CODES check and the address is dropped with no fallback."""
    assert (city, state) in [(h.city, h.state) for h in find_locations(line)]


def test_letter_split_state_name():
    """green-storage-plus-austin-tx-om.pdf sets the state as 'Te xas'. The
    filename claims Austin; the document says Spicewood, and 78669 is
    Spicewood's ZIP — the parser is right and the filename is not."""
    hits = find_locations("5216 Electric Avenue Spicewood, Te xas 78669")
    assert ("Spicewood", "TX") in [(h.city, h.state) for h in hits]


# ── broker suppression ───────────────────────────────────────────────

def test_broker_office_address_is_suppressed():
    assert find_locations("Marcus & Millichap, Encino, CA 91436") == []


def test_broker_suppression_window_stays_tight():
    """Widen the window and, on a whole-document blob, the disclaimer page's
    broker mentions suppress every real address in the file."""
    text = "Marcus & Millichap" + " filler" * 40 + ", Gordonville, TX 76245"
    assert ("Gordonville", "TX") in [(h.city, h.state) for h in find_locations(text)]


def test_near_broker_is_positional():
    text = ("CBRE 1200 Main, Dallas, TX 75201" + " far away" * 20
            + " Kerrville, TX 78028")
    assert near_broker(text, text.index("Dallas"))
    assert not near_broker(text, text.index("Kerrville"))


# ── cover-page-first ordering ────────────────────────────────────────

def test_cover_page_wins_over_a_body_address():
    """The cover carries the subject; later pages carry everyone else's."""
    pages = ["STORAGE LANDING 5541 Interstate 45 N., Madisonville, TX 77864",
             "Disclaimer. Offices at 1 Union Sq, Encino, CA 91436"]
    locs, src = locate(pages)
    assert src == "cover page"
    assert locs[0].city == "Madisonville"


def test_body_is_used_when_the_cover_has_no_address():
    pages = ["EXPO STORAGE", "Property is located at Belton, Texas 76513"]
    locs, src = locate(pages)
    assert src == "body text"
    assert locs[0].city == "Belton"


def test_locate_accepts_a_plain_string():
    locs, _ = locate("Gordonville, TX 76245")
    assert locs[0].city == "Gordonville"


def test_locate_on_empty_input():
    assert locate([]) == ([], "no text")
    assert locate(None) == ([], "no text")


# ── the zip-less last resort ─────────────────────────────────────────

def test_zip_less_matching_is_opt_in():
    """Off by default: an unanchored match is a plausible city, not a proven one,
    and the analysis pipeline feeds city/state into the population gate."""
    cover = ["Boat & RV Storage Property For Sale - Creedmoor, TX"]
    assert locate(cover) == ([], "not found")

    locs, src = locate(cover, allow_zipless=True)
    assert src == "cover page (no ZIP)"
    assert locs[0].city == "Creedmoor"


def test_zip_less_source_is_reported_so_callers_can_downgrade():
    """The rename script keys its confidence off this string; a plain
    'cover page' would file an unproven city as HIGH."""
    _, src = locate(["Boat & RV Storage For Sale - Creedmoor, TX"],
                    allow_zipless=True)
    assert "no ZIP" in src


def test_zip_less_matching_is_never_applied_to_the_body():
    """Even opted in, it stays on the cover: unanchored it reads prose as an
    address. Here the body would otherwise yield 'Austin'."""
    pages = ["EXPO STORAGE", "Comparable facilities in Austin, TX trade at 6%."]
    locs, src = locate(pages, allow_zipless=True)
    assert locs == []
    assert src == "cover page (no ZIP)"


def test_zip_less_matching_does_not_fabricate_a_city_from_prose():
    """A case-blind version read 'strong, in a healthy secondary market' as
    Location('Has Been Strong', 'IN') — 'in' is a valid state code."""
    prose = ["Rent growth has been strong, in a healthy secondary market."]
    assert locate(prose, allow_zipless=True)[0] == []


# ── best_city_state ──────────────────────────────────────────────────

def test_most_frequent_city_wins():
    text = ("Kerrville, TX 78028 ... Kerrville, TX 78028 ... "
            "one mention of Boerne, TX 78006")
    city, state, _ = best_city_state(text)
    assert (city, state) == ("Kerrville", "TX")


def test_state_follows_the_winning_city_when_states_disagree():
    text = "Kerrville, TX 78028 and Kerrville, TX 78028 and Ada, OK 74820"
    city, state, _ = best_city_state(text)
    assert (city, state) == ("Kerrville", "TX")


def test_same_city_name_under_two_states_takes_the_most_common():
    """Taking the first-seen state hands a document that lists 'Springfield, IL'
    as a comp above two mentions of the subject 'Springfield, MO' the comp's
    state — a real shape, since same-named cities are common."""
    text = ("Comp: Springfield, IL 62701 | Subject: Springfield, MO 65801 "
            "| Subject: Springfield, MO 65801")
    city, state, _ = best_city_state(text)
    assert (city, state) == ("Springfield", "MO")


def test_best_city_state_returns_none_when_nothing_is_found():
    city, state, src = best_city_state(["Offering Memorandum", "no address here"])
    assert city is None and state is None and src


# ── parser integration ───────────────────────────────────────────────

def test_parse_cim_reads_the_cover_page_first():
    from extract.parser import parse_cim

    raw = {
        "text": "ignored",
        "tables": [],
        "pages": ["B&C STORAGE FACILITY 5485 AIRPORT HWY. 21, MAXWELL, TX 78666",
                  "Presented by Marcus & Millichap, Encino, CA 91436"],
    }
    data = parse_cim(raw)
    assert (data.city, data.state) == ("Maxwell", "TX")


def test_parse_cim_falls_back_to_flat_text_without_pages():
    """Older callers pass no 'pages' key; they must still resolve, not crash."""
    from extract.parser import parse_cim

    data = parse_cim({"text": "Sited at Gordonville, TX 76245", "tables": []})
    assert (data.city, data.state) == ("Gordonville", "TX")


def test_parse_cim_will_not_use_a_zip_less_guess():
    """city/state reach extract/enrichment.py's geocode, which backfills
    population_3mi — the field gating 'Population >= 50,000 within 3 miles'. An
    unconfirmed city must not drive that; the rename script surfaces it instead."""
    from extract.parser import parse_cim

    raw = {"text": "", "tables": [],
           "pages": ["Boat & RV Storage For Sale - Creedmoor, TX"]}
    assert parse_cim(raw).city is None


def test_parse_cim_leaves_city_none_when_absent():
    from extract.parser import parse_cim

    data = parse_cim({"text": "No address in this document.", "tables": [],
                      "pages": ["No address in this document."]})
    assert data.city is None and data.state is None
