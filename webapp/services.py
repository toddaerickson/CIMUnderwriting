"""Deal-folder, extraction, and duplicate-check services for the web UI.

Owns the deal-folder/meta helpers outright (absorbed from the retired
gui/deal_manager per the Phase 5 retirement map).
"""
import copy
import dataclasses
import datetime
import json
import logging
import math
import numbers
import os
import re
import threading
from contextlib import contextmanager
from datetime import date
from enum import Enum

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

import config as cfg
from engine import (AnalysisResult, _apply_overrides, extract_pdf_data,
                    run_analysis)
from webapp.models import Deal

logger = logging.getLogger("cim_analyst.web")


# ── Deal folder / meta helpers (absorbed from gui/deal_manager) ──────

def sanitize_name(name: str) -> str:
    """Convert a property name to a filesystem-safe folder name."""
    # Replace non-alphanumeric chars (except spaces) with nothing
    clean = re.sub(r"[^\w\s-]", "", name)
    # Replace whitespace runs with underscore
    clean = re.sub(r"\s+", "_", clean.strip())
    return clean or "Unknown_Property"


def write_deal_meta(deal_folder: str, meta: dict):
    """Write deal_meta.json to a deal folder."""
    meta_path = os.path.join(deal_folder, "deal_meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2, default=str)


def read_deal_meta(deal_folder: str) -> dict | None:
    """Read deal_meta.json from a deal folder. Returns None if missing."""
    meta_path = os.path.join(deal_folder, "deal_meta.json")
    if not os.path.isfile(meta_path):
        return None
    with open(meta_path, "r") as f:
        return json.load(f)


# The exact strings detect_asset_type can return — single source for the
# settings editor's scope dropdown (guarded by a no-drift test).
ASSET_TYPES = (
    "Self Storage",
    "Climate-Controlled Self Storage",
    "Boat & RV Storage",
)


def detect_asset_type(cim_data) -> str:
    """Determine asset type from CIM data fields."""
    brv_sf = sum(filter(None, [
        getattr(cim_data, "brv_enclosed_sf", None),
        getattr(cim_data, "brv_covered_sf", None),
        getattr(cim_data, "brv_open_sf", None),
    ]))
    if brv_sf > 0:
        return "Boat & RV Storage"

    cc_pct = getattr(cim_data, "cc_pct", None)
    if cc_pct is not None and cc_pct > 0.5:
        return "Climate-Controlled Self Storage"

    return "Self Storage"


def build_deal_meta(cim_data, result, deal_folder: str, input_files: list[str] = None) -> dict:
    """Assemble deal_meta.json content from analysis results.

    Args:
        cim_data: parsed CIM data
        result: AnalysisResult from engine
        deal_folder: path to deal folder
        input_files: list of uploaded filenames
    """
    # Estimated fair value: prefer VA max offer, fall back to static
    fair_value = None
    if result.va_max_offer and result.va_max_offer.get("max_price"):
        fair_value = result.va_max_offer["max_price"]
    elif result.max_offer and result.max_offer.get("max_price"):
        fair_value = result.max_offer["max_price"]

    recommendation = result.gate_summary.get("recommendation", "N/A") if result.gate_summary else "N/A"

    return {
        "deal_id": sanitize_name(cim_data.property_name or "Unknown").lower(),
        "property_name": cim_data.property_name or "Unknown",
        "city": cim_data.city or "",
        "state": cim_data.state or "",
        "asset_type": detect_asset_type(cim_data),
        "nrsf": cim_data.nrsf,
        "acreage": getattr(cim_data, "acreage", None),
        "asking_price": cim_data.asking_price,
        "estimated_fair_value": round(fair_value) if fair_value else None,
        "recommendation": recommendation,
        "analysis_date": date.today().isoformat(),
        "memo_path": os.path.basename(result.memo_path) if result.memo_path else "",
        "excel_path": os.path.basename(result.excel_path) if result.excel_path else "",
        "input_files": input_files or [],
    }


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


# ── Analysis payload sanitizer ───────────────────────────────────────

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


def build_preview_cim(deal, cleaned, post):
    """Merged cim_data + overrides for PREVIEW ONLY — never persisted.

    Mirrors the save path's cim_overrides application (deal_assumptions /
    _analysis_worker) but stops short of writing anything: no Deal.save,
    no AnalysisRun row.
    """
    from webapp.forms import build_overrides

    cim = cim_from_dict(deal.cim_json or {})
    ov = build_overrides(cleaned, post, deal)
    _apply_overrides(cim, dict(ov.get("cim_overrides", {})))
    return cim, ov


def model_strip_context(deal, cim, fin, form) -> dict:
    """Context for the dense-model-view preview partial: SF/capita,
    the Revenue−Expenses=NOI chip, and the expense-line OOB targets.

    The NOI chip is recomputed from the merged `cim` values (not from
    form.errors) — the preview must show a state even when the form's
    clean() rejected the submission, since a rejected form still leaves
    cleaned_data populated for the individually-valid fields.
    """
    from analysis.filters import sf_per_capita
    from webapp.forms import noi_recon_tolerance

    spc, spc_problem = sf_per_capita(cim)
    rev, exp, noi = cim.ttm_total_revenue, cim.ttm_total_expenses, cim.ttm_noi
    if None not in (rev, exp, noi):
        delta = round(rev - exp - noi, 2)
        noi_state = ("ok" if abs(delta) <= noi_recon_tolerance(rev)
                     else f"off by ${abs(delta):,.0f}")
    else:
        noi_state = "—"
    return {
        "deal": deal,
        "population_3mi": cim.population_3mi,
        "median_hhi_3mi": cim.median_hhi_3mi,
        "sf_per_capita": spc, "sf_per_capita_problem": spc_problem,
        "sf_per_capita_limit": cfg.GATES["max_sf_per_capita"],
        "noi_state": noi_state,
        "expense_lines": (fin.get("expense_analysis") or {}).get("lines", []),
    }


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
        # A per-deal solver target supersedes the global row entirely;
        # stamping the global value would record a threshold the engine
        # never used. The winner is already recorded under "assumptions".
        if overrides.get("solver_target_irr"):
            applied.pop("SOLVER_TARGET_IRR", None)
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
                    enrich=True,
                    expense_line_overrides=overrides.get(
                        "expense_line_overrides"),
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
            # Provenance: which tier supplied each demographic field
            # (CIM/override vs Census vs default) + why enrichment
            # skipped, so a blank population is explainable from the run
            # record instead of a daemon-thread log.
            "enrichment": {
                "fields_enriched": result.enrichment.fields_enriched,
                "geocode_success": result.enrichment.geocode_success,
                "census_success": result.enrichment.census_success,
                "errors": result.enrichment.errors,
                "source_log": result.enrichment.source_log,
            } if result.enrichment else None,
        })
        # Both writes below must land together: if anything past the
        # AnalysisRun "done" flip raises (e.g. building deal_updates),
        # the outer except flips status to "failed" but can't un-write
        # a result_json/finished_at/filenames that already committed —
        # a "failed" row carrying done-looking data. atomic() makes the
        # flip and the Deal refresh all-or-nothing so a failure here
        # rolls back the "done" write too, leaving a clean "failed" row.
        with transaction.atomic():
            AnalysisRun.objects.filter(pk=run_pk).update(
                status="done", finished_at=timezone.now(), result_json=payload,
                error="",
                memo_filename=os.path.basename(result.memo_path or "")[:300],
                excel_filename=os.path.basename(result.excel_path or "")[:300],
                template_filename=os.path.basename(result.template_path or "")[:300],
            )

            deal_updates = {
                "recommendation": (meta.get("recommendation") or "N/A")[:40],
                "estimated_fair_value": meta.get("estimated_fair_value"),
                "analysis_date": datetime.date.fromisoformat(meta["analysis_date"]),
                "memo_filename": os.path.basename(result.memo_path or "")[:300],
                "excel_filename": os.path.basename(result.excel_path or "")[:300],
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
