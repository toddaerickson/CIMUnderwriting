"""Phase 4: AnalysisRun, background analysis, results pages, downloads."""
import json
import os

import pytest


@pytest.fixture
def operator(client, django_user_model):
    user = django_user_model.objects.create_user(username="op", password="x")
    client.force_login(user)
    return user


@pytest.fixture
def deals_dir(tmp_path, settings):
    d = tmp_path / "deals"
    d.mkdir()
    settings.CIM_DEALS_DIR = str(d)
    return d


def _sample_cim():
    from extract.parser import CIMData, UnitType
    return CIMData(
        property_name="Expo Storage", city="Belton", state="TX",
        nrsf=45000.0, total_units=350, physical_occupancy=0.92,
        economic_occupancy=0.78, asking_price=3_500_000.0,
        ttm_noi=250_000.0, ttm_egr=420_000.0, acreage=5.2,
        unit_mix=[UnitType(size_label="10x10", sf=100.0, count=100, rate=95.0),
                  UnitType(size_label="10x20", sf=200.0, count=50, rate=165.0,
                           climate_controlled=True)],
    )


def _make_extracted_deal(deals_dir, slug="expo"):
    """Deal with a completed extraction snapshot, ready to run."""
    from webapp.models import Deal
    from webapp.services import cim_to_dict
    folder = deals_dir / slug
    (folder / "inputs").mkdir(parents=True)
    (folder / "inputs" / "expo.pdf").write_bytes(b"%PDF-1.4 fake")
    cim = _sample_cim()
    return Deal.objects.create(
        deal_id=slug, property_name="Expo Storage", city="Belton", state="TX",
        deal_dir=str(folder), input_files=["expo.pdf"],
        extract_status="done", cim_json=cim_to_dict(cim),
        extraction_report=cim.extraction_report())


@pytest.mark.django_db
def test_analysis_run_defaults_and_ordering(deals_dir):
    from webapp.models import AnalysisRun
    deal = _make_extracted_deal(deals_dir)
    first = AnalysisRun.objects.create(deal=deal)
    second = AnalysisRun.objects.create(deal=deal)
    assert first.status == "running"
    assert first.result_json is None
    assert first.progress_total == 9
    assert deal.runs.first().pk == second.pk  # newest first


def test_json_safe_scrubs_nan_numpy_enum_tuple():
    import numpy as np
    from registry import ScenarioType
    from webapp.services import json_safe

    payload = {
        ScenarioType.BASE: {"irr": np.float64(0.123), "moic": float("nan")},
        "grid": [(np.float64(1.0), float("inf"))],
        "count": np.int64(7),
        "label": "ok",
        "scen": ScenarioType.BEAR,
    }
    out = json_safe(payload)
    # must round-trip through strict JSON (what Postgres JSONB enforces)
    restored = json.loads(json.dumps(out, allow_nan=False))
    assert restored["base"]["irr"] == pytest.approx(0.123)
    assert restored["base"]["moic"] is None
    assert restored["grid"] == [[1.0, None]]
    assert restored["count"] == 7
    assert restored["scen"] == "bear"


def test_json_safe_stringifies_unknown_objects():
    from webapp.services import json_safe

    class Weird:
        def __str__(self):
            return "weird"

    assert json_safe({"x": Weird()}) == {"x": "weird"}


def test_patched_replacement_cost_mutates_in_place_and_restores():
    # analysis.physical holds the dict OBJECT from import time — the
    # patch must be visible through that binding, then fully restored.
    from analysis.physical import REPLACEMENT_COST as bound
    from webapp.services import _patched_replacement_cost

    original = bound["ss_driveup_per_sf"]
    with _patched_replacement_cost({"ss_driveup_per_sf": [100, 120],
                                    "not_a_real_key": [1, 2]}):
        assert tuple(bound["ss_driveup_per_sf"]) == (100, 120)
        assert "not_a_real_key" not in bound
    assert bound["ss_driveup_per_sf"] == original


def test_patched_replacement_cost_restores_on_exception():
    from analysis.physical import REPLACEMENT_COST as bound
    from webapp.services import _patched_replacement_cost

    original = bound["ss_driveup_per_sf"]
    with pytest.raises(RuntimeError):
        with _patched_replacement_cost({"ss_driveup_per_sf": [100, 120]}):
            raise RuntimeError("boom")
    assert bound["ss_driveup_per_sf"] == original


def test_run_analysis_accepts_solver_target_irr():
    import inspect
    from gui.engine import run_analysis
    params = inspect.signature(run_analysis).parameters
    assert "solver_target_irr" in params
    assert params["solver_target_irr"].default is None


def test_solver_honors_target_irr():
    from model.solver import solve_max_price
    out = solve_max_price(adjusted_ttm_noi=250_000.0, capex=0,
                          target_irr=0.12, expense_ratio=0.40)
    assert out["converged"]
    assert out["achieved_irr"] == pytest.approx(0.12, abs=0.005)


def test_engine_end_to_end_with_overrides(tmp_path, monkeypatch):
    """Real pipeline (no PDF, no network): overrides reach the solver and
    the writers produce files. Comp DB redirected to a scratch path —
    data.comp_db binds COMP_DB_PATH at import, so patch that module's name."""
    monkeypatch.setattr("data.comp_db.COMP_DB_PATH", str(tmp_path / "comps.db"))
    from gui.engine import AnalysisResult, run_analysis
    from webapp.services import _patched_replacement_cost

    result = AnalysisResult(pdf_path=str(tmp_path / "expo.pdf"))
    result.cim_data = _sample_cim()
    with _patched_replacement_cost({"ss_driveup_per_sf": [100, 120]}):
        result = run_analysis(result, output_dir=str(tmp_path),
                              solver_target_irr=0.12)
    assert result.max_offer["achieved_irr"] == pytest.approx(0.12, abs=0.005)
    assert os.path.isfile(result.memo_path)
    assert os.path.isfile(result.excel_path)
    assert result.gate_summary["recommendation"]


@pytest.fixture
def fake_run(monkeypatch):
    """Stand-in for gui.engine.run_analysis: fills the result fields the
    worker consumes, writes the three output files, captures kwargs."""
    calls = {}

    def _fake(result, progress=None, output_dir=None, custom_scenarios=None,
              custom_va_scenarios=None, solver_target_irr=None):
        calls["cim_data"] = result.cim_data
        calls["output_dir"] = output_dir
        calls["custom_scenarios"] = custom_scenarios
        calls["custom_va_scenarios"] = custom_va_scenarios
        calls["solver_target_irr"] = solver_target_irr
        if progress:
            progress(9, 9, "Generating memo & model...")
        name = result.cim_data.property_name.replace(" ", "_")
        for attr, suffix in [("memo_path", "_memo.docx"),
                             ("excel_path", "_model.xlsx"),
                             ("template_path", "_uw.xlsm")]:
            path = os.path.join(output_dir, f"{name}{suffix}")
            with open(path, "wb") as f:
                f.write(b"fake-office-bytes")
            setattr(result, attr, path)
        result.gate_results = [
            {"gate": 1, "name": "Population (3-mi ≥ 50K)", "threshold": "≥ 50,000",
             "actual": "62,000", "result": "PASS", "note": "", "source": None},
            {"gate": 2, "name": "No unproven demand", "threshold": "phys ≥ 75%",
             "actual": "92%", "result": "PASS", "note": "", "source": None},
        ]
        result.gate_summary = {"passed": 2, "failed": 0, "tbd": 0, "total": 2,
                               "recommendation": "PURSUE",
                               "failed_gates": [], "tbd_gates": []}
        result.scenario_results = {
            "bear": {"irr": 0.06, "moic": 1.3, "yield_on_cost": 0.065},
            "base": {"irr": float("nan"), "moic": 1.6, "yield_on_cost": 0.075},
            "bull": {"irr": 0.14, "moic": 1.9, "yield_on_cost": 0.085},
        }
        result.sensitivity = {"prices": [3_325_000.0, 3_500_000.0, 3_675_000.0],
                              "exit_caps": [0.055, 0.06, 0.065],
                              "grid": [[0.11, 0.10, 0.09],
                                       [0.10, 0.09, 0.08],
                                       [0.09, 0.08, 0.07]]}
        result.va_results = {
            "base": {"irr": 0.13, "moic": 1.7, "yield_on_cost": 0.08,
                     "development_spread": 0.02, "stabilized_noi": 300_000.0}}
        result.max_offer = {"max_price": 3_100_000.0, "achieved_irr": 0.10,
                            "converged": True}
        result.va_max_offer = {"max_price": 3_300_000.0, "achieved_irr": 0.10,
                               "converged": True}
        result.financial_analysis = {
            "adjusted_ttm_noi": {"cim_ttm_noi": 250_000.0,
                                 "analyst_adjusted_noi": 230_000.0},
            "adjustments": ["Property tax adjusted to benchmark",
                            {"category": "Insurance", "flag": "understated"}],
            "expense_ratio_check": {"opex_revenue_ratio": 0.42},
        }
        result.risk_analysis = {"risks": [
            {"risk": "ECRI bridge", "severity": "HIGH",
             "detail": "Street rates falling", "mitigation": "Verify trend"}]}
        result.adjusted_noi = 230_000.0
        result.expense_ratio = 0.42
        result.errors = ["Template generation failed: test-only"]
        return result

    monkeypatch.setattr("webapp.services.run_analysis", _fake)
    return calls


def _start_run(deal):
    from webapp import services
    from webapp.models import AnalysisRun
    run = AnalysisRun.objects.create(deal=deal)
    services.start_analysis(run)
    run.refresh_from_db()
    return run


@pytest.mark.django_db
def test_worker_success_updates_run_and_deal(deals_dir, fake_run):
    deal = _make_extracted_deal(deals_dir)
    deal.assumption_overrides = {
        "cim_overrides": {"asking_price": 3_400_000.0},
        "scenario_overrides": {"base": {"exit_cap": 0.06}},
        "va_scenario_overrides": {"base": {"target_occupancy": 0.9}},
        "replacement_cost_overrides": {"ss_driveup_per_sf": [100, 120]},
        "solver_target_irr": 0.12,
    }
    deal.save()
    run = _start_run(deal)

    assert run.status == "done"
    assert run.finished_at is not None
    assert run.memo_filename == "Expo_Storage_memo.docx"
    assert run.excel_filename == "Expo_Storage_model.xlsx"
    assert run.template_filename == "Expo_Storage_uw.xlsm"
    # NaN scrubbed for Postgres JSONB
    assert run.result_json["scenario_results"]["base"]["irr"] is None
    assert run.result_json["gate_summary"]["recommendation"] == "PURSUE"
    assert run.result_json["errors"] == ["Template generation failed: test-only"]
    # overrides all reached the engine
    assert fake_run["cim_data"].asking_price == 3_400_000.0
    assert fake_run["custom_scenarios"] == {"base": {"exit_cap": 0.06}}
    assert fake_run["custom_va_scenarios"] == {"base": {"target_occupancy": 0.9}}
    assert fake_run["solver_target_irr"] == 0.12
    assert fake_run["output_dir"] == deal.deal_dir

    deal.refresh_from_db()
    assert deal.recommendation == "PURSUE"
    assert deal.estimated_fair_value == 3_300_000.0  # VA max offer preferred
    assert deal.analysis_date is not None
    assert deal.memo_filename == "Expo_Storage_memo.docx"
    assert deal.excel_filename == "Expo_Storage_model.xlsx"


@pytest.mark.django_db
def test_worker_writes_meta_with_row_deal_id(deals_dir, fake_run):
    # Slug came from the FILENAME (Phase 3 decision #2); property name
    # differs. deal_meta.json must carry the row's slug so import_deals
    # round-trips onto the same row instead of forking a duplicate.
    deal = _make_extracted_deal(deals_dir, slug="expo-cim-v2")
    _start_run(deal)
    with open(os.path.join(deal.deal_dir, "deal_meta.json")) as f:
        meta = json.load(f)
    assert meta["deal_id"] == "expo-cim-v2"
    assert meta["property_name"] == "Expo Storage"
    assert meta["memo_path"] == "Expo_Storage_memo.docx"
    assert meta["input_files"] == ["expo.pdf"]


@pytest.mark.django_db
def test_worker_failure_records_error(deals_dir, monkeypatch):
    def boom(result, **kwargs):
        raise RuntimeError("solver exploded")

    monkeypatch.setattr("webapp.services.run_analysis", boom)
    deal = _make_extracted_deal(deals_dir)
    run = _start_run(deal)
    assert run.status == "failed"
    assert "solver exploded" in run.error
    deal.refresh_from_db()
    assert deal.recommendation == "N/A"  # deal row untouched on failure


@pytest.mark.django_db
def test_worker_progress_updates_row(deals_dir, fake_run):
    deal = _make_extracted_deal(deals_dir)
    run = _start_run(deal)
    assert run.progress_step == 9
    assert run.progress_msg == "Generating memo & model..."
