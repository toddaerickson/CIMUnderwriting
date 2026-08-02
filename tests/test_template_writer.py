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

def _cell_write_values(tree):
    """Yield the VALUE expression of every cell write in the module.

    A cell write is `ws["A1"] = <value>` or
    `ws.cell(row=.., column=..).value = <value>`. Row and column
    arguments sit on the TARGET side and are deliberately not checked:
    a column index is the template's schema, not an underwriting
    assumption. What the rule forbids is the writer choosing the number
    that lands in the cell.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            is_subscript = isinstance(target, ast.Subscript)
            is_cell_value = (
                isinstance(target, ast.Attribute)
                and target.attr == "value"
                and isinstance(target.value, ast.Call)
                and isinstance(target.value.func, ast.Attribute)
                and target.value.func.attr == "cell"
            )
            if is_subscript or is_cell_value:
                yield node, node.value


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

    for assign, value in _cell_write_values(tree):
        for node in ast.walk(value):
            if isinstance(node, ast.Constant) and isinstance(
                    node.value, (int, float)) and not isinstance(
                    node.value, bool):
                offenders.append(f"line {assign.lineno}: {node.value!r}")

    assert not offenders, (
        "numeric literals decide a cell value in template_writer.py:\n  "
        + "\n  ".join(offenders)
        + "\nName it as a structural constant, or read it from config / "
          "the resolved terms / the run's results."
    )


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


@pytest.fixture
def stub_template(tmp_path, monkeypatch):
    """A minimal .xlsm standing in for the proprietary template."""
    path = tmp_path / "stub_template.xlsm"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Underwriting"
    wb.create_sheet("Summary")
    ws["K181"] = REAL_K181_FORMULA
    ws["H258"] = REAL_H258_FORMULA
    ws["H259"] = REAL_H259_FORMULA
    wb.save(path)
    wb.close()
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


def test_benchmark_bands_drive_reserve_and_mgmt_fee(tmp_path, stub_template):
    cells = _generate(tmp_path)
    assert cells["I164"] == cfg.EXPENSE_BENCHMARKS["cap_reserve"][0]
    assert cells["G157"] == cfg.EXPENSE_BENCHMARKS["mgmt_fee_pct"][1]
    # A CIM that states its own rate wins over the benchmark.
    cells = _generate(tmp_path, cim=_CIM(mgmt_fee_pct=0.045))
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


# ── The real template, when it happens to be present ─────────────────

REAL_TEMPLATE = Path(__file__).resolve().parents[1] / "template_uw.xlsm"


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
    finally:
        wb.close()
