"""Typographic budget for the LP-facing investor summary (item G).

## What this guarantees, and what it does not

python-docx does not paginate. It emits OOXML; the RENDERER decides where
pages break. So "assert the page count" cannot be done in this repo —
`soffice`/`libreoffice` are not installed and adding one is a heavyweight
dependency for a single assertion.

The answer is to **constrain Word's layout rather than predict it**. The
writer pins page geometry, uses named styles with EXACTLY line spacing,
and gives every block an EXACTLY row height, so Word cannot reflow the
document. What remains is a *content budget*: this module estimates how
many points each block will occupy and refuses to emit a document whose
content exceeds the page.

**This is a content budget, not a page count.** It is wrong if:

- Word substitutes a non-metric-compatible font. Calibri and its metric
  clone Carlito are safe; most substitutes are not.
- A block gains content without a matching EXACTLY row height.
- A glyph is much wider than its bucket. Non-Latin text is measured at
  FULL WIDTH (see `_UNKNOWN_W`) rather than rejected: an earlier draft
  raised on any non-ASCII, and the pipeline promptly fed it a "≥" from
  its own gate text and killed the document. Refusing to measure is not
  safety when the input is legitimate; measuring wide is.

The bucket widths are deliberately over-wide, so the estimate errs toward
declaring overflow that would not have happened rather than passing a
document that overflows. `tests/test_investor_summary.py` carries an
opt-in `soffice` render-and-count that is the only thing which
re-validates the calibration.
"""

import math

# ── Page geometry, pinned by the writer ──────────────────────────────

PAGE_WIDTH_IN = 8.5
PAGE_HEIGHT_IN = 11.0
MARGIN_X_IN = 0.6
MARGIN_Y_IN = 0.5

USABLE_WIDTH_IN = PAGE_WIDTH_IN - 2 * MARGIN_X_IN          # 7.3in
USABLE_WIDTH_PT = USABLE_WIDTH_IN * 72.0                   # 525.6pt
USABLE_HEIGHT_PT = (PAGE_HEIGHT_IN - 2 * MARGIN_Y_IN) * 72.0   # 720pt

# Budget at 90% of the page. The 10% is headroom against the calibration
# being slightly optimistic, not slack to be spent.
PAGE_BUDGET_PT = USABLE_HEIGHT_PT * 0.90                   # 648pt
# A page 2 that renders nearly empty is also a defect — it means the
# truncation ladder ate content that should have been there. A one-sided
# assert never catches that, so the floor is part of the contract.
#
# Calibrated by MEASURING page 2, not by estimating it: a complete deal
# runs 387pt at 9pt body, and the same deal stripped of its plan table
# and risks falls to 229pt. 240pt sits just above that floor and well
# under the real figure. It is deliberately NOT half the page — this
# document is dense and short by design, and a floor the real content
# cannot clear is a test that only ever fails.
#
# **What this does and does not catch.** The caller applies it only when
# the deal HAS scenarios and risks, so it catches a block that vanished
# from a deal that should have had it — a broken section guard, a
# truncation ladder that took too much. It does NOT catch "the deal had
# no risks", because that path is exempt by construction: a thin
# early-look CIM is legitimately short and must still render.
PAGE_MIN_PT = 240.0

# ── Calibri advance widths, three buckets, per em/1000 ───────────────
# Real Calibri has a per-glyph table; three buckets calibrated wide is
# enough to decide "does this wrap", which is all the budget needs.

_NARROW = set("iljtIf.,;:'!|()[]- ")
_WIDE = set("mMWw@%")
_NARROW_W = 300
_WIDE_W = 900
_DEFAULT_W = 520
# Anything outside ASCII: measured at full width. CJK and emoji really
# are ~1000/em, and for a stray symbol in a Latin string this merely
# over-estimates — which is the direction this module is allowed to be
# wrong in. `memo_writer._ascii` folds the symbols the pipeline actually
# emits (≥ ≤ ± × → −) to ASCII before they ever reach here, so this is
# the backstop, not the common path.
_UNKNOWN_W = 1000
_BOLD_FACTOR = 1.06


def text_width_pt(text: str, size_pt: float, bold: bool = False) -> float:
    """Advance width of `text` at `size_pt`, in points.

    Non-ASCII is measured at full width rather than rejected. The
    original design raised, on the theory that an unmeasurable glyph
    should never reach a page budget — but the first thing the real
    pipeline fed it was "≥" from `analysis.filters`' own gate text, and
    a document that refuses to render because a risk description
    contains a maths symbol is a worse failure than a slightly wide
    estimate. Over-estimating is the direction this module is allowed to
    be wrong in.
    """
    if not text:
        return 0.0
    total = 0
    for char in text:
        if not char.isascii():
            total += _UNKNOWN_W
        elif char in _NARROW:
            total += _NARROW_W
        elif char in _WIDE:
            total += _WIDE_W
        else:
            total += _DEFAULT_W
    width = (total / 1000.0) * size_pt
    return width * _BOLD_FACTOR if bold else width


def estimate_lines(text: str, size_pt: float, col_pt: float,
                   bold: bool = False) -> int:
    """How many rendered lines `text` occupies in a `col_pt`-wide column."""
    if col_pt <= 0:
        raise ValueError(f"column width must be positive, got {col_pt}")
    if not text:
        return 1
    return max(1, math.ceil(text_width_pt(text, size_pt, bold) / col_pt))


# ── Block estimation ─────────────────────────────────────────────────
# The writer declares each block; this turns declarations into points.
# Leading is EXACTLY in the document, so line height is the style's
# exact spacing, not a font-dependent guess.

def line_height_pt(size_pt: float) -> float:
    """EXACTLY line spacing the writer sets for a style of this size."""
    return size_pt * 1.15


def paragraph_pt(text: str, size_pt: float, col_pt: float = None,
                 bold: bool = False, space_after_pt: float = 2.0) -> float:
    col_pt = USABLE_WIDTH_PT if col_pt is None else col_pt
    lines = estimate_lines(text, size_pt, col_pt, bold)
    return lines * line_height_pt(size_pt) + space_after_pt


def table_pt(rows, size_pt: float, col_widths_pt,
             space_after_pt: float = 4.0, cell_pad_pt: float = 2.0) -> float:
    """Points for a table.

    Each row is as tall as its TALLEST cell, wrapped against that cell's
    OWN column width. Pricing a row at one line regardless of cell text
    is the mistake that lets a 150-character risk mitigant in a 2.4in
    column pass a budget it actually blows.
    """
    total = 0.0
    for cells in rows:
        tallest = 1
        for i, cell in enumerate(cells):
            width = col_widths_pt[i] if i < len(col_widths_pt) else col_widths_pt[-1]
            tallest = max(tallest, estimate_lines(str(cell or ""), size_pt, width))
        total += tallest * line_height_pt(size_pt) + cell_pad_pt
    return total + space_after_pt


class InvestorSummaryOverflow(RuntimeError):
    """A page's content exceeds its budget.

    Raised rather than silently shrinking the font or dropping content:
    an auto-shrunk document is one nobody notices is wrong, and this
    document goes to investors. The message names the page, the offending
    block, the estimate and the overage so the fix is obvious.
    """

    def __init__(self, page: str, estimated_pt: float, budget_pt: float,
                 blocks=None):
        self.page = page
        self.estimated_pt = estimated_pt
        self.budget_pt = budget_pt
        self.blocks = blocks or []
        over = estimated_pt - budget_pt
        worst = ""
        if blocks:
            name, pts = max(blocks, key=lambda b: b[1])
            worst = f" Largest block: {name} at {pts:.0f}pt."
        super().__init__(
            f"{page} content is {estimated_pt:.0f}pt against a "
            f"{budget_pt:.0f}pt budget — over by {over:.0f}pt.{worst} "
            "Tighten the content or extend the truncation ladder; do not "
            "raise the budget, which is already 90% of the usable page.")


class InvestorSummaryUnderflow(RuntimeError):
    """A page rendered far emptier than its content should allow.

    The mirror of `InvestorSummaryOverflow`, and the one a single-sided
    assert never catches: if the truncation ladder drops a block it
    should have kept, the document still "fits" — it is just missing the
    plan, or the risks, on a page an investor is reading.
    """

    def __init__(self, page: str, estimated_pt: float, floor_pt: float):
        self.page = page
        self.estimated_pt = estimated_pt
        self.floor_pt = floor_pt
        super().__init__(
            f"{page} rendered only {estimated_pt:.0f}pt against a "
            f"{floor_pt:.0f}pt floor. A page this empty means a block that "
            "should have rendered did not — check the truncation ladder and "
            "the section guards before relaxing the floor.")


class PageBudget:
    """Accumulates block estimates for one page and enforces the budget."""

    def __init__(self, name: str, budget_pt: float = PAGE_BUDGET_PT):
        self.name = name
        self.budget_pt = budget_pt
        self.blocks = []

    def add(self, label: str, points: float) -> None:
        self.blocks.append((label, points))

    @property
    def total_pt(self) -> float:
        return sum(pts for _, pts in self.blocks)

    def check(self, floor_pt: float = None) -> None:
        """Raise if over budget. `floor_pt` guards the opposite defect."""
        if self.total_pt > self.budget_pt:
            raise InvestorSummaryOverflow(
                self.name, self.total_pt, self.budget_pt, self.blocks)
        if floor_pt is not None and self.total_pt < floor_pt:
            raise InvestorSummaryUnderflow(self.name, self.total_pt, floor_pt)
