"""
Structured data extraction from raw CIM text.

CIMs have no standard format. This parser uses regex and keyword matching
to locate key data points. Missing fields are set to None and flagged
for manual review by Claude Code.
"""

import re
from dataclasses import dataclass, field, fields
from datetime import date as _date
from typing import Optional

from extract.location import best_city_state, norm_text
from extract.tables import (
    ROLE_CURRENT, ROLE_T3, ROLE_YEAR_PREFIX, assign_periods, find_header,
)


@dataclass
class UnitType:
    size_label: str = ""          # e.g., "5x10", "10x20"
    width: Optional[float] = None
    depth: Optional[float] = None
    sf: Optional[float] = None
    count: Optional[int] = None
    rate: Optional[float] = None  # monthly rent per unit
    climate_controlled: bool = False


@dataclass
class FinancialLine:
    label: str = ""
    t3: Optional[float] = None    # trailing 3-month annualized
    t12: Optional[float] = None   # trailing 12-month actual
    cim_yr1: Optional[float] = None  # CIM pro forma year 1

    # Where the line came from. `statement` is the ordinal of the source
    # TABLE across the whole document, and it is the identity
    # `analysis.financials._map_expense_lines` reconciles on: a CIM that
    # prints its operating statement once per property and again combined
    # states the same property tax three times, and adding them up booked
    # 2-3x the real expense on the 3 corpus CIMs that carry expense lines
    # at all (Dallas, Wichita, Columbus) — i.e. on every one of them.
    #
    # `page` is for the run warning's TEXT only. Two statements can share a
    # page and one statement can span two, so a page is not an identity —
    # using it as one is the bug wearing a different hat.
    #
    # Both default to None so a snapshot stored before these fields existed
    # still rehydrates (`webapp.services.cim_from_dict` drops unknown keys
    # and lets the rest default). Every line then carries `statement=None`,
    # lands in ONE group, and is summed within it — which is exactly the
    # pre-existing behaviour, so a legacy deal's numbers do not move until
    # it is re-extracted.
    page: Optional[int] = None
    statement: Optional[int] = None


@dataclass
class CIMData:
    # Property basics
    property_name: Optional[str] = None
    #: Street address. Never filled by the parser — see _parse_property_basics
    #: for why. Analyst-entered on the assumptions page, or None.
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    #: The geocoding key. Extracted with the city/state it belongs to, so the
    #: two cannot describe different places.
    zip_code: Optional[str] = None
    msa: Optional[str] = None
    year_built: Optional[int] = None
    year_expanded: Optional[int] = None
    acreage: Optional[float] = None

    # Size & occupancy
    nrsf: Optional[float] = None          # net rentable square feet
    total_units: Optional[int] = None
    cc_units: Optional[int] = None        # climate-controlled units
    non_cc_units: Optional[int] = None
    cc_sf: Optional[float] = None
    non_cc_sf: Optional[float] = None
    cc_pct: Optional[float] = None        # % of NRSF that is CC
    physical_occupancy: Optional[float] = None
    economic_occupancy: Optional[float] = None

    # Facility-type SF breakdowns (for replacement cost)
    ss_driveup_sf: Optional[float] = None      # self-storage drive-up SF
    ss_enclosed_sf: Optional[float] = None      # self-storage enclosed multi-story SF
    brv_enclosed_sf: Optional[float] = None     # boat/RV enclosed building SF
    brv_covered_sf: Optional[float] = None      # boat/RV covered canopy SF
    brv_open_sf: Optional[float] = None         # boat/RV open parking SF

    # Pricing
    asking_price: Optional[float] = None
    price_per_sf: Optional[float] = None

    # Demographics
    population_1mi: Optional[int] = None
    population_3mi: Optional[int] = None
    population_5mi: Optional[int] = None
    median_hhi_3mi: Optional[float] = None

    # Unit mix
    unit_mix: list = field(default_factory=list)

    # Financials
    income_lines: list = field(default_factory=list)
    expense_lines: list = field(default_factory=list)
    ttm_gpr: Optional[float] = None
    ttm_egr: Optional[float] = None
    ttm_total_revenue: Optional[float] = None
    ttm_total_expenses: Optional[float] = None
    ttm_noi: Optional[float] = None
    # How many months of ACTUALS the TTM figures annualize from. 12 means
    # a genuine trailing year; 9 means a partial year scaled up ("T-9
    # annualized"), which the ttm_annualization check flags for
    # seasonality risk. None means the CIM did not say — never assumed
    # to be 12, per the occupancy lesson (decision 9): an invented 12
    # is precisely the answer that silences the check that needs it.
    ttm_months: Optional[int] = None
    cim_yr1_noi: Optional[float] = None
    other_income: Optional[float] = None

    # Comps
    comp_data: list = field(default_factory=list)

    # Supply pipeline
    new_supply_mentions: Optional[str] = None
    # Analyst-entered supply metrics (CIMs rarely state them; gate 5 turns
    # data-driven when present): existing competitor NRSF and
    # under-construction/planned NRSF within 3 miles.
    competitive_supply_sf_3mi: Optional[float] = None
    pipeline_supply_sf_3mi: Optional[float] = None
    # Analyst market verification for gate 7: top_50 | strong_secondary |
    # neither (None = unverified). market_verified_location is stamped
    # automatically at save with the msa/city the verification certified;
    # gate 7 treats a verification for a different location as stale.
    market_verification: Optional[str] = None
    market_verified_location: Optional[str] = None

    # Rate positioning & momentum drivers (screening-framework additions).
    # in_place_avg_rent_psf: analyst override; when None the engine
    # derives a count-weighted scheduled $/SF/mo from the unit mix.
    # street_rate_trend: rising | flat | falling (None = unknown) — feeds
    # the ECRI-in-falling-market risk flag. t3_annualized_revenue: T3
    # annualized $ for the momentum screen vs T12.
    in_place_avg_rent_psf: Optional[float] = None
    street_rate_trend: Optional[str] = None
    t3_annualized_revenue: Optional[float] = None

    # Market rent (for value-add analysis)
    market_rent_psf: Optional[float] = None  # $/SF/month at market rates

    # Misc
    capex_estimate: Optional[float] = None
    mgmt_fee_pct: Optional[float] = None

    # Multi-property detection. None for a single-asset CIM; a dict from
    # extract.portfolio.portfolio_signal().as_dict() when the document looks
    # like a portfolio. Presence is the flag — the analysis pipeline must
    # surface it, not silently underwrite a mixed bag as one property.
    portfolio_signal: Optional[dict] = None

    #: Financial lines the CIM DID state and the parser refused to price,
    #: because the table's columns could not be named. Rows of
    #: `{"label", "page", "reason"}`; `engine.run_analysis` turns them into
    #: run warnings. This is diagnostics ABOUT extraction, not an extracted
    #: field, which is why `extraction_report` skips it — an empty list is
    #: the GOOD outcome, and counting it would report a clean parse as one
    #: more missing field.
    unmapped_financial_lines: list = field(default_factory=list)

    def extraction_report(self) -> dict:
        """Return a report of populated vs missing fields."""
        total = 0
        populated = 0
        missing_fields = []
        for f in fields(self):
            # Both are metadata ABOUT extraction, not extracted values —
            # counting either would move confidence or pad "Missing fields".
            if f.name in ("unmapped_financial_lines", "portfolio_signal"):
                continue
            if f.name in ("unit_mix", "income_lines", "expense_lines", "comp_data"):
                total += 1
                if getattr(self, f.name):
                    populated += 1
                else:
                    missing_fields.append(f.name)
            else:
                total += 1
                if getattr(self, f.name) is not None:
                    populated += 1
                else:
                    missing_fields.append(f.name)

        return {
            "total_fields": total,
            "populated": populated,
            "missing": missing_fields,
            "confidence_pct": round(100 * populated / total, 1) if total else 0,
        }


def parse_cim(raw: dict, filename: str = "") -> CIMData:
    """
    Parse raw PDF extraction into structured CIMData.

    Args:
        raw: dict from pdf_reader.extract_pdf() with keys
             "text", "tables", "page_count", "pages"
        filename: PDF stem (optional) — an extra wording source for the
             portfolio detector (filing prefixes like "SS 2Property ...").

    Returns:
        CIMData with as many fields populated as possible.
    """
    text = raw["text"]
    tables = raw.get("tables", [])
    pages = raw.get("pages") or []
    data = CIMData()

    _parse_property_basics(text, data, pages)
    _parse_size_occupancy(text, data)
    _parse_pricing(text, data)
    _parse_demographics(text, data)
    _parse_financials(text, tables, data)
    _parse_supply(text, data)
    _compute_derived(data)

    # A portfolio CIM (two properties sold as one offering) must be surfaced,
    # not silently collapsed into one wrong deal. Detection is conservative —
    # evidence for a human to confirm, never a hard block.
    from extract.portfolio import portfolio_signal
    signal = portfolio_signal(pages, filename=filename)
    if signal.is_portfolio:
        data.portfolio_signal = signal.as_dict()

    return data


# ── Internal Parsing Functions ──────────────────────────────────────

#: A property name is a name, not a sentence. Six words is generous —
#: "Lone Star Boat & RV Self Storage" is five — and past that the capture
#: has run into whatever sat on the line below it.
MAX_NAME_WORDS = 6

#: Cover-page boilerplate. The name patterns are anchored on the word
#: "Storage", so a runaway capture always eats LEFTWARD: every token here
#: shows up as a prefix on the real name, never as a suffix.
#:
#: `scripts/cims_rename_plan.py` carries a similar-looking junk list and
#: is NOT a duplicate of this one, so do not consolidate them: it answers
#: "what is the first plausible title line on this cover?" for an
#: informational CSV column where a miss costs a blank cell, while this
#: answers "what is this deal called?" for a name that becomes the deal
#: folder, the memo title and the comps match. Different question,
#: different cost of being wrong, so different rules — that one accepts a
#: title with no "Storage" in it and this one must not.
NAME_NOISE = frozenset({
    "confidential", "offering", "memorandum", "om", "for", "sale",
    "exclusively", "offered", "listed", "presented", "by", "investment",
    "opportunity", "brochure", "package", "listing", "executive",
    "summary", "subject", "property", "properties", "asset", "assets",
    "facility", "name", "the", "a", "an", "of", "and", "in", "at",
})

#: The words the patterns anchor ON. A capture that trims down to only
#: these matched the anchor and nothing else, which is not a name.
ANCHOR_ONLY = frozenset({"self", "storage", "ss", "self-storage"})


def tidy_property_name(raw: str) -> str:
    """Trim a captured name back to just the name.

    `\\s` inside the capture class matches newlines, so the whole-document
    search that produced these ran straight through line breaks: a cover
    reading "CONFIDENTIAL OFFERING MEMORANDUM / Expo Storage" stored both
    lines, separator and all, as the property's name. That name is the
    deal's identity everywhere downstream — the folder, the memo's title,
    the pipeline table, the comps match — so it was wrong in every one of
    them at once.

    Boilerplate is dropped from the LEFT because the anchor is on the
    right. What survives is capped at `MAX_NAME_WORDS`, keeping the
    RIGHTMOST words for the same reason: whatever the capture over-ate is
    at the front, and the anchor is at the back."""
    name = norm_text(raw)                       # newlines → single spaces
    tokens = [t for t in name.split(" ") if t]
    # ONE loop over both rules, not one loop each: a real banner mixes
    # them — "OFFERING MEMORANDUM 4 Properties Expo Storage" — and two
    # sequential passes stop at the first token the pass they are in does
    # not recognise, leaving the rest of the prefix in the name.
    while tokens:
        head = tokens[0]
        # A leading token holding a digit is an address number or a
        # portfolio count, never the start of a name.
        if (head.strip(".,-|:").lower() in NAME_NOISE
                or any(ch.isdigit() for ch in head)):
            tokens.pop(0)
            continue
        break
    if len(tokens) > MAX_NAME_WORDS:
        tokens = tokens[-MAX_NAME_WORDS:]
    # Nothing but the anchor left. "Self Storage" is what the pattern
    # matched ON, not a property's name — a banner line reading "FOR SALE
    # Storage" trims down to exactly this, and returning it would put the
    # word Storage in the pipeline table as a deal.
    if all(t.strip(".,-|:").lower() in ANCHOR_ONLY for t in tokens):
        return ""
    return " ".join(tokens).strip(" .,-|:")


#: **Spaces, never `\s`** — in BOTH patterns. This one character is the
#: whole defect: `\s` matches a newline, and the capture is anchored on
#: "Storage" at its right end, so a match beginning at the first capital
#: letter on a cover page ran forward THROUGH every line break until it
#: found one. A cover reading "CONFIDENTIAL OFFERING MEMORANDUM / Expo
#: Storage" stored both lines, separator included, as the name.
#:
#: Scanning line-by-line would fix it equally well and is NOT done here,
#: deliberately: two defenses against one failure mean neither can be
#: tested — remove either alone and the other still holds, so no mutation
#: fails and the suite stops saying anything about which one works.
_NAME_CLASS = r"[A-Z][A-Za-z0-9 \-\']+"

#: Label-led, so it can be trusted anywhere in the document. The label
#: itself is case-insensitive — it was not, which meant the title-cased
#: "Property Name:" that CIMs actually print never matched. That went
#: unnoticed because the anchored pattern below usually reaches the same
#: answer from the other side; it does not when the name ends in "SS",
#: which only the labelled pattern accepts.
_NAME_LABELLED = re.compile(
    r"(?i:property|facility|asset)\s*(?i:name)?[:\s]+"
    rf"({_NAME_CLASS}(?:Self[ \-]?Storage|Storage|SS))")

#: Unlabelled: a name ending in "Storage".
_NAME_ANCHORED = re.compile(rf"({_NAME_CLASS}(?:Self[ \-]?Storage|Storage))")


def _find_property_name(text: str, pages: list = None) -> str | None:
    """Cover page first, whole document second — the same order
    `extract.location` reads for city/state, and for the same reason: the
    name on page 1 is the property's, while a match from page 40 is as
    likely to be the seller's other facility or a comp's."""
    for scope in ([pages[0]] if pages else []) + [text]:
        for rx in (_NAME_LABELLED, _NAME_ANCHORED):
            # finditer, not search: a first match that tidies away to
            # nothing (a banner line that was all boilerplate) must hand
            # off to the NEXT match, not abandon the pattern. With
            # `search` the fallback below re-runs the same pattern from
            # position 0, finds the same doomed match, and the document's
            # real name — two lines down — is never looked at.
            for m in rx.finditer(scope or ""):
                name = tidy_property_name(m.group(1))
                if name:
                    return name
    return None


def _parse_property_basics(text: str, data: CIMData, pages: list = None):
    """Extract property name, address, year built, acreage.

    `pages` is the per-page text list; the property name and the city/state
    lookup both read the cover page first and only fall back to the body.
    Callers without it degrade to the old whole-document behaviour rather
    than failing."""

    name = _find_property_name(text, pages)
    if name:
        data.property_name = name

    # Street address — DELIBERATELY NOT EXTRACTED. `data.address` stays None.
    #
    # This held a whole-document `re.search` for a street pattern: the very
    # approach the comment below records as abandoned for city/state, left in
    # place for the one field that gates the whole demographic chain.
    # `enrich_cim_data` requires address AND city AND state before it geocodes,
    # so the weakest extractor in the module decided where the 3-mile ring was
    # centred — and a broker's metro office geocodes tens of miles away and
    # returns a LARGE population, so gate 1 failed OPEN. A false FAIL gets
    # noticed; a false PASS does not. It also carried no re.IGNORECASE while
    # every sibling pattern here does, so it skipped ALL-CAPS covers and was
    # biased toward the Title-Case broker blocks by construction.
    #
    # Deleting it leaves address None, which short-circuits that `and` chain:
    # no geocode on a broker's office, and gate 1 renders TBD. The ring is
    # centred on the subject property or there is no ring. Precision comes back
    # through the ZIP below, which this module already extracts and the parser
    # used to discard. Do not reinstate a street extractor here without reading
    # extract/location.py's docstring first — that module exists to throw
    # street lines AWAY, and its STREET_SUFFIX means "this is not a city".

    # City, State, ZIP — see extract/location.py. The whole-document regex this
    # replaced returned the broker's disclaimer-page address as often as the
    # property's, could not match an ALL-CAPS cover ("MAXWELL, TX"), and let the
    # street line run into the city ("East Pikes Peak Avenue Colorado Springs").
    city, state, zip_code, _src = best_city_state(pages if pages else text)
    if city:
        data.city, data.state = city, state
        data.zip_code = zip_code

    # Year built
    yb_pat = r"(?:year\s+built|built\s+in|constructed\s+in|vintage)[:\s]*(\d{4})"
    m = re.search(yb_pat, text, re.IGNORECASE)
    if m:
        data.year_built = int(m.group(1))

    # Year expanded
    exp_pat = r"(?:expanded|expansion|addition)\s+(?:in\s+)?(\d{4})"
    m = re.search(exp_pat, text, re.IGNORECASE)
    if m:
        data.year_expanded = int(m.group(1))

    # Acreage
    ac_pat = r"(\d+\.?\d*|\d*\.\d+)\s*(?:acres?|ac\b)"
    m = re.search(ac_pat, text, re.IGNORECASE)
    if m:
        try:
            data.acreage = float(m.group(1))
        except ValueError:
            pass


#: Extraction bounds for the size fields, parser-local for the same reason
#: MIN_PLAUSIBLE_ASKING_PRICE is: they are extraction bounds, not investment
#: criteria, and a config key would owe the assumption register a row.
#: The floor is what turns `Number of Stories 1 Net Rentable SF` from
#: `nrsf = 1` — which ran end-to-end, dividing every $/SF benchmark by 1 —
#: into a refusal at `analysis.fills.require_underwritable` with the right
#: reason. The smallest real facility in the corpus is ~5,400 SF / 16 units;
#: the largest is ~410,000 SF / ~2,000 units. Units gets no floor: no small
#: number ever sits adjacent to a units label in the corpus (measured), and
#: an ungrounded floor is exactly the kind of rule the mutation bar removes.
MIN_PLAUSIBLE_NRSF = 1_000
MAX_PLAUSIBLE_NRSF = 2_000_000
MAX_PLAUSIBLE_UNITS = 5_000

#: The label must say RENTABLE (or NRSF) — a bare `SF` would match site work,
#: retail pads and `Warehouse SF`. Separators are `[\s\-]*` because the MNET
#: text layer glues tokens (`RentableSF`) and prose hyphenates
#: (`rentable-square-feet`).
_NRSF_LABEL = (
    r"(?:net[\s\-]*)?rentable[\s\-]*(?:square[\s\-]*(?:feet|foot|footage)"
    r"|sq\.?[\s\-]*ft\.?|sf)|nrsf")

#: Label first: `NRSF: 84,375`, `Net Rentable SF ±45,755`, `Total NRSF
#: 45,680 Square Feet`, `RentableSF 48,762SF`. The unit word after the label
#: is OPTIONAL — requiring one is why the bare forms, which is how the whole
#: MNET family states the figure, never matched. `±` sits in the tolerated
#: junk beside `~`/`≈` because it is the marker the corpus actually uses.
#: `$` is deliberately NOT tolerated: a building's size is never a dollar
#: figure, and `GPR/NRSF $12.42` must contribute nothing.
_NRSF_LABEL_FIRST_RE = re.compile(
    r"(?:total[\s\-]*)?(?:" + _NRSF_LABEL + r")"
    r"[\s:]*[~≈±]*\s*(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)

#: Value first: `84,375 NRSF`, `48,762 rentable-square-feet`. The value must
#: sit IMMEDIATELY before the label — `Number of Stories 1 Net Rentable SF
#: ±45,755` binds the 1 here, and the plausibility floor is what discards it.
_NRSF_VALUE_FIRST_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*[~≈±]?\s*"
    r"(?:total[\s\-]*)?(?:" + _NRSF_LABEL + r")", re.IGNORECASE)

#: Units label first: `Total Units 362`, `#ofUnits 434` (glued), `Unit
#: Count: 96`. A bare qualified prefix is required — `Occupied Units` and
#: the demographics tables' `2PersonUnits` must not match.
_UNITS_LABEL_FIRST_RE = re.compile(
    r"(?:(?:total|#[\s\-]*of|number[\s\-]*of)[\s\-]*(?:storage[\s\-]*)?units?"
    r"|unit[\s\-]*count)\b[\s:]*[~≈±]*\s*(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)

#: Units value first: `434 units`, `434 Total Units`. Adjacency does the
#: work: `5 uncovered parking units` and `1 PersonUnits` put a word between
#: the number and the label and must not match.
#: The lookbehind refuses the top of a RANGE: `50-66 units, doubling the
#: asset's long-term earning capacity` is a projection whose segment carries
#: no qualifier word, and a range's upper bound is not a unit count.
_UNITS_VALUE_FIRST_RE = re.compile(
    r"(?<![\d.,\-–])(\d[\d,]*(?:\.\d+)?)\s*[~≈±]?\s*"
    r"(?:total[\s\-]*)?(?:storage[\s\-]*)?units\b", re.IGNORECASE)


def _pick_ranked(cands):
    """Resolve ranked (rank, value) candidates: the best rank wins outright,
    and within it the surviving values must agree on exactly ONE number after
    de-dup. Two distinct label-first statements of the building's size is a
    portfolio deck quoting per-property figures or a broken read, and both
    refuse rather than guess — the house rule, same as `_resolve_vintaged`."""
    if not cands:
        return None
    best = min(r for r, _ in cands)
    values = {v for r, v in cands if r == best}
    return values.pop() if len(values) == 1 else None


#: A facility spec card: the `GrossSF: 64,850SF RentableSF: 61,607 SF` /
#: `LotSize: 5.03Acres RentableSF: 51,558SF` rows that MNET prints one per
#: facility in the sale-comps section. Their RentableSF is some OTHER
#: building's, so a card row is demoted to a last-resort rank — consulted
#: only when the deck states nothing else (a subject-only card still reads),
#: and never allowed to contradict the subject's own statement into a
#: refusal. Columbus states `Net Rentable Square Feet 70,102` three times
#: and then prints six comp cards; without the demotion the disagreement
#: rule would refuse the whole deck.
_SPEC_CARD_RE = re.compile(r"gross[\s\-]*sf|lot[\s\-]*size", re.IGNORECASE)

#: The rank a spec-card row is demoted to.
_RANK_SPEC_CARD = 3

#: A figure qualified as a PROJECTION is not the facility: `5,400 NRSF
#: expansion underway` is the addition, not the building, and `Combined
#: Future Unit Count 116-132` counts pads that do not exist. Both shipped as
#: bookings in the first cut of these rules, which is why the qualifier
#: refusal is segment-scoped rather than line-scoped: a bullet line can say
#: `Total Net Rentable Square Feet N • Room for Future Expansion
#: Opportunities` and the total is still the total.
#: "expansion"/"expanded" are deliberately NOT here: `Year Expanded 2023`
#: sits on the same unsegmented spec line as the real NRSF and units.
#:
#: The vocabulary is MEASURED, not imagined: every entry fires alone on at
#: least one corpus line (and one test), and entries that could not — the
#: ones that only ever co-fired with another (`proforma`, `opportunity`,
#: `develop`), the ones no corpus line uses at all (`proposed`, `planned`,
#: `projected`), and ligature-dropped "potential" (`Poten al`), whose every
#: corpus firing guards a range the units lookbehind already refuses — came
#: out under the mutation bar. A projection word this list lacks is a
#: future deck's problem to demonstrate, not this list's to anticipate.
_PROJECTION_RE = re.compile(
    r"future|underway|approved|up\s+to|\badd(?:ed|ing)?\b",
    re.IGNORECASE)

_SEGMENT_SPLIT_RE = re.compile(r"[•|]")

#: A count qualified as a SUBTYPE is a breakdown row, not the total, and one
#: deck states both under the same label: `Number of Units: 242 total` and
#: `Number of Units: 69 non-climate controlled self-storage units`. Read
#: flat, those are two rank-1 statements that disagree, so the whole deck
#: refuses over a row that was never claiming to be the total. Demoted, not
#: dropped — a deck whose only count is a subtype row still gets read, the
#: same bargain the spec card gets.
_UNITS_SUBTYPE_RE = re.compile(r"(?:non[\s\-]*)?climate[\s\-]*controlled",
                               re.IGNORECASE)

#: How far past the value the qualifier may sit. It follows the value in the
#: one corpus line that needs it; the mirror form (`Climate Controlled Units
#: N`, Little Rock and Midland) produces no candidate at all under the label
#: rules, so no rule is written for a position nothing exercises.
#: 30 is also the measured CEILING, not a guess: Crowley states its genuine
#: total with `... units and offers a balanced mix of climate-controlled`,
#: whose qualifier begins exactly 30 characters out (first excluded offset),
#: and Kerrville's amenity sentence follows its total at 70. A wider window
#: demotes real counts; `test_a_distant_subtype_mention_does_not_demote_the_
#: count` pins both layouts.
_SUBTYPE_WINDOW = 30


def _segment(line: str, pos: int) -> str:
    """The bullet-delimited span of `line` containing offset `pos`."""
    start = 0
    for sm in _SEGMENT_SPLIT_RE.finditer(line):
        if sm.start() > pos:
            return line[start:sm.start()]
        start = sm.end()
    return line[start:]


def _ranked_candidates(text, label_first_re, value_first_re, lo, hi,
                       subtype_re=None):
    """All plausible (rank, value) pairs for a size field. Matching is
    per-line, which is the ONE implementation of "the label and its value
    share a line" (a trailing `NRSF` on a cover line must not adopt the next
    line's number) and lets a spec-card row demote everything it carries.
    Rates need no rule of their own: `7.13 NRSF` per capita and `$4.63`/SF
    all fall below MIN_PLAUSIBLE_NRSF, and the plausibility band refuses
    them the same way it refuses a story count."""
    cands = []
    prev_spec = False
    for line in text.split("\n"):
        # The card shape can wrap: `Lot Size: 3Acres GrossSF: 4,079SF` with
        # its `RentableSF: 3,671 SF` on the NEXT text line, so the demotion
        # carries one line forward.
        this_spec = bool(_SPEC_CARD_RE.search(line))
        demoted = this_spec or prev_spec
        prev_spec = this_spec
        for rank, pat in ((1, label_first_re), (2, value_first_re)):
            for m in pat.finditer(line):
                if _PROJECTION_RE.search(_segment(line, m.start())):
                    continue
                val = _parse_number(m.group(1))
                if not val or not lo <= val <= hi:
                    continue
                subtype = subtype_re is not None and subtype_re.search(
                    line[m.end():m.end() + _SUBTYPE_WINDOW])
                cands.append(
                    (_RANK_SPEC_CARD if (demoted or subtype) else rank, val))
    return cands


#: ---- Occupancy ----
#:
#: Occupancy has a BASIS — physical vs economic, square-foot vs unit — and a
#: wrong basis reads exactly like a right answer: one OM states physical 80%
#: (SQ. FT.) and economic 70%, and the block this machinery replaced stored
#: 0.70 as physical. Physical occupancy is a `require_underwritable` input
#: driving the 75% demand gate and the phys/econ mismanagement spread, so a
#: wrong-basis capture silently defeats the refusal design decision 9 built.
#: Same regime as NRSF/units above: per-line ranked candidates, the best
#: rank must agree on one value, refuse over guess.
#:
#: The physical ladder, each tier witnessed in the corpus:
#:   1  "physical occupancy", unqualified or SF-flavored — the stat-block
#:      family, `PHYSICAL OCCUPANCY (SQ. FT.): N%`
#:   2  percent-first prose, `N% physically occupied` — outranks the SF
#:      label because one deck attaches the word "physical" to its unit
#:      figure and quotes the SF basis under a basis-only label, and the
#:      golden labels take the word as the broker's own claim of basis
#:   3  "physical occupancy (UNITS)" — the unit flavor of tier 1
#:   4  SF-basis label without "physical": `Square Foot Occupancy N%` — the
#:      SF basis IS physical occupancy under the broker convention
#:   5  "unit occupancy" — demoted, not dropped: a unit-only deck still
#:      reads
#:   6  bare `Occupancy: N%` / `N% occupied` — the unqualified number a
#:      broker quotes is almost always physical (CLAUDE.md)
#: Economic gets tiers 1 (label) and 2 (prose) and deliberately NO bare
#: tier: an unqualified number is never read as economic — which is also
#: why a bare label sitting right after "Economic" is excluded below.
_OCC_RANK_PHYS_LABEL = 1
_OCC_RANK_PHYS_PROSE = 2
_OCC_RANK_PHYS_UNITS = 3
_OCC_RANK_SF_LABEL = 4
_OCC_RANK_UNIT_LABEL = 5
_OCC_RANK_BARE = 6
_OCC_RANK_ECON_LABEL = 1
_OCC_RANK_ECON_PROSE = 2

#: A label row carrying exactly TWO percent values is an operating-statement
#: row — `Economic Occupancy 70.69% 88.07%` is Current beside Year 1 — and
#: the FIRST value is the in-place figure the golden labels take. Read flat
#: it can tie a deck's own headline: one deck states `Economic Occupancy
#: 86%` on its summary page and `… 85.62% 89.00%` on its statement, two
#: rank-1 values that disagree and refuse a plainly-stated number. Demoted,
#: not dropped — the bargain the spec card and the subtype row already get:
#: a deck whose ONLY economic figure is a statement row still reads its
#: Current column.
_OCC_RANK_STATEMENT_ROW = 10

#: Three or more percent tokens in one segment is never the subject's
#: in-place figure: measured across all 58 corpus PDFs, every such segment
#: containing "occup" is a pro-forma years row (`Physical Occupancy (%)
#: 23.00% 61.00% 82.00% …`), a demographics row (`Owner Occupied 475
#: 53.98% …`), or a comp-trend table — and none sources a golden-correct
#: value. Two is a statement row (demoted above); three is noise.
_OCC_PCT_RE = re.compile(r"\d(?:\.\d+)?\s*%")

#: `year N` beside an occupancy is a projection: `Year 1 17% Economic
#: Occupancy 73%`, `EXPECTED PHYSICAL OCCUPANCY (SF) AS OF YEAR 4 92.00%`.
#: Occupancy-only vocabulary, deliberately NOT added to `_PROJECTION_RE` —
#: that list was measured for sizes, where every entry fires alone on a
#: size line; "year" would not. `AS OF YEAR END 2025` does not fire (no
#: digit after "year"), which one stat-block deck depends on.
_OCC_PROJECTION_RE = re.compile(r"year\s+\d", re.IGNORECASE)

#: What may sit between an occupancy label and its value, on ONE line: an
#: optional parenthetical (the basis — `(SQ. FT.)`, `(UNITS)`, `(%)`), an
#: optional `AS OF`/`THRU` date run, then connective junk. The `%` anchor
#: is what lets a date pass safely — in `AS OF APRIL 30, 2026 91.40%` only
#: 91.40 is followed by a percent sign. The pattern this replaced could
#: cross neither, which parsed the entire stat-block family — 7 of the 13
#: repo decks — to None.
_OCC_TAIL = (r"(?:\s*\((?P<paren>[^)\n]*)\))?"
             r"(?:\s*(?:as\s+of|thru)\b[^%\n]*?)?"
             r"[\s:]*[~≈±]*\s*(?P<v>\d+(?:\.\d+)?)\s*%")

#: `[\s\-]*` between label words: one text layer glues its words
#: (`PhysicalOccupancy 24%`), another hyphenates them.
_OCC_PHYS_LABEL_RE = re.compile(
    r"physical[\s\-]*occupancy" + _OCC_TAIL, re.IGNORECASE)
_OCC_ECON_LABEL_RE = re.compile(
    r"economic[\s\-]*occupancy" + _OCC_TAIL, re.IGNORECASE)
#: Only the spelled-out form: `SF Occupancy` appears in the corpus solely
#: inside the slash dual (read there) and in valueless rent-roll header
#: rows (`SIZE TYPE OCCUPIED VACANT SQ. FT. OCCUPANCY`), so the
#: abbreviated branches could never fire alone and came out under the
#: mutation bar.
_OCC_SF_LABEL_RE = re.compile(
    r"square[\s\-]*foot[\s\-]*occupancy" + _OCC_TAIL, re.IGNORECASE)
_OCC_UNIT_LABEL_RE = re.compile(
    r"unit[\s\-]*occupancy" + _OCC_TAIL, re.IGNORECASE)
_OCC_BARE_LABEL_RE = re.compile(r"occupancy" + _OCC_TAIL, re.IGNORECASE)

#: The bare tier is excluded when its label sits right after "Economic" —
#: that is the economic label's own tail, and reading it as bare is how an
#: economic-only deck fills BOTH fields with the same number. The other
#: basis prefixes need no exclusion: their tiers share `_OCC_TAIL`, so the
#: bare copy succeeds exactly when the specific tier does, lands at a worse
#: rank with the same value, and changes nothing.
_OCC_BARE_EXCLUDE_RE = re.compile(r"economic[\s\-]*$", re.IGNORECASE)

#: Percent-first prose: `92.3% physically occupied by square footage`,
#: `Currently 20% Economically Occupied`. The lookbehind refuses a range's
#: second half (`95-100% occupied`). The bare form's `total` branch is a
#: witnessed layout of its own — `627 Units 57% Total Occupancy` is the
#: only line its deck states the figure on.
_OCC_PHYS_VF_RE = re.compile(
    r"(?<![\d.,\-–—])(?P<v>\d+(?:\.\d+)?)\s*%\s*"
    r"physical(?:ly)?[\s\-]*occup(?:ied|ancy)", re.IGNORECASE)
_OCC_ECON_VF_RE = re.compile(
    r"(?<![\d.,\-–—])(?P<v>\d+(?:\.\d+)?)\s*%\s*"
    r"economic(?:ally)?[\s\-]*occup(?:ied|ancy)", re.IGNORECASE)
_OCC_BARE_VF_RE = re.compile(
    r"(?<![\d.,\-–—])(?P<v>\d+(?:\.\d+)?)\s*%\s*"
    r"(?:total[\s\-]*)?occup(?:ied|ancy)\b", re.IGNORECASE)

#: Slash dual labels zip to their values positionally: `Economic Occupancy
#: / Physical Occupancy (March 31, 2026) 53% / 72%` is econ 53, phys 72.
#: The plain patterns must SKIP such a line — the physical label alone
#: would bind the FIRST value and book the wrong basis at rank 1.
_OCC_DUAL_RE = re.compile(
    r"(?P<l1>economic|physical|sf|unit)[\s\-]*occupancy\s*/\s*"
    r"(?P<l2>economic|physical|sf|unit)[\s\-]*occupancy"
    r"(?:\s*\([^)\n]*\))?"
    r"[\s:]*(?P<v1>\d+(?:\.\d+)?)\s*%\s*/\s*(?P<v2>\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE)

#: A per-property banner: `Biloxi - 92% Physical Occupancy / 80% Economic
#: Occupancy / …` quotes ONE property of a portfolio, not the offering.
#: Measured across all 58 corpus PDFs this shape opens exactly one
#: occupancy-bearing line, so the veto is contingent on that measurement
#: and scoped to percent-first candidates — the only kind such a line
#: yields.
_OCC_BANNER_RE = re.compile(r"^\s*[A-Z][A-Za-z]+\s+-\s")

#: Basis parenthetical flavor, consulted by the PHYSICAL tier only: every
#: corpus `(UNITS)` parenthetical sits on a `PHYSICAL OCCUPANCY` label, so
#: the bare tier classifying its own parens would be a rule nothing
#: exercises. An SF parenthetical reads the same as no parenthetical.
_OCC_UNIT_PAREN_RE = re.compile(r"unit", re.IGNORECASE)

#: Physical occupancy is a share of the building: 0..1, both ends inclusive
#: — a stated 0% is an honestly-reported pre-lease-up asset (design
#: decision 9) and 100.00% is stated twice in the corpus. Economic may
#: honestly exceed 1.0 — one deck states 105.9% against gross potential —
#: so its ceiling is a sanity bound, not a censor: downstream checks flag
#: the excess, the parser reports what the deck states.
_OCC_MAX_PHYS = 1.0
_OCC_MAX_ECON = 1.5


def _occ_phys_label_rank(line, m):
    paren = m.group("paren")
    if paren and _OCC_UNIT_PAREN_RE.search(paren):
        return _OCC_RANK_PHYS_UNITS
    return _OCC_RANK_PHYS_LABEL


def _occ_bare_label_rank(line, m):
    if _OCC_BARE_EXCLUDE_RE.search(line[:m.start()]):
        return None
    return _OCC_RANK_BARE


#: (field, pattern, percent-first?, rank). No spec-card demotion here on
#: purpose: no comp card in either corpus states an occupancy, so there is
#: nothing for it to guard — measured, not forgotten.
_OCC_SPECS = (
    ("phys", _OCC_PHYS_LABEL_RE, False, _occ_phys_label_rank),
    ("phys", _OCC_PHYS_VF_RE, True, lambda line, m: _OCC_RANK_PHYS_PROSE),
    ("phys", _OCC_SF_LABEL_RE, False, lambda line, m: _OCC_RANK_SF_LABEL),
    ("phys", _OCC_UNIT_LABEL_RE, False,
     lambda line, m: _OCC_RANK_UNIT_LABEL),
    ("phys", _OCC_BARE_LABEL_RE, False, _occ_bare_label_rank),
    ("phys", _OCC_BARE_VF_RE, True, lambda line, m: _OCC_RANK_BARE),
    ("econ", _OCC_ECON_LABEL_RE, False,
     lambda line, m: _OCC_RANK_ECON_LABEL),
    ("econ", _OCC_ECON_VF_RE, True, lambda line, m: _OCC_RANK_ECON_PROSE),
)


def _occ_add(field, rank, val, phys_cands, econ_cands):
    if field == "econ":
        if 0.0 <= val <= _OCC_MAX_ECON:
            econ_cands.append((rank, val))
    elif 0.0 <= val <= _OCC_MAX_PHYS:
        phys_cands.append((rank, val))


def _occupancy_candidates(text):
    """All plausible (rank, value) occupancy candidates, physical and
    economic, for `_pick_ranked`. A PARALLEL walk to `_ranked_candidates`,
    not a generalization of it: that function's falsy value-drop is right
    for sizes and lethal here, where a stated 0 must survive to the demand
    gate — and leaving it untouched keeps the size tests as machine proof
    that NRSF/units did not move."""
    phys_cands, econ_cands = [], []
    for line in text.split("\n"):
        dm = _OCC_DUAL_RE.search(line)
        if dm:
            for label, raw in ((dm.group("l1"), dm.group("v1")),
                               (dm.group("l2"), dm.group("v2"))):
                label = label.lower()
                val = float(raw) / 100.0
                if label.startswith("economic"):
                    _occ_add("econ", _OCC_RANK_ECON_LABEL, val,
                             phys_cands, econ_cands)
                elif label.startswith("unit"):
                    _occ_add("phys", _OCC_RANK_UNIT_LABEL, val,
                             phys_cands, econ_cands)
                elif label.startswith("physical"):
                    _occ_add("phys", _OCC_RANK_PHYS_LABEL, val,
                             phys_cands, econ_cands)
                else:
                    _occ_add("phys", _OCC_RANK_SF_LABEL, val,
                             phys_cands, econ_cands)
            continue
        banner = bool(_OCC_BANNER_RE.match(line))
        for field, pat, value_first, rank_of in _OCC_SPECS:
            for m in pat.finditer(line):
                if value_first and banner:
                    continue
                seg = _segment(line, m.start())
                if (_PROJECTION_RE.search(seg)
                        or _OCC_PROJECTION_RE.search(seg)):
                    continue
                pcts = len(_OCC_PCT_RE.findall(seg))
                if pcts >= 3:
                    continue
                rank = rank_of(line, m)
                if rank is None:
                    continue
                if not value_first and pcts == 2:
                    rank += _OCC_RANK_STATEMENT_ROW
                _occ_add(field, rank, float(m.group("v")) / 100.0,
                         phys_cands, econ_cands)
    return phys_cands, econ_cands


def _parse_size_occupancy(text: str, data: CIMData):
    """Extract NRSF, unit counts, CC split, occupancy."""

    # NRSF and total units — ranked candidates, the shape _parse_pricing
    # establishes: label-first outranks value-first, the winning rank must
    # agree on one value, and the plausibility band bounds everything. What
    # this replaced required a unit word after the label (so `NRSF: 84,375`
    # never matched), did not tolerate `±`, and read the number BEFORE the
    # label — on `Number of Stories 1 Net Rentable SF ±45,755` it booked the
    # story count as the building, while `Total Units` took the 45,755.
    nrsf = _pick_ranked(_ranked_candidates(
        text, _NRSF_LABEL_FIRST_RE, _NRSF_VALUE_FIRST_RE,
        MIN_PLAUSIBLE_NRSF, MAX_PLAUSIBLE_NRSF))
    if nrsf is not None:
        data.nrsf = float(nrsf)

    units = _pick_ranked(_ranked_candidates(
        text, _UNITS_LABEL_FIRST_RE, _UNITS_VALUE_FIRST_RE,
        1, MAX_PLAUSIBLE_UNITS, _UNITS_SUBTYPE_RE))
    if units is not None:
        data.total_units = int(units)

    # Climate-controlled percentage
    cc_pat = r"([\d\.]+)\s*%\s*(?:climate[\s\-]?controlled|CC)"
    m = re.search(cc_pat, text, re.IGNORECASE)
    if m:
        data.cc_pct = float(m.group(1)) / 100.0

    # Occupancy — the ranked-candidate regime above. The block this
    # replaced took the first regex hit document-wide: its optional
    # `(?:physical\s+)?` prefix let an economic-only deck fill BOTH fields,
    # a basis parenthetical it could not cross parsed the whole stat-block
    # family to None, and `[:\s]*` crossing newlines let a stat-card label
    # adopt the next line's number.
    phys_cands, econ_cands = _occupancy_candidates(text)
    phys = _pick_ranked(phys_cands)
    if phys is not None:
        data.physical_occupancy = phys
    econ = _pick_ranked(econ_cands)
    if econ is not None:
        data.economic_occupancy = econ


#: Below this, a "price" is a price PER something — per SF, per unit, per
#: acre — or the first number off an unrelated next line (a street number, a
#: page number, an acreage). Measured over the local corpus, the floor is what
#: refuses `List Price` over `$122,155 $336,068 $403,658` (a rent table),
#: `PURCHASE PRICE` over `11017 County Line Road`, and
#: `OFFERING PRICE: CONTACT VERSAL FOR PRICING` over `ADDRESS: 20603 CLAY RD`.
#: The smallest genuine offering in the corpus is $1.3M, so this sits an order
#: of magnitude below anything real while clearing every per-unit figure seen
#: ($8,863 / $14,376 / $24,007). It lives here rather than in config.py for
#: the same reason MAX_NAME_WORDS does: it is an extraction bound, not an
#: investment criterion, and a key under config.GATES would owe the
#: assumption register a row it has no business having.
MIN_PLAUSIBLE_ASKING_PRICE = 250_000

#: A price label qualified as covering the WHOLE offering. Ranked above a bare
#: one because a portfolio CIM states both, and taking the first in document
#: order books one building as the deal — Bastrop Guardian quotes
#: `Purchase Price: $1,600,000` for one property and
#: `The purchase price for all three properties $3,500,000` for the offering.
_PRICE_SCOPE_RE = re.compile(
    r"portfolio|combined|entire|all\s+(?:\w+\s+)?(?:properties|sites|facilities)"
    r"|both\s+propert", re.IGNORECASE)

#: Rank 2 — a specific offering label. `list(?:ing)?\s*` with the space
#: OPTIONAL is the ListingPrice fix: pdfplumber renders the MNET offering
#: summary with the space stripped, so `list\s+price` could never match.
_PRICE_LABEL_SPECIFIC = re.compile(
    r"^\W*(?:the\s+)?(?:estimated?\s+)?"
    r"(?:portfolio|total|asking|offering|list(?:ing)?|sale|"
    r"purchase|offered)\s*pric(?:e|ing)\b", re.IGNORECASE)

#: Rank 3 — a bare `Price` / `Pricing` label, consulted only when no specific
#: one produced a value. `(?:the\s+)?` and the `\W*` bullet allowance are
#: deliberately the ONLY things permitted before it: `A median home price of
#: roughly $340,000` and `reassessed at the purchase price.` are both prose
#: carrying a plausible number, and both are refused by the anchor alone.
_PRICE_LABEL_BARE = re.compile(r"^\W*pric(?:e|ing)\b", re.IGNORECASE)

#: Immediately after the label, these make it a rate rather than a price.
_PRICE_UNIT_SUFFIX = re.compile(
    r"^\s*(?:[:\-]|\bis\b)?\s*(?:per|/|\bpsf\b|\bper\b)?\s*"
    r"(?:sf|psf|sq\.?\s*ft|square\s+(?:foot|feet)|rentable|unit|acre|nrsf|"
    r"month|door)\b|^\s*/\s*|^\s*psf\b", re.IGNORECASE)

#: `$4.5 million` / `$4.5MM` / `$4.5M`. The suffix must stand ALONE directly
#: after the number: the rule this replaces re-scanned a five-character window,
#: so `Asking Price $675 MSA Median Household Income` multiplied by a million
#: off the M of "MSA". The `\b` is what does that work — it is not the letter's
#: case, which is why there is one pattern here and not two.
_MILLIONS_RE = re.compile(r"^\s*(?:million|mm|m)\b", re.IGNORECASE)

_MONEY_TOKEN_RE = re.compile(r"\$?\s*(\d[\d,]*(?:\.\d+)?)")


def _first_price_in(fragment: str) -> Optional[float]:
    """First money-shaped token in `fragment`, scaled if it is quoted in
    millions. Percentages are skipped — `PRICE AVAILABLE` sits above `7%`."""
    for m in _MONEY_TOKEN_RE.finditer(fragment):
        rest = fragment[m.end():]
        if rest[:1] == "%" or rest[:2] in (" %",):
            continue
        val = _parse_number(m.group(1))
        if not val:
            continue
        if _MILLIONS_RE.match(rest):
            val *= 1_000_000
        return val
    return None


def _parse_pricing(text: str, data: CIMData):
    """Extract the asking price for the WHOLE offering.

    Ranked by label specificity rather than document order, because the two
    disagree: MNET DECATUR prints a per-SF price above the real one, and a
    portfolio CIM prints a per-property price above the offering price. Rank
    wins; within a rank the first occurrence wins.
    """
    lines = text.split("\n")
    best_rank, best_val = 99, None

    for i, line in enumerate(lines):
        m = _PRICE_LABEL_SPECIFIC.match(line)
        rank = 2 if m else None
        if not m:
            m = _PRICE_LABEL_BARE.match(line)
            rank = 3 if m else None
        if not m:
            continue

        tail = line[m.end():]
        if _PRICE_UNIT_SUFFIX.match(tail):
            continue                      # a rate, not a price
        if rank == 2 and _PRICE_SCOPE_RE.search(line):
            rank = 1                      # covers the whole offering

        val = _first_price_in(tail)
        if val is None or val < MIN_PLAUSIBLE_ASKING_PRICE:
            # Header-row form: the label heads a column and the value sits in
            # the row beneath (`ListingPrice CapRate (Year One)` over
            # `$3,500,000 7.83% 434`). Reached whenever the label's own line
            # yields no PLAUSIBLE price — not merely when it holds no digits —
            # because `LIST PRICE 2025 NOI STABILIZED MARGIN` is a header row
            # whose only same-line number is the year of the NOI column.
            for probe in lines[i + 1:i + 3]:
                if not re.search(r"\d", probe):
                    continue      # a wrapped header cell, e.g. `(STABILIZED)`
                nxt = _first_price_in(probe)
                if nxt is not None and nxt >= MIN_PLAUSIBLE_ASKING_PRICE:
                    val = nxt
                # The FIRST row carrying digits decides, whether or not it
                # yielded a price. Scanning past it for something plausible is
                # how a label ends up owning a number from another table.
                break
        if val is None or val < MIN_PLAUSIBLE_ASKING_PRICE:
            continue
        if rank < best_rank:
            best_rank, best_val = rank, val

    if best_val is not None:
        data.asking_price = best_val


#: One radius column heading: `3 Miles`, `3Miles`, `1-MILE`, `0.3 mi`,
#: `5 Mile Radius`. The trailing `radius` is absorbed so it cannot break the
#: run-adjacency test below.
_RADIUS_TOKEN_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[-–]?\s*mi(?:le)?s?\b(?:\s*radius)?", re.IGNORECASE)

#: A vintage marker at the START of a line: `2024 Estimate`, `2030 Projection`,
#: `2020 Census`. Requires whitespace after the year so the growth-rate row
#: `2024-2029: Population: Growth Rate ...` cannot set a vintage.
_VINTAGE_LINE_RE = re.compile(
    r"^\s*((?:19|20)\d{2})\s+(?:estimate|projection|census|summary|acs|"
    r"population|median|average|total)", re.IGNORECASE)
_VINTAGE_PREFIX_RE = re.compile(
    r"^\s*((?:19|20)\d{2})\s+(?:estimate|projection|census|summary|acs)?\s*",
    re.IGNORECASE)
_YEAR_ANYWHERE_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_NUM_TOKEN_RE = re.compile(r"\$?\s*(\d[\d,]*(?:\.\d+)?)")
_REGION_END_RE = re.compile(r"^\s*(?:={5,}|-{3}\s*PAGE\b)")

#: How far a header's columns are taken to reach. Measured: the widest real
#: demographics block in the corpus puts its last population row 11 lines
#: under the header.
_REGION_LINES = 14


def _radius_columns(line: str) -> list[float]:
    """The radii a header line declares, or [] if it declares none.

    The discriminator is ADJACENCY: a real header is a run of radius tokens
    with nothing but whitespace between them, in ascending order. Prose and
    comp tables produce radius tokens too — `3 miles East of Bastrop Proper
    and 130 miles Northwest of Houston`, `DISTANCE ~1.75 MILES DISTANCE ~2.30
    MILES`, `5 PUBLIC STORAGE 2901 MILES ROAD 99,833 3.3 MILES` — and every
    one of them has words between the tokens, so none forms a run.

    A multi-panel header repeats its radii (`POPULATION 1Mile 3Miles 5Miles
    HOUSEHOLDSBYINCOME 1Mile 3Miles 5Miles`); the FIRST run is the panel whose
    rows start at column 0, which is the only panel a line-based read can
    attribute correctly.
    """
    run, prev_end = [], None
    for m in _RADIUS_TOKEN_RE.finditer(line):
        adjacent = prev_end is not None and not line[prev_end:m.start()].strip()
        val = float(m.group(1))
        if adjacent and val > run[-1]:
            run.append(val)
        else:
            if len(run) >= 2:
                return run          # first complete run wins
            run = [val]
        prev_end = m.end()
    return run if len(run) >= 2 else []


def _row_label_and_values(line: str, want: int) -> tuple[str, list[float]]:
    """Split a data row into its label and its first `want` numeric cells.

    Percentages are skipped: the MNET decks merge an income-distribution panel
    onto the same text line (`Total Population 19,398 59,666 132,134
    $250,000 or More 15.6% 14.2% 11.6%`), and taking cells positionally past
    the panel boundary would read one panel's header into another's row.
    """
    body = _VINTAGE_PREFIX_RE.sub("", line)
    label, values = None, []
    for m in _NUM_TOKEN_RE.finditer(body):
        rest = body[m.end():]
        # A percentage belongs to a neighbouring panel, and `25+` is an age
        # band glued to its own label (`Population 25+ by Education Level`) —
        # neither ends the label, and treating either as a value shifts every
        # column one to the left.
        if rest[:1] in ("%", "+") or rest[:2] == " %":
            continue
        if label is None:
            label = body[:m.start()].strip(" :.-\t")
        values.append(_parse_number(m.group(1)))
        if len(values) == want:
            break
    return (label or ""), values


def _is_population_label(label: str) -> bool:
    """Only a bare population count. `Daytime Population`, `Population Age
    25+`, `Population By Age` and `Population: Growth Rate` all name something
    else, and every one of them appears in the corpus beside the real row."""
    lab = re.sub(r"^(?:total|est\.?|estimated|current)\s+", "",
                 label.lower().strip())
    return lab.strip(" :.-") == "population"


def _is_median_hhi_label(label: str) -> bool:
    """Median household income — not the AVERAGE, which sits directly beside
    it in most decks and is the number the old pattern was picking up."""
    lab = label.lower()
    return "median" in lab and bool(
        re.search(r"h(?:ousehold|h)\s*(?:income)|hhi", lab))


def _resolve_vintaged(candidates: list[tuple[Optional[int], float]]
                      ) -> Optional[float]:
    """Pick the value for the most recent NON-projected vintage, or refuse.

    Refusing is the correct output, not a failure: `extract.enrichment`
    returns a tier-1 value the moment it is not None, so a guessed population
    permanently suppresses the Census lookup AND stamps itself `CIM/override`
    in the source log. A wrong number here is strictly worse than no number.
    """
    this_year = _date.today().year
    dated = [(v, x) for v, x in candidates if v is not None and v <= this_year]
    if dated:
        newest = max(v for v, _ in dated)
        values = {x for v, x in dated if v == newest}
    else:
        values = {x for v, x in candidates if v is None}
    return values.pop() if len(values) == 1 else None


def _parse_demographics(text: str, data: CIMData):
    """Extract population by radius and the median HHI, by reading the
    demographics table as a table.

    The pattern this replaces read digits out of the COLUMN HEADINGS: against
    `POPULATION 3Miles 5Miles 10Miles` it captured the 5 of `5Miles` as the
    3-mile population. Measured over 45 local CIMs it wrote a value under
    1,000 on 21 of them — including `5` for a deal whose true 3-mile
    population is 69,451, failing gate 1 on a deal that clears it by 39%.
    """
    lines = text.split("\n")
    pop_cands: dict[str, list] = {"1": [], "3": [], "5": []}
    hhi_cands: list = []

    for h, header in enumerate(lines):
        radii = _radius_columns(header)
        if not radii:
            continue
        header_vintage = None
        ym = _YEAR_ANYWHERE_RE.search(header)
        if ym:
            header_vintage = int(ym.group(1))
        pending = header_vintage

        for line in lines[h + 1:h + 1 + _REGION_LINES]:
            if _REGION_END_RE.match(line):
                break
            vm = _VINTAGE_LINE_RE.match(line)
            if vm:
                pending = int(vm.group(1))
            label, values = _row_label_and_values(line, len(radii))
            if len(values) < len(radii):
                continue
            if _is_population_label(label):
                for radius, value in zip(radii, values):
                    key = str(int(radius)) if radius == int(radius) else None
                    if key in pop_cands:
                        pop_cands[key].append((pending, value))
            elif _is_median_hhi_label(label):
                for radius, value in zip(radii, values):
                    if radius == 3:
                        hhi_cands.append((pending, value))

    for key, attr in (("1", "population_1mi"), ("3", "population_3mi"),
                      ("5", "population_5mi")):
        resolved = _resolve_vintaged(pop_cands[key])
        if resolved is not None:
            setattr(data, attr, int(resolved))

    resolved_hhi = _resolve_vintaged(hhi_cands)
    if resolved_hhi is not None:
        data.median_hhi_3mi = resolved_hhi


# How many months of actuals the TTM figures cover. A month count in a
# CIM is not automatically THIS month count — the same document quotes
# lease terms, street-rate windows and momentum figures in months — so
# extraction is deliberately conservative on three axes, and every one
# of them exists because a naive version stored a WRONG number rather
# than merely missing one (review finding, 2026-08-10).
_TTM_MONTHS_PATTERNS = (
    r"\bT-?(\d{1,2})\s+annualized\b",
    r"\b(\d{1,2})\s+months?\s+(?:ending|ended|annualized)\b",
    r"\btrailing\s+(\d{1,2})[-\s]month",
)

#: 1. ANCHOR. The count must sit in a sentence that is talking about the
#: financial statements. "Street rates rose 4% in the 6 months ended
#: June 30" is a rent-trend sentence, and reading a TTM basis out of it
#: describes the wrong figure entirely.
_TTM_MONTHS_ANCHORS = (
    "noi", "revenue", "income", "expense", "financial", "operating",
    "actuals", "annualized", "ttm", "egr", "gpr", "cash flow",
    "p&l", "statement",
)

#: 2. KNOWN COLLISION. "T3 Annualized Revenue" / "trailing 3-month
#: revenue" is the MOMENTUM figure — this codebase models it as its own
#: field (`t3_annualized_revenue`, T3 vs T12) — and it routinely appears
#: beside a genuine trailing-twelve basis. Matching it set ttm_months=3
#: on healthy trailing-twelve deals, so the register recorded a false
#: statement about the CIM and the annualization check fired on a deal
#: with nothing wrong with it. The collocation is removed before the
#: patterns run; a T-3 basis stated WITHOUT the word "revenue" still
#: parses, because a genuine T-3 underwriting basis is exactly what the
#: check exists to flag.
_TTM_MOMENTUM_COLLOCATIONS = (
    r"\bT-?3\s+annualized\s+revenue\b",
    r"\btrailing\s+3[-\s]month\s+revenue\b",
)


def _extract_ttm_months(text: str) -> Optional[int]:
    """Months of actuals behind the TTM figures, or None.

    Never invented and never guessed: an annualization check run against
    a fabricated 12 flags nothing, and one run against a fabricated 9
    flags a deal that is fine. So this returns None for anything short
    of a single unambiguous reading, and the analyst enters the value by
    hand — the same posture `require_underwritable` takes on occupancy.

    3. CONFLICT. If the document supports two different counts, that is
    not a tie to break by pattern order — pattern priority is how "T3
    Annualized Revenue" beat an explicit "Trailing 12-month NOI" in the
    first version. Disagreement means the parser does not know.
    """
    for pattern in _TTM_MOMENTUM_COLLOCATIONS:
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)

    found = set()
    for sentence in re.split(r"[.\n]", text):
        low = sentence.lower()
        if not any(anchor in low for anchor in _TTM_MONTHS_ANCHORS):
            continue
        for pattern in _TTM_MONTHS_PATTERNS:
            for match in re.finditer(pattern, sentence, re.IGNORECASE):
                months = int(match.group(1))
                # Out of range is not a TTM basis at all: "the 24 months
                # ended" is a two-year statement, and claiming a
                # ttm_months from it misdescribes the figure this field
                # exists to qualify.
                if 1 <= months <= 12:
                    found.add(months)

    return found.pop() if len(found) == 1 else None


#: ---- TTM NOI ----
#:
#: A CIM prints its NOI many times over, and the copies mean different
#: things: a trailing actual, the same figure with the broker's expense
#: adjustments, year one of a pro forma, year five, a sale comp's, and a
#: stabilized target. They sit in the same documents in the same typeface,
#: so the number is not the hard part — the COLUMN is. The block this
#: replaces took the first regex hit document-wide and scored 18 correct /
#: 6 wrong / 3 hallucinated over 30 labeled decks, and none of the six were
#: digit errors: Decatur booked a year-three pro forma ($341,022) over an
#: actual trailing LOSS of $20,974, Norman a year-three ($1,570,862) over
#: $774,964, Dallas the broker-adjusted column ($193,603) over the T-12
#: actual ($117,779). Two were not NOI at all — `NOI[:\s]*` let `\s` cross a
#: NEWLINE, so a bare `SALE PRICE NOI` header adopted the next line's list
#: price ($5,600,000 on Hastings) and a `Current NOI Year 2 NOI` header row
#: adopted a unit count (362 on Little Rock).
#:
#: TTM NOI is one of the three `require_underwritable` inputs (decision 9)
#: and it seeds the analyst-adjusted NOI, the entry cap and the 10% IRR
#: gate. A pro forma in that slot does not merely mis-state one line: it
#: inflates the whole screen, and the CIM-Year-1 ≤ 115%-of-TTM flag that
#: exists to catch broker optimism reads the same wrong number, so the
#: check agrees with the thing it was meant to refuse.
#:
#: Same regime as occupancy and the sizes: per-line ranked candidates, the
#: best rank must agree on ONE value, refuse over guess. The ladder:
#:   1  a statement column the header NAMES as trailing — the only tier
#:      that reads a multi-column row, because it is the only one that
#:      knows which column it is reading
#:   2  the same, where the named trailing column is the broker's ADJUSTED
#:      one. Demoted, not dropped: Kerrville states `ADJ. T - 12` and no
#:      unadjusted trailing column anywhere, and the golden label takes it
#:   3  a single figure whose label is qualified as trailing — `NOI
#:      (Current)`, `TTM NOI`, `IN-PLACE NOI`, `NOI (CURRENT T-6)`
#:   4  a single figure qualified by a CALENDAR YEAR — `2025 NOI`. Below
#:      the explicit tier because Coors states both `NOI (Trailing 12 MO)
#:      $471,530` and `NOI (2025) $523,895`, and the year there is the
#:      forward one
#: The statement tiers outrank the label tiers because where a deck states
#: both and they DISAGREE, the label is the one that is wrong: Dallas
#: headlines its broker-adjusted figure as `Current NOI`, 2026 Abilene's
#: exec summary states `NOI (Current) $212,361` against its own statement's
#: $241,491 (the golden label calls it an internal conflict and takes the
#: statement), and Hastings headlines `IN-PLACE NOI $370,770` against a
#: statement current column of $200,770.
#:
#: An unqualified `NOI: $X` yields NO candidate at any tier. That is the
#: rule that refuses the pro-forma-only decks — a development site quoting
#: `NOI: $1,684,438` off a stabilized assumption, a deck whose only figure
#: is a run-rate — and it is the reason to prefer this shape over patching
#: the old first-match: those decks state no trailing actual, so the honest
#: read is None, which `require_underwritable` turns into a refusal the
#: analyst answers by hand rather than a screen built on an invented past.

#: Below this a "figure" on a financial row is a rate, a ratio or a
#: footnote marker, not a dollar column: every per-SF figure in the corpus
#: is under $14 and every stated NOI is over $20,000. It is a bound on the
#: EXTRACTION, not an investment criterion, so it lives here beside
#: MIN_PLAUSIBLE_ASKING_PRICE rather than in config.py.
MIN_PLAUSIBLE_NOI_FIGURE = 1_000

_NOI_RANK_TRAILING_COLUMN = 1
_NOI_RANK_ADJUSTED_COLUMN = 2
_NOI_RANK_TRAILING_LABEL = 3
_NOI_RANK_YEAR_LABEL = 4

#: The NOI label itself. `\bNOI\b` and the spelled form; `Total NOI` and
#: `NET OPERATING INCOME` are the same row.
_NOI_LABEL_RE = re.compile(
    r"\b(?:net\s+operating\s+income|noi)\b", re.IGNORECASE)

#: A STATEMENT ROW starts with the label. The anchor is what separates
#: `Net Operating Income $132,994 $249,950 …` from Butler's two-column page
#: glue, `Effective Gross Income $250,449 Net Operating Income $398,917`,
#: where the NOI sits mid-line and belongs to a different block — the exact
#: line the old first-match rule read as this deck's trailing NOI.
_NOI_ROW_RE = re.compile(
    r"^\W*(?:total\s+)?(?:net\s+operating\s+income|noi)\b", re.IGNORECASE)

#: A money figure as it appears in a text-layer financial row: `$156,128`,
#: `$ 109,556`, a bare `251,859`, and the accounting negative `$(20,974)`
#: — Decatur's trailing NOI is a LOSS, and a pattern that cannot read a
#: parenthesized negative does not merely miss it, it walks on to the pro
#: forma standing beside it.
_NOI_FIGURE_RE = re.compile(
    r"(?P<open>\(\s*)?-?\s*\$?\s*(?P<num>\d[\d,]*(?:\.\d+)?)")

#: A per-SF column, which prints beside its dollar column in half the
#: corpus (`$ 109,556 $1.02SF`). The magnitude floor already refuses these;
#: the suffix is checked as well because it is the deck's own statement of
#: what the column is, and a floor is a measurement that could drift.
_NOI_PER_SF_RE = re.compile(r"^\s*(?:SF|PSF|/\s*SF)", re.IGNORECASE)

#: The same column named in a HEADER rather than suffixed to a value.
_NOI_PER_SF_HEADER_RE = re.compile(
    r"\$\s*/\s*sf\b|\bper\s*sf\b|\bpsf\b|\$\s*/\s*sq", re.IGNORECASE)

#: Period vocabulary, read off the corpus headers. TRAILING is a period
#: that has already happened; PROJECTION is one that has not.
#: `\bt` and not a bare `t`: without the left boundary the T-N form fires
#: INSIDE words, and it did — `paired with an adjacent 10-acre` ends a word
#: in `t` before a number, which made a sentence of marketing prose read as
#: a period header on a deck that states no financials at all.
_NOI_TRAILING_TOK = (
    r"\bt\s*-?\s*\d{1,2}\b|\bttm\b|\btrailing(?:\s+twelve)?\b|\bcurrent\b"
    r"|\bin[\s-]?place\b|\bactual\b")
_NOI_PROJECTION_TOK = (
    r"\bpro\s*-?\s*forma\b|\bproforma\b|\bprojected\b|\bstabilized\b"
    r"|\bmarket\s+adjust(?:ed|ments?)\b"
    r"|\b(?:end[\s-]*)?(?:year|yr\.?)\s*-?\s*"
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|\d{1,2})\b")
#: A bare calendar year names a column of history — LaGrange heads its
#: trailing column `2025`, beside `MARKET ADJUSTED` and `PRO FORMA`. It is
#: read as trailing, which is also what refuses Belton: a header of
#: `2024 2025 2026` names three of them, and three trailing columns is an
#: ambiguity, not an answer.
_NOI_YEAR_TOK = r"\b20\d\d\b"

_NOI_TRAILING_RE = re.compile(_NOI_TRAILING_TOK, re.IGNORECASE)
_NOI_PROJECTION_RE = re.compile(_NOI_PROJECTION_TOK, re.IGNORECASE)
_NOI_YEAR_RE = re.compile(_NOI_YEAR_TOK, re.IGNORECASE)
_NOI_PERIOD_RE = re.compile(
    f"(?P<proj>{_NOI_PROJECTION_TOK})|(?P<trail>{_NOI_TRAILING_TOK})"
    f"|(?P<year>{_NOI_YEAR_TOK})", re.IGNORECASE)

#: The broker's own adjustment marker. Never a column of its own — it
#: qualifies the trailing column it sits on (`T-12 Broker Adjusted`,
#: `T-3 (ADJ)`, `ADJ. T - 12`).
_NOI_ADJUSTED_RE = re.compile(r"\badj\b|\badj\.|\badjusted\b|\bbroker\b",
                              re.IGNORECASE)

#: A percent VALUE, which is how an assumptions row is told from a header
#: row. Kerrville prints its rent-growth assumptions (`Year 1 17%`,
#: `Year 2 4%`) up the same page as its one-column statement, and read as
#: headers they bury the `ADJ. T - 12` column under five phantom years.
#: `% EGI` and `$ / SQ FT` in the MNET headers carry no digit and so are
#: not percent values — which is the distinction that keeps those headers
#: readable.
_NOI_PERCENT_VALUE_RE = re.compile(r"\d\s*%")

#: How far before the NOI word a qualifier may sit, in characters. Sized
#: to `SELLER ANNUALIZED T1 THRU MAY 31, 2026 NOI` — the widest genuine
#: one in the corpus — and no wider: these pages glue two columns of text
#: into one line, so a window that reaches further starts reading the
#: neighbouring block's words as this label's basis.
_NOI_LABEL_WINDOW = 44

#: What may sit between the label and its figure: a basis parenthetical, a
#: colon, `of`/`is`, currency. It may not cross a newline — that crossing
#: is what booked a list price and a unit count as NOI.
_NOI_LABEL_GAP_RE = re.compile(
    r"^(?:\s*\((?P<paren>[^)\n]*)\))?[\s:]*(?:of|is|was|at)?[\s:~]*",
    re.IGNORECASE)


def _noi_figures(text: str) -> list:
    """Dollar figures in one financial row, in column order.

    Per-SF columns and percentages are dropped, so what comes back is one
    value per MONEY column — the list the header's period sequence is
    matched against by count.
    """
    figures = []
    for m in _NOI_FIGURE_RE.finditer(text):
        rest = text[m.end():]
        if rest[:1] == "%" or _NOI_PER_SF_RE.match(rest):
            continue
        value = _parse_number(m.group("num"))
        if abs(value) < MIN_PLAUSIBLE_NOI_FIGURE:
            continue
        # Both accounting negatives appear in the corpus and they bracket
        # the dollar sign differently: `$(44,960)` puts the sign inside and
        # `-$44,960` outside, so a test that reads only the character
        # before the digits sees a `$` in the second form and books a
        # trailing LOSS as a profit — on the one deck whose golden trailing
        # NOI is negative, which is exactly the deck a screen must not get
        # backwards.
        lead = m.group(0)[:m.start("num") - m.start()]
        before = text[:m.start()].rstrip()
        if ("(" in lead or "-" in lead or m.group("open")
                or before.endswith(("-", "("))):
            value = -value
        figures.append(value)
    return figures


def _noi_header_columns(header: str) -> list:
    """The period columns a header declares, in order, as
    `(kind, adjusted)` pairs — `kind` being 'trailing' or 'projection'.

    Column boundaries are the whole difficulty, because a text layer keeps
    no delimiters: `T-12 Actual T-12 Broker Adjusted Pro Forma (Year 3)`
    is three columns of two, three and three words. So a period token
    STARTS a column unless it is a continuation of the one before it, and
    the two continuation rules are each witnessed:

    - A trailing token straight after another trailing token is one label,
      not two: Starkville heads its column `CURRENT T-6`. It stays its own
      column when an adjustment marker follows, which is Hammond's
      `T-3 T-3 (ADJ)` and Dallas's `T-12 Actual T-12 Broker Adjusted` —
      two real columns each, differing only by the broker's hand.
    - A YEAR inside parentheses is a date stamp on the column beside it,
      not a column: `T5 (JAN-MAY 2026)`, `Pro Forma (Year 3)`. Katy's
      `T3 (JAN - MAR` / `2026)` puts the two halves on different text
      lines, which is why the closing parenthesis counts as much as the
      opening one.
    """
    columns = []
    last_year = False
    tokens = list(_NOI_PERIOD_RE.finditer(header))
    for i, m in enumerate(tokens):
        kind = ("projection" if m.group("proj")
                else "trailing" if m.group("trail") else "year")
        before, after = header[:m.start()], header[m.end():]
        if before.rstrip().endswith("(") or after.lstrip().startswith(")"):
            continue                          # a date stamp, not a column
        if kind == "year":
            kind = "trailing"
        gap_next = after[:tokens[i + 1].start() - m.end()] if (
            i + 1 < len(tokens)) else after
        adjusted = bool(_NOI_ADJUSTED_RE.search(m.group(0))
                        or _NOI_ADJUSTED_RE.search(gap_next)
                        or _NOI_ADJUSTED_RE.search(before[-12:]))
        # `CURRENT T-6` is one column's label, so a trailing token straight
        # after another does not open a column — unless an adjustment
        # marker makes it the broker's separate copy (`T-3 T-3 (ADJ)`), or
        # unless either side is a bare YEAR, because a run of years is
        # always a run of columns (`2024 2025 2026`).
        if (kind == "trailing" and columns and columns[-1] == ("trailing",
                                                               False)
                and not m.group("year") and not last_year
                and not _NOI_TRAILING_RE.search(
                    header[tokens[i - 1].end():m.start()])
                and not adjusted):
            continue
        columns.append((kind, adjusted))
        last_year = bool(m.group("year"))
    return columns


def _noi_header_above(lines: list, row: int, figures: int) -> Optional[str]:
    """The period header governing the statement row at `lines[row]`.

    A header NAMES periods and does not price them — the rule
    `extract.tables.find_header` learned from Wichita's footnote row — so
    a line carrying a dollar figure or a percent VALUE cannot be one.

    Candidates are tried NEAREST FIRST and accepted only when the columns
    they declare match the row's figure count, then widened upward one
    line at a time. Both halves earn their place: the nearest-first order
    is what stops a page's INCOME header from being read on top of its
    identical EXPENSES header (2026 Abilene declares the same three
    columns twice), and the widening is what assembles Katy's header out
    of the three separate text lines pdfplumber emits it as.
    """
    above = [i for i in range(row) if _noi_header_line(lines[i])]
    for start in range(len(above) - 1, -1, -1):
        header = " ".join(lines[i] for i in above[start:])
        columns = _noi_header_columns(header)
        if len(columns) == figures:
            return header
    return None


def _noi_header_line(line: str) -> bool:
    """Does this line NAME periods rather than price them?

    The year a column is named for is not a figure that column carries:
    LaGrange heads its trailing column `2025` and Rowlett heads its
    `T5 (JAN-MAY 2026)`, and counting those four digits as money rejects
    both headers — which is to say it rejects the two decks whose header
    states the basis most plainly.
    """
    return (bool(_NOI_PERIOD_RE.search(line))
            and not _noi_figures(_NOI_YEAR_RE.sub(" ", line))
            and not _NOI_PERCENT_VALUE_RE.search(line))


def _noi_statement_candidate(lines: list, row: int):
    """`(rank, value)` for one statement row, or None.

    Refusal has three separate causes here and all three are the same
    answer: no header could be matched to the row's shape, the header
    names no trailing column at all (Belton's `2024 2025 2026` — three
    end-of-year pro formas), or it names more than one and nothing chooses
    between them (Decatur's `SELLER ANNUALIZED T7 SELLER ANNUALIZED T1`).
    A statement that cannot say which column is the actual has not stated
    an actual.
    """
    figures = _noi_figures(lines[row])
    if not figures:
        return None
    header = _noi_header_above(lines, row, len(figures))
    if header is None:
        return None
    # A ONE-column row carries no ordinal information of its own, so the
    # count match that vouches for every other row proves nothing here and
    # a single stray period word anywhere above would carry it. Kerrville
    # is the corpus's only genuine one-column statement and its header
    # reads `REVENUE ADJ. T - 12 $/SF`: the per-SF column is the deck's own
    # evidence that this is a financial statement rather than a paragraph
    # — the same signal `extract.tables` reads as `KIND_PER_SF`. Without
    # it, `With all entitlements in place,` was a header and a development
    # site with no operations reported a trailing NOI of $1,684,438.
    if len(figures) == 1 and not _NOI_PER_SF_HEADER_RE.search(header):
        return None
    columns = _noi_header_columns(header)
    for rank, adjusted in ((_NOI_RANK_TRAILING_COLUMN, False),
                           (_NOI_RANK_ADJUSTED_COLUMN, True)):
        at = [i for i, (kind, adj) in enumerate(columns)
              if kind == "trailing" and adj == adjusted]
        if len(at) == 1:
            return rank, figures[at[0]]
        if at:
            return None
    return None


def _noi_label_candidate(line: str, m):
    """`(rank, value)` for a single figure whose label carries its basis.

    The qualifier may precede the label (`Current NOI`, `IN-PLACE NOI`,
    `2025 NOI`) or sit in the parenthetical after it (`NOI (Current)`,
    `NOI (Trailing 12 MO)`); both forms are one deck's house style rather
    than two claims. A PROJECTION word in either position vetoes outright,
    and it is checked first: `PRO FORMA END OF YEAR 3 NOI` and `NOI (Year
    One)` both carry a period word this would otherwise read as a basis.
    """
    gap = _NOI_LABEL_GAP_RE.match(line[m.end():])
    tail = line[m.end() + gap.end():]
    figure = _noi_figures(tail[:40])
    if not figure:
        return None
    span = line[max(0, m.start() - _NOI_LABEL_WINDOW):m.start()]
    span += " " + (gap.group("paren") or "")
    if _NOI_PROJECTION_RE.search(span):
        return None
    if _NOI_TRAILING_RE.search(span):
        return _NOI_RANK_TRAILING_LABEL, figure[0]
    if _NOI_YEAR_RE.search(span):
        return _NOI_RANK_YEAR_LABEL, figure[0]
    return None


def _noi_candidates(text: str) -> list:
    """All plausible `(rank, value)` TTM NOI candidates, for
    `_pick_ranked`. Page-scoped, because a header governs the statement
    printed under it and nothing else: the same deck prints its trailing
    statement on one page and its five-year cash flow on the next, and the
    second one's first column is year one."""
    candidates = []
    for page in text.split("\n" + "=" * 60):
        lines = page.split("\n")
        for i, line in enumerate(lines):
            if _NOI_ROW_RE.match(line):
                found = _noi_statement_candidate(lines, i)
                if found:
                    candidates.append(found)
            for m in _NOI_LABEL_RE.finditer(line):
                found = _noi_label_candidate(line, m)
                if found:
                    candidates.append(found)
    return candidates


def _parse_financials(text: str, tables: list, data: CIMData):
    """Extract income and expense data from text and tables."""

    noi = _pick_ranked(_noi_candidates(text))
    if noi is not None:
        data.ttm_noi = noi

    data.ttm_months = _extract_ttm_months(text)

    # GPR / Gross Potential Rent
    gpr_pat = r"(?:gross\s+potential\s+(?:rent|revenue)|GPR)[:\s]*\$?\s*([\d,]+(?:\.\d+)?)"
    m = re.search(gpr_pat, text, re.IGNORECASE)
    if m:
        data.ttm_gpr = _parse_number(m.group(1))

    # EGR / Effective Gross Revenue
    egr_pat = r"(?:effective\s+gross\s+(?:revenue|income)|EGR|EGI)[:\s]*\$?\s*([\d,]+(?:\.\d+)?)"
    m = re.search(egr_pat, text, re.IGNORECASE)
    if m:
        data.ttm_egr = _parse_number(m.group(1))

    # Total revenue
    rev_pat = r"total\s+(?:revenue|income)[:\s]*\$?\s*([\d,]+(?:\.\d+)?)"
    m = re.search(rev_pat, text, re.IGNORECASE)
    if m:
        data.ttm_total_revenue = _parse_number(m.group(1))

    # Total expenses
    exp_pat = r"total\s+(?:operating\s+)?expenses?[:\s]*\$?\s*([\d,]+(?:\.\d+)?)"
    m = re.search(exp_pat, text, re.IGNORECASE)
    if m:
        data.ttm_total_expenses = _parse_number(m.group(1))

    # Other income
    oi_pat = r"other\s+income[:\s]*\$?\s*([\d,]+(?:\.\d+)?)"
    m = re.search(oi_pat, text, re.IGNORECASE)
    if m:
        data.other_income = _parse_number(m.group(1))

    # CIM Year 1 NOI
    yr1_patterns = [
        r"(?:year\s*1|yr\.?\s*1|pro\s*forma)\s*NOI[:\s]*\$?\s*([\d,]+(?:\.\d+)?)",
        r"NOI\s*[\-–]\s*(?:year\s*1|yr\.?\s*1|pro\s*forma)[:\s]*\$?\s*([\d,]+(?:\.\d+)?)",
    ]
    for pat in yr1_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            data.cim_yr1_noi = _parse_number(m.group(1))
            break

    # Management fee percentage
    mgmt_pat = r"(?:management|mgmt\.?)\s+fee[:\s]*([\d\.]+)\s*%"
    m = re.search(mgmt_pat, text, re.IGNORECASE)
    if m:
        data.mgmt_fee_pct = float(m.group(1)) / 100.0

    # Attempt to parse financial tables
    _parse_financial_tables(tables, data)


def _parse_financial_tables(tables: list, data: CIMData):
    """Try to extract income/expense line items from parsed tables."""
    income_keywords = [
        "gross potential", "rental income", "vacancy", "concession",
        "effective gross", "other income", "late fee", "admin fee",
        "merchandise", "total revenue", "total income",
    ]
    expense_keywords = [
        "property tax", "real estate tax", "insurance", "utility",
        "utilities", "repair", "maintenance", "r&m", "advertising",
        "marketing", "payroll", "salary", "wages", "general",
        "administrative", "g&a", "management fee", "mgmt fee",
        "total expense", "total operating", "net operating",
    ]

    # `enumerate` over ALL tables, not just the ones that yield lines: the
    # ordinal only has to be stable and unique per table, and counting the
    # skipped ones keeps it that way without a second counter to desync.
    for statement, table_info in enumerate(tables):
        table = table_info["data"]
        page = table_info.get("page")
        # ONE header read per table, not per row — a header is a property of
        # the table, and re-deriving it inside the row loop would let two rows
        # of the same table disagree about what their columns mean.
        roles = find_header(table)

        for row in table:
            if len(row) < 2:
                continue
            label = row[0].lower().strip() if row[0] else ""
            if not label:
                continue

            # Check if this looks like an income or expense line
            is_income = any(kw in label for kw in income_keywords)
            is_expense = any(kw in label for kw in expense_keywords)

            if is_income or is_expense:
                # A label that says "income" is not an expense, whatever
                # else it matched. `expense_keywords` carries a bare
                # "insurance" and `income_keywords` had no bare "income",
                # so `Insurance Income` and `Tenant Insurance Income (net)`
                # matched the EXPENSE list only, landed in expense_lines,
                # mapped to the `insurance` benchmark category and were
                # ADDED to the insurance expense: 52,674 of pure income
                # booked as expense on the Wichita CIM, 16,638 on Dallas.
                #
                # The same rule catches `Net Operating Income`, `Total
                # Operating Income` and `NET OPERATING INCOME GROWTH`,
                # which reached expense_lines through "net operating" /
                # "total operating". Those map to no benchmark category and
                # so moved no money, but they were never expenses either,
                # and fixing insurance alone would leave the next reader to
                # rediscover why a NOI line is filed under costs.
                #
                # Routed to income_lines rather than dropped: nothing
                # downstream PRICES income_lines (only
                # `scripts/extraction_report.py` and the JSON round-trip
                # read it), so this cannot move a number, and keeping the
                # line preserves the extraction report's count.
                #
                # INSIDE the gate, never as part of it. Applied before it,
                # a bare `"income" in label` promotes the section HEADER
                # row — the lone `INCOME` cell above the line items — into
                # a financial line, which then fails period assignment and
                # lands in the refusal log, reporting a parse failure on a
                # table that parsed perfectly. The gate is what separates
                # "a row naming an income or expense line" from "a row
                # saying the word".
                if "income" in label:
                    is_income, is_expense = True, False

                line = FinancialLine(label=row[0].strip(),
                                     page=page, statement=statement)
                periods = assign_periods(roles, row) if roles else None

                if periods is None:
                    # REFUSED, not filled: the columns could not be named, so
                    # no period gets a number. `analysis.fills` draws exactly
                    # this line — "the value was declined, not substituted,
                    # and that is a run warning" — and the alternative is the
                    # inversion this module exists to end.
                    data.unmapped_financial_lines.append({
                        "label": line.label, "page": page,
                        "reason": ("no period header on this table"
                                   if not roles else
                                   "columns do not line up with the header"),
                    })
                else:
                    line.t12 = periods.get(ROLE_CURRENT)
                    line.cim_yr1 = periods.get(f"{ROLE_YEAR_PREFIX}1")
                    line.t3 = periods.get(ROLE_T3)

                if is_income:
                    data.income_lines.append(line)
                else:
                    data.expense_lines.append(line)


def _parse_supply(text: str, data: CIMData):
    """Extract mentions of new supply / construction pipeline."""
    supply_keywords = [
        "new supply", "under construction", "pipeline", "proposed",
        "planned", "entitled", "new development", "new facilit",
        "new storage", "competitor", "new construction",
    ]
    mentions = []
    sentences = re.split(r'[.!?]+', text)
    for sent in sentences:
        if any(kw in sent.lower() for kw in supply_keywords):
            clean = sent.strip()
            if len(clean) > 20:
                mentions.append(clean[:300])

    if mentions:
        data.new_supply_mentions = " | ".join(mentions[:5])


def _compute_derived(data: CIMData):
    """Compute derived fields from parsed data."""
    if data.asking_price and data.nrsf:
        data.price_per_sf = round(data.asking_price / data.nrsf, 2)

    if data.cc_pct and data.nrsf:
        data.cc_sf = round(data.nrsf * data.cc_pct)
        data.non_cc_sf = round(data.nrsf * (1.0 - data.cc_pct))

    # `ttm_noi = ttm_total_revenue - ttm_total_expenses` used to sit here,
    # and it had to go with the first-match NOI patterns rather than after
    # them: it fires exactly when the ranked candidates REFUSED, so it is a
    # route around the refusal, and the two operands it subtracts are still
    # read by unqualified document-wide first-match regexes — the very
    # shape this change removed from NOI. On a pro-forma-only deck those
    # two find a projection's revenue and a projection's expenses and their
    # difference is a projection's NOI, arriving in the slot
    # `require_underwritable` guards (decision 9) wearing no mark of where
    # it came from. It fires on none of the 45 labeled decks, so deleting
    # it moved no measured number; the point is that it could only ever
    # have fired on a deck the column discipline had just declined to read.


# ── Utility Helpers ─────────────────────────────────────────────────

def _parse_number(s: str) -> float:
    """Parse a numeric string, removing commas and whitespace."""
    s = s.replace(",", "").replace(" ", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


# `_parse_currency` lived here and had exactly one caller, the positional
# guess in `_parse_financial_tables`. It is DELETED rather than left for
# reuse (scoped-backlog rule 2, deleted not re-defaulted): the next caller
# would rebuild the compacted `values` list it was written to feed, which is
# the shape that made a header map impossible. `extract.tables.parse_cell`
# replaces it and is strictly better on the corpus — it reads `$ (222,391)`,
# which this returned None for because the `$` sits outside the parenthesis.
