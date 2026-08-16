"""Multi-property / portfolio CIM detection.

One definition shared by the analysis pipeline (extract/parser.py) and the
filing script (scripts/cims_rename_plan.py) -- the same arrangement
extract/location.py uses so a fix reaches both instead of drifting apart.

Why this exists: the analysis pipeline is single-asset by design (one
CIMData, one Deal, one DCF). A portfolio CIM -- two properties sold as one
offering -- would otherwise be SILENTLY collapsed into one wrong deal:
every parser field takes the first regex match or the most-frequent city,
so portfolio-level asking price can get divided by one property's NRSF and
poison every downstream gate. This module exists to make that case LOUD.

It is deliberately conservative. Every signal is presented as evidence for
a human to confirm, never a hard block: a single-asset CIM's body routinely
mentions comps in other cities and states, and the cover can repeat the
subject address in a header and again in a highlights box.
"""

import re
from dataclasses import dataclass, field

from extract.location import find_locations, near_broker


# ── Portfolio wording ──────────────────────────────────────────────

# Moved verbatim from scripts/cims_rename_plan.py so both consumers read
# one copy. Catches "Two Property Portfolio", "portfolio of 3 assets",
# "multi-site", etc.
PORTFOLIO_RE = re.compile(
    r"\b(two|three|four|five|six|\d{1,2})[\s-]*(propert(y|ies)|facilit(y|ies)|site|store|"
    r"asset)s?[\s-]*(portfolio|offering)?\b(?=.{0,40}(portfolio|offering|package))"
    r"|\b(propert(y|ies)|storage|asset)\s+portfolio\b"
    r"|\bportfolio\s+(of|offering|sale)\b"
    r"|\bmulti[\s-]?(property|site)\b", re.IGNORECASE)


def is_portfolio(*texts) -> bool:
    """True if any text carries explicit multi-property / portfolio wording."""
    return any(PORTFOLIO_RE.search(t or "") for t in texts)


# ── Cover-page street addresses ────────────────────────────────────

# A street line: number + name ending in a standard suffix. The name part
# is non-greedy so it stops at the FIRST suffix -- "3000 State Hwy 71 and
# 135 Industry Drive" yields two hits ("3000 State Hwy", "135 Industry
# Drive"), and "2.69 acres of Vacant Land" yields none (no suffix).
STREET_ADDR_RE = re.compile(
    r"(?P<num>\d{1,7})\s+(?P<name>[A-Za-z0-9][A-Za-z0-9\.'\- ]{2,60}?)"
    r"(?P<suf>St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Blvd|Boulevard|"
    r"Ln|Lane|Way|Hwy|Highway|Pkwy|Parkway)\.?\b",
    re.IGNORECASE)

# A street name should not contain a standalone integer (that signals a
# second house number bleeding into the name). Ordinal street names like
# "12th" are fine — their digits are followed by a letter.
_BARE_INT = re.compile(r"(?<!\S)\d+(?!\S)")


def _recover_address(m):
    """Return (number, name, suffix), recovering a real house number when a
    preceding ZIP (or a prior number) was captured as the house number.

    A portfolio cover lists addresses back-to-back as
    "<addr1> <CITY>, ST <ZIP> <addr2> <CITY>, ST <ZIP>", so the regex can
    anchor the second address on the ZIP instead of its own number —
    "43023 13761 LUCILLE LYND ROAD" yields num=43023 (the ZIP) and
    name="13761 LUCILLE LYND". Recovery keeps the LAST bare integer in the
    name as the real number and discards everything before it:
    -> ('13761', 'LUCILLE LYND', 'ROAD')."""
    num = m.group("num")
    name = m.group("name").strip()
    bare = list(_BARE_INT.finditer(name))
    if bare:
        last = bare[-1]
        num = last.group(0)
        name = name[last.end():].strip()
    return num, name, m.group("suf")


def cover_street_addresses(pages) -> list:
    """Distinct street-address lines on the cover page, broker hits suppressed.

    More than one distinct address on a cover is a strong portfolio signal:
    it means the cover is listing two subject properties, not a body page
    of comps. Broker-signature blocks are suppressed so "Presented by Marcus
    & Millichap, 420 Lexington Avenue" cannot add a false second address.
    """
    cover = pages[0] if pages else ""
    addrs, seen = [], set()
    for m in STREET_ADDR_RE.finditer(cover):
        if near_broker(cover, m.start()):
            continue
        num, name, suf = _recover_address(m)
        if not name:                       # nothing left -> not a real street line
            continue
        norm = " ".join(f"{num} {name} {suf}".split())
        if norm.lower() not in seen:
            seen.add(norm.lower())
            addrs.append(norm)
    return addrs


# ── Signal ─────────────────────────────────────────────────────────

def warning_text(evidence=None) -> str:
    """THE portfolio warning sentence, for every analysis surface.

    One definition — engine.run_analysis (run warnings on the results
    page), run.py (the CLI log) and output/memo_writer (the IC memo) all
    render this string, so the surfaces cannot drift apart. The
    assumptions-page banner and the LP investor summary carry their own
    audience-specific prose and are deliberately not consumers.

    ASCII only: the memo path feeds it through python-docx and the
    summary budget measures ASCII-folded text.
    """
    ev = "; ".join(str(e) for e in (evidence or []) if e)
    return (
        "Possible multi-property / portfolio CIM"
        + (f" ({ev})" if ev else "")
        + ". Extracted figures may mix portfolio- and property-level "
          "values - confirm every field against the document before "
          "relying on this analysis, or underwrite each property "
          "separately."
    )


@dataclass
class PortfolioSignal:
    """What a detection run found. `evidence` is non-empty iff flagged."""
    is_portfolio: bool = False
    evidence: list = field(default_factory=list)
    cities: list = field(default_factory=list)
    states: list = field(default_factory=list)
    addresses: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "is_portfolio": self.is_portfolio,
            "evidence": self.evidence,
            "cities": self.cities,
            "states": self.states,
            "addresses": self.addresses,
        }


def portfolio_signal(pages, filename: str = "") -> PortfolioSignal:
    """Detect whether a CIM describes more than one property.

    Union of three conservative signals, each run over the cover page only
    (a body page is where single-asset CIMs list their comps):

      * portfolio / offering wording (`PORTFOLIO_RE`),
      * more than one distinct street address on the cover,
      * more than one distinct city on the cover.

    `pages` is the per-page text list from pdf_reader.extract_pdf(); a plain
    string is accepted and treated as a single cover blob. `filename` (the
    PDF stem) is an extra wording source for filing prefixes like
    "SS 2Property KS Wichita ...".
    """
    if isinstance(pages, str):
        pages = [pages]
    pages = [p for p in (pages or []) if p]
    cover = pages[0] if pages else ""
    evidence = []

    if is_portfolio(filename, cover):
        evidence.append("portfolio / offering wording on the cover page")

    addresses = cover_street_addresses(pages)
    if len(addresses) > 1:
        evidence.append(
            f"{len(addresses)} distinct property addresses on the cover page")

    cities = sorted({c for c, _, _ in find_locations(cover)})
    if len(cities) > 1:
        evidence.append(
            f"multiple cities on the cover page: {', '.join(cities)}")

    states = sorted({s for _, s, _ in find_locations(cover)})
    return PortfolioSignal(
        is_portfolio=bool(evidence),
        evidence=evidence,
        cities=cities,
        states=states,
        addresses=addresses,
    )
