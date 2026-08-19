# Scoped backlog — capital-structure & model-integrity items

Scoped 2026-07-29. Source: gap triage of the CIM Analyst pipeline against the
Top Shelf Models "TSM Storage Development Model" ($1,325 Excel), plus defects
found while reading the DCF code during that triage.

**Status (2026-08-09): A, B, D, E1–E4, G and T all shipped.** The header below
said "scoped, not started" through the whole build; it is corrected here rather
than deleted, because the build order underneath it is still the record of why
the items shipped in the sequence they did. Each item got its own
`docs/superpowers/plans/<date>-<slug>.md` as it reached the front of the queue —
this file is the scope contract, not the build plan.

Item T's six categories closed in PRs #40, #46, #47, #49, #50 and this one.
~~The one acceptance criterion still OPEN across the whole file is the general
numeric-literal sweep under item T~~ — that sweep closed in PR #59 (and PR #64
extended it into `output/`); for a while this header said OPEN while the
Acceptance list below said DONE, and the self-contradiction is corrected here
rather than deleted because a file disagreeing with itself is the same defect
class this item existed to remove.

Only the five items the operator selected are scoped here (a, b, d, e, g of the
triage). Items c (property-tax millage) and f (exit-cap comp panel) stay in the
loose queue. Item h (CapEx input toggle) was listed loose but actually shipped
folded into item D — the CapEx basis toggle lives at `webapp/forms.py`
(`CAPITAL_KEYS` / `capex_basis`) and `engine.py:355-375`; see
`docs/superpowers/plans/2026-07-31-item-d-sources-uses.md`.

Item T (transparency consolidation) joined 2026-08-01, sourced from a
hard-coded-assumptions audit of the pipeline rather than the TSM triage. Its
one sequencing-sensitive piece — `output/template_writer.py` — is folded into
E3b; the rest queues behind E4/G.

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
| E3 | Levered wiring — E3a seam ⚑ shipped; E3b surfaces ⚑ shipped; E3b XLSM de-literalization ⚑ shipped | E2, D | Medium-large | **High-risk** |
| E4 | Solver retargeted to LP net IRR | E3 | Small | **High-risk** |
| T | Transparency consolidation — Cat 1 ⚑ #40, Cat 2 ⚑ #46, Cat 3 ⚑ #47, Cat 4 ⚑ #49, Cat 5 ⚑ #50, Cat 6 ⚑ shipped | E4 | Large | **High-risk** (live literals) |

Sequence: **A → B → D → E1 → E2 → E3 → E4 → G → T**. A goes first because it is the
cheapest and because its checks guard B's arithmetic while B changes it. T
queues last: it touches live literals across analysis/, model/ and output/ and
must not collide with the capital-structure build-out it remediates.

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

~~Deferred: TTM annualization sanity (needs a reporting-period field the
parser does not extract yet).~~ Shipped: `ttm_months` is now a CIMData
field (best-effort parse — "T-9 annualized", "9 months ending" — None
when unstated, never assumed 12), an assumptions-page input bounded
1–12, and the `ttm_annualization` advisory check, which fires when the
stated basis is under twelve months. **advisory**

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

**⚑ SHIPPED 2026-07-31.** Plan and rationale:
[docs/superpowers/plans/2026-07-31-item-b-transaction-costs.md](superpowers/plans/2026-07-31-item-b-transaction-costs.md).
Three deviations from the contract below, all argued in that plan:
(1) the cell this file names at `template_writer.py:396` is the GP
disposition **fee** (`F254`, correctly 0); the real cost of sale is `K182`,
hardcoded at 3.5%, and that is what got wired — along with `D182`, the sale
month, which the contract missed; (2) `model/value_add_model.py` was added
to scope, since leaving it out would have kept `va_max_offer` overstated
beside a static max offer that no longer is; (3) the sensitivity grid also
stopped ignoring per-deal scenario overrides — its centre cell disagreed
with the headline base IRR whenever an analyst edited a scenario.

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
computed on the LP-attributable residual only — which the LPA's "promote on
all capital" turns out to MEAN, once the fund's model workbook is consulted
on which arithmetic that phrase names (question 5 below). The 1% AM fee is a
cash-flow line deducted before the waterfall, charged on LP equity only
(question 4b, 2026-08-14). Design-doc oracles 1–3.

**E3 — wiring, in two parts.** **E3a ⚑ SHIPPED 2026-08-01** (#32):
`model/levered.py`, the seam where the debt layer and the waterfall meet the
unlevered projection — assembly only; sizing stays in `model.debt`,
distribution in `model.waterfall`, the NOI series and exit proceeds in
`project_cash_flows`. Financing costs stay out of the unlevered basis (the
Sources & Uses tie became Uses == total_basis + financing_costs). **E3b —
the surfaces:** debt + waterfall inputs on the assumptions page; levered
results as a **second lens beside the unlevered screen, which stays primary**;
memo section; Excel sheet. Reads the Sources & Uses block from D for equity.
Leverage is **opt-in per deal**: with no debt terms entered there is no
levered lens and every unlevered surface stays byte-identical.

**E3b also owns `output/template_writer.py` de-literalization** ⚑ **SHIPPED
2026-08-01 (#35)** — plan:
[item E3b — template writer](superpowers/plans/2026-08-01-item-e3b-template-writer.md).
Two deviations from the rules below, both argued in that plan and in the PR:
rule 4's "LTC stays 0" described a leverage-opt-out state that E3a had already
removed, so H64 carries the run's sized loan; and the K181/entry-cap items in
the list below were already fixed by #31 and #23 before this item started.
(Folded in
2026-08-01 from the transparency audit — the one piece of item T with a hard
sequencing dependency on item E). The XLSM writer is a parallel assumptions
system, and E3a made its literals live contradictions: the app now computes
levered returns from `DEBT_TERMS` (6.25% / 25-yr / 0-IO / 10-yr) and
`WATERFALL_TERMS` (20% promote, `GP_COINVEST_PCT` 0.10, plus the pref rate —
which since 2026-08-12 resolves from `PREF_RATE_LEVERED`/`PREF_RATE_UNLEVERED`
rather than living in that dict) while every
XLSM artifact still asserts 6.5% / 360-mo amort / 12-mo IO / 60-mo term
(lines 201–209), an env-var waterfall (6% GP equity, 1% AM fee, 20% promote —
lines 450–455), an 8%-or-6% pref formula (H258), terminal cap = entry +
50bps (K181) against the resolved scenario `exit_cap`, a 0%-then-3% growth
ladder on all six rows (213–227), a 0.90 occupancy fallback (234, 263, 315),
a 0.88 stabilization test, a 24-month stabilize, a 10% stabilized vacancy
behind a dead if/else, 1% credit loss, 1.0%/1.25% bank fees, $0.15/SF
reserve, a 6.5% entry-cap fallback (421), a 6% mgmt-fee fallback, and CapEx
timing months 1–6. Two deliverables asserting different terms on the same
deal is the exact failure mode the audit flagged, and as of E3a it ships in
production. This is therefore the blocking piece of E3b, not a follow-up.

**One item on that list was not a contradiction — 2026-08-12.** The
"8%-or-6% pref formula (H258)" was the template being RIGHT: the LPA charges
8% on levered deals and 6% on unlevered ones, and the app was the side
carrying a single hard-coded rate. E3b overrode the formula with that single
rate under rule 2 ("contradictions resolve to config"), which was correct
procedure applied to a wrong premise — the rule assumes config is the more
considered artifact, and here the fifteen-year-old workbook was. The app now
carries both rates and the writer's comment records the reversal. Nothing else
on the list is disturbed; this is one entry, not a verdict on the item.

Rules:

1. **The template never decides a value.** Every number written into the
   XLSM reads from the resolved assumption set (config + ConfigOverride +
   deal overrides) or from the run's computed results. Keys that do not
   exist yet (credit loss, bank/merchant fees, the CapEx timing window) are
   added to config with defaults equal to today's literals —
   behavior-preserving; changing an underwriting default is item T's
   decision, not E3b's.
2. **Contradictions resolve to config.** Where a literal contradicts an
   existing resolved value (debt terms, GP share, promote, pref, exit cap,
   the mgmt-fee and cap-reserve bands) the resolved value wins and the
   literal dies — including the K181 and H258 template formulas and the
   `GP_EQUITY_SHARE` / `GP_AM_FEE_RATE` / `GP_PROMOTE_PCT` env vars, which
   are deleted, not re-defaulted.
3. **The template gains no new opinions.** Where the target definition is
   itself disputed (stabilized occupancy 0.85 vs 0.88; the mgmt-fee
   adjustment target) E3b reads the config key that exists and leaves the
   reconciliation to item T.
4. **The growth ladder becomes scenario-driven** — rent rows grow at the
   resolved revenue CAGR, expense rows at `exp_growth`. Deliberate behavior
   change; say it in the PR the way item B did. With leverage opted out,
   LTC stays 0, but the rate/amort/IO/term cells still carry the resolved
   `DEBT_TERMS` values, so a user who flips LTC in Excel gets the terms the
   app would have used.

Acceptance (in addition to item E's):

- `template_writer.py` contains no numeric literals or env-var reads in its
  write paths — enforced by a test (grep or AST), not by inspection.
- One fixture deal run twice — unlevered, then with debt: the XLSM's debt
  block, waterfall block, pref and exit-cap cells equal the resolved
  `DEBT_TERMS` / `WATERFALL_TERMS` / scenario values the app used for that
  run. A full Python↔XLSM formula-parity harness (evaluate the workbook
  with the `formulas` library) is a stretch goal; do not block E3b on it.
- The unlevered regression holds: an unlevered deal's XLSM differs from
  today's only where this item deliberately changed a value, each delta
  enumerated in the PR.

**E4 — solver retarget.** Max price for a 15% LP net IRR instead of a 10%
unlevered IRR (existing ROADMAP item). The solved price now moves debt sizing,
which moves equity, which moves the waterfall — confirm the target function is
still monotone before trusting the bisection, and add a convergence test that
re-runs the solved price forward through the full levered stack.

**Assumption stamp instead of a block.** The design doc lists 7 LPA
confirmations. One tier plus the operator's no-clawback term answers two of
them. Six change the number; after the 2026-08-12 reading NONE is still open:

| # | Question | Resolved value | Effect if wrong | Status |
|---|----------|----------------|-----------------|--------|
| 1 | Pref simple or compounded, and at what frequency | annually compounded | ~19% swing in GP promote | **CONFIRMED 2026-08-09** |
| 2 | Accrual base: contributed/unreturned vs committed | **committed** | none here — see below | **CONFIRMED 2026-08-12** |
| 3 | ROC-before-pref or pref-before-ROC | ROC first (only matters if simple) | $81,600 vs $72,000 GP on the doc's fixture | **MOOT** (see below) |
| 4 | AM fee above the waterfall or netted from LP distributions | above (deal expense) | shifts LP net IRR directly | **CONFIRMED 2026-08-12** |
| 4b | AM fee charged on invested equity (GP+LP) or LP equity | **LP equity** | overstates the fee by the co-invest share — 11.1% at c=0.10 | **CONFIRMED 2026-08-14** |
| 5 | Promote on 100% of residual or LP-attributable share only | **off the top, then pro rata** (`J250`) | LP overpays the promote by `x·c·R` | **CONFIRMED 2026-08-12** |
| 6 | GP catch-up tier above the pref | none | GP recovers pref leakage; promote rises | **CONFIRMED 2026-08-12** |

**Question 2 changed the WORD, not the number.** The LPA says committed
capital. Under the operator's reading of the surrounding clauses — what is
committed is funded at close, a later call accrues from its own date, and
the base falls as capital is returned — that describes the accrual the
model already ran, so 'committed' and 'contributed' are one arithmetic
here. `test_the_two_accrual_bases_agree_to_the_cent` proves it rather than
asserting it, and `model/waterfall.py` records the precondition that would
break the equivalence (an uncalled commitment becoming expressible). The
stored value changed regardless: a stamp reading "contributed" beside
terms saying "committed" discloses a base the document does not name.

**Question 5 is the one that nearly moved money.** The LPA charges the
promote on ALL capital, which reads two ways that differ by real dollars:
with `c` the co-invest, `x` the promote and `R` the residual, either
`x·R` off the top with the remainder split pro rata (GP share
`x + (1−x)c` = 28%), or the GP's pro-rata slice first and `x·R` charged
on the whole residual on top (GP share `c + x` = 30%). The `x·c·R`
between them comes out of the LP.
**The fund's own model workbook settles it** —
`Self-Storage-Acquisition-Model-v1.3.xlsm`, `Underwriting!J250 =
I250+(1-I250)*$J$244` — as the first, which is what the build already
computed. So NO number moved. The first implementation of this row
resolved the sentence in code, picked the other reading, and moved every
levered figure in the repo; the operator's correction was to go to the
workbook. That is the second time in three days the XLSM has been the
more considered artifact (the 8%/6% pref rate at `H249` was the first),
which is a procedure note, not a coincidence: **consult the workbook
BEFORE resolving an ambiguous LPA sentence in code.**
Both bases ship — `promote_basis` is this list's only convention with two
live values — because only one existed before and nothing stated the
other, which is exactly what let the ambiguity hide.

**Question 6 was not on this list**, because it was filed as a scope
decision (one tier) rather than an LPA question. It is both, and it now
carries a stamp row — an LP reading "20% promote on the residual" cannot
tell from that line whether a catch-up sits above it.

**The pref RATE is two rates**: 8% levered, 6% unlevered (2026-08-12),
per-deal overridable, keyed on the deal's intent to lever rather than on
the sized loan. See CLAUDE.md decision 7 — including that this rule
already existed in the v1.2 XLSM and the writer overrode it as a defect.

**Question 1 is confirmed, and confirming it closed question 3 for free.**
The operator read the pref clause on 2026-08-09: annually compounded, which
is what the build already assumed, so no number moved. Question 3 was never
answered and does not need to be — ROC-before-pref only moves a dollar when
the pref is SIMPLE, so a compounding pref makes the ordering arithmetically
inert.

That distinction is carried in the code rather than only here.
`config.LPA_CONFIRMED` records which questions the document has actually
been read on (with the date, because who said so and when is the whole
content of a confirmation), and `model.waterfall.assumption_stamp` stamps
each row `confirmed` / `moot` / `open`. Three states, not two: "the LPA says
this" and "this cannot move the number given something else the LPA says"
are different claims, and collapsing them would let a moot question borrow a
confirmation it never received. A convention absent from `LPA_CONFIRMED`
defaults to `open` — fail-open, since a convention that silently inherited
someone else's confirmation would print as settled while still being a guess.

Downstream, the LP-facing caveat is now conditional: "proposed terms,
subject to the final partnership agreement" applies only to the rows that
still need it, with a count when the stamp is mixed. Overstating and
understating are both costly on the one document that leaves the firm.

**As of 2026-08-12 none remains open.** Question 5 was the last, and none of
the six moved a number — though 5 came within one reading of moving all of
them. Both promote bases are now implemented and tested (`promote_basis` is
the one convention on this list with two live values), but it gets no form
field regardless, because it is a term of the DOCUMENT, identical on every
deal, and a per-deal dropdown would invite a deal underwritten on a basis the
LPA does not permit. The LP net IRR is decision-grade on its conventions.

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

**Status 2026-08-02: SHIPPED, built to the operator's spec review rather
than to the scope below.** The audience was specified after this section was
written — a highly sophisticated family office — and the framing that came
with it (clear target return, the PLAN to achieve it, risks WITH mitigants)
restructured the document. The scope below is kept for the record; where the
two disagree, the built document wins. What changed:

- **Added: a "Plan to Achieve the Return" section.** The scope had no such
  block — page 2 went straight from scenarios to risks — so nothing stated
  HOW the return is produced, which is the centre of the framing.
- **Added: fee and promote transparency, and the leverage effect.** An LP net
  IRR printed without the AM fee, promote and co-invest that produced it
  invites the first question this audience asks. The unlevered and LP net
  columns carry equal weight because leverage is frequently DILUTIVE at
  config defaults, and burying that costs the document its credibility.
- **Added: mitigants.** The scope said "top 3 risks only, by severity" and
  never asked for mitigation, though all 18 risk sites in `analysis/risks.py`
  already emit one. Mitigants truncate but never drop.
- **Cut: the photo slot.** No image pipeline exists anywhere in the repo, and
  an empty rectangle reads as an unfinished template.
- **Resolved: the three-bullet thesis** now derives from a fixed priority
  ladder, each bullet gated on a value the pipeline produced, with a
  `thesis=` operator override. No new DB field.
- **Corrected: "MSA classification"** — `msa_info` carries a name and a bool.
  There is no MSA rank number in this codebase and none is ever printed.
- **Corrected: "SF per capita"** is conditional, not required; it is `TBD`
  whenever competitive supply is unentered, which is the common case.
- **Replaced: "assert the page count, do not eyeball it."** python-docx does
  not paginate, and `soffice` is not a dependency this repo carries. The
  guarantee is now a CONTENT BUDGET (`output/page_budget.py`): pinned
  geometry, EXACTLY line spacing and row heights so Word cannot reflow, and
  a Calibri width model that raises `InvestorSummaryOverflow` rather than
  shrinking anything. An opt-in `soffice` test re-validates the calibration.
  Read that module's docstring for the conditions under which it is wrong.
  **CLOSED 2026-08-09.** "Opt-in" meant "never ran": the test skips when
  LibreOffice is absent, installing LibreOffice needs root, and the dev box
  has no passwordless sudo. A CI runner does, so the calibration now runs in
  a dedicated `page-budget` job (`libreoffice-writer` + `poppler-utils` +
  Carlito, the metric-compatible Calibri clone) with `CIM_REQUIRE_SOFFICE=1`
  turning the skip into a FAILURE — without that, an apt-get that installed
  nothing would leave the job green having validated nothing.
  **The never-run test was also broken**, which is the point of the
  exercise: `raw.count(b"/Type /Page")` also matches `/Type /Pages`, the
  page-tree root, so it returned N+1 and could only ever have passed on a
  one-page document. It is now `pdf_page_count` with a negative lookahead,
  `pdfinfo` is preferred over it wherever poppler exists, and four
  parametrized cases unit-test the counter directly — those run everywhere,
  so the half most likely to be wrong is covered even where the renderer
  cannot be.

The download button IS wired (migration `0006`, `DOWNLOAD_KINDS` entry,
conditional button), so the deferral recorded here on 2026-08-02 is closed.

~~Still open: the CLI's copy carries no LP net IRR~~ — closed by PR #55:
`AnalysisContext` now carries `debt` / `levered` / `levered_max_offer`
(`context.py:58-69`) and `run.py` computes and prints them
(`run.py:194-200,253-271`), so the CLI's memo and investor summary render the
same levered payload the web path does.

**Why.** [output/memo_writer.py](../output/memo_writer.py) produces a
10-section internal IC memo. There is no condensation for anyone outside the
firm, and TSM sells its 2-page Investor Summary as a headline feature.
Near-zero incremental logic — it is a second rendering of numbers we already
compute.

**Original scope, superseded above.** `generate_investor_summary(...)`
alongside `generate_memo` in the same module — no new file. Two pages, hard
limit, which means a fixed section list and explicit truncation rules rather
than a shrunken IC memo:

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

**Status 2026-08-09 — the engineering side is CLOSED; the legal sign-off is
the operator's and remains OPEN.** Two preconditions were already met before
this change: the legend ships, and the assumption stamp now distinguishes what
the LPA actually says from what the build assumed (see E4 below). **GC sign-off
has not been sought** — it is an operator action, not a code change, and no
amount of further building discharges it.

What changed is that the gate stopped being prose. It lived in this paragraph
and in a comment above `_SUMMARY_LEGEND` — the two places the analyst clicking
"Investor Summary (.docx)" will never look. It is now state:

- `config.INVESTOR_SUMMARY_GC_CLEARED` (True since PR #61 — under an
  **ASSUMED** approval on the operator's direction, 2026-08-09, not a real
  GC review; the sign-off table in `docs/gc-review-investor-summary.md`
  names the reviewer as "none — assumed" and a real review REPLACES that
  row). Deliberately NOT
  settings-page editable — a legal clearance is not a per-deal underwriting
  assumption, and an analyst must not be able to clear it from the screen
  that edits cap rates. A test asserts it never enters the override
  registry, which is derived live from config and could otherwise sweep it
  in.
- While False, `_GC_PENDING_NOTICE` renders on the document's own first
  line, charged to the page budget like every other block. **On the page,
  not only on screen**: the failure this guards is a file already detached
  from the app — attached to an email, sitting in a data room — and a
  caveat beside the download button is invisible the moment the .docx
  moves.
- The download button turns amber and carries the caveat, pointing at the
  review packet.
- `docs/gc-review-investor-summary.md` is that packet: what the document is,
  why it needs counsel, the exact wording proposed for clearance, the
  specific questions, a sign-off table to fill in, and the re-review
  triggers that put it back in front of counsel.

Flipping the flag to True removes the notice and the caveat and leaves
`_SUMMARY_LEGEND`, which is permanent and unconditional in both states.
**Nothing here is legal advice or a substitute for the sign-off** — it makes
the gate enforceable and the review cheap, and the review itself is still
counsel's to do.

One thing to hand GC along with the document, carried over from the LPA-stamp
work: the caveat sentence beneath the LP net figures is now conditional, not
fixed. It reads "proposed terms, subject to the final partnership agreement"
only for conventions still unconfirmed, and says so by count when the stamp is
mixed. That wording is exactly the kind of thing GC will want to set, so route
the three variants in `memo_writer._is_assumption_stamp`, not just the
document. It is question 7 in the review packet.

**The 2-page guarantee is no longer unvalidated.** This paragraph used to
record that `soffice` was absent here and the Calibri calibration had never
been re-checked. It now runs in CI's `page-budget` job — see the note under
"Replaced: assert the page count" above — and its first real execution
confirmed the document renders to exactly 2 pages.

**Acceptance.** Renders to exactly 2 pages with the longest realistic deal
(longest property name, all three scenarios populated, 3 risks at maximum
message length) — assert the page count, do not eyeball it. Every number on the
page reads from the same result dict the IC memo uses; no recomputation, no
second source of truth. Degrades cleanly when the levered layer is absent.

---

## T. Transparency consolidation — one assumptions register, no shadow defaults

**A pattern worth naming, since it has now happened twice.** In Categories 2
and 5 the shipped answer was to REGISTER a family of divergent values behind
an AST guard rather than collapse it to one number — the three age ladders
and the nine occupancy numbers. Both read as half-finished against the scope
text, and neither is: this item's own **Out of scope** clause excludes
"Re-underwriting any default", and deciding which of three age ladders is
correct is exactly that. A third such family should expect the same
treatment rather than a reconciliation.

**Why.** The 2026-08-01 audit of valuation/modeling literals found roughly
fifty hard-coded assumptions outside [config.py](../config.py). The most
corrosive kind is not the missing key but the duplicated one: a value config
owns that the code restates as a literal, so a user who overrides it in
settings changes some outputs and not others — the UI claims the override
works and the model proves otherwise. The second kind is the silent fallback
(`or 0.90`, `or 0.80` vs `or 0.85` on the same field, `or 100_000`, `or 1`,
market rent defaulting to in-place rent) that fabricates an input instead of
failing — each an undisclosed assumption. The `template_writer.py` slice of
the audit is folded into E3b; everything else lands here.

**Prerequisite — the characterization safety net, first task of this item.**
Every change below touches a live literal. Before any of them: fixture deals
(stabilized, value-add, thin-data) run end-to-end with gates, NOI series,
scenario IRRs, max offers, sensitivity grids and memo/excel outputs
snapshotted. Each subsequent change must either reproduce its snapshot
byte-for-byte (a pure literal→config move) or change it deliberately, with
the delta enumerated in the PR. This is item B's "costs at 0 reproduce every
oracle" discipline applied to the whole pipeline.

**Scope.**

1. **Category 1 — kill the duplicates.** ~~`analysis/risks.py` NOI step-up
   0.15 → `GATES["max_noi_step_up"]`; the population literals in
   `analysis/market.py` and `risks.py` → `GATES["population_3mi"]`; the four
   hard-coded 5%-of-EGR management-fee targets in `analysis/financials.py`
   and `value_add.py` → the benchmark band; memo/excel "10% IRR"
   recommendation threshold, labels, sensitivity colors and the VA max-offer
   caption → `SOLVER_TARGET_IRR` / `GATES["min_irr_5yr"]`;
   `model/value_add_model.py` imports `COERCED_SCENARIOS` instead of
   re-declaring it. Gate names and risk strings become f-strings over the
   config values, so labels cannot drift from the tests they describe.~~ —
   **DONE**, PR #40 (`88aaf74`). Every named duplicate now reads from
   config: `GATES["max_noi_step_up"]` at `analysis/risks.py:195,200` (test
   and label both); `GATES["population_3mi"]` across `analysis/market.py`
   and `risks.py`, with the narrative tiers moved to `POPULATION_TIERS` /
   `OCCUPANCY_TIERS`; `MGMT_FEE_TARGET_PCT` behind the one resolver
   `analysis/financials.py:26 resolve_mgmt_fee_target`, called from both
   `financials.py:333` and `value_add.py:182`; `cfg.GATES["min_irr_5yr"]` +
   `cfg.IRR_STRONG_THRESHOLD` at `output/memo_writer.py:1002-1009` and
   `output/excel_writer.py:498-518`; `COERCED_SCENARIOS` declared once at
   `analysis/valuation.py:32`.
   **Two literals in the touched files deliberately survive**, and both
   would look like misses to a later reader: the HHI `50_000` at
   `analysis/market.py:67,89` is a different quantity that happens to equal
   the population gate (pinned apart by
   `test_the_hhi_thresholds_are_not_the_population_gate`), and the
   rent-premium `0.15` at `analysis/risks.py:381` is a different quantity
   that happens to equal the NOI step-up. That coincidence of value is
   exactly what made the original duplicates hazardous, so the separation
   is asserted rather than left to be re-noticed.
2. **`analysis/value_add.py` consolidation** — ~~an entire assumptions layer
   with no config home: the occupancy-target policy, spread-recovery
   haircut, ECRI trigger/impact, ancillary thresholds, and the renovation
   cost schedule with its age triggers become `VALUE_ADD_ASSUMPTIONS` /
   `RENOVATION_COST` config sections (its `EXPENSE_BENCHMARKS` import sits
   unused today).~~ — **DONE**, PR #46 (`fa36cfb`).
   `config.py:442 VALUE_ADD_ASSUMPTIONS` holds the occupancy target, the
   spread-recovery share, the four ECRI keys and the three ancillary keys;
   `config.py:491 RENOVATION_COST` holds the five capex items with their
   age triggers, rendered in declaration order at
   `analysis/value_add.py:215-224`. The dead `EXPENSE_BENCHMARKS` import is
   gone, guarded at `tests/test_config_single_source.py:1179`.
   Settings-editability is split on purpose: `VALUE_ADD_ASSUMPTIONS` is a
   `_PATCHED_DICTS` entry (`webapp/services.py:203`), `RENOVATION_COST` is
   explicitly excluded as presentation-only (`webapp/services.py:184-189`).
   **The age half shipped differently from this clause, and the difference
   is the point.** ~~The three divergent building-age taxonomies
   (value_add 20/15/10, physical 5/15/30, risks 25) reconcile to one
   schedule.~~ They were **REGISTERED, not reconciled** —
   `config.py:567 ASSET_AGE_LADDERS` names all three
   (`registry.AGE_BANDS`, `RENOVATION_COST`, `RISK_TRIGGERS`) with an AST
   guard (`test_no_age_threshold_survives_as_a_bare_literal`) forbidding a
   fourth from appearing as a bare literal, and a completeness guard beside
   it. This is the same call Category 5 later made on the nine occupancy
   numbers, for the same reason: each ladder answers a different
   underwriting question (when is a building due for renovation vs. how
   does its age band a comp vs. when does age become a risk trigger), and
   collapsing them to one schedule is re-underwriting — which this item's
   own **Out of scope** explicitly excludes. A reader holding to the
   original wording should call this DONE-with-deviation, not DONE.
3. **Model-layer hard-codes.** ~~Solver brackets → one `SOLVER_BOUNDS` config
   pair used by both solvers (today static and value-add disagree: NOI/0.03
   vs NOI/0.02); sensitivity-grid axes → `SENSITIVITY_GRID`; `registry.py`'s
   `DEFAULT_EXPENSE_RATIO` / `EXPENSE_RATIO_CLAMP` move to config and
   reconcile with `EXPENSE_BENCHMARKS["opex_revenue_ratio"]` — one statement
   of the default, the clamp bounds, and their relation to the benchmark
   band.~~ — **DONE**, PR #47 (`d6e851e`); the paragraph below this one
   carries the detail. Two anchors it does not: the single bracket helper
   is `model/solver.py:114 solver_price_bracket`, pinned by
   `test_all_three_solvers_bisect_the_same_bracket`, and
   `registry.py:95 EXPENSE_RATIO_LIMITS` is what remains in `registry.py`
   after the move — the sanctioned "registry's non-valuation constants"
   carve-out that this item's own acceptance criterion names, so it is not
   a literal the sweep should later flag.
   ~~the frozen import-time `SOLVER_TARGET_IRR` binding in
   `model/solver.py` resolves at call time, and the `engine.py` truthiness
   guard becomes `is not None` so a 0.0 target is passable~~ — **DONE**,
   pulled forward out of order because the operator asked whether the
   target-IRR setting was editable. Both unlevered solvers resolve through
   `model.solver.resolve_target_irr`; three truthiness guards (`engine.py`
   plus the resolution and the stamp-popping guard in `webapp/services.py`)
   now key on `is None`.
   The rest of this category is **DONE** too, in PR #47 — this sentence
   used to read "the rest of this category is untouched" and was left
   stale when that PR shipped. `SOLVER_BOUNDS` is now one bracket for all
   three solvers (static and levered stopped at a 3% implied entry cap,
   value-add at 2%, and nothing recorded that they differed; resolved at
   the wider 2%); `SENSITIVITY_GRID` replaced the eighteen literal
   offsets with span + step, byte-for-byte; and `registry`'s
   `DEFAULT_EXPENSE_RATIO` / `EXPENSE_RATIO_CLAMP` became
   `config.EXPENSE_RATIO`, with the clamp DERIVED from the
   `opex_revenue_ratio` band widened by a named tolerance rather than
   declared as a second pair.
4. **Loud fallbacks.** One `assumption_fill_log`: any fallback that fires
   (~~occupancy,~~ market rent, mgmt fee, entry cap) records (field, value
   used, source key) and surfaces in the results UI and the memo appendix.
   **Occupancy came off this list in item T Category 5: the
   assumed-occupancy constants were deleted outright, so a CIM missing
   physical occupancy is refused (`analysis.fills.require_underwritable`),
   not logged as a fallback.**
   `nrsf or 1` and `ttm_noi or 100_000` are deleted — a deal without NRSF or
   NOI fails; it is not underwritten as a 1-SF / $100k fiction. The
   zero-rent-gap market-rent fallback gains an explicit flag: "rent ramp
   excluded — no market-rent data."
5. **Reconciliations, decided once in config:**
   ~~"stabilized" occupancy — 0.85 (gate) vs 0.88 (VA target/template) vs
   0.90/0.93 (value_add targets) — and the mgmt-fee adjustment target
   (benchmark floor vs 5%). E3b reads whatever keys exist; this item owns
   the definitions.~~
   The mgmt-fee half is **DONE** — closed earlier, in PR #43:
   `MGMT_FEE_TARGET_PCT` (0.06) with `resolve_mgmt_fee_target` as the one
   resolver, so `analysis/financials.py` (adjusts an understated fee UP)
   and `analysis/value_add.py` (sizes a renegotiation saving DOWN) can
   never resolve the target differently.
   The occupancy half is **REGISTERED, not reconciled** — not what this
   clause asked for. `config.OCCUPANCY_KEYS` names all nine occupancy
   numbers in the codebase, with an AST guard
   (`test_no_occupancy_threshold_survives_as_a_bare_literal`) forbidding a
   tenth from appearing as a bare literal, and `config.OCCUPANCY_TIERS`
   replaced the bare 0.95/0.90/0.85 narrative literals in
   `analysis/market.py` and `analysis/risks.py`. But the nine were never
   collapsed to one value — the same call Category 2 made on the three
   age ladders (`ASSET_AGE_LADDERS`, in config.py): each occupancy number
   answers a different underwriting question (is demand proven at all vs.
   has a post-2020 vintage ever stabilized vs. how the number reads in
   the narrative vs. what a scenario assumes vs. where the value-add
   engine ramps to...), and forcing them to one number is
   re-underwriting, which this item's own scope excludes. The measurement
   that decided it, on the value_add fixture (EGR-forced probe — occupancy,
   the rent override, unit mix and GPR all removed, so both constants fire
   on the same deal): when the two constants AGREE, the choice of number
   moves base IRR by 0.29bps (0.4812%-0.4841% across 0.80/0.85/0.90); two
   of them disagreeing — as `VA_DEFAULT_OCCUPANCY` (0.80) and
   `VA_EGR_ASSUMED_OCCUPANCY` (0.85) did, both firing in the SAME run —
   moves it 14-27bps (0.3417% for the mismatch that actually shipped,
   0.2160% for the widest one). Inside that probe the disagreement is
   48-92x bigger than the agreement-spread, but that ratio belongs to the
   probe, not to occupancy assumptions generally: isolating a single
   constant (only the stated occupancy blanked; unit mix and the EGR
   route both stay intact) swings base IRR 0.0706%-0.3254%, roughly
   25bps — about 88x the 0.29bps figure, because the probe's
   near-zero agreement-spread comes from the ramp start and the implied
   in-place rent moving in OFFSETTING directions, an effect that
   disappears once only one constant is live. So the fix was never "pick
   0.80 or 0.85 or 0.90" — even the smaller, agreement-case spread is not
   a safe number to lean on. What this clause did not
   anticipate: `VA_DEFAULT_OCCUPANCY` and `VA_EGR_ASSUMED_OCCUPANCY` were
   deleted outright rather than folded into the register — there is no
   Python-side assumed occupancy left at all (see
   `analysis.fills.require_underwritable`, which now refuses a deal with
   no stated physical occupancy instead of defaulting one in).
   `config.XLSM_TEMPLATE_INPUTS["assumed_physical_occupancy"]` (0.90) is
   the one surviving assumed occupancy anywhere in the codebase, and
   belongs to item E3b, not this clause.
6. **Memo assumptions appendix.** Every number that moved an output, its
   value, and its provenance (config default / ConfigOverride / deal
   override / CIM datum / fallback + flag) rendered as a memo section —
   E2's assumption stamp extended to the whole model, and the transparency
   requirement made auditable in one place.
   **DONE.** `analysis/assumptions.py` is the register; memo **Appendix
   B** is the section, with the workbook's Inputs sheet and a collapsed
   Summary-tab panel beside it. All five provenances in the clause above
   are implemented as a closed vocabulary with the model's own
   precedence, one row per assumption carrying the winner and `was`
   carrying what it displaced.
   Four things the clause did not anticipate:
   - **It is Appendix B, not a rewrite of Appendix A.** B contains the
     fill log's rows (as provenance `fallback`), so "auditable in one
     place" holds — an auditor who reads only B has seen everything. A
     stays because it answers a sharper question, and nine invented
     numbers inside a hundred and forty do not read as an answer to it.
   - **It is two tables, not one.** B.1 lists only what a human or a
     fallback produced (typically 10-20 rows); B.2 is the full ~130-row
     register. Neither omits anything: a "defaults suppressed for
     brevity" appendix asks the reader to trust that absence means
     default, which is the act of faith this item exists to end.
   - **`MARKET_CAP_RATES` reports its resolved anchor, not its twelve
     cells.** Eleven of them moved nothing, and "every number that moved
     an output" excludes them by its own wording. It is the sole entry in
     `NOT_IN_REGISTER`, and the exemption is only honest because the cell
     that DID move is reported with the class and age band it was looked
     up by.
   - **A SIXTH provenance, and the clause could not have named it.** The
     five above all describe a value someone chose or a document stated;
     `external` (2026-08-18, PR #101) describes one this system MEASURED —
     the Census demographics `extract.enrichment` fetches, which land on
     `cim_data` before the pristine snapshot is saved and were therefore
     indistinguishable from an extracted figure. See CLAUDE.md decision 11
     for why fixing it took three changes rather than one.
     The follow-up (2026-08-19) is the part worth reading twice: a closed
     vocabulary is only half a disclosure, because the SURFACES had
     hard-coded the old partition. `chosen == 0` was rendered as "all
     model defaults" in three places, on runs whose gate-critical
     population had just been measured — so widening the vocabulary
     without re-reading every sentence that counts it ships the same lie
     one layer up. The same commit closed the `cim` catch-all's three
     remaining exits (an analyst's entry into a field extraction left
     empty, and resolver tiers 3 and 4), each of which had the register
     attributing to the seller's document a number it never contained.
   Membership is CI-enforced rather than curated — see the acceptance
   note below.

**Out of scope.** `output/template_writer.py` (item E3b). The Python↔XLSM
formula-parity harness (E3b stretch). Re-underwriting any default — this
item moves values into config and labels them; what the values *should be*
is a separate, per-value decision. New modeling capability of any kind.

**Files.** [config.py](../config.py), [registry.py](../registry.py),
`analysis/{risks,market,value_add,financials,filters,valuation}.py`,
`model/{solver,returns_model,value_add_model}.py`, [engine.py](../engine.py),
[context.py](../context.py), `output/{memo_writer,excel_writer}.py`,
[webapp/services.py](../webapp/services.py) (the cc_pct classification
threshold), tests.

**Acceptance.**
- Characterization snapshots exist and are green before the first literal
  moves; every later delta is enumerated and argued in its PR.
- A grep/AST sweep finds no numeric modeling literals outside `config.py`
  and `registry.py`'s non-valuation constants — enforced by a CI test, not
  by inspection. **DONE**, PR #59 — `tests/test_literal_sweep.py`.
  ~~STILL OPEN — the one acceptance criterion not met.~~ That text was
  written by PR #54 and closed by PR #59 two hours later; it is corrected
  here rather than deleted, because "the doc claimed something the code
  disproved" is the exact defect this whole item exists to remove, and it
  is worth one line to record that the item's own paperwork was the last
  place it happened.
  The sweep exempts by KIND (unit conversions, `round()` args, subscript
  indices, identifier-ish dict keys, and the RHS of any module-level
  UPPER_SNAKE assignment) and falls back to an allowlist keyed by
  `(module, value)` where every entry carries a reason. The five
  per-family guards in `tests/test_config_single_source.py` deliberately
  REMAIN: they name their family and fail with a message about it, where
  the sweep can only say "line 180". Backstop, not replacement.
  **One gap survives and is stated rather than implied:** the main sweep
  covers `analysis/` and `model/`, not `output/`, whose literals are
  overwhelmingly layout. PR #64 narrowed the `output/` gap by SHAPE:
  comparison-shaped literals there (a threshold deciding what a document
  says) are now swept by
  `test_no_threshold_comparison_hides_in_the_output_layer`, ratcheted
  from zero. A NEW non-comparison modeling literal in `memo_writer` or
  `excel_writer` — one hiding in a constructor or arithmetic rather than
  a comparison — remains the stated residual gap.
- Override round-trip: for each formerly-duplicated key, a ConfigOverride
  delta changes every output the audit found divergent (step-up flag,
  population gate and labels, memo recommendation threshold, sensitivity
  coloring).
- Fallback drill: a fixture missing ~~occupancy /~~ market rent / NRSF
  produces the fill log in the UI and the memo, and hard-fails on
  NRSF/NOI **and occupancy** (item T Category 5 moved occupancy from the
  logged-fallback list to the hard-fail list — a fixture missing it now
  refuses the deal instead of producing a fill-log entry).
- The memo appendix lists every assumption its own run used, with
  provenance — an IC reviewer can audit every number in one place.
  **MET** (Category 6). Appendix B, ~130 rows, split into what a human
  chose and the full register. What makes the completeness claim hold is
  not the appendix but
  `test_every_settings_editable_key_is_in_the_register_or_declared_out`:
  it walks `override_key_registry()`, which is derived LIVE from
  config.py, so a new editable constant appears there the moment it is
  declared and FAILS until somebody registers it or writes down why it is
  exempt. A second guard covers the levered constants
  (`DEBT_TERMS`, `WATERFALL_TERMS`, `AM_FEE_PCT`, ...), which are per-deal
  only and so absent from that registry — the first guard's silence about
  a key is not permission to omit a number that moves every levered
  figure in the memo. A third holds `CIM_FIELDS` against the assumptions
  page's own input list, so a new box cannot appear on the form and
  silently miss the appendix.
  Both entry points disclose the same register: a CLI run has no
  `ConfigOverride` table and no assumptions page, so its provenances are
  `config` / `cim` / `fallback` only, and that is pinned by a test rather
  than left to drift — Category 4 shipped exactly that asymmetry once,
  with the fill log reaching none of the CLI's three writers.

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
