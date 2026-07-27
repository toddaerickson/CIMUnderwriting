"""Phase 3: upload, background extract, assumptions editor tests."""
import json
import os

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile


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


@pytest.mark.django_db
def test_deal_extraction_defaults():
    from webapp.models import Deal
    d = Deal.objects.create(deal_id="x", property_name="X")
    assert d.extract_status == ""
    assert d.extract_warnings == []
    assert d.assumption_overrides == {}
    assert d.cim_json is None


def test_cim_dict_round_trip():
    from webapp.services import cim_from_dict, cim_to_dict
    cim = _sample_cim()
    restored = cim_from_dict(json.loads(json.dumps(cim_to_dict(cim))))
    assert restored.property_name == "Expo Storage"
    assert restored.nrsf == 45000.0
    assert len(restored.unit_mix) == 2
    assert restored.unit_mix[1].climate_controlled is True
    assert type(restored.unit_mix[0]).__name__ == "UnitType"


def test_cim_from_dict_ignores_unknown_keys():
    """Schema drift: a stored snapshot with a since-removed key must not crash."""
    from webapp.services import cim_from_dict, cim_to_dict
    d = cim_to_dict(_sample_cim())
    d["some_removed_field"] = 1
    assert cim_from_dict(d).property_name == "Expo Storage"


@pytest.fixture
def fake_extract(monkeypatch):
    from gui.engine import AnalysisResult

    def _fake(pdf_path, cim_overrides=None, progress=None):
        cim = _sample_cim()
        r = AnalysisResult(pdf_path=pdf_path)
        r.cim_data = cim
        r.extraction_report = cim.extraction_report()
        r.errors = ["Enrichment skipped: test"]
        return r

    monkeypatch.setattr("webapp.services.extract_pdf_data", _fake)
    return _fake


def _make_upload_deal(deals_dir, slug="expo-cim"):
    from webapp.models import Deal
    folder = deals_dir / slug
    (folder / "inputs").mkdir(parents=True)
    (folder / "inputs" / "expo.pdf").write_bytes(b"%PDF-1.4 fake")
    return Deal.objects.create(deal_id=slug, property_name="expo",
                               deal_dir=str(folder), input_files=["expo.pdf"],
                               extract_status="pending")


@pytest.mark.django_db
def test_start_extract_success(deals_dir, fake_extract):
    from webapp import services
    deal = _make_upload_deal(deals_dir)
    services.start_extract(deal)
    deal.refresh_from_db()
    assert deal.extract_status == "done"
    assert deal.cim_json["property_name"] == "Expo Storage"
    assert deal.extraction_report["populated"] > 0
    assert deal.extract_warnings == ["Enrichment skipped: test"]
    # extraction refreshes display metadata on the row
    assert deal.property_name == "Expo Storage"
    assert deal.state == "TX"
    assert deal.asset_type != ""
    assert deal.nrsf == 45000.0


@pytest.mark.django_db
def test_start_extract_failure_records_error(deals_dir, monkeypatch):
    from webapp import services

    def boom(pdf_path, cim_overrides=None, progress=None):
        raise RuntimeError("pdf is garbage")

    monkeypatch.setattr("webapp.services.extract_pdf_data", boom)
    deal = _make_upload_deal(deals_dir)
    services.start_extract(deal)
    deal.refresh_from_db()
    assert deal.extract_status == "failed"
    assert "pdf is garbage" in deal.extract_error


@pytest.mark.django_db
def test_stale_extract_worker_is_dropped(deals_dir, fake_extract):
    """A worker holding an old stamp must not overwrite a retried extract."""
    from django.utils import timezone

    from webapp import services
    deal = _make_upload_deal(deals_dir)
    old_stamp = timezone.now()
    deal.extract_status = "running"
    deal.extract_requested_at = timezone.now()  # newer stamp = a retry happened
    deal.save()
    services._extract_worker(deal.pk, os.path.join(deal.deal_dir, "inputs", "expo.pdf"),
                             old_stamp)
    deal.refresh_from_db()
    assert deal.extract_status == "running"  # stale write dropped
    assert deal.cim_json is None


@pytest.mark.django_db
def test_extract_status_running_polls(client, operator, deals_dir):
    from django.utils import timezone
    deal = _make_upload_deal(deals_dir)
    deal.extract_status = "running"
    deal.extract_requested_at = timezone.now()
    deal.save()
    resp = client.get(f"/deals/{deal.pk}/extract-status/")
    assert resp.status_code == 200
    assert b"hx-trigger" in resp.content  # keeps polling


@pytest.mark.django_db
def test_extract_status_done_redirects(client, operator, deals_dir):
    deal = _make_upload_deal(deals_dir)
    deal.extract_status = "done"
    deal.save()
    resp = client.get(f"/deals/{deal.pk}/extract-status/")
    assert resp.status_code == 200
    assert resp.headers["HX-Redirect"] == f"/deals/{deal.pk}/assumptions/"


@pytest.mark.django_db
def test_extract_status_failed_and_timeout_stop_polling(client, operator, deals_dir):
    import datetime

    from django.utils import timezone
    deal = _make_upload_deal(deals_dir)
    deal.extract_status = "failed"
    deal.extract_error = "boom"
    deal.save()
    resp = client.get(f"/deals/{deal.pk}/extract-status/")
    assert b"hx-trigger" not in resp.content
    assert b"Retry extraction" in resp.content
    # timeout: still "running" but stamp is too old
    deal.extract_status = "running"
    deal.extract_error = ""
    deal.extract_requested_at = timezone.now() - datetime.timedelta(seconds=999)
    deal.save()
    resp = client.get(f"/deals/{deal.pk}/extract-status/")
    assert b"hx-trigger" not in resp.content
    assert b"Retry extraction" in resp.content


@pytest.mark.django_db
def test_extract_retry_reruns(client, operator, deals_dir, fake_extract):
    deal = _make_upload_deal(deals_dir)
    deal.extract_status = "failed"
    deal.extract_error = "old error"
    deal.save()
    resp = client.post(f"/deals/{deal.pk}/extract-retry/")
    assert resp.status_code == 302
    deal.refresh_from_db()
    assert deal.extract_status == "done"  # sync mode ran inline
    assert deal.extract_error == ""


@pytest.mark.django_db
def test_assumptions_wait_and_unavailable(client, operator, deals_dir):
    from django.utils import timezone

    from webapp.models import Deal
    deal = _make_upload_deal(deals_dir)
    deal.extract_status = "running"
    deal.extract_requested_at = timezone.now()
    deal.save()
    resp = client.get(f"/deals/{deal.pk}/assumptions/")
    assert resp.status_code == 200
    assert b"Extracting" in resp.content
    imported = Deal.objects.create(deal_id="legacy", property_name="Legacy")
    resp = client.get(f"/deals/{imported.pk}/assumptions/")
    assert resp.status_code == 200
    assert b"no extraction snapshot" in resp.content.lower()
