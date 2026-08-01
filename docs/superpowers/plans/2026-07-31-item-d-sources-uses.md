# Item D — Sources & Uses + capital stack (H folded in)

Scope contract: `docs/scoped-backlog.md` § D, plus item H (CapEx input toggle)
folded in per the investor-readiness ranking. Review tier: **standard** by the
contract, run as **high-risk** here — the change reaches `total_basis`, the
MOIC denominator and both solvers, and CLAUDE.md says anything touching money
gets the full cycle.

## The gap

There is no statement anywhere in the pipeline of what it costs to do the deal
or where the money comes from. `total_basis = price + capex + acquisition_cost`
is computed inside `project_cash_flows` and never decomposed; nothing names the
equity check, nothing splits it GP/LP, and there is no slot for debt when item
E arrives. TSM's Model Outputs sheet leads with exactly this block.

Item H is the same input surface: CapEx is a raw dollar box today, so an
analyst who thinks in `$/SF` or `% of price` does the arithmetic in their head
and types the product — untraceable, and wrong the moment the price moves.

## Design

### One resolver for "an amount entered on some basis"

```
resolve_capital_amount(value, basis, *, nrsf=None, units=None, price=None)
    -> float dollars
```

Bases: `amount` (dollars, the default and today's behavior), `per_sf`,
`per_unit`, `pct_price`. Unknown basis → treated as `amount` and logged, never
raised: a stored override from a future basis must not take down a run.

CapEx accepts all four. The operating reserve accepts `amount` and `per_sf`
only — the contract names those two, and a reserve as a percentage of price is
a category error (it is months of operating expense, not a share of value).

**Defaults are chosen so nothing published moves.** `capex_basis` defaults to
`amount` and the reserve defaults to `$0`, so every existing IRR, MOIC and
solved max price reproduces exactly. That is the regression proof, and it is
why this lands as one PR rather than behind a flag.

### The reserve enters the basis, and is not released at exit

The contract's invariant — *total uses = total sources = the DCF's
`total_basis`* — forces the reserve into `total_basis`; it cannot sit in Uses
and outside the DCF without the two disagreeing by construction. So
`project_cash_flows` gains `reserve=0.0` and

```
total_basis = price + capex + acquisition_cost + reserve
```

**It is not returned at exit.** Two defensible treatments exist (release the
unspent balance at sale, or treat it as spent) and the release treatment is the
more common one in institutional models. We take the conservative one on
purpose: releasing the reserve assumes it was never needed, which is precisely
the assumption a reserve exists to hedge, and this pipeline's stated posture is
that a number we publish must not be the overstated one. The cost of being
wrong is ~15–25 bps of IRR on a realistic reserve, always in the direction of
understating. Flip it by adding the balance to `net_exit_proceeds` — one line,
one oracle — if the operator would rather model the release.

### `build_sources_uses` — a pure function in `model/returns_model.py`

No new file. Signature:

```
build_sources_uses(price, capex, *, acquisition_cost=0.0, reserve=0.0,
                   financing_costs=0.0, senior_debt=0.0,
                   gp_coinvest_pct=None) -> dict
```

Uses: purchase price · acquisition closing costs · upfront CapEx · operating
reserve · financing costs (0 until E1). Sources: senior debt (0 until E1) · GP
co-invest · LP equity.

Equity is the plug: `total_equity = total_uses − senior_debt`, split
`gp_equity = total_equity × gp_coinvest_pct`, `lp_equity` the remainder. That
ordering is what makes the block correct the day E1 sizes a loan — debt
displaces equity, it does not add to uses.

`gp_coinvest_pct` defaults to `config.GP_COINVEST_PCT` (0.10, the design doc's
market term) read at CALL time, not bound at import.

### Nothing goes into `_PATCHED_DICTS`

The new settings are plain module scalars in `config.py`
(`GP_COINVEST_PCT`, `DEFAULT_OPERATING_RESERVE`, `DEFAULT_CAPEX_BASIS`,
`DEFAULT_OPERATING_RESERVE_BASIS`) and travel as **parameters**, resolved once
at the `webapp.services` boundary and passed down whole — the same lane
`DEFAULT_HOLD_YEARS` and `SOLVER_TARGET_IRR` already occupy.

This is deliberate. Item B's shipped bug came from adding `TRANSACTION_COSTS`
to `_PATCHED_DICTS`: the live dict is mutated in place under `_ANALYSIS_LOCK`
for one deal's run, so anything resolving it outside that lock reads another
deal's values. Not adding a dict is strictly cheaper than adding one and then
having to resolve it from `_ORIG_CONFIG` everywhere. The cost is that GP
co-invest and the reserve are per-deal-only, not settings-editable — stated in
the PR, revisit if the operator wants a global default.

### `% of price` CapEx must scale inside the solver

`solve_max_price` bisects on price. If CapEx is entered as a percentage of
price, holding it at the asking-price dollars while the solver tries a lower
price prices the wrong deal — the identical defect item B fixed for closing
costs. Both solvers take `capex_pct_of_price=None`; when set, CapEx is resolved
from the trial price inside the loop.

Monotonicity survives: `total_basis = price × (1 + acq% + capex%) + reserve` is
still strictly increasing in price, so IRR is still decreasing in it and
bisection is still valid. Tested by re-running the solved price forward.

### Check 11

`sources_uses_ties` joins the register in `analysis/checks.py`, **blocking**
per the contract, comparing `total_uses`, `total_sources` and the base
scenario's `total_basis` to the dollar. It is an identity over numbers we
compute ourselves, so a failure means a bug, not a bad input — which is exactly
what deserves the loud severity. It reports `skipped` on the assumptions form,
which cannot see a DCF.

### Surfaces

- **Summary tab** — a "Capital" block beside the existing content, not a new
  tab (the dense-model-view rule holds).
- **Excel** — a "Sources & Uses" sheet via the existing `create_sheet` pattern.
- **Memo** — a subsection under section 6.
- **Assumptions form** — reserve + basis, CapEx basis, GP co-invest %, added to
  the existing Timing & Transaction Costs block rather than a new section.

## Build order

1. `resolve_capital_amount` + `build_sources_uses` + `reserve` in
   `project_cash_flows` — with tests, before any wiring.
2. Engine / solver / value-add threading.
3. Check 11.
4. Form fields, override plumbing, run stamping.
5. The three output surfaces.
6. UI compaction + adversarial density pass on the two touched templates.

## Acceptance

- Sources = Uses = `total_basis` in every scenario.
- Debt at 0 → the stack is 100% equity and equity equals the DCF year-0 outflow.
- Reserve entered as `$/NRSF` and as `$` produce identical results.
- CapEx at `$0.50/SF` on 50,000 NRSF == CapEx at `$25,000`; `% of price` moves
  with the solved price.
- Every existing IRR/MOIC/max-price oracle reproduces unchanged at the defaults.
- `python -m pytest tests/ -v` green, count up from 343.

## What the review caught

Four findings from the pre-push review, all repaired in the same branch.
Recorded here because three of them are the *general* shape of defect this
kind of change produces, not one-off slips.

1. **A basis selector reinterprets the number beside it.** A genuine `2`
   under `% of price` becomes `$2` under `$ total` the moment the selector
   moves — silently removing real CapEx from the basis and *overstating*
   every return, which is the exact direction item B exists to prevent.
   There is no JavaScript on this page to re-key the field, so the fix is
   a hidden unit stamp naming the basis the page was DRAWN under: the
   first save after a unit change is refused, and the re-render stamps the
   new selection so the second proceeds. It is a confirmation, not a
   detector — the stamp cannot tell whether the analyst restated the
   figure, and the message says so.
2. **The sensitivity grid did not rescale a %-of-price CapEx.** Both
   solvers got `capex_pct_of_price`; the grid, whose row axis *is* price,
   did not — so every row but the centre computed a deal whose CapEx had
   not moved with its price. Same parameter, threaded through
   `_build_sensitivity`.
3. **A rate can resolve to $0 long after the form validated it.** The
   form refuses a rate with no driver, but a saved basis outlives the
   value it was checked against: re-extraction rewrites `cim_json`, so a
   re-parse that loses NRSF turns a valid `$0.50/SF` CapEx into $0 on the
   next run, showing as a quiet $0 line. The engine now raises it as a run
   warning — an empty state must not hide a real failure.
4. **Solver non-convergence was invisible on the web page.** The bisection
   returns a price no matter what; `converged` reached the Excel tab and
   nothing else, so a bound it never got away from read exactly like a
   solved answer. Now flagged on the returns tab. Pre-existing, but the
   unbounded reserve dial makes it materially easier to hit.

## Out of scope

Construction draw schedules and per-line-item timing. Multi-tranche debt.
Financing costs beyond a zero placeholder — E1 fills it. Making GP co-invest a
globally editable setting.
