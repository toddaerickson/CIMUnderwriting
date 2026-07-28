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

### Levered Returns / LP Waterfall
- [ ] Add debt layer (senior/junior with term, IO, amort, rate)
- [ ] GP/LP distribution waterfall (pref return + promote tiers)
- [ ] Show both levered (8% pref) and unlevered (6% pref) analyses
- [ ] LP net IRR as primary screening metric (target 15%+)
- [ ] Solver targets LP net IRR instead of unlevered IRR

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
