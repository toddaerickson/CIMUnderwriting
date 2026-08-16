"""Census enrichment — the centre of the demographic ring, and what happens
when it cannot be established.

Every test here patches `extract.enrichment.requests`. CENSUS_API_KEY is unset
locally and declared `sync: false` in render.yaml, so a test that reached the
live API would pass on the operator's machine and fail in CI — or, worse, pass
in both and silently depend on a third party being up.
"""

import pytest
import requests

from extract.enrichment import (
    _fetch_census_demographics,
    _geocode_zip,
    enrich_cim_data,
)
from extract.parser import CIMData


class _Resp:
    """Minimal requests.Response stand-in."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _Requests:
    """Routes each GET to a payload by URL fragment, and records the calls.

    Carries the real exception types: the module under test catches
    `requests.RequestException` off this same object, so a stub without them
    turns a handled network failure into an AttributeError."""

    RequestException = requests.RequestException

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params or {}))
        for fragment, payload in self.routes.items():
            if fragment in url:
                return _Resp(payload)
        raise AssertionError(f"unrouted GET {url}")


ZCTA_OK = {"features": [{"attributes": {
    "ZCTA5": "78602", "CENTLAT": "30.1273", "CENTLON": "-97.3280"}}]}
COORDS_OK = {"result": {"geographies": {"Census Tracts": [
    {"STATE": "48", "COUNTY": "021", "TRACT": "950401"}]}}}


# ── _geocode_zip ──────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [None, "", "786", "78602-1234", "ABCDE", 78602.5])
def test_a_malformed_zip_never_reaches_the_network(bad, monkeypatch):
    """The shape check is first so a junk ZIP costs nothing and cannot be
    interpolated into the ArcGIS `where` clause."""
    called = []
    monkeypatch.setattr("extract.enrichment.requests",
                        type("R", (), {"get": lambda *a, **k: called.append(1)})())
    assert _geocode_zip(bad) is None
    assert not called


def test_a_zip_resolves_to_its_zcta_centroid_and_the_fips_pair(monkeypatch):
    fake = _Requests({"tigerweb": ZCTA_OK, "geocoder": COORDS_OK})
    monkeypatch.setattr("extract.enrichment.requests", fake)

    got = _geocode_zip("78602")
    assert (got["lat"], got["lon"]) == (30.1273, -97.3280)
    assert (got["state_fips"], got["county_fips"]) == ("48", "021")
    assert "78602" in got["matched_address"]


def test_an_unknown_zip_returns_none_rather_than_a_guess(monkeypatch):
    monkeypatch.setattr("extract.enrichment.requests",
                        _Requests({"tigerweb": {"features": []}}))
    assert _geocode_zip("00000") is None


def test_the_zip_path_survives_the_network_being_down(monkeypatch):
    class Boom:
        RequestException = requests.RequestException

        def get(self, *a, **k):
            raise requests.RequestException("no route to host")

    monkeypatch.setattr("extract.enrichment.requests", Boom())
    assert _geocode_zip("78602") is None


# ── the falsy zero ────────────────────────────────────────────────────

def test_no_block_group_in_range_reports_nothing_not_zero(monkeypatch):
    """A zero-filled dict is TRUTHY, so it used to set census_success and get
    stamped "Census API" — while gate 1's `if pop else 'TBD'` printed "not found
    in CIM" beside that source. It also stuck: engine.py only re-enriches fields
    that are still None, so the zero outlived every later address correction."""
    monkeypatch.setattr("extract.enrichment.requests", _Requests({
        "api.census.gov": [
            ["B01003_001E", "B19013_001E", "state", "county", "tract", "block group"],
            ["1200", "65000", "48", "021", "950401", "1"],
        ],
    }))
    # No centroids -> nothing can land inside any ring.
    monkeypatch.setattr("extract.enrichment._fetch_block_group_centroids",
                        lambda *a, **k: {})

    assert _fetch_census_demographics(
        lat=30.1, lon=-97.3, state_fips="48", county_fips="021",
        api_key="dummy") is None


# ── enrich_cim_data: which centre, and is it disclosed ────────────────

def _cim(**kw):
    data = CIMData()
    data.city, data.state = "Bastrop", "TX"
    for k, v in kw.items():
        setattr(data, k, v)
    return data


def test_the_zip_centres_the_ring_when_there_is_no_street_address(monkeypatch):
    """The parser never fills `address`, so this is the ordinary path."""
    monkeypatch.setattr("extract.enrichment.requests",
                        _Requests({"tigerweb": ZCTA_OK, "geocoder": COORDS_OK}))

    result = enrich_cim_data(_cim(zip_code="78602"), census_api_key="")
    assert result.geocode_success
    assert result.source_log["lat"]["source"] == "ZCTA centroid"


def test_a_street_address_is_preferred_and_stamped_as_such(monkeypatch):
    monkeypatch.setattr("extract.enrichment.requests", _Requests({
        "onelineaddress": {"result": {"addressMatches": [{
            "coordinates": {"x": -97.31, "y": 30.11},
            "matchedAddress": "900 INDUSTRY DR, BASTROP, TX, 78602",
            "geographies": {"Census Tracts": [
                {"STATE": "48", "COUNTY": "021", "TRACT": "950401"}]},
        }]}},
    }))

    result = enrich_cim_data(
        _cim(address="900 Industry Dr", zip_code="78602"), census_api_key="")
    assert result.source_log["lat"]["source"] == "Census Geocoder"


def test_no_address_and_no_zip_means_no_ring(monkeypatch):
    """'Centred on the subject property, or there is no ring.' A broker-address
    geocode fails OPEN — it returns a large population and passes gate 1 — so
    abstaining is the correct output, not a degraded one."""
    monkeypatch.setattr("extract.enrichment.requests", _Requests({}))

    result = enrich_cim_data(_cim(), census_api_key="")
    assert not result.geocode_success
    assert result.source_log.get("lat") is None


def test_a_dead_zip_lookup_does_not_claim_a_geocode(monkeypatch):
    monkeypatch.setattr("extract.enrichment.requests",
                        _Requests({"tigerweb": {"features": []}}))

    result = enrich_cim_data(_cim(zip_code="00000"), census_api_key="")
    assert not result.geocode_success
