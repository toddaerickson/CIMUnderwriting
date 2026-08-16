"""Census enrichment — the centre of the demographic ring, and what happens
when it cannot be established.

Every test here patches `extract.enrichment.requests`. CENSUS_API_KEY is unset
locally and declared `sync: false` in render.yaml, so a test that reached the
live API would pass on the operator's machine and fail in CI — or, worse, pass
in both and silently depend on a third party being up.
"""

import contextlib
import http.server
import logging
import threading

import pytest
import requests

from extract.enrichment import (
    _fetch_census_demographics,
    _geocode_zip,
    _redacted,
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
    assert result.source_log.get("lat") is None


# ── the API key must not reach a log record ───────────────────────────
#
# Two independent vectors, both measured against the live API rather than
# reasoned about, and both gated behind DEBUG — nothing ships at DEBUG, so this
# is a LATENT leak, not an active one. What makes it worth closing anyway is
# `log_config.setup_logging`, which attaches a FileHandler at DEBUG: the first
# person to raise the level while chasing a demographics bug writes the key to
# cim_analyst.log, on disk, appended.
#
#   1. urllib3.connectionpool logs the full request line — query string and all
#      — at DEBUG on every SUCCESSFUL request. This is the dominant one and it
#      is not our logger, which is why scrubbing our own exception strings does
#      not touch it.
#   2. Our own `except` clauses. `HTTPError` embeds `response.url`;
#      `ConnectionError` embeds the request path. Both carry `key=` verbatim.
#      Pinning urllib3 cannot help here.

SECRET_KEY = "SECRETKEY0123456789abcdefghij"


class _RecordTrap(logging.Handler):
    """Every record from every logger, message AND raw args.

    Deliberately not `caplog(logger="extract.enrichment")`: the logger that
    actually leaks the key is urllib3's, so a test scoped to our own name
    passes green on a tree that writes the key to disk.
    """

    def __init__(self):
        super().__init__(level=logging.NOTSET)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def leaks(self, secret):
        found = []
        for r in self.records:
            try:
                msg = r.getMessage()
            except Exception:
                msg = str(r.msg)
            if secret in msg or secret in str(r.args):
                found.append(f"{r.name}/{r.levelname}: {msg[:120]}")
        return found


@contextlib.contextmanager
def _watching_every_logger_at_debug():
    root = logging.getLogger()
    trap = _RecordTrap()
    previous = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(trap)
    try:
        yield trap
    finally:
        root.removeHandler(trap)
        root.setLevel(previous)


@contextlib.contextmanager
def _a_local_http_server():
    """A real socket, so urllib3 builds and logs a real request line.

    A stubbed `requests` cannot exercise vector 1 at all — urllib3 is below the
    seam the other tests here patch.
    """
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'[["B01003_001E"],["1"]]'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/data"
    finally:
        server.shutdown()
        server.server_close()


@contextlib.contextmanager
def _urllib3_unpinned():
    """Undo the pin, so the characterization test below sees the raw behaviour."""
    names = ("urllib3", "urllib3.connectionpool")
    previous = {n: logging.getLogger(n).level for n in names}
    for n in names:
        logging.getLogger(n).setLevel(logging.NOTSET)
    try:
        yield
    finally:
        for n, lvl in previous.items():
            logging.getLogger(n).setLevel(lvl)


def test_urllib3_really_does_log_the_query_string_at_debug():
    """The vector the pin closes, pinned as a fact so the pin can be REMOVED if
    it ever stops being true.

    Without this, `pin_third_party_loggers` is a rule nobody can re-derive: a
    future urllib3 that stopped logging query strings would leave a mystery
    setting behind, and the honest response to this test going red is to delete
    the pin, not to patch the test.
    """
    with _urllib3_unpinned(), _a_local_http_server() as url, \
            _watching_every_logger_at_debug() as trap:
        requests.get(url, params={"get": "B01003_001E", "key": SECRET_KEY},
                     timeout=5)

    assert trap.leaks(SECRET_KEY), (
        "urllib3 no longer logs the query string — delete the pin in "
        "log_config.pin_third_party_loggers and this test with it"
    )


def test_the_census_key_never_reaches_a_log_record_on_the_success_path():
    """Vector 1, closed. This is the SUCCESS path — no exception is raised, so
    every `except`-clause fix in the world leaves it exactly as it was."""
    from log_config import pin_third_party_loggers
    pin_third_party_loggers()

    with _a_local_http_server() as url, _watching_every_logger_at_debug() as trap:
        requests.get(url, params={"get": "B01003_001E", "key": SECRET_KEY},
                     timeout=5)

    assert trap.leaks(SECRET_KEY) == []


def test_a_failed_census_fetch_logs_no_key(monkeypatch):
    """Vector 2 — our own logger, which the urllib3 pin does not reach."""
    class Boom:
        RequestException = requests.RequestException

        def get(self, url, params=None, timeout=None):
            raise requests.RequestException(
                f"429 Client Error: Too Many Requests for url: "
                f"{url}?get=X&key={SECRET_KEY}")

    monkeypatch.setattr("extract.enrichment.requests", Boom())

    with _watching_every_logger_at_debug() as trap:
        assert _fetch_census_demographics(
            lat=30.1, lon=-97.3, state_fips="48", county_fips="021",
            api_key=SECRET_KEY) is None

    assert trap.leaks(SECRET_KEY) == []


def test_the_geocoder_and_tigerweb_paths_log_no_key(monkeypatch):
    """Neither endpoint is sent the key, but both interpolate a URL into a log
    line, and `_geocode_address` logs the address itself. Redaction is applied
    uniformly so a later edit cannot reintroduce the key at a site that was
    exempt because it happened to be clean."""
    class Boom:
        RequestException = requests.RequestException

        def get(self, url, params=None, timeout=None):
            raise requests.RequestException(f"down: {url}?key={SECRET_KEY}")

    monkeypatch.setattr("extract.enrichment.requests", Boom())
    monkeypatch.setattr("extract.enrichment.CENSUS_API_KEY", SECRET_KEY)

    with _watching_every_logger_at_debug() as trap:
        assert _geocode_zip("78602") is None

    assert trap.leaks(SECRET_KEY) == []


@pytest.mark.parametrize("key", ["", None, "abc"])
def test_redaction_leaves_an_empty_or_short_key_alone(key):
    """`CENSUS_API_KEY` defaults to `""`, and `str.replace("", X)` splices X
    between every character of the message — a scrubber that destroys the log
    line it was meant to protect. A short key is refused for the neighbouring
    reason: it would match ordinary text."""
    message = "429 Client Error for url: https://api.census.gov/data?get=X"
    assert _redacted(message, key) == message


def test_redaction_catches_the_urlencoded_form():
    """`params=` percent-encodes, so the key can reach a message in a form that
    a plain `str.replace` of the raw value misses."""
    from urllib.parse import quote_plus

    secret = "abc+def/ghi=jkl0123456"
    message = f"failed for url: https://x/y?key={quote_plus(secret)}"
    assert secret not in _redacted(message, secret)
    assert quote_plus(secret) not in _redacted(message, secret)
