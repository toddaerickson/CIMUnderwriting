"""Item G — the LP-facing 2-page investor summary.

Two things are worth testing here and they are different:

1. **It is a second RENDERING, not a second computation.** Every figure
   must trace to the same result dicts the IC memo receives. The tests
   below feed deliberately distinctive values and assert those exact
   values appear, so a helper that quietly recomputed anything would
   print a different number and fail.

2. **It fits on two pages.** The scope contract says "assert the page
   count, do not eyeball it", and that is harder than it sounds —
   python-docx does not paginate. Page count is decided by Word at
   render time, and the only way to get a true count is to render
   through a headless office suite, which is not installed and would be
   a heavyweight new dependency for one assertion (the repo's
   no-net-complexity guardrail).

   So `_estimated_lines` below models rendered height from the document
   body against the known page geometry and font, and the fit tests
   assert each page's content lands inside the printable area. It is a
   CALIBRATED ESTIMATE, not a page count, and it is named that way on
   purpose — it catches the failure that actually happens (a deal whose
   content grows past the page) without pretending to be a renderer.
   Its companion is the structural test: exactly one page break, so the
   document is two pages by construction as long as the content fits.
"""

import os

import pytest
from docx import Document

import config as cfg
from output.memo_writer import (_SUMMARY_BODY_PT, _SUMMARY_MAX_RISKS,
                                _SUMMARY_PAGE_MARGIN_IN,
                                generate_investor_summary)

# ── Page geometry ────────────────────────────────────────────────────
# US Letter, the python-docx default. Printable height is what is left
# after the margins `generate_investor_summary` sets.
_PAGE_HEIGHT_IN = 11.0
_PRINTABLE_IN = _PAGE_HEIGHT_IN - 2 * _SUMMARY_PAGE_MARGIN_IN

# Calibri at 10pt sets on roughly 12pt of leading; Word's default
# paragraph spacing adds ~8pt after each block-level element. Both are
# expressed in inches so the budget is in the same unit as the page.
_LINE_IN = (_SUMMARY_BODY_PT * 1.2) / 72.0
_BLOCK_SPACING_IN = 8.0 / 72.0
# Characters per line at 10pt Calibri across the 7.1in text column, and
# the same density expressed per inch so table cells can be measured
# against their own (much narrower) column width.
_TEXT_COLUMN_IN = 8.5 - 2 * _SUMMARY_PAGE_MARGIN_IN
_CHARS_PER_LINE = 110
_CHARS_PER_IN = _CHARS_PER_LINE / _TEXT_COLUMN_IN
# Cell padding, on top of however many lines the cell text wraps to.
_TABLE_ROW_PAD_IN = _LINE_IN * 0.35


def _wrapped_lines(text, width_in) -> int:
    return max(1, -(-len(text or "") // max(1, int(_CHARS_PER_IN * width_in))))


def _estimated_height_in(blocks) -> float:
    """Estimated rendered height, in inches, of a list of docx blocks.

    Table rows are measured by the TALLEST cell, wrapped against that
    cell's own column width — not assumed to be one line. The risks
    table puts up to `_SUMMARY_MAX_RISK_CHARS` into a ~2.4in column,
    which wraps to four or five lines; pricing that row as one line
    would let the estimate pass while the real page overflowed, which
    is precisely the failure this test exists to catch.
    """
    total = 0.0
    for kind, payload in blocks:
        if kind == "table":
            rows, cols = payload
            col_in = _TEXT_COLUMN_IN / max(1, cols)
            for cells in rows:
                tallest = max(_wrapped_lines(c, col_in) for c in cells)
                total += tallest * _LINE_IN + _TABLE_ROW_PAD_IN
            total += _BLOCK_SPACING_IN
        else:
            text, size_pt = payload
            lines = _wrapped_lines(text, _TEXT_COLUMN_IN)
            total += lines * (size_pt * 1.2) / 72.0 + _BLOCK_SPACING_IN
    return total


def _pages(path):
    """Split the document body into pages at explicit page breaks.

    Returns a list of block lists, one per page, walking the body in
    document order so tables and paragraphs stay interleaved.
    """
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(path)
    body = doc.element.body
    pages, current = [], []

    for child in body.iterchildren():
        if child.tag.endswith("}p"):
            para = Paragraph(child, doc)
            size = (para.runs[0].font.size.pt
                    if para.runs and para.runs[0].font.size
                    else _SUMMARY_BODY_PT)
            if "w:br" in child.xml and 'w:type="page"' in child.xml:
                pages.append(current)
                current = []
                continue
            current.append(("p", (para.text, size)))
        elif child.tag.endswith("}tbl"):
            table = Table(child, doc)
            rows = [[c.text for c in row.cells] for row in table.rows]
            cols = max((len(r) for r in rows), default=1)
            current.append(("table", (rows, cols)))
    pages.append(current)
    return pages


def _text(path) -> str:
    doc = Document(path)
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts += [c.text for c in row.cells]
    return "\n".join(parts)


# ── Fixtures ─────────────────────────────────────────────────────────

class _CIM:
    def __init__(self, **kw):
        defaults = dict(
            property_name="Sunbelt Storage Portfolio",
            address="4100 Industrial Parkway", city="Abilene", state="TX",
            asking_price=10_000_000, price_per_sf=200.0, nrsf=50_000,
            physical_occupancy=0.92, economic_occupancy=0.78,
        )
        defaults.update(kw)
        for k, v in defaults.items():
            setattr(self, k, v)


def _scenarios(**over):
    def one(irr, moic, exit_cap):
        d = {"irr": irr, "moic": moic, "exit_cap": exit_cap,
             "entry_cap": 0.065, "yield_on_cost": 0.0712,
             "noi_projection": [1, 2, 3, 4, 5]}
        d.update(over)
        return d
    return {"bear": one(0.041, 1.19, 0.0785),
            "base": one(0.1234, 1.87, 0.0685),
            "bull": one(0.211, 2.44, 0.0585)}


# `assumption_stamp` shape mirrors model.levered._stamp — label carries
# the RESOLVED convention, which is what has to reach the reader.
STAMP = [
    {"key": "pref_rate", "label": "8.00% preferred return, annually "
                                  "compounded on unreturned capital"},
    {"key": "promote_split", "label": "20% promote on the LP residual"},
    {"key": "am_fee_treatment", "label": "Asset-management fee charged above "
                                         "the waterfall — 1.00% of invested "
                                         "equity, measured at the start of "
                                         "each period"},
]
LEVERED = {"base": {"lp_net_irr": 0.1567, "lp_moic": 2.03,
                    "assumption_stamp": STAMP},
           "bear": {"lp_net_irr": 0.012, "lp_moic": 1.05},
           "bull": {"lp_net_irr": 0.2891, "lp_moic": 2.99}}

SOURCES_USES = {"total_uses": 10_350_000, "senior_debt": 6_400_000,
                "total_equity": 3_950_000, "gp_equity": 395_000,
                "lp_equity": 3_555_000, "gp_coinvest_pct": 0.10}

PHYSICAL = {"price_vs_replacement": {
    "comparable": True, "asking_price": 10_000_000, "asking_per_sf": 200.0,
    "replacement_cost": 13_000_000, "discount_to_replacement": 0.2308}}

MARKET = {"demographics": {"population_3mi": 87_450,
                           "median_hhi_3mi": 64_300},
          "msa_info": {"msa_name": "Abilene", "is_top_50": False}}

GATES = [{"gate": 5, "name": "No Oversupply Flag",
          "actual": "6.2 SF/capita", "result": "PASS"}]

RISKS = {"risks": [
    {"risk": "Low severity item", "severity": "Low", "mitigation": "Monitor",
     "category": "Market"},
    {"risk": "Property tax reassessment on sale", "severity": "High",
     "mitigation": "Model reassessed basis", "category": "Financial"},
    {"risk": "Economic occupancy 14 points below physical", "severity": "High",
     "mitigation": "Audit concessions and delinquency", "category": "Ops"},
    {"risk": "New supply within 3 miles", "severity": "Medium",
     "mitigation": "Confirm pipeline", "category": "Market"},
]}


def _generate(tmp_path, **over):
    kw = dict(property_name="Sunbelt Storage Portfolio", cim_data=_CIM(),
              market_analysis=MARKET, physical_analysis=PHYSICAL,
              scenario_results=_scenarios(), risk_analysis=RISKS,
              gate_results=GATES, sources_uses=SOURCES_USES,
              levered=LEVERED, output_dir=str(tmp_path))
    kw.update(over)
    return generate_investor_summary(**kw)


# ── It fits on two pages ─────────────────────────────────────────────

def test_document_has_exactly_one_page_break(tmp_path):
    """Two pages by construction. More than one break means a section
    escaped the fixed layout; none means page 2's content ran onto
    page 1."""
    assert len(_pages(_generate(tmp_path))) == 2


def test_estimated_content_fits_both_pages(tmp_path):
    """Calibrated height estimate, not a render — see the module
    docstring for why."""
    for i, page in enumerate(_pages(_generate(tmp_path)), start=1):
        height = _estimated_height_in(page)
        assert height <= _PRINTABLE_IN, (
            f"page {i} estimated at {height:.2f}in against a "
            f"{_PRINTABLE_IN:.2f}in printable area")


def test_fits_with_the_longest_realistic_deal(tmp_path):
    """The acceptance criterion's worst case: longest property name, all
    three scenarios populated, the maximum number of risks each at
    maximum message length. If truncation is doing its job this fits with
    the same margin as the ordinary deal."""
    long_risks = {"risks": [
        {"risk": "R" * 400, "severity": sev, "mitigation": "M" * 400,
         "category": "Market"}
        for sev in ("High", "High", "High", "Medium", "Medium", "Low")]}
    path = _generate(
        tmp_path,
        property_name="P" * 300,
        cim_data=_CIM(property_name="P" * 300, address="A" * 200,
                      city="C" * 100, state="TX"),
        risk_analysis=long_risks)

    pages = _pages(path)
    assert len(pages) == 2
    for i, page in enumerate(pages, start=1):
        height = _estimated_height_in(page)
        assert height <= _PRINTABLE_IN, (
            f"page {i} overflows at {height:.2f}in with maximal content")


def test_risks_are_capped_and_the_omission_is_disclosed(tmp_path):
    """Printing three risks without saying how many exist would read as
    'there are three risks', which is the one thing a condensation must
    not imply."""
    body = _text(_generate(tmp_path))
    assert "Property tax reassessment on sale" in body   # High
    assert "Economic occupancy 14 points below physical" in body  # High
    assert "New supply within 3 miles" in body           # Medium, 3rd
    assert "Low severity item" not in body               # cut
    assert f"{_SUMMARY_MAX_RISKS} of {len(RISKS['risks'])}" in body


# ── It renders the model's numbers, and does not compute its own ─────

def test_figures_are_read_from_the_result_dicts(tmp_path):
    body = _text(_generate(tmp_path))
    assert "12.3%" in body            # base unlevered IRR
    assert "1.87x" in body            # base unlevered MOIC
    assert "15.7%" in body            # LP net IRR
    assert "2.03x" in body            # LP net MOIC
    assert "6.9%" in body             # base exit cap
    assert "$3,950,000" in body       # equity required
    assert "$6,400,000" in body       # senior debt
    assert "87,450" in body           # 3-mile population
    assert "6.2 SF/capita" in body    # read off gate 5, not recomputed


def test_scenario_table_carries_all_three_cases(tmp_path):
    body = _text(_generate(tmp_path))
    for value in ("4.1%", "12.3%", "21.1%"):     # bear / base / bull IRR
        assert value in body
    for value in ("1.2%", "28.9%"):              # bear / bull LP net IRR
        assert value in body


def test_thesis_bullets_restate_computed_facts(tmp_path):
    body = _text(_generate(tmp_path))
    # Basis bullet quotes the replacement-cost discount it was given.
    assert "23.1%" in body
    assert "$13,000,000" in body
    # Spread bullet fires because 92% - 78% clears the config threshold.
    assert cfg.GATES["econ_phys_spread_flag"] <= 0.14
    assert "14-point spread" in body


def test_thesis_omits_the_spread_bullet_below_the_config_threshold(tmp_path):
    """The bullet is gated on the same constant the mismanagement screen
    uses, so it cannot claim an opportunity the gate would not flag."""
    body = _text(_generate(tmp_path, cim_data=_CIM(physical_occupancy=0.92,
                                                   economic_occupancy=0.90)))
    assert "spread" not in body.lower()


# ── It degrades cleanly ──────────────────────────────────────────────

def test_unlevered_deal_omits_the_levered_rows(tmp_path):
    """Scope contract: 'degrades cleanly when the levered layer is
    absent'. Omitted, not printed as a row of N/A."""
    body = _text(_generate(tmp_path, levered=None))
    assert "LP Net IRR" not in body
    assert "12.3%" in body            # the unlevered screen still prints
    assert "Unlevered IRR" in body


def test_thin_deal_still_produces_a_document(tmp_path):
    """The early-look CIM: no scenarios, no capital stack, no risks, no
    market data. This is the artifact most likely to be generated from
    thin data, so it must not raise."""
    path = generate_investor_summary(
        property_name="Thin Deal", cim_data=_CIM(asking_price=None,
                                                 price_per_sf=None,
                                                 physical_occupancy=None,
                                                 economic_occupancy=None),
        market_analysis={}, physical_analysis={}, scenario_results={},
        risk_analysis={}, gate_results=None, sources_uses=None,
        levered=None, output_dir=str(tmp_path))

    assert os.path.isfile(path)
    body = _text(path)
    assert "Thin Deal" in body
    assert "Scenario Returns" not in body
    assert "Capital Stack" not in body


# ── The legal legend is not optional ─────────────────────────────────

def test_every_document_carries_the_securities_legend(tmp_path):
    """Distribution of this document sits behind the operator's General
    Counsel gate. The legend is what makes an un-cleared copy visibly
    not an offer, so it ships on every rendering including the thin one.
    """
    for path in (_generate(tmp_path),
                 _generate(tmp_path, levered=None, sources_uses=None)):
        body = _text(path)
        assert "not an offer to sell" in body
        assert "not investment advice" in body


# ── CLAUDE.md decision 7: no LP net IRR without its assumption stamp ──

def test_levered_figures_carry_their_assumption_stamp(tmp_path):
    """The rule binds hardest on THIS surface: it is the only one that
    leaves the firm, and a reader outside it has no memo section 6 and
    no Returns tab to cross-check which conventions produced the number.
    """
    body = _text(_generate(tmp_path))
    assert "15.7%" in body                       # the LP net IRR is printed
    for row in STAMP:                            # ...so the stamp must be too
        assert row["label"] in body
    assert "subject to the final partnership agreement" in body


def test_unlevered_deal_gains_no_orphaned_stamp(tmp_path):
    body = _text(_generate(tmp_path, levered=None))
    assert "LP net returns are computed under" not in body


# ── Regressions found in review ──────────────────────────────────────

def test_msa_is_read_from_the_key_market_analysis_actually_emits(tmp_path):
    """`analysis.market._assess_msa` emits `msa_name`, never `msa`.
    Reading the wrong key printed "Not identified" on every deal — and
    on a top-50 deal, the self-contradicting "Not identified — top-50
    MSA". The original fixture used the wrong key too, so the unit tests
    could not see it; this asserts against the real schema."""
    from analysis.market import _assess_msa

    class _M:
        msa = "Dallas"
        city = "Dallas"
    real = _assess_msa(_M())
    assert "msa_name" in real

    body = _text(_generate(tmp_path, market_analysis={
        "demographics": {"population_3mi": 87_450}, "msa_info": real}))
    assert "Dallas — top-50 MSA" in body
    assert "Not identified" not in body


def test_spread_bullet_survives_a_zero_economic_occupancy(tmp_path):
    """A property collecting nothing is the LOUDEST mismanagement case,
    and truthiness on the pair silently dropped it."""
    body = _text(_generate(tmp_path, cim_data=_CIM(physical_occupancy=0.85,
                                                   economic_occupancy=0.0)))
    assert "85-point spread" in body


def test_thin_deal_omits_the_market_snapshot(tmp_path):
    """Same contract as every other section: omitted when empty, never a
    table of N/A rows. An invented-looking market snapshot is worse on
    an LP-facing document than a missing one."""
    body = _text(generate_investor_summary(
        property_name="Thin Deal", cim_data=_CIM(),
        market_analysis={}, physical_analysis={}, scenario_results={},
        risk_analysis={}, gate_results=None, sources_uses=None,
        levered=None, output_dir=str(tmp_path)))
    # The section with no data at all is omitted...
    assert "Market Snapshot" not in body
    assert "Scenario Returns" not in body
    # ...but gaps inside a section that DOES render are shown, not
    # dropped: an LP cannot tell a missing metric from an uncomputed one
    # if the row simply vanishes.
    assert "Key Metrics" in body
    assert "Entry Cap" in body
    assert "N/A" in body


# ── The filename helper is now shared, and disambiguates ─────────────

def test_long_names_do_not_collide_across_the_writers(tmp_path):
    """`output.safe_filename` is one definition for all three writers.
    `excel_writer`'s private copy was uncapped, and `generate_excel` is
    called UNWRAPPED, so a long property name aborted the whole run one
    line after the memo succeeded."""
    from output import MAX_FILENAME_STEM, safe_filename

    a = "Fund IV Self-Storage Portfolio - " + "X" * 60 + " - Abilene, TX"
    b = "Fund IV Self-Storage Portfolio - " + "X" * 60 + " - Waco, TX"
    assert len(safe_filename(a)) <= MAX_FILENAME_STEM
    assert safe_filename(a) != safe_filename(b)
    # Names that already fit are untouched, so nothing that works today
    # gets a new filename.
    assert safe_filename("Sunbelt Storage") == "Sunbelt_Storage"
