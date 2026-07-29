"""Phase 3: upload, background extract, assumptions editor tests."""
import datetime
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
    from engine import AnalysisResult

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
    # Explicitly older: two bare now() calls can land on the same
    # microsecond, making the stale stamp match the retry's CAS.
    old_stamp = timezone.now() - datetime.timedelta(seconds=5)
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


def _pdf(name="expo.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 fake", content_type="application/pdf")


@pytest.mark.django_db
def test_analyze_requires_login(client):
    resp = client.get("/analyze/")
    assert resp.status_code == 302
    assert resp.url.startswith("/accounts/login/")


@pytest.mark.django_db
def test_upload_creates_deal_and_extracts(client, operator, deals_dir, fake_extract):
    from webapp.models import Deal
    resp = client.post("/analyze/", {"cim": _pdf()})
    deal = Deal.objects.get()
    assert resp.status_code == 302
    assert resp.url == f"/deals/{deal.pk}/assumptions/"
    assert deal.deal_id == "expo"
    assert deal.input_files == ["expo.pdf"]
    assert os.path.isfile(os.path.join(deal.deal_dir, "inputs", "expo.pdf"))
    deal.refresh_from_db()
    assert deal.extract_status == "done"  # sync mode
    assert deal.property_name == "Expo Storage"


@pytest.mark.django_db
def test_upload_saves_optional_files(client, operator, deals_dir, fake_extract):
    from webapp.models import Deal
    client.post("/analyze/", {
        "cim": _pdf(),
        "rent_roll": SimpleUploadedFile("rr.xlsx", b"fake",
                                        content_type="application/octet-stream"),
        "financials": SimpleUploadedFile("fin.csv", b"a,b", content_type="text/csv"),
    })
    deal = Deal.objects.get()
    assert deal.input_files == ["expo.pdf", "rr.xlsx", "fin.csv"]
    assert os.path.isfile(os.path.join(deal.deal_dir, "inputs", "rr.xlsx"))


@pytest.mark.django_db
def test_upload_validation(client, operator, deals_dir):
    from webapp.models import Deal
    resp = client.post("/analyze/", {})
    assert resp.status_code == 422
    resp = client.post("/analyze/", {"cim": SimpleUploadedFile("x.exe", b"z")})
    assert resp.status_code == 422
    resp = client.post("/analyze/", {"cim": _pdf(),
                                     "rent_roll": SimpleUploadedFile("x.exe", b"z")})
    assert resp.status_code == 422
    assert Deal.objects.count() == 0


@pytest.mark.django_db
def test_upload_slug_collision_gets_v2(client, operator, deals_dir, fake_extract,
                                       monkeypatch):
    from webapp import services
    from webapp.models import Deal
    monkeypatch.setattr(services, "_comp_db_dupes", lambda filename: [])
    client.post("/analyze/", {"cim": _pdf()})
    resp = client.post("/analyze/", {"cim": _pdf()})  # same filename again
    assert Deal.objects.count() == 2
    assert set(Deal.objects.values_list("deal_id", flat=True)) == {"expo", "expo-v2"}
    # second upload matched the first deal's input file → dupe confirm page
    assert resp.status_code == 200
    assert b"already exist" in resp.content


@pytest.mark.django_db
def test_comp_db_dupes_surface(client, operator, deals_dir, fake_extract, monkeypatch):
    from webapp import services
    monkeypatch.setattr(services, "_comp_db_dupes", lambda filename: [{
        "property_name": "Expo Storage", "city": "Belton", "state": "TX",
        "analysis_date": "2026-06-01", "pdf_filename": filename,
        "match_type": "filename",
    }])
    resp = client.post("/analyze/", {"cim": _pdf()})
    assert resp.status_code == 200
    assert b"Expo Storage" in resp.content
    assert b"Discard this upload" in resp.content


@pytest.mark.django_db
def test_discard_deletes_upload(client, operator, deals_dir, fake_extract):
    from webapp.models import Deal
    client.post("/analyze/", {"cim": _pdf()})
    deal = Deal.objects.get()
    folder = deal.deal_dir
    resp = client.post(f"/deals/{deal.pk}/discard/")
    assert resp.status_code == 302
    assert Deal.objects.count() == 0
    assert not os.path.isdir(folder)


@pytest.mark.django_db
def test_discard_refuses_imported_and_analyzed(client, operator, deals_dir):
    from webapp.models import Deal
    imported = Deal.objects.create(deal_id="legacy", property_name="Legacy",
                                   deal_dir=str(deals_dir / "legacy"))
    analyzed = Deal.objects.create(deal_id="done-deal", property_name="Done",
                                   deal_dir=str(deals_dir / "done-deal"),
                                   extract_status="done", memo_filename="memo.docx")
    for d in (imported, analyzed):
        os.makedirs(d.deal_dir, exist_ok=True)
        client.post(f"/deals/{d.pk}/discard/")
        assert Deal.objects.filter(pk=d.pk).exists()
        assert os.path.isdir(d.deal_dir)


def _extracted_deal(client, deals_dir, fake_extract):
    from webapp.models import Deal
    client.post("/analyze/", {"cim": _pdf()})
    deal = Deal.objects.latest("pk")
    deal.refresh_from_db()
    assert deal.extract_status == "done"
    return deal


@pytest.mark.django_db
def test_assumptions_get_renders_snapshot(client, operator, deals_dir, fake_extract):
    deal = _extracted_deal(client, deals_dir, fake_extract)
    resp = client.get(f"/deals/{deal.pk}/assumptions/")
    assert resp.status_code == 200
    content = resp.content.decode()
    assert 'value="Expo Storage"' in content          # property name prefilled
    assert 'value="92' in content                     # physical_occupancy 0.92 → 92
    assert 'value="3500000' in content                # asking price
    assert "10x10" in content                         # unit mix row rendered
    assert 'name="um_label"' in content


@pytest.mark.django_db
def test_assumptions_get_flags_missing_required(client, operator, deals_dir,
                                                fake_extract):
    deal = _extracted_deal(client, deals_dir, fake_extract)
    deal.cim_json["ttm_egr"] = None
    deal.extraction_report["missing"] = ["ttm_egr", "msa"]
    deal.save()
    resp = client.get(f"/deals/{deal.pk}/assumptions/")
    content = resp.content.decode()
    assert "required-flag" in content  # marker class on the ttm_egr label


@pytest.mark.django_db
def test_unit_mix_row_endpoint(client, operator):
    resp = client.get("/deals/unit-mix-row/")
    assert resp.status_code == 200
    assert b'name="um_label"' in resp.content


@pytest.mark.django_db
def test_deal_list_links_deal_detail(client, operator, deals_dir, fake_extract):
    deal = _extracted_deal(client, deals_dir, fake_extract)
    resp = client.get("/deals/")
    assert f"/deals/{deal.pk}/".encode() in resp.content


def _post_assumptions(client, deal, extra=None):
    """POST the form as rendered (initial values), with optional edits."""
    from webapp import forms as f
    initial = f.build_initial(deal)
    data = {k: ("" if v is None else v) for k, v in initial.items()}
    rows = f.unit_mix_rows(deal)
    data["um_label"] = [r["size_label"] for r in rows]
    data["um_count"] = [str(r["count"]) for r in rows]
    data["um_sf"] = [str(r["sf"]) for r in rows]
    data["um_rate"] = [str(r["rate"]) for r in rows]
    data["um_cc"] = ["1" if r["climate_controlled"] else "0" for r in rows]
    data.update(extra or {})
    return client.post(f"/deals/{deal.pk}/assumptions/", data)


@pytest.mark.django_db
def test_save_unchanged_form_stores_no_overrides(client, operator, deals_dir,
                                                 fake_extract):
    deal = _extracted_deal(client, deals_dir, fake_extract)
    resp = _post_assumptions(client, deal)
    assert resp.status_code == 302
    deal.refresh_from_db()
    assert deal.assumption_overrides == {}


@pytest.mark.django_db
def test_save_cim_delta_and_pct_conversion(client, operator, deals_dir, fake_extract):
    deal = _extracted_deal(client, deals_dir, fake_extract)
    _post_assumptions(client, deal, {"asking_price": "3200000",
                                     "physical_occupancy": "85"})
    deal.refresh_from_db()
    cim_o = deal.assumption_overrides["cim_overrides"]
    assert cim_o == {"asking_price": 3200000.0, "physical_occupancy": 0.85}


@pytest.mark.django_db
def test_save_scenario_delta_stores_full_section(client, operator, deals_dir,
                                                 fake_extract):
    import config as cfg
    deal = _extracted_deal(client, deals_dir, fake_extract)
    _post_assumptions(client, deal, {"scen_bear_exit_cap": "9"})
    deal.refresh_from_db()
    scen = deal.assumption_overrides["scenario_overrides"]
    assert scen["bear"]["exit_cap"] == 0.09
    # untouched values persisted alongside (auditability)
    assert scen["base"]["exit_cap"] == cfg.SCENARIO_DEFAULTS["base"]["exit_cap"]
    assert "va_scenario_overrides" not in deal.assumption_overrides


@pytest.mark.django_db
def test_save_unit_mix_edit(client, operator, deals_dir, fake_extract):
    deal = _extracted_deal(client, deals_dir, fake_extract)
    _post_assumptions(client, deal, {
        "um_label": ["10x10", "10x20", ""],
        "um_count": ["120", "50", "0"],          # changed 100 → 120; blank row dropped
        "um_sf": ["100", "200", ""],
        "um_rate": ["95", "165", ""],
        "um_cc": ["0", "1", "0"],
    })
    deal.refresh_from_db()
    mix = deal.assumption_overrides["cim_overrides"]["unit_mix"]
    assert len(mix) == 2
    assert mix[0]["count"] == 120
    assert mix[1]["climate_controlled"] is True


@pytest.mark.django_db
def test_save_rc_and_solver_deltas(client, operator, deals_dir, fake_extract):
    deal = _extracted_deal(client, deals_dir, fake_extract)
    _post_assumptions(client, deal, {"rc_ss_driveup_per_sf_low": "60",
                                     "solver_target_irr": "12"})
    deal.refresh_from_db()
    o = deal.assumption_overrides
    assert o["replacement_cost_overrides"] == {"ss_driveup_per_sf": [60.0, 85.0]}
    assert o["solver_target_irr"] == 0.12


@pytest.mark.django_db
def test_saved_values_render_on_next_get(client, operator, deals_dir, fake_extract):
    deal = _extracted_deal(client, deals_dir, fake_extract)
    _post_assumptions(client, deal, {"physical_occupancy": "85"})
    resp = client.get(f"/deals/{deal.pk}/assumptions/")
    assert b'value="85' in resp.content


def test_in_place_rent_and_gap(mock_cim_data):
    from analysis.rent_analysis import analyze_rents
    from extract.parser import UnitType

    mock_cim_data.unit_mix = [
        UnitType(size_label="10x10", sf=100.0, count=100, rate=95.0),
        UnitType(size_label="10x20", sf=200.0, count=50, rate=160.0),
    ]
    mock_cim_data.market_rent_psf = 1.20   # street rate
    r = analyze_rents(mock_cim_data)
    # (95*100 + 160*50) / (100*100 + 200*50) = 17,500 / 20,000 = 0.875
    assert r["in_place_avg_rent_psf"] == 0.88
    assert r["in_place_rent_source"] == "derived"
    assert r["rent_gap_pct"] == round((1.20 - 0.88) / 1.20, 4)

    mock_cim_data.in_place_avg_rent_psf = 1.00   # analyst override wins
    r2 = analyze_rents(mock_cim_data)
    assert r2["in_place_avg_rent_psf"] == 1.00
    assert r2["in_place_rent_source"] == "override"


def test_in_place_rent_none_when_no_unit_mix_or_override(mock_cim_data):
    """Empty unit mix (early-return branch) must not KeyError or divide by zero."""
    from analysis.rent_analysis import analyze_rents

    mock_cim_data.unit_mix = []
    r = analyze_rents(mock_cim_data)
    assert r["in_place_avg_rent_psf"] is None
    assert r["in_place_rent_source"] is None
    assert r["rent_gap_pct"] is None


def test_parse_unit_mix_logs_dropped_rows(caplog):
    """A non-numeric row is skipped, but never silently (audit check 3)."""
    from django.http import QueryDict

    from webapp.forms import parse_unit_mix
    post = QueryDict(mutable=True)
    post.setlist("um_label", ["10x10", "bad"])
    post.setlist("um_count", ["100", "not-a-number"])
    post.setlist("um_sf", ["100", "100"])
    post.setlist("um_rate", ["95", "95"])
    post.setlist("um_cc", ["0", "0"])
    with caplog.at_level("WARNING", logger="cim_analyst.web"):
        rows = parse_unit_mix(post)
    assert len(rows) == 1
    assert "unit-mix row dropped" in caplog.text


# ── T6: live-preview endpoint (server-computed htmx partial) ───────────

@pytest.mark.django_db
def test_assumptions_preview_contract(client, django_user_model, settings, tmp_path):
    from webapp.models import AnalysisRun, Deal
    settings.CIM_DEALS_DIR = str(tmp_path)
    user = django_user_model.objects.create_user(username="op", password="x")
    client.force_login(user)
    deal = Deal.objects.create(
        deal_id="pv", property_name="PV",
        cim_json={"property_name": "PV", "state": "TX", "nrsf": 50_000.0,
                  "population_3mi": 75_000, "ttm_egr": 550_000.0})
    runs_before = AnalysisRun.objects.count()

    resp = client.post(f"/deals/{deal.pk}/assumptions/preview/", {
        "competitive_supply_sf_3mi": "300000",
        "population_3mi": "75000",
        "ttm_total_revenue": "560000", "ttm_total_expenses": "220000",
        "ttm_noi": "340000",
    })
    assert resp.status_code == 200
    html = resp.content.decode()
    # (300k competitive + 0 pipeline + 50k subject) / 75k pop = 4.6667,
    # floatformat:1 renders "4.7" — NOT "7.0" (the sketch's number didn't
    # match its own stated inputs; fixed here to the true arithmetic).
    assert "4.7" in html
    assert 'id="model-strip"' in html
    assert 'id="noi-chip"' in html
    # preview must never write
    assert AnalysisRun.objects.count() == runs_before
    deal.refresh_from_db()
    # Deal.assumption_overrides defaults to {} (JSONField default=dict,
    # not nullable) — confirming it's still the untouched default is the
    # "never writes" check, not an `is None` comparison.
    assert deal.assumption_overrides == {}

    assert client.get(f"/deals/{deal.pk}/assumptions/preview/").status_code == 405
