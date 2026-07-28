# Phase 5 — Comps Browser, Settings Editor, Render/Neon Cutover, gui/ Retirement: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the last two web pages (read-only Comps browser, delta-only Settings editor with asset-type-scoped + effective-dated `ConfigOverride` rows), harden the schema for Postgres, then cut production over from Streamlit-on-Railway to Django-on-Render (Neon Postgres + persistent disk) and retire `gui/`.

**Architecture:** Three sequential PRs. **PR 5A (standard tier, UI passes ×2 per new page):** `ConfigOverride` model + `AnalysisRun.applied_overrides` (migration 0004), a resolution service (asset-specific beats global, later effective-date beats earlier), a generalized in-place config patcher replacing `_patched_replacement_cost` (same `_ANALYSIS_LOCK` + restore-in-finally pattern, now covering all six user-tunable config dicts and fixing the legacy-alias gap), effective-config baselines threaded into the assumptions editor, and the two pages. **PR 5B (high-risk):** the pre-cutover checklist — `Deal.Meta.ordering` NULLs-last, `deal_id` widened to 200 (migration 0005), filename/state write hardening, the `build_deal_meta`↔`import_deals` round-trip drift guard, and a real-Postgres smoke script + CI job (postgres:16 service container). **PR 5C (high-risk):** `gui/engine.py` → root `engine.py`, `deal_manager` helpers absorbed into `webapp/services.py`, delete `gui/`/Streamlit/Docker/Railway artifacts, add `render.yaml`, rewrite docs. Cutover itself is a runbook executed after 5C merges; Railway keeps serving Streamlit until Render is verified, then the operator deletes the Railway service.

**Tech Stack:** Existing Phase 1–4 stack (Django 5.1.15, django-htmx, vendored htmx, compiled Tailwind 3.4.17, gunicorn/whitenoise/psycopg already pinned). No new dependencies in any of the three PRs; PR 5C removes `streamlit`.

## Global Constraints

- Analysis pipeline read-only: no edits to `analysis/`, `model/`, `output/`, `extract/`, `config.py`, `run.py`. The only pipeline-adjacent change is the Phase-5-sanctioned relocation `gui/engine.py` → `engine.py` (PR 5C, `git mv`, import updates only).
- Single source of truth: `ConfigOverride` stores **only deltas** from `config.py` — never copies of it. The editable-key registry is derived **live** from the config module at call time (no hardcoded key lists that can drift). The comps page reads `data/cim_comps.db` via the existing `CompDatabase` API; comp data is never duplicated into Django models.
- Operator directive (2026-07-25): overrides scoped optionally by `asset_type` and effective-dated, so thresholds can tighten/loosen over time and per property class while **past analyses keep the thresholds they ran under** (enforced by stamping resolved deltas onto each `AnalysisRun`).
- Percent convention: users type whole numbers (6 = 6%) everywhere, matching the Phase 3 assumptions editor; decimals are the storage/engine unit.
- All config patching is in-place mutation under `_ANALYSIS_LOCK`, restored in `finally` — never rebind a config dict to a new object (importers bound the objects at import time). The lock is per-process; two gunicorn workers may run two analyses concurrently in separate processes, which is safe because each process patches its own config module copy.
- Migrations are append-only: PR 5A ships 0004, PR 5B ships 0005; PRs merge in order.
- Every new page gets both UI passes with **two independent agents** (new pages); the settings/comps templates follow `deal_list.html`'s table/form idioms.
- Existing 132 tests stay green; `makemigrations --check` CI gate means each migration commits with its model change.
- Tailwind rebuilds carry `TAILWINDCSS_VERSION=v3.4.17` (local and in `render.yaml`'s buildCommand).
- Ship dark: nothing changes the deployed Streamlit app until the cutover runbook runs. Railway deploys are manual, so merging the retirement PR does not touch the running service.
- High-risk tier for 5B and 5C (migrations/DDL + deploy/cutover): full canonical cycle — implement → diff → code-review → repair → re-review until clean → push → PR → CI green + posted review → merge. Never downgraded. 5A is standard tier (one review pass, re-review only on critical/moderate findings).

## Design Decisions (locked)

1. **Three PRs, not one.** The master plan's roadmap said one PR per phase, but Phase 5 bundles two features + DDL + a production cutover. Splitting isolates the high-risk surface (5B/5C) from feature review noise (5A), keeps each diff reviewable, and follows the operator's risk-tiering. Deviation recorded here deliberately.
2. **Settings editor is override-row CRUD, not a mega-form.** The page lists `ConfigOverride` rows (add + delete), shows current effective values per group, and never edits `config.py`. To change a threshold going forward you **add a new row with a later effective date**; delete exists for mistakes only. Runs stamp their resolved deltas into `AnalysisRun.applied_overrides`, so history survives even if rows are later deleted.
3. **Resolution precedence:** asset-type-specific beats global **regardless of dates**; within the same specificity, later `effective_date` wins; ties break on higher `pk`. Rows with `effective_date` in the future are inert until due (shown as "scheduled"). Resolution happens in Python (DB-agnostic ordering), not via collation-dependent SQL ordering.
4. **Editable keys = the six user-tunable config dicts + `SOLVER_TARGET_IRR`.** `GATES`, `EXPENSE_BENCHMARKS`, `REPLACEMENT_COST` (canonical keys only), `SCENARIO_DEFAULTS.<scen>.<param>`, `VALUE_ADD_SCENARIOS.<scen>.<param>`, `VALUE_ADD_TRIGGERS`. Not editable: `SOLVER_TOLERANCE`/`SOLVER_MAX_ITERATIONS` (bisection internals, not investment criteria), `COMP_DB_*`, `CENSUS_API_KEY`, `TOP_50_MSAS`, `EXPENSE_REGIONS`, `STATE_PROPERTY_TAX_*`, `FACILITY_TYPES` (reference data, not thresholds; several are consumed via mechanisms in-place patching can't reach).
5. **Legacy RC aliases are excluded from the picker but auto-synced by the patcher.** `analysis/physical.py:151-154` reads `non_cc_per_sf`/`cc_per_sf` at call time for the replacement-cost breakdown. The Streamlit editor synced aliases; the Phase 4 per-deal RC path did **not** — a latent inconsistency. `_merge_patch` now syncs `ss_driveup_per_sf→non_cc_per_sf`, `ss_enclosed_per_sf→cc_per_sf`, `ss_driveup_site_per_sf→site_work_per_sf` whenever the canonical key is patched, fixing both the global and per-deal paths in one place.
6. **`SOLVER_TARGET_IRR` flows through the existing engine parameter** (import-time function-default binding makes patching unreachable — Phase 4 finding). Per-deal `assumption_overrides["solver_target_irr"]` wins over a global `ConfigOverride`. **Per-deal scenario sections win wholesale:** `build_overrides` stores `scenario_overrides`/`va_scenario_overrides` as FULL 3×6 sections, and the engine replaces defaults wholesale (`custom_scenarios or DEFAULTS`), so when a deal carries a per-deal scenario/VA section the worker DROPS the corresponding `SCENARIO_DEFAULTS.*`/`VALUE_ADD_SCENARIOS.*` global deltas from both the patch and the stamped `applied_overrides["config"]` — otherwise the solver (which reads the patched defaults internally) and the returns model (which receives the per-deal section) would run two different exit caps in one run, and the stamp would record deltas the scenario table never used (plan-review finding, adversary #1).
7. **The assumptions editor's baseline becomes the effective config** (deal's asset type, today) for both display and delta-detection. Otherwise a global override would make the form display stale defaults, and "unchanged" saves would silently pin values that differ from what actually runs. `services.effective_config()` returns deep copies — it never touches the config module.
8. **Comps page is read-only over `CompDatabase.get_comp_summary()`** (`list[dict]`, 11 keys). Filters (state, min NRSF) apply in Python — the table has single-digit rows and the summary query is already the API; no new SQL, no Django model, no second DB alias. CSV export serializes the same filtered rows (single source). *Review ruling:* the simplify critic proposed cutting CSV as out-of-scope; kept deliberately — the retired Streamlit comp page had CSV export, and dropping an existing capability silently is the anti-pattern (cost: ~15 lines + 1 test, vs batch analysis whose loss we record because replacing it would cost a page).
9. **Batch analysis retires with Streamlit, no web successor.** The Streamlit batch page was fully implemented (not a stub, contrary to the master plan's table). The web flow is one-deal-at-a-time upload → run; the operator underwrites deals individually. Recorded in ROADMAP's "Not Building" with a revisit trigger; must be named in the 5C PR body.
10. **One deploy path.** Dockerfile, docker-compose.yml, `deploy/streamlit_config.toml`, `railway.json`, and the CI docker job are deleted at 5C. Render's python runtime (buildCommand/startCommand in `render.yaml`) is the canonical deploy; CI gains the real build gates instead (collectstatic + PG smoke). Git history keeps the Docker files if ever needed.
11. **`render.yaml` mirrors managertools' proven shape** (starter plan, oregon, migrate in buildCommand, gunicorn `--workers 2 --threads 4`), plus what managertools doesn't have: a 1 GB persistent disk at `/data` (deal folders + comp DB + CIM overrides dir). `--timeout 120` (not 60): CIM PDF uploads can be tens of MB over home uplinks. No blueprint-validator script (no cron blocks here; first deploy is manually verified) — no-net-complexity.
12. **`deal_id` widens to 200** (live tree already has a 114/120-char slug; slugs derive from PDF filename stems). `AnalysisRun`/`Deal` filename fields get defensive `[:300]` truncation at the worker write; `import_deals` normalizes `state` to `[:2].upper()` with a warning when it truncates. Postgres fails hard on overflow where SQLite silently accepts — these must land before the Neon import (PR 5B, before 5C).
13. **Unknown override keys never crash a run — and never lie in the stamp.** If `config.py` evolves and an old row's key vanishes, `build_config_patch` logs a warning, skips it, and returns it in a `skipped` list; the worker stamps `applied_overrides = {"config": <applied deltas only>, "config_skipped": [...], "assumptions": ...}` so the run record asserts only thresholds the engine actually saw (a warning-level log in a daemon thread is not an audit trail). The settings page additionally badges such rows "unknown key".
14. **The health endpoint becomes disk-aware at cutover** (PR 5C): when `CIM_DEALS_DIR`/`COMP_DB_PATH` env vars are set, `/health/` verifies the paths exist and reports `"disk"`, degrading to 503 when the Render disk is unmounted or an env var drifts. Without this, a missing disk is invisible: `CompDatabase()` fabricates an empty schema at any path ("No comps yet" masking data loss), uploads write to the ephemeral container FS and evaporate on the next deploy, and `SELECT 1` health stays green throughout.

---

# PR 5A — Comps browser + Settings editor (standard tier, UI passes ×2 per page)

## File Structure

- Modify: `webapp/models.py` (add `ConfigOverride`; add `AnalysisRun.applied_overrides`)
- Create: `webapp/migrations/0004_configoverride_applied_overrides.py` (generated)
- Modify: `webapp/services.py` (registry-driven patching: `_PATCHED_DICTS`, `_ORIG_CONFIG`, `_merge_patch`, `_patched_config`, `resolve_config_overrides`, `build_config_patch`, `effective_config`; worker integration; delete `_patched_replacement_cost`)
- Modify: `webapp/forms.py` (`override_key_registry`, `parse_override_value`, `format_override_value`, `ConfigOverrideForm`; effective-baseline threading in `build_initial`/`build_overrides`)
- Modify: `gui/deal_manager.py` (add `ASSET_TYPES` tuple next to `detect_asset_type` — single source for the scope dropdown; moves to services in PR 5C)
- Modify: `webapp/views.py` (`comps`, `settings_page`, `override_delete`; pass effective baseline in `deal_assumptions`)
- Modify: `webapp/urls.py`
- Create: `webapp/templates/webapp/comps.html`, `webapp/templates/webapp/settings.html`
- Modify: `webapp/templates/base.html` (sidebar spans → links)
- Modify: `static/css/tw.css` (rebuilt)
- Test: `tests/test_web_config.py`

File-count justification: 1 migration (generated), 2 templates (one per page — the phase pattern), 1 test file (one per phase — the phase pattern). Everything else is edits to existing files. No new dependencies.

### Task A1: `ConfigOverride` model + `applied_overrides` field

**Files:**
- Modify: `webapp/models.py`
- Create: `webapp/migrations/0004_configoverride_applied_overrides.py` (generated)
- Modify: `gui/deal_manager.py`
- Test: `tests/test_web_config.py`

**Interfaces:**
- Produces: `webapp.models.ConfigOverride` (fields below); `AnalysisRun.applied_overrides` JSONField; `gui.deal_manager.ASSET_TYPES` tuple `("Self Storage", "Climate-Controlled Self Storage", "Boat & RV Storage")` — the exact strings `detect_asset_type` returns.

- [ ] **Step 1: Write the failing tests** — new file `tests/test_web_config.py`:

```python
"""Phase 5A: ConfigOverride model, resolution, patching, settings + comps pages."""
import copy
import datetime

import pytest

import config as cfg


@pytest.fixture
def operator(client, django_user_model):
    user = django_user_model.objects.create_user(username="op", password="x")
    client.force_login(user)
    return user


@pytest.mark.django_db
def test_config_override_defaults():
    from webapp.models import ConfigOverride

    row = ConfigOverride.objects.create(
        key="GATES.min_irr_5yr", value=0.12,
        effective_date=datetime.date(2026, 7, 1))
    assert row.asset_type == ""          # global scope by default
    assert row.note == ""
    assert row.created_at is not None


@pytest.mark.django_db
def test_analysis_run_applied_overrides_default():
    from webapp.models import AnalysisRun, Deal

    deal = Deal.objects.create(deal_id="x", property_name="X")
    run = AnalysisRun.objects.create(deal=deal)
    assert run.applied_overrides is None


def test_asset_types_matches_detect_asset_type():
    """No-drift guard: the scope dropdown's choices are exactly the values
    detect_asset_type can return."""
    from gui.deal_manager import ASSET_TYPES, detect_asset_type

    class FakeCim:
        brv_enclosed_sf = None
        brv_covered_sf = None
        brv_open_sf = None
        cc_pct = None

    assert detect_asset_type(FakeCim()) in ASSET_TYPES
    FakeCim.cc_pct = 0.8
    assert detect_asset_type(FakeCim()) in ASSET_TYPES
    FakeCim.brv_open_sf = 10_000
    assert detect_asset_type(FakeCim()) in ASSET_TYPES
    assert len(ASSET_TYPES) == 3
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web_config.py -v 2>&1 | tail -5`
Expected: FAIL (no `ConfigOverride`, no `applied_overrides`, no `ASSET_TYPES`).

- [ ] **Step 3: Add the model** (append to `webapp/models.py` after `AnalysisRun`) and the field (inside `AnalysisRun`, after `error`):

```python
    # Snapshot of the deltas this run actually used: {"config": {dotted
    # key: value}, "assumptions": <Deal.assumption_overrides at run
    # time>}. Written at run start so even failed runs record what they
    # attempted — this is how past analyses keep the thresholds they
    # ran under regardless of later ConfigOverride edits.
    applied_overrides = models.JSONField(null=True, blank=True)
```

```python
class ConfigOverride(models.Model):
    """One delta from a config.py threshold. Append-mostly: to change a
    value going forward, add a new row with a later effective_date; the
    resolver picks per key the asset-specific-then-latest row. Values are
    stored in canonical config units (decimals, [low, high] lists).
    """

    key = models.CharField(max_length=80)          # dotted path, e.g. "GATES.min_irr_5yr"
    value = models.JSONField()                     # number or [low, high]
    asset_type = models.CharField(max_length=60, blank=True, default="")  # "" = all
    effective_date = models.DateField()
    note = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["key", "asset_type", "-effective_date", "-pk"]

    def __str__(self):
        scope = self.asset_type or "all"
        return f"{self.key} [{scope}] from {self.effective_date}"
```

- [ ] **Step 4: Add `ASSET_TYPES` to `gui/deal_manager.py`** — directly above `detect_asset_type` (line ~105):

```python
# The exact strings detect_asset_type can return — single source for the
# settings editor's scope dropdown (guarded by a no-drift test).
ASSET_TYPES = (
    "Self Storage",
    "Climate-Controlled Self Storage",
    "Boat & RV Storage",
)
```

Leave `detect_asset_type`'s return statements as the readable string literals they are — the no-drift test (membership + `len == 3`) already catches a renamed label, and this file gets absorbed in PR 5C (review finding: don't churn a file scheduled for relocation).

- [ ] **Step 5: Generate the migration and run tests**

Run: `.venv/bin/python manage.py makemigrations webapp && .venv/bin/python -m pytest tests/test_web_config.py -v 2>&1 | tail -6`
Expected: one 0004 migration created; 3 tests pass.

- [ ] **Step 6: Full suite** — `.venv/bin/python -m pytest tests/ -q 2>&1 | tail -2` → 135 passed (was 132).

- [ ] **Step 7: Commit**

```bash
git add webapp/models.py webapp/migrations/ gui/deal_manager.py tests/test_web_config.py
git commit -m "feat(web): ConfigOverride model + AnalysisRun.applied_overrides + ASSET_TYPES registry"
```

### Task A2: Key registry + value parsing (forms.py)

**Files:**
- Modify: `webapp/forms.py`
- Test: append to `tests/test_web_config.py`

**Interfaces:**
- Consumes: `cfg.GATES/EXPENSE_BENCHMARKS/REPLACEMENT_COST/SCENARIO_DEFAULTS/VALUE_ADD_SCENARIOS/VALUE_ADD_TRIGGERS/SOLVER_TARGET_IRR`; existing `RC_PCT_KEYS` (forms.py:58), `VA_NON_PCT`, `SCENARIO_KEYS`, `SCENARIO_PARAMS`, `VA_PARAMS`; `ASSET_TYPES` (Task A1).
- Produces: `forms.override_key_registry() -> dict[str, dict]` mapping dotted key → `{"group": str, "kind": "scalar"|"range", "pct": bool, "int": bool, "label": str}`, derived live from the config module, excluding RC legacy aliases; `forms.parse_override_value(key, raw) -> number | list` (display units in, canonical units out; raises `forms.ValidationError`); `forms.format_override_value(key, value) -> str` (canonical in, display out); `forms.ConfigOverrideForm` (fields `key`, `value` CharField, `asset_type` ChoiceField `[("", "All asset types")] + ASSET_TYPES`, `effective_date` DateField initial today, `note`; `save()` → `ConfigOverride`).

- [ ] **Step 1: Write the failing tests** (append):

```python
def test_registry_derives_from_config():
    from webapp.forms import override_key_registry

    reg = override_key_registry()
    # spot checks across every group
    assert reg["GATES.min_irr_5yr"] == {
        "group": "Gates", "kind": "scalar", "pct": True, "int": False,
        "label": "Min Irr 5Yr"}
    assert reg["GATES.population_3mi"]["int"] is True
    assert reg["GATES.population_3mi"]["pct"] is False
    assert reg["EXPENSE_BENCHMARKS.property_tax"]["kind"] == "range"
    assert reg["EXPENSE_BENCHMARKS.property_tax"]["pct"] is False
    assert reg["EXPENSE_BENCHMARKS.mgmt_fee_pct"]["pct"] is True
    assert reg["REPLACEMENT_COST.soft_cost_pct"]["pct"] is True
    assert reg["SCENARIO_DEFAULTS.base.exit_cap"]["kind"] == "scalar"
    assert reg["VALUE_ADD_SCENARIOS.bull.months_to_stabilize"]["pct"] is False
    assert reg["VALUE_ADD_TRIGGERS.max_occupancy"]["pct"] is True
    assert reg["SOLVER_TARGET_IRR"]["pct"] is True
    # legacy aliases and derived keys are NOT offered
    for alias in ("non_cc_per_sf", "cc_per_sf", "site_work_per_sf"):
        assert f"REPLACEMENT_COST.{alias}" not in reg
    assert "EXPENSE_BENCHMARKS.total_opex" not in reg   # recomputed per state
    # every registry key resolves against the live config module
    from webapp.forms import dotted_get
    for key in reg:
        dotted_get(cfg, key)          # raises KeyError/AttributeError on drift


def test_parse_and_format_override_values():
    from django.forms import ValidationError

    from webapp.forms import format_override_value, parse_override_value

    assert parse_override_value("GATES.min_irr_5yr", "12") == 0.12
    assert parse_override_value("GATES.population_3mi", "60000") == 60000
    # the displayed format must always be re-enterable (round-trip)
    assert parse_override_value("GATES.population_3mi", "60,000") == 60000
    assert parse_override_value("EXPENSE_BENCHMARKS.property_tax",
                                "1.40, 2.60") == [1.4, 2.6]
    assert parse_override_value("EXPENSE_BENCHMARKS.mgmt_fee_pct",
                                "4, 7") == [0.04, 0.07]
    assert parse_override_value(
        "VALUE_ADD_SCENARIOS.bull.months_to_stabilize", "18") == 18
    with pytest.raises(ValidationError):
        parse_override_value("GATES.min_irr_5yr", "1, 2")     # scalar key
    with pytest.raises(ValidationError):
        parse_override_value("EXPENSE_BENCHMARKS.property_tax", "5")  # range key
    with pytest.raises(ValidationError):
        parse_override_value("EXPENSE_BENCHMARKS.property_tax", "3, 1")  # low > high
    with pytest.raises(ValidationError):
        parse_override_value("GATES.min_irr_5yr", "abc")

    assert format_override_value("GATES.min_irr_5yr", 0.12) == "12%"
    assert format_override_value("EXPENSE_BENCHMARKS.property_tax",
                                 [1.4, 2.6]) == "1.4 – 2.6"
    assert format_override_value("EXPENSE_BENCHMARKS.mgmt_fee_pct",
                                 [0.04, 0.07]) == "4% – 7%"
    assert format_override_value("GATES.population_3mi", 60000) == "60000"


@pytest.mark.django_db
def test_config_override_form_round_trip():
    from webapp.forms import ConfigOverrideForm
    from webapp.models import ConfigOverride

    form = ConfigOverrideForm({
        "key": "GATES.min_irr_5yr", "value": "12", "asset_type": "",
        "effective_date": "2026-07-01", "note": "tighten"})
    assert form.is_valid(), form.errors
    row = form.save()
    assert ConfigOverride.objects.get(pk=row.pk).value == 0.12

    bad = ConfigOverrideForm({
        "key": "GATES.nope", "value": "1",
        "asset_type": "", "effective_date": "2026-07-01"})
    assert not bad.is_valid()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web_config.py -v 2>&1 | tail -6`
Expected: FAIL (no registry/parse functions).

- [ ] **Step 3: Implement in `webapp/forms.py`** — append a new section at the end. Extend the module imports with `from django.utils import timezone` and `from gui.deal_manager import ASSET_TYPES` (services must NOT be imported at module level here — services imports forms lazily in PR 5A Task A3, and a top-level cycle would break startup):

```python
# ── Phase 5: config override registry + form ────────────────────────

GATES_INT_KEYS = {"population_3mi", "unproven_vintage_year"}
EXPENSE_PCT_KEYS = {"mgmt_fee_pct", "opex_revenue_ratio"}
RC_LEGACY_ALIASES = {"non_cc_per_sf", "cc_per_sf", "site_work_per_sf"}
# total_opex is recomputed from the line items by get_regional_benchmarks
# (config.py:394-396) — an override would show in the preview but never
# reach a run with a known state. Derived, not editable.
EXPENSE_DERIVED_KEYS = {"total_opex"}


def _label(key: str) -> str:
    return key.split(".")[-1].replace("_", " ").title()


def dotted_get(root, dotted_key: str):
    """Resolve 'GATES.min_irr_5yr' against a config-shaped tree — the
    config module itself or an effective_config() mapping. ScenarioType
    is a str Enum, so plain [] lookups work at every level."""
    node = root
    for part in dotted_key.split("."):
        node = node[part] if isinstance(node, dict) else getattr(node, part)
    return node


def override_key_registry() -> dict:
    """Editable threshold keys, derived LIVE from config.py so the picker
    can never drift from the real constants. Values are stored/applied in
    canonical config units; `pct` keys display as whole-number percents.
    """
    reg = {}
    for k in cfg.GATES:
        reg[f"GATES.{k}"] = {
            "group": "Gates", "kind": "scalar",
            "pct": k not in GATES_INT_KEYS, "int": k in GATES_INT_KEYS,
            "label": _label(k)}
    for k in cfg.EXPENSE_BENCHMARKS:
        if k in EXPENSE_DERIVED_KEYS:
            continue
        reg[f"EXPENSE_BENCHMARKS.{k}"] = {
            "group": "Expense Benchmarks ($/NRSF/yr)", "kind": "range",
            "pct": k in EXPENSE_PCT_KEYS, "int": False, "label": _label(k)}
    for k in cfg.REPLACEMENT_COST:
        if k in RC_LEGACY_ALIASES:
            continue                     # synced automatically by the patcher
        reg[f"REPLACEMENT_COST.{k}"] = {
            "group": "Replacement Cost ($/SF)", "kind": "range",
            "pct": k in RC_PCT_KEYS, "int": False, "label": _label(k)}
    for top, group in (("SCENARIO_DEFAULTS", "Scenarios"),
                       ("VALUE_ADD_SCENARIOS", "Value-Add Scenarios")):
        for scen, params in getattr(cfg, top).items():
            for p in params:
                is_int = p in VA_NON_PCT
                reg[f"{top}.{scen.value}.{p}"] = {
                    "group": group, "kind": "scalar",
                    "pct": not is_int, "int": is_int,
                    "label": f"{scen.value.title()} {_label(p)}"}
    for k in cfg.VALUE_ADD_TRIGGERS:
        reg[f"VALUE_ADD_TRIGGERS.{k}"] = {
            "group": "Value-Add Triggers", "kind": "scalar",
            "pct": True, "int": False, "label": _label(k)}
    reg["SOLVER_TARGET_IRR"] = {
        "group": "Solver", "kind": "scalar", "pct": True, "int": False,
        "label": "Solver Target IRR"}
    return reg


def _parse_num(raw_part: str) -> float:
    # NB: forms.py already has a `_num()` field factory — don't shadow it.
    try:
        return float(raw_part.replace("%", "").replace("$", "").strip())
    except ValueError:
        raise forms.ValidationError("Enter numbers only.")


def parse_override_value(key: str, raw: str):
    """Display units in ('12' or '1.40, 2.60'), canonical units out
    (0.12 or [1.4, 2.6]). Comma is the range separator ONLY for range
    keys; scalars strip thousands separators so the displayed format is
    always re-enterable (review finding)."""
    spec = override_key_registry().get(key)
    if spec is None:
        raise forms.ValidationError("Unknown setting key.")
    raw = str(raw).replace("–", ",")
    if spec["kind"] == "range":
        parts = [p for p in (s.strip() for s in raw.split(",")) if p]
        if len(parts) != 2:
            raise forms.ValidationError("Enter two numbers: low, high.")
        low, high = _parse_num(parts[0]), _parse_num(parts[1])
        if spec["pct"]:
            low, high = low / 100.0, high / 100.0
        if low > high:
            raise forms.ValidationError("Low must be ≤ high.")
        return [round(low, 6), round(high, 6)]
    v = _parse_num(raw.replace(",", ""))
    if spec["int"]:
        return int(v)
    if spec["pct"]:
        v = v / 100.0
    return round(v, 6)


def format_override_value(key: str, value) -> str:
    """Canonical units in, display string out (inverse of parse — no
    thousands separators, so any displayed value re-parses verbatim)."""
    spec = override_key_registry().get(key)

    def one(v, pct):
        if pct:
            return f"{round(float(v) * 100, 4):g}%"
        if spec and spec["int"]:
            return f"{int(v)}"
        return f"{round(float(v), 4):g}"

    pct = bool(spec and spec["pct"])
    if isinstance(value, (list, tuple)):
        return f"{one(value[0], pct)} – {one(value[1], pct)}"
    return one(value, pct)


class ConfigOverrideForm(forms.Form):
    key = forms.ChoiceField()
    value = forms.CharField(max_length=60)
    asset_type = forms.ChoiceField(required=False)
    # timezone.localdate, NOT datetime.date.today: Render's system clock
    # is UTC — after ~6pm Chicago, today() is tomorrow, and a freshly
    # added override would be silently "scheduled"/inert (review finding).
    effective_date = forms.DateField(initial=timezone.localdate,
                                     widget=forms.DateInput(attrs={"type": "date"}))
    note = forms.CharField(max_length=200, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        reg = override_key_registry()
        groups = {}
        for key, spec in reg.items():
            groups.setdefault(spec["group"], []).append(
                (key, f"{spec['label']} ({key})"))
        self.fields["key"].choices = [
            (g, opts) for g, opts in groups.items()]
        self.fields["asset_type"].choices = (
            [("", "All asset types")] + [(a, a) for a in ASSET_TYPES])

    def clean(self):
        cleaned = super().clean()
        key, raw = cleaned.get("key"), cleaned.get("value")
        if key and raw is not None:
            cleaned["parsed_value"] = parse_override_value(key, raw)
        return cleaned

    def save(self):
        from webapp.models import ConfigOverride
        return ConfigOverride.objects.create(
            key=self.cleaned_data["key"],
            value=self.cleaned_data["parsed_value"],
            asset_type=self.cleaned_data.get("asset_type") or "",
            effective_date=self.cleaned_data["effective_date"],
            note=self.cleaned_data.get("note") or "")
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_web_config.py -v 2>&1 | tail -8`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add webapp/forms.py tests/test_web_config.py
git commit -m "feat(web): live-derived override key registry + value parsing + ConfigOverrideForm"
```

### Task A3: Resolution + generalized config patch + worker integration

**Files:**
- Modify: `webapp/services.py`
- Test: append to `tests/test_web_config.py`

**Interfaces:**
- Consumes: `ConfigOverride` (A1), `override_key_registry` (A2, imported lazily inside functions), existing `_ANALYSIS_LOCK`, `_analysis_worker`.
- Produces: `services.override_precedence(row) -> tuple` (the single within-lane tie-break, shared with A6's badges); `services.resolve_config_overrides(asset_type: str, on_date: date) -> dict[str, value]`; `services.build_config_patch(deltas) -> (patch: dict, solver_target_irr: float | None, skipped_keys: list[str])`; `services._patched_config(patch)` context manager (replaces `_patched_replacement_cost`, which is **deleted**); `services.effective_config(asset_type="", on_date=None) -> dict` with keys `GATES`, `EXPENSE_BENCHMARKS`, `REPLACEMENT_COST`, `SCENARIO_DEFAULTS`, `VALUE_ADD_SCENARIOS`, `VALUE_ADD_TRIGGERS`, `SOLVER_TARGET_IRR` (deep copies of `_ORIG_CONFIG`, deltas applied). Worker stamps `applied_overrides = {"config", "config_skipped", "assumptions"}`, drops scenario/VA deltas when per-deal sections exist, and composes per-deal RC/solver deltas over global ones.

- [ ] **Step 1: Write the failing tests** (append):

```python
@pytest.mark.django_db
def test_resolution_precedence():
    from webapp.models import ConfigOverride
    from webapp.services import resolve_config_overrides

    d = datetime.date
    # global, older
    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.11,
                                  effective_date=d(2026, 1, 1))
    # global, newer — wins over older global
    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.12,
                                  effective_date=d(2026, 6, 1))
    # asset-specific with an EARLIER date — still beats any global
    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.14,
                                  asset_type="Boat & RV Storage",
                                  effective_date=d(2026, 2, 1))
    # future-dated — inert until due
    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.20,
                                  effective_date=d(2027, 1, 1))
    today = d(2026, 7, 27)
    assert resolve_config_overrides("", today) == {"GATES.min_irr_5yr": 0.12}
    assert resolve_config_overrides("Self Storage", today) == {
        "GATES.min_irr_5yr": 0.12}
    assert resolve_config_overrides("Boat & RV Storage", today) == {
        "GATES.min_irr_5yr": 0.14}
    # same key+scope+date: higher pk wins
    a = ConfigOverride.objects.create(key="GATES.population_3mi", value=55000,
                                      effective_date=d(2026, 6, 1))
    b = ConfigOverride.objects.create(key="GATES.population_3mi", value=60000,
                                      effective_date=d(2026, 6, 1))
    assert b.pk > a.pk
    assert resolve_config_overrides("", today)["GATES.population_3mi"] == 60000


def test_build_config_patch_shapes_and_unknown_keys(caplog):
    from registry import ScenarioType
    from webapp.services import build_config_patch

    deltas = {
        "GATES.min_irr_5yr": 0.12,
        "EXPENSE_BENCHMARKS.property_tax": [1.4, 2.6],
        "SCENARIO_DEFAULTS.base.exit_cap": 0.07,
        "SOLVER_TARGET_IRR": 0.12,
        "GATES.retired_key_from_2025": 1.0,      # unknown → skipped, warned
    }
    patch, solver, skipped = build_config_patch(deltas)
    assert solver == 0.12
    assert patch["GATES"] == {"min_irr_5yr": 0.12}
    assert patch["EXPENSE_BENCHMARKS"] == {"property_tax": [1.4, 2.6]}
    assert patch["SCENARIO_DEFAULTS"] == {ScenarioType.BASE: {"exit_cap": 0.07}}
    assert skipped == ["GATES.retired_key_from_2025"]
    assert "retired_key_from_2025" in caplog.text


def test_patched_config_mutates_in_place_and_restores():
    # Importers bound these dict OBJECTS at import time — the patch must
    # be visible through those bindings, then fully restored.
    from analysis.filters import GATES as bound_gates
    from analysis.physical import REPLACEMENT_COST as bound_rc
    from analysis.valuation import SCENARIO_DEFAULTS as bound_scen
    from registry import ScenarioType
    from webapp.services import _patched_config

    orig_irr = bound_gates["min_irr_5yr"]
    orig_exit = bound_scen[ScenarioType.BASE]["exit_cap"]
    orig_rc = bound_rc["ss_driveup_per_sf"]
    orig_alias = bound_rc["non_cc_per_sf"]
    patch = {
        "GATES": {"min_irr_5yr": 0.12, "not_a_key": 9},
        "SCENARIO_DEFAULTS": {ScenarioType.BASE: {"exit_cap": 0.07}},
        "REPLACEMENT_COST": {"ss_driveup_per_sf": [100, 120]},
    }
    with _patched_config(patch):
        assert bound_gates["min_irr_5yr"] == 0.12
        assert "not_a_key" not in bound_gates
        assert bound_scen[ScenarioType.BASE]["exit_cap"] == 0.07
        assert tuple(bound_rc["ss_driveup_per_sf"]) == (100, 120)
        # legacy alias synced (analysis/physical.py:151-154 reads it)
        assert tuple(bound_rc["non_cc_per_sf"]) == (100, 120)
    assert bound_gates["min_irr_5yr"] == orig_irr
    assert bound_scen[ScenarioType.BASE]["exit_cap"] == orig_exit
    assert bound_rc["ss_driveup_per_sf"] == orig_rc
    assert bound_rc["non_cc_per_sf"] == orig_alias


def test_patched_config_restores_on_exception():
    from analysis.filters import GATES as bound_gates
    from webapp.services import _patched_config

    orig = bound_gates["min_irr_5yr"]
    with pytest.raises(RuntimeError):
        with _patched_config({"GATES": {"min_irr_5yr": 0.5}}):
            raise RuntimeError("boom")
    assert bound_gates["min_irr_5yr"] == orig


@pytest.mark.django_db
def test_effective_config_never_mutates_module():
    from webapp.models import ConfigOverride
    from webapp.services import effective_config

    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.13,
                                  effective_date=datetime.date(2026, 1, 1))
    ConfigOverride.objects.create(key="SOLVER_TARGET_IRR", value=0.12,
                                  effective_date=datetime.date(2026, 1, 1))
    eff = effective_config("")
    assert eff["GATES"]["min_irr_5yr"] == 0.13
    assert eff["SOLVER_TARGET_IRR"] == 0.12
    assert cfg.GATES["min_irr_5yr"] == 0.10          # module untouched
    assert cfg.SOLVER_TARGET_IRR == 0.10
    eff["GATES"]["min_irr_5yr"] = 0.99               # caller-owned copy
    assert cfg.GATES["min_irr_5yr"] == 0.10
```

- [ ] **Step 2: Run to verify failure** — new functions missing.

- [ ] **Step 3: Implement in `webapp/services.py`** — replace the whole "Per-run config overrides" section (lines 87–111, `_ORIG_REPLACEMENT_COST` through `_patched_replacement_cost`) with:

```python
# ── Per-run config overrides ────────────────────────────────────────

# Most analysis modules bind these dict OBJECTS at import time
# (`from config import GATES` etc.), so overrides must mutate the shared
# dicts in place and restore them afterwards. The lock serializes
# analysis runs within this process so patched config never leaks
# across deals. (The lock is per-process: two gunicorn workers can run
# two analyses concurrently, each patching its own config module copy —
# safe by construction.)
_PATCHED_DICTS = ("GATES", "EXPENSE_BENCHMARKS", "REPLACEMENT_COST",
                  "SCENARIO_DEFAULTS", "VALUE_ADD_SCENARIOS",
                  "VALUE_ADD_TRIGGERS")
_ORIG_CONFIG = {n: copy.deepcopy(getattr(cfg, n)) for n in _PATCHED_DICTS}
_ANALYSIS_LOCK = threading.Lock()

# analysis/physical.py reads these legacy alias keys at call time; keep
# them in lockstep whenever the canonical key is patched (the Streamlit
# editor did this sync; the per-deal RC path previously missed it).
_RC_ALIAS_SYNC = {"ss_driveup_per_sf": "non_cc_per_sf",
                  "ss_enclosed_per_sf": "cc_per_sf",
                  "ss_driveup_site_per_sf": "site_work_per_sf"}


def _merge_patch(targets: dict, patch: dict) -> None:
    """Apply {constant: {key: value}} (scenario tops nest one level
    deeper) into `targets`' dicts, mutating them in place. Unknown keys
    are ignored; RC alias keys are kept in sync with their canonical
    source."""
    for name, changes in patch.items():
        target = targets.get(name)
        if target is None:
            continue
        for k, v in changes.items():
            if isinstance(v, dict):                  # scenario param dicts
                if k in target:
                    target[k].update(v)
            elif k in _ORIG_CONFIG[name]:
                target[k] = tuple(v) if isinstance(v, (list, tuple)) else v
        if name == "REPLACEMENT_COST":
            for src, alias in _RC_ALIAS_SYNC.items():
                if src in changes:
                    target[alias] = target[src]


@contextmanager
def _patched_config(patch):
    """In-place config mutation for one analysis run. Caller must hold
    _ANALYSIS_LOCK. Never rebinds a config attr — importers hold the
    original dict objects."""
    if not patch:
        yield
        return
    touched = [n for n in patch if n in _PATCHED_DICTS]
    try:
        _merge_patch({n: getattr(cfg, n) for n in touched}, patch)
        yield
    finally:
        for name in touched:
            live = getattr(cfg, name)
            live.clear()
            live.update(copy.deepcopy(_ORIG_CONFIG[name]))


def override_precedence(row):
    """Within one (key, scope) lane: later effective_date wins, then
    higher pk. THE single definition — the resolver's sort and the
    settings page's active/superseded badges both use it, so the
    tie-break can never drift between them (review finding)."""
    return (row.effective_date, row.pk)


def resolve_config_overrides(asset_type: str, on_date) -> dict:
    """{dotted_key: value} effective for (asset_type, on_date).
    Precedence: asset-specific beats global regardless of dates; then
    override_precedence. Resolved in Python so SQLite and Postgres
    behave identically."""
    from webapp.models import ConfigOverride

    rows = list(ConfigOverride.objects.filter(effective_date__lte=on_date)
                .filter(models.Q(asset_type="") |
                        models.Q(asset_type=asset_type or "")))
    rows.sort(key=lambda r: (r.key, r.asset_type != "")
              + override_precedence(r))
    return {r.key: r.value for r in rows}      # winner lands last per key


def build_config_patch(deltas: dict):
    """Dotted-key deltas → (patch for _patched_config, solver_target_irr
    or None, skipped_keys). Keys config.py no longer defines are logged,
    skipped, and RETURNED — the worker stamps them as config_skipped so
    the run record never claims a threshold the engine didn't see (an
    old override row must never crash a run, and never lie either)."""
    from registry import ScenarioType
    from webapp.forms import override_key_registry

    registry = override_key_registry()
    patch, solver_irr, skipped = {}, None, []
    for key, value in deltas.items():
        if key not in registry:
            logger.warning("config override for unknown key %r skipped", key)
            skipped.append(key)
            continue
        if key == "SOLVER_TARGET_IRR":
            solver_irr = float(value)
            continue
        parts = key.split(".")
        if parts[0] in ("SCENARIO_DEFAULTS", "VALUE_ADD_SCENARIOS"):
            scen = ScenarioType(parts[1])
            patch.setdefault(parts[0], {}).setdefault(scen, {})[
                parts[2]] = float(value)
        else:
            patch.setdefault(parts[0], {})[parts[1]] = value
    return patch, solver_irr, skipped


def effective_config(asset_type: str = "", on_date=None) -> dict:
    """Deep-copied config constants with the effective ConfigOverride
    deltas applied — the baseline the settings page displays and the
    assumptions editor diffs against. Never mutates the config module.

    Copies from _ORIG_CONFIG, NOT the live module: a request thread can
    land here while an analysis run holds the live dicts patched with
    ANOTHER deal's values (2 workers × 4 threads) — copying the live
    module would contaminate the baseline (review finding). SOLVER_TARGET_IRR
    is never patched in place, so the live read is safe."""
    deltas = resolve_config_overrides(
        asset_type, on_date or timezone.localdate())
    patch, solver_irr, _skipped = build_config_patch(deltas)
    eff = {n: copy.deepcopy(_ORIG_CONFIG[n]) for n in _PATCHED_DICTS}
    _merge_patch(eff, patch)
    eff["SOLVER_TARGET_IRR"] = (solver_irr if solver_irr is not None
                                else cfg.SOLVER_TARGET_IRR)
    return eff
```

Add `from django.db import models` to the module's django imports (it currently imports only `transaction`; extend that line: `from django.db import models, transaction`).

- [ ] **Step 4: Integrate into `_analysis_worker`** — replace the block from `pdf_path = ""` guard **down through** the `with _ANALYSIS_LOCK:` context (currently lines ~310–329) so the run resolves global deltas, stamps them, and composes per-deal deltas on top:

```python
        pdf_path = ""
        if deal.input_files:
            pdf_path = os.path.join(deal.deal_dir, "inputs", deal.input_files[0])
        result = AnalysisResult(pdf_path=pdf_path)
        result.cim_data = cim
        result.extraction_report = deal.extraction_report or {}

        def _progress(step, total, msg):
            AnalysisRun.objects.filter(pk=run_pk).update(
                progress_step=step, progress_total=total,
                progress_msg=str(msg)[:200])

        # Global ConfigOverride deltas for this deal's asset type today;
        # per-deal assumption overrides compose on top (per-deal wins).
        config_deltas = resolve_config_overrides(
            deal.asset_type, timezone.localdate())
        # Per-deal scenario/VA sections are FULL 3×6 snapshots that the
        # engine applies wholesale (custom_scenarios or DEFAULTS), so
        # global scenario deltas can't reach those runs — drop them from
        # patch AND stamp rather than record deltas that never applied
        # (Design Decision 6).
        for section, prefix in (("scenario_overrides", "SCENARIO_DEFAULTS."),
                                ("va_scenario_overrides",
                                 "VALUE_ADD_SCENARIOS.")):
            if overrides.get(section):
                config_deltas = {k: v for k, v in config_deltas.items()
                                 if not k.startswith(prefix)}
        patch, cfg_solver_irr, skipped = build_config_patch(config_deltas)
        rc = overrides.get("replacement_cost_overrides")
        if rc:
            patch.setdefault("REPLACEMENT_COST", {}).update(rc)
        solver_irr = overrides.get("solver_target_irr") or cfg_solver_irr
        # Stamped BEFORE the run so even failed runs record what they
        # attempted — past analyses keep the thresholds they ran under.
        # Only deltas the engine will actually see go under "config";
        # unknown-key rows are surfaced as config_skipped, not hidden in
        # a daemon-thread log (Design Decision 13).
        applied = {k: v for k, v in config_deltas.items() if k not in skipped}
        AnalysisRun.objects.filter(pk=run_pk).update(
            applied_overrides=json_safe(
                {"config": applied, "config_skipped": skipped,
                 "assumptions": overrides}))

        with _ANALYSIS_LOCK:
            with _patched_config(patch):
                result = run_analysis(
                    result, progress=_progress, output_dir=deal.deal_dir,
                    custom_scenarios=overrides.get("scenario_overrides"),
                    custom_va_scenarios=overrides.get("va_scenario_overrides"),
                    solver_target_irr=solver_irr,
                )
```

- [ ] **Step 5: Update the two Phase 4 patch tests** — in `tests/test_web_runs.py`, `test_patched_replacement_cost_mutates_in_place_and_restores` and `test_patched_replacement_cost_restores_on_exception` reference the deleted `_patched_replacement_cost`. Rewrite both to call `_patched_config({"REPLACEMENT_COST": {...}})` with identical assertions (the new Task A3 Step 1 tests already cover the richer behavior; these two keep the Phase 4 regression names alive):

```python
def test_patched_replacement_cost_mutates_in_place_and_restores():
    from analysis.physical import REPLACEMENT_COST as bound
    from webapp.services import _patched_config

    original = bound["ss_driveup_per_sf"]
    with _patched_config({"REPLACEMENT_COST": {"ss_driveup_per_sf": [100, 120],
                                               "not_a_real_key": [1, 2]}}):
        assert tuple(bound["ss_driveup_per_sf"]) == (100, 120)
        assert "not_a_real_key" not in bound
    assert bound["ss_driveup_per_sf"] == original


def test_patched_replacement_cost_restores_on_exception():
    from analysis.physical import REPLACEMENT_COST as bound
    from webapp.services import _patched_config

    original = bound["ss_driveup_per_sf"]
    with pytest.raises(RuntimeError):
        with _patched_config({"REPLACEMENT_COST": {"ss_driveup_per_sf": [100, 120]}}):
            raise RuntimeError("boom")
    assert bound["ss_driveup_per_sf"] == original
```

Also update `test_engine_end_to_end_with_overrides` in the same file: replace its `_patched_replacement_cost({...})` context with `_patched_config({"REPLACEMENT_COST": {...}})` (import adjusted accordingly).

- [ ] **Step 6: Add worker-level integration tests** (append to `tests/test_web_config.py`; reuses `tests/test_web_runs.py` fixtures via import):

```python
@pytest.mark.django_db
def test_worker_applies_global_overrides_and_stamps_run(deals_dir, monkeypatch):
    """A global ConfigOverride reaches the engine (patched GATES visible
    through the import-time binding DURING the run), per-deal solver
    override beats the global one, and the run row records both."""
    import datetime as dt

    from tests.test_web_runs import _make_extracted_deal

    from webapp.models import ConfigOverride

    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.13,
                                  effective_date=dt.date(2026, 1, 1))
    ConfigOverride.objects.create(key="SOLVER_TARGET_IRR", value=0.12,
                                  effective_date=dt.date(2026, 1, 1))
    # a global scenario delta that must be DROPPED (per-deal section wins
    # wholesale) and an unknown key that must land in config_skipped
    ConfigOverride.objects.create(key="SCENARIO_DEFAULTS.base.exit_cap",
                                  value=0.07,
                                  effective_date=dt.date(2026, 1, 1))
    seen = {}

    def _fake(result, progress=None, output_dir=None, custom_scenarios=None,
              custom_va_scenarios=None, solver_target_irr=None):
        from analysis.filters import GATES
        seen["min_irr_during_run"] = GATES["min_irr_5yr"]
        seen["solver_target_irr"] = solver_target_irr
        seen["custom_scenarios"] = custom_scenarios
        result.gate_results = []
        result.gate_summary = {"passed": 0, "failed": 0, "tbd": 0, "total": 0,
                               "recommendation": "PURSUE",
                               "failed_gates": [], "tbd_gates": []}
        return result

    monkeypatch.setattr("webapp.services.run_analysis", _fake)
    deal = _make_extracted_deal(deals_dir)
    per_deal_scen = {"base": {"exit_cap": 0.08}}
    deal.assumption_overrides = {"solver_target_irr": 0.15,   # per-deal wins
                                 "scenario_overrides": per_deal_scen}
    deal.save()
    from tests.test_web_runs import _start_run
    run = _start_run(deal)

    assert seen["min_irr_during_run"] == 0.13
    assert seen["solver_target_irr"] == 0.15
    assert seen["custom_scenarios"] == per_deal_scen
    # stamp records ONLY what applied: the scenario delta was dropped
    # (per-deal section wins wholesale), nothing was unknown
    assert run.applied_overrides["config"] == {
        "GATES.min_irr_5yr": 0.13, "SOLVER_TARGET_IRR": 0.12}
    assert run.applied_overrides["config_skipped"] == []
    assert run.applied_overrides["assumptions"]["solver_target_irr"] == 0.15
    from analysis.filters import GATES
    assert GATES["min_irr_5yr"] == 0.10          # restored after the run
```

(`deals_dir` is defined in `tests/test_web_runs.py`; re-declare it in this file's fixtures verbatim rather than importing a fixture — pytest fixtures don't import across files without conftest promotion. Copy the 6-line `deals_dir` fixture from `tests/test_web_runs.py` into `tests/test_web_config.py`. The helper *functions* `_make_extracted_deal`/`_start_run` import fine.)

- [ ] **Step 7: Run both files, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_web_config.py tests/test_web_runs.py -v 2>&1 | tail -8` then `.venv/bin/python -m pytest tests/ -q 2>&1 | tail -2`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add webapp/services.py tests/test_web_config.py tests/test_web_runs.py
git commit -m "feat(web): ConfigOverride resolution + generalized in-place config patch with alias sync, worker stamps applied_overrides"
```

### Task A4: Effective-config baseline in the assumptions editor

**Files:**
- Modify: `webapp/forms.py` (`build_initial`, `build_overrides`, `_submitted_sections` call sites), `webapp/views.py` (`deal_assumptions`)
- Test: append to `tests/test_web_config.py`

**Interfaces:**
- Consumes: `services.effective_config` (A3).
- Produces: `build_initial(deal, eff=None)` and `build_overrides(cleaned, post, deal, eff=None)` — the effective-config mapping as an optional argument, `None` → computed internally via a lazy `from webapp import services` import (`services.effective_config(deal.asset_type)`). The default preserves every existing Phase 3 call site — `tests/test_web_analyze.py::_post_assumptions` calls one-arg `build_initial(deal)` and must keep passing unmodified (review finding). Every `cfg.SCENARIO_DEFAULTS` / `cfg.VALUE_ADD_SCENARIOS` / `cfg.REPLACEMENT_COST` / `cfg.SOLVER_TARGET_IRR` read inside them switches to `eff[...]`. `deal_assumptions` computes `eff = services.effective_config(deal.asset_type)` once per request and passes it to both explicitly (one resolution, one consistent baseline for display + diff).

- [ ] **Step 1: Write the failing tests** (append):

```python
@pytest.mark.django_db
def test_assumptions_baseline_reflects_global_override(deals_dir):
    """With a global scenario override active, the form's initial shows
    the EFFECTIVE value, and saving the form untouched produces NO
    per-deal scenario delta (baseline == effective, not config.py).
    post arg is a QueryDict: parse_unit_mix calls .getlist (review)."""
    from django.http import QueryDict

    from tests.test_web_runs import _make_extracted_deal

    from webapp import services
    from webapp.forms import build_initial, build_overrides, AssumptionsForm
    from webapp.models import ConfigOverride

    ConfigOverride.objects.create(
        key="SCENARIO_DEFAULTS.base.exit_cap", value=0.07,
        effective_date=datetime.date(2026, 1, 1))
    deal = _make_extracted_deal(deals_dir)
    eff = services.effective_config(deal.asset_type)

    initial = build_initial(deal, eff)
    assert initial["scen_base_exit_cap"] == 7.0          # 0.07 shown as 7

    form = AssumptionsForm(initial)                      # resubmit as-is
    assert form.is_valid(), form.errors
    out = build_overrides(form.cleaned_data, QueryDict(), deal, eff)
    assert "scenario_overrides" not in out               # no spurious delta


@pytest.mark.django_db
def test_assumptions_explicit_change_still_persists(deals_dir):
    from tests.test_web_runs import _make_extracted_deal

    from webapp import services
    from webapp.forms import build_initial, build_overrides, AssumptionsForm

    from django.http import QueryDict

    deal = _make_extracted_deal(deals_dir)
    eff = services.effective_config(deal.asset_type)
    initial = build_initial(deal, eff)
    initial["scen_base_exit_cap"] = 8.0                  # user edits to 8%
    form = AssumptionsForm(initial)
    assert form.is_valid(), form.errors
    out = build_overrides(form.cleaned_data, QueryDict(), deal, eff)
    assert out["scenario_overrides"]["base"]["exit_cap"] == 0.08
```

(If `AssumptionsForm` requires POST-style strings, coerce with `{k: "" if v is None else v for k, v in initial.items()}` — mirror how `tests/test_web_analyze.py` submits the assumptions form; reuse its helper if one exists.)

- [ ] **Step 2: Run to verify failure** — `build_initial` doesn't accept `eff` yet.

- [ ] **Step 3: Thread `eff` through `webapp/forms.py`**

- `def build_initial(deal) -> dict:` → `def build_initial(deal, eff=None) -> dict:`, opening with:

```python
    if eff is None:
        from webapp import services      # lazy: avoids a module cycle
        eff = services.effective_config(deal.asset_type)
```

  then inside it replace: `cfg.SCENARIO_DEFAULTS.get(sc, {})` → `eff["SCENARIO_DEFAULTS"].get(sc, {})`; `cfg.VALUE_ADD_SCENARIOS.get(sc, {})` → `eff["VALUE_ADD_SCENARIOS"].get(sc, {})`; `cfg.REPLACEMENT_COST[key]` → `eff["REPLACEMENT_COST"][key]`; `cfg.SOLVER_TARGET_IRR` → `eff["SOLVER_TARGET_IRR"]`.
- `def build_overrides(cleaned, post, deal) -> dict:` → `def build_overrides(cleaned, post, deal, eff=None) -> dict:` with the same `if eff is None:` opener, and inside it replace the four `cfg.X` baseline reads the same way (`_submitted_sections(..., cfg.SCENARIO_DEFAULTS)` → `eff["SCENARIO_DEFAULTS"]`, the `_rounded_sections(cfg.X, ...)` comparisons, `cfg.REPLACEMENT_COST[key]` in the RC loop). The `eff=None` defaults keep every Phase 3 caller (incl. `tests/test_web_analyze.py::_post_assumptions`) working unchanged.
- (`ScenarioType` is a str Enum, so `eff["SCENARIO_DEFAULTS"].get("base")` keeps working with string keys — no key conversion needed.)

- [ ] **Step 4: Update the call sites in `webapp/views.py::deal_assumptions`** — add `eff = services.effective_config(deal.asset_type)` right after the deal is fetched, and pass it to every `build_initial(deal)` → `build_initial(deal, eff)` and `build_overrides(form.cleaned_data, request.POST, deal)` → `build_overrides(form.cleaned_data, request.POST, deal, eff)` occurrence (there are one of each on the GET and POST branches — grep `build_initial\|build_overrides` in views.py).

- [ ] **Step 5: Run the new tests + the Phase 3 assumptions tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_web_config.py tests/test_web_analyze.py -q 2>&1 | tail -3 && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -2`
Expected: all pass (Phase 3 tests prove no regression when zero overrides exist — `effective_config` with an empty table returns exactly the config values).

- [ ] **Step 6: Commit**

```bash
git add webapp/forms.py webapp/views.py tests/test_web_config.py
git commit -m "feat(web): assumptions editor baselines on effective config, not raw config.py"
```

### Task A5: Comps browser page

**Files:**
- Modify: `webapp/views.py`, `webapp/urls.py`, `webapp/templates/base.html`
- Create: `webapp/templates/webapp/comps.html`
- Test: append to `tests/test_web_config.py`

**Interfaces:**
- Consumes: `data.comp_db.CompDatabase.get_comp_summary() -> list[dict]` (keys: `property_name, city, state, nrsf, total_units, occupancy, adjusted_noi, revenue_per_sf, noi_per_sf, analysis_date, pdf_filename`), `get_comp_count()`.
- Produces: URL name `comps` at `/comps/`; GET params `state`, `min_nrsf`, `format=csv`.

- [ ] **Step 1: Write the failing tests** (append):

```python
@pytest.fixture
def comp_db(tmp_path, monkeypatch):
    """Scratch comp DB with three rows (data.comp_db binds COMP_DB_PATH
    at import — patch the module attribute, same as test_web_runs)."""
    path = str(tmp_path / "comps.db")
    monkeypatch.setattr("data.comp_db.COMP_DB_PATH", path)
    import sqlite3

    from data.comp_db import CompDatabase
    db = CompDatabase()          # creates schema
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO properties (property_name, city, state, nrsf,"
            " total_units, occupancy, adjusted_noi, revenue_per_sf,"
            " noi_per_sf, analysis_date, pdf_filename)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [("Alpha Storage", "Belton", "TX", 45000, 350, 0.92, 250000,
              9.3, 5.6, "2026-07-01", "alpha.pdf"),
             ("Bravo Storage", "Denver", "CO", 62000, 480, 0.88, 400000,
              10.1, 6.5, "2026-06-15", "bravo.pdf"),
             ("Small Lockers", "Waco", "TX", 18000, 190, 0.95, 90000,
              8.8, 5.0, "2026-05-20", "small.pdf")])
    return db


@pytest.mark.django_db
def test_comps_page_lists_and_filters(client, operator, comp_db):
    resp = client.get("/comps/")
    content = resp.content.decode()
    assert resp.status_code == 200
    assert "Alpha Storage" in content and "Bravo Storage" in content
    assert "3 comps" in content

    content = client.get("/comps/?state=TX").content.decode()
    assert "Alpha Storage" in content
    assert "Bravo Storage" not in content

    content = client.get("/comps/?state=TX&min_nrsf=40000").content.decode()
    assert "Alpha Storage" in content
    assert "Small Lockers" not in content


@pytest.mark.django_db
def test_comps_csv_export(client, operator, comp_db):
    resp = client.get("/comps/?state=TX&format=csv")
    assert resp["Content-Type"].startswith("text/csv")
    body = resp.content.decode()
    assert body.splitlines()[0].startswith("property_name,")
    assert "Alpha Storage" in body and "Bravo Storage" not in body


@pytest.mark.django_db
def test_comps_page_empty_db(client, operator, tmp_path, monkeypatch):
    monkeypatch.setattr("data.comp_db.COMP_DB_PATH", str(tmp_path / "e.db"))
    resp = client.get("/comps/")
    assert resp.status_code == 200
    assert b"No comps yet" in resp.content
```

- [ ] **Step 2: Run to verify failure** — 404 (no route).

- [ ] **Step 3: Add the view** (append to `webapp/views.py`; extend the `django.http` import line with `HttpResponse` — the module currently imports only `FileResponse, Http404, JsonResponse`):

```python
# ── Phase 5: comps browser ──────────────────────────────────────────

COMP_COLUMNS = ["property_name", "city", "state", "nrsf", "total_units",
                "occupancy", "adjusted_noi", "revenue_per_sf",
                "noi_per_sf", "analysis_date", "pdf_filename"]


@login_required
def comps(request):
    """Read-only browser over the existing comp SQLite DB. Filters run
    in Python: the summary query IS the API and the table is tiny."""
    from data.comp_db import CompDatabase

    all_rows = CompDatabase().get_comp_summary()
    state = request.GET.get("state", "").strip().upper()
    min_nrsf_raw = request.GET.get("min_nrsf", "").strip()
    try:
        min_nrsf = float(min_nrsf_raw) if min_nrsf_raw else 0.0
    except ValueError:
        min_nrsf = 0.0
    rows = [r for r in all_rows
            if (not state or (r["state"] or "").upper() == state)
            and (r["nrsf"] or 0) >= min_nrsf]

    if request.GET.get("format") == "csv":
        import csv

        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="comps.csv"'
        writer = csv.DictWriter(resp, fieldnames=COMP_COLUMNS)
        writer.writeheader()
        writer.writerows(
            {k: r.get(k) for k in COMP_COLUMNS} for r in rows)
        return resp

    return render(request, "webapp/comps.html", {
        "rows": rows,
        "total": len(all_rows),
        "state": state,
        "min_nrsf": min_nrsf_raw,
        "state_options": sorted({(r["state"] or "").upper()
                                 for r in all_rows if r["state"]}),
    })
```

Add to `webapp/urls.py`: `path("comps/", views.comps, name="comps"),`

- [ ] **Step 4: Create `webapp/templates/webapp/comps.html`** (follows `deal_list.html`'s filter-form + table idiom):

```html
{% extends "base.html" %}
{% block title %}Comps{% endblock %}
{% block content %}
<div class="max-w-6xl">
  <div class="flex items-baseline justify-between mb-3">
    <h1 class="text-xl font-semibold">Comp Database</h1>
    <span class="text-sm text-slate-500">{{ total }} comp{{ total|pluralize }}</span>
  </div>

  <form method="get" class="flex gap-2 mb-3 items-end">
    <label class="text-xs text-slate-600">State
      <select name="state" class="block border border-slate-300 rounded px-2 py-1 text-sm">
        <option value="">All</option>
        {% for s in state_options %}<option value="{{ s }}" {% if s == state %}selected{% endif %}>{{ s }}</option>{% endfor %}
      </select>
    </label>
    <label class="text-xs text-slate-600">Min NRSF
      <input type="number" name="min_nrsf" value="{{ min_nrsf }}" min="0" step="1000"
             class="block border border-slate-300 rounded px-2 py-1 text-sm w-28">
    </label>
    <button type="submit" class="bg-accent-700 text-white text-sm px-3 py-1.5 rounded">Filter</button>
    <a href="{% url 'comps' %}" class="text-sm text-slate-500 underline">Clear</a>
    <a href="?state={{ state }}&min_nrsf={{ min_nrsf }}&format=csv"
       class="ml-auto text-sm text-accent-700 underline">Export CSV</a>
  </form>

  <div class="overflow-x-auto">
    <table class="w-full text-sm border-collapse">
      <thead>
        <tr class="text-left border-b border-slate-300 text-xs text-slate-600">
          <th class="py-1.5 pr-3">Property</th>
          <th class="py-1.5 pr-3">City</th>
          <th class="py-1.5 pr-3">ST</th>
          <th class="py-1.5 pr-3 text-right">NRSF</th>
          <th class="py-1.5 pr-3 text-right">Units</th>
          <th class="py-1.5 pr-3 text-right">Occ.</th>
          <th class="py-1.5 pr-3 text-right">Adj. NOI</th>
          <th class="py-1.5 pr-3 text-right">Rev/SF</th>
          <th class="py-1.5 pr-3 text-right">NOI/SF</th>
          <th class="py-1.5 pr-3">Analyzed</th>
          <th class="py-1.5">Source PDF</th>
        </tr>
      </thead>
      <tbody>
        {% for r in rows %}
        <tr class="border-b border-slate-100">
          <td class="py-1.5 pr-3 font-medium">{{ r.property_name }}</td>
          <td class="py-1.5 pr-3">{{ r.city|default:"" }}</td>
          <td class="py-1.5 pr-3">{{ r.state|default:"" }}</td>
          <td class="py-1.5 pr-3 text-right">{{ r.nrsf|floatformat:0 }}</td>
          <td class="py-1.5 pr-3 text-right">{{ r.total_units|default_if_none:"" }}</td>
          <td class="py-1.5 pr-3 text-right">{% if r.occupancy is not None %}{% widthratio r.occupancy 1 100 %}%{% endif %}</td>
          <td class="py-1.5 pr-3 text-right">{% if r.adjusted_noi is not None %}${{ r.adjusted_noi|floatformat:0 }}{% endif %}</td>
          <td class="py-1.5 pr-3 text-right">{{ r.revenue_per_sf|floatformat:2 }}</td>
          <td class="py-1.5 pr-3 text-right">{{ r.noi_per_sf|floatformat:2 }}</td>
          <td class="py-1.5 pr-3 whitespace-nowrap">{{ r.analysis_date|default:"" }}</td>
          <td class="py-1.5 text-slate-500 truncate max-w-[16rem]">{{ r.pdf_filename }}</td>
        </tr>
        {% empty %}
        <tr><td colspan="11" class="py-4 text-slate-500">No comps yet — every
          completed analysis adds its property here automatically.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 5: Convert the Comps sidebar span to a link** — in `webapp/templates/base.html` (lines 77–80), replace the Comps `<span>` with the documented pattern (comment at lines 56–65):

```html
          <a href="{% url 'comps' %}"
             class="block px-2 py-1.5 md:py-0.5 rounded {% if request.resolver_match.url_name == 'comps' %}bg-accent-700 text-white{% else %}hover:bg-slate-100{% endif %}">Comps</a>
```

- [ ] **Step 6: Rebuild Tailwind, run tests**

Run: `TAILWINDCSS_VERSION=v3.4.17 .venv/bin/tailwindcss -c tailwind.config.js -i static/src/input.css -o static/css/tw.css --minify && .venv/bin/python -m pytest tests/test_web_config.py -q 2>&1 | tail -2`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add webapp/views.py webapp/urls.py webapp/templates/ static/css/tw.css tests/test_web_config.py
git commit -m "feat(web): read-only comps browser with state/NRSF filters + CSV export"
```

### Task A6: Settings page

**Files:**
- Modify: `webapp/views.py`, `webapp/urls.py`, `webapp/templates/base.html`
- Create: `webapp/templates/webapp/settings.html`
- Test: append to `tests/test_web_config.py`

**Interfaces:**
- Consumes: `ConfigOverrideForm`, `override_key_registry`, `format_override_value` (A2); `services.effective_config`, `resolve_config_overrides` (A3); `ASSET_TYPES` (A1).
- Produces: URL names `settings` (`GET/POST /settings/`) and `override-delete` (`POST /settings/overrides/<pk>/delete/`). GET param `asset_type` previews effective values for that scope.

- [ ] **Step 1: Write the failing tests** (append):

```python
@pytest.mark.django_db
def test_settings_page_add_list_delete(client, operator):
    from webapp.models import ConfigOverride

    resp = client.post("/settings/", {
        "key": "GATES.min_irr_5yr", "value": "12", "asset_type": "",
        "effective_date": "2026-07-01", "note": "tighten"})
    assert resp.status_code == 302
    row = ConfigOverride.objects.get()
    assert row.value == 0.12

    content = client.get("/settings/").content.decode()
    assert "GATES.min_irr_5yr" in content
    assert "12%" in content                       # display units
    assert "tighten" in content

    resp = client.post(f"/settings/overrides/{row.pk}/delete/")
    assert resp.status_code == 302
    assert ConfigOverride.objects.count() == 0


@pytest.mark.django_db
def test_settings_page_rejects_bad_value(client, operator):
    from webapp.models import ConfigOverride

    resp = client.post("/settings/", {
        "key": "EXPENSE_BENCHMARKS.property_tax", "value": "5",
        "asset_type": "", "effective_date": "2026-07-01"})
    assert resp.status_code == 200                # re-rendered with errors
    assert b"two numbers" in resp.content
    assert ConfigOverride.objects.count() == 0


@pytest.mark.django_db
def test_settings_status_badges(client, operator):
    import datetime as dt

    from django.utils import timezone

    from webapp.models import ConfigOverride

    today = timezone.localdate()
    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.11,
                                  effective_date=today - dt.timedelta(days=90))
    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.12,
                                  effective_date=today - dt.timedelta(days=1))
    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.15,
                                  effective_date=today + dt.timedelta(days=30))
    ConfigOverride.objects.create(key="GATES.retired_key", value=1,
                                  effective_date=today)
    content = client.get("/settings/").content.decode()
    assert content.count("superseded") == 1
    assert content.count("scheduled") == 1
    assert "unknown key" in content


@pytest.mark.django_db
def test_settings_effective_preview_by_asset_type(client, operator):
    import datetime as dt

    from webapp.models import ConfigOverride

    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.14,
                                  asset_type="Boat & RV Storage",
                                  effective_date=dt.date(2026, 1, 1))
    default_view = client.get("/settings/").content.decode()
    brv = client.get("/settings/?asset_type=Boat+%26+RV+Storage").content.decode()
    # Substring presence alone is self-fulfilling: the override row's
    # "14%" renders in the Overrides table of BOTH responses (review
    # finding). Assert the scope-sensitive part: the BRV preview adds
    # one more "14%" (its effective cell) than the global preview.
    assert brv.count("14%") == default_view.count("14%") + 1
    # and only the BRV preview marks the key changed
    assert brv.count("font-semibold text-accent-700") == \
        default_view.count("font-semibold text-accent-700") + 1
```

- [ ] **Step 2: Run to verify failure** — 404s.

- [ ] **Step 3: Add the views** (append to `webapp/views.py`):

```python
# ── Phase 5: settings (config overrides) ────────────────────────────

@login_required
def settings_page(request):
    from gui.deal_manager import ASSET_TYPES
    from webapp.forms import (ConfigOverrideForm, format_override_value,
                              override_key_registry)
    from webapp.models import ConfigOverride

    if request.method == "POST":
        form = ConfigOverrideForm(request.POST)
        if form.is_valid():
            row = form.save()
            messages.success(request, f"Override added: {row}.")
            return redirect("settings")
    else:
        form = ConfigOverrideForm()

    registry = override_key_registry()
    today = timezone.localdate()

    # Status per row, judged within its own (key, scope) lane: the
    # winning row is "active", later-dated rows are "scheduled", the
    # rest "superseded"; keys config.py no longer defines: "unknown key".
    # services.override_precedence is THE tie-break — same function the
    # resolver sorts by, so the badge can never disagree with a run.
    rows = list(ConfigOverride.objects.all())
    winners = {}
    for r in rows:
        if r.effective_date > today or r.key not in registry:
            continue
        lane = (r.key, r.asset_type)
        w = winners.get(lane)
        if w is None or services.override_precedence(r) > \
                services.override_precedence(w):
            winners[lane] = r
    overrides = []
    for r in rows:
        if r.key not in registry:
            status = "unknown key"
        elif r.effective_date > today:
            status = "scheduled"
        elif winners.get((r.key, r.asset_type)) is r:
            status = "active"
        else:
            status = "superseded"
        overrides.append({
            "row": r, "status": status,
            "display_value": format_override_value(r.key, r.value),
            "label": registry.get(r.key, {}).get("label", r.key),
        })

    # Effective-values preview for the selected scope. dotted_get works
    # against both the config module and the eff mapping (str-enum keys),
    # including top-level scalars like SOLVER_TARGET_IRR — one traversal,
    # no special cases (review finding).
    from webapp.forms import dotted_get

    sel = request.GET.get("asset_type", "")
    if sel not in ASSET_TYPES:
        sel = ""
    eff = services.effective_config(sel)
    deltas = services.resolve_config_overrides(sel, today)
    groups = {}
    for key, spec in registry.items():
        groups.setdefault(spec["group"], []).append({
            "key": key, "label": spec["label"],
            "default": format_override_value(key, dotted_get(cfg, key)),
            "effective": format_override_value(key, dotted_get(eff, key)),
            "changed": key in deltas,
        })

    return render(request, "webapp/settings.html", {
        "form": form, "overrides": overrides, "groups": groups,
        "asset_types": ASSET_TYPES, "selected_asset_type": sel,
    })


@login_required
@require_POST
def override_delete(request, pk):
    from webapp.models import ConfigOverride

    row = get_object_or_404(ConfigOverride, pk=pk)
    row.delete()
    messages.success(request, f"Deleted override {row.key}.")
    return redirect("settings")
```

Add `import config as cfg` to views.py's imports if not already present (check the header; services is imported as `services`). Add to `webapp/urls.py`:

```python
    path("settings/", views.settings_page, name="settings"),
    path("settings/overrides/<int:pk>/delete/", views.override_delete,
         name="override-delete"),
```

- [ ] **Step 4: Create `webapp/templates/webapp/settings.html`**:

```html
{% extends "base.html" %}
{% block title %}Settings{% endblock %}
{% block content %}
<div class="max-w-5xl space-y-6">
  <div>
    <h1 class="text-xl font-semibold mb-1">Settings</h1>
    <p class="text-sm text-slate-600">Investment thresholds are defined in
    <code>config.py</code>; this page stores <strong>dated deltas</strong> only.
    To change a value going forward, add an override with a later effective
    date — completed analyses keep the thresholds they ran under. Delete is
    for mistakes.</p>
  </div>

  <section>
    <h2 class="text-sm font-bold uppercase tracking-wider text-slate-500 mb-2">Add override</h2>
    <form method="post" class="flex flex-wrap gap-2 items-end">
      {% csrf_token %}
      <label class="text-xs text-slate-600">Setting
        {{ form.key }}
      </label>
      <label class="text-xs text-slate-600">Value
        {{ form.value }}
      </label>
      <label class="text-xs text-slate-600">Asset type
        {{ form.asset_type }}
      </label>
      <label class="text-xs text-slate-600">Effective
        {{ form.effective_date }}
      </label>
      <label class="text-xs text-slate-600">Note
        {{ form.note }}
      </label>
      <button type="submit" class="bg-accent-700 text-white text-sm px-3 py-1.5 rounded">Add</button>
    </form>
    {% if form.errors %}
    <div class="mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
      {{ form.non_field_errors }}{% for field in form %}{{ field.errors }}{% endfor %}
    </div>
    {% endif %}
    <p class="mt-1 text-xs text-slate-500">Percents as whole numbers (12 = 12%).
    Ranges as <code>low, high</code>.</p>
  </section>

  <section>
    <h2 class="text-sm font-bold uppercase tracking-wider text-slate-500 mb-2">Overrides</h2>
    <table class="w-full text-sm border-collapse">
      <thead>
        <tr class="text-left border-b border-slate-300 text-xs text-slate-600">
          <th class="py-1.5 pr-3">Setting</th>
          <th class="py-1.5 pr-3">Value</th>
          <th class="py-1.5 pr-3">Scope</th>
          <th class="py-1.5 pr-3">Effective</th>
          <th class="py-1.5 pr-3">Status</th>
          <th class="py-1.5 pr-3">Note</th>
          <th class="py-1.5"></th>
        </tr>
      </thead>
      <tbody>
        {% for o in overrides %}
        <tr class="border-b border-slate-100 {% if o.status == 'superseded' %}text-slate-400{% endif %}">
          <td class="py-1.5 pr-3 font-medium">{{ o.label }}
            <span class="block text-[11px] text-slate-400">{{ o.row.key }}</span></td>
          <td class="py-1.5 pr-3">{{ o.display_value }}</td>
          <td class="py-1.5 pr-3">{{ o.row.asset_type|default:"All" }}</td>
          <td class="py-1.5 pr-3 whitespace-nowrap">{{ o.row.effective_date|date:"Y-m-d" }}</td>
          <td class="py-1.5 pr-3">
            <span class="text-xs rounded px-1.5 py-0.5
              {% if o.status == 'active' %}bg-emerald-50 text-emerald-700
              {% elif o.status == 'scheduled' %}bg-amber-50 text-amber-700
              {% elif o.status == 'unknown key' %}bg-red-50 text-red-700
              {% else %}bg-slate-100 text-slate-500{% endif %}">{{ o.status }}</span>
          </td>
          <td class="py-1.5 pr-3 text-slate-500">{{ o.row.note }}</td>
          <td class="py-1.5 text-right">
            <form method="post" action="{% url 'override-delete' o.row.pk %}"
                  onsubmit="return confirm('Delete this override?')">
              {% csrf_token %}
              <button type="submit" class="text-xs text-red-600 underline">Delete</button>
            </form>
          </td>
        </tr>
        {% empty %}
        <tr><td colspan="7" class="py-4 text-slate-500">No overrides —
          analyses use the <code>config.py</code> defaults.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </section>

  <section>
    <div class="flex items-center gap-3 mb-2">
      <h2 class="text-sm font-bold uppercase tracking-wider text-slate-500">Effective values</h2>
      <form method="get" class="flex items-center gap-1 text-xs text-slate-600">
        <label for="id_preview_scope">for</label>
        <select name="asset_type" id="id_preview_scope" onchange="this.form.submit()"
                class="border border-slate-300 rounded px-1.5 py-0.5 text-xs">
          <option value="">All asset types</option>
          {% for a in asset_types %}<option value="{{ a }}" {% if a == selected_asset_type %}selected{% endif %}>{{ a }}</option>{% endfor %}
        </select>
      </form>
    </div>
    <div class="grid md:grid-cols-2 gap-x-8 gap-y-4">
      {% for group, entries in groups.items %}
      <div>
        <h3 class="text-xs font-semibold text-slate-700 mb-1">{{ group }}</h3>
        <table class="w-full text-sm border-collapse">
          <thead>
            <tr class="text-left border-b border-slate-200 text-xs text-slate-500">
              <th class="py-1 pr-3">Setting</th>
              <th class="py-1 pr-3 text-right">Default</th>
              <th class="py-1 text-right">Effective</th>
            </tr>
          </thead>
          <tbody>
            {% for e in entries %}
            <tr class="border-b border-slate-50">
              <td class="py-1 pr-3">{{ e.label }}</td>
              <td class="py-1 pr-3 text-right text-slate-500">{{ e.default }}</td>
              <td class="py-1 text-right {% if e.changed %}font-semibold text-accent-700{% endif %}">{{ e.effective }}</td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
      {% endfor %}
    </div>
  </section>
</div>
{% endblock %}
```

Style the form widgets in `ConfigOverrideForm.__init__` (append after the choices setup) so they match the page:

```python
        base = "border border-slate-300 rounded px-2 py-1 text-sm block"
        self.fields["key"].widget.attrs["class"] = base + " max-w-[22rem]"
        self.fields["value"].widget.attrs.update(
            {"class": base + " w-32", "placeholder": "12  or  1.4, 2.6"})
        self.fields["asset_type"].widget.attrs["class"] = base
        self.fields["effective_date"].widget.attrs["class"] = base
        self.fields["note"].widget.attrs.update(
            {"class": base + " w-44", "placeholder": "why"})
```

- [ ] **Step 5: Convert the Settings sidebar span to a link** (base.html lines 81–84, same pattern as Comps; note the `url_name` to match is `settings`).

- [ ] **Step 6: Rebuild Tailwind, run tests, full suite**

Run: `TAILWINDCSS_VERSION=v3.4.17 .venv/bin/tailwindcss -c tailwind.config.js -i static/src/input.css -o static/css/tw.css --minify && .venv/bin/python -m pytest tests/test_web_config.py -q 2>&1 | tail -2 && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -2`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add webapp/ static/css/tw.css tests/test_web_config.py
git commit -m "feat(web): settings page — dated, asset-scoped config override CRUD + effective-values preview"
```

### Task A7: UI passes + PR 5A

- [ ] **Step 1: UI passes** — Comps and Settings are NEW pages: run **two independent agents** (one layout/compaction pass, one adversarial density pass) over `comps.html`, `settings.html`, and the sidebar diff. Fix findings; never trade label/field visibility for density.
- [ ] **Step 2:** Standard-tier cycle: `git diff main` → ONE review pass (paste the diff; reviewer scoped to changed files) → repair critical/moderate findings → re-review only if any were found → commit → push → PR (body: Why → What → Tests `N passed (was 132)` → Test plan → Out of scope: cutover = PR 5B/5C) → CI green → squash-merge → delete branch.

---

# PR 5B — Pre-cutover schema hardening + real-Postgres CI gate (high-risk tier)

Every item here is a Postgres-only failure mode invisible on SQLite (the Phase 2/4 review carry-overs). This PR must merge **before** any Neon migration runs.

## File Structure

- Modify: `webapp/models.py` (`Deal.Meta.ordering` NULLs-last; `deal_id` max_length 200)
- Create: `webapp/migrations/0005_deal_ordering_dealid_width.py` (generated)
- Modify: `webapp/services.py` (worker filename truncation), `webapp/management/commands/import_deals.py` (state normalization, length guard)
- Create: `scripts/smoke_pg_django.py`
- Modify: `.github/workflows/test.yml` (PG smoke job + collectstatic gate)
- Test: append to `tests/test_web_deals.py` (round-trip drift guard, normalization)

### Task B1: Ordering, widths, write hardening

**Files:**
- Modify: `webapp/models.py`, `webapp/services.py`, `webapp/management/commands/import_deals.py`
- Create: `webapp/migrations/0005_*.py` (generated)
- Test: append to `tests/test_web_deals.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_web_deals.py`):

```python
@pytest.mark.django_db
def test_deal_ordering_null_dates_sort_last():
    from webapp.models import Deal

    dated = Deal.objects.create(deal_id="dated", property_name="Dated",
                                analysis_date=datetime.date(2026, 1, 1))
    undated = Deal.objects.create(deal_id="undated", property_name="Undated")
    ids = [d.pk for d in Deal.objects.all()]
    assert ids == [dated.pk, undated.pk]     # NULLs last on BOTH backends


@pytest.mark.django_db
def test_deal_id_supports_200_chars():
    from webapp.models import Deal

    slug = "s" * 200
    Deal.objects.create(deal_id=slug, property_name="Long")
    assert Deal.objects.filter(deal_id=slug).exists()


@pytest.mark.django_db
def test_import_deals_normalizes_state_and_skips_oversize_ids(tmp_path, settings):
    import json as jsonlib

    from django.core.management import call_command

    from webapp.models import Deal

    ok = tmp_path / "ok"
    ok.mkdir()
    (ok / "deal_meta.json").write_text(jsonlib.dumps(
        {"deal_id": "ok", "property_name": "OK", "state": "texas"}))
    huge = tmp_path / "huge"
    huge.mkdir()
    (huge / "deal_meta.json").write_text(jsonlib.dumps(
        {"deal_id": "x" * 250, "property_name": "Huge"}))
    settings.CIM_DEALS_DIR = str(tmp_path)

    call_command("import_deals")
    # "texas" is NOT truncated to the fabricated code "TE" — a non-2-letter
    # state imports as blank (visible gap) with a warning (review finding)
    assert Deal.objects.get(deal_id="ok").state == ""
    assert Deal.objects.count() == 1                       # oversize skipped
```

(Add `import datetime` to the test module imports if missing.)

- [ ] **Step 2: Run to verify failure** — ordering test fails on the current `["-analysis_date", ...]` SQLite behavior? No: on SQLite NULLs sort LAST under DESC already — the current suite can't see this bug, which is the point. The 200-char test fails on `max_length=120` validation only under `full_clean`; SQLite ignores VARCHAR widths. **Expected failures here: the import test (normalization not implemented). The other two document the contract and pass on SQLite either way — their real enforcement is the PG smoke (Task B2).** Run: `.venv/bin/python -m pytest tests/test_web_deals.py -q 2>&1 | tail -3`.

- [ ] **Step 3: Model changes** — in `webapp/models.py`:

```python
    deal_id = models.SlugField(max_length=200, unique=True)
```

```python
    class Meta:
        # F() ordering: NULL analysis_date sorts FIRST on Postgres under
        # plain "-analysis_date" but LAST on SQLite — pin nulls_last so
        # undated deals don't jump to the top at the Neon cutover.
        ordering = [models.F("analysis_date").desc(nulls_last=True),
                    "-created_at"]
```

Run: `.venv/bin/python manage.py makemigrations webapp` → 0005 with `AlterField` + `AlterModelOptions`.

- [ ] **Step 4: Worker truncation** — in `webapp/services.py::_analysis_worker`, wrap the three `os.path.basename(...)` filename writes on the AnalysisRun update and the two on `deal_updates` with `[:300]` (e.g. `memo_filename=os.path.basename(result.memo_path or "")[:300]`). Postgres fails hard where SQLite silently accepts; property-name-derived filenames stay well under 300 today, this is the checklist's defensive guarantee.

- [ ] **Step 5: `import_deals` normalization** — in `webapp/management/commands/import_deals.py`, add `import re` to the module imports, then inside the try block:

```python
                raw_state = (meta.get("state") or "").strip()
                # Validate, don't fabricate: "texas"[:2] would store the
                # fake code "TE" that then feeds the state filter and the
                # regional expense lookup. Blank + warn keeps the gap
                # visible (review finding).
                if re.fullmatch(r"[A-Za-z]{2}", raw_state):
                    state = raw_state.upper()
                else:
                    state = ""
                    if raw_state:
                        self.stderr.write(
                            f"{name}: state {raw_state!r} is not a 2-letter "
                            f"code — imported blank")
                if len(meta["deal_id"]) > 200:
                    skipped += 1
                    self.stderr.write(f"skipped {name}: deal_id longer than 200")
                    continue
```

and use `"state": state` in the `defaults` dict (replacing `meta.get("state") or ""`), plus truncate the two filename defaults: `"memo_filename": (meta.get("memo_path") or "")[:300]`, `"excel_filename": (meta.get("excel_path") or "")[:300]`.

- [ ] **Step 6: Round-trip drift guard** (append to `tests/test_web_deals.py`) — the no-drift CI test the Phase 2 review demanded when services absorbed the meta helpers:

```python
@pytest.mark.django_db
def test_build_deal_meta_import_deals_round_trip_no_drift(tmp_path, settings):
    """Every key build_deal_meta emits must be either imported onto the
    Deal row or on the explicit exempt list. A new meta key that isn't
    classified fails this test — that's the point."""
    import json as jsonlib

    from django.core.management import call_command

    from gui.deal_manager import build_deal_meta
    from gui.engine import AnalysisResult
    from tests.test_web_runs import _sample_cim
    from webapp.models import Deal

    cim = _sample_cim()
    result = AnalysisResult(pdf_path="x.pdf")
    result.cim_data = cim
    result.memo_path = "/tmp/Expo_memo.docx"
    result.excel_path = "/tmp/Expo_model.xlsx"
    result.max_offer = {"max_price": 3_100_000.0}
    result.gate_summary = {"recommendation": "PURSUE"}
    folder = tmp_path / "expo"
    folder.mkdir()
    meta = build_deal_meta(cim, result, str(folder), input_files=["expo.pdf"])
    meta["deal_id"] = "expo"
    (folder / "deal_meta.json").write_text(jsonlib.dumps(meta, default=str))
    settings.CIM_DEALS_DIR = str(tmp_path)

    call_command("import_deals")
    d = Deal.objects.get(deal_id="expo")
    IMPORTED = {"deal_id", "property_name", "city", "state", "asset_type",
                "nrsf", "acreage", "asking_price", "estimated_fair_value",
                "recommendation", "analysis_date", "memo_path", "excel_path",
                "input_files"}
    # LITERAL by design: build_deal_meta today emits exactly the keys in
    # IMPORTED (verified at plan-review time). If it ever grows a key,
    # this assertion fires and the new key must be classified — either
    # mapped in import_deals + IMPORTED, or added here as exempt.
    EXEMPT_DISPLAY_ONLY = set()
    assert d.property_name == meta["property_name"]
    assert d.asking_price == meta["asking_price"]
    assert d.memo_filename == meta["memo_path"]
    assert d.input_files == meta["input_files"]
    unclassified = set(meta) - IMPORTED - EXEMPT_DISPLAY_ONLY
    assert unclassified == set(), (
        f"build_deal_meta grew keys import_deals doesn't map: {unclassified}")
```

(The review round killed an earlier computed-set version of this guard — `set(meta) - IMPORTED` as the exempt set makes the final assertion true by algebra forever. The empty-set literal above is the real state of `build_deal_meta` today; keep it a literal.)

- [ ] **Step 7: Run the file + full suite, and prove the guard is a literal** — `.venv/bin/python -m pytest tests/test_web_deals.py -q 2>&1 | tail -2 && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -2` → all pass; then `grep -n "EXEMPT_DISPLAY_ONLY = set(meta)" tests/` → must return nothing (a computed exempt set is the tautology the review killed).

- [ ] **Step 8: Commit**

```bash
git add webapp/models.py webapp/migrations/ webapp/services.py webapp/management/commands/import_deals.py tests/test_web_deals.py
git commit -m "fix(web): Postgres-safe ordering + widths — nulls_last, deal_id 200, filename/state write hardening, meta round-trip guard"
```

### Task B2: Real-Postgres smoke script + CI job

**Files:**
- Create: `scripts/smoke_pg_django.py`
- Modify: `.github/workflows/test.yml`

**Interfaces:**
- Consumes: `DATABASE_URL` env (postgres). Models + services from this repo.
- Produces: `python scripts/smoke_pg_django.py` exiting non-zero on any failure; CI job `smoke-pg` with a `postgres:16` service container; collectstatic added to the `test` job (real build gate).

- [ ] **Step 1: Create `scripts/smoke_pg_django.py`** (managertools shape, CIM content — migrate from zero, drift check, ORM exercise of every PG-only failure mode this repo has):

```python
#!/usr/bin/env python
"""Real-Postgres smoke: the failure modes SQLite can't see.

Run against a THROWAWAY database (CI service container):
    DATABASE_URL=postgresql://smoke:smoke@localhost:5432/smoke \
        python scripts/smoke_pg_django.py

Steps: migrate from zero → makemigrations drift check → ORM exercise
(NULLs-last ordering, length limits at the boundary, JSONB NaN
rejection + json_safe acceptance, ConfigOverride resolution, unique
constraints). Any failure exits non-zero.
"""
import datetime
import os
import subprocess
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _bail(msg):
    print(f"SMOKE FAIL: {msg}")
    sys.exit(1)


def _step(msg):
    print(f"── {msg}")


def _run_manage(*args):
    proc = subprocess.run(
        [sys.executable, "manage.py", *args],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return proc


def main():
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url.startswith("postgres"):
        _bail("DATABASE_URL must point at a throwaway Postgres")
    # HARD GUARD: this script DELETES rows and the Neon prod URL also
    # starts with postgresql:// — a docstring warning is not a check
    # (review finding, critical). Local hosts or a DB literally named
    # "smoke" only; SMOKE_ALLOW_REMOTE=1 is the explicit escape hatch
    # for a throwaway Neon branch.
    parsed = urlparse(db_url)
    if (parsed.hostname not in ("localhost", "127.0.0.1")
            and (parsed.path or "").lstrip("/") != "smoke"
            and os.environ.get("SMOKE_ALLOW_REMOTE") != "1"):
        _bail(f"refusing remote DATABASE_URL host {parsed.hostname!r} — this "
              "script deletes data; set SMOKE_ALLOW_REMOTE=1 only against a "
              "throwaway branch")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cimweb.settings")
    os.environ.setdefault("DJANGO_SECRET_KEY", "smoke-only-key")
    os.environ.setdefault("DEBUG", "True")

    _step("migrate from zero")
    proc = _run_manage("migrate", "--no-input")
    if proc.returncode != 0:
        _bail(f"migrate failed:\n{proc.stderr[-2000:]}")

    _step("makemigrations drift check")
    proc = _run_manage("makemigrations", "--check", "--dry-run")
    if proc.returncode != 0:
        _bail("model/migration drift (makemigrations --check)")

    import django
    django.setup()

    import json

    from django.db import IntegrityError, connection, transaction

    from webapp.models import AnalysisRun, ConfigOverride, Deal
    from webapp.services import json_safe, resolve_config_overrides

    Deal.objects.all().delete()
    ConfigOverride.objects.all().delete()

    _step("NULL analysis_date sorts last (F ordering)")
    dated = Deal.objects.create(deal_id="smoke-dated", property_name="D",
                                analysis_date=datetime.date(2026, 1, 1))
    undated = Deal.objects.create(deal_id="smoke-undated", property_name="U")
    order = [d.pk for d in Deal.objects.all()]
    if order != [dated.pk, undated.pk]:
        _bail(f"ordering put NULL first: {order}")

    _step("length boundaries: deal_id 200, filenames 300, state 2")
    big = Deal.objects.create(deal_id="s" * 200, property_name="Long",
                              state="TX", memo_filename="m" * 300,
                              excel_filename="e" * 300)
    run = AnalysisRun.objects.create(deal=big, memo_filename="m" * 300,
                                     excel_filename="e" * 300,
                                     template_filename="t" * 300)
    try:
        with transaction.atomic():
            Deal.objects.create(deal_id="smoke-overflow", property_name="X",
                                state="TEXAS")
        _bail("state VARCHAR(2) accepted 5 chars — not Postgres?")
    except Exception as e:
        if "value too long" not in str(e).lower():
            _bail(f"unexpected state-overflow error: {e!r}")

    _step("JSONB rejects raw NaN; json_safe payload inserts")
    try:
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE webapp_analysisrun SET result_json = %s "
                    "WHERE id = %s",
                    [json.dumps({"irr": float("nan")}), run.pk])
        _bail("Postgres accepted a NaN JSON literal — guard is meaningless")
    except Exception as e:
        # Only the JSON-syntax rejection proves the point; a renamed
        # table or dead connection must not silently "pass" (review
        # finding: bare except made this step tautological under drift).
        msg = str(e).lower()
        if not ("json" in msg or "token" in msg or "invalid input" in msg):
            _bail(f"NaN probe failed for the wrong reason: {e!r}")
    run.result_json = json_safe({"irr": float("nan"), "moic": 1.6})
    run.save(update_fields=["result_json"])
    run.refresh_from_db()
    if run.result_json != {"irr": None, "moic": 1.6}:
        _bail(f"json_safe payload mangled: {run.result_json}")

    _step("unique deal_id enforced")
    try:
        with transaction.atomic():
            Deal.objects.create(deal_id="smoke-dated", property_name="Dup")
        _bail("duplicate deal_id accepted")
    except IntegrityError:
        pass

    _step("ConfigOverride resolution on PG")
    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.12,
                                  effective_date=datetime.date(2026, 1, 1))
    ConfigOverride.objects.create(key="GATES.min_irr_5yr", value=0.14,
                                  asset_type="Boat & RV Storage",
                                  effective_date=datetime.date(2025, 6, 1))
    got = resolve_config_overrides("Boat & RV Storage",
                                   datetime.date(2026, 7, 27))
    if got != {"GATES.min_irr_5yr": 0.14}:
        _bail(f"resolution wrong on PG: {got}")

    print("SMOKE OK: all Postgres-only failure modes exercised")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify locally against throwaway Docker Postgres** (the operator rule: validate risky DDL from a blank DB before trusting CI):

Run:
```bash
docker run -d --name smoke-pg -e POSTGRES_USER=smoke -e POSTGRES_PASSWORD=smoke -e POSTGRES_DB=smoke -p 5433:5432 postgres:16
sleep 5
DATABASE_URL=postgresql://smoke:smoke@localhost:5433/smoke .venv/bin/python scripts/smoke_pg_django.py
docker rm -f smoke-pg
```
Expected: `SMOKE OK` (and the migrate-from-zero proves 0001–0005 apply cleanly on PG).

- [ ] **Step 3: Extend `.github/workflows/test.yml`** — add to the `test` job, after the Django checks step:

```yaml
      - name: Collectstatic (whitenoise manifest build gate)
        run: python manage.py collectstatic --noinput
```

and add a new job after `test`:

```yaml
  smoke-pg:
    runs-on: ubuntu-latest
    needs: test
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: smoke
          POSTGRES_PASSWORD: smoke
          POSTGRES_DB: smoke
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready --health-interval 5s
          --health-timeout 5s --health-retries 10
    env:
      DATABASE_URL: postgresql://smoke:smoke@localhost:5432/smoke
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: python scripts/smoke_pg_django.py
```

- [ ] **Step 4: Full local gate** — `.venv/bin/python manage.py check && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -2` → all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke_pg_django.py .github/workflows/test.yml
git commit -m "ci: real-Postgres smoke (migrate-from-zero, NULLs, lengths, JSONB NaN, resolution) + collectstatic gate"
```

### Task B3: PR 5B (high-risk cycle)

- [ ] **Step 1:** Full canonical cycle: `git diff main` → code-review → repair → re-review until clean → push → PR (body names each pre-cutover checklist item it closes; Tests `N passed (was <PR 5A count>)`) → CI green **including the new smoke-pg job** + posted review → squash-merge → delete branch. This PR is migration-bearing but targets a DB that doesn't exist yet (Neon comes at cutover), so the schema-before-code ordering rule is trivially satisfied; the throwaway-Docker validation in B2 Step 2 stands in for the prod dry-run gate.

---

# PR 5C — Engine relocation, gui/ retirement, render.yaml, docs (high-risk tier)

## File Structure

- Move: `gui/engine.py` → `engine.py` (`git mv`)
- Modify: `webapp/services.py` (imports; absorb deal_manager helpers), `tests/test_deal_manager.py`, `tests/test_web_analyze.py`, `tests/test_web_runs.py`, `tests/test_web_config.py`, `webapp/forms.py`, `webapp/views.py` (ASSET_TYPES import)
- Delete: `gui/` (everything else), `Dockerfile`, `docker-compose.yml`, `railway.json`, `deploy/`
- Modify: `requirements.txt` (remove `streamlit==1.55.0`), `.github/workflows/test.yml` (remove docker job)
- Create: `render.yaml`
- Modify: `DEPLOY.md` (rewrite), `CLAUDE.md`, `README.md`, `ROADMAP.md`

### Task C1: Move the engine, absorb the deal_manager helpers

**Files:**
- Move: `gui/engine.py` → `engine.py`
- Modify: `webapp/services.py`, `tests/test_deal_manager.py`, `tests/test_web_analyze.py`, `tests/test_web_runs.py`, `tests/test_web_config.py`, `webapp/forms.py`, `webapp/views.py`

**Interfaces:**
- Produces: root module `engine` exporting `AnalysisResult`, `extract_pdf_data`, `run_analysis`, `run_full_pipeline`, `_apply_overrides` (same signatures — the file moves, no line inside changes); `webapp/services.py` physically owning `ASSET_TYPES`, `sanitize_name`, `detect_asset_type`, `build_deal_meta`, `write_deal_meta`, `read_deal_meta` (bodies moved verbatim from `gui/deal_manager.py`; `create_deal_folder`, `save_uploaded_file`, `list_all_deals` die with the Streamlit UI).

- [ ] **Step 1: Move the engine**

```bash
git mv gui/engine.py engine.py
```

- [ ] **Step 2: Absorb the helpers** — copy `sanitize_name`, `detect_asset_type` (+ the `ASSET_TYPES` tuple), `build_deal_meta`, `write_deal_meta`, `read_deal_meta` (and any module constant they reference, e.g. the meta filename constant) from `gui/deal_manager.py` verbatim into `webapp/services.py` (new section `# ── Deal folder / meta helpers (absorbed from gui/deal_manager) ──` placed above their first use). Then:
  - `webapp/services.py`: delete the `from gui.deal_manager import (...)` line; change `from gui.engine import (...)` → `from engine import (...)`.
  - `webapp/forms.py` + `webapp/views.py`: `from gui.deal_manager import ASSET_TYPES` → `from webapp.services import ASSET_TYPES` (adjust the lazy import inside `settings_page` too).
  - `tests/test_deal_manager.py`: `from gui.deal_manager import ...` → `from webapp.services import ...` (file keeps its name and cases — they now guard the absorbed copies).
  - `tests/test_web_runs.py`, `tests/test_web_analyze.py`, `tests/test_web_config.py`: every `gui.engine` / `gui.deal_manager` reference → `engine` / `webapp.services` (grep: `grep -rn "gui\." tests/ webapp/`).

- [ ] **Step 3: Prove nothing references gui/**

Run: `grep -rn "from gui\|import gui\|gui\.engine\|gui\.deal_manager" --include="*.py" . --exclude-dir=.venv --exclude-dir=gui --exclude-dir=__pycache__`
Expected: zero hits.

- [ ] **Step 4: Full suite** — `.venv/bin/python -m pytest tests/ -q 2>&1 | tail -2` → all pass, same count.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: gui/engine.py → engine.py; deal-folder helpers absorbed into webapp/services"
```

### Task C2: Delete the Streamlit stack + Docker/Railway artifacts

- [ ] **Step 1: Delete**

```bash
git rm -r gui/ deploy/ Dockerfile docker-compose.yml railway.json
```

- [ ] **Step 2: Remove `streamlit==1.55.0` from `requirements.txt`** (dependency count net −1; `pdfplumber`/`beautifulsoup4`/etc. stay — the pipeline uses them). Reinstall to prove the resolver is clean: `.venv/bin/python -m pip install -r requirements.txt`.

- [ ] **Step 3: Remove the `docker` job from `.github/workflows/test.yml`** (the whole job block; `test` + `smoke-pg` remain — they exercise the real build via collectstatic + migrate-from-zero, which is what Render's buildCommand runs).

- [ ] **Step 4: Full suite + checks**

Run: `.venv/bin/python manage.py check && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -2`
Expected: clean; all pass. Also `grep -rn "streamlit" --include="*.py" . --exclude-dir=.venv` → zero hits.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: retire Streamlit GUI, Docker, and Railway artifacts (Render is the deploy path)"
```

### Task C3: `render.yaml` + disk-aware health

**Files:**
- Create: `render.yaml`
- Modify: `webapp/views.py` (`health`), `tests/test_web_auth.py`

- [ ] **Step 1: Create `render.yaml`** at repo root (managertools shape + persistent disk):

```yaml
# Render Blueprint — CIM Analyst (Django + gunicorn, Neon Postgres,
# persistent disk for deal folders + comp DB).
# Managertools-proven shape; disk block is the one addition (deal PDFs,
# generated memos/models, and the SQLite comp DB live on /data).
# NOTE: attaching a disk disables zero-downtime deploys — fine at 1 user.
services:
  - type: web
    name: cim-analyst
    runtime: python
    plan: starter
    region: oregon
    branch: main
    autoDeploy: true
    buildCommand: pip install -r requirements.txt && TAILWINDCSS_VERSION=v3.4.17 tailwindcss -c tailwind.config.js -i static/src/input.css -o static/css/tw.css --minify && python manage.py collectstatic --noinput && python manage.py migrate --no-input
    startCommand: gunicorn cimweb.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
    healthCheckPath: /health/
    disk:
      name: cim-data
      mountPath: /data
      sizeGB: 1
    envVars:
      - key: DATABASE_URL
        sync: false          # Neon pooled connection string
      - key: DJANGO_SECRET_KEY
        generateValue: true
      - key: ALLOWED_EMAILS
        value: terickson@marathoncre.com
      - key: ALLOWED_HOSTS
        sync: false          # cim-analyst.onrender.com (exact host after first deploy)
      - key: CSRF_TRUSTED_ORIGINS
        sync: false          # https://<same host>
      - key: CENSUS_API_KEY
        sync: false
      - key: CIM_DEALS_DIR
        value: /data/deals
      - key: COMP_DB_PATH
        value: /data/cim_comps.db
      - key: CIM_OVERRIDES_DIR
        value: /data/overrides
      - key: PYTHON_VERSION
        value: "3.12.6"
```

- [ ] **Step 2: Make `/health/` disk-aware** (Design Decision 14 — a missing disk must not stay invisible behind a green `SELECT 1`). In `webapp/views.py::health`, after the DB probe:

```python
    # Disk probe: only when the deploy declares file locations via env
    # (Render). Dev/CI leave these unset and skip it. Without this, an
    # unmounted disk is invisible — CompDatabase() fabricates an empty
    # comps DB and uploads land on the ephemeral container FS.
    disk_ok = True
    if os.environ.get("CIM_DEALS_DIR"):
        disk_ok = os.path.isdir(settings.CIM_DEALS_DIR)
    if disk_ok and os.environ.get("COMP_DB_PATH"):
        disk_ok = os.path.exists(os.environ["COMP_DB_PATH"])
    if not disk_ok:
        logger.error("health check: data disk missing or env misrouted")
```

then fold it into the response (replacing the current two lines):

```python
    ok = db_ok and disk_ok
    return JsonResponse(
        {"status": "ok" if ok else "degraded", "db": db_ok,
         "disk": disk_ok, "git_sha": sha[:12]},
        status=200 if ok else 503,
    )
```

Append to `tests/test_web_auth.py`:

```python
@pytest.mark.django_db
def test_health_reports_missing_disk(client, monkeypatch, settings):
    monkeypatch.setenv("CIM_DEALS_DIR", "/data/deals")
    settings.CIM_DEALS_DIR = "/nonexistent/deals"
    resp = client.get("/health/")
    assert resp.status_code == 503
    assert resp.json()["disk"] is False
    assert resp.json()["db"] is True


@pytest.mark.django_db
def test_health_disk_probe_skipped_without_env(client):
    resp = client.get("/health/")
    assert resp.status_code == 200
    assert resp.json()["disk"] is True
```

Run: `.venv/bin/python -m pytest tests/test_web_auth.py -q 2>&1 | tail -2` → all pass.

- [ ] **Step 3: Sanity-check the YAML parses**

Run: `.venv/bin/python -c "import yaml, sys; yaml.safe_load(open('render.yaml')); print('yaml ok')" 2>/dev/null || .venv/bin/python -c "import json; print('pyyaml absent — visually verified')"`
(pyyaml ships as a transitive dep in some environments; if absent, eyeball it — the first deploy is the real validator, and gunicorn/timeout/disk keys are copied from the managertools file that runs in prod today.)

- [ ] **Step 4: Commit**

```bash
git add render.yaml webapp/views.py tests/test_web_auth.py
git commit -m "feat(deploy): Render blueprint (1GB disk at /data) + disk-aware /health"
```

### Task C4: Documentation rewrite

- [ ] **Step 1: Rewrite `DEPLOY.md`** — replace the stale Streamlit/Railway 4-phase doc entirely with:
  1. **Architecture** — Render web service (gunicorn, 2 workers × 4 threads), Neon Postgres (deal/run/override rows), 1 GB Render disk at `/data` (deal folders, generated outputs, comp SQLite DB, CIM overrides dir), whitenoise static, `/health/` reporting `git_sha` + `db`.
  2. **Environment table** — every env var in render.yaml with purpose and where its value comes from.
  3. **Cutover runbook** — the ordered list from "Cutover runbook" below, copied verbatim.
  4. **Data safety** — Neon PITR covers the DB; Render disk daily snapshots (7-day retention) cover files; quarterly restore-drill reminder; comp DB is also regenerable from deal re-runs.
  5. **Rollback** — Railway service keeps running the last Streamlit image until manually deleted (manual deploys = merging this PR changes nothing); post-cutover rollback = `/rollback` on main + Render auto-deploys the revert.
- [ ] **Step 2: Update `CLAUDE.md`** (project) — Architecture tree: `gui/` block replaced by `engine.py  # Analysis orchestration (extract_pdf_data / run_analysis) — the web↔pipeline boundary` + `webapp/` line; "How to run" gains: `python manage.py runserver` (web) alongside `python run.py` (CLI); note the settings editor stores dated deltas (`ConfigOverride`) and the comps browser reads `data/cim_comps.db` read-only.
- [ ] **Step 3: Update `README.md`** — developer setup: runserver + migrate + bootstrap_operator replace any Streamlit instructions.
- [ ] **Step 4: Update `ROADMAP.md`** — check off Web Deployment items (Railway/VPS line becomes "Render (done)"); under "Not Building (By Design)" add: "Batch analysis (multi-PDF) — retired with the Streamlit GUI; the web flow underwrites one deal at a time. Revisit only if a real multi-CIM day actually hurts." Move the completed Phase 1–5 web items into Completed.
- [ ] **Step 5: Commit**

```bash
git add DEPLOY.md CLAUDE.md README.md ROADMAP.md
git commit -m "docs: Render deploy runbook, architecture + roadmap updated for the Django cutover"
```

### Task C5: PR 5C (high-risk cycle)

- [ ] **Step 1:** Full canonical cycle: diff → code-review → repair → re-review until clean → push → PR → CI green (test + smoke-pg — no docker job anymore) + posted review → squash-merge → delete branch. PR body must name: the batch-analysis retirement (decision 9), the Docker-path deletion (decision 10), and that merging changes nothing in prod (Railway deploys are manual) — the cutover is the runbook below.

---

## Cutover runbook (after PR 5C merges — operator gates marked ⚑)

Ship-dark ends here. Railway keeps serving Streamlit until step 8; nothing below is destructive until step 9.

1. ⚑ **Neon**: create project `cim-analyst` (Postgres 16+); copy the **pooled** connection string. (Free tier is fine at this scale; note autosuspend means the first request after idle takes ~1s extra.)
2. ⚑ **Render**: New → Blueprint → point at the GitHub repo; Render reads `render.yaml`. Paste `sync: false` values: `DATABASE_URL` (from step 1), `CENSUS_API_KEY` (from the current Railway env), `ALLOWED_HOSTS` = the assigned host (e.g. `cim-analyst.onrender.com`), `CSRF_TRUSTED_ORIGINS` = `https://<that host>`. First deploy runs migrate against Neon automatically.
3. **Verify boot**: `curl https://<host>/health/` → `{"status": "ok", "db": true, "disk": true, "git_sha": "<HEAD>"}`. The git_sha must match origin/main HEAD; `disk: true` proves the mount and env routing before any data lands on it.
4. **Operator account** — Render Shell tab on the service:
   ```bash
   OPERATOR_PASSWORD='<one-time password from your manager>' python manage.py bootstrap_operator
   mkdir -p /data/deals /data/overrides
   ```
5. ⚑ **Data transfer** — use `scp` in SFTP mode, the path Render's disk docs prescribe (rsync needs a remote rsync binary the python runtime doesn't guarantee — review finding). The service page shows the ssh address:
   ```bash
   scp -s -r deals/ <ssh-address>:/data/deals/
   scp -s data/cim_comps.db <ssh-address>:/data/cim_comps.db
   scp -s -r overrides/ <ssh-address>:/data/overrides/
   ```
   Fallback if scp misbehaves: `tar cz deals data/cim_comps.db overrides | ssh <ssh-address> "tar xz -C /data --strip-components=0"` then move `data/cim_comps.db` → `/data/cim_comps.db` in the Render shell. Verify once with a single small file BEFORE cutover day.
6. **Import + verify data** — Render Shell: `python manage.py import_deals` → imported count matches the folder count; then in the browser: log in → Deal Pipeline shows the legacy deal(s) with downloads → Comps shows 9 rows → Settings loads.
7. **Full-path verification**: upload a real CIM → extraction completes → assumptions → Save & Run → progress → four result tabs → all three downloads open. Add a test override on Settings (e.g. Solver Target IRR 12%), re-run the deal, confirm the run's gates moved and `applied_overrides` recorded it (visible via `/admin/` or the next results header), then delete the test override.
8. ⚑ **Phase gate (operator)**: use the app for a real workflow once. Only after that:
9. ⚑ **Retire Railway**: delete the CIM_Analyst service (and its volume) in the Railway dashboard.
10. **Bookkeeping**: update the deploy table row in `~/.claude/CLAUDE.md` → `| CIM_Analyst | Render | push to main | /health/ |`; set a quarterly calendar reminder for the restore drill (Neon PITR + disk snapshot).

## Verification (end-to-end)

- Per PR: pytest green + `manage.py check` + `makemigrations --check` + collectstatic (from 5B) + smoke-pg (from 5B) in CI; UI PRs carry both design passes; high-risk PRs (5B, 5C) get the full review cycle with a posted review.
- Phase gates: after 5A — a global override visibly changes the assumptions editor baseline and a run's gate outcome, and the run row carries `applied_overrides`; after 5B — smoke-pg green on a blank postgres:16 (CI) AND the local throwaway-Docker run; after 5C — cutover runbook completes through step 8 with all verifications passing, then Railway is deleted.
- The suite count only rises: 132 → ~160 across the three PRs (exact counts recorded per PR body as `N passed (was M)`).

## Self-Review (performed at write time)

1. **Spec coverage:** master-plan Phase 5 row → comps browser (A5), settings editor with the 2026-07-25 operator directive (A1–A4, A6), render.yaml + Neon + persistent disk (C3, runbook), prod deploy + deal import (runbook 5–7), retire gui/Streamlit/Railway (C2, runbook 9), engine move (C1), docs/CLAUDE.md updates (C4). Pre-cutover checklist all five items → B1 (ordering, deal_id length, state validation, filename truncation, round-trip drift test) + B2 enforces them on real PG.
2. **Placeholder scan:** clean — the review round replaced the one computed-then-literalize marker (B1's drift guard) with the verified literal `EXEMPT_DISPLAY_ONLY = set()`, and B1 Step 7 greps for regressions to the computed form. Everything else is literal code.
3. **Type consistency:** `ConfigOverride.value` stores canonical units ([low,high] lists / decimal scalars / ints) — `parse_override_value` produces them, `format_override_value` and `build_config_patch` consume them, `_merge_patch` tuple-ifies lists exactly where config stores tuples. `build_initial(deal, eff)` / `build_overrides(cleaned, post, deal, eff)` signatures match between A4's forms change and its views call-site update. `ASSET_TYPES` import path changes once (A1: gui.deal_manager → C1: webapp.services) and C1 lists every importer. `applied_overrides` schema `{"config", "assumptions"}` is written in A3 and asserted in A3's test.
