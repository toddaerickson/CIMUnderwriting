# Item B — transaction costs + variable hold period

Scope contract: `docs/scoped-backlog.md` § B. Review tier: **high-risk** (money
math; every published IRR changes).

## The defect

Two, with one root cause.

1. **No transaction costs anywhere in the DCF.** `total_basis = price + capex`
   and exit proceeds are the gross `yrN_noi / exit_cap`. Every IRR we publish is
   overstated, and the 10% IRR gate is evaluated on the overstated figure.
2. **The hold period is hardcoded at five years**, so a 3-, 7- or 10-year hold
   cannot be tested.

Root cause: the projection loop exists in triplicate — `analysis/valuation.py:93`,
`model/returns_model.py:132` (sensitivity grid), `model/solver.py:116`. All three
hardcode `range(2, 6)` and all three omit costs. A fourth, structurally different
monthly engine lives in `model/value_add_model.py` (hardcoded `range(60)`).

## Design

### One canonical projection

`analysis.valuation.project_cash_flows()` becomes the single unlevered engine.
It owns the growth banding, the exit-cap coercion rule, the cost arithmetic and
the IRR/MOIC math, and returns everything all three callers need.

```
project_cash_flows(ttm_noi, price, capex, params, *, hold_years,
                   expense_ratio, costs, coerce_exit_cap, exit_cap_override)
    -> {revenue, expenses, noi, entry_cap, requested_exit_cap, exit_cap,
        exit_cap_coerced, exit_value, disposition_cost, net_exit_proceeds,
        acquisition_cost, total_basis, cash_flows, irr, moic, yield_on_cost}
```

- `_run_single_scenario` (valuation) keeps only scenario labelling and
  `noi_per_sf`.
- `_compute_irr_for_sensitivity` (returns_model) and `_compute_irr_at_price`
  (solver) are deleted; both call `project_cash_flows` and read `["irr"]`.

**Growth banding, stated explicitly** (today it is implicit in `range(2, 6)`):
`rev_cagr_yr1_3` applies through year 3; `rev_cagr_yr4_5` applies **year 4
onward** — so a 10-year hold grows years 4–10 at the second rate rather than
falling off the end of the band. Expenses grow at `exp_growth` throughout.

**Exit-cap coercion is a caller policy, not a constant.** The three loops
disagree today, and the disagreement is not all accidental:

| Caller | Today | After | Why |
|---|---|---|---|
| Scenario engine | coerce for base/bear only | unchanged | Bull may underwrite cap compression. |
| Solver | coerce **always** | base/bear only | Latent inconsistency: `scenario="bull"` would coerce here but not in the scenario engine. No caller passes bull today (engine + `run.py` both take the default `"base"`), so no published number moves. |
| Sensitivity grid | never coerce | unchanged | **Deliberate.** The grid's whole purpose is an exit-cap axis; coercing would collapse every cell below entry cap onto one value and destroy the axis. |

### Costs

`config.TRANSACTION_COSTS`: `acquisition_closing_pct` (1.0% — title, legal,
diligence, transfer where applicable) and `disposition_cost_pct` (1.5% — broker
plus closing). Both per-deal editable, because transfer-tax states are exactly
why they cannot be constants.

- Entry: `total_basis = price + capex + price × acquisition_closing_pct`
- Exit: `net_proceeds = exit_value × (1 − disposition_cost_pct)`
- MOIC denominator and yield-on-cost both use the cost-inclusive basis.
- Solver: costs scale with the price being solved for, so they are computed
  **inside** the bisection target function. Bisection stays valid — the target
  is still monotone decreasing in price.

`config.DEFAULT_HOLD_YEARS = 5`, `HOLD_YEARS_RANGE = (1, 10)`.

### Two corrections to the scope contract

1. **The backlog points at the wrong XLSM cell.** It says
   `output/template_writer.py:396` writes "a hardcoded `0` disposition fee" that
   should be wired to the assumption. That cell is `F254` in the *distribution
   waterfall* block — the **GP disposition fee** (a promote-structure fee paid to
   the sponsor), correctly `0` because we model no GP fees. The real cost of sale
   is **`K182 = 0.035`** ("Selling costs"), hardcoded at 3.5% in
   `_write_reversion`. Wiring the broker cost into `F254` would leave `K182`
   still charging 3.5% and double-count. `K182` is what gets wired; `F254` stays
   `0`. The same function hardcodes **`D182 = 60`** (sale month) — that is the
   hold period in the template, and it gets `hold_years × 12`.
2. **`model/value_add_model.py` is added to scope.** The backlog's file list
   omits it. Leaving it out publishes two IRRs computed on different bases and a
   `va_max_offer` that stays overstated — relocating the defect rather than
   fixing it. Its monthly loop is *not* refactored into the canonical projection
   (different engine, and monthly base cash flow is explicitly out of scope); it
   gets the same cost arithmetic and its `range(60)` / `range(5)` bounds
   parametrized on `hold_years`.

### Plumbing

Three new per-deal assumptions follow the `solver_target_irr` path exactly:
form field → `build_overrides` delta → `_analysis_worker` → `run_analysis(...)`
→ model. `override_key_registry()` grows `TRANSACTION_COSTS.*` and
`DEFAULT_HOLD_YEARS` derived live from `config.py`, per the no-drift rule.

**Run stamping is unconditional, not delta-only.** `build_overrides` records
deltas, so a run at defaults would record nothing and an old run would be
silently different from a new one rather than self-describing. The three
resolved values are written into the run's `applied_overrides["assumptions"]`
whatever they are.

## Build order

1. Characterization tests pinning today's exact IRR/MOIC (pre-refactor).
2. `config.py` constants + override registry.
3. `project_cash_flows()`; scenario engine delegates to it.
4. Delete the solver and sensitivity duplicates.
5. Value-add model: costs + hold bounds.
6. Web plumbing (form, services, engine) + unconditional stamp.
7. Outputs: memo, `excel_writer`, `template_writer`.

## Acceptance

- `grep -c "range(2, 6)"` over `analysis/` and `model/` returns 0.
- **Zero-cost regression proof**: with both percentages at 0, every pinned
  IRR/MOIC from step 1 reproduces to 1e-9. This is what proves the refactor
  moved no number on its own.
- Hold-period test: 3-, 5- and 10-year holds each produce the right series
  length and a hand-computed IRR.
- Cost oracles at defaults are hand-computed, never copied from output.
- Solver round-trip: solved max price re-run forward through the DCF returns the
  target IRR within tolerance **with** costs applied.
- Solver monotonicity: max price falls as either cost percentage rises.

## Known consequence — say it out loud in the PR

Every stored `AnalysisRun` IRR becomes non-comparable to new runs. Historical
runs keep their stored numbers; the mitigation is the unconditional stamp above,
so an old run is self-describing rather than silently different. Expect roughly
30–60 bps of IRR compression on a typical deal, and the 10% gate now bites
marginal deals it previously passed. That is the point of the item.

## Out of scope

Monthly cash flow for the base model (annual is correct for a stabilized
acquisition screen). Tax treatment of costs — this is a pre-tax model. Debt and
the waterfall (item E hangs off the `project_cash_flows` seam this creates).
