# CIM Analyst — Agent Instructions

## What this project does
Analyzes a self-storage CIM (PDF) and produces:
1. A completed investment memo (.docx)
2. A returns model (.xlsx)
3. A terminal summary with PASS/FAIL gates and recommendation

## How to run
```bash
python manage.py runserver   # Web app (Django) — upload, assumptions, runs, results
python run.py                # CLI — prompts for a PDF filename in the current directory
```
The web app is the primary interface; the CLI remains for local one-off runs.
The settings editor stores dated deltas (`ConfigOverride` rows) on top of
`config.py` — it never mutates the file. The comps browser reads
`data/cim_comps.db` read-only.

## Simultaneous sessions (MANDATORY)
Multiple Claude sessions can share this one clone, and the primary working
tree is the collision zone: a concurrent session can switch its branch out
from under you, its uncommitted edits show up as foreign dirty state, and a
stray commit lands on the wrong branch. Rule set imported from
fcs-call-reports; enforced by git-tracked hooks in `.claude/settings.json` —
never remove, weaken, or bypass them:

- **SessionStart** `.claude/hooks/session-start-parallel-check.sh` — reports
  linked worktrees, foreign dirty state, and whether solo mode is on.
- **PreToolUse** `.claude/hooks/guard-shared-worktree.py` — DENIES file edits
  and git mutations targeting the primary working tree of this clone.

Working rules:
1. Before any file-mutating work, isolate:
   `git worktree add .claude/worktrees/<slug> -b <branch> origin/main`
   and do everything there. Commit early — branch refs are durable even if
   the worktree dir is lost; uncommitted files are not.
2. The session-start branch line is a point-in-time snapshot. Before every
   commit, re-assert `git branch --show-current` and read `git diff` in that
   exact directory; never commit foreign edits (per-path `git add`, never `-A`).
3. Once your branch is pushed and the PR is open, every remaining ship step is
   working-tree-independent: `gh pr merge --squash`, remote ref delete via gh,
   local ref delete via `git branch -D`. Never `git checkout` in the primary
   tree to "restore" it — that clobbers another session's WIP.
4. Deliberate solo work on the primary clone: launch with `CIM_SOLO=1`, or
   `touch "$(git rev-parse --git-dir)/cim-solo"` — the flag must be set in its
   own Bash call (the guard evaluates pre-tool), and the marker must be
   removed immediately after; leaving it disables the guard for every future
   session.

## When the user provides a CIM PDF
1. Run `python run.py` and provide the filename
2. If extraction is incomplete (CIM formats vary), Claude Code should
   manually read the PDF and fill in the `parsed_data` dict by hand
   from the PDF contents visible in context
3. Review the output memo and model for reasonableness before presenting

## Architecture
```
run.py                     # CLI entry point — file prompt, orchestration
engine.py                  # Analysis orchestration (extract_pdf_data / run_analysis) — the web↔pipeline boundary
config.py                  # Hard-coded investment criteria / thresholds (base for ConfigOverride deltas)
cimweb/                    # Django project (settings, urls, wsgi)
webapp/                    # Django app — views, models (Deal/AnalysisRun/ConfigOverride),
                           #   forms, services (deal folders), results, templates
extract/
  pdf_reader.py            # PDF text + table extraction (pdfplumber)
  parser.py                # Structured data extraction → CIMData dataclass
analysis/
  filters.py               # Go/No-Go gate evaluation (7 gates)
  market.py                # Market & location analysis
  physical.py              # Property description, replacement cost
  financials.py            # Historical financial review, expense benchmarks
  rent_analysis.py         # Unit mix & rent analysis
  valuation.py             # THE unlevered projection (project_cash_flows) + scenario NOI forecast, IRR/MOIC
  value_add.py             # Operational improvement identification
  risks.py                 # Risk identification
model/
  returns_model.py         # Unlevered DCF wrapper + sensitivity grid: Bear/Base/Bull
  solver.py                # Bisection solver: max price for 10% IRR
output/
  memo_writer.py           # Generates .docx from analysis outputs
  excel_writer.py          # Generates .xlsx returns model
```

## Investment criteria (non-negotiable)
- Population ≥ 50,000 within 3-mile radius
- No unproven demand: physical occupancy ≥ 75%, and no post-2020 vintage
  still in ramp (< 85% physical). High-physical/low-economic occupancy
  (spread ≥ 10 pts) is the target mismanagement value-add profile, not an
  exclusion — always compare economic vs physical occupancy; a broker
  quoting one occupancy number is almost always quoting physical.
- Watch: deals whose entire bridge is ECRI in a falling street-rate market —
  the in-place-to-market gap closes from above; verify street-rate trend.
- Asking price ≤ replacement cost
- Base case unlevered IRR ≥ 10% over the hold, NET of transaction costs
  (default 5-year hold, editable 1–10; the config key stays `min_irr_5yr`
  so stored ConfigOverride rows keep resolving)
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
3. **All returns unlevered**: IRR and MOIC ignore debt. Total basis =
   price + CapEx + acquisition closing costs; exit is net of disposition
   costs. `analysis.valuation.project_cash_flows` is the ONE projection —
   the scenario engine, the sensitivity grid and both solvers call it, and
   a second copy of that loop is how they drifted last time.
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
