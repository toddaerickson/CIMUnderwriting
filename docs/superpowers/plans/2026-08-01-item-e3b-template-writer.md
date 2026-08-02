# Item E3b — the XLSM template writer stops deciding values

Plan date: 2026-08-01. Scope contract:
[docs/scoped-backlog.md](../../scoped-backlog.md) §E3, rules 1–4 and its
acceptance list. Predecessors: [E3a — the levered seam](2026-08-01-item-e3a-levered-seam.md)
(the arithmetic), [E3b — the levered surfaces](2026-08-01-item-e3b-levered-surfaces.md)
(the web/memo/xlsx rendering, shipped in #34). This is E3b's remaining half.

Review tier: **high-risk**. It changes numbers a deliverable prints.

## The problem

`output/template_writer.py` is a parallel assumptions system. It asserted
its own debt terms, its own waterfall, its own growth ladder and its own
stabilized vacancy, and none of them matched the model. That was a
latent inconsistency while leverage was hypothetical. Item E3a made it a
live one: every deal now carries an LP net IRR computed from
`DEBT_TERMS` and `WATERFALL_TERMS`, while the .xlsm shipped alongside it
asserted 6.5% / 360-month / 12-month-IO paper and a 6%-GP waterfall read
from environment variables.

## The rule, and how it is enforced

**The template never decides a value.** Every number written into the
workbook reads from the resolved assumption set or the run's computed
results.

Enforced by `tests/test_template_writer.py::test_no_numeric_literals_in_write_paths`
— an AST walk asserting that no numeric literal appears on the **value
side** of a cell write. Row and column arguments sit on the target side
and are not checked; a column index is schema, not an assumption.

The gate has **no exceptions**, deliberately. Everything structural is
named at the top of the module instead: `_COL_IN_PLACE`, `_AT_CLOSING_*`,
`_GROWTH_BEGINS_YEAR`, `_DOLLARS_DP`. An allowlist inside the test would
be a place to hide the next literal; a named constant with a comment
saying why it is schema is reviewable.

A second test pins that the only surviving `os.environ` read is
`UW_TEMPLATE_PATH`, which locates a file and is not an assumption.

## Where the resolved values come from

Resolved objects are passed in, never re-resolved here:

```python
generate_template(..., debt_terms=<DebtTerms>, waterfall_terms=<WaterfallTerms>,
                  am_fee_pct=<float>, sources_uses=<dict>)
```

`engine.py` resolves once and hands the same objects to every writer.
Re-resolving inside the writer would need the deal's capital structure to
get `gp_coinvest_pct` right — the trap `resolve_waterfall_terms` documents
at length: a deal edited to 25% co-invest would print a 25/75 stack in
the .xlsx beside a 10/90 waterfall in the .xlsm, neither flagged. `None`
falls back to config resolvers, which is the whole resolved set on the
CLI path (`run.py` passes no overrides).

Scenario-driven values read `scenario_results[BASE]["params"]` — the
params the projection actually used — rather than re-reading
`config.SCENARIO_DEFAULTS`, which would answer with whatever config says
now instead of what the run did.

## Two deliberate deviations from the scope contract

**1. Rule 4's "LTC stays 0" is not implemented as written.** The rule was
drafted when leverage was opt-in per deal. E3a reversed that on
2026-08-01 (CLAUDE.md design decision 6: every deal is sized at
`DEBT_TERMS` and carries an LP net IRR), so the opted-out state the rule
describes no longer exists. Writing an all-equity workbook beside a
levered memo would leave precisely the contradiction this item exists to
close. H64 therefore carries `sources_uses["ltv"]` — loan / total uses,
which despite the key name is exactly what the template's H64 means
(`K64 = H64*$K$55`, and K55 is Total Uses; verified against the real
workbook by `test_real_template_still_has_the_cells_the_stub_claims`).

The rule's *rationale* is preserved: with no Sources & Uses the block
stays all-equity and the terms cells still carry the resolved loan, so
"a user who flips LTC in Excel gets the terms the app would have used"
still holds on the CLI path.

**2. Several checklist items were already fixed.** K181/K180 (exit cap)
landed in #31, K182 (cost of sale) and D182 (sale month) in #23. The
backlog's list predates both. They are verified, not re-done.

## What is NOT reconciled, and is stamped instead

The workbook computes its own returns, and two of its conventions are not
ours. Neither is reachable from an input cell, so both are recorded in
the module docstring rather than papered over:

1. **The pref is an IRR hurdle** (H257 is literally labelled "IRR
   Hurdle"). `model.waterfall` runs an accrual account on
   contributed/unreturned capital. Writing `pref_rate` into H258 makes
   them agree on the rate, which is as far as an input cell reaches; the
   promote dollars still differ.
2. **The AM fee is charged on LP equity** (`H254 = K60*G254/12`, K60 is
   LP equity) against `config.AM_FEE_BASE = "invested_equity"`. At a 10%
   co-invest the workbook's fee runs ~10% light. Grossing the rate up to
   1.11% would make the dollars tie while printing a fee rate the fund
   does not charge — a hidden discrepancy traded for a visible one. The
   true rate is written and the gap is stated.

Both are item T's to reconcile.

**Tier mapping, since "write 0.20 into three tiers" is not obviously the
same waterfall as one 8% hurdle.** It is: H259/H260/H261 each chain to
the row above (`=+H258`, `=+H259`, `=H260`), so setting H258 sets all
four hurdles to the pref and the structure collapses to a single tier.
`J259 = I259+(1-I259)*$J$253` is promote plus the GP's pari-passu share
— the same construction as ours, promote on the LP-attributable residual
only. The chaining is asserted against the real workbook.

## Testing

The real `template_uw.xlsm` is a 3 MB proprietary macro file and is
gitignored, so CI has never had it. Tests therefore run against a
**synthetic stub** carrying only the cells under test, with the formulas
it must overwrite reproduced verbatim. A skipped-unless-present test
(`test_real_template_still_has_the_cells_the_stub_claims`) asserts the
real workbook still has those formulas, that row mapping, and the two
cell semantics the design rests on — so a stub that drifted fails loudly
rather than passing against fiction.

Regression evidence: both writers were run over one fixture deal with
writes recorded rather than the workbook round-tripped (openpyxl cannot
re-read its own output for this template — the real file has a
Chartsheet its reader trips on, and recording also catches cells the new
code stops writing). 24 of 265 written cells differ; every one is
enumerated in the PR.

## Deferred

Python↔XLSM formula parity via the `formulas` library — the scope
contract calls it a stretch goal and says not to block on it. It would
be the only way to prove the two IRRs agree rather than the two
assumption sets.
