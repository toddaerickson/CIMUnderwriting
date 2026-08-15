"""
Structured data extraction from raw CIM text.

CIMs have no standard format. This parser uses regex and keyword matching
to locate key data points. Missing fields are set to None and flagged
for manual review by Claude Code.
"""

import re
from dataclasses import dataclass, field, fields
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
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
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
            if f.name == "unmapped_financial_lines":
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


def parse_cim(raw: dict) -> CIMData:
    """
    Parse raw PDF extraction into structured CIMData.

    Args:
        raw: dict from pdf_reader.extract_pdf() with keys
             "text", "tables", "page_count", "pages"

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

    # Address
    addr_pat = r"(\d{1,6}\s+[A-Za-z0-9\s\.\,]+(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Boulevard|Blvd|Lane|Ln|Way|Highway|Hwy|Parkway|Pkwy)[\.?\s,]*)"
    m = re.search(addr_pat, text)
    if m:
        data.address = m.group(1).strip().rstrip(",")

    # City, State — see extract/location.py. The whole-document regex this
    # replaced returned the broker's disclaimer-page address as often as the
    # property's, could not match an ALL-CAPS cover ("MAXWELL, TX"), and let the
    # street line run into the city ("East Pikes Peak Avenue Colorado Springs").
    city, state, _src = best_city_state(pages if pages else text)
    if city:
        data.city, data.state = city, state

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


def _parse_size_occupancy(text: str, data: CIMData):
    """Extract NRSF, unit counts, CC split, occupancy."""

    # NRSF / Net Rentable SF
    nrsf_patterns = [
        r"(?:net\s+rentable|NRSF|rentable)\s*(?:square\s*(?:feet|footage)|SF|sq\.?\s*ft\.?)[:\s]*[~≈]*([\d,]+)",
        r"([\d,]+)\s*(?:net\s+rentable|NRSF)\s*(?:square\s*(?:feet|footage)|SF)",
    ]
    for pat in nrsf_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            data.nrsf = _parse_number(m.group(1))
            break

    # Total units
    unit_patterns = [
        r"([\d,]+)\s*(?:total\s+)?(?:storage\s+)?units",
        r"(?:units|unit\s+count)[:\s]*([\d,]+)",
    ]
    for pat in unit_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            data.total_units = int(_parse_number(m.group(1)))
            break

    # Climate-controlled percentage
    cc_pat = r"([\d\.]+)\s*%\s*(?:climate[\s\-]?controlled|CC)"
    m = re.search(cc_pat, text, re.IGNORECASE)
    if m:
        data.cc_pct = float(m.group(1)) / 100.0

    # Occupancy
    occ_patterns = [
        r"(?:physical\s+)?occupancy[:\s]*([\d\.]+)\s*%",
        r"([\d\.]+)\s*%\s*(?:occupied|occupancy)",
    ]
    for pat in occ_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            data.physical_occupancy = float(m.group(1)) / 100.0
            break

    # Economic occupancy
    econ_pat = r"economic\s+occupancy[:\s]*([\d\.]+)\s*%"
    m = re.search(econ_pat, text, re.IGNORECASE)
    if m:
        data.economic_occupancy = float(m.group(1)) / 100.0


def _parse_pricing(text: str, data: CIMData):
    """Extract asking price."""
    price_patterns = [
        r"(?:asking\s+price|list\s+price|offered?\s+(?:at|price)|purchase\s+price)[:\s]*\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|MM|M)",
        r"(?:asking\s+price|list\s+price|offered?\s+(?:at|price)|purchase\s+price)[:\s]*\$\s*([\d,]+(?:\.\d+)?)",
    ]
    for pat in price_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = _parse_number(m.group(1))
            # Check if in millions
            if "million" in text[m.start():m.end()+20].lower() or \
               "MM" in text[m.start():m.end()+10] or \
               (val < 1000 and "M" in text[m.end():m.end()+5]):
                val *= 1_000_000
            data.asking_price = val
            break


def _parse_demographics(text: str, data: CIMData):
    """Extract population by radius and median HHI."""

    # Population within radii
    pop_patterns = {
        "population_1mi": [
            r"1[\s\-]?mile[^:]*?[:\s]*([\d,]+)\s*(?:people|pop|residents)?",
            r"([\d,]+)\s*(?:people|pop|residents)?\s*(?:within|in)\s*(?:a\s*)?1[\s\-]?mile",
        ],
        "population_3mi": [
            r"3[\s\-]?mile[^:]*?[:\s]*([\d,]+)\s*(?:people|pop|residents)?",
            r"([\d,]+)\s*(?:people|pop|residents)?\s*(?:within|in)\s*(?:a\s*)?3[\s\-]?mile",
        ],
        "population_5mi": [
            r"5[\s\-]?mile[^:]*?[:\s]*([\d,]+)\s*(?:people|pop|residents)?",
            r"([\d,]+)\s*(?:people|pop|residents)?\s*(?:within|in)\s*(?:a\s*)?5[\s\-]?mile",
        ],
    }
    for field_name, pats in pop_patterns.items():
        for pat in pats:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                setattr(data, field_name, int(_parse_number(m.group(1))))
                break

    # Median HHI
    hhi_pat = r"(?:median\s+)?(?:household\s+income|HHI)[:\s]*\$\s*([\d,]+)"
    m = re.search(hhi_pat, text, re.IGNORECASE)
    if m:
        data.median_hhi_3mi = _parse_number(m.group(1))


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


def _parse_financials(text: str, tables: list, data: CIMData):
    """Extract income and expense data from text and tables."""

    # Try to find NOI
    noi_patterns = [
        r"(?:TTM|T-?12|trailing\s+twelve)\s*(?:month)?\s*NOI[:\s]*\$?\s*([\d,]+(?:\.\d+)?)",
        r"NOI[:\s]*\$?\s*([\d,]+(?:\.\d+)?)",
        r"net\s+operating\s+income[:\s]*\$?\s*([\d,]+(?:\.\d+)?)",
    ]
    for pat in noi_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            data.ttm_noi = _parse_number(m.group(1))
            break

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

    # If we have total revenue and expenses but no NOI, compute it
    if data.ttm_noi is None and data.ttm_total_revenue and data.ttm_total_expenses:
        data.ttm_noi = data.ttm_total_revenue - data.ttm_total_expenses


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
