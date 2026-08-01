# Item E2 — the single-tier LP waterfall (`model/waterfall.py`)

Plan date: 2026-08-01. Scope contract:
[docs/scoped-backlog.md](../../scoped-backlog.md) item E2. Design input,
conventions and numeric oracles:
[docs/levered-waterfall-design.md](../../levered-waterfall-design.md).

Review tier: **high-risk** (money math). Depends on E1 (the debt layer,
PR #28) only for sequence — E2 imports nothing from it. The two modules
meet at E3.

## What ships

A pure module — `WaterfallTerms`, `resolve_waterfall_terms`,
`run_waterfall` — plus a `config.WATERFALL_TERMS` defaults block and a
test module that reproduces design-doc oracles 1–3 to the cent.

**No wiring**, exactly as E1 shipped: the assumptions form, results page,
memo and Excel writer are untouched. Nothing in the app imports
`model.waterfall`, asserted by an AST test rather than assumed, which is
the proof no published unlevered number can have moved. E3 deletes that
test when wiring is the point.

## Oracles verified before building

All three design-doc oracles were re-derived from scratch — accrual path,
promote and LP IRR — before a line of `model/waterfall.py` existed, so
the tests assert an independently computed answer rather than a snapshot
of the implementation. Flows in every case: contribute $1,000,000 at
close, then distribute 50k / 60k / 70k / 80k / 1,500,000.

- **Oracle 1** (annually compounded, 20% promote): balance path
  1,080,000 → 1,112,400 → 1,136,592 → 1,151,919.36 → 1,157,672.9088 ·
  residual 342,327.09 · GP promote **68,465.42** · LP total 1,691,534.58 ·
  LP IRR **12.1340%** · MOIC 1.6915.
- **Oracle 2** (simple, ROC first): accruals 80,000 / 76,000 / 71,200 /
  65,600 / 59,200 (352,000 total) · GP **81,600.00** · LP IRR **11.9500%**.
- **Oracle 3** (simple, pref first): accrual 80,000/yr · unpaid pref
  entering the year-5 distribution 140,000 · GP **72,000.00** ·
  LP IRR **12.0846%**.

Three conventions the oracles depend on but do not state, pinned here
because each one changes the answer:

1. **The accrual is computed on the unreturned capital at the START of
   the period**, before that period's contributions or distributions.
   Oracle 2's second-year accrual of 76,000 is 8% of 950,000 — capital
   after year 1's return, not before it. Accruing on the ending balance
   would give 71,200 in year 2 and miss every subsequent number.
2. **Period 0 does not accrue.** Capital contributed at close accrues over
   year 1 and is first credited at period 1; a contribution made at
   period *k* starts accruing at *k+1*. Any other reading pays a year of
   pref on money that has been in the deal for zero days.
3. **All three oracles are the zero-co-invest case.** The $1,000,000 is
   the LP's, and GP promote is 20% of the whole residual because the
   LP-attributable share *is* the whole residual. At the shipped
   `gp_coinvest_pct` of 10% the same flows give GP 61,618.88 of promote,
   not 68,465.42. The oracles therefore pin the arithmetic, not the
   default — the co-invest case gets its own independently derived test.

## Design decisions

**Contributions and distributions are period-indexed and the same
length.** `distributions[t]` is total distributable cash at period *t*;
`contributions[t]` is total equity called at period *t*, period 0 being
close. Mismatched lengths raise rather than being padded: padding a
5-element distribution list against a 1-element contribution list aligns
year 1's cash to period 0 and silently deletes a year of pref. A bare
scalar is accepted for `contributions` as shorthand for "all equity at
close", which is unambiguous because a scalar cannot be misaligned — and
is what E3 will pass from item D's `total_equity`.

**GP and LP ride tier 1 pari passu, and the split is exact.** Both
accrue at the same rate on the same proportion of every contribution, so
their claims stay in the fixed ratio `gp_coinvest_pct : 1 −
gp_coinvest_pct` for as long as tier-1 dollars are also split pro rata —
which they are. So the module accrues once, in aggregate, and splits each
tier-1 payment by that ratio. Two separately rolled accounts would give
the same numbers with twice the arithmetic to audit.

**Promote is charged on the LP-attributable residual only.** LP receives
`(1 − c) × R × (1 − x)`, GP receives `c × R + (1 − c) × R × x`. Charging
promote on 100% of the residual pays the GP a promote on its own
co-invest capital — the design doc lists it as a pitfall and the scope
contract's stamp table names it open question 5.

**A promote paid before a later capital call is kept, and reported.**
Found by the review, reproduced before repairing: contribute $1,000,000,
distribute $2,000,000 in period 2 (which clears tier 1 and pays the GP
$150,048), then call $5,000,000 in period 3. Tier 1 re-opens, the LP ends
at 0.39x MOIC and −89.6% IRR, and the GP still has the promote. The first
draft's docstring claimed the per-period gate "removes the need for a
clawback structurally", which overstates it — the gate guarantees only
that each promoted dollar followed a full return of capital and pref *as
of that period*.

The arithmetic is right and stays: LPA question 6 is **answered — no
clawback**, so a GP that keeps interim promote is the operator's actual
fund terms, not a modeling error, and clawback mechanics are explicitly
out of scope. What was wrong was the claim and the silence. The result
now carries `interim_promote` (promote paid in any period a later call
re-opened) and logs a warning, so no consumer can print a promote without
the condition attached. On a single-asset deal the residual normally
arises only at sale, after which nothing can be called; a refinance
distribution followed by a follow-on call is the shape that opens it.

**Ordering is inert under compounding, and that is tested, not asserted.**
The design doc claims ROC-vs-pref ordering is mathematically irrelevant
once the pref compounds, because there is one balance rather than two
claims. `test_ordering_does_not_move_a_compounded_waterfall` runs the same
flows both ways and asserts every LP and GP dollar matches, so the claim
is checked in CI instead of trusted.

**Conventions this module does not implement RAISE; they do not fall back
to the default.** `catch_up=True`, `accrual_base="committed"`,
`am_fee_treatment="netted_from_lp"`, and any unrecognised
`pref_compounding` or `ordering` all raise at construction. The design
doc's own numbers are the argument: the same flows produce GP promote of
68,465 / 72,000 / 81,600 depending only on which convention is in force.
Silently substituting one for another produces a confident wrong LP net
IRR, which is the failure mode the whole item exists to remove.

This is a deliberate split from `resolve_capital_structure`, which
downgrades an unrecognised *basis* to `amount` with a warning. Unknown
override **keys** are still logged and ignored here (E1's contract — a
row written by a future version must not take down a run on an older
one); it is unknown **values** for the four convention fields that raise,
because those are the ones that silently re-price the promote.

**The AM fee is not this module's arithmetic.** Open question 4's
default is "above the waterfall (deal expense)", so the fee reduces
distributable cash before `run_waterfall` ever sees it — E3's cash-flow
line, not a waterfall tier. `am_fee_treatment` exists anyway so the
assumption stamp is complete and so the alternative announces itself
instead of being silently ignored.

**`gp_coinvest_pct` is read from `config.GP_COINVEST_PCT`, not
duplicated into `WATERFALL_TERMS`.** config.py's capital block already
says "item E2 reads the same number for the waterfall's pari-passu tier",
and a second copy is exactly the silent divergence CLAUDE.md's
single-source-of-truth rule forbids. The dataclass carries a mirrored
default for direct construction, guarded against drift in CI the same way
`DebtTerms` is. E3 feeds it the value
`model.returns_model.resolve_capital_structure` already resolves, so the
capital stack and the waterfall cannot disagree about how much of the
equity is the GP's.

**`WATERFALL_TERMS` stays out of `webapp.services._PATCHED_DICTS`**, for
the reason config.py records at length above the capital block: a patched
dict is mutated in place for one deal's run, so anything resolving it
outside that run's lock reads another deal's terms. Waterfall terms
travel as parameters.

**Under a compounded pref, the capital / pref split of a tier-1 payment
is presentation only.** There is one balance and one claim; the split
exists so the memo and Excel can print a Return-of-Capital row and a
Preferred-Return row. The module applies accrued pref first and says so.
No LP or GP dollar depends on it — every distribution is computed from
`tier1_paid` and `residual`, never from the split — and
`test_tier_one_reconciles_to_its_capital_and_pref_parts` pins the split
to the payment and to the ending balance.

**The result is a dict, not the `WaterfallResult` dataclass the design
doc names.** Deviation, recorded on purpose: `build_debt_schedule`,
`build_sources_uses` and `project_cash_flows` all return dicts, and the
memo writer, Excel writer, Django templates and `webapp.services.json_safe`
all consume dicts. A dataclass here would be the only one in the model
layer and would need unpacking at every consumer.

**A non-converging IRR is `None`, never NaN.** Same handling as
`project_cash_flows`: `webapp.services.json_safe` exists because
`json.dumps(nan)` is invalid JSON that Postgres JSONB rejects, and E3
persists these results.

## Handoff to E3: a debt-service shortfall is not a negative distribution

E1's plan recorded the integration trap its own review found before E3
could walk into it. This is E2's equivalent, and it is the reason
`run_waterfall` rejects a negative distribution instead of accepting one.

E3 will build the distribution series from levered cash flow — NOI less
annual debt service, with the exit year adding net sale proceeds less the
loan payoff. **That series can be negative in a year**, and it is exactly
the years a levered deal is most interesting: a partial-IO loan rolling to
its amortizing payment, or a value-add year 1 where in-place NOI does not
yet cover a loan sized on stabilized NOI. `_align_series` raises on it
rather than netting it, because a negative distribution silently netted
against the pref accrual pays the LP a *reduced* preferred return for the
privilege of the deal losing money.

There are two correct treatments and E3 must choose per period, not once:

1. **Draw the operating reserve.** Item D funds a reserve at close, puts
   it in `total_basis`, and deliberately does not release it at exit. A
   shortfall covered from that reserve is not a waterfall event at all —
   the distribution for the period is 0, and the money was already
   contributed at close.
2. **Call capital**, once the reserve is exhausted. That is a
   `contributions[t]` entry, which this module already supports and
   `test_a_mid_stream_capital_call_starts_accruing_the_following_period`
   already pins: the call joins the accrual base and starts earning pref
   the *following* period.

Both are one line in E3. Neither is a negative distribution, and the
current hard failure is what forces the choice to be made explicitly
instead of defaulted into.

**A second, smaller seam.** `run_waterfall` returns the frozen
`WaterfallTerms` under `terms`, and E3 persists results through
`webapp.services.json_safe` into Postgres JSONB. A dataclass is not
JSON-serialisable — E3 either drops the key or converts it
(`dataclasses.asdict`). `assumption_stamp` is already plain dicts and
strings, so the displayed stamp needs no conversion.

## Files

New: `model/waterfall.py`, `tests/test_waterfall.py`, this plan.
Edited: `config.py` (a `WATERFALL_TERMS` defaults block).

Two new files against the no-net-complexity guardrail: `model/waterfall.py`
is named by the scope contract, carries a distinct domain (equity
distribution) that is not loan arithmetic and not the unlevered DCF, and
was pre-announced as `model/debt.py`'s sibling in E1's plan. No new
dependency — the accrual loop is explicit arithmetic and `numpy_financial`
is already pinned for the IRRs.

## Acceptance

- Oracles 1–3 reproduce to the cent; LP IRRs to four decimal places.
- The 10% co-invest case is derived independently and pinned: GP promote
  61,618.88, and GP's tier-1 dollars are exactly 10% of tier 1.
- Ordering is proven inert under compounding and material under simple.
- Edge cases: distributions that never clear the pref (residual 0,
  promote 0, shortfall reported), zero contributions, a mid-stream
  capital call, a zero promote split, zero pref, negative or NaN inputs,
  mismatched series lengths.
- Every unsupported convention raises with a message naming it.
- `WaterfallTerms` defaults do not drift from config, and
  `WATERFALL_TERMS` does not carry a second `gp_coinvest_pct`.
- No module outside `tests/` imports `model.waterfall`.
- Full suite green.
