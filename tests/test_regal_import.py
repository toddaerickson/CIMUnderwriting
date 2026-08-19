"""Tests for `scripts/import_regal_database.py` — the Regal/CAD comp seeder.

This file is the importer's FIRST test coverage. It had none, while README
documents running it as the official "Comp Database Seeding" procedure, so the
defects it pins were shipped and live.

The workbooks here are synthetic and tiny. The real one is 2.9 MB and is not in
git, so a test that read it would be unrunnable in CI and slow everywhere else;
what is under test is the mapping and the write, not that particular file.
"""

import sqlite3

import openpyxl
import pytest

from data.comp_db import CompDatabase
from scripts.import_regal_database import (
    SHEET_VINTAGE,
    ImportError_,
    run_import,
)


# ── Synthetic workbook builders ────────────────────────────────────────

TCAD_HEADER = [
    "PropertyID", "BusinessName", "StreetAddress", "City", "State", "ZipCode",
    "YearBuilt", "Acres", "NRSF", "AppValPerSqFt", "Latitude", "Longitude",
]

TCAD_ROW = [
    "R12345", "Austin Self Storage", "100 Congress Ave", "Austin", "TX", "78701",
    1998, 3.2, 62_000, 41.5, 30.2672, -97.7431,
]


def _write_sheet(wb, title, rows, first=False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    for row in rows:
        ws.append(row)
    return ws


def make_workbook(tmp_path, name="regal.xlsx", tcad_rows=None, header=None,
                  extra=None):
    """A workbook with one usable TCadSqFt sheet (plus optional extra sheets).

    `extra` is a list of (sheet_name, rows) appended after it. Sheets not named
    in PROPERTY_SHEETS are ignored by the importer; sheets named there but
    absent from the workbook are reported as missing, not failed.
    """
    wb = openpyxl.Workbook()
    rows = [header or TCAD_HEADER] + (tcad_rows if tcad_rows is not None
                                      else [TCAD_ROW])
    _write_sheet(wb, "TCadSqFt", rows, first=True)
    for sheet_name, sheet_rows in (extra or []):
        _write_sheet(wb, sheet_name, sheet_rows)
    path = tmp_path / name
    wb.save(path)
    return str(path)


@pytest.fixture
def db(tmp_path):
    """An initialized, empty comp DB — schema only, no rows."""
    path = str(tmp_path / "comps.db")
    CompDatabase(path)  # creates the schema; the importer never creates it
    return path


def rows_of(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def dump(db_path):
    """Every row of every table, for equality comparison across a failed run."""
    return {
        table: rows_of(db_path, f"SELECT * FROM {table} ORDER BY id")
        for table in ("properties", "unit_mix", "expense_lines", "data_sources")
    }


# ── Vintage ────────────────────────────────────────────────────────────

def test_analysis_date_is_the_sheet_vintage_not_the_import_time(tmp_path, db):
    """The load-bearing fix.

    Nothing downstream filters comps by age and `get_comp_summary` orders by
    `analysis_date DESC`, so a 2015 CAD row stamped `datetime.now()` sorts ABOVE
    a real analysis from last month and reads as current.
    """
    run_import(make_workbook(tmp_path), db)

    (date,), = rows_of(db, "SELECT analysis_date FROM properties")
    assert date == SHEET_VINTAGE["TCadSqFt"][0] == "2015-01-01"


def test_source_detail_states_the_vintage_and_its_basis(tmp_path, db):
    run_import(make_workbook(tmp_path), db)

    details = {d for (d,) in rows_of(db, "SELECT DISTINCT source_detail FROM data_sources")}
    assert len(details) == 1
    detail = details.pop()
    assert "vintage 2015" in detail
    # The basis travels with the year: a reader must be able to tell a year read
    # off a dated column from one inferred as an upper bound.
    assert "2015 appraisal column" in detail


def test_every_configured_sheet_has_a_declared_vintage():
    """A new sheet mapping without a vintage would KeyError mid-import.

    Better to fail here, at the declaration, than at row 40,000 of a real run.
    """
    from scripts.import_regal_database import PROPERTY_SHEETS

    assert set(PROPERTY_SHEETS) == set(SHEET_VINTAGE)


# ── Rents are never seeded ─────────────────────────────────────────────

def test_no_unit_mix_rows_are_written(tmp_path, db):
    """The whole rent-seeding path is deleted, not disabled.

    Seeding it made 158 of 166 TX rent comps one decade-old portfolio presenting
    as current, moving a $1.20/SF subject's Tier-3 rent gap from +12.1% to +9.0%.
    """
    stats = run_import(make_workbook(tmp_path), db)

    assert rows_of(db, "SELECT COUNT(*) FROM unit_mix") == [(0,)]
    assert "units" not in stats


def test_unit_rate_columns_are_ignored_even_when_present(tmp_path, db):
    """The real workbook's sheets carry `10x10`-style rate columns.

    Their presence must not resurrect the path: mapping is by configured field
    name, so an unmapped column is data the importer never looks at.
    """
    header = TCAD_HEADER + ["10x10", "10x20 CC", "5x5 Drive Up"]
    path = make_workbook(tmp_path, header=header,
                         tcad_rows=[TCAD_ROW + [95.0, 180.0, 45.0]])
    run_import(path, db)

    assert rows_of(db, "SELECT COUNT(*) FROM unit_mix") == [(0,)]


# ── price_per_sf semantics ─────────────────────────────────────────────

def test_appraised_value_never_lands_in_price_per_sf(tmp_path, db):
    """`properties.price_per_sf` is broker ASKING price / NRSF, filled by
    `save_analysis`. A CAD tax appraisal is a different quantity; blending the
    two would silently corrupt any future price benchmark."""
    run_import(make_workbook(tmp_path), db)

    assert rows_of(db, "SELECT price_per_sf, asking_price FROM properties") == [(None, None)]

    # The figure is not discarded — it is recorded under its own name.
    assert rows_of(
        db,
        "SELECT value_used FROM data_sources WHERE field_name = 'cad_appraised_per_sf'",
    ) == [("41.5",)]


def test_appraised_per_sf_is_derived_when_only_a_total_value_is_mapped(tmp_path, db):
    """Wcad/hcad map a total appraised value (`appr_val`), not a per-SF one."""
    wcad_header = ["PropertyQuickRefID", "DBA", "OwnerName", "SitusAddress",
                   "YearBuilt", "Acres", "Bldg SF", "2015AppVal", "Lat, Long"]
    wcad_row = ["W900", "Round Rock Storage", "RRS LLC", "1 Main St",
                2004, 2.0, 50_000, 2_000_000, "30.5083, -97.6789"]
    path = make_workbook(tmp_path, extra=[("Wcad", [wcad_header, wcad_row])])
    run_import(path, db)

    (value,), = rows_of(
        db,
        "SELECT value_used FROM data_sources d JOIN properties p ON p.id = d.property_id "
        "WHERE d.field_name = 'cad_appraised_per_sf' AND p.property_name = ?",
        ("Round Rock Storage",),
    )
    assert float(value) == pytest.approx(40.0)  # 2,000,000 / 50,000


# ── Geography ──────────────────────────────────────────────────────────

def test_lat_lon_are_persisted(tmp_path, db):
    """They were resolved and then omitted from the INSERT column list, so every
    imported property was invisible to any radius-based selection."""
    run_import(make_workbook(tmp_path), db)

    (lat, lon), = rows_of(db, "SELECT lat, lon FROM properties")
    assert (lat, lon) == pytest.approx((30.2672, -97.7431))


def test_a_combined_lat_long_cell_is_split(tmp_path, db):
    wcad_header = ["PropertyQuickRefID", "DBA", "SitusAddress", "Bldg SF", "Lat, Long"]
    wcad_row = ["W901", "Georgetown Storage", "2 Main St", 40_000, "30.6333, -97.6772"]
    path = make_workbook(tmp_path, extra=[("Wcad", [wcad_header, wcad_row])])
    run_import(path, db)

    (lat, lon), = rows_of(db, "SELECT lat, lon FROM properties WHERE property_name = ?",
                          ("Georgetown Storage",))
    assert (lat, lon) == pytest.approx((30.6333, -97.6772))


# ── Loud failure instead of a silent zero-row import ───────────────────

def test_unresolvable_key_column_raises_instead_of_importing_nothing(tmp_path, db):
    header = ["WrongIdColumn"] + TCAD_HEADER[1:]
    path = make_workbook(tmp_path, header=header)

    with pytest.raises(ImportError_, match="PropertyID"):
        run_import(path, db)

    assert rows_of(db, "SELECT COUNT(*) FROM properties") == [(0,)]


def test_a_sheet_that_yields_no_properties_raises(tmp_path, db):
    """Configured sheet + zero properties = a mapping failure, not an empty
    sheet. It previously reported success."""
    no_nrsf = list(TCAD_ROW)
    no_nrsf[TCAD_HEADER.index("NRSF")] = None
    path = make_workbook(tmp_path, tcad_rows=[no_nrsf])

    with pytest.raises(ImportError_, match="0 properties"):
        run_import(path, db)


def test_headers_are_indexed_across_every_declared_header_row(tmp_path, db):
    """`BcadNRSF` declares `header_rows: 3`.

    Indexing row 1 only meant a sheet whose labels sat in row 2 or 3 resolved
    every column to None — key included — and imported as zero properties with
    no error at all.
    """
    bcad = [
        ["Bexar County — NRSF export", None, None, None],   # banner row
        ["PropertyID", "DBA", "Property Address", "NRSF"],  # the real labels
        [None, None, None, None],                           # spacer
        ["B77", "Alamo Storage", "3 Broadway", 45_000],
    ]
    path = make_workbook(tmp_path, extra=[("BcadNRSF", bcad)])
    run_import(path, db)

    assert rows_of(db, "SELECT nrsf FROM properties WHERE property_name = ?",
                   ("Alamo Storage",)) == [(45_000.0,)]


def test_rows_without_a_key_are_counted(tmp_path, db):
    """They were skipped and counted nowhere, so a sheet half-importing looked
    identical to one importing cleanly."""
    keyless = list(TCAD_ROW)
    keyless[0] = None
    path = make_workbook(tmp_path, tcad_rows=[TCAD_ROW, keyless])

    stats = run_import(path, db)

    assert stats["skipped_no_key"] == 1
    assert stats["properties"] == 1


# ── Atomicity ──────────────────────────────────────────────────────────

def test_a_mid_import_failure_with_reset_leaves_the_db_untouched(tmp_path, db):
    """`--reset` used to commit the sweep BEFORE the import ran, and each row
    committed individually. A failure partway left the DB swept and half-loaded
    — with no rollback copy, because the comp DB is gitignored and untracked.
    """
    # Seed a prior import so --reset has something to sweep.
    run_import(make_workbook(tmp_path, name="first.xlsx"), db)
    before = dump(db)
    assert before["properties"]  # the sweep has real work to do

    # TCadSqFt imports cleanly; Wcad then fails on its key column. Sheets are
    # walked in PROPERTY_SHEETS order, so the failure lands mid-import.
    broken = make_workbook(
        tmp_path, name="broken.xlsx",
        extra=[("Wcad", [["NoSuchKey", "DBA", "Bldg SF"], ["W1", "X", 10_000]])],
    )
    with pytest.raises(ImportError_):
        run_import(broken, db, reset=True)

    assert dump(db) == before


def test_dry_run_writes_nothing(tmp_path, db):
    before = dump(db)

    stats = run_import(make_workbook(tmp_path), db, dry_run=True)

    assert stats["properties"] == 1  # it did the work...
    assert dump(db) == before        # ...and kept none of it


# ── Idempotence ────────────────────────────────────────────────────────

def test_reimporting_the_same_workbook_leaves_no_duplicates_or_orphans(tmp_path, db):
    path = make_workbook(tmp_path)
    run_import(path, db)
    first = dump(db)

    run_import(path, db)

    assert rows_of(db, "SELECT COUNT(*) FROM properties") == [(1,)]
    # The upsert deletes children before the parent; a stranded data_sources row
    # would show up here as a count that grew.
    assert (len(dump(db)["data_sources"]) == len(first["data_sources"]))
    assert rows_of(
        db,
        "SELECT COUNT(*) FROM data_sources WHERE property_id NOT IN "
        "(SELECT id FROM properties)",
    ) == [(0,)]


def test_reset_sweeps_only_regal_rows(tmp_path, db):
    """A sweep that caught real analyses would be unrecoverable — the DB is
    gitignored and has no backup by default."""
    conn = sqlite3.connect(db)
    with conn:
        conn.execute(
            "INSERT INTO properties (property_name, state, nrsf, pdf_filename, analysis_date) "
            "VALUES ('Real Deal', 'TX', 70000, 'katy_om.pdf', '2026-08-01')")
    conn.close()

    run_import(make_workbook(tmp_path), db, reset=True)

    survivors = {n for (n,) in rows_of(db, "SELECT property_name FROM properties")}
    assert "Real Deal" in survivors


# ── The other writer into this schema: save_analysis ───────────────────
#
# Not the importer, but the same defect, and this file is the only place in the
# suite that exercises comp-DB writes directly rather than through a full run.
# The importer's own upsert deletes children before the parent; `save_analysis`
# did not, and every re-analysis of a deal stranded that deal's whole child set.

def test_resaving_a_deal_strands_no_child_rows(tmp_path, mock_cim_data,
                                               base_financial_analysis):
    """`save_analysis` upserts on `pdf_filename`.

    The schema declares FOREIGN KEYs, but nothing enables the `foreign_keys`
    pragma and no constraint carries ON DELETE CASCADE, so deleting the parent
    alone leaves its `unit_mix` / `expense_lines` / `data_sources` rows behind.
    A live DB had accumulated 124 orphaned unit_mix and 279 orphaned
    expense_lines rows this way. They are invisible to results — every benchmark
    query JOINs properties — but they would silently reattach to an unrelated
    property if AUTOINCREMENT were ever reset.
    """
    path = str(tmp_path / "orphans.db")
    comp_db = CompDatabase(path)

    financials = dict(base_financial_analysis)
    financials["expense_analysis"] = {
        "total_adjusted_expenses": 220_000,
        "lines": [{"category": "Property Taxes", "cim_value": 60_000,
                   "adjusted_value": 65_000, "per_nrsf": 1.2, "flag": None}],
    }
    rent = {"unit_mix_summary": [{"size_label": "10x10", "unit_sf": 100,
                                  "count": 40, "monthly_rate": 95.0,
                                  "rate_per_sf": 0.95,
                                  "climate_controlled": False}]}
    sources = {"nrsf": {"tier": 1, "source": "CIM p.4", "value": 50_000}}

    for _ in range(3):
        comp_db.save_analysis(mock_cim_data, financials, rent,
                              "same_deal.pdf", source_log=sources)

    assert rows_of(path, "SELECT COUNT(*) FROM properties") == [(1,)]
    for table in ("unit_mix", "expense_lines", "data_sources"):
        assert rows_of(path, f"SELECT COUNT(*) FROM {table}") == [(1,)], table
        assert rows_of(
            path,
            f"SELECT COUNT(*) FROM {table} WHERE property_id NOT IN "
            "(SELECT id FROM properties)",
        ) == [(0,)], table


# ── prune_comp: the maintenance path that is not a hand-written DELETE ──

def _seeded_db(tmp_path, mock_cim_data, base_financial_analysis, name):
    """One property with a child row in each of the three child tables."""
    path = str(tmp_path / name)
    db = CompDatabase(path)
    financials = dict(base_financial_analysis)
    financials["expense_analysis"] = {
        "total_adjusted_expenses": 220_000,
        "lines": [{"category": "Property Taxes", "cim_value": 60_000,
                   "adjusted_value": 65_000, "per_nrsf": 1.2, "flag": None}],
    }
    rent = {"unit_mix_summary": [{"size_label": "10x10", "unit_sf": 100,
                                  "count": 40, "monthly_rate": 95.0,
                                  "rate_per_sf": 0.95,
                                  "climate_controlled": False}]}
    db.save_analysis(mock_cim_data, financials, rent, "doomed.pdf",
                     source_log={"nrsf": {"tier": 1, "source": "CIM p.4",
                                          "value": 50_000}})
    return path


def _prune(path, monkeypatch, **opts):
    from django.core.management import call_command
    monkeypatch.setattr("data.comp_db.COMP_DB_PATH", path)
    call_command("prune_comp", **opts)


def test_prune_comp_takes_the_children_with_it(tmp_path, monkeypatch,
                                               mock_cim_data,
                                               base_financial_analysis):
    """The whole reason the command exists: `DELETE FROM properties`
    alone strands every child row, because nothing enables the
    `foreign_keys` pragma and no constraint cascades."""
    path = _seeded_db(tmp_path, mock_cim_data, base_financial_analysis, "p.db")
    _prune(path, monkeypatch, pdf="doomed.pdf")

    assert rows_of(path, "SELECT COUNT(*) FROM properties") == [(0,)]
    for table in ("unit_mix", "expense_lines", "data_sources"):
        assert rows_of(path, f"SELECT COUNT(*) FROM {table}") == [(0,)], table


def test_prune_comp_dry_run_changes_nothing(tmp_path, monkeypatch,
                                            mock_cim_data,
                                            base_financial_analysis):
    path = _seeded_db(tmp_path, mock_cim_data, base_financial_analysis, "d.db")
    _prune(path, monkeypatch, pdf="doomed.pdf", dry_run=True)

    assert rows_of(path, "SELECT COUNT(*) FROM properties") == [(1,)]
    for table in ("unit_mix", "expense_lines", "data_sources"):
        assert rows_of(path, f"SELECT COUNT(*) FROM {table}") == [(1,)], table


def test_prune_comp_sweeps_orphans_already_in_the_db(tmp_path, monkeypatch,
                                                     mock_cim_data,
                                                     base_financial_analysis):
    """Rows stranded by the historical bug, which the fix to
    `save_analysis` stopped creating but never cleaned up."""
    import sqlite3
    path = _seeded_db(tmp_path, mock_cim_data, base_financial_analysis, "o.db")
    with sqlite3.connect(path) as conn:      # strand them the old way
        conn.execute("DELETE FROM properties")
    assert rows_of(path, "SELECT COUNT(*) FROM unit_mix") == [(1,)]

    _prune(path, monkeypatch, orphans=True)
    for table in ("unit_mix", "expense_lines", "data_sources"):
        assert rows_of(path, f"SELECT COUNT(*) FROM {table}") == [(0,)], table


def test_prune_comp_refuses_an_ambiguous_invocation(tmp_path, monkeypatch,
                                                    mock_cim_data,
                                                    base_financial_analysis):
    from django.core.management.base import CommandError
    path = _seeded_db(tmp_path, mock_cim_data, base_financial_analysis, "a.db")
    with pytest.raises(CommandError):
        _prune(path, monkeypatch, pdf="doomed.pdf", orphans=True)
    with pytest.raises(CommandError):
        _prune(path, monkeypatch)
    with pytest.raises(CommandError):
        _prune(path, monkeypatch, pdf="never-analysed.pdf")
    assert rows_of(path, "SELECT COUNT(*) FROM properties") == [(1,)]


# ── The gate above both writers: a bad run seeds nothing ────────────

def _run_engine(cim, tmp_path):
    from engine import AnalysisResult, run_analysis
    result = AnalysisResult(pdf_path=str(tmp_path / "subject.pdf"),
                            cim_data=cim)
    run_analysis(result, output_dir=str(tmp_path))
    return result


def test_a_run_with_a_blocking_failure_seeds_no_comp_row(tmp_path, monkeypatch,
                                                        mock_cim_data):
    """A comp row is reference data OTHER deals are benchmarked against.

    The Abilene run seeded one at $90.44 revenue/SF and $87.33 NOI/SF
    beside genuine neighbours at 4-9, because a 10x revenue line drove
    it. The gate holds even for an accepted finding: that hatch is a
    decision about publishing ONE deal's numbers, made by someone looking
    at that deal, and nobody is looking at the benchmark set when they
    tick it.
    """
    path = str(tmp_path / "gated.db")
    monkeypatch.setattr("data.comp_db.COMP_DB_PATH", path)
    CompDatabase(path)                       # create the schema

    cim = mock_cim_data
    cim.ttm_total_revenue = 5_600_000        # 10x, identity now broken
    result = _run_engine(cim, tmp_path)

    assert result.check_summary["blocking_failed"] > 0
    assert rows_of(path, "SELECT COUNT(*) FROM properties") == [(0,)]
    assert any("Not saved to the comp database" in e for e in result.errors)


def test_a_clean_run_still_seeds_its_comp_row(tmp_path, monkeypatch,
                                              mock_cim_data):
    """The gate must not become a wall — the shared fixture is
    self-consistent (560,000 - 220,000 = 340,000)."""
    path = str(tmp_path / "clean.db")
    monkeypatch.setattr("data.comp_db.COMP_DB_PATH", path)
    CompDatabase(path)

    result = _run_engine(mock_cim_data, tmp_path)

    assert result.check_summary["blocking_failed"] == 0
    assert rows_of(path, "SELECT COUNT(*) FROM properties") == [(1,)]
    assert not any("Not saved to the comp database" in e
                   for e in result.errors)
