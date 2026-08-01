"""City / state / ZIP extraction from CIM text.

Split out of parser.py because two callers need it and neither should have to
import the other: extract.parser fills CIMData.city/state for the market and
population gates, and scripts/cims_rename_plan.py files CIMs by location. One
copy, so a fix to either reaches both — the previous arrangement had a second
regex in the rename script that had already drifted ahead of the parser's.

Three things make this harder than a single regex, all seen in real CIM covers:

  * The disclaimer pages carry the BROKER's address, not the property's. A
    whole-document search finds Encino CA before it finds the subject. Hence
    cover-page-first ordering, with proximity suppression as the backstop.
  * The capture runs backwards into the street line, so a naive match yields
    "East Pikes Peak Avenue Colorado Springs" or "State Belton". Hence
    tidy_city, which walks tokens from the right and stops at the first one
    that cannot be part of a city name.
  * Covers are typeset, not written: ALL-CAPS cities ("MAXWELL, TX 78666"),
    no space before the ZIP ("Gordonville,TX76245"), and letters split by the
    text layer ("DECATU R"). Each has a case in tests/test_parser_location.py.
"""

import re
import unicodedata
from typing import NamedTuple

__all__ = ["Location", "STATES", "ST_CODES", "BROKERS", "norm_text",
           "near_broker", "tidy_city", "find_locations", "locate"]


class Location(NamedTuple):
    city: str
    state: str
    zip_code: str


STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
ST_CODES = set(STATES.values())

BROKERS = [
    "marcus & millichap", "marcus and millichap", "cbre", "colliers",
    "cushman & wakefield", "cushman and wakefield", "jll", "jones lang",
    "newmark", "svn", "berkadia", "sharplaunch", "ten-x", "crexi",
    "the storage acquisition group", "argus self storage", "skyview advisors",
    "bellomy & associates", "matthews real estate", "walker & dunlop",
]

# Letters joined by \s* because the PDF text layer splits spelled-out state names
# mid-word: "5216 Electric Avenue Spicewood, Te xas 78669" is verbatim cover text
# from green-storage-plus-austin-tx-om.pdf. A plain "|".join(STATES) misses it.
_ST_ALT = "|".join(r"\s*".join(name) for name in STATES)
# \s* and not \s+: real covers set the address with no space at all
# ("63CedarMillsRd,Gordonville,TX76245" — Texoma 377 OM.pdf).
_ZIP = r"[\.,]?\s*(\d{5})(?:-\d{4})?"

# "…, Springfield, MO 65801" / "… | YORK, SC 29745" / "Rogers, Arkansas 72756".
# The leading boundary is load-bearing: without it the city group runs backwards
# into the street line and yields "BeachRdLincolnville" instead of "Lincolnville".
ADDR_RE = re.compile(
    r"(?:^|[,|•·\n])\s*([A-Za-z][A-Za-z\.\-' ]{2,28}?),\s*"
    r"([A-Z]{2}|" + _ST_ALT + r")" + _ZIP, re.IGNORECASE)
# Fallback for covers with no comma before the city; the street-suffix filter
# does the work instead.
ADDR_RE_LOOSE = re.compile(
    r"([A-Za-z][A-Za-z\.\-' ]{2,28}?),\s*([A-Z]{2}|" + _ST_ALT + r")" + _ZIP,
    re.IGNORECASE)
# "… Waterloo IA 50701" — no comma at all. Common in folder and file names.
ADDR_RE_NOCOMMA = re.compile(
    r"([A-Za-z][A-Za-z\.\-' ]{2,28}?)\s+([A-Z]{2})\s+(\d{5})\b")
# "For Sale - Creedmoor, TX" — plenty of covers name the city with no ZIP at all.
# Deliberately NOT case-insensitive, and the city must be capitalised. With no ZIP
# to anchor it and `[A-Z]{2}` accepting any two letters, a case-blind version reads
# ordinary prose as an address: "Rent growth has been strong, in a healthy
# secondary market" yielded Location('Has Been Strong', 'IN'). Requiring real
# capitalisation is what separates "Creedmoor, TX" from "strong, in a".
# Opt-in only (allow_zipless) and cover-page only — see locate().
ADDR_RE_NOZIP = re.compile(
    r"(?:^|[,|•·\n-])\s*([A-Z][A-Za-z\.\-' ]{2,28}?),\s*"
    r"([A-Z]{2}|" + "|".join(n.title() for n in STATES) + r")(?![A-Za-z])")

# Tokens that are never part of a city name, used to trim a capture that has run
# backwards into the street line or a field label ("State Belton" -> "Belton").
NOISE_TOKENS = {
    "zip", "address", "location", "city", "property", "site", "state",
    "the", "at", "of", "and", "in", "on", "is", "to", "rsf", "psf", "nrsf",
    "sf", "sqft", "acres", "acre", "units", "unit", "price", "cap", "rate",
    "offering", "memorandum", "subject", "located", "situated",
    # Cover-page marketing wording. Without these, "For Sale - Creedmoor, TX"
    # trims to "Sale - Creedmoor" rather than "Creedmoor".
    "sale", "for", "lease", "presented", "exclusively", "confidential",
}

STREET_SUFFIX = re.compile(
    r"\b(road|rd|street|st|avenue|ave|boulevard|blvd|drive|dr|highway|hwy|hway|lane|ln|"
    r"way|court|ct|circle|cir|parkway|pkwy|place|pl|trail|trl|route|rte|loop|pike|"
    r"terrace|ter|suite|ste|floor|fl|unit|building|bldg|north|south|east|west|"
    r"n|s|e|w|ne|nw|se|sw|us|sr|fm|county|co)$", re.IGNORECASE)


def norm_text(s: str) -> str:
    """NFKD-normalise, strip problem codepoints, collapse whitespace."""
    s = unicodedata.normalize("NFKD", s or "")
    for bad, good in (("–", "-"), ("—", "-"), ("‘", "'"),
                      ("’", "'"), ("“", '"'), ("”", '"'),
                      (" ", " "), ("­", "")):
        s = s.replace(bad, good)
    s = "".join(c for c in s if ord(c) < 128 or c.isalnum())
    return re.sub(r"\s+", " ", s).strip()


def near_broker(text: str, pos: int, window: int = 110) -> bool:
    """Is this address sitting inside a broker's own signature block?

    The window is deliberately tight. Widen it and, applied to a whole-document
    blob, it matches the disclaimer page's dozens of broker mentions and
    suppresses every real address in the file."""
    seg = text[max(0, pos - window): pos + window].lower()
    return any(b in seg for b in BROKERS)


def tidy_city(c: str) -> str:
    """Trim a captured city back to just the city.

    Walk the tokens from the right and stop at the first one that cannot be part
    of a city name: a street suffix, a noise word, or anything holding a digit or
    a period. 'Industry Drive Bastrop' -> 'Bastrop'; 'State Belton' -> 'Belton'."""
    c = (c or "").strip(" .,-|")
    c = re.sub(r"([A-Za-z])\s+([A-Z])(?=\s|$)", r"\1\2", c)   # 'DECATU R' -> 'DECATUR'
    keep = []
    for t in reversed(c.split()):
        tl = t.strip(".,").lower()
        # A token with no letters at all is a separator the capture swallowed
        # ("For Sale - Creedmoor"), never part of the name.
        if (STREET_SUFFIX.match(tl) or tl in NOISE_TOKENS
                or not any(ch.isalpha() for ch in t)
                or any(ch.isdigit() for ch in t) or "." in t):
            break
        keep.append(t)
        if len(keep) == 3:          # no US city name needs more than three words here
            break
    c = " ".join(reversed(keep)) if keep else ""

    # 'YORK' -> 'York', but leave genuine CamelCase alone so 'McKinney' and
    # 'LaGrange' survive intact.
    def fix(w):
        return w if re.fullmatch(r"(?:[A-Z][a-z]+){1,3}", w) else w.title()
    return " ".join(fix(w) for w in c.split())


# STATES keys hold one space between words, and the split-state pattern captures
# whatever the text layer produced — "Te xas", "New  York", "N e w Y o r k".
# Squeezing ALL whitespace out gives "newyork", which is not a key, and silently
# drops every two-word state; collapsing runs to a single space is what works.
_STATES_SQUEEZED = {re.sub(r"\s+", "", k): v for k, v in STATES.items()}


def _state_code(raw: str) -> str:
    """-> two-letter code, or '' if the text is not a state."""
    collapsed = re.sub(r"\s+", " ", (raw or "").strip()).lower()
    if len(collapsed) == 2:
        return collapsed.upper()
    return (STATES.get(collapsed)
            or _STATES_SQUEEZED.get(re.sub(r"\s+", "", collapsed), ""))


def _harvest(rx, text: str) -> list:
    out = []
    for m in rx.finditer(text):
        if near_broker(text, m.start()):
            continue
        city = tidy_city(m.group(1))
        st = _state_code(m.group(2))
        if st not in ST_CODES or len(city) < 3:
            continue
        # A 'city' ending in a street suffix is really the tail of the street line.
        last = city.split()[-1] if city.split() else city
        if STREET_SUFFIX.match(last) or re.match(r"^\d", city):
            continue
        out.append(Location(city, st, m.group(3) if rx.groups >= 3 else ""))
    return out


def find_locations(text: str) -> list:
    """All (city, state, zip) hits not sitting next to a broker's own address."""
    for rx in (ADDR_RE, ADDR_RE_LOOSE, ADDR_RE_NOCOMMA):
        out = _harvest(rx, text or "")
        if out:
            return out
    return []


def locate(pages, allow_zipless: bool = False) -> tuple:
    """-> (locations, source). Cover page first, body only as a fallback.

    The cover carries the subject property's address; the disclaimer pages carry
    the broker's. Reading the cover first avoids the confusion outright instead
    of relying on proximity suppression to untangle it afterwards.

    `pages` is the per-page text list from pdf_reader.extract_pdf(); a plain
    string is accepted and treated as a single blob.

    allow_zipless opts into the ADDR_RE_NOZIP last resort. It is off by default
    because an unanchored match is a plausible city, not a proven one, and the
    analysis pipeline feeds city/state into the population gate. Filing tools
    that surface the result for a human to confirm can turn it on."""
    if isinstance(pages, str):
        pages = [pages]
    pages = [p for p in (pages or []) if p]
    if not pages:
        return [], "no text"
    cover_txt = norm_text(pages[0])
    cover = find_locations(cover_txt)
    if cover:
        return cover, "cover page"
    body = norm_text(" ".join(pages[1:])) if len(pages) > 1 else ""
    hits = find_locations(body)
    if hits:
        return hits, "body text"
    if not allow_zipless:
        return [], "not found"
    return _harvest(ADDR_RE_NOZIP, cover_txt), "cover page (no ZIP)"


def best_city_state(pages, allow_zipless: bool = False) -> tuple:
    """-> (city, state, source) using the most frequent city, or (None, None, src).

    Most-frequent rather than first because a cover often repeats the subject
    address in the header and once more in the highlights, while a stray match
    appears once. The state is resolved the same way, among the hits for the
    winning city only: taking the first-seen state instead hands a document that
    lists 'Springfield, IL' as a comp above two mentions of the subject
    'Springfield, MO' the comp's state."""
    from collections import Counter

    locs, src = locate(pages, allow_zipless=allow_zipless)
    if not locs:
        return None, None, src
    city = Counter(loc.city for loc in locs).most_common(1)[0][0]
    state = Counter(loc.state for loc in locs
                    if loc.city == city).most_common(1)[0][0]
    return city, state, src
