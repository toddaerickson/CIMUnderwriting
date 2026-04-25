"""
Tier 2 Data Enrichment — Census Bureau geocoding and ACS demographics.

Provides external data to fill gaps the CIM parser and overrides can't cover:
  - Geocoding: address → lat/lon/census tract (Census Bureau Geocoder, free)
  - Demographics: population by radius, median HHI (Census ACS 5-year, free key)

Data sourcing hierarchy for each field:
  Tier 1: CIM extraction + JSON overrides (already populated)
  Tier 2: This module (Census API)
  Tier 3: Comp database historical averages
  Tier 4: Static defaults / None
"""

import logging
import math
import time
from typing import Optional
from dataclasses import dataclass, field

import requests

from config import CENSUS_API_KEY

logger = logging.getLogger(__name__)


# ── Data Structures ───────────────────────────────────────────────

@dataclass
class EnrichmentResult:
    """Result of enrichment process."""
    fields_enriched: int = 0
    source_log: dict = field(default_factory=dict)
    geocode_success: bool = False
    census_success: bool = False
    errors: list = field(default_factory=list)


class DataResolver:
    """Resolve a field value through the tier hierarchy."""

    def __init__(self, source_log: dict):
        self.source_log = source_log
        self.fields_enriched = 0

    def resolve(self, field_name: str, tier1_value, tier2_fn=None,
                tier3_fn=None, tier4_default=None):
        """
        Try each tier in order. Return first non-None value.

        Args:
            field_name: name of the field being resolved
            tier1_value: CIM/override value (already on cim_data)
            tier2_fn: callable returning Tier 2 value (Census API)
            tier3_fn: callable returning Tier 3 value (comp DB)
            tier4_default: static fallback value
        """
        # Tier 1: already set
        if tier1_value is not None:
            self.source_log[field_name] = {
                "tier": 1, "source": "CIM/override", "value": tier1_value
            }
            return tier1_value

        # Tier 2: external API
        if tier2_fn:
            try:
                val = tier2_fn()
                if val is not None:
                    self.source_log[field_name] = {
                        "tier": 2, "source": "Census API", "value": val
                    }
                    self.fields_enriched += 1
                    return val
            except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
                logger.debug("Tier 2 lookup failed for %s: %s", field_name, exc)

        # Tier 3: comp DB
        if tier3_fn:
            try:
                val = tier3_fn()
                if val is not None:
                    self.source_log[field_name] = {
                        "tier": 3, "source": "comp_db", "value": val
                    }
                    self.fields_enriched += 1
                    return val
            except (KeyError, ValueError, TypeError) as exc:
                logger.debug("Tier 3 lookup failed for %s: %s", field_name, exc)

        # Tier 4: static default
        if tier4_default is not None:
            self.source_log[field_name] = {
                "tier": 4, "source": "default", "value": tier4_default
            }
            return tier4_default

        self.source_log[field_name] = {
            "tier": None, "source": "not available", "value": None
        }
        return None


# ── Geocoding (Census Bureau — free, no API key) ─────────────────

def _geocode_address(address: str, city: str, state: str) -> Optional[dict]:
    """
    Geocode an address using the Census Bureau Geocoder.

    Returns dict with lat, lon, state_fips, county_fips, census_tract
    or None on failure.
    """
    one_line = f"{address}, {city}, {state}"
    url = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
    params = {
        "address": one_line,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "format": "json",
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        matches = data.get("result", {}).get("addressMatches", [])
        if not matches:
            return None

        match = matches[0]
        coords = match.get("coordinates", {})
        geographies = match.get("geographies", {})

        # Census tract info is in "Census Tracts" or "2020 Census Blocks"
        tract_info = {}
        for geo_key in ("Census Tracts", "2020 Census Blocks"):
            geos = geographies.get(geo_key, [])
            if geos:
                tract_info = geos[0]
                break

        return {
            "lat": coords.get("y"),
            "lon": coords.get("x"),
            "matched_address": match.get("matchedAddress", ""),
            "state_fips": tract_info.get("STATE", ""),
            "county_fips": tract_info.get("COUNTY", ""),
            "census_tract": tract_info.get("TRACT", ""),
        }
    except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
        logger.debug("Geocoding failed for %s: %s", one_line, exc)
        return None


# ── Demographics (Census ACS 5-year — requires free API key) ─────

def _fetch_census_demographics(lat: float, lon: float,
                               state_fips: str, county_fips: str,
                               api_key: str) -> Optional[dict]:
    """
    Fetch population and median HHI from Census ACS 5-year data.

    Strategy: fetch all block groups in the county, compute Haversine
    distance from subject property, and aggregate population within
    1/3/5 mile radii.

    ACS variables:
      B01003_001E = total population
      B19013_001E = median household income
    """
    url = "https://api.census.gov/data/2022/acs/acs5"
    params = {
        "get": "B01003_001E,B19013_001E",
        "for": "block group:*",
        "in": f"state:{state_fips} county:{county_fips}",
        "key": api_key,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        rows = resp.json()

        if len(rows) < 2:
            return None

        header = rows[0]
        pop_idx = header.index("B01003_001E")
        hhi_idx = header.index("B19013_001E")

        # For each block group, we need centroid coordinates
        # ACS doesn't provide centroids, so we use the Census gazetteer
        # As a practical simplification, we'll fetch block group centroids
        centroids = _fetch_block_group_centroids(state_fips, county_fips, api_key)

        pop_1mi = 0
        pop_3mi = 0
        pop_5mi = 0
        hhi_values_3mi = []
        hhi_weights_3mi = []

        for row in rows[1:]:
            pop = _safe_int(row[pop_idx])
            hhi = _safe_float(row[hhi_idx])
            bg_state = row[header.index("state")]
            bg_county = row[header.index("county")]
            bg_tract = row[header.index("tract")]
            bg_id = row[header.index("block group")]

            geoid = f"{bg_state}{bg_county}{bg_tract}{bg_id}"

            if geoid in centroids:
                bg_lat, bg_lon = centroids[geoid]
                dist = _haversine_miles(lat, lon, bg_lat, bg_lon)

                if dist <= 1.0:
                    pop_1mi += pop
                if dist <= 3.0:
                    pop_3mi += pop
                    if hhi and hhi > 0 and pop > 0:
                        hhi_values_3mi.append(hhi)
                        hhi_weights_3mi.append(pop)
                if dist <= 5.0:
                    pop_5mi += pop

        # Weighted median HHI within 3 miles
        median_hhi = None
        if hhi_values_3mi and hhi_weights_3mi:
            total_weight = sum(hhi_weights_3mi)
            weighted_sum = sum(h * w for h, w in zip(hhi_values_3mi, hhi_weights_3mi))
            median_hhi = round(weighted_sum / total_weight) if total_weight else None

        return {
            "population_1mi": pop_1mi,
            "population_3mi": pop_3mi,
            "population_5mi": pop_5mi,
            "median_hhi_3mi": median_hhi,
        }

    except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
        logger.debug("Census demographics fetch failed: %s", exc)
        return None


def _fetch_block_group_centroids(state_fips: str, county_fips: str,
                                 api_key: str) -> dict:
    """
    Fetch block group centroids from Census TIGERweb.

    Returns dict mapping GEOID → (lat, lon).

    Tries multiple TIGERweb ACS vintages since available services change.
    CENTLAT/CENTLON come as strings like "+29.6968469"/"-095.4989283".
    """
    # Try multiple ACS vintages (newest first) since availability varies
    vintages = ["ACS2023", "ACS2024", "ACS2025", "ACS2022", "ACS2021"]

    for vintage in vintages:
        url = (
            f"https://tigerweb.geo.census.gov/arcgis/rest/services/"
            f"TIGERweb/tigerWMS_{vintage}/MapServer/10/query"
        )
        params = {
            "where": f"STATE='{state_fips}' AND COUNTY='{county_fips}'",
            "outFields": "GEOID,CENTLAT,CENTLON",
            "returnGeometry": "false",
            "f": "json",
            "resultRecordCount": 10000,
        }

        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                continue

            centroids = {}
            for feature in data.get("features", []):
                attrs = feature.get("attributes", {})
                geoid = str(attrs.get("GEOID", ""))
                # CENTLAT/CENTLON are strings like "+29.6968469"/"-095.4989283"
                lat = _safe_float(str(attrs.get("CENTLAT", "")), allow_negative=True)
                lon = _safe_float(str(attrs.get("CENTLON", "")), allow_negative=True)
                if geoid and lat and lon:
                    centroids[geoid] = (lat, lon)

            if centroids:
                return centroids
        except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
            logger.debug("TIGERweb %s fetch failed: %s", vintage, exc)
            continue

    return {}


# ── Haversine Distance ────────────────────────────────────────────

def _haversine_miles(lat1: float, lon1: float,
                     lat2: float, lon2: float) -> float:
    """Compute distance in miles between two lat/lon points."""
    R = 3958.8  # Earth radius in miles
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# ── Orchestrator ──────────────────────────────────────────────────

def enrich_cim_data(cim_data, census_api_key: str = None,
                    comp_db=None) -> EnrichmentResult:
    """
    Enrich CIM data using the tier hierarchy.

    For each demographic field:
      Tier 1: CIM/override (already on cim_data)
      Tier 2: Census API (if address available)
      Tier 3: Comp DB averages (not used for demographics — too location-specific)
      Tier 4: None (don't guess gate-critical fields)

    Also geocodes the property address if lat/lon not available.
    """
    result = EnrichmentResult()
    api_key = census_api_key or CENSUS_API_KEY

    resolver = DataResolver(result.source_log)

    # Step 1: Geocode if we have address but no lat/lon
    geocode_data = None
    if (cim_data.address and cim_data.city and cim_data.state and
            not getattr(cim_data, 'lat', None)):
        geocode_data = _geocode_address(
            cim_data.address, cim_data.city, cim_data.state
        )
        if geocode_data:
            result.geocode_success = True
            result.source_log["lat"] = {
                "tier": 2, "source": "Census Geocoder",
                "value": geocode_data["lat"]
            }
            result.source_log["lon"] = {
                "tier": 2, "source": "Census Geocoder",
                "value": geocode_data["lon"]
            }

    # Step 2: Fetch demographics if API key available
    census_data = None
    if api_key and geocode_data:
        census_data = _fetch_census_demographics(
            lat=geocode_data["lat"],
            lon=geocode_data["lon"],
            state_fips=geocode_data["state_fips"],
            county_fips=geocode_data["county_fips"],
            api_key=api_key,
        )
        if census_data:
            result.census_success = True
    elif api_key and not geocode_data:
        result.errors.append("Cannot fetch demographics without geocoded address")
    elif not api_key:
        result.errors.append("No Census API key — demographics enrichment skipped. "
                             "Register free at api.census.gov")

    # Step 3: Resolve each field using tier hierarchy
    cim_data.population_1mi = resolver.resolve(
        "population_1mi",
        tier1_value=cim_data.population_1mi,
        tier2_fn=lambda: census_data.get("population_1mi") if census_data else None,
    )

    cim_data.population_3mi = resolver.resolve(
        "population_3mi",
        tier1_value=cim_data.population_3mi,
        tier2_fn=lambda: census_data.get("population_3mi") if census_data else None,
    )

    cim_data.population_5mi = resolver.resolve(
        "population_5mi",
        tier1_value=cim_data.population_5mi,
        tier2_fn=lambda: census_data.get("population_5mi") if census_data else None,
    )

    cim_data.median_hhi_3mi = resolver.resolve(
        "median_hhi_3mi",
        tier1_value=cim_data.median_hhi_3mi,
        tier2_fn=lambda: census_data.get("median_hhi_3mi") if census_data else None,
    )

    result.fields_enriched = resolver.fields_enriched
    return result


# ── Utilities ─────────────────────────────────────────────────────

def _safe_int(val) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _safe_float(val, allow_negative=False) -> Optional[float]:
    try:
        v = float(val)
        if allow_negative:
            return v if v != 0 else None
        # Census ACS uses -666666666 for missing data
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None
