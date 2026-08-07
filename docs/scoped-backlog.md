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
| T | Transparency consolidation (audit remediation) | E4 | Large | **High-risk** (live literals) |

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
computed on the LP-attributable residual only. The 1% AM fee is a cash-flow line
deducted before the waterfall. Design-doc oracles 1–3.

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
`WATERFALL_TERMS` (8% pref, 20% promote, `GP_COINVEST_PCT` 0.10) while every
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

The download button IS wired (migration `0006`, `DOWNLOAD_KINDS` entry,
conditional button), so the deferral recorded here on 2026-08-02 is closed.

Still open: the CLI's copy carries no LP net IRR — `AnalysisContext` has
never held a `levered`/`debt` payload and `run.py` never computes one. That
is a pre-existing CLI gap the IC memo shares, not something this item
introduced.

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

**Acceptance.** Renders to exactly 2 pages with the longest realistic deal
(longest property name, all three scenarios populated, 3 risks at maximum
message length) — assert the page count, do not eyeball it. Every number on the
page reads from the same result dict the IC memo uses; no recomputation, no
second source of truth. Degrades cleanly when the levered layer is absent.

---

## T. Transparency consolidation — one assumptions register, no shadow defaults

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

1. **Category 1 — kill the duplicates.** `analysis/risks.py` NOI step-up
   0.15 → `GATES["max_noi_step_up"]`; the population literals in
   `analysis/market.py` and `risks.py` → `GATES["population_3mi"]`; the four
   hard-coded 5%-of-EGR management-fee targets in `analysis/financials.py`
   and `value_add.py` → the benchmark band; memo/excel "10% IRR"
   recommendation threshold, labels, sensitivity colors and the VA max-offer
   caption → `SOLVER_TARGET_IRR` / `GATES["min_irr_5yr"]`;
   `model/value_add_model.py` imports `COERCED_SCENARIOS` instead of
   re-declaring it. Gate names and risk strings become f-strings over the
   config values, so labels cannot drift from the tests they describe.
2. **`analysis/value_add.py` consolidation** — an entire assumptions layer
   with no config home: the occupancy-target policy, spread-recovery
   haircut, ECRI trigger/impact, ancillary thresholds, and the renovation
   cost schedule with its age triggers become `VALUE_ADD_ASSUMPTIONS` /
   `RENOVATION_COST` config sections (its `EXPENSE_BENCHMARKS` import sits
   unused today). The three divergent building-age taxonomies
   (value_add 20/15/10, physical 5/15/30, risks 25) reconcile to one
   schedule.
3. **Model-layer hard-codes.** Solver brackets → one `SOLVER_BOUNDS` config
   pair used by both solvers (today static and value-add disagree: NOI/0.03
   vs NOI/0.02); sensitivity-grid axes → `SENSITIVITY_GRID`; `registry.py`'s
   `DEFAULT_EXPENSE_RATIO` / `EXPENSE_RATIO_CLAMP` move to config and
   reconcile with `EXPENSE_BENCHMARKS["opex_revenue_ratio"]` — one statement
   of the default, the clamp bounds, and their relation to the benchmark
   band.
   ~~the frozen import-time `SOLVER_TARGET_IRR` binding in
   `model/solver.py` resolves at call time, and the `engine.py` truthiness
   guard becomes `is not None` so a 0.0 target is passable~~ — **DONE**,
   pulled forward out of order because the operator asked whether the
   target-IRR setting was editable. Both unlevered solvers resolve through
   `model.solver.resolve_target_irr`; three truthiness guards (`engine.py`
   plus the resolution and the stamp-popping guard in `webapp/services.py`)
   now key on `is None`. The rest of this category is untouched.
4. **Loud fallbacks.** One `assumption_fill_log`: any fallback that fires
   (occupancy, market rent, mgmt fee, entry cap) records (field, value used,
   source key) and surfaces in the results UI and the memo appendix.
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
   that decided it, on the value_add fixture: with exactly one occupancy
   assumption in play, the choice of number moves base IRR by ~3bps
   (0.4812%-0.4841% across 0.80/0.85/0.90); two of them disagreeing — as
   `VA_DEFAULT_OCCUPANCY` (0.80) and `VA_EGR_ASSUMED_OCCUPANCY` (0.85)
   did, both firing in the SAME run — moves it 14-27bps (0.3417% for the
   mismatch that actually shipped, 0.2160% for the widest one). The
   disagreement is 5-9x bigger than the number, which is why the fix was
   never "pick 0.80 or 0.85 or 0.90." What this clause did not
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
  by inspection.
- Override round-trip: for each formerly-duplicated key, a ConfigOverride
  delta changes every output the audit found divergent (step-up flag,
  population gate and labels, memo recommendation threshold, sensitivity
  coloring).
- Fallback drill: a fixture missing occupancy / market rent / NRSF produces
  the fill log in the UI and the memo, and hard-fails on NRSF/NOI.
- The memo appendix lists every assumption its own run used, with
  provenance — an IC reviewer can audit every number in one place.

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
