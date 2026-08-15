"""The property name is the deal's identity, so a bad one is wrong
everywhere at once.

A browser QA pass filed it as cosmetic — "raw concatenated CIM property
names" in the pipeline table. It is not only cosmetic. The name reaches
`webapp.services.deal_meta` as both `property_name` AND, through
`sanitize_name`, the `deal_id` and the deal FOLDER; it titles the memo
and the investor summary; and `services` matches it against the comps DB
to decide whether an upload is a duplicate. One bad capture is wrong in
all of those simultaneously, and the folder name is the one that cannot
be corrected by re-running.

The mechanism: `\\s` inside the capture's character class matches
newlines, and the pattern is anchored on the word "Storage" at the right
end. So a whole-document `re.search` starting at the first capital letter
ran forward THROUGH the line breaks until it found "Storage", swallowing
every line in between.
"""

import pytest

from extract.parser import (MAX_NAME_WORDS, _find_property_name,
                            parse_cim, tidy_property_name)


# The shape of a real cover page: a banner line, the name, then location
# and marketing. Under the old pattern the first of these stored
# "CONFIDENTIAL OFFERING MEMORANDUM\nExpo Storage".
COVERS = [
    ("CONFIDENTIAL OFFERING MEMORANDUM\nExpo Storage\n"
     "Belton, Texas 76513\nPresented by Marcus & Millichap",
     "Expo Storage"),
    ("FOR SALE\nExclusively Offered By\nSummit Self Storage\n"
     "1234 Main Street",
     "Summit Self Storage"),
    ("OFFERING MEMORANDUM\n4 Properties Throughout Abilene MSA\n"
     "Abilene Self Storage Portfolio\n48,762 NRSF",
     "Abilene Self Storage"),
    ("Investment Opportunity\nRoadrunner Storage\nOdessa, TX",
     "Roadrunner Storage"),
]


@pytest.mark.parametrize("cover,expected", COVERS)
def test_a_name_never_spans_a_line_break(cover, expected):
    """MUTATION: put `\\s` back in either name pattern's character class.

    Each of these covers stored the banner line and the name as one
    string, separator included."""
    assert _find_property_name(cover) == expected


@pytest.mark.parametrize("cover,expected", COVERS)
def test_the_name_survives_the_full_parser(cover, expected):
    """Through `parse_cim`, not just the helper — the extraction the
    web upload actually runs."""
    data = parse_cim({"text": cover, "tables": [],
                           "pages": [cover], "page_count": 1})
    assert data.property_name == expected


def test_a_title_cased_label_is_read():
    """MUTATION: make the label case-sensitive again.

    `Property Name:` is what CIMs print, and `(?:property|facility|
    asset)` matched only lower case — so the labelled branch never fired
    on a real document. It went unnoticed because the anchored pattern
    reaches the same answer from the other side on an ordinary name;
    these two cases are where it cannot. "SS" is accepted only by the
    labelled pattern, and a name with no "Storage" in it at all is
    reachable only through a label."""
    assert _find_property_name("Property Name: The Vault SS\n") == "Vault SS"
    assert _find_property_name("Facility: Summit Self Storage\n") \
        == "Summit Self Storage"


def test_the_cover_page_outranks_the_body():
    """MUTATION: drop the `pages[0]` scope and search only `text`.

    Same precedence `extract.location` reads for city/state, for the same
    reason: a "Storage" on page 40 is as likely to be a comp or the
    seller's other facility as it is to be this asset.

    The mutation is invisible on an ordinary document — the cover is a
    PREFIX of `text`, so the first match in the whole document is
    usually the cover's anyway. It bites where the two patterns disagree
    across pages: the cover names the asset UNLABELLED, and a rent-comp
    table forty pages later carries a literal "Property Name:" label.
    Without the cover scope the labelled pattern sweeps the entire
    document before the anchored one is tried at all, and the comp
    wins."""
    cover = "OFFERING MEMORANDUM\nRoadrunner Storage\nOdessa, TX"
    body = ("Rent Comparables\n"
            "Property Name: Big Bend Storage\nMidland, TX")
    assert _find_property_name(cover + "\n" + body, [cover, body]) \
        == "Roadrunner Storage"


def test_a_banner_that_tidies_away_hands_off_to_the_next_match():
    """MUTATION: `finditer` back to `search`.

    The cover's first anchored match is "OFFERING MEMORANDUM FOR SALE
    Storage" — all boilerplate, so `tidy_property_name` returns "". That
    has to advance to the next match on the page, not abandon the
    pattern: the fallback scope re-runs from position 0, finds the same
    doomed match, and the real name two lines down is never read."""
    cover = ("OFFERING MEMORANDUM FOR SALE Storage\n"
             "Roadrunner Storage\nOdessa, TX")
    assert _find_property_name(cover, [cover]) == "Roadrunner Storage"


def test_the_body_still_answers_when_the_cover_does_not():
    """Recall is not traded away for precision: a cover that is one image
    and one tagline used to fall through to the body, and still does."""
    cover = "A NEW STANDARD IN SELF-STORAGE"
    body = "The Property\nBig Bend Storage is a 48,000 SF facility."
    assert _find_property_name(cover + "\n" + body, [cover, body]) \
        == "Big Bend Storage"


def test_a_document_with_no_name_reports_none():
    """None, not a guess. `CIMData` flags the gap and the analyst fills
    it; a plausible-looking wrong name is the outcome with no signal
    attached to it."""
    assert _find_property_name("Rent roll and operating statements.") is None


# ── tidy_property_name ──────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("CONFIDENTIAL OFFERING MEMORANDUM\nExpo Storage", "Expo Storage"),
    ("FOR SALE - Summit Self Storage", "Summit Self Storage"),
    ("OFFERING MEMORANDUM | Expo Storage", "Expo Storage"),
    ("The Property: Roadrunner Storage", "Roadrunner Storage"),
    # A leading number is an address or a portfolio count, never a name.
    ("1234 Main Street Storage", "Main Street Storage"),
    # Boilerplate and a count INTERLEAVED. Two sequential passes — noise
    # first, then digits — stop at the "4" and leave "Properties" in the
    # name; one loop over both rules consumes the whole prefix.
    ("OFFERING MEMORANDUM 4 Properties Expo Storage", "Expo Storage"),
])
def test_boilerplate_is_trimmed_from_the_left(raw, expected):
    """Left, because the patterns anchor on "Storage" at the right — a
    runaway capture can only have eaten leftward, so a suffix trim would
    remove the one part that is certainly the name."""
    assert tidy_property_name(raw) == expected


def test_a_runaway_capture_is_capped_at_the_tail():
    """Belt and braces for a cover whose boilerplate is not in
    NAME_NOISE. The cap keeps the RIGHTMOST words for the same reason the
    trim works leftward, so the anchor survives it."""
    raw = ("Nineteen Eighty Four Vintage Brick And Timber Warehouse "
           "Conversion Expo Storage")
    out = tidy_property_name(raw)
    assert len(out.split()) == MAX_NAME_WORDS
    assert out.endswith("Expo Storage")


def test_tidying_a_clean_name_changes_nothing():
    """The guard against a trim that improves the broken case by
    damaging the common one."""
    for name in ("Expo Storage", "Summit Self Storage",
                 "Lone Star RV Self Storage", "O'Connor Storage"):
        assert tidy_property_name(name) == name


def test_a_name_that_is_all_boilerplate_yields_empty_not_junk():
    """`_find_property_name` treats "" as no answer and keeps looking,
    which is what stops a banner line from becoming a deal folder."""
    assert tidy_property_name("OFFERING MEMORANDUM FOR SALE") == ""


def test_the_anchor_alone_is_not_a_name():
    """MUTATION: drop the ANCHOR_ONLY rejection.

    These are what the patterns matched ON, not what they found. A cover
    banner reading "FOR SALE Storage" trims to the bare word, and
    returning it puts "Storage" in the pipeline table as the deal's
    name — and, through `sanitize_name`, as its folder."""
    for raw in ("FOR SALE Storage", "Self Storage", "The Storage",
                "OFFERING MEMORANDUM Self Storage"):
        assert tidy_property_name(raw) == "", raw
