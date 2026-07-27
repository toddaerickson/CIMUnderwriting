# Phase 4 — AnalysisRun, Threaded Run + Poll, Results Pages, Downloads: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From a deal with an extraction snapshot + saved assumptions, run the full analysis pipeline in a background thread with HTMX progress polling, persist results as an `AnalysisRun` row, render Summary/Returns/Financials/Risks results pages, serve .docx/.xlsx/.xlsm downloads, and make the Deal detail page real — replacing the Streamlit Upload & Analyze results half and its four results components.

**Architecture:** One PR, standard tier, UI passes required (new pages → two independent agents). A POST to `/deals/<pk>/run/` creates a fresh `AnalysisRun` row and spawns a daemon thread (inline in tests via `ANALYSIS_USE_THREAD=False`). The worker rehydrates `CIMData` from `Deal.cim_json`, applies `assumption_overrides` (CIM deltas via the engine's `_apply_overrides`; scenario/VA deltas via existing `run_analysis` params; replacement-cost deltas via in-place config-dict patching under a module lock; solver target via a new additive `solver_target_irr` engine param), runs `gui.engine.run_analysis(output_dir=deal.deal_dir)`, writes `deal_meta.json` (deal_id = the row's slug, per the Phase 3 decision record), refreshes the Deal row, and stores a JSON-sanitized result payload on the run row. Deal detail (`/deals/<pk>/`) is a server-rendered page with `?tab=` switching between four section templates; downloads stream from the deal folder via `FileResponse` with a path-containment guard.

**Tech Stack:** Existing Phase 1–3 stack (Django 5.1, django-htmx, vendored htmx, compiled Tailwind 3.4.17). No new dependencies.

## Global Constraints

- Analysis pipeline read-only: no edits to `analysis/`, `model/`, `output/`, `extract/`, `config.py`, `run.py`. **One deliberate exception this phase:** `gui/engine.py::run_analysis` gains an optional `solver_target_irr: float = None` parameter (additive; default `None` preserves Streamlit/CLI behavior exactly). Rationale: `model/solver.py` binds `SOLVER_TARGET_IRR` as a function-default at import time, so no config patching can ever reach it — the parameter is the only non-monkeypatch seam, and engine is the designated web↔pipeline boundary (it survives Phase 5 as root `engine.py`).
- `analysis/physical.py` does `from config import REPLACEMENT_COST` (object binding at import). Per-deal RC overrides therefore mutate `config.REPLACEMENT_COST` **in place** (`.update()` / `.clear()`) inside a context manager, serialized by a module-level `threading.Lock` held for the whole analysis run. Never rebind `config.REPLACEMENT_COST` to a new dict — importers would keep the old object.
- New run = new `AnalysisRun` row. A worker writes only to its own row (pk-scoped updates), so no CAS stamp is needed (unlike extraction). "One run at a time per deal" is enforced at the view, not the worker.
- `result_json` must survive Postgres JSONB at Phase 5 cutover: `npf.irr` returns NaN when cash flows never turn positive, and `json.dumps(nan)` produces invalid JSON that Postgres rejects (SQLite accepts it silently — invisible until cutover). Every payload goes through `services.json_safe()` (NaN/inf → None, Enum → value, tuple → list, unknown objects → str).
- Single source of truth: `deal_meta.json` is written via `gui.deal_manager.build_deal_meta` + `write_deal_meta` (imported, not copied), then `meta["deal_id"]` is overwritten with the row's `deal_id` — never recomputed from property name (Phase 3 decision #2).
- Money/measure fields stay floats. Percent values in `result_json` stay decimals; ×100 formatting happens only in `webapp/results.py` display helpers.
- Every new page needs both UI passes; these are NEW pages → **two independent agents** (layout/compaction, then adversarial density).
- Existing 101 tests stay green; `makemigrations --check` CI gate means the 0003 migration commits with the model change.
- Tailwind rebuilds carry `TAILWINDCSS_VERSION=v3.4.17`.
- Ship dark: deployed Streamlit app untouched (engine change is default-inert).
- File-count justification: `webapp/results.py` (pure result_json→display-context builders; keeps views.py from doubling — Phase 3 plan explicitly deferred "Phase 4 may split it"), 6 templates (deal_detail + run-status partial + one per tab: each tab is a distinct layout a reviewer evaluates separately), 1 generated migration, 1 test file. No new dependencies.

## Design Decisions (locked)

1. **Run history is append-only.** Every "Run Analysis" creates a new `AnalysisRun`; the detail page shows the latest `done` run's results and the latest run's status. A stale thread finishing late writes to its own (older) row — harmless by construction. No delete UI (admin covers it).
2. **Views guard, workers don't.** `deal_run` refuses when the deal has no snapshot or the latest run is still `running` (and not timed out, `ANALYSIS_TIMEOUT_SECONDS = 300`). The worker assumes a valid deal.
3. **Tab switching is server-side links** (`?tab=summary|returns|financials|risks`), no JS state. Poll partial (`_run_status.html`) follows the Phase 3 `_extract_status.html` pattern exactly: re-requests itself via `hx-trigger="load delay:2s"`, terminal states lack `hx-trigger`, `done` → `HttpResponseClientRedirect` to the detail page.
4. **Progress is best-effort row updates.** The engine's `progress(step, total, msg)` callback does a pk-scoped `.update()` on the run row; the poll partial renders a fraction bar + message. No SSE, no channels.
5. **Downloads resolve filename → latest done run's field, falling back to the Deal row's field** (imported legacy deals have `memo_filename`/`excel_filename` from `deal_meta.json` but no runs; they get downloads but no Run button — no snapshot to run from). `template` has no Deal-row fallback (legacy meta never recorded it). Path guard mirrors `deal_discard`: realpath containment inside `CIM_DEALS_DIR` + `os.path.basename()` on the stored filename.
6. **Deal list rows link to the detail page** (all deals, not just extracted ones); the detail page links onward to the assumptions editor. The assumptions page gains a second submit button ("Save & Run Analysis") that saves overrides then starts a run.
7. **Enrichment source tags are absent on web runs** (accepted): the extraction snapshot already contains enriched *values* (enrichment ran during Phase 3 extract and mutated `cim_data` before serialization), but the `source_log` object was never persisted, so `evaluate_gates` gets `source_log={}` and gate rows lose their "source" annotation. Documented here + in the PR body; revisit only if the annotation is missed in practice.

## File Structure

- Modify: `webapp/models.py` (add `AnalysisRun`)
- Create: `webapp/migrations/0003_analysisrun.py` (generated)
- Modify: `webapp/services.py` (`json_safe`, `_patched_replacement_cost`, `start_analysis`, `_analysis_worker`)
- Modify: `gui/engine.py` (additive `solver_target_irr` param on `run_analysis`)
- Create: `webapp/results.py` (display-context builders over `result_json`)
- Modify: `webapp/views.py` (`deal_detail`, `deal_run`, `run_status`, `deal_download`, assumptions Save-&-Run branch)
- Modify: `webapp/urls.py`
- Create: `webapp/templates/webapp/deal_detail.html`, `_run_status.html`, `_tab_summary.html`, `_tab_returns.html`, `_tab_financials.html`, `_tab_risks.html`
- Modify: `webapp/templates/webapp/deal_list.html` (rows link to detail), `webapp/templates/webapp/analyze_dupes.html` (Open → detail), `webapp/templates/webapp/assumptions.html` (Save & Run button + detail breadcrumb)
- Modify: `cimweb/settings_test.py` (`ANALYSIS_USE_THREAD = False`)
- Modify: `static/css/tw.css` (rebuilt)
- Test: `tests/test_web_runs.py`

---

### Task 1: AnalysisRun model + JSON sanitizer

**Files:**
- Modify: `webapp/models.py`
- Create: `webapp/migrations/0003_analysisrun.py` (generated)
- Modify: `webapp/services.py`
- Test: `tests/test_web_runs.py`

**Interfaces:**
- Produces: `webapp.models.AnalysisRun` (fields below; `deal.runs` related manager, newest first); `services.json_safe(obj) -> JSON-safe object`; `services.ANALYSIS_TIMEOUT_SECONDS = 300`.

- [ ] **Step 1: Write the failing tests** — new file `tests/test_web_runs.py`:

```python
"""Phase 4: AnalysisRun, background analysis, results pages, downloads."""
import json
import os

import pytest


@pytest.fixture
def operator(client, django_user_model):
    user = django_user_model.objects.create_user(username="op", password="x")
    client.force_login(user)
    return user


@pytest.fixture
def deals_dir(tmp_path, settings):
    d = tmp_path / "deals"
    d.mkdir()
    settings.CIM_DEALS_DIR = str(d)
    return d


def _sample_cim():
    from extract.parser import CIMData, UnitType
    return CIMData(
        property_name="Expo Storage", city="Belton", state="TX",
        nrsf=45000.0, total_units=350, physical_occupancy=0.92,
        economic_occupancy=0.78, asking_price=3_500_000.0,
        ttm_noi=250_000.0, ttm_egr=420_000.0, acreage=5.2,
        unit_mix=[UnitType(size_label="10x10", sf=100.0, count=100, rate=95.0),
                  UnitType(size_label="10x20", sf=200.0, count=50, rate=165.0,
                           climate_controlled=True)],
    )


def _make_extracted_deal(deals_dir, slug="expo"):
    """Deal with a completed extraction snapshot, ready to run."""
    from webapp.models import Deal
    from webapp.services import cim_to_dict
    folder = deals_dir / slug
    (folder / "inputs").mkdir(parents=True)
    (folder / "inputs" / "expo.pdf").write_bytes(b"%PDF-1.4 fake")
    cim = _sample_cim()
    return Deal.objects.create(
        deal_id=slug, property_name="Expo Storage", city="Belton", state="TX",
        deal_dir=str(folder), input_files=["expo.pdf"],
        extract_status="done", cim_json=cim_to_dict(cim),
        extraction_report=cim.extraction_report())


@pytest.mark.django_db
def test_analysis_run_defaults_and_ordering(deals_dir):
    from webapp.models import AnalysisRun
    deal = _make_extracted_deal(deals_dir)
    first = AnalysisRun.objects.create(deal=deal)
    second = AnalysisRun.objects.create(deal=deal)
    assert first.status == "running"
    assert first.result_json is None
    assert first.progress_total == 9
    assert deal.runs.first().pk == second.pk  # newest first


def test_json_safe_scrubs_nan_numpy_enum_tuple():
    import numpy as np
    from registry import ScenarioType
    from webapp.services import json_safe

    payload = {
        ScenarioType.BASE: {"irr": np.float64(0.123), "moic": float("nan")},
        "grid": [(np.float64(1.0), float("inf"))],
        "count": np.int64(7),
        "label": "ok",
        "scen": ScenarioType.BEAR,
    }
    out = json_safe(payload)
    # must round-trip through strict JSON (what Postgres JSONB enforces)
    restored = json.loads(json.dumps(out, allow_nan=False))
    assert restored["base"]["irr"] == pytest.approx(0.123)
    assert restored["base"]["moic"] is None
    assert restored["grid"] == [[1.0, None]]
    assert restored["count"] == 7
    assert restored["scen"] == "bear"


def test_json_safe_stringifies_unknown_objects():
    from webapp.services import json_safe

    class Weird:
        def __str__(self):
            return "weird"

    assert json_safe({"x": Weird()}) == {"x": "weird"}
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web_runs.py -v 2>&1 | tail -5`
Expected: FAIL (no `AnalysisRun`, no `json_safe`).

- [ ] **Step 3: Add `AnalysisRun` to `webapp/models.py`** (append after `Deal`):

```python
class AnalysisRun(models.Model):
    """One execution of the analysis pipeline against a Deal's snapshot
    + overrides. Append-only: each Run Analysis click creates a row, the
    worker writes only its own row, the UI shows the newest done run.
    """

    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="runs")
    # running → done | failed (no pending: the row is created at start time)
    status = models.CharField(max_length=10, default="running")
    progress_step = models.IntegerField(default=0)
    progress_total = models.IntegerField(default=9)
    progress_msg = models.CharField(max_length=200, blank=True, default="")
    result_json = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    memo_filename = models.CharField(max_length=300, blank=True, default="")
    excel_filename = models.CharField(max_length=300, blank=True, default="")
    template_filename = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.deal_id}:{self.pk} {self.status}"
```

- [ ] **Step 4: Add the sanitizer to `webapp/services.py`** (new section after the serialization block; extend the module imports with `import math`, `import numbers`, `from enum import Enum`):

```python
ANALYSIS_TIMEOUT_SECONDS = 300  # run-status partial flips to failed after this


def json_safe(obj):
    """Recursively coerce an analysis payload to strict-JSON-safe values.

    npf.irr yields NaN on non-converging cash flows; json.dumps(nan) is
    invalid JSON that Postgres JSONB rejects (SQLite accepts it — the
    breakage would be invisible until the Phase 5 cutover). Scenario
    dicts are keyed by ScenarioType (str Enum) and sensitivity rows are
    tuples of numpy floats.
    """
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, Enum):
        return json_safe(obj.value)
    if isinstance(obj, str):
        return obj
    if isinstance(obj, numbers.Integral):
        return int(obj)
    if isinstance(obj, numbers.Real):
        f = float(obj)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            k = json_safe(k)
            out[k if isinstance(k, str) else str(k)] = json_safe(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return str(obj)
```

- [ ] **Step 5: Generate the migration and run the new tests**

Run: `.venv/bin/python manage.py makemigrations webapp && .venv/bin/python -m pytest tests/test_web_runs.py -v 2>&1 | tail -6`
Expected: `0003_analysisrun.py` created; 3 tests pass.

- [ ] **Step 6: Full suite** — `.venv/bin/python -m pytest tests/ -q 2>&1 | tail -2` → 104 passed (was 101).

- [ ] **Step 7: Commit**

```bash
git add webapp/models.py webapp/migrations/ webapp/services.py tests/test_web_runs.py
git commit -m "feat(web): AnalysisRun model + strict-JSON payload sanitizer"
```

### Task 2: Override seams — engine solver param + in-place RC patch

**Files:**
- Modify: `gui/engine.py:119-212`
- Modify: `webapp/services.py`
- Test: append to `tests/test_web_runs.py`

**Interfaces:**
- Consumes: `config.REPLACEMENT_COST` (shared dict object), `model.solver` signatures.
- Produces: `run_analysis(..., solver_target_irr: float = None)`; `services._patched_replacement_cost(overrides: dict | None)` context manager; `services._ANALYSIS_LOCK` (`threading.Lock`).

- [ ] **Step 1: Write the failing tests** (append):

```python
def test_patched_replacement_cost_mutates_in_place_and_restores():
    # analysis.physical holds the dict OBJECT from import time — the
    # patch must be visible through that binding, then fully restored.
    from analysis.physical import REPLACEMENT_COST as bound
    from webapp.services import _patched_replacement_cost

    original = bound["ss_driveup_per_sf"]
    with _patched_replacement_cost({"ss_driveup_per_sf": [100, 120],
                                    "not_a_real_key": [1, 2]}):
        assert tuple(bound["ss_driveup_per_sf"]) == (100, 120)
        assert "not_a_real_key" not in bound
    assert bound["ss_driveup_per_sf"] == original


def test_patched_replacement_cost_restores_on_exception():
    from analysis.physical import REPLACEMENT_COST as bound
    from webapp.services import _patched_replacement_cost

    original = bound["ss_driveup_per_sf"]
    with pytest.raises(RuntimeError):
        with _patched_replacement_cost({"ss_driveup_per_sf": [100, 120]}):
            raise RuntimeError("boom")
    assert bound["ss_driveup_per_sf"] == original


def test_run_analysis_accepts_solver_target_irr():
    import inspect
    from gui.engine import run_analysis
    params = inspect.signature(run_analysis).parameters
    assert "solver_target_irr" in params
    assert params["solver_target_irr"].default is None


def test_solver_honors_target_irr():
    from model.solver import solve_max_price
    out = solve_max_price(adjusted_ttm_noi=250_000.0, capex=0,
                          target_irr=0.12, expense_ratio=0.40)
    assert out["converged"]
    assert out["achieved_irr"] == pytest.approx(0.12, abs=0.005)


def test_engine_end_to_end_with_overrides(tmp_path, monkeypatch):
    """Real pipeline (no PDF, no network): overrides reach the solver and
    the writers produce files. Comp DB redirected to a scratch path —
    data.comp_db binds COMP_DB_PATH at import, so patch that module's name."""
    monkeypatch.setattr("data.comp_db.COMP_DB_PATH", str(tmp_path / "comps.db"))
    from gui.engine import AnalysisResult, run_analysis
    from webapp.services import _patched_replacement_cost

    result = AnalysisResult(pdf_path=str(tmp_path / "expo.pdf"))
    result.cim_data = _sample_cim()
    with _patched_replacement_cost({"ss_driveup_per_sf": [100, 120]}):
        result = run_analysis(result, output_dir=str(tmp_path),
                              solver_target_irr=0.12)
    assert result.max_offer["achieved_irr"] == pytest.approx(0.12, abs=0.005)
    assert os.path.isfile(result.memo_path)
    assert os.path.isfile(result.excel_path)
    assert result.gate_summary["recommendation"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web_runs.py -v 2>&1 | tail -8`
Expected: FAIL (no `_patched_replacement_cost`; signature lacks `solver_target_irr`).

- [ ] **Step 3: Extend `gui/engine.py::run_analysis`** — additive only. Change the signature:

```python
def run_analysis(result: AnalysisResult, progress: Callable = None,
                  output_dir: str = None,
                  custom_scenarios: dict = None,
                  custom_va_scenarios: dict = None,
                  solver_target_irr: float = None) -> AnalysisResult:
```

Extend the docstring Args list with:

```
        solver_target_irr: per-analysis max-offer IRR target; None keeps
            the config.SOLVER_TARGET_IRR default (solver binds it as a
            function default at import, so a parameter is the only seam)
```

Then in Step 7 (max price solver), pass it through to BOTH solver calls:

```python
        solver_kwargs = (
            {"target_irr": solver_target_irr} if solver_target_irr else {})
        result.max_offer = solve_max_price(
            adjusted_ttm_noi=result.adjusted_noi,
            capex=capex,
            expense_ratio=result.expense_ratio,
            **solver_kwargs,
        )
        if result.va_results:
            result.va_max_offer = solve_max_price_value_add(
                cim_data=cim_data,
                financial_analysis=result.financial_analysis,
                capex=capex,
                **solver_kwargs,
            )
```

No other engine lines change.

- [ ] **Step 4: Add the RC patch + lock to `webapp/services.py`** (extend module imports with `import copy`, `from contextlib import contextmanager`, `import config as cfg`; add after the `json_safe` block):

```python
# ── Per-run config overrides ────────────────────────────────────────

# analysis.physical binds the REPLACEMENT_COST dict OBJECT at import
# (`from config import REPLACEMENT_COST`), so per-deal overrides must
# mutate that shared dict in place and restore it afterwards. The lock
# serializes analysis runs so patched config never leaks across deals.
_ORIG_REPLACEMENT_COST = copy.deepcopy(cfg.REPLACEMENT_COST)
_ANALYSIS_LOCK = threading.Lock()


@contextmanager
def _patched_replacement_cost(overrides):
    """Apply {key: [low, high]} deltas to config.REPLACEMENT_COST in
    place; unknown keys ignored. Caller must hold _ANALYSIS_LOCK."""
    if not overrides:
        yield
        return
    try:
        cfg.REPLACEMENT_COST.update(
            {k: tuple(v) for k, v in overrides.items()
             if k in _ORIG_REPLACEMENT_COST})
        yield
    finally:
        cfg.REPLACEMENT_COST.clear()
        cfg.REPLACEMENT_COST.update(copy.deepcopy(_ORIG_REPLACEMENT_COST))
```

- [ ] **Step 5: Run the new tests**

Run: `.venv/bin/python -m pytest tests/test_web_runs.py -v 2>&1 | tail -8`
Expected: all pass (the end-to-end test takes a few seconds — docx/xlsx generation).

- [ ] **Step 6: Regression gate for the untouched consumers** — run the CLI-path tests that exercise the engine defaults:

Run: `.venv/bin/python -m pytest tests/test_solver.py tests/test_valuation.py -q 2>&1 | tail -2`
Expected: all pass (default `solver_target_irr=None` leaves every existing call site byte-identical).

- [ ] **Step 7: Commit**

```bash
git add gui/engine.py webapp/services.py tests/test_web_runs.py
git commit -m "feat(web): per-run override seams — engine solver_target_irr param, in-place RC config patch"
```

### Task 3: Background analysis worker

**Files:**
- Modify: `webapp/services.py`, `cimweb/settings_test.py`
- Test: append to `tests/test_web_runs.py`

**Interfaces:**
- Consumes: Task 1 model + sanitizer, Task 2 seams, Phase 3's `cim_from_dict`, `gui.engine._apply_overrides`, `gui.deal_manager.build_deal_meta`/`write_deal_meta`.
- Produces: `services.start_analysis(run) -> None` (spawns `_analysis_worker(run_pk)`; inline when `settings.ANALYSIS_USE_THREAD` is False). Worker contract: on success run.status="done" + `result_json` + output filenames + `finished_at`, Deal row refreshed (recommendation, estimated_fair_value, analysis_date, memo/excel filenames, display metadata) and `deal_meta.json` written with the ROW's deal_id; on failure run.status="failed" + `error`.

- [ ] **Step 1: Write the failing tests** (append). The `fake_run` fixture is reused by Tasks 4–5:

```python
@pytest.fixture
def fake_run(monkeypatch):
    """Stand-in for gui.engine.run_analysis: fills the result fields the
    worker consumes, writes the three output files, captures kwargs."""
    calls = {}

    def _fake(result, progress=None, output_dir=None, custom_scenarios=None,
              custom_va_scenarios=None, solver_target_irr=None):
        calls["cim_data"] = result.cim_data
        calls["output_dir"] = output_dir
        calls["custom_scenarios"] = custom_scenarios
        calls["custom_va_scenarios"] = custom_va_scenarios
        calls["solver_target_irr"] = solver_target_irr
        if progress:
            progress(9, 9, "Generating memo & model...")
        name = result.cim_data.property_name.replace(" ", "_")
        for attr, suffix in [("memo_path", "_memo.docx"),
                             ("excel_path", "_model.xlsx"),
                             ("template_path", "_uw.xlsm")]:
            path = os.path.join(output_dir, f"{name}{suffix}")
            with open(path, "wb") as f:
                f.write(b"fake-office-bytes")
            setattr(result, attr, path)
        result.gate_results = [
            {"gate": 1, "name": "Population (3-mi ≥ 50K)", "threshold": "≥ 50,000",
             "actual": "62,000", "result": "PASS", "note": "", "source": None},
            {"gate": 2, "name": "No unproven demand", "threshold": "phys ≥ 75%",
             "actual": "92%", "result": "PASS", "note": "", "source": None},
        ]
        result.gate_summary = {"passed": 2, "failed": 0, "tbd": 0, "total": 2,
                               "recommendation": "PURSUE",
                               "failed_gates": [], "tbd_gates": []}
        result.scenario_results = {
            "bear": {"irr": 0.06, "moic": 1.3, "yield_on_cost": 0.065},
            "base": {"irr": float("nan"), "moic": 1.6, "yield_on_cost": 0.075},
            "bull": {"irr": 0.14, "moic": 1.9, "yield_on_cost": 0.085},
        }
        result.sensitivity = {"prices": [3_325_000.0, 3_500_000.0, 3_675_000.0],
                              "exit_caps": [0.055, 0.06, 0.065],
                              "grid": [[0.11, 0.10, 0.09],
                                       [0.10, 0.09, 0.08],
                                       [0.09, 0.08, 0.07]]}
        result.va_results = {
            "base": {"irr": 0.13, "moic": 1.7, "yield_on_cost": 0.08,
                     "development_spread": 0.02, "stabilized_noi": 300_000.0}}
        result.max_offer = {"max_price": 3_100_000.0, "achieved_irr": 0.10,
                            "converged": True}
        result.va_max_offer = {"max_price": 3_300_000.0, "achieved_irr": 0.10,
                               "converged": True}
        result.financial_analysis = {
            "adjusted_ttm_noi": {"cim_ttm_noi": 250_000.0,
                                 "analyst_adjusted_noi": 230_000.0},
            "adjustments": ["Property tax adjusted to benchmark",
                            {"category": "Insurance", "flag": "understated"}],
            "expense_ratio_check": {"opex_revenue_ratio": 0.42},
        }
        result.risk_analysis = {"risks": [
            {"risk": "ECRI bridge", "severity": "HIGH",
             "detail": "Street rates falling", "mitigation": "Verify trend"}]}
        result.adjusted_noi = 230_000.0
        result.expense_ratio = 0.42
        result.errors = ["Template generation failed: test-only"]
        return result

    monkeypatch.setattr("webapp.services.run_analysis", _fake)
    return calls


def _start_run(deal):
    from webapp import services
    from webapp.models import AnalysisRun
    run = AnalysisRun.objects.create(deal=deal)
    services.start_analysis(run)
    run.refresh_from_db()
    return run


@pytest.mark.django_db
def test_worker_success_updates_run_and_deal(deals_dir, fake_run):
    deal = _make_extracted_deal(deals_dir)
    deal.assumption_overrides = {
        "cim_overrides": {"asking_price": 3_400_000.0},
        "scenario_overrides": {"base": {"exit_cap": 0.06}},
        "va_scenario_overrides": {"base": {"target_occupancy": 0.9}},
        "replacement_cost_overrides": {"ss_driveup_per_sf": [100, 120]},
        "solver_target_irr": 0.12,
    }
    deal.save()
    run = _start_run(deal)

    assert run.status == "done"
    assert run.finished_at is not None
    assert run.memo_filename == "Expo_Storage_memo.docx"
    assert run.excel_filename == "Expo_Storage_model.xlsx"
    assert run.template_filename == "Expo_Storage_uw.xlsm"
    # NaN scrubbed for Postgres JSONB
    assert run.result_json["scenario_results"]["base"]["irr"] is None
    assert run.result_json["gate_summary"]["recommendation"] == "PURSUE"
    assert run.result_json["errors"] == ["Template generation failed: test-only"]
    # overrides all reached the engine
    assert fake_run["cim_data"].asking_price == 3_400_000.0
    assert fake_run["custom_scenarios"] == {"base": {"exit_cap": 0.06}}
    assert fake_run["custom_va_scenarios"] == {"base": {"target_occupancy": 0.9}}
    assert fake_run["solver_target_irr"] == 0.12
    assert fake_run["output_dir"] == deal.deal_dir

    deal.refresh_from_db()
    assert deal.recommendation == "PURSUE"
    assert deal.estimated_fair_value == 3_300_000.0  # VA max offer preferred
    assert deal.analysis_date is not None
    assert deal.memo_filename == "Expo_Storage_memo.docx"
    assert deal.excel_filename == "Expo_Storage_model.xlsx"


@pytest.mark.django_db
def test_worker_writes_meta_with_row_deal_id(deals_dir, fake_run):
    # Slug came from the FILENAME (Phase 3 decision #2); property name
    # differs. deal_meta.json must carry the row's slug so import_deals
    # round-trips onto the same row instead of forking a duplicate.
    deal = _make_extracted_deal(deals_dir, slug="expo-cim-v2")
    _start_run(deal)
    with open(os.path.join(deal.deal_dir, "deal_meta.json")) as f:
        meta = json.load(f)
    assert meta["deal_id"] == "expo-cim-v2"
    assert meta["property_name"] == "Expo Storage"
    assert meta["memo_path"] == "Expo_Storage_memo.docx"
    assert meta["input_files"] == ["expo.pdf"]


@pytest.mark.django_db
def test_worker_failure_records_error(deals_dir, monkeypatch):
    def boom(result, **kwargs):
        raise RuntimeError("solver exploded")

    monkeypatch.setattr("webapp.services.run_analysis", boom)
    deal = _make_extracted_deal(deals_dir)
    run = _start_run(deal)
    assert run.status == "failed"
    assert "solver exploded" in run.error
    deal.refresh_from_db()
    assert deal.recommendation == "N/A"  # deal row untouched on failure


@pytest.mark.django_db
def test_worker_progress_updates_row(deals_dir, fake_run):
    deal = _make_extracted_deal(deals_dir)
    run = _start_run(deal)
    assert run.progress_step == 9
    assert run.progress_msg == "Generating memo & model..."
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web_runs.py -v 2>&1 | tail -6`
Expected: FAIL (`webapp.services` has no `run_analysis` name to patch, no `start_analysis`).

- [ ] **Step 3: Implement the worker in `webapp/services.py`** — extend the gui imports at the top of the module:

```python
from gui.deal_manager import (build_deal_meta, detect_asset_type,
                              sanitize_name, write_deal_meta)
from gui.engine import (AnalysisResult, _apply_overrides, extract_pdf_data,
                        run_analysis)
```

and add `import datetime` to the stdlib imports. Then append the section:

```python
# ── Background analysis runs ────────────────────────────────────────

def start_analysis(run) -> None:
    """Run the pipeline for an AnalysisRun (thread in prod, inline in
    tests). The worker writes only to its own row, so late/stale threads
    are harmless by construction — no CAS stamp needed."""
    if getattr(settings, "ANALYSIS_USE_THREAD", True):
        threading.Thread(target=_analysis_worker, args=(run.pk,),
                         daemon=True).start()
    else:
        _analysis_worker(run.pk)


def _analysis_worker(run_pk):
    from webapp.models import AnalysisRun

    try:
        run = AnalysisRun.objects.select_related("deal").get(pk=run_pk)
        deal = run.deal
        overrides = deal.assumption_overrides or {}

        cim = cim_from_dict(deal.cim_json)
        cim_o = overrides.get("cim_overrides")
        if cim_o:
            _apply_overrides(cim, copy.deepcopy(cim_o))

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

        with _ANALYSIS_LOCK:
            with _patched_replacement_cost(
                    overrides.get("replacement_cost_overrides")):
                result = run_analysis(
                    result, progress=_progress, output_dir=deal.deal_dir,
                    custom_scenarios=overrides.get("scenario_overrides"),
                    custom_va_scenarios=overrides.get("va_scenario_overrides"),
                    solver_target_irr=overrides.get("solver_target_irr"),
                )

        meta = build_deal_meta(cim, result, deal.deal_dir,
                               input_files=deal.input_files)
        meta["deal_id"] = deal.deal_id  # row slug, never property-name derived
        write_deal_meta(deal.deal_dir, meta)

        payload = json_safe({
            "gate_results": result.gate_results,
            "gate_summary": result.gate_summary,
            "scenario_results": result.scenario_results,
            "sensitivity": result.sensitivity,
            "va_results": result.va_results,
            "max_offer": result.max_offer,
            "va_max_offer": result.va_max_offer,
            "financial_analysis": result.financial_analysis,
            "market_analysis": result.market_analysis,
            "physical_analysis": result.physical_analysis,
            "rent_analysis": result.rent_analysis,
            "value_add": result.value_add,
            "risk_analysis": result.risk_analysis,
            "adjusted_noi": result.adjusted_noi,
            "expense_ratio": result.expense_ratio,
            "errors": result.errors,
        })
        AnalysisRun.objects.filter(pk=run_pk).update(
            status="done", finished_at=timezone.now(), result_json=payload,
            error="",
            memo_filename=os.path.basename(result.memo_path or ""),
            excel_filename=os.path.basename(result.excel_path or ""),
            template_filename=os.path.basename(result.template_path or ""),
        )

        deal_updates = {
            "recommendation": (meta.get("recommendation") or "N/A")[:40],
            "estimated_fair_value": meta.get("estimated_fair_value"),
            "analysis_date": datetime.date.fromisoformat(meta["analysis_date"]),
            "memo_filename": os.path.basename(result.memo_path or ""),
            "excel_filename": os.path.basename(result.excel_path or ""),
            "asset_type": detect_asset_type(cim),
        }
        if cim.property_name:
            deal_updates["property_name"] = cim.property_name[:200]
        if cim.city:
            deal_updates["city"] = cim.city[:100]
        if cim.state:
            deal_updates["state"] = cim.state[:2].upper()
        if cim.nrsf:
            deal_updates["nrsf"] = cim.nrsf
        if cim.acreage:
            deal_updates["acreage"] = cim.acreage
        if cim.asking_price:
            deal_updates["asking_price"] = cim.asking_price
        Deal.objects.filter(pk=deal.pk).update(**deal_updates)
    except Exception as e:
        logger.exception("analysis worker failed for run %s", run_pk)
        AnalysisRun.objects.filter(pk=run_pk).update(
            status="failed", finished_at=timezone.now(),
            error=str(e)[:2000])
    finally:
        if getattr(settings, "ANALYSIS_USE_THREAD", True):
            from django.db import connections
            connections.close_all()
```

- [ ] **Step 4: Add to `cimweb/settings_test.py`** (below `EXTRACT_USE_THREAD`):

```python
# Same reasoning as EXTRACT_USE_THREAD: analysis runs inline in tests.
ANALYSIS_USE_THREAD = False
```

- [ ] **Step 5: Run tests** — `.venv/bin/python -m pytest tests/test_web_runs.py -v 2>&1 | tail -8` → all pass.

- [ ] **Step 6: Full suite** — `.venv/bin/python -m pytest tests/ -q 2>&1 | tail -2` → all pass.

- [ ] **Step 7: Commit**

```bash
git add webapp/services.py cimweb/settings_test.py tests/test_web_runs.py
git commit -m "feat(web): background analysis worker — overrides through the engine, deal_meta + Deal refresh"
```

### Task 4: Run trigger, status poll, Save-&-Run

**Files:**
- Modify: `webapp/views.py`, `webapp/urls.py`
- Create: `webapp/templates/webapp/_run_status.html`
- Modify: `webapp/templates/webapp/assumptions.html`
- Test: append to `tests/test_web_runs.py`

**Interfaces:**
- Consumes: Task 3 `start_analysis`; Phase 3 `_extract_state` pattern.
- Produces: URL names `deal-run` (`POST /deals/<pk>/run/`), `run-status` (`/deals/<pk>/run-status/`); view helper `_run_state(run) -> None | "running" | "failed" | "done"` (timeout counts as failed). `deal-detail` (`/deals/<pk>/`) exists after Task 5 — this task registers the URL with a minimal placeholder view body (`HttpResponse` of the property name) that Task 5 replaces, so redirects resolve.

- [ ] **Step 1: Write the failing tests** (append):

```python
@pytest.mark.django_db
def test_deal_run_starts_and_redirects(client, operator, deals_dir, fake_run):
    deal = _make_extracted_deal(deals_dir)
    resp = client.post(f"/deals/{deal.pk}/run/")
    assert resp.status_code == 302
    assert resp.url == f"/deals/{deal.pk}/"
    run = deal.runs.first()
    assert run.status == "done"  # sync mode ran inline


@pytest.mark.django_db
def test_deal_run_refuses_without_snapshot(client, operator, deals_dir):
    from webapp.models import Deal
    imported = Deal.objects.create(deal_id="legacy", property_name="Legacy")
    resp = client.post(f"/deals/{imported.pk}/run/")
    assert resp.status_code == 302
    assert imported.runs.count() == 0


@pytest.mark.django_db
def test_deal_run_refuses_while_running(client, operator, deals_dir, monkeypatch):
    from webapp import services
    from webapp.models import AnalysisRun
    deal = _make_extracted_deal(deals_dir)
    AnalysisRun.objects.create(deal=deal)  # status=running, fresh stamp
    monkeypatch.setattr(services, "start_analysis",
                        lambda run: pytest.fail("must not start a second run"))
    client.post(f"/deals/{deal.pk}/run/")
    assert deal.runs.count() == 1


@pytest.mark.django_db
def test_run_status_running_polls(client, operator, deals_dir):
    from webapp.models import AnalysisRun
    deal = _make_extracted_deal(deals_dir)
    AnalysisRun.objects.create(deal=deal, progress_step=3,
                               progress_msg="Analyzing market...")
    resp = client.get(f"/deals/{deal.pk}/run-status/")
    assert resp.status_code == 200
    assert b"hx-trigger" in resp.content
    assert b"Analyzing market..." in resp.content


@pytest.mark.django_db
def test_run_status_done_redirects(client, operator, deals_dir):
    from webapp.models import AnalysisRun
    deal = _make_extracted_deal(deals_dir)
    AnalysisRun.objects.create(deal=deal, status="done")
    resp = client.get(f"/deals/{deal.pk}/run-status/")
    assert resp.headers["HX-Redirect"] == f"/deals/{deal.pk}/"


@pytest.mark.django_db
def test_run_status_failed_and_timeout_stop_polling(client, operator, deals_dir):
    import datetime as dt

    from django.utils import timezone

    from webapp.models import AnalysisRun
    deal = _make_extracted_deal(deals_dir)
    run = AnalysisRun.objects.create(deal=deal, status="failed",
                                     error="solver exploded")
    resp = client.get(f"/deals/{deal.pk}/run-status/")
    assert b"hx-trigger" not in resp.content
    assert b"solver exploded" in resp.content
    # timeout: still "running" but created too long ago
    run.status = "running"
    run.error = ""
    run.save()
    AnalysisRun.objects.filter(pk=run.pk).update(
        created_at=timezone.now() - dt.timedelta(seconds=999))
    resp = client.get(f"/deals/{deal.pk}/run-status/")
    assert b"hx-trigger" not in resp.content
    assert b"timed out" in resp.content


@pytest.mark.django_db
def test_assumptions_save_and_run(client, operator, deals_dir, fake_run):
    deal = _make_extracted_deal(deals_dir)
    resp = client.post(f"/deals/{deal.pk}/assumptions/",
                       {"asking_price": "3400000", "run": "1"})
    assert resp.status_code == 302
    assert resp.url == f"/deals/{deal.pk}/"
    deal.refresh_from_db()
    assert deal.assumption_overrides["cim_overrides"]["asking_price"] == 3_400_000.0
    assert deal.runs.first().status == "done"
```

- [ ] **Step 2: Run to verify failure** — 404s (no routes).

- [ ] **Step 3: Add views** (append to `webapp/views.py`; extend imports with `from webapp.models import AnalysisRun`):

```python
# ── Phase 4: analysis runs ──────────────────────────────────────────

def _run_state(run):
    """None | 'running' | 'failed' | 'done' — a running row older than
    the timeout counts as failed so the UI never spins forever."""
    if run is None:
        return None
    if run.status in ("done", "failed"):
        return run.status
    if (timezone.now() - run.created_at).total_seconds() > \
            services.ANALYSIS_TIMEOUT_SECONDS:
        return "failed"
    return "running"


@login_required
@require_POST
def deal_run(request, pk):
    deal = get_object_or_404(Deal, pk=pk)
    if not deal.cim_json:
        messages.error(request, "No extraction snapshot — upload the CIM "
                                "under New Analysis first.")
        return redirect("deal-detail", pk=deal.pk)
    if _run_state(deal.runs.first()) == "running":
        messages.error(request, "An analysis is already running for this deal.")
        return redirect("deal-detail", pk=deal.pk)
    run = AnalysisRun.objects.create(deal=deal)
    services.start_analysis(run)
    return redirect("deal-detail", pk=deal.pk)


@login_required
def run_status(request, pk):
    deal = get_object_or_404(Deal, pk=pk)
    run = deal.runs.first()
    state = _run_state(run)
    if state == "done":
        return HttpResponseClientRedirect(reverse("deal-detail", args=[deal.pk]))
    return render(request, "webapp/_run_status.html",
                  {"deal": deal, "run": run, "failed": state == "failed"})
```

In `deal_assumptions`, replace the two-line success branch (`messages.success` … `return redirect`) with:

```python
            deal.save(update_fields=["assumption_overrides", "updated_at"])
            if "run" in request.POST:
                if _run_state(deal.runs.first()) == "running":
                    messages.error(
                        request, "An analysis is already running for this deal.")
                    return redirect("deal-detail", pk=deal.pk)
                run = AnalysisRun.objects.create(deal=deal)
                services.start_analysis(run)
                return redirect("deal-detail", pk=deal.pk)
            messages.success(request, "Assumptions saved.")
            return redirect("deal-assumptions", pk=deal.pk)
```

Add the placeholder detail view (replaced in Task 5):

```python
@login_required
def deal_detail(request, pk):
    deal = get_object_or_404(Deal, pk=pk)
    return HttpResponse(deal.property_name)  # replaced in Task 5
```

Add to `webapp/urls.py` (note: `deals/unit-mix-row/` stays ABOVE the `<int:pk>` route — it matches first as a literal; int converter rejects it anyway):

```python
    path("deals/<int:pk>/", views.deal_detail, name="deal-detail"),
    path("deals/<int:pk>/run/", views.deal_run, name="deal-run"),
    path("deals/<int:pk>/run-status/", views.run_status, name="run-status"),
```

- [ ] **Step 4: Create `webapp/templates/webapp/_run_status.html`** (Phase 3 `_extract_status.html` pattern):

```html
{% if failed %}
<div id="run-status" class="max-w-xl rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
  <p class="font-medium">Analysis failed{% if run.error %}: {{ run.error }}{% else %} or timed out.{% endif %}</p>
  <form method="post" action="{% url 'deal-run' deal.pk %}" class="mt-2">
    {% csrf_token %}
    <button type="submit" class="bg-accent-700 text-white text-sm px-3 py-1.5 rounded">Re-run analysis</button>
  </form>
</div>
{% else %}
<div id="run-status"
     hx-get="{% url 'run-status' deal.pk %}"
     hx-trigger="load delay:2s" hx-swap="outerHTML"
     class="max-w-xl rounded-md border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">
  <div class="flex items-center justify-between mb-1.5">
    <span>{{ run.progress_msg|default:"Starting analysis…" }}</span>
    <span class="text-xs text-slate-500">{{ run.progress_step }}/{{ run.progress_total }}</span>
  </div>
  <div class="h-1.5 w-full rounded bg-slate-100">
    <div class="h-1.5 rounded bg-accent-700"
         style="width: {% widthratio run.progress_step run.progress_total 100 %}%"></div>
  </div>
</div>
{% endif %}
```

- [ ] **Step 5: Add the Save-&-Run button to `assumptions.html`** — next to the existing save submit button (read the file first to match its footer structure), add as a sibling:

```html
    <button type="submit" name="run" value="1"
            class="bg-accent-700 text-white text-sm px-4 py-1.5 rounded">Save &amp; Run Analysis</button>
```

and restyle the existing plain save button as secondary (`border border-slate-300 text-slate-700 hover:bg-slate-50` in place of its accent background) so the primary action is the run.

- [ ] **Step 6: Run tests** — `.venv/bin/python -m pytest tests/test_web_runs.py -v 2>&1 | tail -10` → all pass.

- [ ] **Step 7: Full suite** — `.venv/bin/python -m pytest tests/ -q 2>&1 | tail -2` → all pass.

- [ ] **Step 8: Commit**

```bash
git add webapp/views.py webapp/urls.py webapp/templates/
git commit -m "feat(web): run trigger + progress poll + Save-&-Run (placeholder detail page)"
```

### Task 5: Deal detail page with results tabs

**Files:**
- Create: `webapp/results.py`
- Modify: `webapp/views.py` (real `deal_detail`)
- Create: `webapp/templates/webapp/deal_detail.html`, `_tab_summary.html`, `_tab_returns.html`, `_tab_financials.html`, `_tab_risks.html`
- Test: append to `tests/test_web_runs.py`

**Interfaces:**
- Consumes: `AnalysisRun.result_json` payload keys (Task 3), `_run_state` (Task 4), `services.expense_benchmark_rows` (Phase 3).
- Produces: `results.header_metrics(deal, r) -> dict` (keys `recommendation`, `base_irr`, `max_price`, `discount_to_asking` — all preformatted strings); `results.summary_context(r) -> dict` (keys `gate_summary`, `gates`, `rec_tone` ∈ {"pass","warn","fail"}, `repl_rows`, `repl_total`, `repl_delta_label`, `repl_delta`); `results.returns_context(r) -> dict` (keys `scenario_rows`, `va_rows`, `max_offer`, `va_max_offer`, `sens_caps`, `sens_rows`); `results.financials_context(r) -> dict` (keys `cim_noi`, `adj_noi`, `expense_ratio`, `adjustments` — list of strings); `results.risks_context(r) -> dict` (key `risks`); formatting helpers `fmt_pct`, `fmt_money`, `fmt_x`. Tab templates consume ONLY these contexts. `r` is always `run.result_json` (a plain dict).

- [ ] **Step 1: Write the failing tests** (append):

```python
def test_results_formatters():
    from webapp.results import fmt_money, fmt_pct, fmt_x
    assert fmt_pct(0.123) == "12.3%"
    assert fmt_pct(None) == "N/A"
    assert fmt_money(3_100_000.0) == "$3,100,000"
    assert fmt_money(None) == "N/A"
    assert fmt_x(1.62) == "1.62x"
    assert fmt_x(None) == "N/A"


def _run_deal(client, deals_dir):
    deal = _make_extracted_deal(deals_dir)
    client.post(f"/deals/{deal.pk}/run/")
    return deal


@pytest.mark.django_db
def test_detail_summary_tab(client, operator, deals_dir, fake_run):
    deal = _run_deal(client, deals_dir)
    resp = client.get(f"/deals/{deal.pk}/")
    content = resp.content.decode()
    assert resp.status_code == 200
    assert "PURSUE" in content
    assert "Population (3-mi ≥ 50K)" in content       # gate row
    assert "Template generation failed: test-only" in content  # run warnings
    assert "Belton, TX" in content                     # property caption


@pytest.mark.django_db
def test_detail_returns_tab(client, operator, deals_dir, fake_run):
    deal = _run_deal(client, deals_dir)
    content = client.get(f"/deals/{deal.pk}/?tab=returns").content.decode()
    assert "14.0%" in content            # bull IRR
    assert "N/A" in content              # NaN base IRR scrubbed → N/A
    assert "$3,300,000" in content       # VA max offer
    assert "$3,325,000" in content       # sensitivity price row
    assert "6.0%" in content             # sensitivity cap column


@pytest.mark.django_db
def test_detail_financials_and_risks_tabs(client, operator, deals_dir, fake_run):
    deal = _run_deal(client, deals_dir)
    fin = client.get(f"/deals/{deal.pk}/?tab=financials").content.decode()
    assert "$230,000" in fin                       # analyst-adjusted NOI
    assert "Property tax adjusted to benchmark" in fin
    assert "Insurance: understated" in fin         # dict adjustment normalized
    risks = client.get(f"/deals/{deal.pk}/?tab=risks").content.decode()
    assert "ECRI bridge" in risks
    assert "HIGH" in risks


@pytest.mark.django_db
def test_detail_no_runs_shows_run_button(client, operator, deals_dir):
    deal = _make_extracted_deal(deals_dir)
    content = client.get(f"/deals/{deal.pk}/").content.decode()
    assert "Run Analysis" in content
    assert "tab=returns" not in content   # no tabs before first done run


@pytest.mark.django_db
def test_detail_imported_deal_has_no_run_button(client, operator):
    from webapp.models import Deal
    imported = Deal.objects.create(deal_id="legacy", property_name="Legacy",
                                   recommendation="PURSUE")
    content = client.get(f"/deals/{imported.pk}/").content.decode()
    assert "Run Analysis" not in content
    assert "no extraction snapshot" in content.lower()


@pytest.mark.django_db
def test_detail_bad_tab_falls_back_to_summary(client, operator, deals_dir, fake_run):
    deal = _run_deal(client, deals_dir)
    resp = client.get(f"/deals/{deal.pk}/?tab=nope")
    assert resp.status_code == 200
    assert b"Go / No-Go Gates" in resp.content
```

- [ ] **Step 2: Run to verify failure** — placeholder view renders none of it.

- [ ] **Step 3: Create `webapp/results.py`**:

```python
"""Display-context builders over AnalysisRun.result_json.

Pure functions: result_json (plain dict) in, template-ready contexts of
preformatted strings out. Percent decimals become display strings HERE
and nowhere else. Templates stay dumb; formatting stays testable.
"""


def fmt_pct(v, digits=1):
    return f"{float(v) * 100:.{digits}f}%" if v is not None else "N/A"


def fmt_money(v):
    return f"${float(v):,.0f}" if v is not None else "N/A"


def fmt_x(v):
    return f"{float(v):.2f}x" if v is not None else "N/A"


SCENARIOS = ("bear", "base", "bull")


def _metric_rows(block, metrics):
    rows = []
    for label, key, fmt in metrics:
        rows.append({"label": label,
                     "cells": [fmt((block.get(sc) or {}).get(key))
                               for sc in SCENARIOS]})
    return rows


def header_metrics(deal, r) -> dict:
    base = (r.get("scenario_results") or {}).get("base") or {}
    max_price = (r.get("max_offer") or {}).get("max_price")
    asking = deal.asking_price
    discount = None
    if max_price and asking:
        discount = (asking - max_price) / asking
    return {
        "recommendation": (r.get("gate_summary") or {}).get("recommendation", "N/A"),
        "base_irr": fmt_pct(base.get("irr")),
        "max_price": fmt_money(max_price),
        "discount_to_asking": fmt_pct(discount),
    }


def summary_context(r) -> dict:
    summary = r.get("gate_summary") or {}
    rec = summary.get("recommendation", "N/A")
    if "DECLINE" in rec:
        tone = "fail"
    elif "CONTINGENT" in rec or rec == "N/A":
        tone = "warn"
    else:
        tone = "pass"
    repl = (r.get("physical_analysis") or {}).get("replacement_cost") or {}
    comp = (r.get("physical_analysis") or {}).get("price_vs_replacement") or {}
    repl_rows = [{"type": td.get("type"), "sf": f"{td.get('sf') or 0:,.0f}",
                  "hard_rate": fmt_money(td.get("hard_rate")),
                  "hard_cost": fmt_money(td.get("hard_cost"))}
                 for td in repl.get("facility_type_details") or []]
    delta_label = delta = None
    if comp.get("comparable"):
        d = comp.get("discount_to_replacement")
        if d is not None:
            delta_label = "Discount to Replacement" if d > 0 else "Premium to Replacement"
            delta = fmt_pct(abs(d))
    return {
        "gate_summary": summary, "rec_tone": tone,
        "gates": r.get("gate_results") or [],
        "repl_estimable": bool(repl.get("estimable")),
        "repl_rows": repl_rows,
        "repl_total": fmt_money(repl.get("total_replacement")),
        "repl_delta_label": delta_label, "repl_delta": delta,
    }


def returns_context(r) -> dict:
    scen = r.get("scenario_results") or {}
    va = r.get("va_results") or {}
    sens = r.get("sensitivity") or {}
    sens_rows = []
    prices = sens.get("prices") or []
    for i, row in enumerate(sens.get("grid") or []):
        price = prices[i] if i < len(prices) else None
        sens_rows.append({"price": fmt_money(price),
                          "cells": [fmt_pct(v) for v in row]})
    return {
        "scenario_rows": _metric_rows(scen, [
            ("Yr1 Yield on Cost", "yield_on_cost", fmt_pct),
            ("5-Year MOIC", "moic", fmt_x),
            ("5-Year IRR", "irr", fmt_pct),
        ]),
        "has_va": bool(va),
        "va_rows": _metric_rows(va, [
            ("Stabilized Yield on Cost", "yield_on_cost", fmt_pct),
            ("5-Year MOIC", "moic", fmt_x),
            ("5-Year IRR", "irr", fmt_pct),
            ("Development Spread", "development_spread",
             lambda v: f"{float(v) * 10000:,.0f} bps" if v is not None else "N/A"),
            ("Stabilized NOI", "stabilized_noi", fmt_money),
        ]),
        "max_offer": fmt_money((r.get("max_offer") or {}).get("max_price")),
        "va_max_offer": fmt_money((r.get("va_max_offer") or {}).get("max_price")),
        "has_va_max_offer": bool((r.get("va_max_offer") or {}).get("max_price")),
        "has_sensitivity": bool(sens.get("grid")),
        "sens_caps": [fmt_pct(c) for c in sens.get("exit_caps") or []],
        "sens_rows": sens_rows,
    }


def financials_context(r) -> dict:
    fin = r.get("financial_analysis") or {}
    adj = fin.get("adjusted_ttm_noi") or {}
    adjustments = []
    for a in fin.get("adjustments") or []:
        if isinstance(a, dict):
            adjustments.append(
                f"{a.get('category', '')}: {a.get('flag', '')}".strip(": "))
        else:
            adjustments.append(str(a))
    return {
        "cim_noi": fmt_money(adj.get("cim_ttm_noi")),
        "adj_noi": fmt_money(adj.get("analyst_adjusted_noi")),
        "expense_ratio": fmt_pct(
            (fin.get("expense_ratio_check") or {}).get("opex_revenue_ratio")),
        "adjustments": adjustments,
    }


def risks_context(r) -> dict:
    return {"risks": (r.get("risk_analysis") or {}).get("risks") or []}
```

- [ ] **Step 4: Replace the placeholder `deal_detail` in `webapp/views.py`** (add `from webapp import results as results_ctx` to imports):

```python
TAB_NAMES = ("summary", "returns", "financials", "risks")


@login_required
def deal_detail(request, pk):
    deal = get_object_or_404(Deal, pk=pk)
    latest = deal.runs.first()
    state = _run_state(latest)
    done_run = latest if state == "done" else \
        deal.runs.filter(status="done").exclude(result_json=None).first()
    tab = request.GET.get("tab", "summary")
    if tab not in TAB_NAMES:
        tab = "summary"
    ctx = {
        "deal": deal, "run": latest, "done_run": done_run,
        "state": state, "tab": tab,
        "has_snapshot": bool(deal.cim_json),
        "show_progress": state in ("running", "failed") and latest and
                         latest.pk != (done_run.pk if done_run else None),
    }
    if done_run:
        r = done_run.result_json or {}
        ctx["header"] = results_ctx.header_metrics(deal, r)
        ctx["run_warnings"] = r.get("errors") or []
        if tab == "summary":
            ctx.update(results_ctx.summary_context(r))
        elif tab == "returns":
            ctx.update(results_ctx.returns_context(r))
        elif tab == "financials":
            ctx.update(results_ctx.financials_context(r))
            ctx["benchmark_rows"] = services.expense_benchmark_rows(deal)
        elif tab == "risks":
            ctx.update(results_ctx.risks_context(r))
    return render(request, "webapp/deal_detail.html", ctx)
```

- [ ] **Step 5: Create `webapp/templates/webapp/deal_detail.html`**:

```html
{% extends "base.html" %}
{% block title %}{{ deal.property_name }}{% endblock %}
{% block content %}
<div class="max-w-5xl">
  <div class="flex flex-wrap items-start justify-between gap-2 mb-1">
    <div>
      <h1 class="font-display text-xl font-semibold">{{ deal.property_name }}</h1>
      <p class="text-xs text-slate-500">
        {% if deal.city or deal.state %}{{ deal.city }}{% if deal.city and deal.state %}, {% endif %}{{ deal.state }}{% endif %}
        {% if deal.nrsf %} &middot; {{ deal.nrsf|floatformat:0 }} SF{% endif %}
        {% if deal.asset_type %} &middot; {{ deal.asset_type }}{% endif %}
        {% if deal.asking_price %} &middot; Asking ${{ deal.asking_price|floatformat:0 }}{% endif %}
      </p>
    </div>
    <div class="flex items-center gap-2">
      {% if has_snapshot %}
      <a href="{% url 'deal-assumptions' deal.pk %}"
         class="text-sm px-3 py-1.5 rounded border border-slate-300 text-slate-700 hover:bg-slate-50">Assumptions</a>
      {% if state != "running" %}
      <form method="post" action="{% url 'deal-run' deal.pk %}">
        {% csrf_token %}
        <button type="submit" class="bg-accent-700 text-white text-sm px-3 py-1.5 rounded">
          {% if done_run %}Re-run Analysis{% else %}Run Analysis{% endif %}</button>
      </form>
      {% endif %}
      {% endif %}
    </div>
  </div>

  {% if done_run %}
  <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 my-3">
    {% for label, value in header.items %}{% endfor %}
    <div class="rounded-md border border-slate-200 bg-white px-3 py-2">
      <div class="text-[11px] uppercase tracking-wide text-slate-500">Recommendation</div>
      <div class="text-sm font-semibold">{{ header.recommendation }}</div>
    </div>
    <div class="rounded-md border border-slate-200 bg-white px-3 py-2">
      <div class="text-[11px] uppercase tracking-wide text-slate-500">Base Case IRR</div>
      <div class="text-sm font-semibold">{{ header.base_irr }}</div>
    </div>
    <div class="rounded-md border border-slate-200 bg-white px-3 py-2">
      <div class="text-[11px] uppercase tracking-wide text-slate-500">Max Offer</div>
      <div class="text-sm font-semibold">{{ header.max_price }}</div>
    </div>
    <div class="rounded-md border border-slate-200 bg-white px-3 py-2">
      <div class="text-[11px] uppercase tracking-wide text-slate-500">Discount to Asking</div>
      <div class="text-sm font-semibold">{{ header.discount_to_asking }}</div>
    </div>
  </div>
  {% endif %}

  {% if show_progress %}
  <div class="my-3">{% include "webapp/_run_status.html" with run=run failed=state|slugify|default:""|stringformat:"s" %}</div>
  {% endif %}

  {% if not has_snapshot and not done_run %}
  <div class="my-3 rounded-md border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600">
    This deal was imported from a completed analysis folder — there is no extraction
    snapshot to run. Downloads below cover the original outputs where present.
  </div>
  {% endif %}

  {% if run_warnings %}
  <div class="my-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
    <ul class="list-disc pl-4">{% for w in run_warnings %}<li>{{ w }}</li>{% endfor %}</ul>
  </div>
  {% endif %}

  {% if deal.memo_filename or done_run %}
  <div class="flex flex-wrap gap-2 my-3">
    <a href="{% url 'deal-download' deal.pk 'memo' %}" class="text-sm px-3 py-1.5 rounded border border-slate-300 text-slate-700 hover:bg-slate-50">Memo (.docx)</a>
    <a href="{% url 'deal-download' deal.pk 'excel' %}" class="text-sm px-3 py-1.5 rounded border border-slate-300 text-slate-700 hover:bg-slate-50">Returns Model (.xlsx)</a>
    {% if done_run.template_filename %}
    <a href="{% url 'deal-download' deal.pk 'template' %}" class="text-sm px-3 py-1.5 rounded border border-slate-300 text-slate-700 hover:bg-slate-50">UW Template (.xlsm)</a>
    {% endif %}
  </div>
  {% endif %}

  {% if done_run %}
  <nav class="flex gap-1 border-b border-slate-200 mb-3 text-sm">
    {% for name in "summary,returns,financials,risks"|split_hack %}{% endfor %}
    <a href="{% url 'deal-detail' deal.pk %}?tab=summary"
       class="px-3 py-1.5 rounded-t {% if tab == 'summary' %}bg-white border border-b-white border-slate-200 font-medium{% else %}text-slate-500 hover:text-slate-700{% endif %}">Summary</a>
    <a href="{% url 'deal-detail' deal.pk %}?tab=returns"
       class="px-3 py-1.5 rounded-t {% if tab == 'returns' %}bg-white border border-b-white border-slate-200 font-medium{% else %}text-slate-500 hover:text-slate-700{% endif %}">Returns</a>
    <a href="{% url 'deal-detail' deal.pk %}?tab=financials"
       class="px-3 py-1.5 rounded-t {% if tab == 'financials' %}bg-white border border-b-white border-slate-200 font-medium{% else %}text-slate-500 hover:text-slate-700{% endif %}">Financials</a>
    <a href="{% url 'deal-detail' deal.pk %}?tab=risks"
       class="px-3 py-1.5 rounded-t {% if tab == 'risks' %}bg-white border border-b-white border-slate-200 font-medium{% else %}text-slate-500 hover:text-slate-700{% endif %}">Risks</a>
  </nav>
  {% if tab == "summary" %}{% include "webapp/_tab_summary.html" %}
  {% elif tab == "returns" %}{% include "webapp/_tab_returns.html" %}
  {% elif tab == "financials" %}{% include "webapp/_tab_financials.html" %}
  {% elif tab == "risks" %}{% include "webapp/_tab_risks.html" %}{% endif %}
  {% endif %}
</div>
{% endblock %}
```

**Implementation notes for this template (the engineer applies these, they are not optional):** the `{% for label, value in header.items %}{% endfor %}` line and the `"…"|split_hack` line above are LEFTOVER SCAFFOLD ILLUSTRATIONS — DELETE both empty for-loops; the four metric cards and four tab links are written out explicitly as shown. The `{% include "webapp/_run_status.html" %}` must be plain `{% include "webapp/_run_status.html" with failed=run_failed %}` — add `"run_failed": state == "failed"` to the view context in Step 4 (`ctx["run_failed"] = state == "failed"`) instead of the slugify contortion shown.

- [ ] **Step 6: Create the four tab partials.**

`webapp/templates/webapp/_tab_summary.html`:

```html
<div class="space-y-4">
  <div class="rounded-md px-4 py-2.5 text-sm font-medium
              {% if rec_tone == 'pass' %}border border-green-200 bg-green-50 text-green-800
              {% elif rec_tone == 'warn' %}border border-amber-200 bg-amber-50 text-amber-800
              {% else %}border border-red-200 bg-red-50 text-red-800{% endif %}">
    {{ gate_summary.recommendation }} ({{ gate_summary.passed }}/{{ gate_summary.total }} gates passed)
  </div>

  <div>
    <h2 class="text-sm font-semibold mb-1.5">Go / No-Go Gates</h2>
    <div class="bg-white border border-slate-200 rounded-lg overflow-x-auto">
      <table class="w-full text-sm border-collapse">
        <thead>
          <tr class="text-left border-b border-slate-300 text-xs text-slate-600">
            <th class="py-1.5 pl-3 pr-3">#</th>
            <th class="py-1.5 pr-3">Gate</th>
            <th class="py-1.5 pr-3">Result</th>
            <th class="py-1.5 pr-3">Threshold</th>
            <th class="py-1.5 pr-3">Actual</th>
            <th class="py-1.5 pr-3">Note</th>
          </tr>
        </thead>
        <tbody>
          {% for g in gates %}
          <tr class="border-b border-slate-100">
            <td class="py-1.5 pl-3 pr-3 text-slate-500">{{ g.gate }}</td>
            <td class="py-1.5 pr-3 font-medium">{{ g.name }}</td>
            <td class="py-1.5 pr-3">
              <span class="inline-block rounded px-1.5 py-0.5 text-xs font-semibold
                    {% if g.result == 'PASS' %}bg-green-100 text-green-800
                    {% elif g.result == 'FAIL' %}bg-red-100 text-red-800
                    {% else %}bg-amber-100 text-amber-800{% endif %}">{{ g.result }}</span>
            </td>
            <td class="py-1.5 pr-3 text-slate-600">{{ g.threshold }}</td>
            <td class="py-1.5 pr-3">{{ g.actual }}</td>
            <td class="py-1.5 pr-3 text-slate-500">{{ g.note }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>

  {% if repl_estimable %}
  <div>
    <h2 class="text-sm font-semibold mb-1.5">Replacement Cost Estimate</h2>
    <div class="bg-white border border-slate-200 rounded-lg overflow-x-auto">
      <table class="w-full text-sm border-collapse">
        <thead>
          <tr class="text-left border-b border-slate-300 text-xs text-slate-600">
            <th class="py-1.5 pl-3 pr-3">Facility Type</th>
            <th class="py-1.5 pr-3 text-right">SF</th>
            <th class="py-1.5 pr-3 text-right">Hard $/SF</th>
            <th class="py-1.5 pr-3 text-right">Hard Cost</th>
          </tr>
        </thead>
        <tbody>
          {% for row in repl_rows %}
          <tr class="border-b border-slate-100">
            <td class="py-1.5 pl-3 pr-3">{{ row.type }}</td>
            <td class="py-1.5 pr-3 text-right">{{ row.sf }}</td>
            <td class="py-1.5 pr-3 text-right">{{ row.hard_rate }}</td>
            <td class="py-1.5 pr-3 text-right">{{ row.hard_cost }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
    <p class="mt-1.5 text-sm">
      <span class="font-medium">Total Replacement Cost: {{ repl_total }}</span>
      {% if repl_delta %} &middot; {{ repl_delta_label }}: {{ repl_delta }}{% endif %}
    </p>
  </div>
  {% endif %}
</div>
```

`webapp/templates/webapp/_tab_returns.html`:

```html
<div class="space-y-4">
  <div>
    <h2 class="text-sm font-semibold mb-1.5">Static Returns <span class="font-normal text-slate-500">(Unlevered, All-Equity)</span></h2>
    <div class="bg-white border border-slate-200 rounded-lg overflow-x-auto">
      <table class="w-full text-sm border-collapse">
        <thead>
          <tr class="text-left border-b border-slate-300 text-xs text-slate-600">
            <th class="py-1.5 pl-3 pr-3">Metric</th>
            <th class="py-1.5 pr-3 text-right">Bear</th>
            <th class="py-1.5 pr-3 text-right">Base</th>
            <th class="py-1.5 pr-3 text-right">Bull</th>
          </tr>
        </thead>
        <tbody>
          {% for row in scenario_rows %}
          <tr class="border-b border-slate-100">
            <td class="py-1.5 pl-3 pr-3 font-medium">{{ row.label }}</td>
            {% for cell in row.cells %}<td class="py-1.5 pr-3 text-right">{{ cell }}</td>{% endfor %}
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>

  {% if has_va %}
  <div>
    <h2 class="text-sm font-semibold mb-1.5">Value-Add Returns <span class="font-normal text-slate-500">(Unlevered, All-Equity)</span></h2>
    <div class="bg-white border border-slate-200 rounded-lg overflow-x-auto">
      <table class="w-full text-sm border-collapse">
        <thead>
          <tr class="text-left border-b border-slate-300 text-xs text-slate-600">
            <th class="py-1.5 pl-3 pr-3">Metric</th>
            <th class="py-1.5 pr-3 text-right">Bear</th>
            <th class="py-1.5 pr-3 text-right">Base</th>
            <th class="py-1.5 pr-3 text-right">Bull</th>
          </tr>
        </thead>
        <tbody>
          {% for row in va_rows %}
          <tr class="border-b border-slate-100">
            <td class="py-1.5 pl-3 pr-3 font-medium">{{ row.label }}</td>
            {% for cell in row.cells %}<td class="py-1.5 pr-3 text-right">{{ cell }}</td>{% endfor %}
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  {% endif %}

  <div class="grid grid-cols-2 gap-2 max-w-lg">
    <div class="rounded-md border border-slate-200 bg-white px-3 py-2">
      <div class="text-[11px] uppercase tracking-wide text-slate-500">Max Offer — Static</div>
      <div class="text-sm font-semibold">{{ max_offer }}</div>
    </div>
    {% if has_va_max_offer %}
    <div class="rounded-md border border-slate-200 bg-white px-3 py-2">
      <div class="text-[11px] uppercase tracking-wide text-slate-500">Max Offer — Value-Add</div>
      <div class="text-sm font-semibold">{{ va_max_offer }}</div>
    </div>
    {% endif %}
  </div>

  {% if has_sensitivity %}
  <div>
    <h2 class="text-sm font-semibold mb-1.5">IRR Sensitivity <span class="font-normal text-slate-500">(Price × Exit Cap)</span></h2>
    <div class="bg-white border border-slate-200 rounded-lg overflow-x-auto">
      <table class="w-full text-sm border-collapse">
        <thead>
          <tr class="text-left border-b border-slate-300 text-xs text-slate-600">
            <th class="py-1.5 pl-3 pr-3">Price \ Exit Cap</th>
            {% for cap in sens_caps %}<th class="py-1.5 pr-3 text-right">{{ cap }}</th>{% endfor %}
          </tr>
        </thead>
        <tbody>
          {% for row in sens_rows %}
          <tr class="border-b border-slate-100">
            <td class="py-1.5 pl-3 pr-3 font-medium whitespace-nowrap">{{ row.price }}</td>
            {% for cell in row.cells %}<td class="py-1.5 pr-3 text-right">{{ cell }}</td>{% endfor %}
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  {% endif %}
</div>
```

`webapp/templates/webapp/_tab_financials.html`:

```html
<div class="space-y-4 max-w-3xl">
  <div class="grid grid-cols-3 gap-2">
    <div class="rounded-md border border-slate-200 bg-white px-3 py-2">
      <div class="text-[11px] uppercase tracking-wide text-slate-500">CIM TTM NOI</div>
      <div class="text-sm font-semibold">{{ cim_noi }}</div>
    </div>
    <div class="rounded-md border border-slate-200 bg-white px-3 py-2">
      <div class="text-[11px] uppercase tracking-wide text-slate-500">Analyst-Adjusted NOI</div>
      <div class="text-sm font-semibold">{{ adj_noi }}</div>
    </div>
    <div class="rounded-md border border-slate-200 bg-white px-3 py-2">
      <div class="text-[11px] uppercase tracking-wide text-slate-500">OpEx / Revenue</div>
      <div class="text-sm font-semibold">{{ expense_ratio }}</div>
    </div>
  </div>

  {% if adjustments %}
  <div>
    <h2 class="text-sm font-semibold mb-1.5">Expense Adjustments</h2>
    <ul class="list-disc pl-5 text-sm text-slate-700 space-y-0.5">
      {% for a in adjustments %}<li>{{ a }}</li>{% endfor %}
    </ul>
  </div>
  {% endif %}

  {% if benchmark_rows %}
  <div>
    <h2 class="text-sm font-semibold mb-1.5">Expense Benchmarks <span class="font-normal text-slate-500">($/NRSF/yr, state-adjusted)</span></h2>
    <div class="bg-white border border-slate-200 rounded-lg overflow-x-auto">
      <table class="w-full text-sm border-collapse">
        <thead>
          <tr class="text-left border-b border-slate-300 text-xs text-slate-600">
            <th class="py-1.5 pl-3 pr-3">Category</th>
            <th class="py-1.5 pr-3 text-right">CIM</th>
            <th class="py-1.5 pr-3 text-right">Low</th>
            <th class="py-1.5 pr-3 text-right">High</th>
          </tr>
        </thead>
        <tbody>
          {% for row in benchmark_rows %}
          <tr class="border-b border-slate-100">
            <td class="py-1.5 pl-3 pr-3">{{ row.category }}</td>
            <td class="py-1.5 pr-3 text-right">{% if row.cim is not None %}${{ row.cim|floatformat:2 }}{% else %}—{% endif %}</td>
            <td class="py-1.5 pr-3 text-right">${{ row.low|floatformat:2 }}</td>
            <td class="py-1.5 pr-3 text-right">${{ row.high|floatformat:2 }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  {% endif %}
</div>
```

`webapp/templates/webapp/_tab_risks.html`:

```html
{% if risks %}
<div class="bg-white border border-slate-200 rounded-lg overflow-x-auto">
  <table class="w-full text-sm border-collapse">
    <thead>
      <tr class="text-left border-b border-slate-300 text-xs text-slate-600">
        <th class="py-1.5 pl-3 pr-3">Risk</th>
        <th class="py-1.5 pr-3">Severity</th>
        <th class="py-1.5 pr-3">Detail</th>
        <th class="py-1.5 pr-3">Mitigation</th>
      </tr>
    </thead>
    <tbody>
      {% for r in risks %}
      <tr class="border-b border-slate-100 align-top">
        <td class="py-1.5 pl-3 pr-3 font-medium">{{ r.risk }}</td>
        <td class="py-1.5 pr-3">
          <span class="inline-block rounded px-1.5 py-0.5 text-xs font-semibold
                {% if r.severity == 'HIGH' %}bg-red-100 text-red-800
                {% elif r.severity == 'MEDIUM' %}bg-amber-100 text-amber-800
                {% else %}bg-slate-100 text-slate-700{% endif %}">{{ r.severity }}</span>
        </td>
        <td class="py-1.5 pr-3 text-slate-600">{{ r.detail }}</td>
        <td class="py-1.5 pr-3 text-slate-600">{{ r.mitigation }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% else %}
<p class="text-sm text-slate-600">No significant risks identified.</p>
{% endif %}
```

- [ ] **Step 7: Run tests** — `.venv/bin/python -m pytest tests/test_web_runs.py -v 2>&1 | tail -12` → all pass. (The download link `{% url 'deal-download' ... %}` will fail template rendering until Task 6 registers the URL — if so, register the URL + a stub `deal_download` view returning 404 in THIS task's Step 4 and note it; Task 6 replaces the stub.)

- [ ] **Step 8: Full suite + commit**

```bash
git add webapp/results.py webapp/views.py webapp/templates/ tests/test_web_runs.py
git commit -m "feat(web): Deal detail page — header metrics, run controls, four results tabs"
```

### Task 6: Download endpoints + list/dupes linking

**Files:**
- Modify: `webapp/views.py`, `webapp/urls.py`
- Modify: `webapp/templates/webapp/deal_list.html`, `webapp/templates/webapp/analyze_dupes.html`, `webapp/templates/webapp/assumptions.html`
- Test: append to `tests/test_web_runs.py`; adjust one Phase 3 assertion in `tests/test_web_analyze.py`

**Interfaces:**
- Consumes: `AnalysisRun` filename fields, `Deal.memo_filename`/`excel_filename` (legacy fallback), `CIM_DEALS_DIR` containment guard pattern from `deal_discard`.
- Produces: URL name `deal-download` (`/deals/<pk>/download/<str:kind>/`), kinds `memo|excel|template`.

- [ ] **Step 1: Write the failing tests** (append):

```python
@pytest.mark.django_db
def test_download_endpoints_serve_run_outputs(client, operator, deals_dir, fake_run):
    deal = _run_deal(client, deals_dir)
    for kind, filename, mime in [
        ("memo", "Expo_Storage_memo.docx",
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("excel", "Expo_Storage_model.xlsx",
         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("template", "Expo_Storage_uw.xlsm",
         "application/vnd.ms-excel.sheet.macroEnabled.12"),
    ]:
        resp = client.get(f"/deals/{deal.pk}/download/{kind}/")
        assert resp.status_code == 200
        assert filename in resp.headers["Content-Disposition"]
        assert resp.headers["Content-Type"] == mime


@pytest.mark.django_db
def test_download_legacy_deal_falls_back_to_deal_row(client, operator, deals_dir):
    from webapp.models import Deal
    folder = deals_dir / "legacy"
    folder.mkdir()
    (folder / "memo.docx").write_bytes(b"legacy-memo")
    legacy = Deal.objects.create(deal_id="legacy", property_name="Legacy",
                                 deal_dir=str(folder), memo_filename="memo.docx")
    resp = client.get(f"/deals/{legacy.pk}/download/memo/")
    assert resp.status_code == 200
    resp = client.get(f"/deals/{legacy.pk}/download/template/")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_download_rejects_bad_kind_missing_file_and_escape(client, operator, deals_dir):
    from webapp.models import Deal
    deal = _make_extracted_deal(deals_dir)
    assert client.get(f"/deals/{deal.pk}/download/nope/").status_code == 404
    assert client.get(f"/deals/{deal.pk}/download/memo/").status_code == 404  # no file yet
    # a poisoned stored filename must not escape the deal folder
    outside = deals_dir.parent / "secret.docx"
    outside.write_bytes(b"secret")
    deal.memo_filename = "../../secret.docx"
    deal.save()
    assert client.get(f"/deals/{deal.pk}/download/memo/").status_code == 404


@pytest.mark.django_db
def test_deal_list_links_detail(client, operator, deals_dir, fake_run):
    deal = _run_deal(client, deals_dir)
    resp = client.get("/deals/")
    assert f'href="/deals/{deal.pk}/"'.encode() in resp.content
```

- [ ] **Step 2: Run to verify failure** — 404s / missing links.

- [ ] **Step 3: Implement `deal_download`** (append to `webapp/views.py`; add `from django.http import FileResponse, Http404` to imports — keep the existing `HttpResponse, JsonResponse` names in that import line):

```python
DOWNLOAD_KINDS = {
    "memo": ("memo_filename",
             "application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document"),
    "excel": ("excel_filename",
              "application/vnd.openxmlformats-officedocument"
              ".spreadsheetml.sheet"),
    "template": ("template_filename",
                 "application/vnd.ms-excel.sheet.macroEnabled.12"),
}


@login_required
def deal_download(request, pk, kind):
    deal = get_object_or_404(Deal, pk=pk)
    if kind not in DOWNLOAD_KINDS:
        raise Http404
    field, mime = DOWNLOAD_KINDS[kind]
    run = deal.runs.filter(status="done").first()
    filename = (getattr(run, field, "") if run else "") or \
        getattr(deal, field, "")  # Deal has no template_filename → ""
    filename = os.path.basename(filename or "")
    if not filename or not deal.deal_dir:
        raise Http404
    path = os.path.realpath(os.path.join(deal.deal_dir, filename))
    deals_root = os.path.realpath(settings.CIM_DEALS_DIR)
    if not path.startswith(deals_root + os.sep) or not os.path.isfile(path):
        raise Http404
    return FileResponse(open(path, "rb"), as_attachment=True,
                        filename=filename, content_type=mime)
```

Add to `webapp/urls.py`:

```python
    path("deals/<int:pk>/download/<str:kind>/", views.deal_download,
         name="deal-download"),
```

(If Task 5 Step 7 required a stub for URL resolution, this replaces it.)

- [ ] **Step 4: Link the pipeline list to detail** — in `deal_list.html`, wrap the property-name cell content:

```html
        <td class="py-1.5 pr-3 font-medium">
          <a href="{% url 'deal-detail' d.pk %}" class="text-accent-700 hover:underline">{{ d.property_name }}</a>
        </td>
```

In `analyze_dupes.html`, change the deal-row "Open" link target from `{% url 'deal-assumptions' d.deal_pk %}` to `{% url 'deal-detail' d.deal_pk %}`. In `assumptions.html`, add a breadcrumb link back to the deal at the top of the page header area:

```html
  <a href="{% url 'deal-detail' deal.pk %}" class="text-xs text-slate-500 hover:underline">&larr; {{ deal.property_name }}</a>
```

- [ ] **Step 5: Update the one Phase 3 assertion** — `tests/test_web_analyze.py::test_deal_list_links_extracted_deals` asserted the list links to `/deals/<pk>/assumptions/`; the list now links to the detail page. Change the assertion to:

```python
    assert f"/deals/{deal.pk}/".encode() in resp.content
```

and rename the test to `test_deal_list_links_deal_detail`. (Same behavior contract — a row links to the deal's page — the destination moved by design. Note in the commit body.)

- [ ] **Step 6: Run tests** — `.venv/bin/python -m pytest tests/test_web_runs.py tests/test_web_analyze.py -v 2>&1 | tail -8` → all pass.

- [ ] **Step 7: Full suite + commit**

```bash
git add webapp/ tests/
git commit -m "feat(web): download endpoints with containment guard; pipeline rows link to deal detail"
```

### Task 7: Tailwind rebuild, UI passes, docs

**Files:**
- Modify: `static/css/tw.css` (rebuilt), templates per UI-pass findings
- Modify: `docs/superpowers/plans/2026-07-24-django-frontend.md` (Phase 4 row → link this doc)

- [ ] **Step 1: Rebuild Tailwind** (new classes: green-50/100/800, tab classes, progress bar):

Run: `TAILWINDCSS_VERSION=v3.4.17 .venv/bin/tailwindcss -c tailwind.config.js -i static/src/input.css -o static/css/tw.css --minify && ls -la static/css/tw.css`

- [ ] **Step 2: UI pass 1 — layout/compaction agent** on `deal_detail.html` + the four tab partials + `_run_status.html` + the `assumptions.html` button changes. Paste the rendered template sources into the agent prompt; scope it to those files only.

- [ ] **Step 3: UI pass 2 — independent adversarial density agent**, fresh context, same files, explicitly instructed: reject any finding that sacrifices label/field visibility. Apply surviving findings from both passes; rebuild Tailwind if classes changed.

- [ ] **Step 4: Update the roadmap row** — in `docs/superpowers/plans/2026-07-24-django-frontend.md`, Phase 4 row: `Own plan doc` → `docs/superpowers/plans/2026-07-27-phase4-analysis-runs-results.md`.

- [ ] **Step 5: Full gate**

Run: `.venv/bin/python manage.py check && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -2`
Expected: check silent; all tests pass (expect ~127; report exact `N passed (was 101)` in the PR).

- [ ] **Step 6: Commit**

```bash
git add static/css/tw.css webapp/templates/ docs/superpowers/plans/
git commit -m "style(web): Tailwind rebuild + both UI passes on Phase 4 pages; roadmap link"
```

### Task 8: Phase 4 PR

- [ ] **Step 1:** Standard-tier cycle: `git diff main` → ONE review pass (paste the diff; review scoped to changed files) → repair critical/moderate findings → re-review only if any were found → push → PR (body: Why → What → Tests `N passed (was 101)` → Test plan → Out of scope: comps browser/settings editor/deploy = Phase 5; enrichment source tags on gates = decision #7) → CI green → squash-merge → delete branch → verify locally: `import_deals` against the real `deals/` tree still round-trips (meta files written by web runs import onto their existing rows, no duplicates).

---

## Self-Review (performed at write time)

1. **Spec coverage:** roadmap Phase 4 row = "AnalysisRun model (status/progress/result_json/output paths)" → Task 1; "threaded run + HTMX poll" → Tasks 3–4; "results pages (Summary gates + replacement cost, Returns + sensitivity, Financials, Risks)" → Task 5 (four tabs mirror `gui/pages/upload_analyze.py::_render_results` + the four retired components); "download endpoints (.docx/.xlsx/.xlsm)" → Task 6; "Deal detail becomes real" → Tasks 5–6 (list + dupes pages link to it). Assumption overrides reach the pipeline (cim/scenario/va/rc/solver — all five Phase 3 sections) → Tasks 2–3.
2. **Placeholder scan:** Task 5's detail template contains two flagged scaffold lines with explicit deletion instructions in the implementation notes — deliberate, since the surrounding markup is complete. Task 4 registers `deal-detail` with a declared placeholder body that Task 5 replaces (needed so `redirect("deal-detail", ...)` resolves in Task 4's tests). No other TBDs.
3. **Type consistency:** `result_json` keys written in Task 3's payload match the keys read by `webapp/results.py` in Task 5 (`gate_results`, `gate_summary`, `scenario_results`, `sensitivity`, `va_results`, `max_offer`, `va_max_offer`, `financial_analysis`, `physical_analysis`, `risk_analysis`, `errors`). `fake_run` fixture fields match `AnalysisResult` attribute names in `gui/engine.py`. Filename fields (`memo_filename`/`excel_filename`/`template_filename`) consistent across model, worker, `DOWNLOAD_KINDS`, and tests. `_run_state` return vocabulary consistent between Tasks 4 and 5. URL names `deal-detail`/`deal-run`/`run-status`/`deal-download` consistent across urls.py, views, templates, tests.
