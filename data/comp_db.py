"""
Historical Comp Database — SQLite storage for prior CIM analysis results.

After each CIM is analyzed, key metrics are saved here. Future CIMs can
query this database for expense/SF, revenue/SF, and rent/SF benchmarks
from actual deals — progressively replacing the static national benchmarks
in config.py as the database grows.

Tier 3 in the data sourcing hierarchy:
  Tier 1: CIM extraction + JSON overrides
  Tier 2: External APIs (Census, geocoding)
  Tier 3: This comp database
  Tier 4: Static config.py benchmarks (national/regional)
"""

import os
import sqlite3
import math
from datetime import datetime
from typing import Optional

from config import COMP_DB_PATH, COMP_DB_MIN_COMPS, COMP_DB_NRSF_RANGE
from registry import STANDARD_SIZE_BUCKETS, SIZE_BUCKET_TOLERANCE


# ── Size Bucket Normalization ─────────────────────────────────────

def normalize_size_bucket(sf: float) -> str:
    """Map a unit SF to the nearest standard size bucket label."""
    if sf is None or sf <= 0:
        return "other"

    best_label = "other"
    best_diff = float("inf")

    for label, standard_sf in STANDARD_SIZE_BUCKETS.items():
        diff_pct = abs(sf - standard_sf) / standard_sf
        if diff_pct <= SIZE_BUCKET_TOLERANCE and diff_pct < best_diff:
            best_diff = diff_pct
            best_label = label

    return best_label


# ── Database Manager ──────────────────────────────────────────────

class CompDatabase:
    """SQLite database for historical self-storage comp data."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or COMP_DB_PATH
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist."""
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS properties (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    -- Identity
                    property_name TEXT,
                    address TEXT,
                    city TEXT,
                    state TEXT,
                    msa TEXT,
                    zip_code TEXT,
                    lat REAL,
                    lon REAL,
                    -- Physical
                    year_built INTEGER,
                    nrsf REAL,
                    total_units INTEGER,
                    cc_pct REAL,
                    acreage REAL,
                    occupancy REAL,
                    -- Pricing
                    asking_price REAL,
                    price_per_sf REAL,
                    -- Demographics
                    population_1mi INTEGER,
                    population_3mi INTEGER,
                    population_5mi INTEGER,
                    median_hhi_3mi REAL,
                    -- Financials
                    ttm_gpr REAL,
                    ttm_egr REAL,
                    ttm_noi REAL,
                    adjusted_noi REAL,
                    revenue_per_sf REAL,
                    noi_per_sf REAL,
                    opex_ratio REAL,
                    -- Market
                    market_rent_psf REAL,
                    in_place_rent_psf REAL,
                    -- Meta
                    pdf_filename TEXT UNIQUE,
                    analysis_date TEXT
                );

                CREATE TABLE IF NOT EXISTS expense_lines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    property_id INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    cim_value REAL,
                    adjusted_value REAL,
                    per_nrsf REAL,
                    adjusted_per_nrsf REAL,
                    flag TEXT,
                    FOREIGN KEY (property_id) REFERENCES properties(id)
                );

                CREATE TABLE IF NOT EXISTS unit_mix (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    property_id INTEGER NOT NULL,
                    size_label TEXT,
                    unit_sf REAL,
                    count INTEGER,
                    monthly_rate REAL,
                    rate_per_sf_mo REAL,
                    climate_controlled INTEGER,
                    size_bucket TEXT,
                    FOREIGN KEY (property_id) REFERENCES properties(id)
                );

                CREATE TABLE IF NOT EXISTS data_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    property_id INTEGER NOT NULL,
                    field_name TEXT NOT NULL,
                    tier INTEGER NOT NULL,
                    source_detail TEXT,
                    value_used TEXT,
                    FOREIGN KEY (property_id) REFERENCES properties(id)
                );

                CREATE INDEX IF NOT EXISTS idx_properties_state ON properties(state);
                CREATE INDEX IF NOT EXISTS idx_properties_msa ON properties(msa);
                CREATE INDEX IF NOT EXISTS idx_expense_lines_property ON expense_lines(property_id);
                CREATE INDEX IF NOT EXISTS idx_expense_lines_category ON expense_lines(category);
                CREATE INDEX IF NOT EXISTS idx_unit_mix_property ON unit_mix(property_id);
                CREATE INDEX IF NOT EXISTS idx_unit_mix_bucket ON unit_mix(size_bucket);
            """)

    def _connect(self):
        return sqlite3.connect(self.db_path)

    # ── Save ──────────────────────────────────────────────────────

    def save_analysis(self, cim_data, financial_analysis: dict,
                      rent_analysis: dict, pdf_filename: str,
                      source_log: dict = None):
        """
        Save a completed CIM analysis to the database.

        If a record with the same pdf_filename already exists, it is
        replaced (upsert behavior).
        """
        nrsf = cim_data.nrsf or 0
        adjusted_noi_dict = financial_analysis.get("adjusted_ttm_noi", {})
        adjusted_noi = adjusted_noi_dict.get("analyst_adjusted_noi")
        expense_check = financial_analysis.get("expense_ratio_check", {})

        # Compute revenue_per_sf and noi_per_sf
        revenue_per_sf = None
        if cim_data.ttm_total_revenue and nrsf > 0:
            revenue_per_sf = cim_data.ttm_total_revenue / nrsf
        elif cim_data.ttm_egr and nrsf > 0:
            revenue_per_sf = cim_data.ttm_egr / nrsf

        noi_per_sf = adjusted_noi / nrsf if (adjusted_noi and nrsf > 0) else None

        # In-place rent per SF from rent analysis
        in_place_rent = rent_analysis.get("weighted_avg_rent_per_sf_mo")
        if in_place_rent is None:
            rent_est = rent_analysis.get("rent_per_sf", {})
            if rent_est and rent_est.get("estimated"):
                in_place_rent = rent_est.get("monthly_per_sf")

        with self._connect() as conn:
            # Delete existing record for this PDF (upsert)
            conn.execute("DELETE FROM properties WHERE pdf_filename = ?",
                         (pdf_filename,))

            # Insert property
            conn.execute("""
                INSERT INTO properties (
                    property_name, address, city, state, msa, zip_code,
                    year_built, nrsf, total_units, cc_pct, acreage, occupancy,
                    asking_price, price_per_sf,
                    population_1mi, population_3mi, population_5mi, median_hhi_3mi,
                    ttm_gpr, ttm_egr, ttm_noi, adjusted_noi,
                    revenue_per_sf, noi_per_sf, opex_ratio,
                    market_rent_psf, in_place_rent_psf,
                    pdf_filename, analysis_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                cim_data.property_name, cim_data.address,
                cim_data.city, cim_data.state, cim_data.msa, None,
                cim_data.year_built, nrsf, cim_data.total_units,
                cim_data.cc_pct, cim_data.acreage,
                cim_data.physical_occupancy,
                cim_data.asking_price, cim_data.price_per_sf,
                cim_data.population_1mi, cim_data.population_3mi,
                cim_data.population_5mi, cim_data.median_hhi_3mi,
                cim_data.ttm_gpr, cim_data.ttm_egr, cim_data.ttm_noi,
                adjusted_noi, revenue_per_sf, noi_per_sf,
                expense_check.get("opex_revenue_ratio"),
                cim_data.market_rent_psf, in_place_rent,
                pdf_filename, datetime.now().isoformat(),
            ))

            property_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            # Save expense lines
            expense_analysis = financial_analysis.get("expense_analysis", {})
            for line in expense_analysis.get("lines", []):
                adj_per_nrsf = line["adjusted_value"] / nrsf if (
                    line.get("adjusted_value") and nrsf > 0) else None
                conn.execute("""
                    INSERT INTO expense_lines (
                        property_id, category, cim_value, adjusted_value,
                        per_nrsf, adjusted_per_nrsf, flag
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    property_id, line["category"],
                    line.get("cim_value"), line.get("adjusted_value"),
                    line.get("per_nrsf"), adj_per_nrsf,
                    line.get("flag"),
                ))

            # Save unit mix
            for unit_info in rent_analysis.get("unit_mix_summary", []):
                sf = unit_info.get("unit_sf")
                bucket = normalize_size_bucket(sf) if sf else "other"
                conn.execute("""
                    INSERT INTO unit_mix (
                        property_id, size_label, unit_sf, count,
                        monthly_rate, rate_per_sf_mo, climate_controlled,
                        size_bucket
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    property_id, unit_info.get("size_label"),
                    sf, unit_info.get("count"),
                    unit_info.get("monthly_rate"),
                    unit_info.get("rate_per_sf"),
                    1 if unit_info.get("climate_controlled") else 0,
                    bucket,
                ))

            # Save data source audit trail
            if source_log:
                for field_name, info in source_log.items():
                    tier = info.get("tier", 1)
                    source_detail = info.get("source", "")
                    value = str(info.get("value", ""))
                    conn.execute("""
                        INSERT INTO data_sources (
                            property_id, field_name, tier, source_detail, value_used
                        ) VALUES (?, ?, ?, ?, ?)
                    """, (property_id, field_name, tier, source_detail, value))

            conn.commit()

        return property_id

    # ── Expense Benchmark Queries ─────────────────────────────────

    def query_expense_benchmarks(self, state: str = None, msa: str = None,
                                 nrsf: float = None, cc_pct: float = None,
                                 min_comps: int = None) -> Optional[dict]:
        """
        Query historical expense data for benchmarking.

        Returns 25th/50th/75th percentile adjusted_per_nrsf per category,
        or None if fewer than min_comps properties match.
        """
        min_comps = min_comps or COMP_DB_MIN_COMPS

        # Build property filter
        where_clauses = []
        params = []

        if state:
            where_clauses.append("p.state = ?")
            params.append(state.upper())

        if nrsf:
            low_mult, high_mult = COMP_DB_NRSF_RANGE
            where_clauses.append("p.nrsf BETWEEN ? AND ?")
            params.extend([nrsf * low_mult, nrsf * high_mult])

        if cc_pct is not None:
            where_clauses.append("p.cc_pct BETWEEN ? AND ?")
            params.extend([max(0, cc_pct - 0.15), min(1.0, cc_pct + 0.15)])

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        with self._connect() as conn:
            # Check comp count
            count = conn.execute(
                f"SELECT COUNT(DISTINCT p.id) FROM properties p WHERE {where_sql}",
                params
            ).fetchone()[0]

            if count < min_comps:
                return None

            # Query expense lines joined to matching properties
            rows = conn.execute(f"""
                SELECT e.category, e.adjusted_per_nrsf
                FROM expense_lines e
                JOIN properties p ON e.property_id = p.id
                WHERE {where_sql} AND e.adjusted_per_nrsf IS NOT NULL
                ORDER BY e.category, e.adjusted_per_nrsf
            """, params).fetchall()

        # Group by category and compute percentiles
        from collections import defaultdict
        by_category = defaultdict(list)
        for category, value in rows:
            by_category[category].append(value)

        result = {
            "comp_count": count,
            "categories": {},
        }

        for category, values in by_category.items():
            if len(values) < min_comps:
                continue
            result["categories"][category] = {
                "p25": _percentile(values, 25),
                "p50": _percentile(values, 50),
                "p75": _percentile(values, 75),
                "min": min(values),
                "max": max(values),
                "count": len(values),
            }

        return result if result["categories"] else None

    # ── Rent Comp Queries ─────────────────────────────────────────

    def query_rent_comps(self, state: str = None, msa: str = None,
                         size_bucket: str = None,
                         climate_controlled: bool = None,
                         min_comps: int = None) -> Optional[dict]:
        """
        Query historical rent data by unit type.

        Returns avg/low/high rent_per_sf_mo, or None if too few comps.
        """
        min_comps = min_comps or COMP_DB_MIN_COMPS

        where_clauses = []
        params = []

        if state:
            where_clauses.append("p.state = ?")
            params.append(state.upper())

        if size_bucket:
            where_clauses.append("u.size_bucket = ?")
            params.append(size_bucket)

        if climate_controlled is not None:
            where_clauses.append("u.climate_controlled = ?")
            params.append(1 if climate_controlled else 0)

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        with self._connect() as conn:
            rows = conn.execute(f"""
                SELECT u.rate_per_sf_mo, u.size_bucket, u.climate_controlled,
                       p.property_name, p.state
                FROM unit_mix u
                JOIN properties p ON u.property_id = p.id
                WHERE {where_sql} AND u.rate_per_sf_mo IS NOT NULL
                ORDER BY u.rate_per_sf_mo
            """, params).fetchall()

        if len(rows) < min_comps:
            return None

        rates = [r[0] for r in rows]

        return {
            "comp_count": len(rates),
            "avg_rent_per_sf_mo": sum(rates) / len(rates),
            "p25": _percentile(rates, 25),
            "p50": _percentile(rates, 50),
            "p75": _percentile(rates, 75),
            "min": min(rates),
            "max": max(rates),
        }

    # ── Revenue Benchmarks ────────────────────────────────────────

    def query_revenue_benchmarks(self, state: str = None,
                                 nrsf: float = None,
                                 min_comps: int = None) -> Optional[dict]:
        """
        Query historical revenue/SF data.

        Returns avg/percentile revenue_per_sf, or None if too few comps.
        """
        min_comps = min_comps or COMP_DB_MIN_COMPS

        where_clauses = ["revenue_per_sf IS NOT NULL"]
        params = []

        if state:
            where_clauses.append("state = ?")
            params.append(state.upper())

        if nrsf:
            low_mult, high_mult = COMP_DB_NRSF_RANGE
            where_clauses.append("nrsf BETWEEN ? AND ?")
            params.extend([nrsf * low_mult, nrsf * high_mult])

        where_sql = " AND ".join(where_clauses)

        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT revenue_per_sf FROM properties WHERE {where_sql}",
                params
            ).fetchall()

        if len(rows) < min_comps:
            return None

        values = [r[0] for r in rows]

        return {
            "comp_count": len(values),
            "avg": sum(values) / len(values),
            "p25": _percentile(values, 25),
            "p50": _percentile(values, 50),
            "p75": _percentile(values, 75),
            "min": min(values),
            "max": max(values),
        }

    # ── Summary / Info ────────────────────────────────────────────

    def find_duplicates(self, filename: str = None,
                        property_name: str = None) -> list[dict]:
        """Search for existing records matching a filename or property name.

        Returns list of matching property dicts (may be empty).
        """
        results = []
        with self._connect() as conn:
            if filename:
                rows = conn.execute(
                    "SELECT property_name, city, state, analysis_date, pdf_filename "
                    "FROM properties WHERE pdf_filename = ?",
                    (filename,)
                ).fetchall()
                for r in rows:
                    results.append({
                        "property_name": r[0], "city": r[1], "state": r[2],
                        "analysis_date": r[3], "pdf_filename": r[4],
                        "match_type": "filename",
                    })

            if property_name:
                # Fuzzy match: check if the property name appears as a substring
                rows = conn.execute(
                    "SELECT property_name, city, state, analysis_date, pdf_filename "
                    "FROM properties WHERE LOWER(property_name) LIKE ?",
                    (f"%{property_name.lower()}%",)
                ).fetchall()
                for r in rows:
                    # Avoid duplicating filename matches
                    if not any(d["pdf_filename"] == r[4] and d["match_type"] == "filename"
                               for d in results):
                        results.append({
                            "property_name": r[0], "city": r[1], "state": r[2],
                            "analysis_date": r[3], "pdf_filename": r[4],
                            "match_type": "name",
                        })
        return results

    def get_comp_count(self) -> int:
        """Return total number of properties in database."""
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]

    def get_comp_summary(self) -> list[dict]:
        """Return a summary of all properties in the database."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT property_name, city, state, nrsf, total_units,
                       occupancy, adjusted_noi, revenue_per_sf, noi_per_sf,
                       analysis_date, pdf_filename
                FROM properties
                ORDER BY analysis_date DESC
            """).fetchall()

        return [
            {
                "property_name": r[0],
                "city": r[1],
                "state": r[2],
                "nrsf": r[3],
                "total_units": r[4],
                "occupancy": r[5],
                "adjusted_noi": r[6],
                "revenue_per_sf": r[7],
                "noi_per_sf": r[8],
                "analysis_date": r[9],
                "pdf_filename": r[10],
            }
            for r in rows
        ]


# ── Percentile Helper ─────────────────────────────────────────────

def _percentile(sorted_values: list, pct: int) -> float:
    """
    Compute percentile from a sorted list using linear interpolation.
    SQLite lacks native PERCENTILE, so we compute in Python.
    """
    if not sorted_values:
        return 0.0
    values = sorted(sorted_values)
    n = len(values)
    if n == 1:
        return values[0]
    k = (pct / 100.0) * (n - 1)
    floor_k = int(math.floor(k))
    ceil_k = min(floor_k + 1, n - 1)
    frac = k - floor_k
    return values[floor_k] + frac * (values[ceil_k] - values[floor_k])
