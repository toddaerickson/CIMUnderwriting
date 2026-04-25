"""
Structured data extraction from raw CIM text.

CIMs have no standard format. This parser uses regex and keyword matching
to locate key data points. Missing fields are set to None and flagged
for manual review by Claude Code.
"""

import re
from dataclasses import dataclass, field, fields
from typing import Optional


@dataclass
class UnitType:
    size_label: str = ""          # e.g., "5x10", "10x20"
    width: Optional[float] = None
    depth: Optional[float] = None
    sf: Optional[float] = None
    count: Optional[int] = None
    rate: Optional[float] = None  # monthly rent per unit
    climate_controlled: bool = False


@dataclass
class FinancialLine:
    label: str = ""
    t3: Optional[float] = None    # trailing 3-month annualized
    t12: Optional[float] = None   # trailing 12-month actual
    cim_yr1: Optional[float] = None  # CIM pro forma year 1


@dataclass
class CIMData:
    # Property basics
    property_name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    msa: Optional[str] = None
    year_built: Optional[int] = None
    year_expanded: Optional[int] = None
    acreage: Optional[float] = None

    # Size & occupancy
    nrsf: Optional[float] = None          # net rentable square feet
    total_units: Optional[int] = None
    cc_units: Optional[int] = None        # climate-controlled units
    non_cc_units: Optional[int] = None
    cc_sf: Optional[float] = None
    non_cc_sf: Optional[float] = None
    cc_pct: Optional[float] = None        # % of NRSF that is CC
    physical_occupancy: Optional[float] = None
    economic_occupancy: Optional[float] = None

    # Facility-type SF breakdowns (for replacement cost)
    ss_driveup_sf: Optional[float] = None      # self-storage drive-up SF
    ss_enclosed_sf: Optional[float] = None      # self-storage enclosed multi-story SF
    brv_enclosed_sf: Optional[float] = None     # boat/RV enclosed building SF
    brv_covered_sf: Optional[float] = None      # boat/RV covered canopy SF
    brv_open_sf: Optional[float] = None         # boat/RV open parking SF

    # Pricing
    asking_price: Optional[float] = None
    price_per_sf: Optional[float] = None

    # Demographics
    population_1mi: Optional[int] = None
    population_3mi: Optional[int] = None
    population_5mi: Optional[int] = None
    median_hhi_3mi: Optional[float] = None

    # Unit mix
    unit_mix: list = field(default_factory=list)

    # Financials
    income_lines: list = field(default_factory=list)
    expense_lines: list = field(default_factory=list)
    ttm_gpr: Optional[float] = None
    ttm_egr: Optional[float] = None
    ttm_total_revenue: Optional[float] = None
    ttm_total_expenses: Optional[float] = None
    ttm_noi: Optional[float] = None
    cim_yr1_noi: Optional[float] = None
    other_income: Optional[float] = None

    # Comps
    comp_data: list = field(default_factory=list)

    # Supply pipeline
    new_supply_mentions: Optional[str] = None

    # Market rent (for value-add analysis)
    market_rent_psf: Optional[float] = None  # $/SF/month at market rates

    # Misc
    capex_estimate: Optional[float] = None
    mgmt_fee_pct: Optional[float] = None

    def extraction_report(self) -> dict:
        """Return a report of populated vs missing fields."""
        total = 0
        populated = 0
        missing_fields = []
        for f in fields(self):
            if f.name in ("unit_mix", "income_lines", "expense_lines", "comp_data"):
                total += 1
                if getattr(self, f.name):
                    populated += 1
                else:
                    missing_fields.append(f.name)
            else:
                total += 1
                if getattr(self, f.name) is not None:
                    populated += 1
                else:
                    missing_fields.append(f.name)

        return {
            "total_fields": total,
            "populated": populated,
            "missing": missing_fields,
            "confidence_pct": round(100 * populated / total, 1) if total else 0,
        }


def parse_cim(raw: dict) -> CIMData:
    """
    Parse raw PDF extraction into structured CIMData.

    Args:
        raw: dict from pdf_reader.extract_pdf() with keys
             "text", "tables", "page_count", "pages"

    Returns:
        CIMData with as many fields populated as possible.
    """
    text = raw["text"]
    tables = raw.get("tables", [])
    data = CIMData()

    _parse_property_basics(text, data)
    _parse_size_occupancy(text, data)
    _parse_pricing(text, data)
    _parse_demographics(text, data)
    _parse_financials(text, tables, data)
    _parse_supply(text, data)
    _compute_derived(data)

    return data


# ── Internal Parsing Functions ──────────────────────────────────────

def _parse_property_basics(text: str, data: CIMData):
    """Extract property name, address, year built, acreage."""

    # Property name — often near top, try common patterns
    name_patterns = [
        r"(?:property|facility|asset)\s*(?:name)?[:\s]+([A-Z][A-Za-z0-9\s\-\']+(?:Self[\s\-]?Storage|Storage|SS))",
        r"([A-Z][A-Za-z0-9\s\-\']+(?:Self[\s\-]?Storage|Storage))",
    ]
    for pat in name_patterns:
        m = re.search(pat, text)
        if m:
            data.property_name = m.group(1).strip()
            break

    # Address
    addr_pat = r"(\d{1,6}\s+[A-Za-z0-9\s\.\,]+(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Boulevard|Blvd|Lane|Ln|Way|Highway|Hwy|Parkway|Pkwy)[\.?\s,]*)"
    m = re.search(addr_pat, text)
    if m:
        data.address = m.group(1).strip().rstrip(",")

    # City, State
    city_state_pat = r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)*),\s*([A-Z]{2})\s+\d{5}"
    m = re.search(city_state_pat, text)
    if m:
        data.city = m.group(1).strip()
        data.state = m.group(2).strip()

    # Year built
    yb_pat = r"(?:year\s+built|built\s+in|constructed\s+in|vintage)[:\s]*(\d{4})"
    m = re.search(yb_pat, text, re.IGNORECASE)
    if m:
        data.year_built = int(m.group(1))

    # Year expanded
    exp_pat = r"(?:expanded|expansion|addition)\s+(?:in\s+)?(\d{4})"
    m = re.search(exp_pat, text, re.IGNORECASE)
    if m:
        data.year_expanded = int(m.group(1))

    # Acreage
    ac_pat = r"(\d+\.?\d*|\d*\.\d+)\s*(?:acres?|ac\b)"
    m = re.search(ac_pat, text, re.IGNORECASE)
    if m:
        try:
            data.acreage = float(m.group(1))
        except ValueError:
            pass


def _parse_size_occupancy(text: str, data: CIMData):
    """Extract NRSF, unit counts, CC split, occupancy."""

    # NRSF / Net Rentable SF
    nrsf_patterns = [
        r"(?:net\s+rentable|NRSF|rentable)\s*(?:square\s*(?:feet|footage)|SF|sq\.?\s*ft\.?)[:\s]*[~≈]*([\d,]+)",
        r"([\d,]+)\s*(?:net\s+rentable|NRSF)\s*(?:square\s*(?:feet|footage)|SF)",
    ]
    for pat in nrsf_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            data.nrsf = _parse_number(m.group(1))
            break

    # Total units
    unit_patterns = [
        r"([\d,]+)\s*(?:total\s+)?(?:storage\s+)?units",
        r"(?:units|unit\s+count)[:\s]*([\d,]+)",
    ]
    for pat in unit_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            data.total_units = int(_parse_number(m.group(1)))
            break

    # Climate-controlled percentage
    cc_pat = r"([\d\.]+)\s*%\s*(?:climate[\s\-]?controlled|CC)"
    m = re.search(cc_pat, text, re.IGNORECASE)
    if m:
        data.cc_pct = float(m.group(1)) / 100.0

    # Occupancy
    occ_patterns = [
        r"(?:physical\s+)?occupancy[:\s]*([\d\.]+)\s*%",
        r"([\d\.]+)\s*%\s*(?:occupied|occupancy)",
    ]
    for pat in occ_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            data.physical_occupancy = float(m.group(1)) / 100.0
            break

    # Economic occupancy
    econ_pat = r"economic\s+occupancy[:\s]*([\d\.]+)\s*%"
    m = re.search(econ_pat, text, re.IGNORECASE)
    if m:
        data.economic_occupancy = float(m.group(1)) / 100.0


def _parse_pricing(text: str, data: CIMData):
    """Extract asking price."""
    price_patterns = [
        r"(?:asking\s+price|list\s+price|offered?\s+(?:at|price)|purchase\s+price)[:\s]*\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|MM|M)",
        r"(?:asking\s+price|list\s+price|offered?\s+(?:at|price)|purchase\s+price)[:\s]*\$\s*([\d,]+(?:\.\d+)?)",
    ]
    for pat in price_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = _parse_number(m.group(1))
            # Check if in millions
            if "million" in text[m.start():m.end()+20].lower() or \
               "MM" in text[m.start():m.end()+10] or \
               (val < 1000 and "M" in text[m.end():m.end()+5]):
                val *= 1_000_000
            data.asking_price = val
            break


def _parse_demographics(text: str, data: CIMData):
    """Extract population by radius and median HHI."""

    # Population within radii
    pop_patterns = {
        "population_1mi": [
            r"1[\s\-]?mile[^:]*?[:\s]*([\d,]+)\s*(?:people|pop|residents)?",
            r"([\d,]+)\s*(?:people|pop|residents)?\s*(?:within|in)\s*(?:a\s*)?1[\s\-]?mile",
        ],
        "population_3mi": [
            r"3[\s\-]?mile[^:]*?[:\s]*([\d,]+)\s*(?:people|pop|residents)?",
            r"([\d,]+)\s*(?:people|pop|residents)?\s*(?:within|in)\s*(?:a\s*)?3[\s\-]?mile",
        ],
        "population_5mi": [
            r"5[\s\-]?mile[^:]*?[:\s]*([\d,]+)\s*(?:people|pop|residents)?",
            r"([\d,]+)\s*(?:people|pop|residents)?\s*(?:within|in)\s*(?:a\s*)?5[\s\-]?mile",
        ],
    }
    for field_name, pats in pop_patterns.items():
        for pat in pats:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                setattr(data, field_name, int(_parse_number(m.group(1))))
                break

    # Median HHI
    hhi_pat = r"(?:median\s+)?(?:household\s+income|HHI)[:\s]*\$\s*([\d,]+)"
    m = re.search(hhi_pat, text, re.IGNORECASE)
    if m:
        data.median_hhi_3mi = _parse_number(m.group(1))


def _parse_financials(text: str, tables: list, data: CIMData):
    """Extract income and expense data from text and tables."""

    # Try to find NOI
    noi_patterns = [
        r"(?:TTM|T-?12|trailing\s+twelve)\s*(?:month)?\s*NOI[:\s]*\$?\s*([\d,]+(?:\.\d+)?)",
        r"NOI[:\s]*\$?\s*([\d,]+(?:\.\d+)?)",
        r"net\s+operating\s+income[:\s]*\$?\s*([\d,]+(?:\.\d+)?)",
    ]
    for pat in noi_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            data.ttm_noi = _parse_number(m.group(1))
            break

    # GPR / Gross Potential Rent
    gpr_pat = r"(?:gross\s+potential\s+(?:rent|revenue)|GPR)[:\s]*\$?\s*([\d,]+(?:\.\d+)?)"
    m = re.search(gpr_pat, text, re.IGNORECASE)
    if m:
        data.ttm_gpr = _parse_number(m.group(1))

    # EGR / Effective Gross Revenue
    egr_pat = r"(?:effective\s+gross\s+(?:revenue|income)|EGR|EGI)[:\s]*\$?\s*([\d,]+(?:\.\d+)?)"
    m = re.search(egr_pat, text, re.IGNORECASE)
    if m:
        data.ttm_egr = _parse_number(m.group(1))

    # Total revenue
    rev_pat = r"total\s+(?:revenue|income)[:\s]*\$?\s*([\d,]+(?:\.\d+)?)"
    m = re.search(rev_pat, text, re.IGNORECASE)
    if m:
        data.ttm_total_revenue = _parse_number(m.group(1))

    # Total expenses
    exp_pat = r"total\s+(?:operating\s+)?expenses?[:\s]*\$?\s*([\d,]+(?:\.\d+)?)"
    m = re.search(exp_pat, text, re.IGNORECASE)
    if m:
        data.ttm_total_expenses = _parse_number(m.group(1))

    # Other income
    oi_pat = r"other\s+income[:\s]*\$?\s*([\d,]+(?:\.\d+)?)"
    m = re.search(oi_pat, text, re.IGNORECASE)
    if m:
        data.other_income = _parse_number(m.group(1))

    # CIM Year 1 NOI
    yr1_patterns = [
        r"(?:year\s*1|yr\.?\s*1|pro\s*forma)\s*NOI[:\s]*\$?\s*([\d,]+(?:\.\d+)?)",
        r"NOI\s*[\-–]\s*(?:year\s*1|yr\.?\s*1|pro\s*forma)[:\s]*\$?\s*([\d,]+(?:\.\d+)?)",
    ]
    for pat in yr1_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            data.cim_yr1_noi = _parse_number(m.group(1))
            break

    # Management fee percentage
    mgmt_pat = r"(?:management|mgmt\.?)\s+fee[:\s]*([\d\.]+)\s*%"
    m = re.search(mgmt_pat, text, re.IGNORECASE)
    if m:
        data.mgmt_fee_pct = float(m.group(1)) / 100.0

    # Attempt to parse financial tables
    _parse_financial_tables(tables, data)


def _parse_financial_tables(tables: list, data: CIMData):
    """Try to extract income/expense line items from parsed tables."""
    income_keywords = [
        "gross potential", "rental income", "vacancy", "concession",
        "effective gross", "other income", "late fee", "admin fee",
        "merchandise", "total revenue", "total income",
    ]
    expense_keywords = [
        "property tax", "real estate tax", "insurance", "utility",
        "utilities", "repair", "maintenance", "r&m", "advertising",
        "marketing", "payroll", "salary", "wages", "general",
        "administrative", "g&a", "management fee", "mgmt fee",
        "total expense", "total operating", "net operating",
    ]

    for table_info in tables:
        table = table_info["data"]
        for row in table:
            if len(row) < 2:
                continue
            label = row[0].lower().strip() if row[0] else ""
            if not label:
                continue

            # Check if this looks like an income or expense line
            is_income = any(kw in label for kw in income_keywords)
            is_expense = any(kw in label for kw in expense_keywords)

            if is_income or is_expense:
                values = []
                for cell in row[1:]:
                    val = _parse_currency(cell)
                    if val is not None:
                        values.append(val)

                line = FinancialLine(label=row[0].strip())
                if len(values) >= 1:
                    line.t12 = values[-1]  # Assume last column is most recent
                if len(values) >= 2:
                    line.t3 = values[-2]   # Second to last might be T3
                if len(values) >= 3:
                    line.cim_yr1 = values[0]  # First might be pro forma

                if is_income:
                    data.income_lines.append(line)
                else:
                    data.expense_lines.append(line)


def _parse_supply(text: str, data: CIMData):
    """Extract mentions of new supply / construction pipeline."""
    supply_keywords = [
        "new supply", "under construction", "pipeline", "proposed",
        "planned", "entitled", "new development", "new facilit",
        "new storage", "competitor", "new construction",
    ]
    mentions = []
    sentences = re.split(r'[.!?]+', text)
    for sent in sentences:
        if any(kw in sent.lower() for kw in supply_keywords):
            clean = sent.strip()
            if len(clean) > 20:
                mentions.append(clean[:300])

    if mentions:
        data.new_supply_mentions = " | ".join(mentions[:5])


def _compute_derived(data: CIMData):
    """Compute derived fields from parsed data."""
    if data.asking_price and data.nrsf:
        data.price_per_sf = round(data.asking_price / data.nrsf, 2)

    if data.cc_pct and data.nrsf:
        data.cc_sf = round(data.nrsf * data.cc_pct)
        data.non_cc_sf = round(data.nrsf * (1.0 - data.cc_pct))

    # If we have total revenue and expenses but no NOI, compute it
    if data.ttm_noi is None and data.ttm_total_revenue and data.ttm_total_expenses:
        data.ttm_noi = data.ttm_total_revenue - data.ttm_total_expenses


# ── Utility Helpers ─────────────────────────────────────────────────

def _parse_number(s: str) -> float:
    """Parse a numeric string, removing commas and whitespace."""
    s = s.replace(",", "").replace(" ", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_currency(s: str) -> float | None:
    """Parse a currency value like '$1,234,567' or '(1,234)' for negatives."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    s = s.replace("$", "").replace(",", "").replace(" ", "").strip()
    if not s:
        return None
    try:
        val = float(s)
        return -val if negative else val
    except ValueError:
        return None
