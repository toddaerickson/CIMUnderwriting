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
