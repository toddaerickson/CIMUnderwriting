"""Item T, task 1 — the characterization safety net.

Item T moves roughly fifty hard-coded assumptions out of `analysis/`,
`model/` and `output/` and into config. Every one of those edits touches a
live literal in the middle of the pricing path, and the failure mode is
silent: a moved literal that lands one decimal off changes an IRR by a few
basis points and nothing complains.

So before any of it: **pin what the pipeline computes today.** Three fixture
deals run end-to-end through the real `engine.run_analysis` — no mocks, no
stubbed scenario dicts — and every number a user could see is snapshotted to
JSON. Each subsequent item-T change must either reproduce its snapshot
byte-for-byte (a pure literal-to-config move, which is most of them) or
change it deliberately, with the delta enumerated in the PR.

This is item B's "costs at 0 reproduce every oracle" discipline applied to
the whole pipeline instead of one module.

## Why the snapshots are committed, not generated on the fly

A snapshot the test regenerates when it fails is not a test. These files are
checked in, and `--snapshot-update` is deliberately NOT a pytest flag here:
updating one means editing the JSON in the same commit as the code change,
so the diff shows the reviewer exactly which numbers moved. That is the
whole point — the artifact under review IS the behavioural delta.

## What is deliberately NOT pinned

Anything that legitimately varies run to run: file paths, the analysis begin
date the XLSM writer derives from `datetime.now()`, and the template-writer
error on machines without the gitignored `template_uw.xlsm`. `_scrub`
removes them. Pinning those would produce a test that fails on a Tuesday.
"""

import json
import math
from pathlib import Path

import pytest

from engine import AnalysisResult, run_analysis
from extract.parser import CIMData, FinancialLine, UnitType

SNAPSHOTS = Path(__file__).parent / "snapshots"

# Rounding for every float that reaches a snapshot. Enough digits that a
# real change in a rate or a dollar shows up; few enough that platform
# float noise does not. An IRR moving in the 9th decimal is not a finding.
_DP = 6


# ── Fixture deals ────────────────────────────────────────────────────
# Three shapes, chosen because item T's changes bite differently in each:
# the stabilized deal exercises the static DCF and the benchmark bands;
# the value-add deal exercises the monthly engine, the occupancy-target
# policy and the renovation schedule; the thin deal exercises every
# fallback the item is about to make loud.


def _units(rows):
    """`rate` is monthly rent per UNIT, matching `UnitType.rate`."""
    return [UnitType(size_label=lbl, count=n, sf=sf, rate=rate,
                     climate_controlled=cc)
            for lbl, n, sf, rate, cc in rows]


def _expenses(pairs):
    """`t12` is the trailing-12 actual `analysis.financials` reads."""
    return [FinancialLine(label=label, t12=amount) for label, amount in pairs]


def stabilized_deal() -> CIMData:
    """A clean, fully-let asset. No value-add trigger, no missing inputs."""
    return CIMData(
        property_name="Characterization Stabilized",
        address="1 Stabilized Way", city="Abilene", state="TX",
        msa="Abilene", year_built=2015, acreage=4.5,
        nrsf=60_000, total_units=480, cc_sf=27_000, non_cc_sf=33_000,
        cc_pct=0.45, ss_driveup_sf=33_000, ss_enclosed_sf=27_000,
        physical_occupancy=0.93, economic_occupancy=0.90,
        asking_price=9_000_000, price_per_sf=150.0,
        population_3mi=78_000, median_hhi_3mi=61_500,
        competitive_supply_sf_3mi=310_000, pipeline_supply_sf_3mi=0,
        unit_mix=_units([("5x10", 160, 50, 62.0, False),
                         ("10x10", 200, 100, 108.0, False),
                         ("10x15", 80, 150, 152.0, True),
                         ("10x20", 40, 200, 189.0, True)]),
        ttm_gpr=1_180_000, ttm_egr=1_062_000, ttm_total_revenue=1_098_000,
        ttm_total_expenses=428_000, ttm_noi=670_000, cim_yr1_noi=690_000,
        other_income=36_000, in_place_avg_rent_psf=1.47,
        market_rent_psf=1.52, mgmt_fee_pct=0.05,
        # Lines SUM to ttm_total_expenses. An inconsistent fixture would
        # pin the check register's own complaint as if it were normal.
        expense_lines=_expenses([
            ("Property Taxes", 170_000), ("Insurance", 16_000),
            ("Utilities", 9_000), ("Repairs & Maintenance", 24_000),
            ("Advertising", 8_000), ("Payroll", 126_000),
            ("General & Administrative", 22_000), ("Management Fee", 53_000),
        ]),
    )


def value_add_deal() -> CIMData:
    """Mismanaged: high physical, low economic, rents under market. This is
    the profile the fund targets, so it is the one whose numbers must not
    drift."""
    return CIMData(
        property_name="Characterization Value Add",
        address="2 Upside Blvd", city="Waco", state="TX",
        msa="Waco", year_built=2004, acreage=5.2,
        nrsf=52_000, total_units=410, cc_sf=15_600, non_cc_sf=36_400,
        cc_pct=0.30, ss_driveup_sf=36_400, ss_enclosed_sf=15_600,
        physical_occupancy=0.91, economic_occupancy=0.74,
        asking_price=6_400_000, price_per_sf=123.08,
        population_3mi=64_000, median_hhi_3mi=52_000,
        competitive_supply_sf_3mi=280_000, pipeline_supply_sf_3mi=40_000,
        unit_mix=_units([("5x10", 140, 50, 48.0, False),
                         ("10x10", 170, 100, 86.0, False),
                         ("10x15", 60, 150, 121.0, True),
                         ("10x20", 40, 200, 158.0, True)]),
        ttm_gpr=880_000, ttm_egr=651_000, ttm_total_revenue=672_000,
        ttm_total_expenses=318_000, ttm_noi=354_000, cim_yr1_noi=402_000,
        other_income=21_000, in_place_avg_rent_psf=1.08,
        market_rent_psf=1.28, mgmt_fee_pct=0.06,
        expense_lines=_expenses([
            ("Property Taxes", 128_000), ("Insurance", 12_000),
            ("Utilities", 7_000), ("Repairs & Maintenance", 21_000),
            ("Advertising", 5_000), ("Payroll", 87_000),
            ("General & Administrative", 19_000), ("Management Fee", 39_000),
        ]),
    )


def thin_deal() -> CIMData:
    """An early-look CIM with the fields brokers routinely omit: no
    economic occupancy, no market rent, no demographics, no expense
    detail. Every silent fallback item T is about to make loud fires on
    this one, which is exactly why it is pinned BEFORE they change."""
    return CIMData(
        property_name="Characterization Thin",
        address="3 Sparse Rd", city="Tyler", state="TX",
        year_built=1998, nrsf=40_000, total_units=320,
        physical_occupancy=0.88,
        asking_price=4_200_000, price_per_sf=105.0,
        unit_mix=_units([("10x10", 200, 100, 74.0, False),
                         ("10x20", 120, 200, 132.0, False)]),
        ttm_total_revenue=486_000, ttm_total_expenses=214_000,
        ttm_noi=272_000,
    )


DEALS = {"stabilized": stabilized_deal,
         "value_add": value_add_deal,
         "thin": thin_deal}


# ── Snapshot shaping ─────────────────────────────────────────────────

def _round(value):
    """Recursively round floats; leave everything else alone.

    NaN survives as the string "NaN" rather than failing JSON round-trip —
    and it is worth pinning, because a scenario IRR going NaN is a real
    regression that a silently-dropped key would hide.
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return round(value, _DP)
    if isinstance(value, dict):
        return {k: _round(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round(v) for v in value]
    return value


def _scrub(errors):
    """Drop the errors that are environmental rather than behavioural.

    The XLSM template is gitignored, so `generate_template` fails on any
    machine that does not happen to have it — including CI. Pinning that
    string would make the snapshot depend on a file the repo cannot carry.
    """
    return sorted(e.split(":")[0] for e in errors
                  if "Template not found" not in e
                  and "Template generation failed" not in e)


def _scenario(scen: dict) -> dict:
    """The scenario fields a user actually sees. Deliberately explicit
    rather than `dict(scen)`: a new key appearing in the projection should
    not silently enter the snapshot without someone choosing to pin it."""
    return {k: _round(scen.get(k)) for k in (
        "irr", "moic", "yield_on_cost", "entry_cap", "exit_cap",
        "requested_exit_cap", "exit_cap_coerced", "total_basis",
        "exit_value", "noi_projection", "revenue_projection",
        "expense_projection", "hold_years")}


def snapshot_of(result: AnalysisResult) -> dict:
    """Everything item T could plausibly move, and nothing that varies."""
    return {
        "adjusted_noi": _round(result.adjusted_noi),
        "expense_ratio": _round(result.expense_ratio),
        "gates": [{"gate": g.get("gate"), "name": g.get("name"),
                   "threshold": str(g.get("threshold")),
                   "actual": str(g.get("actual")), "result": g.get("result")}
                  for g in result.gate_results],
        "gate_summary": {k: _round(v) for k, v in result.gate_summary.items()
                         if k != "failed_gates" and k != "tbd_gates"},
        "scenarios": {name: _scenario(s)
                      for name, s in sorted(result.scenario_results.items())},
        "sensitivity": _round(result.sensitivity.get("irr_grid")),
        "max_offer": {k: _round(result.max_offer.get(k))
                      for k in ("max_price", "target_irr", "achieved_irr",
                                "total_basis")},
        "va_max_offer": {k: _round(result.va_max_offer.get(k))
                         for k in ("max_price", "target_irr", "achieved_irr")},
        "sources_uses": {k: _round(result.sources_uses.get(k))
                         for k in ("total_uses", "total_sources", "total_equity",
                                   "senior_debt", "gp_equity", "lp_equity",
                                   "ltv", "balanced")},
        "debt": {k: _round(result.debt.get(k))
                 for k in ("loan", "binding_constraint", "dscr_year_1",
                           "origination_fee", "financing_costs")},
        "levered": {name: {k: _round(lev.get(k))
                           for k in ("lp_net_irr", "lp_moic", "am_fee_total",
                                     "gp_promote")}
                    for name, lev in sorted((result.levered or {}).items())},
        "value_add": {
            "estimated_noi_uplift": _round(
                (result.value_add or {}).get("estimated_noi_uplift")),
            "revenue_categories": [op.get("category") for op in
                                   (result.value_add or {}).get(
                                       "revenue_opportunities", [])],
            "expense_categories": [op.get("category") for op in
                                   (result.value_add or {}).get(
                                       "expense_opportunities", [])],
        },
        "va_results": {name: {k: _round(v.get(k))
                              for k in ("irr", "moic", "yield_on_cost",
                                        "months_to_stabilize",
                                        "current_occupancy", "target_occupancy",
                                        "in_place_rent_psf", "target_rent_psf",
                                        "market_rent_psf", "stabilized_noi")}
                       for name, v in sorted((result.va_results or {}).items())},
        # LISTS, not tuples: JSON has no tuple, so a tuple here compares
        # unequal to its own round-tripped self and every run looks like a
        # regression.
        "checks": sorted([c.get("id"), c.get("severity"), c.get("status")]
                         for c in (result.checks or [])),
        "check_summary": {k: _round(v) for k, v in
                          (result.check_summary or {}).items()},
        "risks": sorted([r.get("severity"), r.get("risk")]
                        for r in (result.risk_analysis or {}).get("risks", [])),
        "errors": _scrub(result.errors),
    }


@pytest.fixture(autouse=True)
def isolated_comp_db(tmp_path, monkeypatch):
    """Point the comp database at a throwaway file for every test here.

    Not hygiene — WITHOUT it these snapshots are not reproducible, and
    finding out why was the first thing this harness did. `engine.py`
    saves every completed run into the comp DB, and
    `analysis.financials._get_benchmarks` READS that same DB, switching
    from national benchmarks to comp-derived ones once
    `COMP_DB_MIN_COMPS` similar properties exist. So a run feeds the next
    run's assumptions: the thin fixture's adjusted NOI moved 368,395 ->
    305,595 between two identical invocations, purely because the first
    invocation had added itself as a comp.

    That is a real property of the product, not of the test — the same
    deal analysed before and after a few others prices differently, and
    nothing on any surface says so. Recorded for item T; the harness
    isolates so it can measure everything else.

    `data.comp_db` binds `COMP_DB_PATH` at import, so patching
    `config.COMP_DB_PATH` does not reach it — the same frozen-import
    defect item T already lists for `model.solver`'s target IRR.
    """
    import data.comp_db as comp_db_module
    monkeypatch.setattr(comp_db_module, "COMP_DB_PATH",
                        str(tmp_path / "comps.db"))


def run_deal(name: str, tmp_path) -> dict:
    result = AnalysisResult(pdf_path=f"{name}.pdf", cim_data=DEALS[name]())
    run_analysis(result, output_dir=str(tmp_path))
    return snapshot_of(result)


# ── The gate ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(DEALS))
def test_pipeline_output_matches_its_snapshot(name, tmp_path):
    """Item T's safety net.

    A failure here is not automatically a bug — it means a number moved.
    Decide which: if the change was meant to be behaviour-preserving (a
    literal moving to config), it is a bug and the code is wrong. If the
    change was deliberate, update the JSON IN THE SAME COMMIT so the diff
    shows the reviewer exactly which outputs moved and by how much.
    """
    path = SNAPSHOTS / f"{name}.json"
    actual = run_deal(name, tmp_path)

    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
        pytest.fail(f"wrote a new snapshot at {path} — commit it, and check "
                    f"the numbers are what you expect before you do")

    expected = json.loads(path.read_text())
    if actual == expected:
        return

    diffs = []
    for key in sorted(set(expected) | set(actual)):
        if expected.get(key) != actual.get(key):
            diffs.append(f"  {key}:\n    was: {expected.get(key)}\n"
                         f"    now: {actual.get(key)}")
    pytest.fail(f"{name} deal moved:\n" + "\n".join(diffs))


def test_the_fixtures_exercise_the_paths_they_claim_to():
    """A characterization suite whose fixtures all take the same branch
    pins one path three times. These assertions are what make the three
    deals actually different."""
    stab, va, thin = stabilized_deal(), value_add_deal(), thin_deal()

    # The mismanagement spread is the value-add trigger; the stabilized
    # deal must NOT have it, or both fixtures pin the same engine.
    assert (stab.physical_occupancy - stab.economic_occupancy) < 0.10
    assert (va.physical_occupancy - va.economic_occupancy) >= 0.10
    # The thin deal must actually be thin, or it pins no fallback at all.
    assert thin.economic_occupancy is None
    assert thin.market_rent_psf is None
    assert thin.population_3mi is None
    assert not thin.expense_lines

    # A fixture whose expense lines contradict its stated total pins the
    # check register's complaint instead of the pipeline's arithmetic.
    for deal in (stab, va):
        assert sum(line.t12 for line in deal.expense_lines) == \
            deal.ttm_total_expenses, deal.property_name
