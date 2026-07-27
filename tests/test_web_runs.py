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
