"""Financial-table cells and column roles — which period does a number belong to?

`extract.parser` used to answer that positionally::

    line.t12 = values[-1]      # Assume last column is most recent
    line.t3 = values[-2]       # Second to last might be T3
    line.cim_yr1 = values[0]   # First might be pro forma

The comments were honest and the assumption was wrong. Measured across seven
real CIMs — 227 tables, 173 financial lines: **no table anywhere orders its
periods most-recent-last.** Where a trailing column exists it comes FIRST and
the projection years follow, so `values[-1]` was reading the final projection
year into `t12` and `values[0]` was reading the actual into `cim_yr1`. Of the
116 lines that carried a figure under the old rule, exactly ONE survives the
new one unchanged: 101 change value, 14 lose a figure they should never have
had, and 12 lines that were empty gain one.

That matters through exactly one consumer, and it is worth naming precisely
because the obvious one is a red herring: `CIMData.income_lines` is read by no
analysis, model or output module (only override plumbing), so the inverted GPR
row moves nothing. `expense_lines` is the live path, via
`analysis.financials._map_expense_lines`, and 71 of its 81 valued lines change.

Two rules earn their place here, and both were learned by measurement rather
than reasoning:

1. **Cells keep their position.** The old code appended only parseable values,
   so `values[i]` stopped corresponding to column `i` the moment a `$0.20SF` or
   a `37.14%` cell appeared — which is most rows. No header map can sit on top
   of a compacted list.

2. **Periods are matched by ORDER, not by index.** In the Columbus CIM the
   header puts `CURRENT` at index 1 while every data row leaves index 1 BLANK
   and prints the money at index 2. Index equality loses that CIM entirely.
   Matching the header's period sequence against the row's money cells in
   order fixes all of it. Aligning by index is the intuitive design and it is
   the one the evidence rejects.

What this module will NOT do is guess. A table whose header cannot be read
yields no period assignment at all, and the caller is expected to refuse the
line rather than book it — see `analysis.fills`, "a REFUSAL is not a fill".
Guessing is what produced the inversion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ── cell kinds ───────────────────────────────────────────────────────

KIND_MONEY = "money"
KIND_PER_SF = "per_sf"
KIND_PERCENT = "percent"
KIND_TEXT = "text"
KIND_BLANK = "blank"

# ── column roles ─────────────────────────────────────────────────────

ROLE_CURRENT = "current"
#: Trailing-three annualized. `FinancialLine.t3` has meant this all along;
#: what it HELD was column N−1, i.e. year 4 of a five-year pro forma. No CIM
#: in the corpus states a T-3 column, so this role never fires on today's
#: seven — it is here because a column the mapper cannot NAME is a column
#: that breaks the count and refuses the whole row, and silently refusing a
#: real T-3 table would be a worse failure than never having the role.
ROLE_T3 = "t3"
#: Projection years are `year_1` … `year_n`; only `year_1` is consumed today
#: (`FinancialLine.cim_yr1`), but the later ones must still be RECOGNISED or
#: the ordinal match in `assign_periods` loses its alignment.
ROLE_YEAR_PREFIX = "year_"


def is_period(role: str) -> bool:
    """A role that names a reporting period rather than a presentation column."""
    return (role == ROLE_CURRENT or role == ROLE_T3
            or role.startswith(ROLE_YEAR_PREFIX))


#: A `$/SF` column reads as money to any naive parser — `$5.58SF` only fails
#: `float()` because of the suffix, and a bare `4.63` does not fail at all.
#: The Kerrville CIM books three per-SF figures as dollars today, including a
#: `$4.63` "total operating expenses" sitting beside the real $211,591. The
#: suffix form is caught here; the bare form is caught by the header, which is
#: the only place that knows the column is per-SF — and where no header can be
#: read, by the refusal.
#: Anchored on a DIGIT or a slash, never on a bare "SF": a label cell reading
#: "Total NRSF" ends in those two letters and is not a per-SF figure.
_PER_SF_RE = re.compile(r"\d\s*/?\s*(?:SF|PSF)\b|/\s*(?:SF|SQ\.?\s*FT)\b|\bPSF\b",
                        re.IGNORECASE)
#: At least one DIGIT. `[\d,]+` alone matches a lone comma, whose group then
#: parses as the empty string.
_MONEY_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_PERCENT_RE = re.compile(r"-?[\d,]+(?:\.\d+)?\s*%")


@dataclass(frozen=True)
class Cell:
    """One source column of one row. Position is preserved even when empty."""
    raw: str
    value: Optional[float]
    kind: str


def parse_cell(raw) -> Cell:
    """Classify a single cell, never inventing a value.

    A cell carrying BOTH a percent and an amount (`37.14% $ (222,391) $2.07SF`
    arrives as one cell whenever the table is under-segmented) yields the
    AMOUNT: the percent is a derived ratio the row already states in dollars,
    so taking it would substitute a rate for a figure.
    """
    if raw is None:
        return Cell("", None, KIND_BLANK)
    text = str(raw).strip()
    if not text:
        return Cell(text, None, KIND_BLANK)

    # Per-SF first: `$5.58SF` contains a perfectly good-looking money token,
    # and whichever test runs first decides what the cell is.
    if _PER_SF_RE.search(text):
        return Cell(text, None, KIND_PER_SF)

    stripped = _strip_percents(text)
    money = _MONEY_RE.search(stripped)
    if money:
        value = float(money.group(0).replace(",", ""))
        if _is_negative(stripped, money.start(), money.end()):
            value = -value
        return Cell(text, value, KIND_MONEY)

    if _PERCENT_RE.search(text):
        return Cell(text, None, KIND_PERCENT)
    return Cell(text, None, KIND_TEXT)


def _strip_percents(text: str) -> str:
    return _PERCENT_RE.sub(" ", text)


def _is_negative(text: str, start: int, end: int) -> bool:
    """Accounting negatives, read from what SURROUNDS the digits.

    `$ (222,391)` puts the dollar sign OUTSIDE the parenthesis, so a pattern
    that expects `(` first misses it and books a credit as a charge.
    """
    before = text[:start].rstrip().rstrip("$").rstrip()
    after = text[end:].lstrip()
    if before.endswith("(") and after.startswith(")"):
        return True
    return before.endswith("-")


def parse_row(row: list) -> list[Cell]:
    """One `Cell` per source column, in order. Never compacted."""
    return [parse_cell(cell) for cell in (row or [])]


# ── header detection ─────────────────────────────────────────────────

_CURRENT_TOKENS = ("current", "actual", "t-12", "t 12", "t12", "trailing",
                   "ttm", "in place", "in-place")
_T3_TOKENS = ("t-3", "t 3", "t3", "trailing 3", "trailing three")
_YEAR_RE = re.compile(r"\b(?:year|yr\.?)\s*(\d{1,2})\b", re.IGNORECASE)
_BARE_INT_RE = re.compile(r"^\s*(\d{1,2})\s*$")
_PER_SF_HEADER_RE = re.compile(r"\bper\s*sf\b|\$\s*/\s*sf\b|\bpsf\b",
                               re.IGNORECASE)


def _header_role(text: str) -> Optional[str]:
    low = text.lower()
    if _PER_SF_HEADER_RE.search(low):
        return KIND_PER_SF
    year = _YEAR_RE.search(text)
    if year:
        return f"{ROLE_YEAR_PREFIX}{int(year.group(1))}"
    # T-3 before the trailing-twelve tokens: "Trailing 3" contains "trailing".
    if any(tok in low for tok in _T3_TOKENS):
        return ROLE_T3
    if any(tok in low for tok in _CURRENT_TOKENS):
        return ROLE_CURRENT
    return None


def header_roles(row: list) -> dict[int, str]:
    """Column index → role for one candidate header row.

    Bare integers become `year_N`, but ONLY beside a spelled-out period. The
    Dallas pro forma heads its columns `Year | T-12 Broker Adjusted | 1 | 2 |
    3 | 4 | 5` — fourteen lines and six periods that a `year N` pattern alone
    cannot see. Requiring a spelled-out neighbour is what keeps a bare `5` in
    a units column from becoming a projection year.

    Column 0 never takes a role: it is the label column in every table in the
    corpus, and `assign_periods` reads no money from it either. Wichita's
    footnote row `['Current to Year 1', '9.63%', '', '1.', …]` is exactly the
    shape that abuses it.
    """
    roles: dict[int, str] = {}
    for i, cell in enumerate(row or []):
        if i == 0:
            continue
        role = _header_role(str(cell or ""))
        if role:
            roles[i] = role

    spelled = any(is_period(r) for r in roles.values())
    if spelled:
        for i, cell in enumerate(row or []):
            if i == 0 or i in roles:
                continue
            bare = _BARE_INT_RE.match(str(cell or ""))
            if bare and 1 <= int(bare.group(1)) <= 10:
                roles[i] = f"{ROLE_YEAR_PREFIX}{int(bare.group(1))}"
    return roles


def find_header(rows: list) -> Optional[dict[int, str]]:
    """The topmost row that DECLARES periods rather than reporting them.

    A data row can look like a header: Wichita carries
    `['Current to Year 1', '9.63%', '', '1.', 'Pro Forma Taxes are increased
    by 2.50% each year']` on three separate pages, which matches "year 1" and
    would hand every following row a period map built from a footnote. So a
    row is rejected when a cell that carries NO period role parses as money —
    headers name periods, they do not price them.

    The unroled qualifier is the whole rule, not a refinement: `Year 1` and
    the bare `1` of a `1 | 2 | 3 | 4 | 5` header both parse as money on their
    own digits, so a flat "no money anywhere" test rejects every real header
    in the corpus. It was written that way first, and found zero headers in
    all 34 line-bearing tables.
    """
    for row in rows or []:
        roles = header_roles(row)
        cells = parse_row(row)
        if any(c.kind == KIND_MONEY and i not in roles
               for i, c in enumerate(cells) if i > 0):
            continue
        periods = [r for r in roles.values() if is_period(r)]
        if len(periods) >= 2:
            return roles
    return None


# ── period assignment ────────────────────────────────────────────────

def period_sequence(roles: dict[int, str]) -> list[str]:
    """Period roles in column order: `['current', 'year_1', …]`."""
    return [roles[i] for i in sorted(roles) if is_period(roles[i])]


def assign_periods(roles: dict[int, str], row: list) -> Optional[dict[str, float]]:
    """Map a data row's money cells onto the header's periods, or refuse.

    ORDINAL, not positional — see the module docstring: Columbus declares
    `CURRENT` at header index 1 and prints the money at data index 2, and all
    52 of its lines fail under index equality.

    Two shapes are accepted and everything else is refused:

    - one money cell per PERIOD → the direct match;
    - one per DECLARED COLUMN, periods and `$/SF` columns together → the
      `value, $/SF, value, $/SF` interleave Abilene heads as
      `INCOME | Current | PER SF | Year 1 | PER SF NOTES`. Its per-SF figures
      print bare (`5.58`, no suffix), so they parse as money and the row
      carries twice the period count. Matching against the full declared
      sequence rather than assuming an alternation is what makes this general:
      the header states where the per-SF columns are, so nothing needs to be
      inferred from the stride.
    - anything else → `None`. A partial match is where guessing is most
      tempting and least defensible: nothing identifies WHICH periods the
      present cells belong to, and decision 9's lesson is that a wrong number
      costs more than a missing one.

    Column 0 is the LABEL and is never data, matching what
    `_parse_financial_tables` has always done (`row[0]` names the line,
    `row[1:]` prices it). Scanning it would cost real rows: Wichita's
    `Effective Gross Income1` carries a footnote marker that parses as the
    number 1, pushing the row to seven money cells against six periods and
    refusing a line that maps perfectly.
    """
    periods = period_sequence(roles)
    if not periods:
        return None

    money = [c.value for c in parse_row(row)[1:] if c.kind == KIND_MONEY]
    if not money:
        return None

    if len(money) == len(periods):
        return dict(zip(periods, money))

    declared = [roles[i] for i in sorted(roles)]
    if len(money) == len(declared):
        return {role: val for role, val in zip(declared, money)
                if is_period(role)}
    return None
