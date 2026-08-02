# Item E3b — the levered surfaces

Plan date: 2026-08-01. Scope contract:
[docs/scoped-backlog.md](../../scoped-backlog.md) item E3. Design input
and conventions: [docs/levered-waterfall-design.md](../../levered-waterfall-design.md).
Predecessor: [item E3a](2026-08-01-item-e3a-levered-seam.md).

Review tier: **standard**, plus BOTH required UI passes. E3a computed and
persisted the levered lens on every deal; **nothing renders it**. This
item is presentation and input only — it moves no arithmetic, and the
acceptance criteria below say so explicitly rather than trusting that a
presentation diff stayed presentational.

## What already exists, and must not be recomputed

`result_json["debt"]` (the one sized loan) and
`result_json["levered"][scenario]` (equity CF, AM fee, waterfall,
assumption stamp) are persisted with every run. E3b **renders those** —
it does not call `build_levered_returns` a second time. A results page
that re-derives an LP net IRR against whatever config says today is a
different number wearing the run's date, which is the same defect the
run payload was fixed to avoid.

`webapp.services` already reads three override keys the assumptions page
does not yet write: `overrides["debt_terms"]`,
`overrides["waterfall_terms"]` and `overrides["am_fee_pct"]`. This item
supplies them. No engine or services signature changes.

## The input surface: which terms get a field, and which do not

Twelve debt fields, seven waterfall fields and two AM-fee constants are
in scope on paper. Exposing all twenty-one would be wrong twice over —
clutter, and three of them **raise** on their second value.

**Debt — exposed (9):** `rate`, `amort_years`, `io_months`,
`term_years`, `max_ltv`, `min_dscr`, `min_debt_yield`, `orig_fee_pct`,
`exit_fee_pct`.

**Debt — NOT exposed (3):** `loan_type`, `index_rate`, `spread`.
Floating-rate paper is a MODE, not two more boxes.
`resolve_debt_terms` clears the seeded fixed rate only when a floating
half arrives *without* an explicit `rate` — and the form prefills `rate`
from the resolved terms, so a naive pair of floating boxes would post a
fixed rate alongside them on every save, hit the "both named, fixed
wins" branch, and silently ignore what the analyst typed. Doing it right
needs a fixed/floating selector and there is no JavaScript on this page.
Config is bank fixed-rate paper; floating stays override-only until it
earns its own item.

**Waterfall — exposed (4):** `pref_rate`, `promote_split`,
`pref_compounding`, `ordering`. Both values of both selectors are
implemented.

**Waterfall — NOT exposed (3):** `accrual_base`, `am_fee_treatment`,
`catch_up`. Each has exactly ONE implemented value;
`WaterfallTerms.__post_init__` raises on the other. A dropdown whose
second option crashes the run is a trap, not a setting. They stay in the
assumption stamp, which is where an open LPA question belongs.

**AM fee — exposed (1):** `am_fee_pct`. `am_fee_base` has one
implemented value and `model.levered._fee_base` raises on any other;
same rule as above.

### Unexposed keys must SURVIVE a save

`build_overrides` rebuilds each section from the form, so a key with no
field is dropped from a stored override the next time anybody presses
Save. For the capital block that could not happen — every key had a
field. Here six do not. So the debt and waterfall deltas are merged onto
the previously saved override's unexposed keys:

```python
out["debt_terms"] = {**{k: v for k, v in prev.items() if k not in DEBT_FORM_KEYS},
                     **deltas}
```

Without it, a CLI-set floating rate is silently converted to the config
fixed rate by an unrelated edit on the assumptions page — the deal keeps
running, at a different cost of debt, with nothing anywhere saying so.
A test pins it.

### The percent-vs-decimal boundary

Flagged by E3a as the thing that will break a naive form, with a
measured cost: `rate=6.5` meaning 6.5% priced a $6.5M loan at
$3,520,833/mo against a correct $43,888/mo. `DebtTerms` now RAISES on
any decimal field > 1.0, so a naive form does not silently misprice —
it fails every save. Either way the form owns the conversion, as it does
for every other percentage on this page:

| Class | Fields | Conversion |
|---|---|---|
| Percent | `rate`, `max_ltv`, `min_debt_yield`, `orig_fee_pct`, `exit_fee_pct`, `pref_rate`, `promote_split`, `am_fee_pct` | ÷100 save, ×100 load, `max_value=100` |
| Ratio | `min_dscr` | none — 1.25x is a coverage ratio, and `DebtTerms` exempts it from the >1.0 rule for exactly that reason |
| Integer | `amort_years`, `term_years` (`min_value=1`), `io_months` (`min_value=0`) | none |
| Choice | `pref_compounding`, `ordering` | none |

`min_value`/`max_value` are set so that every `DebtTerms` /
`WaterfallTerms` raise is unreachable **from the form**. The dataclass
guards stay as the backstop for the CLI and stored rows, which is what
E1 built them for.

### Validation calls the real resolver

Bounds on fields are not the same thing as a resolvable set of terms, so
`AssumptionsForm.clean` builds the deltas and calls
`resolve_debt_terms` / `resolve_waterfall_terms` for real, surfacing a
`ValueError` as a form error. Re-listing the dataclass's rules in the
form is the duplicated-constant failure this repo has a rule against;
running the actual resolver cannot drift from it.

Reported as a NON-field error, for the reason item D's basis checks
learned the hard way: `add_error(field, ...)` deletes that field from
`cleaned_data`, and `assumptions_preview` reads `cleaned_data` on an
invalid form by design.

### `loan_matures_before_exit` goes live here

The ADVISORY check exists and cannot fire today: config term is 10
years and the hold is capped at 10. Making `term_years` editable is what
switches it on. A test sets `term_years=3` on a 5-year hold and asserts
the finding appears in the register — the first assertion in the repo
that the check can fire at all.

## The output surfaces

**Results page — the Returns tab, below the unlevered tables.** Not a
new tab: a lens the analyst has to go looking for is not a second lens,
and the unlevered screen stays visually first because it is still the
primary gate. `webapp/results.py` gains `levered_context(r)`, a pure
formatter over the persisted payload like every sibling context builder.
It renders:

- one row per scenario: LP net IRR, LP MOIC, GP promote, equity;
- the loan strip: amount, binding constraint, all-in rate, amortization,
  IO, term, LTV, year-1 DSCR, debt yield, origination fee;
- the base case's per-year levered cash flow (NOI, debt service, AM fee,
  distribution, capital call, DSCR);
- **the assumption stamp, unconditionally.** No LP net IRR renders
  without it — the scope contract's rule, and five of the inputs are
  still open LPA questions;
- the flags that change what the number means: capital called after
  close, loan maturing before exit, and leverage being DILUTIVE (LP net
  below the unlevered IRR), which is a legitimate outcome the model is
  allowed to print and a reader must not misread as a bug.

**Memo — a level-2 subsection inside section 6** ("Valuation &
Returns"), after Sources & Uses. Level 2, not a new section 7: ten
numbered sections are referenced from `_add_section_10`'s recommendation
text and from the CLI summary, and renumbering them to insert a
presentation block is churn with a real chance of an off-by-one.

**Excel — a new "Levered Returns" tab**, after "Sources & Uses" and
before "Checks", so the workbook keeps reading deal → returns → capital
→ levered → integrity.

Both degrade cleanly when the levered payload is absent (a CLI run
against a deal with no NOI, or a stored run from before E3a), which is
the same contract `sources_uses` and `checks` already follow in both
writers.

**`engine.py`** passes `levered=` and `debt=` to `generate_memo` and
`generate_excel`. That is the only non-presentation line in the item.

## Files

Modified: `webapp/forms.py`, `webapp/views.py`,
`webapp/templates/webapp/assumptions.html`, `webapp/results.py`,
`webapp/templates/webapp/_tab_returns.html`, `output/memo_writer.py`,
`output/excel_writer.py`, `engine.py`, `model/debt.py` (docstring only —
see below), `tests/`.

New: this plan. **No new production file, no new dependency** — the
no-net-complexity guardrail is satisfied by editing, and every surface
here already exists.

`model/debt.py`'s `build_debt_schedule` docstring still prescribes E1's
route ("E3 must ALSO extend the projection to carry financing costs into
the basis") and names
`test_financing_costs_break_the_basis_tie_until_e3_extends_it`, a test
E3a deleted. The operator reversed that route on 2026-08-01. Left alone
it tells the next reader — E4 — to do the thing this repo decided not to
do, citing evidence that no longer exists. Corrected in place; no code
change.

## Acceptance

- **The unlevered numbers are byte-identical.** A run with the levered
  surfaces rendered produces the same `scenario_results`, `max_offer`
  and `sources_uses` as one without. Asserted, not assumed.
- Every percent field round-trips: save 6.25 → stored 0.0625 → redisplay
  6.25, for all eight, and `min_dscr` round-trips as 1.25 through the
  same path.
- A debt override saved from the form resolves to the terms the run
  stamps — assert `stamped["assumptions"]["debt_terms"]` equals what the
  form wrote, resolved.
- Unexposed keys (`index_rate`, `spread`, `loan_type`, `accrual_base`,
  `am_fee_treatment`, `catch_up`) survive an unrelated save.
- A form set to values that would raise (`amort_years=0`, `rate=650`)
  produces a form error, never a 500 and never a saved override that
  fails at run time.
- `loan_matures_before_exit` fires with `term_years=3` on a 5-year hold.
- The results block renders with no levered payload (old run), with a
  `None` LP IRR (non-converging), and with a zero loan.
- The memo and the workbook both build with `levered=None`.
- The assumption stamp appears on every surface that shows an LP net
  IRR — page, memo and workbook. A test per surface.
- Both UI passes run and are recorded in the PR.
- All four local gates: `python -m pytest tests/`,
  `python manage.py check`,
  `python manage.py makemigrations --check --dry-run`,
  `python manage.py collectstatic --noinput`.
