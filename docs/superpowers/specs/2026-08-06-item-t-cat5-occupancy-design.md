# Item T Category 5 — occupancy: one required input, one registered vocabulary

**Date:** 2026-08-06
**Backlog:** `docs/scoped-backlog.md` §T scope clause 5
**Tier:** standard, with a live behaviour change (deals that ran before will
now be refused). Not a pure literal move — the PR carries a measurement.

## Why

Scope clause 5 asks for "stabilized occupancy, decided once". The audit that
wrote it saw one problem. There are two, and they need opposite treatments.

### Pile A — one question with three answers

*"What physical occupancy do we assume when the CIM states none?"*

| constant | value | what it drives |
|---|---|---|
| `model.value_add_model.VA_DEFAULT_OCCUPANCY` | 0.80 | where the lease-up ramp starts |
| `model.value_add_model.VA_EGR_ASSUMED_OCCUPANCY` | 0.85 | backing in-place rent out of EGR |
| `config.XLSM_TEMPLATE_INPUTS["assumed_physical_occupancy"]` | 0.90 | the workbook |

The first two fire **in the same run, on the same deal**. On a CIM with no
stated occupancy and no unit mix, the model simultaneously assumes the asset
is 80% full and 85% full.

Measured on the `value_add` fixture with occupancy blanked and the deal forced
onto the EGR route (probe: occupancy, the analyst rent override, the unit mix
and GPR all removed, since `_compute_in_place_rent_psf` tries those first):

| pair | implied in-place | base IRR |
|---|---|---|
| **shipped** (0.80 ramp / 0.85 EGR) | $1.2274 | **0.3417%** |
| both 0.80 | $1.3041 | 0.4837% |
| both 0.85 | $1.2274 | 0.4812% |
| both 0.88 | $1.1855 | 0.4823% |
| both 0.90 | $1.1592 | 0.4841% |
| widest mismatch (0.80 ramp / 0.90 EGR) | $1.1592 | 0.2160% |
| reversed mismatch (0.90 ramp / 0.85 EGR) | $1.2274 | 0.6214% |

**When the two constants agree, which number you pick barely matters — a 3 bp
spread across 0.80–0.90. When they disagree it costs 14–27 bps.** The
disagreement is worth roughly five times the choice, because the EGR constant
sets implied in-place rent (higher assumed occupancy → lower implied rent)
while the ramp constant sets where lease-up begins; agreeing makes revenue
self-consistent, disagreeing double-counts the same vacancy.

Isolating the ramp constant on an otherwise intact deal (only the stated
occupancy blanked, so the unit mix still supplies in-place rent and the EGR
constant never fires) shows the wider spread, against a control of 0.3510%
when occupancy is actually stated at 91%:

| ramp starts | base IRR |
|---|---|
| 0.80 | 0.0706% |
| 0.85 | 0.1978% |
| 0.88 | 0.2743% |
| 0.90 | 0.3254% |
| *(occupancy stated at 91%)* | *0.3510%* |

**What the gate does today, which is why the refusal belongs upstream of it.**
`analysis/filters.py` reads `if occ is None: "TBD"`, then `elif occ < floor:
"FAIL"`. So a missing occupancy does not fail the demand gate — it renders as
TBD and the deal proceeds through the rest of the model on a fabricated
occupancy. A stated `0.0` *does* fail it, correctly, as "unproven demand —
physical occupancy below 75% floor". Absence and zero already take different
paths here, and decision 3 keeps them different.

**One thing the measurement refuted, recorded so nobody re-derives it.** The
assumed occupancy sets the in-place rent that the rent-gap trigger is measured
against, so it looks like it should be able to flip a deal into the value-add
classification. It cannot: `detect_value_add` gates its rent-gap branch on
`cim_data.unit_mix`, and the EGR route only fires when there is no unit mix.
The two can never interact. Measured across 0.80/0.85/0.88/0.90, the
classification never moved.

### Pile B — a family of different questions sharing a word

`GATES["stabilized_occupancy"]` 0.85 (the ramp test), `SCENARIO_DEFAULTS[*]
["stabilized_occ"]` 0.82/0.88/0.93, `VALUE_ADD_SCENARIOS[*]
["target_occupancy"]` 0.85/0.88/0.92, `VALUE_ADD_TRIGGERS["max_occupancy"]`
0.85, `VALUE_ADD_ASSUMPTIONS["occupancy_target"]` 0.93 and `["ecri_min_
occupancy"]` 0.88, and `market.py`'s bare 0.90/0.85 narrative tiers.

These are the same shape as the three building-age ladders, which the operator
chose to **register rather than reconcile** once the snap was measured (item T
Category 2, 2026-08-05). Collapsing them is re-underwriting, which item T's own
scope excludes.

## Decisions

**1. There is no assumed occupancy. It is a required input.** Operator's call,
2026-08-06, taken against the measurement above in preference to both offered
numbers. Physical occupancy joins NRSF and TTM NOI as an input no default can
honestly stand in for. This deletes Pile A rather than reconciling it.

**2. The XLSM's 0.90 stays for E3b.** Operator's call, same date.
`output/template_writer.py` is explicitly out of item T's scope. But
`value_add_model.py`'s docstring currently tells the reader "Category 5 must
decide all three" — that note is rewritten to record that Category 5 decided
*no assumption*, so E3b inherits a decision instead of an open question.

**3. Occupancy is tested with `is None`, not falsy — a deliberate divergence
from the other two required fields.** `require_underwritable` uses a falsy
check because `extract.parser._parse_number` returns `0.0` when it cannot read
a figure, so an unparseable NRSF arrives as zero and would otherwise sail into
every division. Occupancy is different, and the repo already made this call at
`model/value_add_model.py:434`: *"a stated 0% physical occupancy is an
honestly-reported pre-lease-up asset."* A genuinely 0%-occupied deal should be
refused by the 75% demand gate, with that as the stated reason — not by the
input check, with the wrong one. Both paths refuse the deal; only the message
differs, and only one of them is true. This requires `require_underwritable` to
carry a per-field predicate rather than one blanket `not`.

**4. The `if occ:` guards in `filters.py`, `risks.py`, `market.py`,
`financials.py` and `value_add.py` stay.** They become unreachable through the
engine, but `require_underwritable` runs at the engine boundary, not inside
`CIMData` — and tests, the CLI's manual-fill path and any direct construction
still build the dataclass by hand. Deleting them would trade a live safety net
for tidiness.

## Scope

### 5a — occupancy becomes required

- `analysis/fills.py`: `REQUIRED_UNDERWRITING_FIELDS` gains physical occupancy;
  the check grows a per-field predicate (decision 3). The refusal message names
  the Assumptions page, as the existing one does.
- `model/value_add_model.py`: delete `VA_DEFAULT_OCCUPANCY`,
  `VA_EGR_ASSUMED_OCCUPANCY`, both fallback branches, and the two `Fill`
  records they emit. Rewrite the Category-5 docstring note per decision 2.
- `analysis/fills.py`: retire the `OCCUPANCY_ABSENT` and
  `EGR_OCCUPANCY_ASSUMED` source keys **if and only if** nothing else emits
  them — verify by grep, do not assume.
- The **catalogue** of substitutions the model can log drops from twelve to ten
  (a per-run count was always the length of that run's `collect()`, and no
  fixture emits either of these two). Every surface reads
  the same `collect()`, so this is one change, not five: memo Appendix A and
  its section-1 count sentence, the Summary-tab panel, the workbook Inputs
  block, the LP summary footer, the CLI terminal summary.
- `tests/test_config_single_source.py:2471-2473` currently asserts both
  constants exist and differ. It inverts: neither exists, and occupancy is
  required.

### 5b — the family gets a vocabulary

- `analysis/market.py:125,127`: the bare 0.90/0.85 narrative tiers move to a
  config block, following the `POPULATION_TIERS` shape from PR #42. These are
  the last un-configured occupancy literals.
- One config doc block naming every occupancy key, the question it answers, and
  why it differs from its neighbours — the `ASSET_AGE_LADDERS` treatment.
- An AST guard test: no bare occupancy literal in `analysis/`, `model/` or
  `output/`, mirroring `test_no_age_threshold_survives_as_a_bare_literal`, so a
  fourth one cannot appear quietly.

### Out of scope

`output/template_writer.py` and its 0.90 (item E3b). Re-underwriting any
Pile B value. Economic occupancy, which has its own fallbacks and its own
question.

## Testing

- **Characterization:** all three fixtures state occupancy (0.93 / 0.91 /
  0.88), so all three snapshots must reproduce **byte-for-byte**. A delta means
  something moved that should not have.
- **The refusal:** a fixture with `physical_occupancy=None` raises
  `MissingUnderwritingInput` from `engine.run_analysis`, and `run.stage_analyze`
  exits 2. A fixture with `physical_occupancy=0.0` does **not** raise — it
  reaches the demand gate and fails there, with the gate's reason. That pair is
  decision 3's red test and neither half is optional.
- **The deletion:** `grep` proves no module references either deleted constant.
- **Mutation, before trusting any of it.** Per the standing lesson: green is
  also what a dead wire looks like. Break the wiring on purpose — restore a
  fallback branch, revert the `is None` predicate to falsy, patch a moved
  `market.py` tier — and confirm each break fails the suite. The `is None`
  → falsy mutation is the one most likely to pass wrongly, because only the
  0.0 fixture distinguishes them.
- **No `git checkout --` in the mutation script.** Commit first, then mutate,
  then restore by re-applying the inverse edit.

## Risks

- **This refuses deals that previously ran.** That is the intent, but it is a
  behaviour change, not a literal move. The PR states it plainly rather than
  reporting a green suite.
- **The web path must stay usable.** `physical_occupancy` is already an
  assumptions-page field (`webapp/forms.py:229`), so a refused deal is fixable
  without code — verified before this spec was written. If that ever stops
  being true, this change traps deals.
- **The fill-log count is rendered in five places.** They all read one
  `collect()`, but the count sentence in memo section 1 and the LP summary
  footer are prose — check they read the length rather than restating twelve.
