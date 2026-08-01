# Item E3a — the levered seam (`model/levered.py`)

Plan date: 2026-08-01. Scope contract:
[docs/scoped-backlog.md](../../scoped-backlog.md) item E3. Design input,
conventions and numeric oracles:
[docs/levered-waterfall-design.md](../../levered-waterfall-design.md).

Review tier: **high-risk** (money math). Consumes E1 (`model/debt.py`,
PR #28) and E2 (`model/waterfall.py`, PR #30), which until now nothing
outside `tests/` was allowed to import. This is the item that wires them.

## E3 is two PRs

E3 as scoped spans two risk classes, so it ships as two:

- **E3a — this plan.** The levered seam: the Sources & Uses tie,
  `model/levered.py`, engine wiring, config defaults, the three guard
  tests deleted, the exit-NOI convention recorded. The levered lens is
  computed and persisted on every deal at config defaults.
- **E3b — next.** The surfaces: debt and waterfall inputs on the
  assumptions page, the levered second lens on the results page, the memo
  section and the Excel sheet. Standard tier, plus both required UI
  passes.

Splitting on risk keeps a presentation diff from riding in on a review
that has to hold levered arithmetic in its head.

## Operator decisions taken 2026-08-01, before any code

**1. Financing costs stay OUT of the unlevered basis.** This reverses
E1's recorded handoff, deliberately and with the operator's call.

E1's plan and `build_debt_schedule`'s docstring both prescribe adding a
zero-defaulted `financing_costs` term to `project_cash_flows` so
`analysis.checks.sources_uses_ties` keeps tying Uses to `total_basis`.
That is a mechanical fix to a check, and it has an underwriting
consequence nobody costed: the origination fee would enter the
**unlevered** basis, so the primary 10% IRR screen would move the moment
a deal named a loan. An unlevered return charged a financing fee is not
an unlevered return.

So the tie changes instead of the projection:

```text
Uses  ==  DCF total_basis + financing_costs        (to the cent)
```

`analysis.valuation.project_cash_flows` is **not touched by this item**.
That also deletes the largest piece of E3's blast radius — E1's route
would have threaded a new term through the scenario engine,
`_run_single_scenario`, the sensitivity grid, both solvers and the
value-add model. None of that happens now, and E4 inherits an unlevered
engine that still behaves exactly as it did.

`test_financing_costs_break_the_basis_tie_until_e3_extends_it` pinned the
gap to EQUAL `financing_costs`, which is precisely the identity above —
so E1's measurement stands, only the side of the equation it is written
on changes. That test is deleted here and replaced by a test of the new
identity.

**2. The AM fee is 1% of invested equity, measured at the START of each
period.** E2 left this open on purpose: it does not charge the fee, and
the design doc names the rate but never the base. "1% of committed
equity", "1% of invested capital" and "1% of asset value" are all live
conventions and on the oracle-A fixture they differ by ~2.4x, straight
through to LP net IRR.

Start-of-period is load-bearing, not stylistic. A shortfall triggers a
capital call, the call raises invested equity, and an end-of-period base
would raise the fee, which deepens the shortfall — a loop with no fixed
point. Measuring the base before the period's own call breaks it, and it
matches the accrual convention `model/waterfall.py` already uses (accrue
on the balance at the START of the period; period 0 does not accrue).
Oracle C pins it: the year-2 fee of 29,683.42 is 1% of
2,825,000.00 + 143,342.06, the year-1 call included and the year-2 call
excluded.

**3. The levered lens is ON by default**, sized at `config.DEBT_TERMS`,
for every deal and all three scenarios.

**4. The loan is sized ONCE, off the base case**, and the same loan is
carried through bear, base and bull. Sizing per scenario would hand the
bear case a smaller loan and flatten its own downside — the model would
understate exactly the risk the bear case exists to show.

## Oracles derived before building

All three were computed from scratch in `Decimal`, importing nothing from
the repo, so the tests assert an independently derived answer rather than
a snapshot of the implementation. Each exercises a path the others do
not. Common frame: $10,000,000 price, 1% acquisition closing, 1%
disposition, 5-year hold, 10% GP co-invest, 8% annually-compounded pref,
20% promote, 1% AM fee on invested equity.

**The derivation cross-validates against E1.** Oracle A's annual debt
service (455,088.98) and payoff balance (5,616,658.65) reproduce
design-doc oracle 5 to the cent from an independent monthly roll-forward,
which is evidence the two agree about amortization before any wiring
depends on it.

### Oracle A — debt-yield bound, deal never clears the pref

$600,000 Y1 NOI, 3% growth, 6.25% exit cap; 6.50% / 30yr / no IO,
65% LTV / 1.25x DSCR / 10% debt yield / 1 point.

Caps 6,500,000 / 6,328,432.78 / 6,000,000 → **loan 6,000,000, debt yield
binding**. Financing costs 60,000; `total_basis` 10,100,000;
**Uses 10,160,000** and the tie holds. Equity 4,160,000 (LP 3,744,000 /
GP 416,000); AM fee 41,600/yr flat. Levered CF 103,311.02 / 121,311.02 /
139,851.02 / 158,947.22 / 5,258,793.39.

Tier 1 ends **225,455.71 unpaid**, so **promote is 0.00**,
`tier1_current` is False, LP net IRR **7.1479%**, LP MOIC **1.3900**.

This is the case worth having: **leverage is DILUTIVE here** — 7.1479%
LP net against a 7.3031% unlevered IRR, because the 7.585% loan constant
sits above the deal's yield on cost. A levered lens that always prints a
bigger number than the unlevered screen is a lens with a bug, and this
oracle is the regression that says so.

### Oracle B — value-add, clears the pref and pays a promote

$750,000 Y1 NOI, 4% growth, 5.75% exit cap; 6.25% / 30yr / **24 months
IO**, same covenants.

**Loan 6,500,000, LTV binding.** Financing costs 65,000; Uses 10,165,000;
equity 3,665,000. The IO roll is visible and is the design doc's named
"IO→amort transition payment not recomputed" error, caught: debt service
is 406,250.00 in years 1–2 (interest only) and **480,259.42** from year 3.

Tier 1 fully paid, **GP promote 965,508.78**, LP net IRR **22.9916%**,
LP MOIC **2.5656**, GP IRR 43.4113%, against a 15.2683% unlevered IRR.

### Oracle C — reserve draw, then four capital calls

$400,000 Y1 NOI, 12% growth, 6.00% exit cap; 7.50% / 25yr / no IO,
75% LTV with **no DSCR and no debt-yield covenant**, 1 point, 0.5% exit
fee, and a **$150,000 funded operating reserve**.

**Loan 7,500,000, LTV binding** — the covenant floors are what normally
prevent this, and removing them is how the fixture forces the shortfall
path. `total_basis` 10,250,000 (the reserve is in it, per item D);
Uses 10,325,000; equity 2,825,000.

Levered CF is negative in years 1–4: −293,342.06 / −246,775.48 /
−195,483.24 / −137,226.87. Year 1 draws the reserve to zero (150,000)
and calls the remaining **143,342.06**; years 2–4 call the full amount.
Contributions become
`[2,825,000.00, 143,342.06, 246,775.48, 195,483.24, 137,226.87, 0.00]`
and **every distribution in those years is 0.00, never a negative
distribution**. The AM fee climbs 28,250.00 → 29,683.42 → 32,151.18 →
34,106.01 → 35,478.28 as the calls land, one period behind each.

Exit fee 34,399.71 is deducted at sale, not carried as a use of funds.
Tier 1 ends 1,633,225.57 short, promote 0.00, LP net IRR **−0.9442%**,
LP MOIC 0.9583. `unrecovered_promote` is 0.00 — no promote was paid
before the last call — which is the capped behaviour E2's second review
pass installed.

## The four traps E2 and E1 left flagged

Each is handled explicitly, and each gets a test that fails without it.

1. **A negative levered CF is never a negative distribution.** Order per
   period: draw the funded operating reserve to zero first — that money
   was contributed at close and item D already put it in `total_basis`,
   so the period's distribution is simply 0 and nothing new is called —
   then the remainder becomes a `contributions[t]` capital call, which
   starts accruing pref at t+1. With `DEFAULT_OPERATING_RESERVE = 0.0`
   the default path is a straight capital call. Oracle C pins both legs.
2. **`resolve_waterfall_terms(capital_structure=...)` is never omitted.**
   The deal's resolved co-invest reaches the waterfall, so a deal edited
   to 25% cannot print a 25/75 stack beside an LP net IRR computed on
   10/90. Test: a 25% co-invest deal, asserting GP tier-1 dollars are
   exactly 25% of tier 1.
3. **Both series are spelled out; the scalar shorthand is never used.**
   `contributions = [total_equity, call₁, …]` and
   `distributions = [0.0, cf₁, …]`, both length `hold_years + 1`.
   Passing `cash_flows[1:]` under the shorthand measured 14.1563% against
   a correct 11.2437% on E2's fixture. Test: the assembled distribution
   series has a 0.0 at index 0 and length `hold_years + 1`.
4. **The AM-fee stamp row carries the rate and the base actually
   charged.** `model/levered.py` extends E2's stamp row in place —
   "above the waterfall (deal expense) — 1.00% of invested equity,
   measured at the start of each period" — so the stamp cannot read
   complete beside an LP *net* IRR while omitting the input that makes it
   net. Test: the assembled stamp's `am_fee_treatment` row contains both
   the rate and the base.

## The exit-NOI convention, recorded at both ends

`project_cash_flows` capitalizes the **terminal hold year's own** NOI
(`exit_noi = noi_series[-1]`). `tests/test_debt.py`'s oracle 5 reproduces
the design doc, which capitalizes **year 6** — about 3% higher. Both are
correct for what they are, and E3a is the item where they meet, so the
mismatch gets stated rather than discovered:

- `CLAUDE.md`'s design-decisions block gains the convention explicitly.
- `tests/test_debt.py`'s oracle 5 keeps its numbers — it is a faithful
  reproduction of the design doc and E1 shipped against it — and gains a
  comment naming it as the **forward**-NOI convention, not the
  pipeline's.
- `tests/test_levered.py` pins the **trailing** convention that the
  wiring actually uses, so the repo has a test on each side.

The underwriting judgment (a buyer at the end of year 5 prices on year
6's NOI, which is the institutional norm) stays deferred, not settled.
Do not silently switch it.

## Design

**`model/levered.py`** — one new module, the third sibling of `debt.py`
and `waterfall.py`, and the seam where they and the unlevered projection
meet.

```python
build_levered_returns(projection, *, sources_uses, debt, waterfall_terms,
                      am_fee_pct=None, reserve=0.0) -> dict
```

It consumes a `project_cash_flows` result, a `build_debt_schedule`
result and a `build_sources_uses` block; it computes the AM fee, the
levered cash flow, the reserve draw / capital call split, and calls
`run_waterfall`. It reads no config beyond the AM-fee defaults and sizes
no loan — sizing belongs to `debt.py`, distribution belongs to
`waterfall.py`, and this module only assembles.

Per year `t` in `1..N`: `NOI_t − debt_service_t − am_fee_t`, with the
final year adding `net_exit_proceeds − payoff_balance − exit_fee`.
`net_exit_proceeds` comes from the projection, so the disposition cost is
the one the unlevered model already charged rather than a second
computation of the same percentage — the same rule that put
`build_sources_uses` inside `build_returns_model`.

Returns a dict (every other builder in the model layer does, and the memo
writer, Excel writer, templates and `webapp.services.json_safe` all
consume dicts): the per-year rows, `am_fee_total`, `capital_calls`,
`reserve_drawn`, the debt block, the waterfall result, and
`lp_net_irr` / `lp_moic` lifted for consumers that only want the headline.
A non-converging IRR is `None`, never NaN — `json.dumps(nan)` is invalid
JSON and these results are persisted to JSONB.

**`config.py`** gains an AM-fee block next to the capital block:
`AM_FEE_PCT = 0.01` and `AM_FEE_BASE = "invested_equity"`. Scalars passed
as parameters, never a `_PATCHED_DICTS` entry — the in-place mutation
gotcha item B shipped and this repo has now paid for twice. `DEBT_TERMS`
and `WATERFALL_TERMS` travel as parameters for the same reason.

**`analysis/checks.py`** — `_sources_uses_ties` compares Uses to
`basis + financing_costs`, and its message names the identity so a
failure says which side moved. `build_sources_uses` gains a top-level
`financing_costs` key so the check reads a number rather than searching
the `uses` list by key.

**`engine.py`** — `run_analysis` gains `debt_terms` and `waterfall_terms`
override dicts alongside the existing `capital_structure`, resolves them
through `resolve_debt_terms` / `resolve_waterfall_terms(...,
capital_structure=capital)`, sizes the loan once off the base scenario,
feeds `build_sources_uses` the real `senior_debt` and `financing_costs`,
and hangs a `levered` dict off `AnalysisResult` with one entry per
scenario.

**`webapp/services.py`** — the resolved debt and waterfall terms join the
stamped assumptions, so a run records the terms it used. Auditability is
built in, not bolted on.

## Files

New: `model/levered.py`, `tests/test_levered.py`, this plan.
Modified: `config.py`, `analysis/checks.py`, `model/returns_model.py`
(one added return key), `engine.py`, `webapp/services.py`, `CLAUDE.md`,
`tests/test_debt.py` (two deletions, one comment), `tests/test_waterfall.py`
(one deletion).

One new production file against the no-net-complexity guardrail:
`model/levered.py` carries a distinct domain — equity cash-flow assembly
— that is neither loan arithmetic nor distribution arithmetic nor the
unlevered DCF, and it was pre-announced as the meeting point in both E1's
and E2's plans. Folding it into `returns_model.py` would mix the
unlevered wrapper with levered assembly in one file. No new dependency:
`numpy_financial` is already pinned and the accrual and roll-forward
loops are explicit arithmetic.

Deleted: `test_no_production_module_imports_the_debt_layer_yet`,
`test_nothing_outside_tests_imports_the_waterfall`,
`test_financing_costs_break_the_basis_tie_until_e3_extends_it`. The first
two were the proof that E1 and E2 moved no published number; wiring is
now the point, so they have done their job. Deleting them is stated here
so it reads as the plan rather than as a guard quietly disappearing.

## What the audit found, and what changed

Two independent agents reviewed the production diff. Both, separately,
found the same blocking defect, and it was the one this plan had already
promised not to ship.

**BLOCKING — the levered lens was computed and thrown away.** `engine.py`
set `result.debt` and `result.levered`, and `webapp/services.py`'s
persisted payload never carried either. Every deal computed an LP net
IRR, a waterfall, an AM fee and a debt schedule, then discarded all of it
when the worker returned. No error, no log, nothing missing on screen —
the failure mode was silence, on every single run. This plan's own text
says "the levered lens is computed and **persisted** on every deal", so
this was a gap against the stated acceptance criterion, not a scope
question. Fixed: `"debt"` and `"levered"` join the payload, with
`test_run_payload_carries_the_levered_lens` as the regression. Rendering
them stays E3b, and the payload comment says so, so the gap that remains
is deliberate and legible instead of invisible.

**The tie check was self-referential.** `_sources_uses_ties` read
`financing_costs` from the very `sources_uses` dict it was validating. A
caller that forgot to pass `financing_costs=debt["financing_costs"]`
produces a `total_uses` missing the fee AND a reported `financing_costs`
of 0 — both wrong the same way, so `uses == basis + 0` reconciled and the
BLOCKING check PASSED on a deal underfunded by the whole origination fee,
which surfaces as an equity shortfall at closing. `CheckInput` now
carries `debt`, and the check cross-validates against the module that
computed the fee. The test asserts the old blind spot: same broken stack,
PASS without `debt=`, FAIL with it.

**`matures_before_exit` was a log line nobody reads.** E1 computed it and
warned; E1 was unwired, so it was dead code. E3a put a sized loan on
every deal and made it live. A `logger.warning` reaches a server log
while the results page shows a levered IRR computed as though the loan
amortized straight past its own maturity — no refinancing, no rate reset,
no prepayment cost. It is now `loan_matures_before_exit`, ADVISORY in the
register, where every other assumption-quality finding already lives.

**Three hardening fixes**, none reachable today, all one refactor away:
the `debt` dict was aliased into all three scenarios' results rather than
copied (the first consumer to annotate it in place would corrupt the
other two); `payoff_balance` and `exit_fee` silently defaulted to 0.0,
which on a missing key computes the exit as though the loan were forgiven
at sale and reports an LP net IRR that is too HIGH; and `am_fee_pct` used
`is None` where every sibling resolver in the repo uses
`not in (None, "")`, which would have made E3b's form field raise on
`float("")` the day it landed.

One agent also asked whether the AM-fee base should decline as capital is
returned mid-hold. It should not, and the docstring now argues why:
splitting a distribution into capital and profit is what the waterfall
does, from distributable cash the fee has already been deducted from — a
second circularity. On a single-asset deal the two definitions agree.

Also corrected during the audit: a `git checkout` and a relative-path
heredoc were aimed at the shared primary working tree. The PostToolUse
hook caught the write and the PreToolUse guard blocked the checkout,
which is exactly the pair of failures CLAUDE.md's parallel-session
section describes. Reverted; no foreign state touched.

## Acceptance

- Oracles A, B and C reproduce to the cent; LP IRRs to four decimals.
- The unlevered IRR is unchanged by the presence of a loan — asserted
  directly, by running one deal with and without debt and comparing
  `project_cash_flows` output.
- `sources_uses_ties` PASSES on a levered deal and FAILS if the
  financing-cost term is dropped from either side.
- Oracle A's LP net IRR is BELOW its unlevered IRR (negative leverage is
  representable).
- All four flagged traps have a test that fails without the handling.
- The loan is sized once: bear, base and bull carry an identical
  `loan`, `binding_constraint` and debt-service series.
- Edge cases: zero loan (covenants bind to nothing), a deal whose
  levered CF is negative in the exit year, `hold_years` of 1 and 10,
  a 25% co-invest deal, a non-converging LP IRR returning `None`.
- The repo's gates are green locally, all four of them, because a green
  test run is not a green build: `python manage.py check`,
  `python manage.py makemigrations --check --dry-run`,
  `python manage.py collectstatic --noinput`, `python -m pytest tests/`.
