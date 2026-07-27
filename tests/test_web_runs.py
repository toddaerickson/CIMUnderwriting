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
