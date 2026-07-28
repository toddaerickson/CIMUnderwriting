"""Deal model + import command + list view tests."""
import datetime
import json

import pytest


@pytest.mark.django_db
def test_deal_str_and_defaults():
    from webapp.models import Deal

    d = Deal.objects.create(deal_id="test-storage", property_name="Test Storage")
    assert str(d) == "Test Storage"
    assert d.recommendation == "N/A"
    assert d.input_files == []


@pytest.mark.django_db
def test_import_deals_idempotent(tmp_path, settings):
    from django.core.management import call_command
    from webapp.models import Deal

    meta = {
        "deal_id": "expo-storage", "property_name": "Expo Storage",
        "city": "Belton", "state": "TX", "asset_type": "Boat & RV",
        "nrsf": 45000.0, "acreage": 5.2, "asking_price": 3_500_000,
        "estimated_fair_value": 3_100_000, "recommendation": "PURSUE",
        "analysis_date": "2026-07-01", "memo_path": "memo.docx",
        "excel_path": "model.xlsx", "input_files": ["om.pdf"],
    }
    folder = tmp_path / "expo-storage"
    folder.mkdir()
    (folder / "deal_meta.json").write_text(json.dumps(meta))
    settings.CIM_DEALS_DIR = str(tmp_path)

    call_command("import_deals")
    call_command("import_deals")  # idempotent

    assert Deal.objects.count() == 1
    d = Deal.objects.get(deal_id="expo-storage")
    assert d.state == "TX"
    assert d.analysis_date.isoformat() == "2026-07-01"
    assert d.deal_dir == str(folder)


@pytest.mark.django_db
def test_import_deals_skips_malformed_folder(tmp_path, settings):
    from django.core.management import call_command
    from webapp.models import Deal

    bad_meta = {
        "deal_id": "aaa-bad", "property_name": "Bad Storage",
        "nrsf": [1, 2, 3],  # non-numeric FloatField value -> TypeError on save
    }
    good_meta = {
        "deal_id": "zzz-good", "property_name": "Good Storage",
        "city": "Waco", "state": "TX", "asset_type": "Self Storage",
        "nrsf": 30000.0, "acreage": 3.0, "asking_price": 2_000_000,
        "estimated_fair_value": 1_900_000, "recommendation": "PASS",
        "analysis_date": "2026-07-05", "memo_path": "memo.docx",
        "excel_path": "model.xlsx", "input_files": ["om.pdf"],
    }
    bad_folder = tmp_path / "aaa-bad"
    bad_folder.mkdir()
    (bad_folder / "deal_meta.json").write_text(json.dumps(bad_meta))
    good_folder = tmp_path / "zzz-good"
    good_folder.mkdir()
    (good_folder / "deal_meta.json").write_text(json.dumps(good_meta))
    settings.CIM_DEALS_DIR = str(tmp_path)

    call_command("import_deals")  # must not raise

    assert Deal.objects.count() == 1
    d = Deal.objects.get()
    assert d.deal_id == "zzz-good"


@pytest.mark.django_db
def test_deal_list_filters(client, django_user_model):
    from webapp.models import Deal

    user = django_user_model.objects.create_user(username="op", password="x")
    client.force_login(user)
    Deal.objects.create(deal_id="alpha", property_name="Alpha Storage",
                        state="TX", recommendation="PURSUE")
    Deal.objects.create(deal_id="bravo", property_name="Bravo Storage",
                        state="CO", recommendation="DECLINE")

    resp = client.get("/deals/?state=TX")
    assert resp.status_code == 200
    assert b"Alpha Storage" in resp.content
    assert b"Bravo Storage" not in resp.content


@pytest.mark.django_db
def test_deal_ordering_null_dates_sort_last():
    from webapp.models import Deal

    dated = Deal.objects.create(deal_id="dated", property_name="Dated",
                                analysis_date=datetime.date(2026, 1, 1))
    undated = Deal.objects.create(deal_id="undated", property_name="Undated")
    ids = [d.pk for d in Deal.objects.all()]
    assert ids == [dated.pk, undated.pk]
    # SQLite already sorts NULLs last under DESC, so the behavioral
    # assertion above cannot catch a reversion of nulls_last=True — the
    # Postgres-only bug this ordering exists to fix (review finding).
    # Pin the compiled SQL: dropping the F(...nulls_last=True) ordering
    # removes the modifier. Backend behavior is enforced in the PG smoke.
    assert "NULLS LAST" in str(Deal.objects.all().query).upper()


@pytest.mark.django_db
def test_deal_id_supports_200_chars():
    from webapp.models import Deal

    # SQLite ignores declared VARCHAR widths and create() skips
    # full_clean(), so the insert below passes at any max_length (review
    # finding). Pin the declared width so a revert to 120 fails here;
    # storage-layer enforcement is the PG smoke's length-boundaries step.
    assert Deal._meta.get_field("deal_id").max_length == 200
    slug = "s" * 200
    Deal.objects.create(deal_id=slug, property_name="Long")
    assert Deal.objects.filter(deal_id=slug).exists()


@pytest.mark.django_db
def test_import_deals_normalizes_state_and_skips_oversize_ids(tmp_path, settings):
    import json as jsonlib

    from django.core.management import call_command

    from webapp.models import Deal

    ok = tmp_path / "ok"
    ok.mkdir()
    (ok / "deal_meta.json").write_text(jsonlib.dumps(
        {"deal_id": "ok", "property_name": "OK", "state": "texas"}))
    huge = tmp_path / "huge"
    huge.mkdir()
    (huge / "deal_meta.json").write_text(jsonlib.dumps(
        {"deal_id": "x" * 250, "property_name": "Huge"}))
    settings.CIM_DEALS_DIR = str(tmp_path)

    call_command("import_deals")
    # "texas" is NOT truncated to the fabricated code "TE" — a non-2-letter
    # state imports as blank (visible gap) with a warning (review finding)
    assert Deal.objects.get(deal_id="ok").state == ""
    assert Deal.objects.count() == 1                       # oversize skipped


@pytest.mark.django_db
def test_build_deal_meta_import_deals_round_trip_no_drift(tmp_path, settings):
    """Every key build_deal_meta emits must be either imported onto the
    Deal row or on the explicit exempt list. A new meta key that isn't
    classified fails this test — that's the point."""
    import json as jsonlib

    from django.core.management import call_command

    from gui.deal_manager import build_deal_meta
    from gui.engine import AnalysisResult
    from tests.test_web_runs import _sample_cim
    from webapp.models import Deal

    cim = _sample_cim()
    result = AnalysisResult(pdf_path="x.pdf")
    result.cim_data = cim
    result.memo_path = "/tmp/Expo_memo.docx"
    result.excel_path = "/tmp/Expo_model.xlsx"
    result.max_offer = {"max_price": 3_100_000.0}
    result.gate_summary = {"recommendation": "PURSUE"}
    folder = tmp_path / "expo"
    folder.mkdir()
    meta = build_deal_meta(cim, result, str(folder), input_files=["expo.pdf"])
    meta["deal_id"] = "expo"
    (folder / "deal_meta.json").write_text(jsonlib.dumps(meta, default=str))
    settings.CIM_DEALS_DIR = str(tmp_path)

    call_command("import_deals")
    d = Deal.objects.get(deal_id="expo")
    IMPORTED = {"deal_id", "property_name", "city", "state", "asset_type",
                "nrsf", "acreage", "asking_price", "estimated_fair_value",
                "recommendation", "analysis_date", "memo_path", "excel_path",
                "input_files"}
    # LITERAL by design: build_deal_meta today emits exactly the keys in
    # IMPORTED (verified at plan-review time). If it ever grows a key,
    # this assertion fires and the new key must be classified — either
    # mapped in import_deals + IMPORTED, or added here as exempt.
    EXEMPT_DISPLAY_ONLY = set()
    assert d.property_name == meta["property_name"]
    assert d.asking_price == meta["asking_price"]
    assert d.memo_filename == meta["memo_path"]
    assert d.input_files == meta["input_files"]
    unclassified = set(meta) - IMPORTED - EXEMPT_DISPLAY_ONLY
    assert unclassified == set(), (
        f"build_deal_meta grew keys import_deals doesn't map: {unclassified}")
