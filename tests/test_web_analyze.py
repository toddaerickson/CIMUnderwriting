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
