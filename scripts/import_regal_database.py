#!/usr/bin/env python3
"""
Import the BCRE/Regal self-storage market database into the comp database.

The source workbook ("BCRE REGAL FULL SELF STORAGE DATABASE.xlsx") is a
Texas-focused market database compiled by Regal. This importer maps its
County Appraisal District (CAD) sheets and its unit-rate sheets into the same
SQLite schema that ``data/comp_db.py`` maintains, so the normal benchmark
queries (rent/sf by size bucket + climate, revenue/sf, expense/sf) see
hundreds of real facilities instead of only the handful produced by prior
CIM analyses.

What is imported
    properties   - facilities carrying a usable NRSF + location/valuation,
                   from the richest CAD sheets (Travis/TCadSqFt,
                   Bexar/BcadNRSF, Williamson/Wcad, Harris/hcad).
    unit_mix     - per-size asking rents (climate vs non-climate) from the two
                   sheets that carry unit-rate columns (TCadSqFt, BcadNRSF).
    data_sources - provenance rows (tier=3, source_detail="BCRE Regal DB:<sheet>").

Rows are tagged with ``pdf_filename = "[regal:<sheet>:<source_id>]"`` so they
can be upserted idempotently on re-run and swept clean with ``--reset`` without
touching rows that came from real CIM analyses.

Usage
    python scripts/import_regal_database.py                # upsert into data/cim_comps.db
    python scripts/import_regal_database.py --file X.xlsx  # a different workbook
    python scripts/import_regal_database.py --db /path.db  # a different comp DB
    python scripts/import_regal_database.py --dry-run      # report only, no writes
    python scripts/import_regal_database.py --reset        # drop prior regal rows first
"""

import argparse
import os
import re
import sqlite3
import sys

# Make repo-root modules importable when run as `python scripts/....py`.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import openpyxl
except ImportError:  # pragma: no cover - dependency guard
    sys.exit("Missing dependency: pip install openpyxl")

from config import COMP_DB_PATH  # noqa: E402
from data.comp_db import normalize_size_bucket  # noqa: E402


# ── Unit-column parsing ────────────────────────────────────────────────

_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+)")
_NONCLIMATE_RE = re.compile(
    r"\bnon[- ]climate\b|\bstd\b|\boutside\b|ncco|ncci|\bparking\b",
    re.IGNORECASE,
)
_CLIMATE_RE = re.compile(r"\bcc\b|\bup\b|\bccd\b|\bccu\b|\bhcc\b|\bclimate\b", re.IGNORECASE)


def _fmt(w: float) -> str:
    return f"{w:g}"


def extract_unit_columns(sheet, header_rows: int):
    """Scan a sheet's header rows and return [(col_idx, size_label, sf, climate)]."""
    header = list(sheet.iter_rows(min_row=1, max_row=header_rows, values_only=True))
    if not header:
        return []
    ncols = max(len(r) for r in header)
    out = []
    for c in range(ncols):
        cells = [
            str(header[r][c]).strip()
            for r in range(len(header))
            if c < len(header[r]) and header[r][c] not in (None, "")
        ]
        combined = " / ".join(cells)
        m = _SIZE_RE.search(combined)
        if not m:
            continue
        sf = int(round(float(m.group(1)) * float(m.group(2))))
        if not (20 <= sf <= 1000):
            continue
        low = combined.lower()
        if _NONCLIMATE_RE.search(low):
            climate = 0
        elif _CLIMATE_RE.search(low):
            climate = 1
        else:
            climate = 0  # no climate keyword -> treat as standard/outside
        label = f"{_fmt(float(m.group(1)))}x{_fmt(float(m.group(2)))}"
        out.append((c, label, sf, climate))
    return out


# ── Scalar coercion ────────────────────────────────────────────────────

_ERROR_TOKENS = {"none", "nan", "null", "n/a", "site", "unknown",
                 "#num!", "#ref!", "#value!", "#div/0!", "#n/a"}


def to_float(v):
    """Best-effort float conversion; returns None for blanks / CAD error cells."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if f == f else None  # drop NaN
    s = str(v).strip().replace(",", "").replace("$", "").replace("%", "")
    if not s or s.lower() in _ERROR_TOKENS:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if f == f else None


def to_int(v):
    f = to_float(v)
    if f is None:
        return None
    return int(round(f))


def split_latlon(v):
    """Parse '30.659, -97.874' style cells into (lat, lon) or (None, None)."""
    if v is None:
        return None, None
    parts = re.split(r"[,\s]+", str(v).strip())
    nums = [n for n in (to_float(p) for p in parts) if n is not None]
    if len(nums) >= 2:
        return nums[0], nums[1]
    return None, None


# ── Sheet layouts (CAD sheets rich enough to become properties) ────────

REGAL_PREFIX = "[regal:"
# Match imported rows by their literal "[regal:" prefix. SQLite LIKE treats
# '[' as an ordinary literal here (not a character-class wildcard), but we
# compare with substr() anyway so the sweep never depends on LIKE bracket
# semantics that differ across SQLite builds.
REGAL_PREFIX_LEN = len(REGAL_PREFIX)  # len("[regal:") == 7

PROPERTY_SHEETS = {
    "TCadSqFt": {  # Travis County (Austin)
        "key": "PropertyID",
        "header_rows": 1,
        "fields": {
            "name": "BusinessName",
            "address": "StreetAddress",
            "city": "City",
            "state": "State",
            "zip_code": "ZipCode",
            "year_built": "YearBuilt",
            "acreage": "Acres",
            "nrsf": "NRSF",
            "price_per_sf": "AppValPerSqFt",
            "lat": "Latitude",
            "lon": "Longitude",
        },
    },
    "BcadNRSF": {  # Bexar County (San Antonio)
        "key": "PropertyID",
        "header_rows": 3,
        "fields": {
            "name": "DBA",
            "address": "Property Address",
            "zip_code": "Zip Code",
            "year_built": "Year Built",
            "acreage": "Land, Acres",
            "nrsf": "NRSF",
            "lat": "LAT",
            "lon": "LONG",
        },
        "state": "TX",
    },
    "Wcad": {  # Williamson County
        "key": "PropertyQuickRefID",
        "header_rows": 1,
        "fields": {
            "name": "DBA",
            "name_fallback": "OwnerName",
            "address": "SitusAddress",
            "year_built": "YearBuilt",
            "acreage": "Acres",
            "nrsf": "Bldg SF",
            "appr_val": "2015AppVal",
            "latlon": "Lat, Long",
        },
        "state": "TX",
    },
    "hcad": {  # Harris County (Houston)
        "key": "AccountNumber",
        "header_rows": 1,
        "fields": {
            "name": "DBA",
            "name_fallback": "Owner Name",
            "address": "PropertyAddress",
            "zip_code": "Zip",
            "year_built": "Built",
            "nrsf": "BldgSqFt",
            "price_per_sf": "AppValPSF",
            "appr_val": "MktValHCAD",
        },
        "state": "TX",
    },
}

UNIT_SHEETS = {"TCadSqFt", "BcadNRSF"}


def _header_index(header_row):
    index = {}
    for i, v in enumerate(header_row):
        if v in (None, ""):
            continue
        name = str(v).strip().lower()
        if name and name not in index:
            index[name] = i
    return index



# ── Database helpers ───────────────────────────────────────────────────

def _now():
    from datetime import datetime
    return datetime.now().isoformat()


def _insert_property(conn, sheet, key, row, header, sheet_cfg, unit_columns):
    """Upsert one facility. Returns (property_id, unit_rows) or None to skip."""
    def col(field):
        name = sheet_cfg["fields"].get(field)
        return header.get(name.lower()) if name else None

    def cell(field):
        i = col(field)
        return row[i] if i is not None and i < len(row) else None

    nrsf = to_float(cell("nrsf"))
    if not nrsf or nrsf <= 0:
        return None  # only import facilities with a usable square-footage

    name = cell("name") or cell("name_fallback") or sheet
    state = cell("state") or sheet_cfg.get("state", "TX")
    state = str(state).strip().upper()[:2] if state else "TX"

    lat = lon = None
    if col("latlon") is not None:
        lat, lon = split_latlon(cell("latlon"))
    if col("lat") is not None:
        lat = to_float(cell("lat")) or lat
    if col("lon") is not None:
        lon = to_float(cell("lon")) or lon

    price_per_sf = to_float(cell("price_per_sf"))
    appr_val = to_float(cell("appr_val"))
    if price_per_sf is None and appr_val and nrsf:
        price_per_sf = appr_val / nrsf

    filename = f"{REGAL_PREFIX}{sheet}:{key}].xlsx"

    # Clear the prior version of this imported row (idempotent upsert).
    old_id = conn.execute(
        "SELECT id FROM properties WHERE pdf_filename = ?", (filename,)
    ).fetchone()
    old_id = old_id[0] if old_id else None
    if old_id:
        for tbl in ("data_sources", "unit_mix", "expense_lines"):
            conn.execute(f"DELETE FROM {tbl} WHERE property_id = ?", (old_id,))
        conn.execute("DELETE FROM properties WHERE id = ?", (old_id,))

    conn.execute(
        """
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
        """,
        (
            str(name), cell("address"), cell("city"), state, None,
            cell("zip_code"), to_int(cell("year_built")), nrsf, None, None,
            to_float(cell("acreage")), None,
            None, price_per_sf,
            None, None, None, None,
            None, None, None, None,
            None, None, None,
            None, None,
            filename,
            _now(),
        ),
    )
    property_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Provenance rows for the fields that actually moved a number.
    sources = [
        ("nrsf", nrsf),
        ("year_built", to_int(cell("year_built"))),
        ("acreage", to_float(cell("acreage"))),
        ("price_per_sf", price_per_sf),
        ("state", state),
    ]
    if lat is not None and lon is not None:
        sources.append(("lat_lon", f"{lat},{lon}"))
    for field, value in sources:
        if value in (None, ""):
            continue
        conn.execute(
            "INSERT INTO data_sources (property_id, field_name, tier, source_detail, value_used) "
            "VALUES (?, ?, ?, ?, ?)",
            (property_id, field, 3, f"BCRE Regal DB ({sheet})", str(value)),
        )

    # Unit rents (climate vs non-climate) for the sheets that carry them.
    unit_rows = 0
    if sheet in UNIT_SHEETS:
        for col_idx, label, sf, climate in unit_columns:
            if col_idx >= len(row):
                continue
            price = to_float(row[col_idx])
            if price is None or price <= 0:
                continue
            conn.execute(
                "INSERT INTO unit_mix (property_id, size_label, unit_sf, count, "
                "monthly_rate, rate_per_sf_mo, climate_controlled, size_bucket) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (property_id, label, sf, None, price,
                 price / sf, climate, normalize_size_bucket(sf)),
            )
            unit_rows += 1
        conn.execute(
            "INSERT INTO data_sources (property_id, field_name, tier, source_detail, value_used) "
            "VALUES (?, ?, ?, ?, ?)",
            (property_id, "unit_mix", 3, f"BCRE Regal DB ({sheet})", str(unit_rows)),
        )

    return property_id, unit_rows


def _clear_regal_rows(conn):
    rows = conn.execute(
        "SELECT id FROM properties "
        "WHERE substr(pdf_filename, 1, ?) = ?",
        (REGAL_PREFIX_LEN, REGAL_PREFIX),
    ).fetchall()
    ids = [r[0] for r in rows]
    for pid in ids:
        for tbl in ("data_sources", "unit_mix", "expense_lines"):
            conn.execute(f"DELETE FROM {tbl} WHERE property_id = ?", (pid,))
    conn.execute(
        "DELETE FROM properties WHERE substr(pdf_filename, 1, ?) = ?",
        (REGAL_PREFIX_LEN, REGAL_PREFIX),
    )
    return len(ids)



# ── Main ───────────────────────────────────────────────────────────────

def run_import(workbook_path, db_path, dry_run=False, reset=False):
    wb = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)
    conn = sqlite3.connect(db_path)
    try:
        stats = {"properties": 0, "units": 0, "skipped_no_nrsf": 0, "sheets": {}}
        if reset:
            stats["cleared"] = _clear_regal_rows(conn)
            if not dry_run:
                conn.commit()

        for sheet_name, cfg in PROPERTY_SHEETS.items():
            if sheet_name not in wb.sheetnames:
                continue
            ws = wb[sheet_name]
            rows = ws.iter_rows(values_only=True)
            header_row = next(rows)
            header = _header_index(header_row)
            # Consume the remaining header rows (e.g. BcadNRSF's 3-level header).
            for _ in range(cfg["header_rows"] - 1):
                next(rows, None)
            unit_columns = extract_unit_columns(ws, cfg["header_rows"])
            sheet_props = sheet_units = 0
            for row in rows:
                key_col = header.get(str(cfg["key"]).lower())
                if key_col is None or key_col >= len(row):
                    continue
                key = row[key_col]
                if key in (None, ""):
                    continue
                result = _insert_property(
                    conn, sheet_name, key, row, header, cfg, unit_columns,
                )
                if result is None:
                    stats["skipped_no_nrsf"] += 1
                    continue
                sheet_props += 1
                sheet_units += result[1]
                if not dry_run:
                    conn.commit()
            stats["properties"] += sheet_props
            stats["units"] += sheet_units
            stats["sheets"][sheet_name] = {"properties": sheet_props, "units": sheet_units}
        return stats
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import the BCRE/Regal self-storage database.")
    parser.add_argument("--file", default="BCRE REGAL FULL SELF STORAGE DATABASE.xlsx",
                        help="Path to the workbook (default: repo-root BCRE REGAL DB).")
    parser.add_argument("--db", default=COMP_DB_PATH, help="Path to the comp SQLite DB.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be imported without writing.")
    parser.add_argument("--reset", action="store_true",
                        help="Drop prior [regal: rows before importing.")
    args = parser.parse_args(argv)

    if not os.path.exists(args.file):
        sys.exit(f"Workbook not found: {args.file}")

    if args.dry_run:
        tmp = args.db + ".dryrun"
        import shutil
        try:
            shutil.copyfile(args.db, tmp)
        except OSError as exc:
            sys.exit(f"Cannot create dry-run copy of {args.db}: {exc}")
        stats = run_import(args.file, tmp, dry_run=True, reset=args.reset)
        os.remove(tmp)
    else:
        stats = run_import(args.file, args.db, dry_run=False, reset=args.reset)

    print(f"Regal DB import into: {args.db}")
    if stats.get("cleared"):
        print(f"  cleared prior regal rows: {stats['cleared']}")
    for sheet, s in stats["sheets"].items():
        print(f"  {sheet:10s} properties={s['properties']:<4d} unit_rows={s['units']}")
    print(f"  TOTAL properties={stats['properties']} unit_rows={stats['units']} "
          f"skipped_no_nrsf={stats['skipped_no_nrsf']}")
    print("  dry-run (no writes)" if args.dry_run else "  committed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

