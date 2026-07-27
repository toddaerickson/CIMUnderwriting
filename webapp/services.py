"""Deal-folder, extraction, and duplicate-check services for the web UI.

Absorbs gui/deal_manager responsibilities per the Phase 5 retirement map;
imports the shared helpers from there (stdlib-only module) rather than
copying them, so there is one source of truth until gui/ retires.
"""
import copy
import dataclasses
import datetime
import logging
import math
import numbers
import os
import threading
from contextlib import contextmanager
from enum import Enum

from django.conf import settings
from django.db import transaction
from django.utils import timezone

import config as cfg
from gui.deal_manager import (build_deal_meta, detect_asset_type,
                              sanitize_name, write_deal_meta)
from gui.engine import (AnalysisResult, _apply_overrides, extract_pdf_data,
                        run_analysis)
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
