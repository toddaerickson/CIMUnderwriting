# Phase 3 — Upload, Background Extract, Assumptions Editor: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Web upload of a CIM PDF (+ optional rent roll / financials) → duplicate check → background extraction with HTMX status polling → extraction report → full assumptions editor persisting per-deal overrides, replacing the Streamlit Upload & Analyze page up to (but not including) the Run Analysis step.

**Architecture:** One PR, standard tier. Upload always creates the deal folder + `Deal` row and starts extraction immediately; duplicate matches render a confirm page *after* creation (keep / discard) instead of a staging area — one creation path, no re-upload friction. Extraction runs `gui.engine.extract_pdf_data()` in a daemon thread (synchronous in tests via a settings flag), persists the `CIMData` snapshot as JSON on the `Deal` row, and is CAS-guarded on an `extract_requested_at` stamp (managertools PR 8 stale-thread lesson). The assumptions editor is a single Django form over collapsible `<details>` sections; percent fields display whole numbers (type 6 for 6%); saves store **deltas only** in `Deal.assumption_overrides`.

**Tech Stack:** Existing Phase 1/2 stack (Django 5.1, django-htmx, compiled Tailwind 3.4.17). htmx vendored from managertools (`static/js/htmx.min.js`) — first htmx usage in this repo.

## Global Constraints

- Analysis pipeline read-only: no edits to `analysis/`, `model/`, `output/`, `extract/`, `config.py`, `run.py`, `gui/` — webapp only imports from them.
- Single source of truth: `webapp/services.py` imports `sanitize_name` / `detect_asset_type` from `gui.deal_manager` (stdlib-only module, safe to import) rather than copying them; the one deliberate duplicate (`REQUIRED_FIELDS`) gets a CI parity test.
- Per-deal `assumption_overrides` stores **deltas**: CIM fields only when they differ from the extracted snapshot; scenario/VA/solver/replacement-cost sections only when they differ from `config.py` defaults (full section stored when any value differs, so past analyses keep the values they ran under).
- Percentage inputs as whole numbers (type 6 for 6%) — conversion at the initial-building and override-building boundaries in `webapp/forms.py`, never in custom form fields (bound redisplay must round-trip raw strings).
- Money/measure fields stay floats (display metadata, not accounting).
- Every new page needs both UI passes; these are NEW pages → **two independent agents** (layout/compaction, then adversarial density).
- Existing 70 tests stay green; `makemigrations --check` CI gate means the migration must be committed with the model change.
- Tailwind rebuilds carry `TAILWINDCSS_VERSION=v3.4.17`.
- File-count justification: `webapp/services.py` (earmarked by the Phase 5 retirement table as the deal_manager successor), `webapp/forms.py` (Django convention; keeps views lean), 6 templates (one per page/partial), 1 vendored js, 1 migration, 1 test file. `webapp/views.py` stays a single module this phase (~350 lines); Phase 4 may split it.

## Design Decisions (locked)

1. **Create-then-confirm dupes.** POST `/analyze/` always creates the deal (slug auto-suffixed `-v2`, `-v3`… on collision) and starts extraction. If `find_upload_duplicates()` matched (checked *before* creation so the new row can't match itself), render `analyze_dupes.html` offering "Keep & continue" (→ assumptions page) or "Discard this upload" (guarded delete of the just-created row + folder). No staging directory.
2. **`deal_id` = unique slug of the PDF filename stem** for web uploads (Streamlit derived it from property name, which isn't known until extraction). `property_name`/`city`/`state`/`asset_type`/`nrsf`/`acreage`/`asking_price` are refreshed onto the row when extraction completes. Phase 4's `deal_meta.json` writer must use the row's `deal_id`, not recompute from property name.
3. **Extraction state lives on `Deal`** (Phase 4 adds `AnalysisRun` for analysis runs): `extract_status` (`""`=imported/no snapshot, `pending`, `running`, `done`, `failed`), `extract_requested_at`, `extract_error` (fatal), `extract_warnings` (engine's non-fatal `result.errors`), `cim_json`, `extraction_report`, `assumption_overrides`.
4. **Threading:** `settings.EXTRACT_USE_THREAD` (default True; False in `settings_test` → synchronous, same reasoning as managertools' COACHING_ENABLED note — a daemon thread against the in-memory test DB leaks writes past rollback). Worker closes connections only in thread mode. Terminal timeout 180 s (`EXTRACT_TIMEOUT_SECONDS`) — the poll partial switches to a failed/retry state; retry stamps a new `extract_requested_at`, which makes any still-running stale thread's CAS update a no-op.
5. **Poll pattern** copied from managertools `journal_coaching`: partial re-requests itself via `hx-trigger="load delay:2s"`; polling stops when the swapped fragment lacks `hx-trigger`; `done` → `HttpResponseClientRedirect` to the assumptions page.
6. **Comp-DB dupe check is advisory:** wrapped so a broken/missing comp DB logs loudly (`logger.exception`) but never blocks an upload. Tests monkeypatch `webapp.services._comp_db_dupes` (the real `CompDatabase()` would touch `data/cim_comps.db`).
7. **Unit-mix editor:** parallel `getlist` arrays (`um_label`, `um_count`, `um_sf`, `um_rate`, `um_cc`); CC is a Yes/No `<select>` (always submits — keeps arrays aligned, unlike checkboxes); add-row via htmx `beforeend` append of a blank-row partial; remove via inline `this.closest('tr').remove()`. Rows with count ≤ 0 are dropped on parse (Streamlit parity).

## File Structure

- Modify: `webapp/models.py` (Deal extraction/assumption fields)
- Create: `webapp/migrations/0002_*.py` (generated)
- Create: `webapp/services.py` (serialization, folder/upload, dupes, extraction worker, benchmark rows)
- Create: `webapp/forms.py` (AssumptionsForm, section/grid definitions, initial + delta builders, unit-mix parsing)
- Modify: `webapp/views.py` (analyze, deal_discard, extract_status, extract_retry, deal_assumptions, unit_mix_row)
- Modify: `webapp/urls.py`
- Create: `webapp/templates/webapp/analyze.html`, `analyze_dupes.html`, `assumptions_wait.html`, `assumptions.html`, `_extract_status.html`, `_unit_mix_row.html`
- Modify: `webapp/templates/base.html` (htmx script tag, New Analysis nav link), `webapp/templates/webapp/deal_list.html` (assumptions link)
- Create: `static/js/htmx.min.js` (vendored from managertools)
- Modify: `cimweb/settings_test.py` (`EXTRACT_USE_THREAD = False`)
- Modify: `static/css/tw.css` (rebuilt)
- Test: `tests/test_web_analyze.py`

---

### Task 1: Deal extraction fields + CIMData serialization

**Files:**
- Modify: `webapp/models.py`
- Create: `webapp/migrations/0002_*.py` (generated), `webapp/services.py`
- Test: `tests/test_web_analyze.py`

**Interfaces:**
- Produces: `Deal.extract_status/extract_requested_at/extract_error/extract_warnings/cim_json/extraction_report/assumption_overrides`; `services.cim_to_dict(cim_data) -> dict`; `services.cim_from_dict(d) -> CIMData`.

- [ ] **Step 1: Write the failing tests** — new file `tests/test_web_analyze.py`:

```python
"""Phase 3: upload, background extract, assumptions editor tests."""
import json
import os

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile


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


@pytest.mark.django_db
def test_deal_extraction_defaults():
    from webapp.models import Deal
    d = Deal.objects.create(deal_id="x", property_name="X")
    assert d.extract_status == ""
    assert d.extract_warnings == []
    assert d.assumption_overrides == {}
    assert d.cim_json is None


def test_cim_dict_round_trip():
    from webapp.services import cim_from_dict, cim_to_dict
    cim = _sample_cim()
    restored = cim_from_dict(json.loads(json.dumps(cim_to_dict(cim))))
    assert restored.property_name == "Expo Storage"
    assert restored.nrsf == 45000.0
    assert len(restored.unit_mix) == 2
    assert restored.unit_mix[1].climate_controlled is True
    assert type(restored.unit_mix[0]).__name__ == "UnitType"


def test_cim_from_dict_ignores_unknown_keys():
    """Schema drift: a stored snapshot with a since-removed key must not crash."""
    from webapp.services import cim_from_dict, cim_to_dict
    d = cim_to_dict(_sample_cim())
    d["some_removed_field"] = 1
    assert cim_from_dict(d).property_name == "Expo Storage"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web_analyze.py -v 2>&1 | tail -4`
Expected: FAIL (no `webapp.services`; Deal lacks `extract_status`).

- [ ] **Step 3: Add fields to `webapp/models.py`** — append inside `class Deal` after `input_files`:

```python
    # ── Phase 3: web upload + extraction state ──
    # ""=imported (no snapshot), then pending → running → done|failed.
    extract_status = models.CharField(max_length=10, blank=True, default="")
    extract_requested_at = models.DateTimeField(null=True, blank=True)
    extract_error = models.TextField(blank=True, default="")
    extract_warnings = models.JSONField(default=list, blank=True)
    cim_json = models.JSONField(null=True, blank=True)
    extraction_report = models.JSONField(null=True, blank=True)
    assumption_overrides = models.JSONField(default=dict, blank=True)
```

- [ ] **Step 4: Create `webapp/services.py`** (serialization part; extraction/upload functions come in Tasks 2–4):

```python
"""Deal-folder, extraction, and duplicate-check services for the web UI.

Absorbs gui/deal_manager responsibilities per the Phase 5 retirement map;
imports the shared helpers from there (stdlib-only module) rather than
copying them, so there is one source of truth until gui/ retires.
"""
import dataclasses
import logging
import os
import threading

from django.conf import settings
from django.utils import timezone

from gui.deal_manager import detect_asset_type, sanitize_name
from gui.engine import extract_pdf_data
from webapp.models import Deal

logger = logging.getLogger("cim_analyst.web")


# ── CIMData snapshot serialization ──────────────────────────────────

def cim_to_dict(cim_data) -> dict:
    """CIMData → JSON-safe dict (nested dataclasses included)."""
    return dataclasses.asdict(cim_data)


def cim_from_dict(d: dict):
    """Rehydrate a stored snapshot; unknown keys (schema drift) dropped."""
    from extract.parser import CIMData, FinancialLine, UnitType

    known = {f.name for f in dataclasses.fields(CIMData)}
    data = {k: v for k, v in (d or {}).items() if k in known}
    data["unit_mix"] = [UnitType(**u) for u in data.get("unit_mix") or []]
    data["income_lines"] = [FinancialLine(**l) for l in data.get("income_lines") or []]
    data["expense_lines"] = [FinancialLine(**l) for l in data.get("expense_lines") or []]
    return CIMData(**data)
```

- [ ] **Step 5: Generate the migration and run tests**

Run: `.venv/bin/python manage.py makemigrations webapp && .venv/bin/python -m pytest tests/test_web_analyze.py -v 2>&1 | tail -5`
Expected: `0002_*.py` created; 3 tests pass.

- [ ] **Step 6: Full suite** — `.venv/bin/python -m pytest tests/ -q 2>&1 | tail -2` → 73 passed.

- [ ] **Step 7: Commit**

```bash
git add webapp/models.py webapp/migrations/ webapp/services.py tests/test_web_analyze.py
git commit -m "feat(web): Deal extraction-state fields + CIMData JSON snapshot round-trip"
```

### Task 2: Background extraction service

**Files:**
- Modify: `webapp/services.py`, `cimweb/settings_test.py`
- Test: append to `tests/test_web_analyze.py`

**Interfaces:**
- Consumes: Task 1 fields + serialization.
- Produces: `services.start_extract(deal) -> None` (stamps `running` + `extract_requested_at`, spawns `_extract_worker(deal_pk, pdf_path, stamp)` — inline when `settings.EXTRACT_USE_THREAD` is False); `services.EXTRACT_TIMEOUT_SECONDS = 180`.

- [ ] **Step 1: Write the failing tests** (append). The `fake_extract` fixture is reused by later tasks:

```python
@pytest.fixture
def fake_extract(monkeypatch):
    from gui.engine import AnalysisResult

    def _fake(pdf_path, cim_overrides=None, progress=None):
        cim = _sample_cim()
        r = AnalysisResult(pdf_path=pdf_path)
        r.cim_data = cim
        r.extraction_report = cim.extraction_report()
        r.errors = ["Enrichment skipped: test"]
        return r

    monkeypatch.setattr("webapp.services.extract_pdf_data", _fake)
    return _fake


def _make_upload_deal(deals_dir, slug="expo-cim"):
    from webapp.models import Deal
    folder = deals_dir / slug
    (folder / "inputs").mkdir(parents=True)
    (folder / "inputs" / "expo.pdf").write_bytes(b"%PDF-1.4 fake")
    return Deal.objects.create(deal_id=slug, property_name="expo",
                               deal_dir=str(folder), input_files=["expo.pdf"],
                               extract_status="pending")


@pytest.mark.django_db
def test_start_extract_success(deals_dir, fake_extract):
    from webapp import services
    deal = _make_upload_deal(deals_dir)
    services.start_extract(deal)
    deal.refresh_from_db()
    assert deal.extract_status == "done"
    assert deal.cim_json["property_name"] == "Expo Storage"
    assert deal.extraction_report["populated"] > 0
    assert deal.extract_warnings == ["Enrichment skipped: test"]
    # extraction refreshes display metadata on the row
    assert deal.property_name == "Expo Storage"
    assert deal.state == "TX"
    assert deal.asset_type != ""
    assert deal.nrsf == 45000.0


@pytest.mark.django_db
def test_start_extract_failure_records_error(deals_dir, monkeypatch):
    from webapp import services

    def boom(pdf_path, cim_overrides=None, progress=None):
        raise RuntimeError("pdf is garbage")

    monkeypatch.setattr("webapp.services.extract_pdf_data", boom)
    deal = _make_upload_deal(deals_dir)
    services.start_extract(deal)
    deal.refresh_from_db()
    assert deal.extract_status == "failed"
    assert "pdf is garbage" in deal.extract_error


@pytest.mark.django_db
def test_stale_extract_worker_is_dropped(deals_dir, fake_extract):
    """A worker holding an old stamp must not overwrite a retried extract."""
    from django.utils import timezone

    from webapp import services
    deal = _make_upload_deal(deals_dir)
    old_stamp = timezone.now()
    deal.extract_status = "running"
    deal.extract_requested_at = timezone.now()  # newer stamp = a retry happened
    deal.save()
    services._extract_worker(deal.pk, os.path.join(deal.deal_dir, "inputs", "expo.pdf"),
                             old_stamp)
    deal.refresh_from_db()
    assert deal.extract_status == "running"  # stale write dropped
    assert deal.cim_json is None
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_web_analyze.py -v 2>&1 | tail -5` → FAIL (no `start_extract`).

- [ ] **Step 3: Implement in `webapp/services.py`** (append):

```python
# ── Background extraction ───────────────────────────────────────────

EXTRACT_TIMEOUT_SECONDS = 180  # poll partial flips to failed/retry after this


def start_extract(deal) -> None:
    """Stamp the deal and run extraction (thread in prod, inline in tests).

    The stamp is a CAS token: a retry writes a new stamp, so a stale
    still-running worker's final update matches zero rows (managertools
    PR 8 stale-thread lesson).
    """
    pdf_path = os.path.join(deal.deal_dir, "inputs", deal.input_files[0])
    stamp = timezone.now()
    Deal.objects.filter(pk=deal.pk).update(
        extract_status="running", extract_requested_at=stamp, extract_error="")
    if getattr(settings, "EXTRACT_USE_THREAD", True):
        threading.Thread(target=_extract_worker,
                         args=(deal.pk, pdf_path, stamp), daemon=True).start()
    else:
        _extract_worker(deal.pk, pdf_path, stamp)


def _extract_worker(deal_pk, pdf_path, stamp):
    try:
        result = extract_pdf_data(pdf_path)
        cim = result.cim_data
        updates = {
            "cim_json": cim_to_dict(cim),
            "extraction_report": result.extraction_report,
            "extract_warnings": list(result.errors),
            "extract_status": "done",
            "extract_error": "",
            "asset_type": detect_asset_type(cim),
        }
        if cim.property_name:
            updates["property_name"] = cim.property_name[:200]
        if cim.city:
            updates["city"] = cim.city[:100]
        if cim.state:
            updates["state"] = cim.state[:2].upper()
        if cim.nrsf:
            updates["nrsf"] = cim.nrsf
        if cim.acreage:
            updates["acreage"] = cim.acreage
        if cim.asking_price:
            updates["asking_price"] = cim.asking_price
        matched = Deal.objects.filter(
            pk=deal_pk, extract_requested_at=stamp).update(**updates)
        if not matched:
            logger.warning("extract worker: stale thread for deal %s dropped", deal_pk)
    except Exception as e:
        logger.exception("extract worker failed for deal %s", deal_pk)
        Deal.objects.filter(pk=deal_pk, extract_requested_at=stamp).update(
            extract_status="failed", extract_error=str(e)[:2000])
    finally:
        if getattr(settings, "EXTRACT_USE_THREAD", True):
            from django.db import connections
            connections.close_all()
```

- [ ] **Step 4: Add to `cimweb/settings_test.py`**:

```python
# Extraction runs inline: a daemon thread opens its own connection to the
# shared in-memory test DB and its writes commit outside the test
# transaction (same reasoning as managertools' COACHING_ENABLED note).
EXTRACT_USE_THREAD = False
```

- [ ] **Step 5: Run tests** — `.venv/bin/python -m pytest tests/test_web_analyze.py -v 2>&1 | tail -6` → all pass.

- [ ] **Step 6: Commit**

```bash
git add webapp/services.py cimweb/settings_test.py tests/test_web_analyze.py
git commit -m "feat(web): background extraction worker with CAS stale-thread guard"
```

### Task 3: Poll endpoint, retry, wait shell, htmx vendoring

**Files:**
- Modify: `webapp/views.py`, `webapp/urls.py`, `webapp/templates/base.html`
- Create: `webapp/templates/webapp/_extract_status.html`, `webapp/templates/webapp/assumptions_wait.html`, `static/js/htmx.min.js` (vendored)
- Test: append to `tests/test_web_analyze.py`

**Interfaces:**
- Consumes: Task 2 services.
- Produces: URL names `deal-assumptions` (`/deals/<pk>/assumptions/`), `extract-status` (`/deals/<pk>/extract-status/`), `extract-retry` (`/deals/<pk>/extract-retry/`); view helper `_extract_state(deal) -> "done"|"failed"|"running"` (timeout counts as failed). `deal_assumptions` renders the wait/unavailable shell; its `done` branch is a placeholder replaced in Task 5.

- [ ] **Step 1: Write the failing tests** (append):

```python
@pytest.mark.django_db
def test_extract_status_running_polls(client, operator, deals_dir):
    from django.utils import timezone
    deal = _make_upload_deal(deals_dir)
    deal.extract_status = "running"
    deal.extract_requested_at = timezone.now()
    deal.save()
    resp = client.get(f"/deals/{deal.pk}/extract-status/")
    assert resp.status_code == 200
    assert b"hx-trigger" in resp.content  # keeps polling


@pytest.mark.django_db
def test_extract_status_done_redirects(client, operator, deals_dir):
    deal = _make_upload_deal(deals_dir)
    deal.extract_status = "done"
    deal.save()
    resp = client.get(f"/deals/{deal.pk}/extract-status/")
    assert resp.status_code == 200
    assert resp.headers["HX-Redirect"] == f"/deals/{deal.pk}/assumptions/"


@pytest.mark.django_db
def test_extract_status_failed_and_timeout_stop_polling(client, operator, deals_dir):
    import datetime

    from django.utils import timezone
    deal = _make_upload_deal(deals_dir)
    deal.extract_status = "failed"
    deal.extract_error = "boom"
    deal.save()
    resp = client.get(f"/deals/{deal.pk}/extract-status/")
    assert b"hx-trigger" not in resp.content
    assert b"Retry extraction" in resp.content
    # timeout: still "running" but stamp is too old
    deal.extract_status = "running"
    deal.extract_error = ""
    deal.extract_requested_at = timezone.now() - datetime.timedelta(seconds=999)
    deal.save()
    resp = client.get(f"/deals/{deal.pk}/extract-status/")
    assert b"hx-trigger" not in resp.content
    assert b"Retry extraction" in resp.content


@pytest.mark.django_db
def test_extract_retry_reruns(client, operator, deals_dir, fake_extract):
    deal = _make_upload_deal(deals_dir)
    deal.extract_status = "failed"
    deal.extract_error = "old error"
    deal.save()
    resp = client.post(f"/deals/{deal.pk}/extract-retry/")
    assert resp.status_code == 302
    deal.refresh_from_db()
    assert deal.extract_status == "done"  # sync mode ran inline
    assert deal.extract_error == ""


@pytest.mark.django_db
def test_assumptions_wait_and_unavailable(client, operator, deals_dir):
    from django.utils import timezone

    from webapp.models import Deal
    deal = _make_upload_deal(deals_dir)
    deal.extract_status = "running"
    deal.extract_requested_at = timezone.now()
    deal.save()
    resp = client.get(f"/deals/{deal.pk}/assumptions/")
    assert resp.status_code == 200
    assert b"Extracting" in resp.content
    imported = Deal.objects.create(deal_id="legacy", property_name="Legacy")
    resp = client.get(f"/deals/{imported.pk}/assumptions/")
    assert resp.status_code == 200
    assert b"no extraction snapshot" in resp.content.lower()
```

- [ ] **Step 2: Run to verify failure** — 404s (no routes).

- [ ] **Step 3: Vendor htmx and wire it into `base.html`**

Run: `mkdir -p static/js && cp /home/terickson/managertools/manager-tool-django/static/js/htmx.min.js static/js/htmx.min.js`

In `webapp/templates/base.html`, after the `tw.css` `<link>` add:

```html
  <script src="{% static 'js/htmx.min.js' %}" defer></script>
```

- [ ] **Step 4: Add views** (append to `webapp/views.py`; extend the module imports with `from django.contrib import messages`, `from django.http import HttpResponse`, `from django.shortcuts import get_object_or_404, redirect`, `from django.urls import reverse`, `from django.utils import timezone`, `from django.views.decorators.http import require_POST`, `from django_htmx.http import HttpResponseClientRedirect`, `from webapp import services`, `from webapp.models import Deal`):

```python
def _extract_state(deal) -> str:
    """'done' | 'failed' | 'running' — a stamp older than the timeout counts
    as failed so the UI never shows an eternal spinner."""
    if deal.extract_status == "done":
        return "done"
    if deal.extract_status == "failed":
        return "failed"
    if deal.extract_requested_at and (
            timezone.now() - deal.extract_requested_at
    ).total_seconds() > services.EXTRACT_TIMEOUT_SECONDS:
        return "failed"
    return "running"


@login_required
def extract_status(request, pk):
    deal = get_object_or_404(Deal, pk=pk)
    state = _extract_state(deal)
    if state == "done":
        return HttpResponseClientRedirect(reverse("deal-assumptions", args=[deal.pk]))
    return render(request, "webapp/_extract_status.html",
                  {"deal": deal, "failed": state == "failed"})


@login_required
@require_POST
def extract_retry(request, pk):
    deal = get_object_or_404(Deal, pk=pk)
    if deal.extract_status == "" or not deal.input_files:
        messages.error(request, "No uploaded CIM to re-extract.")
        return redirect("deal-list")
    services.start_extract(deal)
    return redirect("deal-assumptions", pk=deal.pk)


@login_required
def deal_assumptions(request, pk):
    deal = get_object_or_404(Deal, pk=pk)
    if deal.extract_status == "" and not deal.cim_json:
        return render(request, "webapp/assumptions_wait.html",
                      {"deal": deal, "unavailable": True})
    state = _extract_state(deal)
    if state != "done":
        return render(request, "webapp/assumptions_wait.html",
                      {"deal": deal, "failed": state == "failed"})
    return HttpResponse("Assumptions editor lands in Task 5.")  # replaced in Task 5
```

Add to `webapp/urls.py` urlpatterns:

```python
    path("deals/<int:pk>/assumptions/", views.deal_assumptions, name="deal-assumptions"),
    path("deals/<int:pk>/extract-status/", views.extract_status, name="extract-status"),
    path("deals/<int:pk>/extract-retry/", views.extract_retry, name="extract-retry"),
```

- [ ] **Step 5: Create `webapp/templates/webapp/_extract_status.html`**:

```html
{% if failed %}
<div id="extract-status" class="max-w-xl rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
  <p class="font-medium">Extraction failed{% if deal.extract_error %}: {{ deal.extract_error }}{% else %} or timed out.{% endif %}</p>
  <form method="post" action="{% url 'extract-retry' deal.pk %}" class="mt-2">
    {% csrf_token %}
    <button type="submit" class="bg-accent-700 text-white text-sm px-3 py-1.5 rounded">Retry extraction</button>
  </form>
</div>
{% else %}
<div id="extract-status"
     hx-get="{% url 'extract-status' deal.pk %}"
     hx-trigger="load delay:2s" hx-swap="outerHTML"
     class="max-w-xl rounded-md border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">
  <span class="inline-block animate-pulse">Extracting PDF data&hellip; this usually takes under a minute.</span>
</div>
{% endif %}
```

- [ ] **Step 6: Create `webapp/templates/webapp/assumptions_wait.html`**:

```html
{% extends "base.html" %}
{% block title %}{{ deal.property_name }} — Extraction{% endblock %}
{% block content %}
<div class="max-w-3xl">
  <h1 class="font-display text-xl font-semibold mb-3">{{ deal.property_name }}</h1>
  {% if unavailable %}
  <div class="rounded-md border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600">
    This deal was imported from a completed analysis folder — there is no extraction
    snapshot to edit. Re-upload the CIM under New Analysis to underwrite it again.
  </div>
  {% else %}
  {% include "webapp/_extract_status.html" %}
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 7: Run tests** — `.venv/bin/python -m pytest tests/test_web_analyze.py -v 2>&1 | tail -8` → all pass.

- [ ] **Step 8: Commit**

```bash
git add webapp/views.py webapp/urls.py webapp/templates/ static/js/htmx.min.js
git commit -m "feat(web): extraction status poll + retry + wait shell (vendored htmx)"
```

### Task 4: Upload page, duplicate confirm, discard

**Files:**
- Modify: `webapp/services.py`, `webapp/views.py`, `webapp/urls.py`, `webapp/templates/base.html`
- Create: `webapp/templates/webapp/analyze.html`, `webapp/templates/webapp/analyze_dupes.html`
- Test: append to `tests/test_web_analyze.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: URL names `analyze` (`/analyze/`), `deal-discard` (`/deals/<pk>/discard/`); `services.create_deal_from_upload(cim_file, rent_roll=None, financials=None) -> Deal`; `services.find_upload_duplicates(filename) -> list[dict]` (dicts carry `property_name/city/state/analysis_date/pdf_filename/match_type` and, for deal-row matches, `deal_pk`); `services.unique_deal_slug(base) -> str`; `services.ALLOWED_DOC_EXTS`, `services.MAX_UPLOAD_BYTES`.

- [ ] **Step 1: Write the failing tests** (append):

```python
def _pdf(name="expo.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 fake", content_type="application/pdf")


@pytest.mark.django_db
def test_analyze_requires_login(client):
    resp = client.get("/analyze/")
    assert resp.status_code == 302
    assert resp.url.startswith("/accounts/login/")


@pytest.mark.django_db
def test_upload_creates_deal_and_extracts(client, operator, deals_dir, fake_extract):
    from webapp.models import Deal
    resp = client.post("/analyze/", {"cim": _pdf()})
    deal = Deal.objects.get()
    assert resp.status_code == 302
    assert resp.url == f"/deals/{deal.pk}/assumptions/"
    assert deal.deal_id == "expo"
    assert deal.input_files == ["expo.pdf"]
    assert os.path.isfile(os.path.join(deal.deal_dir, "inputs", "expo.pdf"))
    deal.refresh_from_db()
    assert deal.extract_status == "done"  # sync mode
    assert deal.property_name == "Expo Storage"


@pytest.mark.django_db
def test_upload_saves_optional_files(client, operator, deals_dir, fake_extract):
    from webapp.models import Deal
    client.post("/analyze/", {
        "cim": _pdf(),
        "rent_roll": SimpleUploadedFile("rr.xlsx", b"fake", content_type="application/octet-stream"),
        "financials": SimpleUploadedFile("fin.csv", b"a,b", content_type="text/csv"),
    })
    deal = Deal.objects.get()
    assert deal.input_files == ["expo.pdf", "rr.xlsx", "fin.csv"]
    assert os.path.isfile(os.path.join(deal.deal_dir, "inputs", "rr.xlsx"))


@pytest.mark.django_db
def test_upload_validation(client, operator, deals_dir):
    from webapp.models import Deal
    resp = client.post("/analyze/", {})
    assert resp.status_code == 422
    resp = client.post("/analyze/", {"cim": SimpleUploadedFile("x.exe", b"z")})
    assert resp.status_code == 422
    resp = client.post("/analyze/", {"cim": _pdf(), "rent_roll": SimpleUploadedFile("x.exe", b"z")})
    assert resp.status_code == 422
    assert Deal.objects.count() == 0


@pytest.mark.django_db
def test_upload_slug_collision_gets_v2(client, operator, deals_dir, fake_extract, monkeypatch):
    from webapp import services
    from webapp.models import Deal
    monkeypatch.setattr(services, "_comp_db_dupes", lambda filename: [])
    client.post("/analyze/", {"cim": _pdf()})
    resp = client.post("/analyze/", {"cim": _pdf()})  # same filename again
    assert Deal.objects.count() == 2
    assert set(Deal.objects.values_list("deal_id", flat=True)) == {"expo", "expo-v2"}
    # second upload matched the first deal's input file → dupe confirm page
    assert resp.status_code == 200
    assert b"already exist" in resp.content


@pytest.mark.django_db
def test_comp_db_dupes_surface(client, operator, deals_dir, fake_extract, monkeypatch):
    from webapp import services
    monkeypatch.setattr(services, "_comp_db_dupes", lambda filename: [{
        "property_name": "Expo Storage", "city": "Belton", "state": "TX",
        "analysis_date": "2026-06-01", "pdf_filename": filename,
        "match_type": "filename",
    }])
    resp = client.post("/analyze/", {"cim": _pdf()})
    assert resp.status_code == 200
    assert b"Expo Storage" in resp.content
    assert b"Discard this upload" in resp.content


@pytest.mark.django_db
def test_discard_deletes_upload(client, operator, deals_dir, fake_extract):
    from webapp.models import Deal
    client.post("/analyze/", {"cim": _pdf()})
    deal = Deal.objects.get()
    folder = deal.deal_dir
    resp = client.post(f"/deals/{deal.pk}/discard/")
    assert resp.status_code == 302
    assert Deal.objects.count() == 0
    assert not os.path.isdir(folder)


@pytest.mark.django_db
def test_discard_refuses_imported_and_analyzed(client, operator, deals_dir):
    from webapp.models import Deal
    imported = Deal.objects.create(deal_id="legacy", property_name="Legacy",
                                   deal_dir=str(deals_dir / "legacy"))
    analyzed = Deal.objects.create(deal_id="done-deal", property_name="Done",
                                   deal_dir=str(deals_dir / "done-deal"),
                                   extract_status="done", memo_filename="memo.docx")
    for d in (imported, analyzed):
        os.makedirs(d.deal_dir, exist_ok=True)
        client.post(f"/deals/{d.pk}/discard/")
        assert Deal.objects.filter(pk=d.pk).exists()
        assert os.path.isdir(d.deal_dir)
```

- [ ] **Step 2: Run to verify failure** — 404s (no `/analyze/`).

- [ ] **Step 3: Add upload/dupe services** (append to `webapp/services.py`):

```python
# ── Upload / deal creation / duplicate check ────────────────────────

ALLOWED_DOC_EXTS = {".pdf", ".xlsx", ".xls", ".csv"}
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def _safe_filename(name: str) -> str:
    base = os.path.basename((name or "").replace("\x00", "")).strip()
    if base in ("", ".", ".."):
        raise ValueError("invalid filename")
    return base


def unique_deal_slug(base: str) -> str:
    """Free slug for a new upload; collisions get -v2, -v3, … (the web
    equivalent of Streamlit's 'Continue as New (v2)')."""
    base = (base or "deal")[:100]

    def taken(slug):
        return (Deal.objects.filter(deal_id=slug).exists()
                or os.path.isdir(os.path.join(settings.CIM_DEALS_DIR, slug)))

    if not taken(base):
        return base
    n = 2
    while taken(f"{base}-v{n}"):
        n += 1
    return f"{base}-v{n}"


def create_deal_from_upload(cim_file, rent_roll=None, financials=None) -> Deal:
    """Create the deal folder + inputs/ + Deal row from uploaded files.

    deal_id derives from the PDF filename stem (property name isn't known
    until extraction completes and refreshes the row)."""
    cim_name = _safe_filename(cim_file.name)
    stem = os.path.splitext(cim_name)[0]
    slug = unique_deal_slug(sanitize_name(stem).lower())
    deal_dir = os.path.join(settings.CIM_DEALS_DIR, slug)
    inputs_dir = os.path.join(deal_dir, "inputs")
    os.makedirs(inputs_dir, exist_ok=True)
    input_files = []
    for f in (cim_file, rent_roll, financials):
        if not f:
            continue
        name = _safe_filename(f.name)
        with open(os.path.join(inputs_dir, name), "wb") as out:
            for chunk in f.chunks():
                out.write(chunk)
        input_files.append(name)
    return Deal.objects.create(
        deal_id=slug, property_name=stem, deal_dir=deal_dir,
        input_files=input_files, extract_status="pending")


def _comp_db_dupes(filename: str) -> list[dict]:
    """Advisory comp-DB matches; a broken comp DB must not block an
    upload, but it must be loud in the logs."""
    try:
        from data.comp_db import CompDatabase
        stem = os.path.splitext(filename)[0]
        return CompDatabase().find_duplicates(filename=filename, property_name=stem)
    except Exception:
        logger.exception("comp DB duplicate check failed")
        return []


def find_upload_duplicates(filename: str) -> list[dict]:
    """Comp-DB matches + Deal rows whose input_files contain this
    filename. Call BEFORE create_deal_from_upload, or the new row
    matches itself."""
    dupes = _comp_db_dupes(filename)
    for deal in Deal.objects.all():
        if filename in (deal.input_files or []):
            dupes.append({
                "property_name": deal.property_name, "city": deal.city,
                "state": deal.state,
                "analysis_date": deal.analysis_date.isoformat() if deal.analysis_date else "",
                "pdf_filename": filename, "match_type": "deal_folder",
                "deal_pk": deal.pk,
            })
    return dupes
```

- [ ] **Step 4: Add views** (append to `webapp/views.py`; add `import shutil` and `from django.conf import settings` to the imports):

```python
@login_required
def analyze(request):
    if request.method != "POST":
        return render(request, "webapp/analyze.html")
    errors = []
    cim = request.FILES.get("cim")
    if not cim:
        errors.append("A CIM PDF is required.")
    elif not cim.name.lower().endswith(".pdf"):
        errors.append("The CIM must be a .pdf file.")
    optional = {}
    for key, label in (("rent_roll", "Rent roll"), ("financials", "Financials")):
        f = request.FILES.get(key)
        optional[key] = f
        if f is not None:
            ext = os.path.splitext(f.name)[1].lower()
            if ext not in services.ALLOWED_DOC_EXTS:
                errors.append(f"{label}: unsupported file type {ext or '(none)'}.")
    for f in [f for f in (cim, optional["rent_roll"], optional["financials"]) if f]:
        if f.size > services.MAX_UPLOAD_BYTES:
            errors.append(f"{f.name} is larger than 200 MB.")
    if errors:
        return render(request, "webapp/analyze.html", {"errors": errors}, status=422)
    dupes = services.find_upload_duplicates(os.path.basename(cim.name))
    try:
        deal = services.create_deal_from_upload(
            cim, rent_roll=optional["rent_roll"], financials=optional["financials"])
    except ValueError as e:
        return render(request, "webapp/analyze.html", {"errors": [str(e)]}, status=422)
    services.start_extract(deal)
    if dupes:
        return render(request, "webapp/analyze_dupes.html",
                      {"deal": deal, "dupes": dupes})
    return redirect("deal-assumptions", pk=deal.pk)


@login_required
@require_POST
def deal_discard(request, pk):
    """Delete a just-uploaded deal (dupe-confirm page). Refuses imported
    deals (no extraction state) and anything that already produced
    analysis outputs — those folders hold real history."""
    deal = get_object_or_404(Deal, pk=pk)
    deals_root = os.path.realpath(settings.CIM_DEALS_DIR)
    target = os.path.realpath(deal.deal_dir) if deal.deal_dir else ""
    if (deal.extract_status == "" or deal.memo_filename or deal.excel_filename
            or not target.startswith(deals_root + os.sep)):
        messages.error(request, "This deal can't be discarded from here.")
        return redirect("deal-list")
    shutil.rmtree(target, ignore_errors=True)
    name = deal.property_name
    deal.delete()
    messages.success(request, f"Discarded upload “{name}”.")
    return redirect("deal-list")
```

Add to `webapp/urls.py`:

```python
    path("analyze/", views.analyze, name="analyze"),
    path("deals/<int:pk>/discard/", views.deal_discard, name="deal-discard"),
```

- [ ] **Step 5: Create `webapp/templates/webapp/analyze.html`**:

```html
{% extends "base.html" %}
{% block title %}New Analysis{% endblock %}
{% block content %}
<div class="max-w-3xl">
  <h1 class="font-display text-xl font-semibold mb-3">New Analysis</h1>
  {% if errors %}
  <div class="mb-3 rounded-md border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-800">
    <ul class="list-disc pl-4">{% for e in errors %}<li>{{ e }}</li>{% endfor %}</ul>
  </div>
  {% endif %}
  <form method="post" enctype="multipart/form-data"
        class="bg-white border border-slate-200 rounded-lg p-4 space-y-3">
    {% csrf_token %}
    <div class="grid gap-3 sm:grid-cols-3">
      <label class="block text-xs font-medium text-slate-600">CIM (PDF, required)
        <input type="file" name="cim" accept=".pdf" required
               class="mt-1 block w-full text-sm text-slate-700 file:mr-2 file:rounded file:border-0 file:bg-slate-100 file:px-2 file:py-1.5 file:text-sm hover:file:bg-slate-200">
      </label>
      <label class="block text-xs font-medium text-slate-600">Rent Roll (optional)
        <input type="file" name="rent_roll" accept=".pdf,.xlsx,.xls,.csv"
               class="mt-1 block w-full text-sm text-slate-700 file:mr-2 file:rounded file:border-0 file:bg-slate-100 file:px-2 file:py-1.5 file:text-sm hover:file:bg-slate-200">
      </label>
      <label class="block text-xs font-medium text-slate-600">Financials (optional)
        <input type="file" name="financials" accept=".pdf,.xlsx,.xls,.csv"
               class="mt-1 block w-full text-sm text-slate-700 file:mr-2 file:rounded file:border-0 file:bg-slate-100 file:px-2 file:py-1.5 file:text-sm hover:file:bg-slate-200">
      </label>
    </div>
    <p class="text-xs text-slate-500">Files are stored in the deal folder; extraction starts immediately after upload.</p>
    <button type="submit" class="bg-accent-700 text-white text-sm px-4 py-1.5 rounded">Upload &amp; Extract</button>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 6: Create `webapp/templates/webapp/analyze_dupes.html`**:

```html
{% extends "base.html" %}
{% block title %}Possible Duplicate — {{ deal.property_name }}{% endblock %}
{% block content %}
<div class="max-w-4xl">
  <h1 class="font-display text-xl font-semibold mb-3">Possible duplicate</h1>
  <div class="mb-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
    This file or property may already exist ({{ dupes|length }} match{{ dupes|length|pluralize:"es" }} found).
    Extraction of the new upload has already started in the background.
  </div>
  <div class="bg-white border border-slate-200 rounded-lg overflow-x-auto mb-4">
    <table class="w-full text-sm border-collapse">
      <thead>
        <tr class="text-left border-b border-slate-300 text-xs text-slate-600">
          <th class="py-1.5 pl-3 pr-3">Property</th>
          <th class="py-1.5 pr-3">City</th>
          <th class="py-1.5 pr-3">ST</th>
          <th class="py-1.5 pr-3">Analyzed</th>
          <th class="py-1.5 pr-3">Match</th>
          <th class="py-1.5 pr-3"></th>
        </tr>
      </thead>
      <tbody>
        {% for d in dupes %}
        <tr class="border-b border-slate-100">
          <td class="py-1.5 pl-3 pr-3 font-medium">{{ d.property_name }}</td>
          <td class="py-1.5 pr-3">{{ d.city }}</td>
          <td class="py-1.5 pr-3">{{ d.state }}</td>
          <td class="py-1.5 pr-3 whitespace-nowrap">{{ d.analysis_date }}</td>
          <td class="py-1.5 pr-3">{{ d.match_type }}</td>
          <td class="py-1.5 pr-3">
            {% if d.deal_pk %}<a href="{% url 'deal-assumptions' d.deal_pk %}" class="text-accent-700 hover:underline">Open</a>{% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  <div class="flex gap-2 items-center">
    <a href="{% url 'deal-assumptions' deal.pk %}"
       class="bg-accent-700 text-white text-sm px-4 py-1.5 rounded">Keep &amp; continue as “{{ deal.deal_id }}”</a>
    <form method="post" action="{% url 'deal-discard' deal.pk %}">
      {% csrf_token %}
      <button type="submit" class="text-sm px-4 py-1.5 rounded border border-red-300 text-red-700 hover:bg-red-50">Discard this upload</button>
    </form>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 7: Go live in the sidebar** — in `webapp/templates/base.html`, replace the New Analysis muted span with (pattern from the file's own comment):

```html
          <a href="{% url 'analyze' %}"
             class="block px-2 py-1.5 md:py-0.5 rounded {% if request.resolver_match.url_name == 'analyze' %}bg-accent-700 text-white{% else %}hover:bg-slate-100{% endif %}">New Analysis</a>
```

- [ ] **Step 8: Run tests** — `.venv/bin/python -m pytest tests/test_web_analyze.py -v 2>&1 | tail -10` → all pass.

- [ ] **Step 9: Commit**

```bash
git add webapp/ tests/test_web_analyze.py
git commit -m "feat(web): upload flow — create deal + auto-extract, dupe confirm, guarded discard"
```

### Task 5: Assumptions editor — form definitions and GET rendering

**Files:**
- Create: `webapp/forms.py`, `webapp/templates/webapp/assumptions.html`, `webapp/templates/webapp/_unit_mix_row.html`
- Modify: `webapp/views.py` (`deal_assumptions` done-branch, `unit_mix_row` view), `webapp/urls.py`, `webapp/services.py` (`expense_benchmark_rows`), `webapp/templates/webapp/deal_list.html`
- Test: append to `tests/test_web_analyze.py`

**Interfaces:**
- Consumes: `Deal.cim_json/extraction_report/assumption_overrides`.
- Produces: `AssumptionsForm` (all fields `required=False`; percent fields hold WHOLE numbers in form data, decimals only in `build_overrides` output); `forms.build_initial(deal) -> dict`; `forms.unit_mix_rows(deal) -> list[dict]`; `forms.section_fields(form, pairs, missing_required)`; `forms.scenario_grid(form)`, `forms.va_grid(form)`, `forms.rc_grid(form)`; `forms.REQUIRED_FIELDS`; `services.expense_benchmark_rows(deal) -> list[dict]`; URL name `unit-mix-row` (`/deals/unit-mix-row/`). Task 6 consumes `forms.parse_unit_mix` and `forms.build_overrides` (defined here signature-wise, implemented in Task 6).

- [ ] **Step 1: Write the failing tests** (append):

```python
def _extracted_deal(client, deals_dir, fake_extract):
    from webapp.models import Deal
    client.post("/analyze/", {"cim": _pdf()})
    deal = Deal.objects.latest("pk")
    deal.refresh_from_db()
    assert deal.extract_status == "done"
    return deal


@pytest.mark.django_db
def test_assumptions_get_renders_snapshot(client, operator, deals_dir, fake_extract):
    deal = _extracted_deal(client, deals_dir, fake_extract)
    resp = client.get(f"/deals/{deal.pk}/assumptions/")
    assert resp.status_code == 200
    content = resp.content.decode()
    assert 'value="Expo Storage"' in content          # property name prefilled
    assert 'value="92' in content                     # physical_occupancy 0.92 → 92
    assert 'value="3500000' in content                # asking price
    assert "10x10" in content                         # unit mix row rendered
    assert 'name="um_label"' in content


@pytest.mark.django_db
def test_assumptions_get_flags_missing_required(client, operator, deals_dir, fake_extract):
    deal = _extracted_deal(client, deals_dir, fake_extract)
    # sample CIM lacks total_units? it has it — blank one required field instead
    deal.cim_json["ttm_egr"] = None
    deal.extraction_report["missing"] = ["ttm_egr", "msa"]
    deal.save()
    resp = client.get(f"/deals/{deal.pk}/assumptions/")
    content = resp.content.decode()
    assert "required-flag" in content  # marker class on the ttm_egr label


@pytest.mark.django_db
def test_unit_mix_row_endpoint(client, operator):
    resp = client.get("/deals/unit-mix-row/")
    assert resp.status_code == 200
    assert b'name="um_label"' in resp.content


@pytest.mark.django_db
def test_required_fields_parity_with_gui():
    from gui.components.assumptions_editor import REQUIRED_FIELDS as gui_required
    from webapp.forms import REQUIRED_FIELDS as web_required
    assert web_required == gui_required


@pytest.mark.django_db
def test_deal_list_links_extracted_deals(client, operator, deals_dir, fake_extract):
    deal = _extracted_deal(client, deals_dir, fake_extract)
    resp = client.get("/deals/")
    assert f"/deals/{deal.pk}/assumptions/".encode() in resp.content
```

- [ ] **Step 2: Run to verify failure** — placeholder response has none of it.

- [ ] **Step 3: Create `webapp/forms.py`**:

```python
"""Assumptions editor — field definitions, initial building, delta saving.

Percent convention: templates and form data hold WHOLE numbers (type 6
for 6%); snapshots, config defaults, and stored overrides hold decimals.
Conversion happens ONLY in build_initial (×100) and build_overrides
(÷100) — never in custom form fields, so bound redisplay round-trips
the raw submitted strings untouched.
"""
from django import forms

import config as cfg
from registry import ScenarioType

# Mirrors gui/components/assumptions_editor.REQUIRED_FIELDS — parity-
# tested in tests/test_web_analyze.py; consolidated when gui/ retires
# in Phase 5.
REQUIRED_FIELDS = {
    "asking_price", "nrsf", "total_units", "ttm_noi",
    "physical_occupancy", "state", "ttm_egr",
}

SCENARIO_KEYS = [s.value for s in ScenarioType]          # bear, base, bull
SCENARIO_PARAM_LABELS = [
    ("yr1_noi_bump", "Yr1 NOI Bump (%)"),
    ("stabilized_occ", "Stabilized Occ (%)"),
    ("rev_cagr_yr1_3", "Rev CAGR Yr 1–3 (%)"),
    ("rev_cagr_yr4_5", "Rev CAGR Yr 4–5 (%)"),
    ("exp_growth", "Expense Growth (%)"),
    ("exit_cap", "Exit Cap Rate (%)"),
]
SCENARIO_PARAMS = [p for p, _ in SCENARIO_PARAM_LABELS]
VA_PARAM_LABELS = [
    ("target_occupancy", "Target Occ (%)"),
    ("months_to_stabilize", "Months to Stabilize"),
    ("rent_growth_to_market", "Rent Growth to Mkt (%)"),
    ("post_stabilize_rev_growth", "Post-Stab Rev Growth (%)"),
    ("exit_cap", "Exit Cap Rate (%)"),
    ("expense_growth", "Expense Growth (%)"),
]
VA_PARAMS = [p for p, _ in VA_PARAM_LABELS]
VA_NON_PCT = {"months_to_stabilize"}

CIM_CHAR_FIELDS = ["property_name", "address", "city", "state", "msa"]
CIM_INT_FIELDS = ["year_built", "year_expanded", "total_units",
                  "population_1mi", "population_3mi", "population_5mi"]
CIM_FLOAT_FIELDS = ["acreage", "nrsf", "ss_driveup_sf", "ss_enclosed_sf",
                    "brv_enclosed_sf", "brv_covered_sf", "brv_open_sf",
                    "asking_price", "capex_estimate", "ttm_gpr", "other_income",
                    "ttm_egr", "ttm_total_revenue", "ttm_total_expenses",
                    "cim_yr1_noi", "ttm_noi", "median_hhi_3mi", "market_rent_psf"]
CIM_PCT_FIELDS = ["cc_pct", "physical_occupancy", "economic_occupancy", "mgmt_fee_pct"]
CIM_SCALAR_FIELDS = CIM_CHAR_FIELDS + CIM_INT_FIELDS + CIM_FLOAT_FIELDS + CIM_PCT_FIELDS

RC_PCT_KEYS = {"soft_cost_pct", "dev_profit_pct"}
RC_KEYS = [k for hard, site, _ in cfg.FACILITY_TYPES for k in (hard, site)] \
    + ["soft_cost_pct", "dev_profit_pct"]

# Section layouts consumed by section_fields() + the template.
SECTION_PROPERTY = [
    ("property_name", "Property Name"), ("address", "Address"),
    ("city", "City"), ("state", "State"), ("msa", "MSA"),
    ("year_built", "Year Built"), ("year_expanded", "Year Expanded"),
    ("acreage", "Acreage"),
]
SECTION_SIZE = [
    ("nrsf", "NRSF"), ("total_units", "Total Units"), ("cc_pct", "CC (%)"),
    ("physical_occupancy", "Physical Occupancy (%)"),
    ("economic_occupancy", "Economic Occupancy (%)"),
    ("ss_driveup_sf", "SS Drive-Up SF"), ("ss_enclosed_sf", "SS Enclosed SF"),
    ("brv_enclosed_sf", "BRV Enclosed SF"), ("brv_covered_sf", "BRV Covered SF"),
    ("brv_open_sf", "BRV Open Parking SF"),
]
SECTION_INCOME = [
    ("asking_price", "Asking Price ($)"), ("capex_estimate", "CapEx Estimate ($)"),
    ("ttm_gpr", "Gross Potential Rent ($)"), ("other_income", "Other Income ($)"),
    ("ttm_egr", "Effective Gross Revenue ($)"), ("ttm_total_revenue", "Total Revenue ($)"),
    ("ttm_total_expenses", "Total Expenses ($)"), ("cim_yr1_noi", "CIM Year 1 NOI ($)"),
    ("ttm_noi", "TTM NOI ($)"), ("mgmt_fee_pct", "Mgmt Fee (% EGR)"),
]
SECTION_DEMOGRAPHICS = [
    ("population_1mi", "Population 1-mi"), ("population_3mi", "Population 3-mi"),
    ("population_5mi", "Population 5-mi"), ("median_hhi_3mi", "Median HHI 3-mi ($)"),
    ("market_rent_psf", "Market Rent ($/SF/mo)"),
]

INPUT_CSS = "w-full border border-slate-300 rounded px-2 py-1 text-sm"


def _text():
    return forms.TextInput(attrs={"class": INPUT_CSS})


def _num():
    return forms.NumberInput(attrs={"class": INPUT_CSS, "step": "any", "min": "0"})


class AssumptionsForm(forms.Form):
    """Every field optional; blanks mean 'no override' (CIM fields) or
    'keep the config default' (scenario/RC/solver fields)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in CIM_CHAR_FIELDS:
            self.fields[name] = forms.CharField(
                required=False, max_length=2 if name == "state" else 200,
                widget=_text())
        for name in CIM_INT_FIELDS:
            self.fields[name] = forms.IntegerField(
                required=False, min_value=0, widget=_num())
        for name in CIM_FLOAT_FIELDS + CIM_PCT_FIELDS:
            self.fields[name] = forms.FloatField(
                required=False, min_value=0, widget=_num())
        for sc in SCENARIO_KEYS:
            for p in SCENARIO_PARAMS:
                self.fields[f"scen_{sc}_{p}"] = forms.FloatField(
                    required=False, widget=_num())
            for p in VA_PARAMS:
                self.fields[f"va_{sc}_{p}"] = forms.FloatField(
                    required=False, min_value=0, widget=_num())
        for key in RC_KEYS:
            for bound in ("low", "high"):
                self.fields[f"rc_{key}_{bound}"] = forms.FloatField(
                    required=False, min_value=0, widget=_num())
        self.fields["solver_target_irr"] = forms.FloatField(
            required=False, min_value=0, widget=_num())

    def clean_state(self):
        return (self.cleaned_data.get("state") or "").upper()


# ── Initial values (decimals → whole-number display) ────────────────

def _pct_display(v):
    return round(float(v) * 100, 4) if v is not None else None


def build_initial(deal) -> dict:
    snapshot = deal.cim_json or {}
    saved = deal.assumption_overrides or {}
    merged = {**snapshot, **saved.get("cim_overrides", {})}
    initial = {}
    for name in CIM_SCALAR_FIELDS:
        v = merged.get(name)
        initial[name] = _pct_display(v) if name in CIM_PCT_FIELDS else v
    scen_saved = saved.get("scenario_overrides", {})
    for sc in SCENARIO_KEYS:
        current = {**cfg.SCENARIO_DEFAULTS.get(sc, {}), **scen_saved.get(sc, {})}
        for p in SCENARIO_PARAMS:
            initial[f"scen_{sc}_{p}"] = _pct_display(current.get(p))
    va_saved = saved.get("va_scenario_overrides", {})
    for sc in SCENARIO_KEYS:
        current = {**cfg.VALUE_ADD_SCENARIOS.get(sc, {}), **va_saved.get(sc, {})}
        for p in VA_PARAMS:
            v = current.get(p)
            if p in VA_NON_PCT:
                initial[f"va_{sc}_{p}"] = float(v) if v is not None else None
            else:
                initial[f"va_{sc}_{p}"] = _pct_display(v)
    rc_saved = saved.get("replacement_cost_overrides", {})
    for key in RC_KEYS:
        low, high = rc_saved.get(key, cfg.REPLACEMENT_COST[key])
        if key in RC_PCT_KEYS:
            low, high = float(low) * 100, float(high) * 100
        initial[f"rc_{key}_low"] = round(float(low), 4)
        initial[f"rc_{key}_high"] = round(float(high), 4)
    initial["solver_target_irr"] = _pct_display(
        saved.get("solver_target_irr", cfg.SOLVER_TARGET_IRR))
    return initial


def _normalize_unit_mix(raw) -> list[dict]:
    """Snapshot/override unit-mix dicts → canonical editor rows
    (drops zero-count rows and UnitType's width/depth extras)."""
    rows = []
    for u in raw or []:
        count = int(u.get("count") or 0)
        if count <= 0:
            continue
        rows.append({
            "size_label": str(u.get("size_label") or ""),
            "count": count,
            "sf": float(u.get("sf") or 0),
            "rate": float(u.get("rate") or 0),
            "climate_controlled": bool(u.get("climate_controlled")),
        })
    return rows


def unit_mix_rows(deal) -> list[dict]:
    saved = (deal.assumption_overrides or {}).get("cim_overrides", {})
    if saved.get("unit_mix") is not None:
        return _normalize_unit_mix(saved["unit_mix"])
    return _normalize_unit_mix((deal.cim_json or {}).get("unit_mix"))


# ── Template helpers ────────────────────────────────────────────────

def section_fields(form, pairs, missing_required):
    return [{"bf": form[name], "label": label,
             "flag": name in missing_required} for name, label in pairs]


def scenario_grid(form):
    return [{"label": label,
             "cells": [form[f"scen_{sc}_{p}"] for sc in SCENARIO_KEYS]}
            for p, label in SCENARIO_PARAM_LABELS]


def va_grid(form):
    return [{"label": label,
             "cells": [form[f"va_{sc}_{p}"] for sc in SCENARIO_KEYS]}
            for p, label in VA_PARAM_LABELS]


def rc_grid(form):
    """One row per facility type: hard low/high + site low/high."""
    return [{"label": display,
             "cells": [form[f"rc_{hard}_low"], form[f"rc_{hard}_high"],
                       form[f"rc_{site}_low"], form[f"rc_{site}_high"]]}
            for hard, site, display in cfg.FACILITY_TYPES]
```

(`parse_unit_mix` and `build_overrides` are added in Task 6.)

- [ ] **Step 4: Add `expense_benchmark_rows` to `webapp/services.py`** (append):

```python
def expense_benchmark_rows(deal) -> list[dict]:
    """Read-only reference table: CIM $/SF vs state-adjusted benchmarks."""
    from config import EXPENSE_BENCHMARKS, get_regional_benchmarks
    from registry import EXPENSE_CATEGORIES

    snapshot = deal.cim_json or {}
    state = (snapshot.get("state") or deal.state or "").upper()
    nrsf = snapshot.get("nrsf") or 0
    benchmarks = get_regional_benchmarks(state) if state else EXPENSE_BENCHMARKS
    cim_exp = {}
    if nrsf:
        for line in snapshot.get("expense_lines") or []:
            if line.get("t12"):
                cim_exp[(line.get("label") or "").lower()] = line["t12"] / nrsf
    rows = []
    for cat in EXPENSE_CATEGORIES:
        low, high = benchmarks.get(cat.key, (0, 0))
        cim_val = next((val for kw in cat.parse_keywords
                        for label, val in cim_exp.items() if kw in label), None)
        rows.append({"category": cat.display_name, "cim": cim_val,
                     "low": low, "high": high})
    return rows
```

- [ ] **Step 5: Replace the `deal_assumptions` done-branch placeholder** in `webapp/views.py` (add `import config as cfg` and `from webapp import forms as assumptions_forms` to imports). POST handling arrives in Task 6 — this task renders GET only; a POST simply falls through to the same render:

```python
@login_required
def deal_assumptions(request, pk):
    deal = get_object_or_404(Deal, pk=pk)
    if deal.extract_status == "" and not deal.cim_json:
        return render(request, "webapp/assumptions_wait.html",
                      {"deal": deal, "unavailable": True})
    state = _extract_state(deal)
    if state != "done":
        return render(request, "webapp/assumptions_wait.html",
                      {"deal": deal, "failed": state == "failed"})
    report = deal.extraction_report or {}
    missing_required = set(report.get("missing", [])) & assumptions_forms.REQUIRED_FIELDS
    form = assumptions_forms.AssumptionsForm(
        initial=assumptions_forms.build_initial(deal))
    rows = assumptions_forms.unit_mix_rows(deal)
    f = assumptions_forms
    return render(request, "webapp/assumptions.html", {
        "deal": deal, "form": form, "report": report,
        "missing_fields": report.get("missing", []),
        "warnings": deal.extract_warnings,
        "unit_rows": rows,
        "benchmark_rows": services.expense_benchmark_rows(deal),
        "sec_property": f.section_fields(form, f.SECTION_PROPERTY, missing_required),
        "sec_size": f.section_fields(form, f.SECTION_SIZE, missing_required),
        "sec_income": f.section_fields(form, f.SECTION_INCOME, missing_required),
        "sec_demo": f.section_fields(form, f.SECTION_DEMOGRAPHICS, missing_required),
        "scenario_rows": f.scenario_grid(form),
        "va_rows": f.va_grid(form),
        "rc_rows": f.rc_grid(form),
        "rc_soft": [form["rc_soft_cost_pct_low"], form["rc_soft_cost_pct_high"],
                    form["rc_dev_profit_pct_low"], form["rc_dev_profit_pct_high"]],
        "solver_field": form["solver_target_irr"],
    })


@login_required
def unit_mix_row(request):
    return render(request, "webapp/_unit_mix_row.html", {"row": {}})
```

Add to `webapp/urls.py`:

```python
    path("deals/unit-mix-row/", views.unit_mix_row, name="unit-mix-row"),
```

- [ ] **Step 6: Create `webapp/templates/webapp/_unit_mix_row.html`**:

```html
<tr class="border-b border-slate-100">
  <td class="py-1 pr-2"><input type="text" name="um_label" value="{{ row.size_label|default:'' }}" aria-label="Unit size" class="w-24 border border-slate-300 rounded px-2 py-1 text-sm"></td>
  <td class="py-1 pr-2"><input type="number" name="um_count" value="{{ row.count|default:'' }}" min="0" step="1" aria-label="Count" class="w-20 border border-slate-300 rounded px-2 py-1 text-sm"></td>
  <td class="py-1 pr-2"><input type="number" name="um_sf" value="{{ row.sf|default:'' }}" min="0" step="any" aria-label="Square feet" class="w-24 border border-slate-300 rounded px-2 py-1 text-sm"></td>
  <td class="py-1 pr-2"><input type="number" name="um_rate" value="{{ row.rate|default:'' }}" min="0" step="any" aria-label="Monthly rent" class="w-24 border border-slate-300 rounded px-2 py-1 text-sm"></td>
  <td class="py-1 pr-2">
    <select name="um_cc" aria-label="Climate controlled" class="border border-slate-300 rounded px-2 py-1 text-sm">
      <option value="0" {% if not row.climate_controlled %}selected{% endif %}>No</option>
      <option value="1" {% if row.climate_controlled %}selected{% endif %}>Yes</option>
    </select>
  </td>
  <td class="py-1"><button type="button" onclick="this.closest('tr').remove()" aria-label="Remove row" class="text-slate-400 hover:text-red-600 text-sm px-1">&#10005;</button></td>
</tr>
```

- [ ] **Step 7: Create `webapp/templates/webapp/assumptions.html`**:

```html
{% extends "base.html" %}
{% block title %}{{ deal.property_name }} — Assumptions{% endblock %}
{% block content %}
<div class="max-w-5xl">
  <div class="flex flex-wrap items-baseline gap-x-4 gap-y-1 mb-1">
    <h1 class="font-display text-xl font-semibold">{{ deal.property_name }}</h1>
    <span class="text-sm text-slate-500">{{ deal.city }}{% if deal.city and deal.state %}, {% endif %}{{ deal.state }}</span>
  </div>

  <div class="flex flex-wrap gap-2 mb-2 text-xs">
    <span class="rounded-full bg-slate-100 px-2.5 py-1 text-slate-700">Confidence {{ report.confidence_pct }}%</span>
    <span class="rounded-full bg-slate-100 px-2.5 py-1 text-slate-700">Fields {{ report.populated }}/{{ report.total_fields }}</span>
    <span class="rounded-full bg-slate-100 px-2.5 py-1 text-slate-700">Missing {{ missing_fields|length }}</span>
  </div>

  {% if warnings %}
  <div class="mb-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-800">
    {% for w in warnings %}<div>{{ w }}</div>{% endfor %}
  </div>
  {% endif %}

  {% if missing_fields %}
  <details class="mb-3 text-xs text-slate-600">
    <summary class="cursor-pointer select-none">Missing fields ({{ missing_fields|length }})</summary>
    <p class="mt-1">{{ missing_fields|join:", " }}</p>
  </details>
  {% endif %}

  <form method="post" class="space-y-3">
    {% csrf_token %}
    {% if form.errors %}
    <div class="rounded-md border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-800">
      Some fields could not be saved — check the highlighted values.
      {{ form.errors }}
    </div>
    {% endif %}

    <details open class="bg-white border border-slate-200 rounded-lg">
      <summary class="cursor-pointer select-none px-4 py-2 text-sm font-semibold">Property</summary>
      <div class="px-4 pb-3 grid gap-x-4 gap-y-2 sm:grid-cols-3">
        {% for it in sec_property %}
        <label class="block text-xs text-slate-600">
          {% if it.flag %}<span class="required-flag text-red-600 font-bold" title="Required for IRR modeling">!</span> {% endif %}{{ it.label }}
          {{ it.bf }}
        </label>
        {% endfor %}
      </div>
    </details>

    <details open class="bg-white border border-slate-200 rounded-lg">
      <summary class="cursor-pointer select-none px-4 py-2 text-sm font-semibold">Size &amp; Occupancy</summary>
      <div class="px-4 pb-3 grid gap-x-4 gap-y-2 sm:grid-cols-3">
        {% for it in sec_size %}
        <label class="block text-xs text-slate-600">
          {% if it.flag %}<span class="required-flag text-red-600 font-bold" title="Required for IRR modeling">!</span> {% endif %}{{ it.label }}
          {{ it.bf }}
        </label>
        {% endfor %}
      </div>
      <div class="px-4 pb-3">
        <div class="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">Replacement Cost Benchmarks ($/SF)</div>
        <div class="overflow-x-auto">
          <table class="text-sm border-collapse">
            <thead>
              <tr class="text-left text-xs text-slate-600 border-b border-slate-200">
                <th class="py-1 pr-3">Facility Type</th>
                <th class="py-1 pr-2">Hard Low</th><th class="py-1 pr-2">Hard High</th>
                <th class="py-1 pr-2">Site Low</th><th class="py-1 pr-2">Site High</th>
              </tr>
            </thead>
            <tbody>
              {% for r in rc_rows %}
              <tr class="border-b border-slate-100">
                <td class="py-1 pr-3 whitespace-nowrap text-xs text-slate-600">{{ r.label }}</td>
                {% for cell in r.cells %}<td class="py-1 pr-2 w-24">{{ cell }}</td>{% endfor %}
              </tr>
              {% endfor %}
              <tr>
                <td class="py-1 pr-3 whitespace-nowrap text-xs text-slate-600">Soft Cost % / Dev Profit %</td>
                {% for cell in rc_soft %}<td class="py-1 pr-2 w-24">{{ cell }}</td>{% endfor %}
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </details>

    <details class="bg-white border border-slate-200 rounded-lg">
      <summary class="cursor-pointer select-none px-4 py-2 text-sm font-semibold">Unit Mix</summary>
      <div class="px-4 pb-3">
        <table class="text-sm border-collapse">
          <thead>
            <tr class="text-left text-xs text-slate-600 border-b border-slate-200">
              <th class="py-1 pr-2">Unit Size</th><th class="py-1 pr-2">Count</th>
              <th class="py-1 pr-2">Sq Ft</th><th class="py-1 pr-2">Rent/Mo ($)</th>
              <th class="py-1 pr-2">Climate Ctrl</th><th class="py-1"></th>
            </tr>
          </thead>
          <tbody id="um-body">
            {% for row in unit_rows %}{% include "webapp/_unit_mix_row.html" %}{% endfor %}
          </tbody>
        </table>
        <button type="button" hx-get="{% url 'unit-mix-row' %}" hx-target="#um-body" hx-swap="beforeend"
                class="mt-2 text-sm text-accent-700 hover:underline">+ Add row</button>
        <p class="mt-1 text-xs text-slate-500">Rows with a zero count are dropped on save.</p>
      </div>
    </details>

    <details class="bg-white border border-slate-200 rounded-lg">
      <summary class="cursor-pointer select-none px-4 py-2 text-sm font-semibold">Income &amp; Expenses</summary>
      <div class="px-4 pb-3 grid gap-4 lg:grid-cols-2">
        <div class="grid gap-x-4 gap-y-2 sm:grid-cols-2 content-start">
          {% for it in sec_income %}
          <label class="block text-xs text-slate-600">
            {% if it.flag %}<span class="required-flag text-red-600 font-bold" title="Required for IRR modeling">!</span> {% endif %}{{ it.label }}
            {{ it.bf }}
          </label>
          {% endfor %}
        </div>
        <div>
          <div class="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">Expense Benchmarks (state-adjusted, $/NRSF/yr)</div>
          <table class="w-full text-sm border-collapse">
            <thead>
              <tr class="text-left text-xs text-slate-600 border-b border-slate-200">
                <th class="py-1 pr-2">Category</th><th class="py-1 pr-2 text-right">CIM $/SF</th>
                <th class="py-1 pr-2 text-right">Low</th><th class="py-1 text-right">High</th>
              </tr>
            </thead>
            <tbody>
              {% for r in benchmark_rows %}
              <tr class="border-b border-slate-100">
                <td class="py-1 pr-2">{{ r.category }}</td>
                <td class="py-1 pr-2 text-right">{% if r.cim %}${{ r.cim|floatformat:2 }}{% else %}—{% endif %}</td>
                <td class="py-1 pr-2 text-right">${{ r.low|floatformat:2 }}</td>
                <td class="py-1 text-right">${{ r.high|floatformat:2 }}</td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
    </details>

    <details class="bg-white border border-slate-200 rounded-lg">
      <summary class="cursor-pointer select-none px-4 py-2 text-sm font-semibold">Scenarios</summary>
      <div class="px-4 pb-3 space-y-4">
        <div class="overflow-x-auto">
          <table class="text-sm border-collapse">
            <thead>
              <tr class="text-left text-xs text-slate-600 border-b border-slate-200">
                <th class="py-1 pr-3">Bear / Base / Bull</th>
                <th class="py-1 pr-2">Bear</th><th class="py-1 pr-2">Base</th><th class="py-1 pr-2">Bull</th>
              </tr>
            </thead>
            <tbody>
              {% for r in scenario_rows %}
              <tr class="border-b border-slate-100">
                <td class="py-1 pr-3 whitespace-nowrap text-xs text-slate-600">{{ r.label }}</td>
                {% for cell in r.cells %}<td class="py-1 pr-2 w-28">{{ cell }}</td>{% endfor %}
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
        <details>
          <summary class="cursor-pointer select-none text-sm font-medium text-slate-700">Value-Add Assumptions</summary>
          <div class="overflow-x-auto mt-2">
            <table class="text-sm border-collapse">
              <thead>
                <tr class="text-left text-xs text-slate-600 border-b border-slate-200">
                  <th class="py-1 pr-3">VA Parameter</th>
                  <th class="py-1 pr-2">Bear</th><th class="py-1 pr-2">Base</th><th class="py-1 pr-2">Bull</th>
                </tr>
              </thead>
              <tbody>
                {% for r in va_rows %}
                <tr class="border-b border-slate-100">
                  <td class="py-1 pr-3 whitespace-nowrap text-xs text-slate-600">{{ r.label }}</td>
                  {% for cell in r.cells %}<td class="py-1 pr-2 w-28">{{ cell }}</td>{% endfor %}
                </tr>
                {% endfor %}
              </tbody>
            </table>
          </div>
        </details>
        <label class="block text-xs text-slate-600 max-w-[12rem]">Solver Target IRR (%)
          {{ solver_field }}
        </label>
      </div>
    </details>

    <details class="bg-white border border-slate-200 rounded-lg">
      <summary class="cursor-pointer select-none px-4 py-2 text-sm font-semibold">Demographics</summary>
      <div class="px-4 pb-3 grid gap-x-4 gap-y-2 sm:grid-cols-3">
        {% for it in sec_demo %}
        <label class="block text-xs text-slate-600">
          {% if it.flag %}<span class="required-flag text-red-600 font-bold" title="Required for IRR modeling">!</span> {% endif %}{{ it.label }}
          {{ it.bf }}
        </label>
        {% endfor %}
      </div>
    </details>

    <div class="flex items-center gap-3">
      <button type="submit" class="bg-accent-700 text-white text-sm px-4 py-1.5 rounded">Save Assumptions</button>
      <span class="text-xs text-slate-500">Run Analysis lands in Phase 4 — saved assumptions will feed it.</span>
    </div>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 8: Link extracted deals from the pipeline** — in `webapp/templates/webapp/deal_list.html`, replace the Property cell:

```html
            <td class="py-1.5 pl-3 pr-3 font-medium">
              {% if d.cim_json %}<a href="{% url 'deal-assumptions' d.pk %}" class="text-accent-700 hover:underline">{{ d.property_name }}</a>
              {% else %}{{ d.property_name }}{% endif %}
            </td>
```

- [ ] **Step 9: Run tests** — `.venv/bin/python -m pytest tests/test_web_analyze.py -v 2>&1 | tail -10` → all pass.

- [ ] **Step 10: Commit**

```bash
git add webapp/ tests/test_web_analyze.py
git commit -m "feat(web): assumptions editor — collapsible sections, unit-mix rows, benchmark tables"
```

### Task 6: Assumptions editor — POST, deltas, save

**Files:**
- Modify: `webapp/forms.py`, `webapp/views.py`
- Test: append to `tests/test_web_analyze.py`

**Interfaces:**
- Consumes: Task 5 form + helpers.
- Produces: `forms.parse_unit_mix(post) -> list[dict] | None`; `forms.build_overrides(cleaned, post, deal) -> dict` (keys only when non-default: `cim_overrides`, `scenario_overrides`, `va_scenario_overrides`, `replacement_cost_overrides` — per-key `[low, high]` lists — and `solver_target_irr`). Phase 4 consumes `Deal.assumption_overrides` in exactly this shape (note: JSON turns RC tuples into lists; `low, high = rc[key]` unpacking still works).

- [ ] **Step 1: Write the failing tests** (append):

```python
def _post_assumptions(client, deal, extra=None):
    """POST the form as rendered (initial values), with optional edits."""
    from webapp import forms as f
    initial = f.build_initial(deal)
    data = {k: ("" if v is None else v) for k, v in initial.items()}
    rows = f.unit_mix_rows(deal)
    data["um_label"] = [r["size_label"] for r in rows]
    data["um_count"] = [str(r["count"]) for r in rows]
    data["um_sf"] = [str(r["sf"]) for r in rows]
    data["um_rate"] = [str(r["rate"]) for r in rows]
    data["um_cc"] = ["1" if r["climate_controlled"] else "0" for r in rows]
    data.update(extra or {})
    return client.post(f"/deals/{deal.pk}/assumptions/", data)


@pytest.mark.django_db
def test_save_unchanged_form_stores_no_overrides(client, operator, deals_dir, fake_extract):
    deal = _extracted_deal(client, deals_dir, fake_extract)
    resp = _post_assumptions(client, deal)
    assert resp.status_code == 302
    deal.refresh_from_db()
    assert deal.assumption_overrides == {}


@pytest.mark.django_db
def test_save_cim_delta_and_pct_conversion(client, operator, deals_dir, fake_extract):
    deal = _extracted_deal(client, deals_dir, fake_extract)
    _post_assumptions(client, deal, {"asking_price": "3200000",
                                     "physical_occupancy": "85"})
    deal.refresh_from_db()
    cim_o = deal.assumption_overrides["cim_overrides"]
    assert cim_o == {"asking_price": 3200000.0, "physical_occupancy": 0.85}


@pytest.mark.django_db
def test_save_scenario_delta_stores_full_section(client, operator, deals_dir, fake_extract):
    import config as cfg
    deal = _extracted_deal(client, deals_dir, fake_extract)
    _post_assumptions(client, deal, {"scen_bear_exit_cap": "9"})
    deal.refresh_from_db()
    scen = deal.assumption_overrides["scenario_overrides"]
    assert scen["bear"]["exit_cap"] == 0.09
    # untouched values persisted alongside (auditability)
    assert scen["base"]["exit_cap"] == cfg.SCENARIO_DEFAULTS["base"]["exit_cap"]
    assert "va_scenario_overrides" not in deal.assumption_overrides


@pytest.mark.django_db
def test_save_unit_mix_edit(client, operator, deals_dir, fake_extract):
    deal = _extracted_deal(client, deals_dir, fake_extract)
    _post_assumptions(client, deal, {
        "um_label": ["10x10", "10x20", ""],
        "um_count": ["120", "50", "0"],          # changed 100 → 120; blank row dropped
        "um_sf": ["100", "200", ""],
        "um_rate": ["95", "165", ""],
        "um_cc": ["0", "1", "0"],
    })
    deal.refresh_from_db()
    mix = deal.assumption_overrides["cim_overrides"]["unit_mix"]
    assert len(mix) == 2
    assert mix[0]["count"] == 120
    assert mix[1]["climate_controlled"] is True


@pytest.mark.django_db
def test_save_rc_and_solver_deltas(client, operator, deals_dir, fake_extract):
    deal = _extracted_deal(client, deals_dir, fake_extract)
    _post_assumptions(client, deal, {"rc_ss_driveup_per_sf_low": "60",
                                     "solver_target_irr": "12"})
    deal.refresh_from_db()
    o = deal.assumption_overrides
    assert o["replacement_cost_overrides"] == {"ss_driveup_per_sf": [60.0, 85.0]}
    assert o["solver_target_irr"] == 0.12


@pytest.mark.django_db
def test_saved_values_render_on_next_get(client, operator, deals_dir, fake_extract):
    deal = _extracted_deal(client, deals_dir, fake_extract)
    _post_assumptions(client, deal, {"physical_occupancy": "85"})
    resp = client.get(f"/deals/{deal.pk}/assumptions/")
    assert b'value="85' in resp.content
```

- [ ] **Step 2: Run to verify failure** — POSTs return 200 (GET render), no save.

- [ ] **Step 3: Add parsing + delta builders to `webapp/forms.py`** (append):

```python
# ── POST parsing + delta computation ────────────────────────────────

def parse_unit_mix(post) -> list[dict] | None:
    """Parallel getlist arrays → canonical rows; count ≤ 0 rows dropped.
    Returns None when the form carried no unit-mix inputs at all."""
    labels = post.getlist("um_label")
    if not labels:
        return None
    counts = post.getlist("um_count")
    sfs = post.getlist("um_sf")
    rates = post.getlist("um_rate")
    ccs = post.getlist("um_cc")
    rows = []
    for label, count, sf, rate, cc in zip(labels, counts, sfs, rates, ccs):
        try:
            count = int(float(count or 0))
            sf = float(sf or 0)
            rate = float(rate or 0)
        except ValueError:
            continue
        if count <= 0:
            continue
        rows.append({"size_label": label.strip(), "count": count, "sf": sf,
                     "rate": rate, "climate_controlled": cc == "1"})
    return rows


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return round(float(a), 6) == round(float(b), 6)
    return a == b


def _rounded_sections(defaults, params, non_pct=()) -> dict:
    return {sc: {p: round(float(defaults[sc][p]), 6) for p in params}
            for sc in SCENARIO_KEYS}


def _submitted_sections(cleaned, prefix, params, defaults, non_pct=()) -> dict:
    """Grid values → decimal dicts; blanks fall back to config defaults."""
    out = {}
    for sc in SCENARIO_KEYS:
        d = defaults.get(sc, {})
        s = {}
        for p in params:
            v = cleaned.get(f"{prefix}_{sc}_{p}")
            if v is None:
                s[p] = round(float(d.get(p, 0)), 6)
            elif p in non_pct:
                s[p] = round(float(v), 6)
            else:
                s[p] = round(float(v) / 100.0, 6)
        out[sc] = s
    return out


def build_overrides(cleaned, post, deal) -> dict:
    """Deltas only — see the plan's Design Decisions. Sections equal to
    their defaults are omitted entirely."""
    snapshot = deal.cim_json or {}
    out = {}

    cim_o = {}
    for name in CIM_SCALAR_FIELDS:
        v = cleaned.get(name)
        if v in (None, ""):
            continue
        if name in CIM_PCT_FIELDS:
            v = round(v / 100.0, 6)
        snap = snapshot.get(name)
        if snap is None or not _same(v, snap):
            cim_o[name] = v
    mix = parse_unit_mix(post)
    if mix is not None and mix != _normalize_unit_mix(snapshot.get("unit_mix")):
        cim_o["unit_mix"] = mix
    if cim_o:
        out["cim_overrides"] = cim_o

    scen = _submitted_sections(cleaned, "scen", SCENARIO_PARAMS, cfg.SCENARIO_DEFAULTS)
    if scen != _rounded_sections(cfg.SCENARIO_DEFAULTS, SCENARIO_PARAMS):
        out["scenario_overrides"] = scen
    va = _submitted_sections(cleaned, "va", VA_PARAMS, cfg.VALUE_ADD_SCENARIOS,
                             non_pct=VA_NON_PCT)
    va_defaults = {sc: {p: round(float(cfg.VALUE_ADD_SCENARIOS[sc][p]), 6)
                        for p in VA_PARAMS} for sc in SCENARIO_KEYS}
    if va != va_defaults:
        out["va_scenario_overrides"] = va

    rc = {}
    for key in RC_KEYS:
        low = cleaned.get(f"rc_{key}_low")
        high = cleaned.get(f"rc_{key}_high")
        if low is None or high is None:
            continue
        if key in RC_PCT_KEYS:
            low, high = low / 100.0, high / 100.0
        cur = [round(float(low), 6), round(float(high), 6)]
        d_low, d_high = cfg.REPLACEMENT_COST[key]
        if cur != [round(float(d_low), 6), round(float(d_high), 6)]:
            rc[key] = cur
    if rc:
        out["replacement_cost_overrides"] = rc

    tgt = cleaned.get("solver_target_irr")
    if tgt is not None:
        tgt = round(tgt / 100.0, 6)
        if not _same(tgt, cfg.SOLVER_TARGET_IRR):
            out["solver_target_irr"] = tgt

    return out
```

Note the `_submitted_sections` non-pct nuance: `_rounded_sections` ignores `non_pct` because it only rounds (no ÷100), which is identical for both cases — the two dicts compare correctly.

- [ ] **Step 4: Wire POST into `deal_assumptions`** — replace the form-construction lines with:

```python
    if request.method == "POST":
        form = assumptions_forms.AssumptionsForm(request.POST)
        if form.is_valid():
            deal.assumption_overrides = assumptions_forms.build_overrides(
                form.cleaned_data, request.POST, deal)
            deal.save(update_fields=["assumption_overrides", "updated_at"])
            messages.success(request, "Assumptions saved.")
            return redirect("deal-assumptions", pk=deal.pk)
        rows = assumptions_forms.parse_unit_mix(request.POST) or []
        status = 422
    else:
        form = assumptions_forms.AssumptionsForm(
            initial=assumptions_forms.build_initial(deal))
        rows = assumptions_forms.unit_mix_rows(deal)
        status = 200
```

and pass `status=status` to the final `render(...)` call.

- [ ] **Step 5: Run tests** — `.venv/bin/python -m pytest tests/test_web_analyze.py -v 2>&1 | tail -12` → all pass.

- [ ] **Step 6: Full suite** — `.venv/bin/python -m pytest tests/ -q 2>&1 | tail -2` → all pass (~94).

- [ ] **Step 7: Commit**

```bash
git add webapp/forms.py webapp/views.py tests/test_web_analyze.py
git commit -m "feat(web): assumptions save — delta-only overrides, unit-mix parsing, pct conversion"
```

### Task 7: Tailwind rebuild, UI passes, gates

- [ ] **Step 1: Rebuild Tailwind** (new classes across 6 templates):

Run: `TAILWINDCSS_VERSION=v3.4.17 .venv/bin/tailwindcss -c tailwind.config.js -i static/src/input.css -o static/css/tw.css --minify && ls -la static/css/tw.css`

- [ ] **Step 2: Django checks** — `.venv/bin/python manage.py check && .venv/bin/python manage.py makemigrations --check --dry-run`

- [ ] **Step 3: UI passes — NEW pages, so TWO independent agents.** Agent 1 (layout/compaction): paste the four page templates + partials, check grid alignment, consistent spacing/label patterns vs `deal_list.html`, no wasted vertical space. Agent 2 (adversarial density, fresh context): hunt for label/field visibility sacrifices, sub-16px tap targets on mobile-relevant controls, tables that force page-level horizontal scroll (must scroll inside their own wrapper), illegible contrast. Fix findings; rebuild `tw.css` if classes changed.

- [ ] **Step 4: Full suite again** after fixes; commit:

```bash
git add webapp/templates/ static/css/tw.css
git commit -m "style(web): analyze/assumptions UI passes — layout/compaction + density"
```

### Task 8: Phase 3 PR (standard tier)

- [ ] **Step 1:** Standard-tier cycle per operator rules: diff → ONE review pass → repair → re-review only if critical/moderate findings → push `claude/p3-upload-extract-assumptions` → PR → CI green → squash-merge → delete branch.
- [ ] **Step 2:** Post-merge smoke on main: `python manage.py migrate` locally, upload a real CIM from `CIMs/` via runserver, watch extract complete, save an assumption edit, confirm `assumption_overrides` row contents.

---

## Self-Review (performed at write time)

1. **Spec coverage:** upload → Task 4; dupe check → Task 4 (create-then-confirm + discard); background extract with thread + DB status + HTMX poll → Tasks 2–3; extraction report → assumptions header (Task 5); assumptions editor with collapsible sections, per-deal `assumption_overrides` JSONField, unit-mix row editor → Tasks 1, 5, 6.
2. **Placeholder scan:** the single intentional placeholder is Task 3's `deal_assumptions` done-branch (`HttpResponse("Assumptions editor lands in Task 5.")`), explicitly replaced in Task 5 Step 5 — deliberate sequencing, not a gap.
3. **Type consistency:** `extract_status` values (`""/pending/running/done/failed`) consistent across model, services, views, tests; percent convention (whole in form data, decimal in storage) enforced in exactly two functions; `um_*` field names identical in `_unit_mix_row.html`, `parse_unit_mix`, and tests; URL names `analyze`/`deal-discard`/`deal-assumptions`/`extract-status`/`extract-retry`/`unit-mix-row` consistent across urls.py, templates, and tests; `find_upload_duplicates` runs before `create_deal_from_upload` (self-match hazard documented in both places).
