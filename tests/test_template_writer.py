"""Item E3b — the XLSM template writer decides no value of its own.

Two gates:

1. `test_no_numeric_literals_in_write_paths` — an AST walk over
   `output/template_writer.py`. Structural, runs anywhere, needs no
   workbook. This is the acceptance criterion the scoped backlog asked
   be enforced "by a test (grep or AST), not by inspection".
2. Behavior tests against a SYNTHETIC workbook. The real
   `template_uw.xlsm` is a 3 MB proprietary macro file and is
   gitignored, so CI has never had it and never will. The stub below
   carries only the cells and formulas under test, which is enough to
   assert what the writer writes — and the formulas it must overwrite
   are reproduced verbatim from the real workbook so a stub that
   drifted would fail loudly rather than pass vacuously.
"""

import ast
import os
from pathlib import Path

import openpyxl
import pytest

import config as cfg
from model.debt import resolve_debt_terms
from model.waterfall import resolve_waterfall_terms
from output import template_writer
from registry import ScenarioType

WRITER_SOURCE = Path(template_writer.__file__)


# ── Gate 1: no numeric literal decides a cell value ──────────────────

def _is_cell_write(node):
    """True for `ws["A1"] = ...` and `ws.cell(...).value = ...`.

    Row and column arguments sit on the TARGET side and are deliberately
    not checked: a column index is the template's schema, not an
    underwriting assumption. What the rule forbids is the writer
    choosing the number that lands in the cell.
    """
    for target in node.targets:
        if isinstance(target, ast.Subscript):
            return True
        if (isinstance(target, ast.Attribute)
                and target.attr == "value"
                and isinstance(target.value, ast.Call)
                and isinstance(target.value.func, ast.Attribute)
                and target.value.func.attr == "cell"):
            return True
    return False


def _local_assignments(func):
    """name -> [expressions assigned to it] inside one function."""
    out = {}
    for node in ast.walk(func):
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif (isinstance(node, (ast.AnnAssign, ast.AugAssign))
                and isinstance(node.target, ast.Name) and node.value):
            targets = [node.target]
        for target in targets:
            out.setdefault(target.id, []).append(node.value)
    return out


def _reachable_literals(expr, locals_, seen=None):
    """Numeric literals reachable from `expr`, following local names.

    A bare `ws["K180"] = value` where `value = 0.065` two lines earlier
    is the same defect as writing the literal inline, so the walk does
    not stop at the name — it follows every expression assigned to that
    name within the function. `seen` guards the self-referential case
    (`capex = ... if capex is None else capex`).
    """
    seen = set() if seen is None else seen
    found = []
    for node in ast.walk(expr):
        if (isinstance(node, ast.Constant)
                and isinstance(node.value, (int, float))
                and not isinstance(node.value, bool)):
            found.append(node.value)
        elif isinstance(node, ast.Name) and node.id not in seen:
            seen.add(node.id)
            for assigned in locals_.get(node.id, []):
                found += _reachable_literals(assigned, locals_, seen)
    return found


def _cell_write_values(tree):
    """Yield (function, assign-node, value-expr, local-assignment map)."""
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        locals_ = _local_assignments(func)
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and _is_cell_write(node):
                yield func, node, node.value, locals_


def test_no_numeric_literals_in_write_paths():
    """No numeric literal may appear on the value side of a cell write.

    This is the whole item in one assertion. Every number written into
    the workbook must trace to config, to a resolved terms object, to
    the run's results, or to a named structural constant at the top of
    the module — because a literal here is a second underwriting opinion
    that nothing reconciles against the memo and the .xlsx.

    If this fails on a legitimately structural number (a column index, a
    zero that means "at closing"), the fix is to NAME it at the top of
    template_writer.py with a comment saying why it is schema and not an
    assumption. The fix is never to add an exception here.
    """
    tree = ast.parse(WRITER_SOURCE.read_text())
    offenders = []

    for func, assign, value, locals_ in _cell_write_values(tree):
        for literal in _reachable_literals(value, locals_):
            offenders.append(
                f"{func.name} line {assign.lineno}: {literal!r}")

    assert not offenders, (
        "numeric literals decide a cell value in template_writer.py:\n  "
        + "\n  ".join(offenders)
        + "\nName it as a structural constant, or read it from config / "
          "the resolved terms / the run's results."
    )


def test_the_literal_gate_catches_a_literal_behind_a_variable():
    """The gate is only worth having if it fails when it should.

    A literal parked in a local and written a line later is the same
    defect as writing it inline, and the obvious implementation — check
    only the RHS of the cell-write statement — misses it silently. This
    pins that the walk follows local names, so a future simplification
    of `_reachable_literals` cannot quietly reopen the hole.
    """
    hidden = ast.parse(
        "def f(ws):\n"
        "    value = 0.065\n"
        "    ws['K180'] = value\n"
    )
    caught = [lit
              for _f, _a, value, locals_ in _cell_write_values(hidden)
              for lit in _reachable_literals(value, locals_)]
    assert caught == [0.065]

    # ...and does not fire on a value that traces to config.
    clean = ast.parse(
        "def f(ws):\n"
        "    value = cfg.DEBT_TERMS['rate']\n"
        "    ws['K180'] = value\n"
    )
    assert not [lit
                for _f, _a, value, locals_ in _cell_write_values(clean)
                for lit in _reachable_literals(value, locals_)]


def test_no_environment_reads_outside_the_template_path():
    """The GP_EQUITY_SHARE / GP_AM_FEE_RATE / GP_PROMOTE_PCT block is
    deleted, not re-defaulted (scoped-backlog rule 2).

    `UW_TEMPLATE_PATH` survives: it locates the file, it is not an
    underwriting assumption. Everything else that used to come from the
    environment now lives in config, where it can be found.
    """
    tree = ast.parse(WRITER_SOURCE.read_text())
    reads = [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "environ"
        and node.args and isinstance(node.args[0], ast.Constant)
    ]
    assert reads == ["UW_TEMPLATE_PATH"], reads


# ── Synthetic workbook ───────────────────────────────────────────────

# Formulas copied verbatim from template_uw.xlsm. The writer must
# overwrite the first two with values; the third proves the tier rows
# chain to H258 and so collapse to a single hurdle.
REAL_K181_FORMULA = "=+K180+0.005"
REAL_H258_FORMULA = '=IF(H64>0,0.08,IF(H64=0,0.06,"n/a"))'
REAL_H259_FORMULA = "=+H258"


def build_stub_template(path):
    """Write a minimal .xlsm standing in for the proprietary template.

    Shared with `tests/test_web_runs.py`, which drives the real
    `engine.run_analysis` against it.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Underwriting"
    wb.create_sheet("Summary")
    ws["K181"] = REAL_K181_FORMULA
    ws["H258"] = REAL_H258_FORMULA
    ws["H259"] = REAL_H259_FORMULA
    wb.save(path)
    wb.close()
    return path


@pytest.fixture
def stub_template(tmp_path, monkeypatch):
    path = build_stub_template(tmp_path / "stub_template.xlsm")
    monkeypatch.setattr(template_writer, "TEMPLATE_PATH", str(path))
    return path


class _Unit:
    def __init__(self, size_label, count, sf, rate, climate_controlled=False):
        self.size_label = size_label
        self.count = count
        self.sf = sf
        self.rate = rate
        self.climate_controlled = climate_controlled


class _CIM:
    """Only the attributes the writer touches."""

    def __init__(self, **kwargs):
        defaults = dict(
            property_name="Test Storage", address="1 Main St", city="Abilene",
            acreage=4.0, year_built=2005, asking_price=10_000_000,
            physical_occupancy=0.92, nrsf=50_000, other_income=25_000,
            mgmt_fee_pct=None, capex_estimate=0,
            unit_mix=[_Unit("10x10", 100, 100, 95.0)],
        )
        defaults.update(kwargs)
        for key, value in defaults.items():
            setattr(self, key, value)


FINANCIALS = {"adjusted_ttm_noi": {"analyst_adjusted_noi": 650_000},
              "expense_analysis": {"lines": []}}


def _scenarios(**param_overrides):
    params = dict(cfg.SCENARIO_DEFAULTS[ScenarioType.BASE])
    params.update(param_overrides)
    return {ScenarioType.BASE: {
        "params": params,
        "exit_cap": 0.0685,
        "exit_cap_detail": {"market_cap": 0.0625},
    }}


def _generate(tmp_path, cim=None, **kwargs):
    kwargs.setdefault("financial_analysis", FINANCIALS)
    kwargs.setdefault("scenario_results", _scenarios())
    out = template_writer.generate_template(
        cim_data=cim or _CIM(),
        output_dir=str(tmp_path),
        **kwargs,
    )
    wb = openpyxl.load_workbook(out, keep_vba=True)
    try:
        yield_ws = wb["Underwriting"]
        return {c.coordinate: c.value
                for row in yield_ws.iter_rows() for c in row}
    finally:
        wb.close()


# ── Gate 2: the workbook carries the run's resolved terms ────────────

def test_debt_block_equals_resolved_debt_terms(tmp_path, stub_template):
    """The XLSM's loan terms are the loan the app actually priced.

    Pre-E3b these cells asserted 60-month term / 12-month IO / 360-month
    amortization / 6.5% against a model running 120 / 0 / 300 / 6.25%.
    """
    terms = resolve_debt_terms()
    cells = _generate(tmp_path, debt_terms=terms,
                      sources_uses={"ltv": 0.62})

    assert cells["F73"] == terms.term_years * 12 == 120
    assert cells["G73"] == terms.io_months == 0
    assert cells["H73"] == terms.amort_years * 12 == 300
    assert cells["I73"] == terms.all_in_rate() == 0.0625
    # H64 is the run's own leverage, not a hardcoded all-equity 0.
    assert cells["H64"] == 0.62
    assert cells["H65"] == 0        # we model no junior/mezz tranche


def test_debt_block_survives_an_overridden_rate(tmp_path, stub_template):
    """A per-deal override reaches the workbook. This is the test that
    would fail if the writer re-read config instead of the resolved
    object handed to it."""
    terms = resolve_debt_terms({"rate": 0.075, "amort_years": 30,
                                "term_years": 7, "io_months": 24})
    cells = _generate(tmp_path, debt_terms=terms)

    assert cells["I73"] == 0.075
    assert cells["H73"] == 360
    assert cells["F73"] == 84
    assert cells["G73"] == 24


def test_no_sources_uses_leaves_the_block_all_equity(tmp_path, stub_template):
    """The CLI path, and any deal that never sized. Terms still land, so
    flipping LTC in Excel gives the terms the app would have used."""
    cells = _generate(tmp_path, sources_uses=None)
    assert cells["H64"] == 0
    assert cells["I73"] == resolve_debt_terms().all_in_rate()


def test_waterfall_block_equals_resolved_waterfall_terms(tmp_path,
                                                         stub_template):
    """Pref, promote and GP co-invest come from WATERFALL_TERMS and
    GP_COINVEST_PCT — not from the deleted env vars, which defaulted GP
    equity to 6% against a config value of 10%."""
    terms = resolve_waterfall_terms()
    cells = _generate(tmp_path, waterfall_terms=terms, am_fee_pct=cfg.AM_FEE_PCT)

    assert cells["H59"] == terms.gp_coinvest_pct == 0.10
    assert cells["G254"] == cfg.AM_FEE_PCT == 0.01
    for row in ("I259", "I260", "I261"):
        assert cells[row] == terms.promote_split == 0.20
    # H258's IF(H64>0, 0.08, ...) formula is replaced by a value; the
    # tier rows chain to it, so all four hurdles are the one pref.
    assert cells["H258"] == terms.pref_rate == 0.08
    assert cells["H259"] == REAL_H259_FORMULA
    assert cells["C253"] == cfg.GP_ENTITY_NAME


def test_waterfall_carries_a_per_deal_coinvest(tmp_path, stub_template):
    """The trap `resolve_waterfall_terms` documents: a deal edited to 25%
    co-invest must not print a 10/90 waterfall next to a 25/75 stack."""
    terms = resolve_waterfall_terms(capital_structure={"gp_coinvest_pct": 0.25})
    cells = _generate(tmp_path, waterfall_terms=terms)
    assert cells["H59"] == 0.25


def test_growth_ladder_is_scenario_driven(tmp_path, stub_template):
    """Deliberate behavior change (scoped-backlog rule 4): the flat
    0%-then-3% ladder on all six rows becomes the resolved banding —
    revenue rows on the revenue CAGR, expense rows on `exp_growth`."""
    cells = _generate(tmp_path, scenario_results=_scenarios(
        rev_cagr_yr1_3=0.04, rev_cagr_yr4_5=0.035, exp_growth=0.028))

    for row in (101, 102, 103):                      # revenue rows
        assert cells[f"C{row}"] == 0                 # year 1: in-place
        assert cells[f"D{row}"] == cells[f"E{row}"] == 0.04    # years 2-3
        assert cells[f"F{row}"] == cells[f"G{row}"] == cells[f"H{row}"] == 0.035
    for row in (104, 105, 106):                      # expense rows
        assert cells[f"C{row}"] == 0
        for col in "DEFGH":
            assert cells[f"{col}{row}"] == 0.028


def test_stabilized_vacancy_complements_the_scenario(tmp_path, stub_template):
    """Was a standing 0.10 behind a dead if/else — the one value in the
    block that matched neither the workbook's own default nor the model."""
    cells = _generate(tmp_path, scenario_results=_scenarios(stabilized_occ=0.88))
    assert cells["I146"] == 0.12
    assert cells["G146"] == pytest.approx(0.08)   # 1 - 0.92 physical


def test_credit_loss_and_bank_fees_come_from_config(tmp_path, stub_template):
    inputs = cfg.XLSM_TEMPLATE_INPUTS
    cells = _generate(tmp_path)
    assert cells["G147"] == inputs["credit_loss_in_place"]
    assert cells["I147"] == inputs["credit_loss_stabilized"]
    assert cells["G155"] == inputs["bank_fee_pct_in_place"]
    assert cells["I155"] == inputs["bank_fee_pct_stabilized"]


def test_benchmark_band_drives_the_capital_reserve(tmp_path, stub_template):
    cells = _generate(tmp_path)
    assert cells["I164"] == cfg.EXPENSE_BENCHMARKS["cap_reserve"][0]


def test_the_mgmt_fee_follows_the_resolved_target_not_the_band(
        tmp_path, stub_template):
    """This assertion used to read
    `cfg.EXPENSE_BENCHMARKS["mgmt_fee_pct"][1]`, and it still PASSES that
    way today by coincidence — `MGMT_FEE_TARGET_PCT` is now 6%, which is
    the band's high end. Same number, two routes, and the test could not
    tell them apart.

    It matters because the routes diverge the moment a deal sets a
    per-deal target: the memo and the .xlsx would move and this workbook
    — a real deliverable — would keep writing 6%, two output files
    asserting different management fees on the same deal with nothing on
    screen saying so. So the target is moved away from the band high here
    to prove which one the cell actually follows.
    """
    cells = _generate(tmp_path, mgmt_fee_target_pct=0.02)
    assert cells["G157"] == 0.02
    assert cells["I157"] == 0.02
    assert cells["G157"] != cfg.EXPENSE_BENCHMARKS["mgmt_fee_pct"][1]

    # Unset, it falls back to the config default (which IS the band high
    # today — asserted against the target, not the band).
    cells = _generate(tmp_path)
    assert cells["G157"] == cfg.MGMT_FEE_TARGET_PCT

    # A CIM that states its own rate still wins over either.
    cells = _generate(tmp_path, cim=_CIM(mgmt_fee_pct=0.045),
                      mgmt_fee_target_pct=0.02)
    assert cells["G157"] == 0.045


def test_capex_timing_comes_from_config(tmp_path, stub_template):
    inputs = cfg.XLSM_TEMPLATE_INPUTS
    cells = _generate(tmp_path, cim=_CIM(capex_estimate=250_000), capex=250_000)
    assert cells["K30"] == 250_000
    assert cells["E30"] == inputs["capex_start_month"]
    assert cells["F30"] == inputs["capex_duration_months"]


def test_missing_occupancy_warns_and_uses_the_config_assumption(
        tmp_path, stub_template, caplog):
    """The audit's complaint was the silence, not the 0.90."""
    with caplog.at_level("WARNING"):
        cells = _generate(tmp_path, cim=_CIM(physical_occupancy=None))

    assumed = cfg.XLSM_TEMPLATE_INPUTS["assumed_physical_occupancy"]
    assert cells["G146"] == pytest.approx(1 - assumed)
    assert any("Physical occupancy missing" in r.message
               for r in caplog.records)


def test_thin_deal_does_not_invent_a_cap_rate(tmp_path, stub_template, caplog):
    """The 6.5% entry-cap fallback is gone. With no scenario, no NOI and
    no price there is nothing to write, so K181 keeps the workbook's own
    entry+50bp formula — which is only safe because there is no resolved
    exit cap for it to contradict."""
    thin = _CIM(asking_price=None)
    with caplog.at_level("WARNING"):
        cells = _generate(tmp_path, cim=thin, scenario_results=None,
                          financial_analysis={})

    assert cells["K180"] is None                     # never written
    assert cells["K181"] == REAL_K181_FORMULA        # formula untouched
    assert any("no entry cap" in r.message for r in caplog.records)


def test_resolved_exit_cap_overwrites_the_entry_plus_50bp_formula(
        tmp_path, stub_template):
    cells = _generate(tmp_path)
    assert cells["K180"] == 0.0625
    assert cells["K181"] == 0.0685


def test_defaults_resolve_without_any_terms_passed(tmp_path, stub_template):
    """`run.py` passes no terms at all. Config must be the whole resolved
    set on that path, not a crash and not a literal."""
    cells = _generate(tmp_path)
    assert cells["I73"] == cfg.DEBT_TERMS["rate"]
    assert cells["H258"] == cfg.WATERFALL_TERMS["pref_rate"]
    assert cells["H59"] == cfg.GP_COINVEST_PCT
    assert cells["G254"] == cfg.AM_FEE_PCT


# ── The divergence disclosures (settled 2026-08-10) ──────────────────

def test_divergence_disclosures_land_in_the_workbook(tmp_path,
                                                     stub_template):
    """Both structural divergences are stamped INTO the workbook, not
    only into the module docstring — the docstring is the one place the
    analyst reading the .xlsm will never look."""
    cells = _generate(tmp_path)
    assert (cells[template_writer._DISCLOSURE_PREF_CELL]
            == template_writer._PREF_DISCLOSURE)
    assert (cells[template_writer._DISCLOSURE_AM_FEE_CELL]
            == template_writer._AM_FEE_DISCLOSURE)
    # And the TRUE rate stands beside the disclosure: the operator
    # rejected the gross-up (2026-08-10), reaffirming the recorded
    # stance — dollars that tie by printing a rate the fund does not
    # charge trade a visible discrepancy for a hidden one.
    assert cells["G254"] == cfg.AM_FEE_PCT


def _disclosure_texts(ws):
    return [c.value for row in ws.iter_rows() for c in row
            if isinstance(c.value, str)
            and c.value in (template_writer._PREF_DISCLOSURE,
                            template_writer._AM_FEE_DISCLOSURE)]


def _write_into(tmp_path, prepare):
    """Run the disclosure writer over a sheet `prepare` has shaped."""
    wb = openpyxl.Workbook()
    ws = wb.active
    prepare(ws)
    template_writer._write_divergence_disclosures(ws)
    return ws


def test_disclosures_survive_a_merged_band_at_the_preferred_rows(tmp_path):
    """The failure the parity test alone could not prevent, now harmless.

    A merged label band covering B263/B264 makes those cells read empty
    while B264 is a read-only MergedCell — so the old writer raised
    AttributeError mid-run and NO workbook was produced. The disclosure
    now moves below the band instead.
    """
    ws = _write_into(tmp_path, lambda w: w.merge_cells("B263:D264"))
    assert _disclosure_texts(ws) == [template_writer._PREF_DISCLOSURE,
                                     template_writer._AM_FEE_DISCLOSURE]
    # And it did NOT write into the merged band.
    assert ws["B263"].value is None and ws["B264"].value is None


def test_disclosures_never_overwrite_an_occupied_cell(tmp_path):
    """The other half: a label already sitting where we guessed."""
    def occupy(w):
        w["B263"] = "Waterfall Notes"
        w["B264"] = "=SUM(H259:H261)"

    ws = _write_into(tmp_path, occupy)
    assert ws["B263"].value == "Waterfall Notes"
    assert ws["B264"].value == "=SUM(H259:H261)"
    assert _disclosure_texts(ws) == [template_writer._PREF_DISCLOSURE,
                                     template_writer._AM_FEE_DISCLOSURE]


def test_no_room_warns_and_writes_nothing_rather_than_corrupting(
        tmp_path, caplog):
    """The last resort must still produce a workbook. A missing note with
    a loud warning beats a destroyed deliverable — and beats a crash that
    leaves the analyst with no file at all."""
    def fill(w):
        for offset in range(template_writer._DISCLOSURE_SEARCH_ROWS):
            w.cell(row=template_writer._DISCLOSURE_FIRST_ROW + offset,
                   column=template_writer._DISCLOSURE_COL).value = "taken"

    with caplog.at_level("WARNING"):
        ws = _write_into(tmp_path, fill)
    assert _disclosure_texts(ws) == []
    assert any("divergence disclosures" in r.message for r in caplog.records)


def test_disclosures_state_mechanism_and_direction():
    """The disclosure is only a disclosure while it names the mechanism
    and the direction. A future rewording may shorten it; it may not
    hollow it."""
    pref = template_writer._PREF_DISCLOSURE
    fee = template_writer._AM_FEE_DISCLOSURE
    assert "IRR hurdle" in pref and "accru" in pref
    assert "LP equity" in fee and "invested equity" in fee
    assert "light" in fee            # the direction of the gap
    assert "true rate" in fee        # what G254 actually carries


# ── The real template, when it happens to be present ─────────────────

def _is_merged(ws, coordinate: str) -> bool:
    """True if `coordinate` falls inside ANY merged range.

    Covers both halves of the failure: the anchor (a writable Cell that
    would overwrite a multi-cell label) and every other member (a
    MergedCell whose `.value` is read-only, so the write raises).
    """
    cell = ws[coordinate]
    return any(cell.row >= rng.min_row and cell.row <= rng.max_row
               and cell.column >= rng.min_col and cell.column <= rng.max_col
               for rng in ws.merged_cells.ranges)


def test_the_merge_detector_catches_both_halves_of_the_failure():
    """The tripwire is only worth having if it fails when it should, and
    `.value is None` provably does not: a merged band reads empty from
    every cell it covers. Pins the detector against the two shapes —
    anchor and member — that an emptiness check waves through."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.merge_cells("B263:D264")
    try:
        assert ws["B263"].value is None and ws["B264"].value is None
        assert _is_merged(ws, "B263")      # anchor: silent overwrite
        assert _is_merged(ws, "B264")      # member: the write raises
        assert not _is_merged(ws, "B270")  # a genuinely free cell
    finally:
        wb.close()



# The path the WRITER resolves, not a second guess at it: the module
# honors UW_TEMPLATE_PATH (render.yaml sets it to /data), and a parity
# test hardcoded to the project root would skip — reporting "no template
# here" — on exactly the machine that has one somewhere else. Read at
# import, before any fixture monkeypatches it.
REAL_TEMPLATE = Path(template_writer.TEMPLATE_PATH)


@pytest.mark.skipif(not REAL_TEMPLATE.exists(),
                    reason="proprietary template is gitignored; absent in CI")
def test_real_template_still_has_the_cells_the_stub_claims(monkeypatch):
    """Guards the stub against drift. If the real workbook's formulas or
    row mapping ever move, the synthetic tests above are asserting
    against fiction — and this is the only test that can notice."""
    wb = openpyxl.load_workbook(REAL_TEMPLATE, keep_vba=True)
    try:
        ws = wb["Underwriting"]
        assert ws["K181"].value == REAL_K181_FORMULA
        assert ws["H258"].value == REAL_H258_FORMULA
        assert ws["H259"].value == REAL_H259_FORMULA
        # The tier rows chain upward, which is what collapses four
        # hurdles into the single one model.waterfall implements.
        assert ws["H260"].value == "=+H259"
        assert ws["H261"].value == "=H260"
        # Growth ladder row labels, so the revenue/expense split is real.
        assert ws["B101"].value == "In-Place Rent"
        assert ws["B103"].value == "Other Income"
        assert ws["B104"].value == "OpEx (Excl. Taxes)"
        assert ws["B106"].value == "CapEx"
        # H64 is loan / Total Uses, which is what makes sources_uses["ltv"]
        # the right thing to write there.
        assert ws["K64"].value == "=H64*$K$55"
        # The AM fee's base really is LP equity, not invested equity —
        # the residual mismatch the module docstring stamps.
        assert ws["H254"].value == (
            '=IF(G253="% of EGR",G254*K148/12,K60*G254/12)')
        assert ws["K60"].value == "=($K$55-$K$66)*H60"
        # The PREFERRED disclosure rows should be blank and unmerged in
        # the real workbook. This is now a drift report, not a load-
        # bearing guarantee: `_free_disclosure_cells` verifies its target
        # at write time, so a wrong guess costs a lower row rather than a
        # corrupted or missing workbook. Still asserted, because the
        # first machine to open the real file is the only one that can
        # tell us the constant points somewhere sensible.
        for cell in (template_writer._DISCLOSURE_PREF_CELL,
                     template_writer._DISCLOSURE_AM_FEE_CELL):
            assert ws[cell].value is None, (
                f"{cell} is occupied in the real template — the writer "
                f"will skip past it; move _DISCLOSURE_FIRST_ROW")
            # Emptiness alone would NOT be enough (review finding): a
            # cell inside a merged band reads None whatever the band
            # displays, so this checks the shape the value cannot show.
            assert not _is_merged(ws, cell), (
                f"{cell} participates in a merged range in the real "
                f"template — the writer will skip past it; move "
                f"_DISCLOSURE_FIRST_ROW to a free area")
    finally:
        wb.close()
