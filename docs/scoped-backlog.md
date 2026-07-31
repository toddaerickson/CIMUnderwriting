# Scoped backlog — capital-structure & model-integrity items

Scoped 2026-07-29. Source: gap triage of the CIM Analyst pipeline against the
Top Shelf Models "TSM Storage Development Model" ($1,325 Excel), plus defects
found while reading the DCF code during that triage.

**Status: scoped, not started.** Everything here queues behind the in-flight
dense-model-view build (`.superpowers/sdd/2026-07-29-dense-model-view/`, T8–T9).
Each item gets its own `docs/superpowers/plans/<date>-<slug>.md` when it reaches
the front of the queue — this file is the scope contract, not the build plan.

Only the five items the operator selected are scoped here (a, b, d, e, g of the
triage). Items c (property-tax millage), f (exit-cap comp panel) and h (CapEx
input toggle) stay in the loose queue.

## Build order

Dependencies are real, not preference — B extracts the shared cash-flow
projection that D, E and G all read from.

| # | Item | Depends on | Effort | Review tier |
|---|------|-----------|--------|-------------|
| A | Model error-check register | — | Medium-small | Standard |
| B | Transaction costs + variable hold period | — | Medium | **High-risk** (money math) |
| D | Sources & Uses + capital stack | B | Small | Standard |
| G | LP-facing 2-page investor summary | D to build, **E4 to ship** | Small-medium | Standard |
| E1 | Debt layer (`model/debt.py`) | B | Medium | **High-risk** |
| E2 | Single-tier waterfall (`model/waterfall.py`) | E1 | Medium | **High-risk** |
| E3 | Levered wiring (assumptions / results / memo / xlsx) | E2, D | Medium | High-risk |
| E4 | Solver retargeted to LP net IRR | E3 | Small | **High-risk** |

Sequence: **A → B → D → E1 → E2 → E3 → E4 → G**. A goes first because it is the
cheapest and because its checks guard B's arithmetic while B changes it.

**G moved after E** (revised 2026-07-31; the table above still lists G's hard
dependency as D, which remains true). The table's "(E preferred)" note
understates it: an LP-facing summary produced before the LP-net-IRR engine
exists can only quote a property-level unlevered IRR — the number that document
specifically exists to replace. The fund mandate is a 15% LP *net* IRR, so G
ships once E4 can compute it.

---

## A. Model error-check register

**Why.** TSM ships a dedicated Error Check sheet. We have exactly one check —
the Revenue − Expenses = NOI identity in `AssumptionsForm.clean()`
([forms.py:203](../webapp/forms.py#L203)) — and everything else fails silently.
The Abilene CIM's `$1` property-tax line reached the model unflagged. This is
also the CLAUDE.md auditability requirement: every value a user sees should be
traceable, and a wrong input should announce itself.

**Scope.** New `analysis/checks.py` — pure functions, no Django import, so the
same registry runs from the assumptions form, the live preview, the engine, the
memo, and the Excel writer. One check = `(id, severity, message, values, source)`.

Two severities:
- **blocking** — form invalid unless accepted with a note (reuse the existing
  accept-with-discrepancy mechanism, do not invent a second one)
- **advisory** — always shown, never blocks

Checks in scope (all computable from fields that exist today):

1. Income identity: Revenue − Expenses = NOI — *migrate* the existing check into
   the registry, keeping its tolerance and accept path. Do not copy it; this
   must retire the duplicated one-liner between `clean()` and
   `model_strip_context` (accepted-minor from dense-model-view T6). **blocking**
2. Unit-mix SF reconciliation: Σ(`unit_sf` × `count`) vs stated `nrsf`, 2% tol. **blocking**
3. Unit-mix GPR reconciliation: Σ(`count` × `monthly_rate` × 12) vs stated GPR,
   3% tol — gross potential, so vacancy/concessions are *not* the explanation
   for a large miss. **advisory**
4. Occupancy sanity: physical and economic both in [0, 1]; economic ≤ physical
   (the mismanagement spread gate assumes that direction). **blocking**
5. EGR ≤ GPR. **blocking**
6. OpEx/EGR inside the 35–55% band from `EXPENSE_BENCHMARKS["opex_revenue_ratio"]`. **advisory**
7. Total OpEx $/NRSF inside $3.00–$5.50 from `EXPENSE_BENCHMARKS["total_opex"]`. **advisory**
8. Per-line expense floor: any expense category at zero, blank, or below half its
   benchmark low — the `$1 property tax` catcher. **advisory, loud**
9. Exit cap ≥ entry cap: today [valuation.py:110](../analysis/valuation.py#L110)
   *silently coerces* exit cap up to entry cap for bear/base. Surface the
   coercion instead of hiding it. **advisory**
10. Price vs replacement cost: $/SF against the band already computed in
    `analysis/physical.py`. **advisory**
11. Sources & Uses ties to DCF total basis — added with item D. **blocking**

Deferred: TTM annualization sanity (needs a reporting-period field the parser
does not extract yet).

**Out of scope.** Auto-repair of bad inputs. Checks that need data we do not
have (permits, third-party comps). A configurable rules engine — the check list
is code, reviewed like code.

**Files.** New `analysis/checks.py`; edit [webapp/forms.py](../webapp/forms.py)
(clean → registry), [webapp/views.py:256](../webapp/views.py#L256) (preview
partial), `webapp/templates/webapp/_model_preview.html` and `_tab_summary.html`
(check panel), [engine.py](../engine.py) (run checks, attach to the result),
[output/excel_writer.py](../output/excel_writer.py) (new "Checks" sheet, follows
the existing `create_sheet` pattern), [output/memo_writer.py](../output/memo_writer.py)
(short "Model Checks" block in section 1).

**Acceptance.**
- Every check has a unit test with a passing case, a failing case, and a
  boundary case at its tolerance.
- The existing identity tests in [tests/test_web_deals.py:199](../tests/test_web_deals.py#L199)
  still pass unmodified — proof the migration preserved behavior.
- A run with a `$1` property-tax line produces check 8 in the memo, the Excel
  Checks sheet, and the results page.
- Blocking checks block the save; advisory checks never do.
- `./verify.sh` prints `VERIFY: PASS` (or the repo's pytest gate is green if
  no verify.sh exists).

---

## B. Transaction costs + variable hold period

**Why.** Two defects, one fix. (1) No acquisition closing costs and no
disposition costs anywhere in the DCF — `total_basis = price + capex` and exit
proceeds are the gross `yr5_noi / exit_cap`. Every IRR we publish is overstated
by roughly 30–60 bps against a net-of-costs number, and the 10% IRR gate is
evaluated on the overstated figure. (2) The five-year hold is hardcoded, so a
3-, 7- or 10-year hold cannot be tested at all — TSM's timing assumptions block
is the thing we're actually missing.

**Underlying problem: the projection loop exists in triplicate** —
[valuation.py:93](../analysis/valuation.py#L93),
[returns_model.py:132](../model/returns_model.py#L132),
[solver.py:116](../model/solver.py#L116). All three hardcode `range(2, 6)` and
all three omit costs. Fixing this in three places is how they drift again.

**Scope.**
1. Extract one canonical projection into
   [analysis/valuation.py](../analysis/valuation.py) — a `project_noi(...)`
   returning revenue/expense/NOI series and the unlevered cash-flow vector.
   `returns_model.py` and `solver.py` import it; the duplicate loops are deleted.
   No new file. This is also the seam item E hangs the debt layer on.
2. `hold_years` assumption — default 5, range 1–10, per-deal editable, stored in
   the run's assumption snapshot.
3. Growth banding generalized: `rev_cagr_yr1_3` applies through year 3,
   `rev_cagr_yr4_5` applies year 4 onward. State this in the docstring; today it
   is implicit in `range(2, 6)`.
4. `TRANSACTION_COSTS` in [config.py](../config.py):
   `acquisition_closing_pct` (default 1.0% of price — title, legal, diligence,
   transfer where applicable) and `disposition_cost_pct` (default 1.5% — broker
   plus closing). Both per-deal editable via the assumptions form and
   `ConfigOverride`; transfer-tax states are the reason these must not be
   constants.
5. Entry: `total_basis = price + capex + price × acquisition_closing_pct`.
   Exit: net proceeds = `exit_value × (1 − disposition_cost_pct)`.
   MOIC denominator uses the cost-inclusive basis.
6. Solver: closing costs scale with the price being solved for, so the bisection
   target function must include them inside the loop, not add them after.
   Bisection stays valid — the function is still monotone in price.
7. [output/template_writer.py:396](../output/template_writer.py#L396) currently
   writes a hardcoded `0` disposition fee into the XLSM; wire it to the
   assumption.

**Out of scope.** Monthly cash flow for the base model (annual is correct for a
stabilized acquisition screen; the value-add module already runs 60 months).
Tax treatment of costs (capitalize vs expense) — pre-tax model.

**Files.** [analysis/valuation.py](../analysis/valuation.py),
[model/returns_model.py](../model/returns_model.py),
[model/solver.py](../model/solver.py), [config.py](../config.py),
[webapp/forms.py](../webapp/forms.py), the assumptions/model-view templates,
[output/excel_writer.py](../output/excel_writer.py),
[output/template_writer.py](../output/template_writer.py),
[output/memo_writer.py](../output/memo_writer.py).

**Acceptance.**
- One projection function; `grep -c "range(2, 6)"` over `analysis/` and `model/`
  returns 0.
- Hold-period test: a 3-, 5- and 10-year hold each produce the right series
  length and a hand-computed IRR.
- Cost test: with both cost percentages at 0, every existing IRR/MOIC oracle in
  [tests/test_valuation.py](../tests/test_valuation.py) and
  [tests/test_solver.py](../tests/test_solver.py) reproduces exactly — that is
  the regression proof. With defaults on, new oracles are hand-computed, not
  copied from output.
- Solver test: solved max price at target IRR, re-run forward through the DCF,
  returns the target IRR within tolerance *with* closing costs applied.

**Known consequence — say it out loud in the PR.** Every stored `AnalysisRun`
IRR becomes non-comparable to new runs. Historical runs keep their stored
numbers; the fix is that `hold_years` and both cost assumptions must be written
into the run's assumption snapshot so an old run is self-describing rather than
silently different.

---

## D. Sources & Uses + capital stack

**Why.** TSM's Model Outputs sheet leads with sources/uses and capital stack;
we produce neither, so there is no statement anywhere of what it costs to do
the deal or where the money comes from. It is useful unlevered today and it is
the schema item E plugs debt into, so it is not throwaway.

**Scope.** A pure `build_sources_uses(...)` in
[model/returns_model.py](../model/returns_model.py) — no new file.

Uses: purchase price · acquisition closing costs (from B) · upfront CapEx ·
operating/working-capital reserve · financing costs (0 until E1) · total uses.
Sources: senior debt (0 until E1) · GP co-invest · LP equity · total sources.

New assumption: upfront operating reserve, entered as `$` or `$/NRSF`. Keep it
distinct from the `cap_reserve` expense benchmark, which is an annual OpEx line
— different thing, same word, and confusing them is the obvious failure mode.
GP co-invest % is an input here and is reused by E2.

Surfaces: a "Capital" block on the existing summary tab (not a new tab — the
dense-model-view density rule holds), a "Sources & Uses" sheet in
[output/excel_writer.py](../output/excel_writer.py), and a subsection under memo
section 6.

**Invariant.** Total uses must equal total sources must equal the DCF's
`total_basis`. That equality is check 11 in item A, and it is what stops the
capital stack and the returns model from quietly disagreeing.

**Out of scope.** Construction draw schedules and per-line-item timing —
development machinery, we buy operating assets. Multi-tranche debt (senior +
mezz); one senior loan until proven otherwise.

**Acceptance.** Sources = Uses = `total_basis` in every scenario, tested. With
debt at 0 the stack is 100% equity and the equity figure equals the DCF year-0
outflow. Reserve entered as $/NRSF and as $ produce identical results.

---

## E. Levered returns + single-tier LP waterfall

**Why.** The fund mandate is a 15% LP **net** IRR; we can only produce a
property-level unlevered IRR. This is the one place a $1,325 spreadsheet answers
a question our pipeline structurally cannot.

Design, market terms, common modeling errors and **verified numeric test
oracles** already exist in [docs/levered-waterfall-design.md](levered-waterfall-design.md).
Do not re-research it — execute it.

**Structure confirmed by the operator 2026-07-29: one tier only.** GP takes an
annual management fee, invests capital upfront alongside the LPs, and earns an
x% promoted interest above a y% preferred return. No catch-up, no clawback, no
second hurdle. This is exactly the single-hurdle accrual-account formulation the
design doc proves is deterministic — a forward loop, no solver — and it
retires any notion of a configurable N-tier waterfall builder. Pref rate and
promote split are parameters (`y`, `x`); the *number of tiers* is not.

**E1 — `model/debt.py`.** `DebtTerms` dataclass; `size_loan()` returning the
loan and the **binding constraint label** as min(LTV cap, DSCR cap, debt-yield
cap) — never LTV alone; monthly amortization roll-forward aggregated to annual
debt service plus payoff balance; origination and exit fees. Design-doc oracles
4 and 5. Not blocked by any LPA question — buildable immediately after B.

**E2 — `model/waterfall.py`.** `WaterfallTerms` (`pref_rate`,
`pref_compounding`, `promote_split`, `gp_coinvest_pct`, accrual base, ordering);
`run_waterfall(contributions, distributions, terms)` → per-period LP/GP rows,
LP net IRR, LP MOIC. GP co-invest is pari passu through the pref; promote is
computed on the LP-attributable residual only. The 1% AM fee is a cash-flow line
deducted before the waterfall. Design-doc oracles 1–3.

**E3 — wiring.** Debt + waterfall inputs on the assumptions page; levered
results as a **second lens beside the unlevered screen, which stays primary**;
memo section; Excel sheet. Reads the Sources & Uses block from D for equity.

**E4 — solver retarget.** Max price for a 15% LP net IRR instead of a 10%
unlevered IRR (existing ROADMAP item). The solved price now moves debt sizing,
which moves equity, which moves the waterfall — confirm the target function is
still monotone before trusting the bisection, and add a convergence test that
re-runs the solved price forward through the full levered stack.

**Assumption stamp instead of a block.** The design doc lists 7 LPA
confirmations. One tier plus the operator's no-clawback term answers two of
them. Five still change the number and stay open:

| # | Open question | Build default | Effect if wrong |
|---|---------------|---------------|-----------------|
| 1 | Pref simple or compounded, and at what frequency | annually compounded | ~19% swing in GP promote |
| 2 | Accrual base: contributed/unreturned vs committed | contributed/unreturned | material on slow-draw deals |
| 3 | ROC-before-pref or pref-before-ROC | ROC first (only matters if simple) | $81,600 vs $72,000 GP on the doc's fixture |
| 4 | AM fee above the waterfall or netted from LP distributions | above (deal expense) | shifts LP net IRR directly |
| 5 | Promote on 100% of residual or LP-attributable share only | LP-attributable only | GP overpaid by its co-invest share |

Build with these as **named parameters carrying the defaults above**, and print
the resolved assumption set next to every LP net IRR we display. That converts a
blocker into a labeled assumption — which is the same discipline the rest of the
pipeline already uses — but the number is not decision-grade until the LPA is
read. Do not let an LP net IRR leave the building without its stamp.

**Out of scope.** Construction debt and a forward SOFR curve (development
machinery; a checked-in curve is a hardcoded constant that goes stale, which the
data rules forbid — use one rate assumption plus a sensitivity band). Catch-up
and clawback mechanics. Multi-hurdle IRR-lookback waterfalls. Tax
distributions.

**Acceptance.** Every numeric oracle in the design doc reproduces to the cent /
to 4 decimal places on IRR. Debt sizing reports which of the three constraints
bound. Unlevered results are unchanged by the presence of the levered layer —
regression-tested, since the unlevered screen remains the primary gate.

---

## G. LP-facing 2-page investor summary

**Why.** [output/memo_writer.py](../output/memo_writer.py) produces a 9-section
internal IC memo. There is no condensation for anyone outside the firm, and TSM
sells its 2-page Investor Summary as a headline feature. Near-zero incremental
logic — it is a second rendering of numbers we already compute.

**Scope.** `generate_investor_summary(...)` alongside `generate_memo` in the
same module — no new file. Two pages, hard limit, which means a fixed section
list and explicit truncation rules rather than a shrunken IC memo:

Page 1 — property header and photo slot · three-bullet thesis · key metrics
table (price, $/SF, entry cap, exit cap, Year-1 yield on cost, unlevered
IRR/MOIC, and after E: LP net IRR/MOIC and equity required) · Sources & Uses
from item D.
Page 2 — scenario returns table (bear/base/bull) · market snapshot (3-mile
population, SF per capita, MSA classification) · top 3 risks only, by severity ·
footer legend.

Downloadable next to the existing memo/xlsx buttons on the results page.

**Out of scope.** Per-line expense tables, the full risk register, gate
mechanics, replacement-cost derivation — all of that stays in the IC memo. Page
count is the constraint that keeps it honest.

**Flag before this goes to anyone outside the firm.** A document written for
prospective investors edges toward securities-marketing territory, which sits
behind the operator's General Counsel gate. Build it as an internal /
prospect-discussion document with a plain "not an offer to sell securities"
legend, and route the final wording past GC before any external distribution.
The build is not blocked; the distribution is.

**Acceptance.** Renders to exactly 2 pages with the longest realistic deal
(longest property name, all three scenarios populated, 3 risks at maximum
message length) — assert the page count, do not eyeball it. Every number on the
page reads from the same result dict the IC memo uses; no recomputation, no
second source of truth. Degrades cleanly when the levered layer is absent.

---

## Deliberately not building (from the same triage)

Recorded so these do not get re-proposed. Rationale in the 2026-07-29 triage.

- Construction budget with per-line draw timing — we buy operating assets.
- Construction debt and a forward SOFR curve — see item E out-of-scope.
- Monthly cash flow for the base acquisition model — annual is correct; the
  value-add module already carries 60-month granularity where timing matters.
- Circular-reference break and cross-sheet hyperlinks — artifacts of Excel
  being the runtime.
- CoStar comp-export importers — brittle against a vendor schema, presumes a
  subscription.
- Embedded instruction manual — a manual is for a file someone else operates;
  ours is a web app used by its authors. Field-level provenance beats it.
- A configurable N-tier waterfall builder — one tier, per the operator.
  Configurability is TSM's product because they sell to many buyers; for us it
  is cost with no consumer.
- Competing on spreadsheet surface area — we already emit both an .xlsx returns
  model and the pre-filled Spencer Burton .xlsm. Our edge is upstream:
  extraction, gates, and benchmark-adjusted NOI.
