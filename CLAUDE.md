# CIM Analyst — Agent Instructions

## What this project does
Analyzes a self-storage CIM (PDF) and produces:
1. A completed investment memo (.docx)
2. A returns model (.xlsx)
3. A terminal summary with PASS/FAIL gates and recommendation

## How to run
```bash
python run.py
```
The program prompts for a PDF filename in the current directory.

## When the user provides a CIM PDF
1. Run `python run.py` and provide the filename
2. If extraction is incomplete (CIM formats vary), Claude Code should
   manually read the PDF and fill in the `parsed_data` dict by hand
   from the PDF contents visible in context
3. Review the output memo and model for reasonableness before presenting

## Architecture
```
run.py                     # Entry point — file prompt, orchestration
config.py                  # Hard-coded investment criteria / thresholds
extract/
  pdf_reader.py            # PDF text + table extraction (pdfplumber)
  parser.py                # Structured data extraction → CIMData dataclass
analysis/
  filters.py               # Go/No-Go gate evaluation (7 gates)
  market.py                # Market & location analysis
  physical.py              # Property description, replacement cost
  financials.py            # Historical financial review, expense benchmarks
  rent_analysis.py         # Unit mix & rent analysis
  valuation.py             # Scenario NOI forecast, IRR/MOIC calc
  value_add.py             # Operational improvement identification
  risks.py                 # Risk identification
model/
  returns_model.py         # 5-year unlevered DCF: Bear/Base/Bull
  solver.py                # Bisection solver: max price for 10% IRR
output/
  memo_writer.py           # Generates .docx from analysis outputs
  excel_writer.py          # Generates .xlsx returns model
```

## Investment criteria (non-negotiable)
- Population ≥ 50,000 within 3-mile radius
- Physical occupancy ≥ 85% (no lease-up deals)
- Asking price ≤ replacement cost
- Base case 5-year unlevered IRR ≥ 10%
- Top-50 MSA or strong secondary market
- CIM Year 1 NOI ≤ 115% of TTM actual (flag if exceeded)
- Exit cap rate ≥ entry cap rate in base case

## Expense benchmarks ($/NRSF/yr, stabilized non-CC)
| Category       | Low    | High   |
|----------------|--------|--------|
| Property Taxes | $1.20  | $2.50  |
| Insurance      | $0.12  | $0.25  |
| Utilities      | $0.08  | $0.18  |
| R&M            | $0.20  | $0.40  |
| Advertising    | $0.05  | $0.15  |
| Payroll        | $0.30  | $0.60  |
| G&A            | $0.10  | $0.20  |
| Mgmt Fee       | 3%     | 6% EGR |
| Capital Reserve| $0.15  | $0.25  |
| Total OpEx     | $3.00  | $5.50  |
| OpEx/Revenue   | 35%    | 55%    |

## Replacement cost benchmarks
| Component        | Low    | High   |
|------------------|--------|--------|
| Non-CC $/SF      | $55    | $85    |
| CC $/SF          | $90    | $130   |
| Site work $/SF   | $5     | $12    |
| Soft costs       | 8%     | 12%    |
| Developer profit | 10%    | 15%    |

## Key design decisions
1. **Parser tolerance**: CIM formats vary wildly. The parser extracts what it
   can and flags gaps. Claude Code fills in missing data from PDF context.
2. **Analyst-adjusted NOI**: Never trust CIM expenses at face value. Uses
   max(CIM expense, benchmark midpoint) for lines that appear understated.
3. **All returns unlevered**: IRR and MOIC ignore debt. Total equity = price + CapEx.
4. **Exit cap ≥ entry cap** in base and bear cases.
5. **Bisection solver**: Deterministic, 20 iterations to 0.1% precision.

## Manual steps flagged by the program
- Population verification (if not in CIM)
- Comp rent verification (CIM data taken at face value initially)
- Physical condition assessment (requires site visit)
- Property tax reassessment calculation
- New supply pipeline confirmation

## Dependencies
```
pip install -r requirements.txt
```
Requires: pdfplumber, python-docx, openpyxl, numpy-financial
