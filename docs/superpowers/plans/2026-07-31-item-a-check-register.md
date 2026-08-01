# Item A — Model error-check register (build plan)

Scope contract: [docs/scoped-backlog.md](../../scoped-backlog.md) § A. Build
order: [project memory](../../../CLAUDE.md) — A → B → D → E1-E4 → G. A goes
first because its checks guard B's arithmetic *while B rewrites it*.

This file is the build plan; the backlog is the scope contract. Where the two
disagree, the deviations are listed and justified below.

---

## Shape

One new pure module, `analysis/checks.py`. No Django import, so the identical
registry runs from the assumptions form, the live htmx preview, the engine,
the memo and the Excel writer. A check that lives in only one surface is a
check the other four silently skip — which is exactly how the Abilene CIM's
`$1` property-tax line reached a published model.

```
CheckInput      canonical-unit inputs (decimal percents, dollars)
CheckResult     (id, label, severity, status, message, values, source)
CHECKS          ordered registry of (id, label, severity, fn)
run_checks(inp) -> list[CheckResult]
```

Three statuses, not two: `pass` / `fail` / **`skipped`**. A check whose inputs
are absent must say so rather than render as a pass — the register is an
auditability surface, and "we never looked" and "we looked and it was fine"
are different claims.

Two severities, per the contract:
- **blocking** — form invalid unless the analyst accepts with the finding
  recorded (reuses the existing accept-with-discrepancy control; no second
  mechanism)
- **advisory** — always shown, never blocks

## The checks

| # | id | Severity | Fires when |
|---|----|----------|-----------|
| 1 | `income_identity` | blocking | \|Rev − Exp − NOI\| > max($1k, 1% Rev) |
| 2 | `unit_mix_sf` | **advisory** (deviation) | Σ(sf × count) vs NRSF off > 2% |
| 3 | `unit_mix_gpr` | advisory | Σ(count × rate × 12) vs GPR off > 3% |
| 4 | `occupancy_sanity` | blocking | phys/econ outside [0,1], or econ > phys |
| 5 | `egr_le_gpr` | blocking | EGR > GPR |
| 6 | `opex_ratio_band` | advisory | OpEx/Revenue outside 35–55% |
| 7 | `opex_per_nrsf_band` | advisory | Total OpEx outside $3.00–$5.50/NRSF |
| 8 | `expense_line_floor` | advisory, loud | any line zero/blank/below ½ its benchmark low |
| 9 | `exit_cap_coercion` | advisory | valuation silently raised exit cap to entry cap |
| 10 | `price_vs_replacement` | advisory | asking $/SF > replacement $/SF |

Check 11 (Sources & Uses ties to `total_basis`) lands with item D, which is
what creates the Sources & Uses block. Deferred: TTM annualization sanity —
needs a reporting-period field the parser does not extract.

### Deviations from the scope contract, and why

1. **Check 2 is advisory, not blocking.** The contract assigns it blocking.
   Partial unit-mix extraction is the *normal* state of this pipeline —
   CLAUDE.md's first design decision is "the parser extracts what it can and
   flags gaps." A parser that recovered 8 of 12 unit rows would hard-block the
   assumptions form on every such CIM, which makes the page unusable on a
   typical deal. It stays loud and always-visible; it just does not stop work.
   Checks 1, 4 and 5 stay blocking: each is a statement about the *same* three
   or two numbers that cannot both be true, with no legitimate reading.

2. **Check 6 compares OpEx to total revenue, not EGR.** The contract says
   "OpEx/EGR"; the band it names (`EXPENSE_BENCHMARKS["opex_revenue_ratio"]`,
   35–55%) is the OpEx/**Revenue** ratio, which is what
   `_expense_ratio_check` already computes and what CLAUDE.md's benchmark
   table calls it. Using the existing ratio means the check and the financials
   tab can never disagree; comparing a revenue-based band against an EGR-based
   ratio would have made the check wrong by the amount of other income.

## Accept-with-note, generalized

The existing control is one checkbox (`accept_noi_discrepancy`) that today
accepts exactly one finding. Blocking checks 4 and 5 need the same escape
hatch, and the contract is explicit: reuse it, do not invent a second one.

- Field name and the stored `noi_reconciliation` record are **unchanged** —
  the identity delta keeps its existing audit key and its existing tests.
- The control is relabelled to name what it accepts, and the blocking findings
  are listed above it so the analyst accepts specific statements, not a
  category.
- `build_overrides` additionally records `accepted_checks: [{id, message}]`
  for the full accepted set, so a run's `applied_overrides` says which
  integrity findings were waived and by what wording.

## Wiring, in build order

1. `analysis/checks.py` + `tests/test_checks.py` — registry first, tests
   alongside. `noi_recon_tolerance` moves here and is re-exported from
   `webapp/forms.py` so existing importers (`services.py`, `build_overrides`)
   keep working against a single definition.
2. `analysis/valuation.py` — record `requested_exit_cap` / `exit_cap_coerced`
   on each scenario. Additive keys only; the coercion behaviour itself does
   not change (that is item B's territory). Check 9 has no input without this.
3. `webapp/forms.py` — `clean()` builds a `CheckInput` and delegates. The
   duplicated identity one-liner between `clean()` and
   `model_strip_context` is retired here (dense-model-view T6 accepted-minor).
4. `webapp/services.py` + `_model_preview.html` — the live preview runs the
   *whole* registry against the merged preview CIM (which already carries unit
   mix and expense lines) and renders a compact check panel.
5. `engine.py` — run the registry after analysis, attach to `AnalysisResult`.
6. `webapp/results.py` + `_tab_summary.html`, `output/excel_writer.py`
   (a "Checks" sheet via the existing `create_sheet` pattern),
   `output/memo_writer.py` (a "Model Checks" block in section 1).

## Acceptance (from the contract)

- Every check has a passing case, a failing case, and a boundary case at its
  tolerance.
- The identity tests at `tests/test_web_deals.py:199` pass **unmodified** —
  that is the proof the migration preserved behaviour, including the
  `"off by $60,000"` wording.
- A run with a `$1` property-tax line produces check 8 in the memo, the Excel
  Checks sheet, and the results page.
- Blocking checks block the save; advisory checks never do.
- Full pytest suite green (no `verify.sh` in this repo).
