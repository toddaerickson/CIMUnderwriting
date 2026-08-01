# Item E1 — the debt layer (`model/debt.py`)

Plan date: 2026-08-01. Scope contract:
[docs/scoped-backlog.md](../../scoped-backlog.md) item E1. Design input,
market terms and numeric oracles:
[docs/levered-waterfall-design.md](../../levered-waterfall-design.md).

Review tier: **high-risk** (money math). Depends on item B (the one
projection) and item D (the Sources & Uses schema this fills).

## What ships

A pure module — `DebtTerms`, `size_loan`, `amortization_schedule`,
`build_debt_schedule` — plus `config.DEBT_TERMS` defaults and a test
module that reproduces design-doc oracles 4 and 5 to the cent.

**No wiring.** The assumptions form, results page, memo and Excel writer
are untouched; that is item E3. Nothing in the app imports `model.debt`
yet, so no published number can move — asserted by a regression test
rather than assumed.

This mirrors how item D shipped `senior_debt` and `financing_costs` as
real-but-zero parameters of `build_sources_uses`: establish the schema
first, fill it second. The alternative — wiring debt into Sources & Uses
now — would move the published equity figure while still being unable to
produce the LP net IRR that justifies leverage at all. That number
arrives with E4.

## Oracles verified before building

Both design-doc oracles were recomputed from scratch and reproduce
exactly, so they are a trustworthy target rather than a recalled one:

- **Oracle 4** ($6.5M, 6.50%, 30yr amort, 24mo IO): IO debt service
  422,500.00/yr · amortizing payment 41,084.42/mo · annual 493,013.06 ·
  constant 7.5848% · payoff at month 60 **6,267,120.72**.
- **Oracle 5** ($10M price, $600k Y1 NOI, 65% / 1.25x / 10% @ 6.5%/30yr):
  caps 6,500,000 / 6,328,432.78 / 6,000,000 → loan **6,000,000**, bound by
  debt yield · equity 4,100,000 · annual DS 455,088.98 · payoff
  5,616,658.65 · exit 11,129,031.11 · levered IRR **9.9952%** · MOIC
  1.5664.

Two conventions the doc leaves implicit, pinned here because both change
the answer and neither is stated in the oracle:

1. **The amortizing payment is sized on the full `amort_years`**, not
   re-amortized over the months remaining after the IO period. Sizing on
   360 months is what reproduces 6,267,120.72; re-amortizing over the
   remaining 336 would not. This is the market convention — the
   amortization schedule is a term-sheet number independent of the IO
   period — and it means the loan balloons rather than fully amortizing.
2. **Oracle 5's exit is gross.** No disposition cost is deducted, and the
   "1% closing" in the doc's parenthetical is the *acquisition* closing
   cost inside the 4,100,000 equity figure. The test therefore builds its
   cash flows directly rather than borrowing the pipeline's defaults,
   which carry a 1.5% disposition cost from item B.

## Design decisions

**Sizing tests every NOI basis, not one.** The design doc names "DSCR
tested on the wrong NOI basis" as a common modeling error, and the fix is
to model trailing *and* stabilized. `size_loan` computes each constraint
on every NOI basis it is given and takes the minimum across all of them,
reporting both the binding constraint and the basis that bound it.
`stabilized_noi` defaults to `y1_noi`, which collapses to the single-basis
case and reproduces oracle 5. Consequence: on a value-add deal the loan
sizes off the weaker (Year 1) test, which is what a bank actually does. A
bridge lender sizing on stabilized alone is a different product and would
be a parameter added when something needs it.

**DSCR is tested on the payment that will actually come due.** If the loan
is interest-only for its entire term, the DSCR constant is the IO rate;
otherwise it is the amortizing constant, because that payment arrives
before maturity. Sizing a partial-IO loan on its IO payment is the
"max-leverage sizing with no covenant headroom" error from the design doc.

**Maturity before exit is reported, not silently rolled past.** If the
hold runs beyond `term_years` the balloon is due before the sale, which is
a refinancing event and out of scope for E1. The schedule returns
`matures_before_exit=True` and logs, rather than quietly amortizing
through a maturity date that does not exist. Per CLAUDE.md, a missing
capability announces itself.

**A rate that cannot be resolved raises.** `DebtTerms` accepts either an
all-in `rate` or `index_rate` + `spread`. If neither resolves, the
constructor raises rather than defaulting to zero — a 0% loan produces a
spectacular IRR and no error, which is the worst available failure mode.

**Terms that are not loans are rejected at the boundary.** Added after the
pre-push audit found two inputs that produced confident nonsense instead
of an error: `amort_years=0` fell through to a sizing constant of
1200%/yr, which sizes a loan two orders of magnitude too small, and
`term_years=0` silently switched off both the full-IO test and the
maturity warning. Both now raise in `__post_init__`, as do negative
months, ratios and fees. Validating at construction rather than inside
the math matters because E3 will feed these from a web form.

**Zero coverage floors mean "no such covenant", not "no debt".** Reading a
missing `min_dscr` as a zero ceiling would refuse debt on every deal that
omits one. The consequence is that clearing BOTH floors lends the full
LTV with no coverage test at all, which is a real path and is pinned by
its own test so it stays a decision rather than a surprise.

**`DEBT_TERMS` stays out of `webapp.services._PATCHED_DICTS`.** Same
reasoning config.py already records for the capital-structure scalars: a
patched dict is mutated in place for one deal's run, so anything resolving
it outside that run's lock reads another deal's values. Debt terms travel
as parameters.

## Files

New: `model/debt.py`, `tests/test_debt.py`, this plan.
Edited: `config.py` (a `DEBT_TERMS` defaults block).

Two new files against the no-net-complexity guardrail: `model/debt.py` is
named by the scope contract, carries a distinct domain (loan sizing and
amortization) that does not belong in the returns model, and gains a
sibling in `model/waterfall.py` at E2. No new dependency — the monthly
roll-forward is explicit arithmetic, and `numpy_financial` is already
present for the IRR assertions.

## Acceptance

- Oracles 4 and 5 reproduce to the cent; the levered IRR to 4 decimals.
- `size_loan` reports which of the three constraints bound, on which NOI
  basis, for a case engineered to make each one bind in turn.
- Amortization edge cases: zero rate, full-term IO, a loan that fully
  amortizes inside the hold, hold beyond maturity, zero/negative loan.
- Negative NOI supports no debt (caps clamp at 0, never negative).
- Origination fee is charged on the loan at close; the exit fee is charged
  on the payoff balance at sale.
- No module outside `tests/` imports `model.debt` — the proof that
  unlevered results cannot have moved.
- Full suite green.
