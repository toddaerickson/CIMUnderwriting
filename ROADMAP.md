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
- [x] Streamlit dashboard with file upload
- [x] 6-tab assumptions editor (Property, Size, Unit Mix, Income & Expenses, Scenarios, Demographics)
- [x] Percentage inputs as whole numbers (type 6 for 6%)
- [x] Per-analysis scenario overrides (don't mutate global config)
- [x] Deal tracker with persistent folders
- [x] Comp database (SQLite)
- [x] Batch analysis
- [x] Docker + docker-compose deployment
- [x] GitHub Actions CI (pytest + Docker build)
- [x] Environment variable externalization
- [x] Security audit and sanitization

## Next Up

### Web Deployment (Phase 2)
- [ ] Deploy to Railway or VPS
- [ ] Cloudflare Tunnel + Access for authentication
- [ ] Custom domain (cim.your-domain.com)
- [ ] SQLite WAL mode for concurrent reads
- [ ] Temp file cleanup after analysis
- [ ] Weekly database backup script

### Levered Returns / LP Waterfall
- [ ] Add debt layer (senior/junior with term, IO, amort, rate)
- [ ] GP/LP distribution waterfall (pref return + promote tiers)
- [ ] LP net IRR as primary screening metric (target 15%+)
- [ ] Solver targets LP net IRR instead of unlevered IRR

### UI Polish
- [ ] Extraction confidence indicators per field (green/yellow/red)
- [ ] Inline validation (flag when inputs violate gate thresholds)
- [ ] Comp overlay on unit mix tab (show nearby comps alongside inputs)
- [ ] Dark/light theme toggle

### Analysis Enhancements
- [ ] Property tax reassessment modeling (post-acquisition revaluation)
- [ ] New supply pipeline integration (permit data APIs)
- [ ] Rent comp verification against third-party sources
- [ ] Multi-property portfolio analysis

## Not Building (By Design)

- React/Next.js frontend — Streamlit is sufficient for internal tools
- PostgreSQL — SQLite is correct at 1-5 users
- REST API — no second consumer exists
- Multi-tenancy — single firm, shared pipeline
- Celery/Redis background workers — analysis takes 10-30 seconds
