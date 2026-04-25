"""Tests for deal manager module."""

import os
import json
import pytest
from gui.deal_manager import (
    sanitize_name, create_deal_folder, write_deal_meta,
    read_deal_meta, list_all_deals, detect_asset_type, DEALS_DIR,
)


def test_sanitize_name_basic():
    assert sanitize_name("DeSoto Self Storage") == "DeSoto_Self_Storage"


def test_sanitize_name_special_chars():
    assert sanitize_name("B&C Storage - Offering") == "BC_Storage_-_Offering"


def test_sanitize_name_empty():
    assert sanitize_name("") == "Unknown_Property"


def test_sanitize_name_spaces():
    assert sanitize_name("  Lots   Of   Spaces  ") == "Lots_Of_Spaces"


def test_create_deal_folder(tmp_path, monkeypatch):
    """Create deal folder in a temp directory."""
    monkeypatch.setattr("gui.deal_manager.DEALS_DIR", str(tmp_path / "deals"))
    folder = create_deal_folder("Test Storage")
    assert os.path.isdir(folder)
    assert os.path.isdir(os.path.join(folder, "inputs"))


def test_write_and_read_meta(tmp_path):
    """Round-trip deal_meta.json."""
    meta = {"property_name": "Test", "asking_price": 1_000_000}
    write_deal_meta(str(tmp_path), meta)
    loaded = read_deal_meta(str(tmp_path))
    assert loaded["property_name"] == "Test"
    assert loaded["asking_price"] == 1_000_000


def test_read_meta_missing(tmp_path):
    """Missing deal_meta.json returns None."""
    assert read_deal_meta(str(tmp_path)) is None


def test_list_all_deals(tmp_path, monkeypatch):
    """List deals from filesystem."""
    monkeypatch.setattr("gui.deal_manager.DEALS_DIR", str(tmp_path))
    # Create two deal folders
    for name, dt in [("Deal_A", "2026-01-01"), ("Deal_B", "2026-02-01")]:
        d = tmp_path / name
        d.mkdir()
        (d / "deal_meta.json").write_text(
            json.dumps({"property_name": name, "analysis_date": dt})
        )
    deals = list_all_deals()
    assert len(deals) == 2
    # Should be sorted newest first
    assert deals[0]["property_name"] == "Deal_B"


def test_detect_asset_type_self_storage(mock_cim_data):
    assert detect_asset_type(mock_cim_data) == "Self Storage"


def test_detect_asset_type_brv(mock_cim_data):
    mock_cim_data.brv_enclosed_sf = 5000
    assert detect_asset_type(mock_cim_data) == "Boat & RV Storage"


def test_detect_asset_type_cc(mock_cim_data):
    mock_cim_data.cc_pct = 0.75
    assert detect_asset_type(mock_cim_data) == "Climate-Controlled Self Storage"
