# CIM Underwriting

Self-storage investment screening and underwriting tool. Upload a CIM (Confidential Information Memorandum), review extracted data, adjust assumptions, and generate investment memos, returns models, and pre-filled underwriting templates.

## Features

- **PDF extraction** — pulls property details, unit mix, financials, and demographics from CIM PDFs
- **Assumptions editor** — 6-tab form to review/edit all inputs before analysis (Property, Size, Unit Mix, Income & Expenses, Scenarios, Demographics)
- **Go/No-Go gates** — 7 investment criteria with PASS/FAIL/TBD evaluation
- **Expense benchmarking** — analyst-adjusted NOI using $/NRSF benchmarks by state and region
- **5-year unlevered DCF** — Bear/Base/Bull scenarios with IRR, MOIC, yield-on-cost
- **Value-add modeling** — monthly cash flow engine for lease-up and rent growth deals
- **Max price solver** — bisection solver finds highest price for target IRR
- **Output generation** — Word memo (.docx), Excel returns model (.xlsx), pre-filled underwriting template (.xlsm)
- **Deal tracker** — persistent deal folder with metadata and comp database
- **Streamlit dashboard** — web UI with file upload, interactive editing, and download buttons

## Quick Start

```bash
pip install -r requirements.txt
streamlit run gui/app.py
```

Open http://localhost:8501, upload a CIM PDF, review assumptions, click "Run Analysis."

### CLI

```bash
python run.py
```

Prompts for a PDF filename and runs the full pipeline with terminal output.

### Docker

```bash
docker compose up --build
```

Runs on port 8501 with persistent data volume at `/data`.

## Project Structure

```
gui/                    # Streamlit dashboard
  app.py                # Entry point, navigation
  pages/                # Upload, Deal Tracker, Settings, Comp DB
  components/           # Assumptions editor, gates, metrics, downloads
  engine.py             # Analysis pipeline callable from GUI
extract/                # PDF text extraction and CIM parsing
analysis/               # Financial, market, physical, rent, risk analysis
model/                  # Returns model, value-add model, solver
output/                 # Memo writer, Excel writer, UW template writer
data/                   # Comp database (SQLite)
config.py               # Investment criteria, benchmarks, scenario defaults
```

## Configuration

Copy `.env.example` to `.env` and fill in values. See `DEPLOY.md` for full deployment guide.

Key environment variables:
- `CENSUS_API_KEY` — demographic enrichment (optional)
- `GP_NAME`, `GP_EQUITY_SHARE`, `GP_AM_FEE_RATE`, `GP_PROMOTE_PCT` — fund structure for UW template
- `COMP_DB_PATH`, `CIM_DEALS_DIR`, `CIM_OVERRIDES_DIR` — data paths (for Docker)

## Investment Criteria

| Gate | Threshold |
|------|-----------|
| Population (3-mi) | >= 50,000 |
| Physical Occupancy | >= 85% |
| Asking Price | <= Replacement Cost |
| Base Case 5-yr IRR | >= 10% |
| MSA Quality | Top-50 or strong secondary |
| CIM Yr1 NOI Step-Up | <= 15% vs TTM |
| Exit Cap | >= Entry Cap (base case) |

## Developer Setup

```bash
git clone https://github.com/toddaerickson/CIMUnderwriting.git
cd CIMUnderwriting
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in CENSUS_API_KEY, GP_* values
```

## Tests

```bash
pytest tests/ -v
```
