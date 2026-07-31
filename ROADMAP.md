# Roadmap

## Completed

- [x] PDF extraction pipeline (pdfplumber + regex parser)
- [x] Financial analysis with expense benchmarking by state/region
- [x] 5-year unlevered DCF (Bear/Base/Bull)
- [x] Value-add monthly cash flow model
- [x] Max price bisection solver
- [x] Go/No-Go gate evaluation (7 gates)
- [x] Word memo generation (.docx)
- [x] Excel returns model (.xlsx)
- [x] Pre-filled underwriting template (.xlsm)
- [x] Streamlit dashboard with file upload (retired in the Phase 5 cutover)
- [x] 6-tab assumptions editor (Property, Size, Unit Mix, Income & Expenses, Scenarios, Demographics)
- [x] Percentage inputs as whole numbers (type 6 for 6%)
- [x] Per-analysis scenario overrides (don't mutate global config)
- [x] Per-deal replacement cost overrides ($/SF per facility type)
- [x] Required field indicators (red ! for IRR-critical fields)
- [x] Clickable CIM tile (opens PDF in new browser tab via Blob URL)
- [x] Duplicate detection on upload (comp DB + deal folder search)
- [x] Deal tracker with persistent folders
- [x] Comp database (SQLite)
- [x] Batch analysis (retired with the Streamlit GUI — see Not Building)
- [x] Sidebar redesign (New Analysis, Deal Pipeline, Comps, Settings)
- [x] Docker + docker-compose deployment (retired in the Phase 5 cutover)
- [x] GitHub Actions CI (pytest + real-Postgres smoke + collectstatic)
- [x] Environment variable externalization
- [x] Security audit and sanitization
- [x] SQLite WAL mode for concurrent reads
- [x] Temp file cleanup after analysis
- [x] DB backup script (scripts/backup_db.sh)
- [x] Django web app (cimweb/webapp): allowlisted auth, upload + extraction,
      6-tab assumptions editor, threaded analysis runs, results tabs, downloads
- [x] Comps browser + settings override CRUD (`ConfigOverride` dated deltas)
- [x] Postgres-safe schema hardening + real-Postgres CI smoke job
- [x] engine.py at root; Streamlit GUI / Docker / Railway artifacts retired
- [x] Render blueprint (`render.yaml`): Neon Postgres + 1GB disk + disk-aware /health/

## Next Up

### Web Deployment
- [x] Render (done) — blueprint merged; production cutover follows the DEPLOY.md runbook
- [x] Authentication — Django allowlisted login (`ALLOWED_EMAILS`) replaced the Cloudflare Tunnel + Access plan
- [ ] Custom domain
- [ ] Post-cutover hardening (deferred from PR #2 review): `SECURE_HSTS_SECONDS`
      + `manage.py check --deploy` in CI; decide `/admin/` exposure (see DEPLOY.md)

### Model Integrity & Capital Structure

Scoped in [docs/scoped-backlog.md](docs/scoped-backlog.md) — scope contract,
acceptance criteria, and build order. Queued behind the dense-model-view build.
Order is **A → B → D → E1 → E2 → E3 → E4 → G**; B extracts the shared cash-flow
projection the rest depend on, and **G ships last, after E** — a 2-page LP
summary built before the LP-net-IRR engine exists would hand an LP a
property-level unlevered IRR, which is the one number that document exists to
avoid.

- [ ] **A.** Model error-check register (`analysis/checks.py`) — generalize the
      lone Revenue−Expenses=NOI identity into 11 checks (unit-mix reconciliation,
      occupancy sanity, expense-line floors, exit-vs-entry cap coercion surfaced)
- [ ] **B.** Transaction costs + variable hold period — closing/disposition costs
      are absent from the DCF and the 5-year hold is hardcoded in three duplicated
      projection loops; collapse to one and add both. Changes every published IRR.
- [ ] **D.** Sources & Uses + capital stack — unlevered-safe now, debt-ready;
      must tie to DCF total basis (enforced by check 11)
- [ ] **G.** LP-facing 2-page investor summary (.docx) — **ships last, after
      E4**, so it can quote LP net IRR rather than unlevered property IRR; GC
      gate before any external distribution

### Levered Returns / LP Waterfall

Design + verified numeric oracles: [docs/levered-waterfall-design.md](docs/levered-waterfall-design.md).
**Single tier only** (operator, 2026-07-29): GP management fee, GP co-invest
upfront, x% promote above a y% pref. No catch-up, no clawback, no tier builder.

- [ ] **E1.** Debt layer — `model/debt.py`, sizing as min(LTV, DSCR, debt-yield)
      with the binding constraint reported; monthly amort roll-forward
- [ ] **E2.** Single-hurdle waterfall — `model/waterfall.py`, deterministic
      forward loop; 5 LPA questions remain open and ship as named, stamped defaults
- [ ] **E3.** Levered wiring — assumptions / results / memo / xlsx; unlevered
      screen stays primary, levered is the second lens
- [ ] **E4.** Solver targets LP net IRR (15%+) instead of unlevered IRR

### UI Polish
- [ ] Extraction confidence indicators per field (green/yellow/red)
- [ ] Inline validation (flag when inputs violate gate thresholds)
- [ ] Comp overlay on unit mix tab (show nearby comps alongside inputs)

### Analysis Enhancements
- [ ] Property tax reassessment modeling (post-acquisition revaluation)
- [ ] New supply pipeline integration (permit data APIs)
- [ ] Rent comp verification against third-party sources
- [ ] Multi-property portfolio analysis

## Not Building (By Design)

- React/Next.js frontend — server-rendered Django templates are sufficient for internal tools
- REST API — no second consumer exists
- Multi-tenancy — single firm, shared pipeline
- Celery/Redis background workers — analysis takes 10-30 seconds; a thread per run suffices
- Batch analysis (multi-PDF) — retired with the Streamlit GUI; the web flow
  underwrites one deal at a time. Revisit only if a real multi-CIM day actually hurts.
