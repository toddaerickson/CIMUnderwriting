# CIM Underwriting

Self-storage investment screening and underwriting tool. Upload a CIM (Confidential Information Memorandum), review extracted data, adjust assumptions, and generate investment memos, returns models, and pre-filled underwriting templates.

## Features

- **PDF extraction** — pulls property details, unit mix, financials, and demographics from CIM PDFs
- **Assumptions editor** — 6-tab form to review/edit all inputs before analysis (Property, Size, Unit Mix, Income & Expenses, Scenarios, Demographics)
- **Go/No-Go gates** — 7 investment criteria with PASS/FAIL/TBD evaluation
- **Expense benchmarking** — analyst-adjusted NOI using $/NRSF benchmarks by state and region
- **Unlevered DCF** — Bear/Base/Bull scenarios with IRR, MOIC, yield-on-cost, net of transaction costs (default 5-year hold, editable 1–10)
- **Value-add modeling** — monthly cash flow engine for lease-up and rent growth deals
- **Levered returns & LP waterfall** — debt sized as min(LTV, DSCR, debt yield), a 1% AM fee on LP equity charged above a single-tier waterfall (pref + promote), with LP net IRR/MOIC as a second lens beside the unlevered screen, every figure stamped with its resolved LPA assumptions
- **Max price solvers** — two bisection solvers, both shown: max price for the 10% unlevered IRR gate, and max price for the fund's 15% LP net IRR
- **Output generation** — Word memo (.docx), Excel returns model (.xlsx), pre-filled underwriting template (.xlsm), and a 2-page LP-facing investor summary (.docx)
- **Deal tracker** — persistent deal folder with metadata and comp database
- **Web app** — Django UI with upload, assumptions editing, threaded analysis runs, results tabs, downloads, comps browser, and settings overrides

## Quick Start

```bash
pip install -r requirements.txt
python manage.py migrate
OPERATOR_PASSWORD='<choose one>' python manage.py bootstrap_operator
python manage.py runserver
```

Open http://localhost:8000, log in, upload a CIM PDF, review assumptions, click "Save & Run."

### CLI

```bash
python run.py
```

Prompts for a PDF filename and runs the full pipeline with terminal output.

## Project Structure

```
cimweb/                 # Django project (settings, urls, wsgi)
webapp/                 # Django app — views, models, forms, services, results, templates
engine.py               # Analysis orchestration (extract_pdf_data / run_analysis)
extract/                # PDF text extraction and CIM parsing
analysis/               # Financial, market, physical, rent, risk analysis
model/                  # Returns model, value-add model, solver
output/                 # Memo writer, Excel writer, UW template writer
data/                   # Comp database (SQLite)
config.py               # Investment criteria, benchmarks, scenario defaults
```

## Configuration

Copy `.env.example` to `.env` and fill in values. See `DEPLOY.md` for the
Render/Neon deployment architecture and cutover runbook.

Key environment variables:
- `CENSUS_API_KEY` — demographic enrichment (optional)
- `GP_NAME` — GP display name for the UW template (the old `GP_EQUITY_SHARE` /
  `GP_AM_FEE_RATE` / `GP_PROMOTE_PCT` vars were removed in item E3b — fund
  structure now lives in `config.py` and the per-deal Debt & Waterfall inputs)
- `COMP_DB_PATH`, `CIM_DEALS_DIR`, `CIM_OVERRIDES_DIR` — data paths (set by `render.yaml` in prod)

## Comp Database Seeding

The comp database (`data/cim_comps.db`) normally fills only from your own prior
CIM analyses. To seed it with third-party market data (e.g. the Texas
`BCRE REGAL FULL SELF STORAGE DATABASE.xlsx`), use the importer:

```bash
python scripts/import_regal_database.py                # upsert into data/cim_comps.db
python scripts/import_regal_database.py --dry-run      # report only, no writes
python scripts/import_regal_database.py --reset        # drop prior [regal: rows first
```

It maps the workbook's four County Appraisal District sheets into `properties`
— NRSF, year built, acreage, location — plus provenance rows in `data_sources`
(tier 3). Rows are tagged `pdf_filename = "[regal:<sheet>:<key>].xlsx"`; note
the trailing `.xlsx`, because a sweep written against the bracketed prefix alone
matches nothing. Reruns are idempotent and never touch rows that came from
actual CIM analyses; `--reset` sweeps only `[regal:` rows.

Two things it deliberately does **not** do, and the reason matters if you are
about to seed a different workbook:

- **It writes no rents.** This workbook's data is ~2013–2016, and its unit-rate
  columns are decade-old asking rents. Seeding them made 158 of 166 Texas rent
  comps one stale portfolio presenting as current, which moved a $1.20/SF
  subject's rent gap from +12.1% to +9.0%. Nothing downstream filters comps by
  age, so stale rents are worse than none — `query_rent_comps` returning `None`
  falls back to the CIM's own stated market rent.
- **It writes no `price_per_sf`.** The CAD figure is a tax-appraised value; that
  column is filled by real analyses with broker asking price ÷ NRSF. The
  appraised figure is kept in `data_sources` under `cad_appraised_per_sf`.

`analysis_date` carries the **source's** vintage (2015/2016 here), not the
import time, so imported rows sort below real analyses under
`ORDER BY analysis_date DESC` and read as what they are.

## Investment Criteria

| Gate | Threshold |
|------|-----------|
| Population (3-mi) | >= 50,000 |
| No Unproven Demand | Physical occupancy >= 75%, and no post-2020 vintage still in ramp (< 85%) |
| Asking Price | <= Replacement Cost |
| Base Case unlevered IRR | >= 10% over the hold (default 5-yr, editable 1–10) |
| MSA Quality | Top-50 or strong secondary |
| CIM Yr1 NOI Step-Up | <= 15% vs TTM |
| Exit Cap | >= Entry Cap (base case) |

## Developer Setup

```bash
git clone https://github.com/toddaerickson/CIMUnderwriting.git
cd CIMUnderwriting
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in CENSUS_API_KEY (optional) and GP_NAME
python manage.py migrate
OPERATOR_PASSWORD='<choose one>' python manage.py bootstrap_operator
```

## Tests

```bash
pytest tests/ -v
```
