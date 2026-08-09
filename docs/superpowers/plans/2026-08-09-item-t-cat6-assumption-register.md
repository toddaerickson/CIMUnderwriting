# Item T Category 6 — Assumption Register

**Goal:** Every number that moved an output in a run, its value, and its
provenance, assembled once and rendered where a reviewer can audit it.

**Backlog clause:** `docs/scoped-backlog.md` item T, scope item 6. Acceptance:
"The memo appendix lists every assumption its own run used, with provenance —
an IC reviewer can audit every number in one place."

**Spec + plan in one file.** Category 5 split them because its decision (delete
vs reconcile the occupancy constants) needed measurement before any code could
be planned. This category has no such open question: the provenance data already
exists and is already persisted, and the work is assembly and rendering.

---

## The finding

Item T's audit named two corrosive patterns. Categories 1–3 closed the
duplicated constant; Category 4 closed the silent fallback. What neither closed
is the **undisclosed provenance of a value that is not wrong**: `GATES.min_irr_5yr`
at 10% and `GATES.min_irr_5yr` at 8% both render as "10%" and "8%" with nothing
saying that one is the shipped default and the other is a dated `ConfigOverride`
row somebody wrote in June. The memo prints the number. It does not print who
chose it.

Every input needed to fix that is already computed and already stored:

| Provenance | Where it already lives |
|---|---|
| deal override | `Deal.assumption_overrides` — deltas only by construction (`forms.build_overrides`) |
| settings override | `services.resolve_config_overrides(asset_type, date)`, stamped as `applied_overrides["config"]` |
| CIM datum | `Deal.cim_json` — the pristine pre-analyst extraction |
| fallback | `analysis/fills.py`, item T Category 4 |
| config default | `config.py`, read live |

Nothing joins them. `applied_overrides` is a stamp an auditor can read *if they
query the database*; it reaches no document, no page and no workbook.

---

## Design decisions

### 1. The register is a second rendering, never a second computation

`analysis/assumptions.py` is the analogue of `analysis/checks.py` and
`analysis/fills.py`: assembled ONCE at the engine, handed to every surface. The
same rule and the same reason — three surfaces computing provenance
independently is how they come to disagree about a deal.

It **decides nothing and resolves nothing**. Every value it reports is read from
what the run already resolved: live `config` (which is patched in place for the
duration of the run, so a live read IS the effective value), or the resolved
dicts the engine already receives as parameters. A register that re-resolved
`hold_years` would be a second answer to a question the worker already answered,
which is the duplicated-constant defect wearing item T's own badge.

### 2. Provenance is a closed vocabulary with ONE winner per assumption

```
deal      entered on this deal's Assumptions page
settings  a dated ConfigOverride row
fallback  invented by the model — see Appendix A
cim       stated in the CIM
config    shipped model default
```

Precedence is the model's actual precedence: `deal` > `settings` > `config` for
config-shaped keys, `deal` > `cim` for extracted fields, `fallback` where a fill
fired. **One row per assumption**, carrying the winner. Printing both the
superseded and the applied value is how a reader ends up auditing a number the
engine never used — the exact defect `webapp/services.py` already guards against
when it pops `SOLVER_TARGET_IRR` out of `applied` on a per-deal override.

Each row optionally carries `was`: the value the winner displaced. That is what
makes an analyst correction auditable ("NRSF 60,000 — entered by analyst; the
CIM said 58,400") rather than merely disclosed.

### 3. Appendix B, beside Appendix A — not folded into it

The backlog says "auditable in one place", and Appendix B satisfies that on its
own: it contains every assumption INCLUDING the invented ones, which appear with
provenance `fallback`. Appendix A is the zoom-in, kept because it answers a
different question with a different failure axis — "what did the model invent?"
versus "what did the model use, and who chose it?". Folding nine invented numbers
into a hundred-and-forty-row table does not consolidate the disclosure, it
dilutes it.

This is the same argument `analysis/fills.py` already makes for not folding
fills into `checks`: forcing one register's vocabulary onto another's question
makes a shared term mean two things.

### 4. A table-shaped config key contributes its RESOLVED row, not its table

`config.MARKET_CAP_RATES` holds twelve cells. One of them priced this exit; the
other eleven moved nothing, and "every number that moved an output" excludes
them by its own wording. The register reports the resolved anchor the run
actually used, which `market_cap` already publishes with its class and age band.

The same reasoning does NOT apply to `SCENARIO_DEFAULTS`: all three scenarios
run, so all fifteen parameters move an output.

### 5. Ordering is audit-first, and the bulk goes to the back

Two tables in Appendix B. The first lists only rows whose provenance is `deal`,
`settings` or `fallback` — what is unusual about THIS run, typically ten to
twenty rows. The second is the full register grouped by subject with provenance
as a column. An IC reader who reads only the first table has read every number a
human chose for this deal; an auditor who reads the second has read everything.

Neither table filters anything out. A "defaults omitted for brevity" register
requires the reader to trust that omission means default, which is the same act
of faith the item exists to end.

### 6. The CLI discloses exactly what the web app does

The CLI has no `ConfigOverride` table and no Assumptions page, so its register is
`config` / `cim` / `fallback` only — correct, and it must be TESTED, because
Category 4 shipped precisely this defect: `run.stage_output` passed no fill log
to any writer, so the entire item's disclosure existed on one entry point and not
the other. Two orchestrations means every wire is run twice or one path hides
what the other shows.

### 7. No new tunable numbers

Same rule Category 4 held. The register reports; it introduces no threshold, no
default and no policy. The one judgment it encodes — WHICH keys move an output —
is a membership declaration, and it is CI-guarded (below), not asserted.

---

## Anti-drift guard

The register's failure mode is silent incompleteness: a config key added next
month moves an output and never appears, and the appendix still reads as
complete. So membership is enforced, not curated:

`test_every_settings_editable_key_is_in_the_register_or_declared_out` walks
`webapp.forms.override_key_registry()` (95 keys, derived live from config) and
fails unless each dotted key either produces a register row or appears in
`assumptions.NOT_IN_REGISTER` with a stated reason. A new key defaults to
FAILING, which is the only setting under which a completeness claim survives
contact with a codebase that keeps growing.

A second test covers the non-settings constants (`DEBT_TERMS`, `WATERFALL_TERMS`,
`AM_FEE_PCT`, `DEFAULT_HOLD_YEARS`, capital defaults, `SOLVER_BOUNDS`,
`SENSITIVITY_GRID`), which `override_key_registry()` deliberately does not list.

---

## Tasks

- [ ] **1. `analysis/assumptions.py`** — `Assumption` dataclass, provenance
      vocabulary, `to_dicts`/`from_dicts`, `format_value`, and `collect()`
      assembling from live config + resolved run values + the two delta dicts.
      Tests: precedence (one winner), the `was` field, JSON round-trip tolerant
      of an older row, the closed vocabulary.
- [ ] **2. Membership declaration + the two guards** — `NOT_IN_REGISTER` with
      reasons; the `override_key_registry()` sweep; the non-settings constant
      sweep. Both must fail on a deliberately removed row.
- [ ] **3. Engine + CLI wiring** — `config_deltas` / `deal_overrides` parameters
      on `run_analysis`; assembly beside the fill log in both `engine.py` and
      `run.stage_gates_and_risks`; `AnalysisResult.assumption_register` and
      `AnalysisContext.assumption_register`. Test: the CLI register carries
      `config`/`cim`/`fallback` and never `settings`/`deal`.
- [ ] **4. Worker wiring + persistence** — the worker passes the deltas it
      already resolved; `result_json["assumption_register"]`. Test runs the REAL
      worker and reads the REAL row (Category 4's audit finding 1 — a test that
      hand-builds `result_json` passes against a worker that drops the key).
- [ ] **5. Memo Appendix B** — the two tables, the headline counts, and the
      cross-reference to Appendix A. Test: a settings override reaches the
      appendix with `settings` provenance and its `was` value.
- [ ] **6. Results page + workbook** — an Assumptions panel on the Inputs tab and
      a register block in the workbook's Inputs sheet. Both required UI passes on
      the panel.
- [ ] **7. Docs** — `CLAUDE.md` design decision, `docs/scoped-backlog.md` Cat 6
      marked done (and Cat 3's stale "the rest of this category is untouched"
      corrected), `ROADMAP.md` checkbox sweep for A/B/D/E1/E2/E3.

## Global constraints

- **Worktree.** `/home/teric/CIMAnalyst/CIMUnderwriting/.claude/worktrees/item-t-cat6`,
  branch `item-t-cat6-assumptions-appendix`. `cd` does not persist between Bash
  calls — absolute paths and `git -C <worktree>` in every command that writes.
- **Interpreter.** `.venv/bin/python` inside the worktree. Baseline before any
  change: **1122 passed, 2 skipped**. Sanity-check the COUNT, not just the green.
- **Characterization.** All three snapshots must move in their `documents` key
  ONLY. Any scenario, IRR, gate, check or max-offer delta means the register
  changed a number, which it must not — it is a reporting layer.
- **Config reads at call time, and the idiom differs by type.** A SCALAR must be
  read `cfg.X`; a DICT is imported by name and read directly, because
  `webapp.services` mutates those dicts in place for the run. Both rules are
  already in force — see the import comment in `analysis/market.py`.
- **Per-path `git add`.** Never `git add -A` — this clone is shared.