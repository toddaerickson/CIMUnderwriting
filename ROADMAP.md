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
- [ ] Post-cutover hardening (deferred from PR #2 review): `manage.py check
      --deploy` in CI; decide `/admin/` exposure (see DEPLOY.md). The third
      piece of that deferral, `SECURE_HSTS_SECONDS`, shipped in PR #51
      (`cimweb/settings.py:140` — 31536000, preload deliberately off)

### Operator actions (not code)

These cannot be discharged by building anything; they are recorded here so the
non-code items have one home:

- [x] Read LPA sections for `accrual_base`, `am_fee_treatment` and `catch_up`
      — **done 2026-08-12**, dated entries in `config.LPA_CONFIRMED`. All three
      matched the shipped default, so none became implementation work; the
      reading did add the pref RATE as two numbers (8% levered / 6% unlevered)
      and moved the catch-up question onto the stamp.
- [x] Read the LPA's promote clause — `promote_basis`, the last open
      waterfall question. **Done 2026-08-12: the promote is earned on all
      capital and the GP's co-invest earns the pref.** That sentence reads two
      ways and they differ by real dollars, so the fund's own model workbook
      settled it (`Underwriting!J250 = I250+(1-I250)*$J$244` — promote off the
      top, remainder pro rata), and it is what the build already computed. No
      number moved. Every stamp row is now confirmed or moot.
- [x] **The AM fee's BASE — the one confirmation that moved money.**
      **Done 2026-08-14**, operator: a GP charges no asset-management fee on
      its own co-investment, so the fee is 1% of LP equity, not of invested
      equity. `config.AM_FEE_BASE = "lp_equity"`; `invested_equity` raises.
      The build had charged GP+LP since 2026-08-01, overstating the fee by
      the co-invest share (11.1% at 10%), so every levered figure in the repo
      moved and the snapshots were regenerated. Not a seventh LPA question —
      it is question 4 read to its end — and the model workbook had it right
      all along (`G244` = "% of LP Equity"), the THIRD such correction in six
      days.
- [ ] **Re-read the LP investor summary's caveat.** Not a code task — a
      consequence of the line above. With no open row the "proposed terms,
      subject to the final partnership agreement" clause no longer renders,
      which is the one of `_is_assumption_stamp`'s three variants that has
      never been reviewed against a rendered document. See
      `docs/gc-review-investor-summary.md` question 7; the standing clearance
      is ASSUMED, so this rides with the real GC review below.
- [ ] Real GC review of the LP investor summary. The current clearance is an
      ASSUMED approval (operator direction 2026-08-09, marked as such on every
      surface); a real review REPLACES the assumed row in
      `docs/gc-review-investor-summary.md`.
- [ ] Quarterly restore drill (Neon PITR + disk snapshot — see DEPLOY.md; a
      backup that's never been restored is a hope, not a backup).
- [ ] Custom domain (also tracked under Web Deployment above).

### Model Integrity & Capital Structure

Scoped in [docs/scoped-backlog.md](docs/scoped-backlog.md) — scope contract,
acceptance criteria, and build order. **All shipped**, in the order
**A → B → D → E1 → E2 → E3 → E4 → G → T**; B extracted the shared cash-flow
projection the rest depend on, and **G shipped last of the capital-structure
items, after E** — a 2-page LP summary built before the LP-net-IRR engine
existed would have handed an LP a property-level unlevered IRR, which is the
one number that document exists to avoid.

**T (transparency consolidation)** closed after G, across six categories: kill
the duplicated constants (#40), the value-add assumptions layer (#46), the
model-layer hard-codes (#47), loud fallbacks — the assumption fill log (#49),
occupancy becomes a required input (#50), and the assumption register with its
memo Appendix B. The last acceptance criterion — a general CI sweep for numeric
modeling literals outside `config.py` — closed in PR #59
(`tests/test_literal_sweep.py`: exemption by KIND plus an allowlist where every
entry carries a reason), and PR #64 extended it to comparison-shaped literals
in `output/`. The per-family AST guards in `tests/test_config_single_source.py`
deliberately remain as backstops — they name which family broke, where the
sweep can only cite a line (see `docs/scoped-backlog.md`, item T Acceptance,
for the full record including what the sweep still does not cover).

- [x] **A.** Model error-check register (`analysis/checks.py`) — generalize the
      lone Revenue−Expenses=NOI identity into a register of checks (unit-mix
      reconciliation, occupancy sanity, expense-line floors, exit-vs-entry cap
      coercion surfaced; 11 at ship, 14 today — `sources_uses_ties` and
      `loan_matures_before_exit` joined with items D/E, `ttm_annualization`
      when its `ttm_months` field finally existed)
- [x] **B.** Transaction costs + variable hold period — closing/disposition costs
      are absent from the DCF and the 5-year hold is hardcoded in three duplicated
      projection loops; collapse to one and add both. Changes every published IRR.
- [x] **D.** Sources & Uses + capital stack — unlevered-safe now, debt-ready;
      must tie to DCF total basis (enforced by check 11)

### Levered Returns / LP Waterfall

Design + verified numeric oracles: [docs/levered-waterfall-design.md](docs/levered-waterfall-design.md).
**Single tier only** (operator, 2026-07-29): GP management fee, GP co-invest
upfront, x% promote above a y% pref. No catch-up, no clawback, no tier builder.

- [x] **E1.** Debt layer — `model/debt.py`, sizing as min(LTV, DSCR, debt-yield)
      with the binding constraint reported; monthly amort roll-forward
- [x] **E2.** Single-hurdle waterfall — `model/waterfall.py`, deterministic
      forward loop; LPA questions ship as named, stamped defaults (5 open at
      the time; 1 open as of 2026-08-12)
- [x] **E3.** Levered wiring — assumptions / results / memo / xlsx; unlevered
      screen stays primary, levered is the second lens
- [x] **E4.** Levered max offer — `solve_max_price_levered` targets a 15%
      LP net IRR. Shipped BESIDE the 10% unlevered max offer rather than
      instead of it (operator, 2026-08-01): the unlevered price is what the
      primary gate is read against, so both are computed and both are shown,
      each labelled with the target it was solved to. Monotonicity is
      CI-guarded by a sweep across the exit-cap-coerced region, and an
      observed inversion is reported on all three surfaces.
- [x] **G.** LP-facing 2-page investor summary (.docx) — target-return box
      (unlevered and LP net at equal weight, with the leverage effect), a
      derived thesis, a "Plan to Achieve the Return" section, fee and promote
      transparency, and risks WITH mitigants. Two pages held by a CONTENT
      BUDGET (`output/page_budget.py`), not by a page count python-docx cannot
      produce. Downloadable from the results page (migration `0006`).
      **GC gate still binds before any external distribution** — the build was
      never blocked, the distribution is.

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
