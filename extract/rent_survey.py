"""
Competitive Rent Survey — Web-based market rent data.

Scrapes publicly available self-storage listing sites to gather
current market rents for comparable facilities near the subject property.

Data sources (in priority order):
1. SpareFoot search results (sparefoot.com)
2. StorageCafe listings (storagecafe.com)

Falls back gracefully if scraping fails — the pipeline continues
with CIM-provided or override market_rent_psf.
"""

import re
import time
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Standard unit size buckets for comparison (from central registry)
from registry import STANDARD_SIZE_BUCKETS as STANDARD_SIZES


@dataclass
class CompFacility:
    """A competing self-storage facility from web search."""
    name: str
    address: str = ""
    distance_mi: float = None
    units: list = field(default_factory=list)  # list of dicts: {size, sf, rate, climate_controlled}
    avg_rate_per_sf: float = None
    source: str = ""


@dataclass
class RentSurveyResult:
    """Result of a competitive rent survey."""
    success: bool
    source: str = ""
    facilities: list = field(default_factory=list)
    market_rent_per_sf_mo: float = None  # weighted avg $/SF/mo across all comps
    market_rent_by_size: dict = field(default_factory=dict)  # size_label → avg rate
    comp_count: int = 0
    error: str = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "source": self.source,
            "comp_count": self.comp_count,
            "market_rent_per_sf_mo": self.market_rent_per_sf_mo,
            "market_rent_by_size": self.market_rent_by_size,
            "facilities": [
                {
                    "name": f.name,
                    "address": f.address,
                    "distance_mi": f.distance_mi,
                    "avg_rate_per_sf": f.avg_rate_per_sf,
                    "unit_count": len(f.units),
                }
                for f in self.facilities
            ],
            "error": self.error,
        }


def run_rent_survey(city: str, state: str, zip_code: str = None) -> RentSurveyResult:
    """
    Run competitive rent survey for a location.

    Tries SpareFoot first, then StorageCafe. Returns market rent
    data or a graceful failure result.

    Args:
        city: city name (e.g., "Pasadena")
        state: 2-letter state code (e.g., "TX")
        zip_code: optional ZIP code for more precise results

    Returns:
        RentSurveyResult with market rent data
    """
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return RentSurveyResult(
            success=False,
            error="Missing dependencies: pip install requests beautifulsoup4",
        )

    # Try SpareFoot first
    result = _scrape_sparefoot(city, state, zip_code, requests, BeautifulSoup)
    if result.success and result.comp_count >= 3:
        return result

    # Try StorageCafe as fallback
    result2 = _scrape_storagecafe(city, state, zip_code, requests, BeautifulSoup)
    if result2.success and result2.comp_count >= 3:
        return result2

    # Return whichever got more data
    if result.comp_count > result2.comp_count:
        return result
    if result2.comp_count > 0:
        return result2

    return RentSurveyResult(
        success=False,
        error=f"Could not retrieve sufficient comp data for {city}, {state}. "
              "Set market_rent_psf in override JSON manually.",
    )


def _scrape_sparefoot(city: str, state: str, zip_code: str,
                       requests, BeautifulSoup) -> RentSurveyResult:
    """Scrape SpareFoot search results."""
    facilities = []

    try:
        # Build search URL
        search_term = zip_code if zip_code else f"{city}-{state}"
        url = f"https://www.sparefoot.com/self-storage/{state.lower()}/{city.lower().replace(' ', '-')}/search"
        if zip_code:
            url = f"https://www.sparefoot.com/self-storage/search?search={zip_code}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return RentSurveyResult(
                success=False, source="sparefoot",
                error=f"SpareFoot returned HTTP {resp.status_code}",
            )

        soup = BeautifulSoup(resp.text, "html.parser")

        # SpareFoot embeds facility data in JSON-LD or data attributes
        # Look for facility cards
        facility_cards = soup.find_all("div", {"class": re.compile(r"facility|listing|result", re.I)})

        # Also try to find price data from text patterns
        all_prices = _extract_prices_from_text(resp.text)

        for card in facility_cards[:15]:  # Limit to 15 facilities
            fac = _parse_sparefoot_card(card)
            if fac and fac.units:
                facilities.append(fac)

        # If card parsing didn't work, try JSON-LD
        if not facilities:
            facilities = _parse_json_ld(soup, "sparefoot")

        # If still nothing, try extracting from raw price patterns
        if not facilities and all_prices:
            fac = CompFacility(
                name="Market Average (SpareFoot)",
                source="sparefoot",
                units=all_prices,
            )
            if all_prices:
                total_sf = sum(u.get("sf", 100) for u in all_prices)
                total_rent = sum(u.get("rate", 0) for u in all_prices)
                fac.avg_rate_per_sf = total_rent / total_sf if total_sf else None
                facilities.append(fac)

    except requests.exceptions.RequestException as e:
        return RentSurveyResult(
            success=False, source="sparefoot",
            error=f"Network error: {e}",
        )
    except (AttributeError, TypeError, ValueError, IndexError) as e:
        logger.debug("SpareFoot parse error: %s", e)
        return RentSurveyResult(
            success=False, source="sparefoot",
            error=f"Parse error: {e}",
        )

    return _build_result(facilities, "sparefoot")


def _scrape_storagecafe(city: str, state: str, zip_code: str,
                         requests, BeautifulSoup) -> RentSurveyResult:
    """Scrape StorageCafe listings."""
    facilities = []

    try:
        # StorageCafe URL pattern
        city_slug = city.lower().replace(" ", "-")
        state_slug = state.lower()
        url = f"https://www.storagecafe.com/self-storage/{state_slug}/{city_slug}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return RentSurveyResult(
                success=False, source="storagecafe",
                error=f"StorageCafe returned HTTP {resp.status_code}",
            )

        soup = BeautifulSoup(resp.text, "html.parser")

        # StorageCafe typically has facility listings with price tables
        listing_cards = soup.find_all(["div", "article"], {"class": re.compile(r"listing|facility|property", re.I)})

        for card in listing_cards[:15]:
            fac = _parse_storagecafe_card(card)
            if fac and fac.units:
                facilities.append(fac)

        # Try JSON-LD as fallback
        if not facilities:
            facilities = _parse_json_ld(soup, "storagecafe")

        # Extract any visible prices from the page
        if not facilities:
            all_prices = _extract_prices_from_text(resp.text)
            if all_prices:
                fac = CompFacility(
                    name="Market Average (StorageCafe)",
                    source="storagecafe",
                    units=all_prices,
                )
                total_sf = sum(u.get("sf", 100) for u in all_prices)
                total_rent = sum(u.get("rate", 0) for u in all_prices)
                fac.avg_rate_per_sf = total_rent / total_sf if total_sf else None
                facilities.append(fac)

    except (AttributeError, TypeError, ValueError, IndexError) as e:
        logger.debug("StorageCafe parse error: %s", e)
        return RentSurveyResult(
            success=False, source="storagecafe",
            error=f"Error: {e}",
        )

    return _build_result(facilities, "storagecafe")


def _parse_sparefoot_card(card) -> CompFacility | None:
    """Parse a single SpareFoot facility card."""
    try:
        # Extract facility name
        name_el = card.find(["h2", "h3", "a"], {"class": re.compile(r"name|title", re.I)})
        name = name_el.get_text(strip=True) if name_el else None
        if not name:
            return None

        # Extract address
        addr_el = card.find(["span", "p", "div"], {"class": re.compile(r"address|location", re.I)})
        address = addr_el.get_text(strip=True) if addr_el else ""

        # Extract unit prices — look for price elements
        units = []
        price_els = card.find_all(["span", "div", "td"], {"class": re.compile(r"price|rate|cost", re.I)})
        size_els = card.find_all(["span", "div", "td"], {"class": re.compile(r"size|dimension|type", re.I)})

        for i, price_el in enumerate(price_els):
            price_text = price_el.get_text(strip=True)
            rate = _parse_price(price_text)
            if rate is None:
                continue

            # Try to match with a size
            size_text = size_els[i].get_text(strip=True) if i < len(size_els) else ""
            sf = _parse_size_to_sf(size_text)

            units.append({
                "size": size_text,
                "sf": sf or 100,
                "rate": rate,
            })

        # Also check for price data in data attributes
        if not units:
            for el in card.find_all(attrs={"data-price": True}):
                rate = _parse_price(el.get("data-price", ""))
                size = el.get("data-size", "")
                sf = _parse_size_to_sf(size)
                if rate:
                    units.append({"size": size, "sf": sf or 100, "rate": rate})

        if not units:
            return None

        fac = CompFacility(name=name, address=address, units=units, source="sparefoot")
        total_sf = sum(u["sf"] for u in units)
        total_rent = sum(u["rate"] for u in units)
        fac.avg_rate_per_sf = total_rent / total_sf if total_sf else None
        return fac

    except (AttributeError, TypeError, ValueError, IndexError) as e:
        logger.debug("SpareFoot card parse error: %s", e)
        return None


def _parse_storagecafe_card(card) -> CompFacility | None:
    """Parse a single StorageCafe facility card."""
    try:
        name_el = card.find(["h2", "h3", "a"], {"class": re.compile(r"name|title|heading", re.I)})
        name = name_el.get_text(strip=True) if name_el else None
        if not name:
            return None

        addr_el = card.find(["span", "p"], {"class": re.compile(r"address|location", re.I)})
        address = addr_el.get_text(strip=True) if addr_el else ""

        units = []
        # StorageCafe often has price rows in tables
        rows = card.find_all("tr")
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                size_text = cells[0].get_text(strip=True)
                price_text = cells[-1].get_text(strip=True)
                rate = _parse_price(price_text)
                sf = _parse_size_to_sf(size_text)
                if rate and sf:
                    units.append({"size": size_text, "sf": sf, "rate": rate})

        # Also look for price divs
        if not units:
            price_divs = card.find_all(["div", "span"], string=re.compile(r"\$\d+"))
            for div in price_divs:
                text = div.get_text(strip=True)
                rate = _parse_price(text)
                if rate:
                    units.append({"size": "", "sf": 100, "rate": rate})

        if not units:
            return None

        fac = CompFacility(name=name, address=address, units=units, source="storagecafe")
        total_sf = sum(u["sf"] for u in units)
        total_rent = sum(u["rate"] for u in units)
        fac.avg_rate_per_sf = total_rent / total_sf if total_sf else None
        return fac

    except (AttributeError, TypeError, ValueError, IndexError) as e:
        logger.debug("StorageCafe card parse error: %s", e)
        return None


def _parse_json_ld(soup, source: str) -> list:
    """Try to extract facility data from JSON-LD structured data."""
    import json
    facilities = []

    for script in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                for item in data:
                    fac = _json_ld_to_facility(item, source)
                    if fac:
                        facilities.append(fac)
            elif isinstance(data, dict):
                fac = _json_ld_to_facility(data, source)
                if fac:
                    facilities.append(fac)
        except (json.JSONDecodeError, TypeError):
            continue

    return facilities


def _json_ld_to_facility(data: dict, source: str) -> CompFacility | None:
    """Convert JSON-LD entity to CompFacility if it looks like a storage facility."""
    schema_type = data.get("@type", "")
    if schema_type not in ("LocalBusiness", "SelfStorage", "Place", "Product"):
        return None

    name = data.get("name", "")
    if not name:
        return None

    address = ""
    addr_data = data.get("address", {})
    if isinstance(addr_data, dict):
        address = f"{addr_data.get('streetAddress', '')} {addr_data.get('addressLocality', '')}".strip()

    # Look for offers/prices
    units = []
    offers = data.get("offers", data.get("hasOfferCatalog", {}).get("itemListElement", []))
    if isinstance(offers, dict):
        offers = [offers]
    if isinstance(offers, list):
        for offer in offers:
            if isinstance(offer, dict):
                price = offer.get("price") or offer.get("lowPrice")
                if price:
                    try:
                        rate = float(price)
                        name_str = offer.get("name", "")
                        sf = _parse_size_to_sf(name_str)
                        units.append({"size": name_str, "sf": sf or 100, "rate": rate})
                    except (ValueError, TypeError):
                        pass

    if not units:
        return None

    fac = CompFacility(name=name, address=address, units=units, source=source)
    total_sf = sum(u["sf"] for u in units)
    total_rent = sum(u["rate"] for u in units)
    fac.avg_rate_per_sf = total_rent / total_sf if total_sf else None
    return fac


def _extract_prices_from_text(html_text: str) -> list:
    """Extract unit size + price pairs from raw HTML text."""
    units = []

    # Pattern: "10x10" ... "$XX" or "$XX/mo"
    # Look for size-price pairs within proximity
    size_price_pattern = re.compile(
        r'(\d+)\s*[xX×]\s*(\d+)[^$]{0,60}\$(\d+(?:\.\d{2})?)',
        re.DOTALL
    )
    for match in size_price_pattern.finditer(html_text):
        w, d, price = int(match.group(1)), int(match.group(2)), float(match.group(3))
        sf = w * d
        if 20 <= sf <= 1000 and 10 <= price <= 1000:
            units.append({"size": f"{w}x{d}", "sf": sf, "rate": price})

    return units


def _parse_price(text: str) -> float | None:
    """Extract a dollar amount from text."""
    match = re.search(r'\$\s*(\d+(?:,\d{3})*(?:\.\d{2})?)', text)
    if match:
        price_str = match.group(1).replace(",", "")
        try:
            price = float(price_str)
            if 5 <= price <= 2000:  # Reasonable monthly storage rent
                return price
        except ValueError:
            pass
    return None


def _parse_size_to_sf(text: str) -> int | None:
    """Convert a size label like '10x10' or '10 x 15' to square feet."""
    match = re.search(r'(\d+)\s*[xX×]\s*(\d+)', text)
    if match:
        w, d = int(match.group(1)), int(match.group(2))
        sf = w * d
        if 20 <= sf <= 1500:
            return sf

    # Check standard labels
    for label, sf in STANDARD_SIZES.items():
        if label.replace("x", "") in text.replace(" ", "").replace("x", ""):
            return sf

    return None


def _build_result(facilities: list, source: str) -> RentSurveyResult:
    """Build RentSurveyResult from list of facilities."""
    if not facilities:
        return RentSurveyResult(success=False, source=source, error="No facilities found")

    # Compute overall market rent per SF
    all_units = []
    for fac in facilities:
        all_units.extend(fac.units)

    if not all_units:
        return RentSurveyResult(
            success=False, source=source,
            facilities=facilities, comp_count=len(facilities),
            error="Facilities found but no unit prices extracted",
        )

    # Weighted average rent per SF (monthly)
    total_sf = sum(u.get("sf", 100) for u in all_units)
    total_rent = sum(u.get("rate", 0) for u in all_units)
    market_rent_psf = total_rent / total_sf if total_sf else None

    # By size bucket
    by_size = {}
    for u in all_units:
        size = u.get("size", "unknown")
        if size not in by_size:
            by_size[size] = {"total_rate": 0, "count": 0, "sf": u.get("sf", 100)}
        by_size[size]["total_rate"] += u.get("rate", 0)
        by_size[size]["count"] += 1

    market_by_size = {}
    for size, data in by_size.items():
        if data["count"] > 0:
            avg_rate = data["total_rate"] / data["count"]
            market_by_size[size] = {
                "avg_rate": avg_rate,
                "rate_per_sf": avg_rate / data["sf"] if data["sf"] else None,
                "sample_size": data["count"],
            }

    return RentSurveyResult(
        success=True,
        source=source,
        facilities=facilities,
        market_rent_per_sf_mo=market_rent_psf,
        market_rent_by_size=market_by_size,
        comp_count=len(facilities),
    )
