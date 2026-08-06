# Item T Category 5 — Occupancy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Physical occupancy becomes an input the model refuses to invent, and
the remaining "stabilized occupancy" numbers become a registered vocabulary that
cannot grow a fourth member quietly.

**Architecture:** Two independent halves. (a) `analysis/fills.py` gains a
per-field missing-ness predicate so occupancy can be required on `is None` while
NRSF and TTM NOI stay required on falsy; the two assumed-occupancy constants in
`model/value_add_model.py` and their `Fill` records are deleted. (b) The three
remaining bare occupancy literals move into one `config.OCCUPANCY_TIERS` block
alongside a register of every occupancy key, guarded by an AST walk.

**Tech Stack:** Python 3, pytest, Django (webapp only), `ast` from the stdlib.

**Spec:** `docs/superpowers/specs/2026-08-06-item-t-cat5-occupancy-design.md`

## Global Constraints

- **Worktree.** All work happens in
  `/home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5` on branch
  `claude/item-t-cat5`. `cd` does NOT persist between Bash calls — use absolute
  paths and `git -C <worktree>` in every command that writes.
- **Python.** `/home/terickson/CIM_Analyst/.venv/bin/python` is the interpreter.
  Run tests from inside the worktree and **sanity-check the test COUNT**, not
  just the green — a run in the wrong tree looks like a passing run.
- **Characterization.** All three snapshots (`stabilized`, `value_add`, `thin`)
  must reproduce **byte-for-byte**. Every fixture states occupancy, so any delta
  means something moved that should not have. Enumerate and argue any delta in
  the PR, never silently re-baseline.
- **Config reads happen at call time.** Never `from config import X` for a
  scalar — that freezes it at import and defeats `ConfigOverride`. Use
  `import config as cfg` then `cfg.X`, matching the modules being edited.
- **Commit before mutating.** Never `git checkout --` in a mutation script; it
  discards unstaged implementation. Restore by re-applying the inverse edit.
- **Per-path `git add`.** Never `git add -A` — this clone is shared and a
  concurrent session's edits must never ride your commit.

---

### Task 1: Occupancy becomes a required input, and both fallbacks are deleted

Required-ness and the deletion ship in ONE commit deliberately. Splitting them
leaves an intermediate state where occupancy is required but the fallbacks still
exist — green, unreachable, and meaningless.

**Files:**
- Modify: `analysis/fills.py` (`REQUIRED_UNDERWRITING_FIELDS`,
  `require_underwritable`, the source-key vocabulary, `SOURCE_LABELS`)
- Modify: `model/value_add_model.py:19-20` (imports), `:31`, `:47`,
  `:439-447`, `:527-540`
- Test: `tests/test_config_single_source.py` (two existing tests invert)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `analysis.fills.REQUIRED_UNDERWRITING_FIELDS` becomes a tuple of
  `(label: str, attr: str, is_missing: Callable[[Any], bool])` 3-tuples, where
  `is_missing` returns `True` when the value is absent. Task 3's register test
  does not read it; nothing else downstream depends on the shape change.

- [ ] **Step 1: Write the failing test — the `0.0` / `None` pair**

This pair IS decision 3. Neither half is optional: together they pin that
absence and zero take different paths, and a mutation back to a blanket falsy
check must fail on the first half.

Add to `tests/test_config_single_source.py`:

```python
def test_absent_occupancy_is_refused_but_a_stated_zero_reaches_the_gate():
    """Decision 3 of the Category 5 spec, and the reason
    `require_underwritable` needed a per-field predicate.

    `extract.parser._parse_number` returns 0.0 when it cannot read a
    figure, which is why NRSF and TTM NOI are checked with a FALSY test.
    Occupancy is different: a stated 0% is an honestly-reported
    pre-lease-up asset, and `analysis/filters.py` already fails it
    correctly as "unproven demand — below the 75% floor". Refusing it as
    a MISSING input would give a true refusal the wrong reason.

    Absence is the ununderwritable case: `filters.py` renders `None` as
    a TBD gate, so before this change a deal with no occupancy sailed
    through the whole model on a fabricated one.
    """
    import pytest
    from analysis.fills import MissingUnderwritingInput, require_underwritable
    from analysis.filters import evaluate_gates

    cim = _thin_va_deal()

    cim.physical_occupancy = None
    with pytest.raises(MissingUnderwritingInput) as exc:
        require_underwritable(cim)
    assert "Physical Occupancy" in str(exc.value)

    # A stated 0% is data, not absence: the input check passes it and the
    # demand gate is what refuses it, naming the real reason. Gate 2 is
    # the unproven-demand gate; the dicts key on an INT `gate` and a
    # singular `note` (analysis/filters.py:126-131).
    cim.physical_occupancy = 0.0
    require_underwritable(cim)          # must NOT raise
    gate_2 = next(g for g in evaluate_gates(cim) if g["gate"] == 2)
    assert gate_2["result"] == "FAIL"
    assert "75%" in gate_2["note"]
```

- [ ] **Step 2: Run it and verify it fails**

```bash
cd /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 && \
  /home/terickson/CIM_Analyst/.venv/bin/python -m pytest \
  tests/test_config_single_source.py::test_absent_occupancy_is_refused_but_a_stated_zero_reaches_the_gate -v
```

Expected: FAIL — `MissingUnderwritingInput` is not raised, because occupancy is
not yet a required field.

- [ ] **Step 3: Add the per-field predicate in `analysis/fills.py`**

Replace `REQUIRED_UNDERWRITING_FIELDS` and `require_underwritable` (currently
`analysis/fills.py:152-175`) with:

```python
def _absent_or_zero(value) -> bool:
    """Missing when falsy.

    `extract.parser._parse_number` returns 0.0 when it cannot read a
    figure, AND that 0.0 counts as populated in the extraction report. An
    `is None` gate would wave a 0-SF property straight into every
    division this refuses.
    """
    return not value


def _absent_only(value) -> bool:
    """Missing only when absent — a stated zero is data.

    Occupancy diverges from the two fields above deliberately (Category 5
    decision 3). A 0% physical occupancy is an honestly-reported
    pre-lease-up asset, and `analysis/filters.py` already refuses it as
    unproven demand with that as the stated reason. Refusing it here
    instead would give a correct outcome the wrong explanation.
    """
    return value is None


#: The three inputs with no defensible fallback, each with the predicate
#: that decides whether THIS field is missing. NRSF divides every
#: benchmark in the expense analysis and multiplies every $/SF rate; TTM
#: NOI sets the solver's search bracket; the occupancy gain is what a
#: value-add deal's return IS. Substituting any of them does not make the
#: answer approximate, it makes it unrelated to the asset.
REQUIRED_UNDERWRITING_FIELDS = (
    ("NRSF", "nrsf", _absent_or_zero),
    ("TTM NOI", "ttm_noi", _absent_or_zero),
    ("Physical Occupancy", "physical_occupancy", _absent_only),
)


def require_underwritable(cim_data) -> None:
    """Raise `MissingUnderwritingInput` unless the deal can be priced.

    Each field carries its own missing-ness test rather than sharing one,
    because 0.0 means "unreadable" for NRSF and "empty building" for
    occupancy. See `_absent_or_zero` and `_absent_only`.
    """
    missing = [label for label, attr, is_missing in REQUIRED_UNDERWRITING_FIELDS
               if is_missing(getattr(cim_data, attr, None))]
    if not missing:
        return
    raise MissingUnderwritingInput(
        f"{' and '.join(missing)} missing from this deal, so it cannot be "
        f"underwritten — every $/SF benchmark divides by NRSF, the solver's "
        f"price bracket derives from TTM NOI, and an assumed occupancy "
        f"invents the very gain a value-add return is made of. Enter "
        f"{'it' if len(missing) == 1 else 'them'} on the Assumptions page "
        f"and re-run.")
```

- [ ] **Step 4: Run the new test — first half passes, second half may not yet**

```bash
cd /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 && \
  /home/terickson/CIM_Analyst/.venv/bin/python -m pytest \
  tests/test_config_single_source.py::test_absent_occupancy_is_refused_but_a_stated_zero_reaches_the_gate -v
```

Expected: PASS. If the gate half fails, fix the assertion to match
`evaluate_gates`' real shape — do not weaken it to `assert True`-style checks or
delete the gate half; it is what distinguishes this from a blanket falsy check.

- [ ] **Step 5: Delete both constants and both fallback branches**

In `model/value_add_model.py`:

1. Delete `VA_DEFAULT_OCCUPANCY = 0.80` and its `#:` comment block (`:29-31`).
2. Delete `VA_EGR_ASSUMED_OCCUPANCY = 0.85` and its `#:` comment block
   (`:33-47`), which is the block instructing the reader that "Category 5 must
   decide all three".
3. In its place, record the decision so E3b inherits it rather than re-opening
   it:

```python
#: Physical occupancy is NOT assumed here, and that is Category 5's
#: decision (2026-08-06), not an oversight. Two constants used to answer
#: this one question at different numbers — 0.80 where the lease-up ramp
#: starts, 0.85 where in-place rent is backed out of EGR — and both fired
#: in the SAME run, so a deal with no stated occupancy was underwritten
#: as 80% and 85% full at once. Measured: when the two agree the choice of
#: number is worth ~3bps of base IRR; when they disagree it is worth
#: 14-27. So the answer was neither number. `analysis.fills.
#: require_underwritable` refuses the deal and the analyst enters it.
#:
#: `config.XLSM_TEMPLATE_INPUTS["assumed_physical_occupancy"]` (0.90) is
#: the same field at a THIRD number and still stands, because
#: `output/template_writer.py` is item E3b's, not item T's. It inherits
#: this decision; it does not get to re-take it.
```

4. In `_resolve_va_inputs` (`:439-447`), replace the fallback branch with a
   refusal at the site that needs the value:

```python
    # No fallback (Category 5). Unreachable through `engine.run_analysis`
    # and `run.stage_analyze`, which both call `require_underwritable`
    # first — but this function is also called directly, and a `None`
    # here would otherwise surface as a TypeError deep inside the monthly
    # loop rather than as the refusal it actually is.
    current_occ = cim_data.physical_occupancy
    if current_occ is None:
        raise MissingUnderwritingInput(
            "Physical Occupancy missing, so the value-add engine has no "
            "starting point for the lease-up ramp. Enter it on the "
            "Assumptions page and re-run.")
```

5. In `_compute_in_place_rent_psf` (`:527-540`), drop the assumption entirely.
   The function's contract already returns `(0.0, None)` for "no figure", and a
   missing occupancy is now that case:

```python
    # No assumed occupancy (Category 5). A missing occupancy is refused
    # upstream; reaching here without one means a direct caller, and
    # "no figure" is what this function has always returned for an input
    # it cannot compute from.
    egr = cim_data.ttm_egr
    occ = cim_data.physical_occupancy
    if egr and occ:
        return egr / (nrsf * 12 * occ), None

    return 0.0, None
```

6. Fix the imports at `:19-20`: drop `EGR_OCCUPANCY_ASSUMED` and
   `OCCUPANCY_ABSENT`, add `MissingUnderwritingInput`. Keep the rest.

- [ ] **Step 6: Retire the two source keys**

In `analysis/fills.py`, delete `OCCUPANCY_ABSENT` and `EGR_OCCUPANCY_ASSUMED`
(`:75-76`) and their two `SOURCE_LABELS` rows (`:100-101`). `SOURCE_KEYS` is
derived from `SOURCE_LABELS`, so it follows automatically — do not edit it.

Before deleting, prove nothing else emits them:

```bash
cd /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 && \
  grep -rn "OCCUPANCY_ABSENT\|EGR_OCCUPANCY_ASSUMED" --include=*.py .
```

Expected after the edits: no hits outside the two inverted tests in Step 7. If
anything else references them, STOP and report — the spec's "if and only if
nothing else emits them" condition has failed and the vocabulary must keep the
key.

Also update the module docstring's "Two fallbacks are deleted rather than
logged" paragraph (`analysis/fills.py:20-26`) to say **three**, naming
occupancy and its `is None` divergence in one clause.

- [ ] **Step 7: Invert the two tests that pinned the old behaviour**

Replace `test_the_two_value_add_occupancy_defaults_are_still_declared_apart`
(`tests/test_config_single_source.py:2465-2474`) with:

```python
def test_neither_value_add_occupancy_default_survives():
    """Category 5 (2026-08-06) answered "which number?" with "none of
    them". Two constants answered ONE question — where the lease-up ramp
    starts, and what occupancy backs in-place rent out of EGR — at 0.80
    and 0.85, and both fired in the same run. Re-introducing either is
    re-introducing a number nobody chose for the deal being priced.
    """
    from model import value_add_model as vam

    assert not hasattr(vam, "VA_DEFAULT_OCCUPANCY")
    assert not hasattr(vam, "VA_EGR_ASSUMED_OCCUPANCY")
```

Then rewrite the fill-log assertions at `:2643-2653`. That test built a deal
with `physical_occupancy = None` and asserted both fills were logged; it now
asserts the refusal, and that a stated occupancy still resolves cleanly:

```python
    cim = _thin_va_deal()
    cim.unit_mix = []
    cim.ttm_gpr = None
    cim.ttm_egr = 430_000

    cim.physical_occupancy = None
    with pytest.raises(MissingUnderwritingInput):
        _resolve_va_inputs(cim, {})

    # stated occupancy: the rent is backed out of the real number and
    # neither occupancy fill exists to be logged any more
    cim.physical_occupancy = 0.70
    resolved = _resolve_va_inputs(cim, {})
    assert resolved.in_place_rent_psf == pytest.approx(
        430_000 / (45_000 * 12 * 0.70))
    assert all(f.field != "physical_occupancy" for f in resolved.fills)
```

Read the surrounding test's name and docstring first and update both to match
what it now proves — a docstring describing the deleted behaviour is worse than
no docstring.

- [ ] **Step 7b: Confirm no surface restates the substitution count as a number**

The catalogue of loggable substitutions drops from twelve to ten. Every surface
reads the same `collect()`, so a rendered count follows automatically — unless
someone wrote the number into prose. Two are prose and must be checked by eye:
the count sentence in memo section 1, and the LP summary footer.

```bash
cd /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 && \
  grep -rn "twelve\|Twelve\| 12 " output/ webapp/templates/ run.py | \
  grep -iv "month\|12)" | head
```

Expected: no hit that means "twelve assumption fills". If one exists, change it
to read `len(...)` off the log rather than to say "ten" — a hard-coded count is
the same defect at a new number.

Also confirm the refusal is actionable in the web app, which is what keeps this
change from trapping deals: `physical_occupancy` must still be an editable field
on the assumptions page.

```bash
cd /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 && \
  grep -n "physical_occupancy" webapp/forms.py | head -5
```

Expected: it appears in `CIM_PCT_FIELDS` and in the field label list
(`webapp/forms.py:85` and `:229`). If it does not, STOP — the refusal would have
no remedy and the spec's stated risk has materialised.

- [ ] **Step 8: Run the full suite and the characterization snapshots**

```bash
cd /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 && \
  /home/terickson/CIM_Analyst/.venv/bin/python -m pytest tests/ -q 2>&1 | tail -15
```

Expected: all pass. Note the count and compare it to `main`'s — a count that
DROPS by more than the tests you deliberately removed means a collection error
is hiding failures.

Then confirm byte-for-byte reproduction explicitly:

```bash
cd /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 && \
  /home/terickson/CIM_Analyst/.venv/bin/python -m pytest tests/test_characterization.py -q 2>&1 | tail -5 && \
  git -C /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 status --short tests/snapshots/
```

Expected: green, and `git status` on `tests/snapshots/` reports **nothing**. A
modified snapshot here is a real finding — stop and enumerate the delta rather
than committing it.

- [ ] **Step 9: Commit**

```bash
git -C /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 add \
  analysis/fills.py model/value_add_model.py tests/test_config_single_source.py
git -C /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 commit -q -m "$(cat <<'EOF'
feat(model): occupancy is a required input, not an assumption (item T Cat 5)

Two constants answered ONE question at different numbers and both fired in
the same run: a deal with no stated occupancy was underwritten as 80% full
(the lease-up start) and 85% full (backing rent out of EGR) at once.

Measured: when the two agree the choice of number is worth ~3bps of base
IRR; when they disagree, 14-27. So the answer is neither number — occupancy
joins NRSF and TTM NOI in require_underwritable.

Occupancy is checked with `is None`, not falsy, unlike the other two. A
stated 0% is an honestly-reported pre-lease-up asset and the 75% demand
gate already refuses it with the right reason; only ABSENCE is
ununderwritable, and absence used to render as a TBD gate and proceed.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: The three narrative occupancy literals move to config

The spec named two. A grep during planning found a third — `analysis/risks.py:263`
`occ > 0.95` ("over-occupied — potential rate suppression"). All three grade how
an occupancy READS; none of them screens a deal. They are the `POPULATION_TIERS`
shape from PR #42, and they get the same treatment.

**Files:**
- Modify: `config.py` (new `OCCUPANCY_TIERS` block, sited next to
  `POPULATION_TIERS`)
- Modify: `analysis/market.py:123-130`
- Modify: `analysis/risks.py:263`
- Modify: `webapp/services.py` (`_PATCHED_DICTS`)
- Test: `tests/test_config_single_source.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `config.OCCUPANCY_TIERS`, a dict with exactly the keys
  `"over_occupied"` (0.95), `"strong"` (0.90), `"healthy"` (0.85), all floats in
  (0, 1). Task 3's register lists it by name.

- [ ] **Step 1: Write the failing test — and prove WHICH constant is read**

`market.py`'s healthy tier is 0.85 and `GATES["stabilized_occupancy"]` is also
0.85. A test that just asserts "0.85 behaviour" passes against both, which is
the coincidence-not-coverage trap that has now bitten this repo four times. So
the test MOVES the tier and asserts the narrative follows it, while the gate
stays put.

**`monkeypatch.setitem` on the live dict, never `setattr` on the module.**
`analysis/market.py` reads `from config import GATES, POPULATION_TIERS` and says
why in a comment: the dicts are *mutated in place* by the per-run override patch,
so a module-level binding still sees a `ConfigOverride`. Rebinding
`config.OCCUPANCY_TIERS` would leave `market.py` holding the original dict and
the test would prove nothing. `setitem` is also exactly what
`webapp.services._merge_patch` does to a real override row.

```python
def test_occupancy_narrative_tiers_are_config_not_the_stabilization_gate(
        monkeypatch):
    """`OCCUPANCY_TIERS["healthy"]` and `GATES["stabilized_occupancy"]`
    are both 0.85 today and they are NOT the same number: one grades how
    an occupancy reads in the memo's demand narrative, the other decides
    whether a post-2020 vintage has ever stabilized.

    Asserting 0.85 behaviour would pass against either. So move the tier
    and leave the gate alone — only code reading the tier follows.
    """
    import config as cfg
    from analysis.market import _assess_demand

    cim = _thin_va_deal()
    cim.physical_occupancy = 0.87

    baseline = _assess_demand(cim)
    assert any("Healthy occupancy" in p for p in baseline["positives"])

    monkeypatch.setitem(cfg.OCCUPANCY_TIERS, "healthy", 0.88)
    moved = _assess_demand(cim)

    # 0.87 read as "healthy" at a 0.85 floor and is below it at 0.88
    assert not any("Healthy occupancy" in p for p in moved["positives"])
    assert any("stabilized threshold" in n for n in moved["negatives"])
    # the GATE did not move
    assert cfg.GATES["stabilized_occupancy"] == 0.85
```

`_assess_demand` returns `{"positives": [...], "negatives": [...]}`
(`analysis/market.py:167-170`), so those two keys are correct as written.

- [ ] **Step 2: Run it and verify it fails**

```bash
cd /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 && \
  /home/terickson/CIM_Analyst/.venv/bin/python -m pytest \
  tests/test_config_single_source.py::test_occupancy_narrative_tiers_are_config_not_the_stabilization_gate -v
```

Expected: FAIL with `AttributeError: module 'config' has no attribute
'OCCUPANCY_TIERS'`.

- [ ] **Step 3: Add the config block**

In `config.py`, immediately after the `POPULATION_TIERS` block:

```python
# ── Occupancy narrative tiers ───────────────────────────────────────
#
# NARRATIVE tiers, not screens — the same lane as POPULATION_TIERS above.
# `GATES["min_physical_occupancy"]` is the only occupancy number that
# passes or fails a deal; these three grade how an occupancy READS in the
# memo's demand narrative and risk list.
#
# `healthy` (0.85) EQUALS `GATES["stabilized_occupancy"]` today and is
# deliberately a separate key: one asks "does this read as stable demand?"
# and the other asks "has this post-2020 vintage ever stabilized?".
# Collapsing them would tie the memo's prose to a screening threshold, so
# that tuning the narrative silently re-screens deals.
# `test_occupancy_narrative_tiers_are_config_not_the_stabilization_gate`
# is what stops that.
#
# Ordering invariant: over_occupied >= strong >= healthy. These are
# settings-editable, so two independently valid edits can invert the pair
# and produce a band no occupancy can land in — the same composed-value
# hole `registry.EXPENSE_RATIO_LIMITS` closed for the expense clamp.
# `test_the_occupancy_tiers_stay_ordered` is the guard.
OCCUPANCY_TIERS = {
    "over_occupied": 0.95,   # above → rate suppression risk (rents too low)
    "strong":        0.90,   # at/above → "demand exceeds supply"
    "healthy":       0.85,   # at/above → "stable demand"
}
```

- [ ] **Step 4: Point the three call sites at it**

Both modules import config dicts BY NAME (`from config import GATES,
POPULATION_TIERS`) precisely so in-place override patching reaches them. Extend
those existing import lines with `OCCUPANCY_TIERS`; do NOT switch either module
to `import config as cfg`, which would break the patching contract the comment
above `market.py`'s import describes.

`analysis/market.py:123-130` — and make the prose an f-string over the value so
the label cannot drift from the threshold (the Category 1 convention):

```python
    occ = cim_data.physical_occupancy
    if occ:
        if occ >= OCCUPANCY_TIERS["strong"]:
            positives.append(f"Strong occupancy at {occ:.1%} — demand exceeds supply.")
        elif occ >= OCCUPANCY_TIERS["healthy"]:
            positives.append(f"Healthy occupancy at {occ:.1%} — stable demand.")
        else:
            negatives.append(f"Occupancy at {occ:.1%} — below the "
                             f"{OCCUPANCY_TIERS['healthy']:.0%} stabilized "
                             f"threshold.")
```

Note `if occ:` is preserved. Spec decision 4 keeps these guards: they become
unreachable through the engine, but tests and the CLI's manual-fill path build
`CIMData` directly and `require_underwritable` sits at the engine boundary.

`analysis/risks.py:263`:

```python
    elif occ and occ > OCCUPANCY_TIERS["over_occupied"]:
```

- [ ] **Step 5: Make the tiers settings-editable**

`POPULATION_TIERS` is a `_PATCHED_DICTS` entry (PR #42) and these are its twin.
Add `"OCCUPANCY_TIERS"` to `webapp/services._PATCHED_DICTS` beside it.

Bounds are DERIVED from each key's shape by `webapp.forms._bounds_for`, so three
decimal rates inherit `(0, 1)` with no hand-written entries — that is #45's
design paying off. Verify rather than assume:

```bash
cd /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 && \
  /home/terickson/CIM_Analyst/.venv/bin/python -m pytest \
  tests/test_config_single_source.py -q -k "bounds or registry_kind" 2>&1 | tail -5
```

Expected: PASS, including `test_every_default_sits_inside_its_own_bounds` and
`test_every_registry_kind_matches_the_shape_of_its_config_value`, both of which
read config live and will catch a mis-shaped new key immediately.

- [ ] **Step 6: Write the ordering guard**

```python
def test_the_occupancy_tiers_stay_ordered():
    """Three independently editable settings that must stay ordered.
    Per-field bounds cannot see this: 0.95/0.90/0.85 each sit inside
    (0, 1) individually, and so do 0.85/0.90/0.95 — inverted, which
    leaves `strong` unreachable and every occupancy reading as
    "healthy" at best. Same composed-value hole the expense-ratio clamp
    took three audit rounds to close.
    """
    import config as cfg

    t = cfg.OCCUPANCY_TIERS
    assert t["over_occupied"] >= t["strong"] >= t["healthy"], (
        f"occupancy tiers out of order: {t}")
```

If the codebase enforces such invariants at resolution time rather than by test
(check how `registry.EXPENSE_RATIO_LIMITS` does it), follow that pattern instead
and keep this test as its red gate.

- [ ] **Step 7: Run the suite and confirm snapshots are untouched**

```bash
cd /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 && \
  /home/terickson/CIM_Analyst/.venv/bin/python -m pytest tests/ -q 2>&1 | tail -8 && \
  git -C /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 status --short tests/snapshots/
```

Expected: green, and NO snapshot modifications. Every value equals the literal
it replaced, so this task must move no number at all.

- [ ] **Step 8: Commit**

```bash
git -C /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 add \
  config.py analysis/market.py analysis/risks.py webapp/services.py \
  tests/test_config_single_source.py
git -C /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 commit -q -m "$(cat <<'EOF'
refactor(config): the occupancy narrative tiers get a home (item T Cat 5)

market.py's 0.90/0.85 and risks.py's 0.95 were the last bare occupancy
literals. All three grade how an occupancy READS; none screens a deal, so
they follow POPULATION_TIERS into config and onto the settings page.

`healthy` equals GATES["stabilized_occupancy"] and stays a separate key on
purpose — one grades prose, the other screens a vintage, and a test moves
the tier while pinning the gate so nothing can pass against both.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: The occupancy register and its AST guard

**Files:**
- Modify: `config.py` (an `OCCUPANCY_KEYS` register, beside `ASSET_AGE_LADDERS`)
- Test: `tests/test_config_single_source.py`

**Interfaces:**
- Consumes: `config.OCCUPANCY_TIERS` from Task 2.
- Produces: `config.OCCUPANCY_KEYS`, a tuple of dotted/subscript strings naming
  every occupancy number in the repo. Nothing imports it at runtime; it exists
  to be asserted against.

- [ ] **Step 1: Write the AST guard, with its exemption**

The exemption is load-bearing and easy to miss: `analysis/value_add.py:77` reads
`impact = rev * (occ_delta / occ) if occ > 0 else 0`. That is a divide-by-zero
guard, not a threshold, and a guard that flags it will be weakened or deleted by
the next person rather than obeyed.

```python
def test_no_occupancy_threshold_survives_as_a_bare_literal():
    """`config.OCCUPANCY_KEYS` is only worth having if it is complete.
    Category 5 decided these numbers stay APART (they answer different
    questions); a fourth one appearing quietly in some module is not a
    decision, it is the drift item T exists to stop.

    Walk `analysis/`, `model/` and `output/` and fail on any comparison of
    an occupancy against a numeric literal.

    EXEMPT: comparison against 0. `value_add.py` reads `occ > 0` as a
    divide-by-zero guard before `occ_delta / occ`, and `checks.py` bounds
    occupancy against 0 and 1 for validity. Neither invents a threshold,
    and a guard that flags them gets deleted rather than obeyed.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    offenders = []

    OCC_NAMES = {"occ", "current_occ", "phys", "econ",
                 "physical_occupancy", "economic_occupancy", "occupancy"}

    def is_occ(node):
        return ((isinstance(node, ast.Name) and node.id in OCC_NAMES)
                or (isinstance(node, ast.Attribute)
                    and node.attr in ("physical_occupancy",
                                      "economic_occupancy")))

    def is_threshold_num(node):
        """A bare number that is not 0 or 1 — see the exemption above."""
        return (isinstance(node, ast.Constant)
                and isinstance(node.value, (int, float))
                and not isinstance(node.value, bool)
                and node.value not in (0, 1))

    for pkg in ("analysis", "model", "output"):
        for path in sorted((root / pkg).glob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Compare):
                    continue
                left_occ = is_occ(node.left)
                right_occ = any(is_occ(c) for c in node.comparators)
                hit = ((left_occ and any(is_threshold_num(c)
                                         for c in node.comparators))
                       or (right_occ and is_threshold_num(node.left)))
                if hit:
                    offenders.append(
                        f"{path.relative_to(root)}:{node.lineno} — "
                        f"{ast.unparse(node)}")

    assert not offenders, (
        "occupancy compared to a bare literal; declare the threshold in "
        "config.py and list it in OCCUPANCY_KEYS:\n  "
        + "\n  ".join(offenders))
```

- [ ] **Step 2: Run it and confirm it passes ONLY because Task 2 landed**

```bash
cd /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 && \
  /home/terickson/CIM_Analyst/.venv/bin/python -m pytest \
  tests/test_config_single_source.py::test_no_occupancy_threshold_survives_as_a_bare_literal -v
```

Expected: PASS. **Then prove it can fail** — temporarily re-insert
`if occ >= 0.90:` into `analysis/market.py`, re-run, confirm FAIL with that line
named, and revert by re-applying the inverse edit (never `git checkout --`). A
guard nobody has watched fail is not a guard.

- [ ] **Step 3: Add the register**

In `config.py`, beside `ASSET_AGE_LADDERS`:

```python
# ── The occupancy register (item T Category 5) ──────────────────────
# Every occupancy number in the model, and the question each answers.
# They are deliberately NOT one number: Category 5 registered them the
# way Category 2 registered the three age ladders, because collapsing
# them is re-underwriting and item T's scope excludes that.
#
#   GATES["min_physical_occupancy"]              is demand proven at all?
#   GATES["stabilized_occupancy"]                has a post-2020 vintage
#                                                ever stabilized?
#   OCCUPANCY_TIERS                              how does this occupancy
#                                                READ? (narrative only)
#   SCENARIO_DEFAULTS[*]["stabilized_occ"]       what the static DCF
#                                                assumes per scenario
#   VALUE_ADD_TRIGGERS["max_occupancy"]          below this the deal is
#                                                a value-add deal
#   VALUE_ADD_SCENARIOS[*]["target_occupancy"]   where the lease-up
#                                                engine ramps TO
#   VALUE_ADD_ASSUMPTIONS["occupancy_target"]    what a well-run asset
#                                                reaches (opportunity
#                                                sizing, not the engine)
#   VALUE_ADD_ASSUMPTIONS["ecri_min_occupancy"]  full enough to push
#                                                rents without bleeding
#   XLSM_TEMPLATE_INPUTS["assumed_physical_occupancy"]
#                                                the workbook's fallback
#                                                — item E3b's, and the
#                                                LAST assumed occupancy
#                                                left anywhere
#
# There is no "assumed occupancy" in the Python model and that is
# deliberate: see `model/value_add_model.py`'s Category 5 note.
OCCUPANCY_KEYS = (
    'GATES["min_physical_occupancy"]',
    'GATES["stabilized_occupancy"]',
    "OCCUPANCY_TIERS",
    'SCENARIO_DEFAULTS[*]["stabilized_occ"]',
    'VALUE_ADD_TRIGGERS["max_occupancy"]',
    'VALUE_ADD_SCENARIOS[*]["target_occupancy"]',
    'VALUE_ADD_ASSUMPTIONS["occupancy_target"]',
    'VALUE_ADD_ASSUMPTIONS["ecri_min_occupancy"]',
    'XLSM_TEMPLATE_INPUTS["assumed_physical_occupancy"]',
)
```

- [ ] **Step 4: Write the register-completeness test**

Mirror `test_the_age_register_names_every_ladder_that_exists` — read its body
first (`grep -n "def test_the_age_register_names_every_ladder_that_exists" -A 30
tests/test_config_single_source.py`) and follow its approach, so the two
registers are policed the same way:

```python
def test_the_occupancy_register_names_every_occupancy_key_that_exists():
    """A register that silently omits a key is worse than none — it reads
    as completeness. Every config key holding an occupancy must appear in
    OCCUPANCY_KEYS.
    """
    import config as cfg

    named = set(cfg.OCCUPANCY_KEYS)
    missing = []

    for key in ("min_physical_occupancy", "stabilized_occupancy"):
        if f'GATES["{key}"]' not in named:
            missing.append(f'GATES["{key}"]')
    if "OCCUPANCY_TIERS" not in named:
        missing.append("OCCUPANCY_TIERS")
    for dict_name, inner in (
            ("SCENARIO_DEFAULTS", "stabilized_occ"),
            ("VALUE_ADD_SCENARIOS", "target_occupancy")):
        for params in getattr(cfg, dict_name).values():
            if inner in params and f'{dict_name}[*]["{inner}"]' not in named:
                missing.append(f'{dict_name}[*]["{inner}"]')
                break
    for dict_name, inner in (
            ("VALUE_ADD_TRIGGERS", "max_occupancy"),
            ("VALUE_ADD_ASSUMPTIONS", "occupancy_target"),
            ("VALUE_ADD_ASSUMPTIONS", "ecri_min_occupancy"),
            ("XLSM_TEMPLATE_INPUTS", "assumed_physical_occupancy")):
        if inner in getattr(cfg, dict_name) and \
                f'{dict_name}["{inner}"]' not in named:
            missing.append(f'{dict_name}["{inner}"]')

    assert not missing, f"occupancy keys absent from OCCUPANCY_KEYS: {missing}"
```

- [ ] **Step 5: Run the suite**

```bash
cd /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 && \
  /home/terickson/CIM_Analyst/.venv/bin/python -m pytest tests/ -q 2>&1 | tail -8
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
git -C /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 add \
  config.py tests/test_config_single_source.py
git -C /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 commit -q -m "$(cat <<'EOF'
test(config): register every occupancy number, guard against a tenth

Category 5 registered the occupancy family the way Category 2 registered
the three age ladders: they answer different questions and collapsing them
is re-underwriting, which item T's scope excludes.

The AST guard exempts comparison against 0 and 1 — value_add.py reads
`occ > 0` as a divide-by-zero guard and checks.py bounds occupancy for
validity. A guard that flags those gets deleted rather than obeyed.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Mutation proof

Green is also what a dead wire looks like. This task writes no feature code; it
breaks each wire on purpose and confirms the suite notices. Two of the four
mutations below have a real chance of passing wrongly.

**Files:** none committed unless a mutation survives.

- [ ] **Step 1: Confirm the tree is committed**

```bash
git -C /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 status --short
```

Expected: clean. If not, commit first — mutating over unstaged work is how the
Category 3 branch lost a whole PR.

- [ ] **Step 2: Mutation A — the predicate reverts to falsy (HIGHEST RISK)**

In `analysis/fills.py`, change occupancy's entry from `_absent_only` to
`_absent_or_zero`. Run:

```bash
cd /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 && \
  /home/terickson/CIM_Analyst/.venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: **FAIL**, on the `0.0` half of Task 1's test. This is the mutation
most likely to pass wrongly — only that one fixture distinguishes the two
predicates. If it passes, Task 1's test is not doing its job; fix the test
before continuing.

Restore by re-applying the inverse edit (`_absent_or_zero` → `_absent_only`).

- [ ] **Step 3: Mutation B — a fallback comes back**

In `model/value_add_model.py`, replace the `raise MissingUnderwritingInput` in
`_resolve_va_inputs` with `current_occ = 0.80`. Run the suite.

Expected: **FAIL**, on the inverted fill-log test from Task 1 Step 7.

Restore by re-applying the inverse edit.

- [ ] **Step 4: Mutation C — a tier is cross-wired to the gate**

In `analysis/market.py`, change `OCCUPANCY_TIERS["healthy"]` to
`GATES["stabilized_occupancy"]`. Both are 0.85, so every number stays identical
and the whole suite has every reason to stay green.

Expected: **FAIL**, on Task 2's test, which moves the tier and asserts the
narrative follows. If it PASSES, the test is measuring a coincidence and must be
rewritten before this PR ships — that is the exact defect this repo has hit four
times.

Restore by re-applying the inverse edit.

- [ ] **Step 5: Mutation D — the AST guard is blinded**

In `analysis/risks.py`, revert `OCCUPANCY_TIERS["over_occupied"]` to `0.95`.

Expected: **FAIL**, on Task 3's AST guard, naming `risks.py` and the line.

Restore by re-applying the inverse edit.

- [ ] **Step 6: Confirm the tree is clean and green**

```bash
cd /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 && \
  git -C /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 status --short && \
  /home/terickson/CIM_Analyst/.venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: no modified files, suite green. Record all four mutation results for
the PR body — "mutation-proven" without the four outcomes is an assertion, not
evidence.

---

### Task 5: Documentation

**Files:**
- Modify: `CLAUDE.md` (investment-criteria and design-decisions blocks)
- Modify: `docs/scoped-backlog.md` (§T scope clause 5)

- [ ] **Step 1: Record the decision in `CLAUDE.md`**

Add to the "Key design decisions" block, as a new numbered item:

```markdown
9. **Occupancy is never assumed.** Physical occupancy joins NRSF and TTM
   NOI in `analysis.fills.require_underwritable`: a CIM that does not
   state it is refused at `engine.run_analysis` and `run.stage_analyze`
   (CLI exit 2), and the analyst enters it on the Assumptions page. Two
   constants used to answer this question at 0.80 and 0.85 and both
   fired in the SAME run. Occupancy is tested with `is None`, NOT the
   falsy check the other two use — a stated 0% is an honestly-reported
   pre-lease-up asset and the 75% demand gate refuses it with the right
   reason. `config.XLSM_TEMPLATE_INPUTS["assumed_physical_occupancy"]`
   is the one surviving assumed occupancy and belongs to item E3b.
```

Also update the "Investment criteria" bullet on unproven demand to note that a
missing occupancy is now refused rather than rendered as a TBD gate.

- [ ] **Step 2: Mark scope clause 5 done in the backlog**

In `docs/scoped-backlog.md` §T, rewrite clause 5. It currently reads
"'stabilized' occupancy — 0.85 (gate) vs 0.88 (VA target/template) vs 0.90/0.93
(value_add targets) — and the mgmt-fee adjustment target (benchmark floor vs
5%)". Record that:

- the mgmt-fee half closed earlier, in PR #43 (`MGMT_FEE_TARGET_PCT` 0.06 with
  `resolve_mgmt_fee_target` as the one resolver);
- the occupancy half was REGISTERED, not reconciled — with the measurement that
  decided it;
- the assumed-occupancy fallbacks were deleted outright, which was not what the
  clause anticipated.

Follow the strikethrough-plus-note convention clause 3 already uses, so the
reversal stays visible rather than being edited out of history.

- [ ] **Step 3: Commit**

```bash
git -C /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 add \
  CLAUDE.md docs/scoped-backlog.md
git -C /home/terickson/CIM_Analyst/.claude/worktrees/item-t-cat5 commit -q -m "$(cat <<'EOF'
docs: record Category 5's decisions — no assumed occupancy, tiers registered

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Shipping

Standard tier per the spec, with one addition: this PR changes behaviour, so the
PR body must state plainly that deals which previously ran are now refused, and
carry the four mutation outcomes from Task 4 and the measurement from the spec.
A green suite is not the evidence here.

Sequence: push → open PR → `/code-review:code-review` → repair
critical/moderate findings → re-review only if any were found → CI green →
merge → verify the deploy on Render (`git_sha` at
`https://cim-analyst.onrender.com/health/` must equal the squash commit) →
delete the branch → remove the worktree.
