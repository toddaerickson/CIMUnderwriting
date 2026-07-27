"""Deal model + import command + list view tests."""
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
