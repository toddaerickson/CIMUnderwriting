# Dense Model View — design spec

Date: 2026-07-29 · Status: approved by operator (brainstorm session) ·
Implementation: single release (operator decision), sliced into commits
within one PR.

## Goal

Replace the two-column assumptions form with a single-page, vertical,
driver-first "model view" — spreadsheet-dense but schema-backed. Outcome of
the form-vs-spreadsheet debate (3-analyst panel, unanimous): the XLSM stays
the model of record; the app's edge is provenance, validation, and gates.
This page closes the gap that debate identified: editing friction and
buried drivers.

## Locked decisions (operator)

1. **Replaces** the assumptions page outright — no parallel view.
2. Header strip = **Market screen**: Population 3-mi, SF/capita, Median
   HHI 3-mi (+ NOI-identity status chip).
3. Expense rows = **full provenance**: CIM (read-only) | Analyst
   (editable) | Used (computed) | Flag.
4. Scope = **everything in one release**: layout + per-line expenses +
   rate/momentum drivers + their analysis wiring.
5. Live recalc = **server-computed htmx preview** (single source of truth
   for formulas; no JS math, no new dependencies).

## Page architecture (top → bottom)

1. Sticky action bar (existing, PR #13) — Save / Save & Run + error banner.
2. **Header strip** (computed, htmx-refreshed): Population 3-mi with
   source tag (CIM / operator / Census); SF/capita vs
   `GATES.max_sf_per_capita` (green ≤ threshold, red above, "—" until
   supply entered); Median HHI 3-mi; NOI identity chip (OK / off by $X /
   accepted).
3. Property identity block (compact): name, address, city, state, MSA,
   market verification dropdown.
4. **Drivers** — vertical rows `label | input | source tag`:
   - Pricing: asking price, CapEx estimate.
   - Occupancy: physical, economic, computed spread (highlighted when
     ≥ `GATES.econ_phys_spread_flag`).
   - Rate positioning: in-place avg rent $/SF/mo (computed from unit mix,
     analyst-overridable), street rate (EXISTING `market_rent_psf`
     relabeled "Street Rate ($/SF/mo)" — no new field), street-rate trend
     (choice: rising / flat / falling / unset), computed rent gap %.
   - Momentum: T3-annualized revenue; computed T3-vs-T12 %.
   - Supply (existing, PR #14): competitive SF, pipeline SF.
5. **Income & Expenses**: TTM totals (identity-checked as today) + expense
   table, one row per benchmark category:
   `Category | CIM | Analyst input | Used | Flag` with a Total OpEx +
   $/SF footer. Mgmt fee (% EGR) and capital reserve rows included.
6. Unit-mix table (existing editor, restyled to match).
7. Collapsed `<details>` sections: Scenarios (Bear/Base/Bull), Value-Add,
   Replacement Cost, Solver — unchanged content, denser styling.

## Data model changes (no DB migration — JSON snapshot/override flow)

New `CIMData` fields (old snapshots resolve to `None`):
- `in_place_avg_rent_psf: Optional[float]` — analyst override; when None,
  engine computes occupied-weighted in-place $/SF/mo from unit mix.
- `street_rate_trend: Optional[str]` — `rising | flat | falling`.
- `t3_annualized_revenue: Optional[float]`.

New override key (not a CIMData field): `expense_line_overrides:
dict[benchmark_key, float]` stored inside `assumption_overrides`
(`cim_overrides` sibling). Round-trips through `build_initial` /
`build_overrides` like scenario overrides (deltas only; blank = no
override).

## Engine changes

- `analysis/financials.py`: `_map_expense_lines` output is merged with
  `expense_line_overrides` — **analyst value wins over CIM-extracted** for
  that category; the existing benchmark adjustment then computes "Used"
  unchanged. Adjustment notes name the source ("analyst-entered").
- `analysis/rent_analysis.py`: expose in-place avg rent (computed or
  override) + rent gap % for the drivers section and preview.
- `analysis/risks.py`: two new risk flags (no new gates; the 7 gates are
  stable): (a) ECRI-in-falling-market — rent-gap bridge present AND
  `street_rate_trend == "falling"`; (b) negative momentum — T3-vs-T12 < 0
  while underwriting assumes revenue growth.
- Memo: risk items flow through existing generic rendering; expense table
  in the memo gains the analyst column source note.

## Preview endpoint

`POST /deals/<pk>/assumptions/preview` (login-required, CSRF-exempt NO —
normal CSRF, htmx sends the token):
- Binds `AssumptionsForm` on the posted data; ignores validity for
  preview purposes (renders what it can; identity chip shows the failure
  state instead of blocking).
- Builds an in-memory cim_data = snapshot + submitted values (same merge
  as save, NOT persisted).
- Computes ONLY: header-strip values, expense table rows via
  `analyze_financials` (fast path — no scenarios, no solver, no writers),
  NOI identity state.
- Returns one partial template; htmx `hx-trigger="change delay:400ms from:form"`
  swaps strip + expense table + chip.
- Failure → swap in a "preview unavailable" badge; editing and Save are
  never blocked by preview problems. No DB writes on this path, ever.

## Provenance tags

Per editable row, a small source tag: `CIM` (snapshot value, untouched) /
`you` (override differs from snapshot) / `Census` (enrichment source_log
tier 2) / `derived` (computed default, e.g. in-place rent from unit mix).
Data sources: `deal.cim_json` vs `assumption_overrides` vs the run
payload's `enrichment.source_log` (PR #11).

## Error handling

- Save path is byte-for-byte today's: full validation, NOI identity block
  with accept checkbox, `build_overrides` deltas.
- Preview never mutates, never blocks, degrades to badge.
- Legacy snapshots (missing new keys) render as blank inputs with `CIM`
  tag absent — same behavior as PR #14 fields.

## Testing

- Round-trip tests: 3 new CIMData fields + `expense_line_overrides`
  (build_initial/build_overrides deltas, old-snapshot None defaults).
- Precedence test: analyst expense line beats CIM line; benchmark
  adjustment still applies on top.
- Preview contract test: posts form data, asserts strip values + expense
  rows + no DB writes (Deal/AnalysisRun row counts unchanged).
- Risk-flag tests: ECRI-falling and negative-momentum trigger conditions.
- Template render smoke via existing view tests; suite baseline 184 stays
  green.
- UI: TWO independent agents — layout/compaction pass, then fresh-context
  adversarial density pass (major layout work rule). Both must pass
  before merge.

## Out of scope (separate queue items)

Levered returns + waterfall module (docs/levered-waterfall-design.md, ⚑
LPA answers pending); property-tax millage module; benchmark-source
labeling; settings scope-labeling PR; comps↔pipeline promotion.

## Risks

- Biggest review surface of the queue (operator chose single release):
  mitigated by commit slicing (fields → engine → preview → template) and
  the two-pass UI review.
- Preview latency on Render free-tier cold paths: acceptable (~200ms warm;
  preview is advisory only).
- `market_rent_psf` relabel must not change semantics — it already feeds
  value-add analysis as market rent; the label change is display-only.
