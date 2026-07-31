# PE-Readiness Scorecard — "Not Yet Institutional, But Could Be" Detection

**Status: PROPOSAL — not approved, not scoped, not queued.** Authored outside
the operator's selected backlog and preserved here on merit. It is NOT part of
the A → B → D → E1-E4 → G sequence in [scoped-backlog.md](scoped-backlog.md)
and competes with that queue for time. Do not begin building it without an
explicit operator decision; if selected, it needs a scope contract in the
backlog first, like every other item.

## Goal

Add a second analysis axis alongside the 7 go/no-go gates: **how close is this deal to
PE/institutional grade, and is the gap fixable?** Today the pipeline ends at
PURSUE / PURSUE-CONTINGENT / DECLINE. The Storage Brief's buyer framework (the 10 PE
criteria, PE-grade checklist, "fixable middle ground," premium factors) has no
representation. This plan adds it.

**Hard constraint:** the scorecard is *informational only*. It must NOT change the 7
gates, the recommendation logic, or any existing underwriting math (CLAUDE.md:
investment criteria are non-negotiable). Purely additive.

## Already covered (do not rebuild)

- Criterion 4 (rate positioning): rent gap, econ/physical spread, ECRI-bridge risk — strong
- Criterion 8 (op efficiency): expense benchmarking + opex/revenue ratio — strong
- Criterion 1: T3/T12 momentum, NOI step-up gate, 3-scenario DCF — partial
- Criterion 2: pop/HHI adequacy, SF/capita gate — partial
- Criterion 5: age-based CapEx checklist — partial
- Criterion 6: supply mentions + SF/capita — partial

## New work (10 criteria → scorecard)

### 1. Config — `config.py`

Add `PE_READINESS` dict (thresholds from the Brief, all overridable):

```python
PE_READINESS = {
    # PE-grade hard checks (note §3)
    "min_nrsf": 40_000, "min_units": 300, "min_noi": 300_000,
    "min_occupancy": 0.80, "path_to_noi_months": 24,
    # Structural disqualifiers
    "oversupply_sf_per_capita": 10, "undersupplied_sf_per_capita": 6.0,
    # Premium factors (note §6)
    "premium_cc_pct": 0.40, "premium_rent_gap": (0.05, 0.20),
    "premium_occ_for_gap": 0.90,
    # Criterion thresholds
    "opex_ratio_pe_grade": 0.40, "pop_growth_target": 0.01,
    "avg_los_target_months": 12, "autopay_target": 0.50,
    "google_reviews_target": 50, "google_rating_target": 4.0,
    "hhi_min": 50_000, "rev_growth_band": (0.03, 0.08),
    # Valuation outputs
    "exit_cap_compression_bps": 50,     # note: 25–75bps; midpoint default
    "deferred_maint_risk_premium": 0.15, # note: 10–20%; midpoint default
}
```

Register `PE_READINESS.*` keys in `override_key_registry()` (webapp/forms.py) so the
settings editor picks them up — follow the existing `GATES.*` loop pattern
(`pct` for rate/ratio keys, `int` where whole, range-kind for the two tuples).

### 2. New CIMData fields — `extract/parser.py`

All optional; `None` = unknown → per-item TBD (mirrors gates' TBD pattern):

- **Systems (Criterion 7):** `mgmt_software` (str), `online_rentals` (str yes/no),
  `autopay_pct`, `google_review_count` (int), `google_rating`, `revenue_mgmt_system` (str)
- **Expansion (Criterion 3):** `developable_acres`, `expansion_zoning`
  (in_place/likely/unknown/hostile), `expansion_nrsf_potential`, `cc_conversion_sf`
- **Tenant (Criterion 9):** `residential_pct`, `avg_los_months`, `commercial_pct`
- **Physical/environmental (Criteria 5/red flags):** `deferred_maintenance_est`,
  `phase1_status` (clean/rec_found/not_done)
- **Market (Criteria 2/10):** `population_growth_annual`, `visibility_rating` (high/medium/low)
- **Criterion 1 trend:** `prior_year_revenue`, `prior_year_noi`

Parser keyword extraction (tolerant, same style as existing): software names
(SiteLink, storEDGE, Easy Storage Solutions, Yardi), "online rental", "autopay",
"tenant insurance", expansion phrases ("excess land", "additional acreage",
"expansion", "phase II"), "Phase I", tenant-mix mentions. Extraction only assists;
analyst overrides are the reliable path.

### 3. Assumptions form — `webapp/forms.py` + `assumptions.html`

- Add fields to `CIM_CHAR_FIELDS` / `CIM_INT_FIELDS` / `CIM_FLOAT_FIELDS` /
  `CIM_PCT_FIELDS` (percent convention: whole-number display, decimal storage —
  `autopay_pct`, `residential_pct`, `commercial_pct`, `population_growth_annual` go
  in `CIM_PCT_FIELDS`).
- ChoiceFields (like `street_rate_trend`): `expansion_zoning`, `phase1_status`,
  `visibility_rating`, `online_rentals`, `revenue_mgmt_system`.
- New section `SECTION_OPERATIONS` ("Operations & PE Profile") rendered as its own
  block in the assumptions template. No tab-count change required — append a section.
- `build_overrides` / `build_initial` pick new fields up automatically via the
  `CIM_SCALAR_FIELDS` union.

### 4. Census enrichment — `extract/enrichment.py`

Add Tier-2 resolution for `population_growth_annual` (two ACS 5-yr vintages at the
geocoded tract/radius, or 2020 decennial vs latest ACS). Tier-1 precedence preserved
(CIM/analyst always wins). On any failure → `None` → TBD, never a crash (existing
`DataResolver` pattern). Keep `enrich=False` default so CLI/tests stay network-free.

### 5. Core module — `analysis/institutional.py` (new)

```python
def score_pe_readiness(cim_data, financial_analysis, rent_analysis,
                       scenario_results, va_results, comp_db=None) -> dict
```

Returns:

```python
{
  "classification": "INSTITUTIONAL_READY" | "FIXABLE_TO_GRADE" | "STRUCTURAL_PASS" | "INSUFFICIENT_DATA",
  "readiness_pct": float,            # scored criteria points / available points
  "criteria": [ {key, name, score(0|1|2|None=TBD), actual, target, fixable, note} x10 ],
  "scale_check": {nrsf, units, noi, noi_path_months, passes, note},
  "gaps": [ {label, fixable(bool), est_impact_usd, est_timeline, detail} ],
  "premium_factors": [ {key, label, detail} ],   # badges, note §6
  "institutional_exit": {stabilized_noi, exit_cap, compressed_cap,
                         pe_exit_value, uplift_vs_asking, note},
  "deferred_maintenance": {est, risk_premium, price_deduction, note},
}
```

Rules:

- **Structural disqualifiers** (any → `STRUCTURAL_PASS` regardless of score):
  `phase1_status == "rec_found"`; SF/capita > `oversupply_sf_per_capita`;
  `market_verification == "neither"`; pop < 50K AND (growth None or < target);
  `expansion_zoning == "hostile"` when sub-scale.
- **Scale check:** NRSF ≥ 40K (or units ≥ 300) AND NOI ≥ $300K. NOI path: if current
  NOI < $300K, use `va_results[BASE].stabilized_noi` (and scenario base stabilized NOI)
  — path passes if a scenario reaches $300K within `path_to_noi_months`. Sub-scale with
  no path but `expansion_nrsf_potential` present → FIXABLE, not structural.
- **FIXABLE_TO_GRADE:** no structural disqualifier, and ≥1 fixable gap (below-market
  rents, systems missing, opex ratio > 40% with identified reductions, NOI scale with
  modeled path, deferred maintenance < $500K, cosmetic items). Each gap carries
  `fixable=True` + $ impact (reuse `value_add.py` opportunity impacts where overlapping).
- **INSTITUTIONAL_READY:** no structural disqualifier, scale passes, no material gaps.
- **INSUFFICIENT_DATA:** >3 of 10 criteria TBD. Score 0/1/2 per criterion from the
  thresholds; TBD criteria are excluded from `readiness_pct` denominator and listed.
- **Premium-factor badges** (all computed from existing data):
  - Below-market rents at high occ: `rent_analysis["rent_gap_pct"]` in
    `premium_rent_gap` AND physical occ ≥ 0.90. **GOTCHA — two sign conventions:**
    top-level `rent_gap_pct` = (street − in-place)/street (positive = below market);
    nested `rent_gap_analysis.gap_pct` = (in-place − market)/market (negative = below
    market). Use the top-level one here; do not mix them.
  - CC product: `cc_pct` ≥ 0.40.
  - Supply moat: SF/capita < 6 (reuse `filters.sf_per_capita`).
  - Strategic density: comp-DB comps in same state (simple count via existing
    `query_rent_comps(state=...)`); label honestly as same-state activity.
- **Institutional exit:** `stabilized_noi` from VA base (fallback: scenario base
  stabilized NOI); `compressed_cap = base_exit_cap − compression_bps/10000`;
  `pe_exit_value = stabilized_noi / compressed_cap`; `uplift_vs_asking` vs asking
  price. Narrative: "value at PE-grade packaging vs today."
- **Deferred maintenance:** if `deferred_maintenance_est`, deduction =
  est × (1 + risk_premium); surface as adjustment note against `max_offer.max_price`
  (informational row — do NOT modify the solver).

### 6. Engine wiring — `engine.py`

- `AnalysisResult.pe_readiness: dict = field(default_factory=dict)`.
- In `run_analysis`, after gates/risks (step 8), call `score_pe_readiness(...)` with
  the already-computed `financial_analysis`, `rent_analysis`, `scenario_results`,
  `va_results`, `comp_db`. Cheap — no new I/O except the comp-DB count.
- Pass into `generate_memo(...)`; ensure wherever AnalysisResult is serialized into
  `AnalysisRun.result_json` (webapp/views.py) the new key is included.

### 7. Results UI — `webapp/results.py` + templates

- `pe_readiness_context(r)` builder (pure function, preformatted strings — house style).
- New `_tab_pe_readiness.html` partial + tab registration in `analyze.html` (follow
  `_tab_risks.html` pattern): classification banner with tone (ready=pass,
  fixable=warn, structural=fail), 10-criteria score table, fix-list with $/timeline,
  premium badges, institutional-exit panel, DM deduction note.
- `deal_list.html`: small badge on deals whose latest run classification is
  `FIXABLE_TO_GRADE` (that is the deal-hunting signal this feature exists for).

### 8. Memo — `output/memo_writer.py`

New section "Institutional Readiness": classification, criteria table, fixable gap
list w/ $ and timeline, premium factors, PE-exit value vs asking. Placed after
Value-Add, before Risks. CLI `run.py`: one-line classification print in the summary.

### 9. Docs

- `CLAUDE.md`: add `analysis/institutional.py` to architecture tree; note the
  scorecard is informational and does not alter gates.
- `ROADMAP.md`: move a "PE-readiness scorecard" entry to Completed.
- `README.md`: one feature bullet.

## Out of scope (explicit)

- No changes to the 7 gates, recommendation logic, solver internals, or DCF.
- No automated competitor street-rate scraping (rent survey already exists).
- No portfolio/multi-property analysis (ROADMAP already lists separately).

## Risks / gotchas

- **Rent-gap sign conventions** (above) — biggest correctness trap; test both.
- Percent convention in forms: whole-number display / decimal storage. New pct fields
  MUST go through `CIM_PCT_FIELDS` or every display/save round-trips wrong.
- Census growth-rate API: two-vintage ACS comparison is fiddly — any failure → None →
  TBD; never block a run. Keep tests network-free (mock the resolver).
- `result_json` serialization: verify AnalysisRun payload includes the new dict and
  old runs without it render the tab as "not available" (use `.get()` everywhere in
  results.py, matching existing style).
- Old deals/runs lack the new fields — everything must degrade to TBD, never KeyError.

## Validation

- `pytest tests/ -v` — full suite green (watch `test_web_runs.py` /
  `test_web_analyze.py` for result_json shape assertions).
- New `tests/test_institutional.py`: classification rules (each structural
  disqualifier; scale pass/fail/path; fixable-vs-structural taxonomy), both rent-gap
  sign conventions, PE-exit math (hand-computed), DM deduction math, all-None →
  INSUFFICIENT_DATA / TBD behavior, premium-badge triggers.
- Form test: new pct fields round-trip through `build_initial`/`build_overrides`.
- Manual: run one real CIM through the webapp; confirm tab, memo section, deal-list
  badge, and that re-runs of pre-existing deals degrade gracefully.
