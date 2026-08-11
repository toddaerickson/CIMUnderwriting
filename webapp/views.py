import logging
import os
import shutil

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import (FileResponse, Http404, HttpResponse, JsonResponse,
                         QueryDict)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_htmx.http import HttpResponseClientRedirect

import config as cfg
from webapp import forms as assumptions_forms
from webapp import results as results_ctx
from webapp import services
from webapp.models import AnalysisRun, Deal

logger = logging.getLogger("cim_analyst.web")


def health(request):
    """Public health + version endpoint (same contract as managertools).

    Reports git SHA so /verify-deploy gets a definitive match answer,
    and proves the DB is reachable — a booted-but-dead process must
    not report healthy.
    """
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        db_ok = False
        logger.exception("health check: database unreachable")
    sha = (
        os.environ.get("RENDER_GIT_COMMIT")
        or os.environ.get("RAILWAY_GIT_COMMIT_SHA")
        or "unknown"
    )
    # Disk probe: only when the deploy declares file locations via env
    # (Render). Dev/CI leave these unset and skip it. Without this, an
    # unmounted disk is invisible — CompDatabase() fabricates an empty
    # comps DB and uploads land on the ephemeral container FS.
    # Probes the MOUNT (parent of each configured path), not the data
    # files: on first boot the deal dirs and comp DB legitimately don't
    # exist until the cutover runbook creates them (steps 4-5), but the
    # disk mount must — an unmounted disk has no /data mount point, so
    # this still 503s on a lost or misrouted mount.
    disk_ok = True
    if os.environ.get("CIM_DEALS_DIR"):
        mount = os.path.dirname(str(settings.CIM_DEALS_DIR).rstrip("/"))
        disk_ok = os.path.ismount(mount)
    if disk_ok and os.environ.get("COMP_DB_PATH"):
        mount = os.path.dirname(os.environ["COMP_DB_PATH"].rstrip("/"))
        disk_ok = os.path.ismount(mount)
    if not disk_ok:
        logger.error("health check: data disk missing or env misrouted")
    ok = db_ok and disk_ok
    return JsonResponse(
        {"status": "ok" if ok else "degraded", "db": db_ok,
         "disk": disk_ok, "git_sha": sha[:12]},
        status=200 if ok else 503,
    )


@login_required
def home(request):
    return redirect("deal-list")


@login_required
def deal_list(request):
    from webapp.models import Deal

    deals = Deal.objects.all()
    state = request.GET.get("state", "")
    recommendation = request.GET.get("recommendation", "")
    asset_type = request.GET.get("asset_type", "")
    if state:
        deals = deals.filter(state=state)
    if recommendation:
        deals = deals.filter(recommendation=recommendation)
    if asset_type:
        deals = deals.filter(asset_type=asset_type)

    def _options(field):
        return (Deal.objects.exclude(**{field: ""})
                .order_by(field).values_list(field, flat=True).distinct())

    return render(request, "webapp/deal_list.html", {
        "deals": deals,
        "state": state, "recommendation": recommendation, "asset_type": asset_type,
        "state_options": _options("state"),
        "recommendation_options": _options("recommendation"),
        "asset_type_options": _options("asset_type"),
    })


# ── Phase 3: extraction status polling ──────────────────────────────

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
    eff = services.effective_config(deal.asset_type)
    if deal.extract_status == "" and not deal.cim_json:
        return render(request, "webapp/assumptions_wait.html",
                      {"deal": deal, "unavailable": True})
    state = _extract_state(deal)
    if state != "done":
        return render(request, "webapp/assumptions_wait.html",
                      {"deal": deal, "failed": state == "failed"})
    report = deal.extraction_report or {}
    missing_required = set(report.get("missing", [])) & assumptions_forms.REQUIRED_FIELDS
    ai_filled = set(report.get("ai_filled") or [])   # item B1 provenance tag
    if request.method == "POST":
        form = assumptions_forms.AssumptionsForm(request.POST)
        if form.is_valid():
            deal.assumption_overrides = assumptions_forms.build_overrides(
                form.cleaned_data, request.POST, deal, eff)
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
        rows = assumptions_forms.parse_unit_mix(request.POST) or []
        status = 422
        preview_cleaned = getattr(form, "cleaned_data", None) or {}
        preview_post = request.POST
    else:
        initial = assumptions_forms.build_initial(deal, eff)
        form = assumptions_forms.AssumptionsForm(initial=initial)
        rows = assumptions_forms.unit_mix_rows(deal)
        status = 200
        # First paint has no in-progress edits, but it must still reflect
        # the deal's SAVED overrides (not just the raw CIM snapshot) — an
        # empty `cleaned` makes build_overrides' delta loop skip every
        # field (cleaned.get(name) is always None), so cim_overrides would
        # come back {} regardless of what's actually saved, and the strip
        # would show pre-override numbers until the analyst's first edit.
        # build_initial's output is already in the same whole-number-percent,
        # exp_<key>-keyed shape build_overrides expects from cleaned_data —
        # it's the merged snapshot+saved-overrides state, so reusing it here
        # reconstructs that same saved state instead of a stale one.
        preview_cleaned = initial
        preview_post = QueryDict("")

    # First-paint model-strip + expense-row context — the same computation
    # the live htmx preview does, so the page never opens on stale "—"
    # placeholders that only resolve after the analyst's first edit. A
    # broken/legacy snapshot must not 500 the page (same resilience
    # posture as assumptions_preview).
    try:
        cim, ov = services.build_preview_cim(deal, preview_cleaned, preview_post)
        saved_exp_overrides = (deal.assumption_overrides or {}).get(
            "expense_line_overrides")
        from analysis.financials import analyze_financials
        fin = analyze_financials(
            cim, expense_line_overrides=(
                ov.get("expense_line_overrides") or saved_exp_overrides),
            mgmt_fee_target_pct=ov.get("mgmt_fee_target_pct"))
        strip_ctx = services.model_strip_context(deal, cim, fin, form)
    except Exception:
        logger.exception("assumptions initial strip failed for deal %s", deal.pk)
        strip_ctx = {
            "population_3mi": (deal.cim_json or {}).get("population_3mi"),
            "median_hhi_3mi": (deal.cim_json or {}).get("median_hhi_3mi"),
            "sf_per_capita": None, "sf_per_capita_problem": "unavailable",
            "sf_per_capita_limit": cfg.GATES.get("max_sf_per_capita"),
            "noi_state": "—", "benchmark_rows": services.stale_benchmark_rows(),
            "check_rows": [], "flagged_checks": [], "check_summary": {},
            "preview_error": True, "stale": False,
        }

    # benchmark_rows is the SAME list model_strip_context returned (or the
    # stale placeholder on the except branch) — no second call, so the
    # visible table and the preview's OOB targets can never compute two
    # different Low/High/CIM $/SF for the same category (services.py
    # review finding). `bf` (the exp_<key> widget) is added here because
    # only the page template needs an editable cell; the preview partial
    # never renders {{ r.bf }}.
    benchmark_rows = strip_ctx["benchmark_rows"]
    for row in benchmark_rows:
        row["bf"] = form[f"exp_{row['key']}"]

    latest_run = deal.runs.first()
    source_log = {}
    if latest_run is not None:
        source_log = ((latest_run.result_json or {}).get("enrichment") or {}
                      ).get("source_log", {})

    f = assumptions_forms
    ctx = {
        "deal": deal, "form": form, "report": report,
        "missing_fields": report.get("missing", []),
        "missing_required": missing_required,
        "warnings": deal.extract_warnings,
        "unit_rows": rows,
        "benchmark_rows": benchmark_rows,
        "driver_rows": f.model_rows(form, f.SECTION_DRIVERS, deal.cim_json or {},
                                    source_log, ai_filled=ai_filled,
                                    extras={"capex_estimate": form["capex_basis"]}),
        "sec_property": f.section_fields(form, f.SECTION_PROPERTY, missing_required),
        "sec_size": f.section_fields(form, f.SECTION_SIZE, missing_required),
        "sec_income": f.section_fields(form, f.SECTION_INCOME, missing_required),
        "demo_rows": f.model_rows(form, f.SECTION_DEMOGRAPHICS, deal.cim_json or {},
                                  source_log, ai_filled=ai_filled),
        "scenario_rows": f.scenario_grid(form),
        "va_rows": f.va_grid(form),
        "rc_rows": f.rc_grid(form),
        "rc_soft": [form["rc_soft_cost_pct_low"], form["rc_soft_cost_pct_high"],
                    form["rc_dev_profit_pct_low"], form["rc_dev_profit_pct_high"]],
        "solver_field": form["solver_target_irr"],
        "mgmt_fee_target_field": form["mgmt_fee_target_pct"],
        "hold_years_field": form["hold_years"],
        "market_cap_field": form["market_cap_rate"],
        "txn_cost_fields": [{"label": label, "field": form[name]}
                            for name, label in f.TXN_COST_LABELS],
        "reserve_field": form["operating_reserve"],
        "reserve_basis_field": form["operating_reserve_basis"],
        "gp_coinvest_field": form["gp_coinvest_pct"],
        # The levered lens's inputs (item E3b). Labels live in forms.py
        # beside the unit rules that convert them, so the template never
        # has to know which of these is a percent and which is a ratio.
        "debt_fields": [{"label": label, "field": form[f"debt_{name}"]}
                        for name, label in f.DEBT_FORM_LABELS],
        "waterfall_fields": [{"label": label, "field": form[f"wf_{name}"]}
                             for name, label in f.WF_FORM_LABELS],
        "am_fee_field": form["am_fee_pct"],
    }
    ctx.update(strip_ctx)
    return render(request, "webapp/assumptions.html", ctx, status=status)


@login_required
@require_POST
def assumptions_preview(request, pk):
    """Live-preview htmx partial: recompute SF/capita, the NOI identity
    chip, and expense-line figures from the in-progress form POST —
    never writes (no Deal.save, no AnalysisRun row)."""
    deal = get_object_or_404(Deal, pk=pk)
    form = assumptions_forms.AssumptionsForm(request.POST)
    ok = form.is_valid()   # populate cleaned_data; preview shows, never blocks
    cleaned = getattr(form, "cleaned_data", None) or {}
    from analysis.financials import analyze_financials
    # A broken/legacy snapshot (or a parse surprise from in-progress form
    # values) must not 500 the preview — same resilience posture as
    # deal_assumptions' first-paint strip (see its comment above).
    try:
        cim, ov = services.build_preview_cim(deal, cleaned, request.POST)
        fin = analyze_financials(
            cim, expense_line_overrides=ov.get("expense_line_overrides"),
            mgmt_fee_target_pct=ov.get("mgmt_fee_target_pct"))
        strip_ctx = services.model_strip_context(deal, cim, fin, form)
        # Distinct from preview_error below: the strip DID recompute, but
        # against a form that failed Django cleaning on at least one field
        # (e.g. a browser-valid-but-Integer-invalid decimal in a
        # population field) — is_valid()'s return was previously discarded
        # entirely, so nothing on screen could ever tell the analyst the
        # numbers they're looking at didn't fully absorb their last edit.
        strip_ctx["stale"] = not ok
    except Exception:
        logger.exception("preview financials failed for deal %s", deal.pk)
        strip_ctx = {
            "deal": deal,
            "population_3mi": (deal.cim_json or {}).get("population_3mi"),
            "median_hhi_3mi": (deal.cim_json or {}).get("median_hhi_3mi"),
            "sf_per_capita": None, "sf_per_capita_problem": "unavailable",
            "sf_per_capita_limit": cfg.GATES.get("max_sf_per_capita"),
            "noi_state": "—", "benchmark_rows": services.stale_benchmark_rows(),
            "check_rows": [], "flagged_checks": [], "check_summary": {},
            "preview_error": True, "stale": False,
        }
    return render(request, "webapp/_model_preview.html", strip_ctx)


@login_required
def unit_mix_row(request):
    return render(request, "webapp/_unit_mix_row.html", {"row": {}})


# ── Phase 3: upload flow ─────────────────────────────────────────────

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
    elif not services.looks_like_pdf(cim):
        errors.append("The CIM is named .pdf but its contents are not a PDF.")
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
        "run_failed": state == "failed",
        # Item G's distribution gate. Read at request time via `cfg.` so
        # flipping the flag takes effect on the next page load rather
        # than on the next restart.
        "gc_cleared": cfg.INVESTOR_SUMMARY_GC_CLEARED,
    }
    if done_run:
        r = done_run.result_json or {}
        ctx["header"] = results_ctx.header_metrics(deal, r)
        ctx["run_warnings"] = r.get("errors") or []

        # A settings override this run REFUSED, surfaced where the numbers
        # it changed are read. `build_config_patch` skips a stored row
        # whose value is outside its key's bounds, and the worker has
        # always stamped that on `applied_overrides["config_skipped"]` —
        # but nothing rendered the stamp, so it was a database field no
        # user could see.
        #
        # That gap mattered more than it looks. Refusing an out-of-range
        # row is only defensible BECAUSE it is reported; the argument for
        # reversing PR #45's "badge it and apply it anyway" was precisely
        # that skipping is not silent. A stamp nobody can read makes it
        # silent, which is the position #45 was right to reject.
        #
        # It joins `run_warnings` rather than getting a banner of its own:
        # that banner already renders on EVERY tab, and an override the
        # run declined moved numbers on all of them, not just the summary.
        skipped_cfg = (done_run.applied_overrides or {}).get(
            "config_skipped") or []
        if skipped_cfg:
            # Concatenate, never append — `r["errors"]` is the stored
            # result payload, and mutating it here would edit the run's
            # own record of what the engine reported.
            ctx["run_warnings"] = ctx["run_warnings"] + [
                "Settings override not applied to this run: "
                f"{', '.join(sorted(skipped_cfg))}. The value stored for "
                "it is outside the allowed range for that setting (or the "
                "key no longer exists), so this run used the built-in "
                "default instead. Fix or delete the row on the Settings "
                "page and re-run."]
        if tab == "summary":
            ctx.update(results_ctx.summary_context(r))
            ctx.update(results_ctx.checks_context(r))
            # Provenance sits with integrity, above the numbers: the
            # register says whether the inputs are self-consistent, the
            # fill log says which of them the CIM never supplied (item T
            # Category 4).
            ctx.update(results_ctx.fill_log_context(r))
            # ...and the register says where every OTHER number came
            # from, which is the question the two above leave open (item
            # T Category 6).
            ctx.update(results_ctx.register_context(r))
            ctx.update(results_ctx.capital_context(r))
        elif tab == "returns":
            ctx.update(results_ctx.returns_context(r))
            # The levered second lens, below the unlevered tables on the
            # same tab. Not its own tab: a lens you have to go looking for
            # is not a second lens, and the unlevered screen stays first
            # because it is still the primary gate.
            ctx.update(results_ctx.levered_context(r))
        elif tab == "financials":
            ctx.update(results_ctx.financials_context(r))
            ctx["benchmark_rows"] = services.expense_benchmark_rows(deal)
        elif tab == "risks":
            ctx.update(results_ctx.risks_context(r))
    return render(request, "webapp/deal_detail.html", ctx)


DOWNLOAD_KINDS = {
    "memo": ("memo_filename",
             "application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document"),
    "excel": ("excel_filename",
              "application/vnd.openxmlformats-officedocument"
              ".spreadsheetml.sheet"),
    "template": ("template_filename",
                 "application/vnd.ms-excel.sheet.macroEnabled.12"),
    # Item G. Only ever served from a RUN, never from the Deal: `Deal`
    # has no such field, and `getattr(deal, field, "")` below would
    # silently return "" rather than 404 if that ever changed.
    "investor_summary": ("investor_summary_filename",
                         "application/vnd.openxmlformats-officedocument"
                         ".wordprocessingml.document"),
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


# ── Phase 5: settings (config overrides) ────────────────────────────

@login_required
def settings_page(request):
    from webapp.forms import (ConfigOverrideForm, bounds_display,
                              format_override_value, override_key_registry,
                              value_in_bounds)
    from webapp.services import ASSET_TYPES
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
        # Rows saved before the value box grew bounds, or written straight
        # to the model, are badged rather than deleted — the row is never
        # retired from the database. What CHANGED is that they no longer
        # reach a run: `build_config_patch` re-checks bounds and skips
        # them, so the model uses the config default and the run record
        # lists the key under `config_skipped`.
        #
        # The note STILL branches on all four statuses, and the branch is
        # now load-bearing in a way the first draft of this change missed.
        #
        # `resolve_config_overrides` returns ONE row per lane — the
        # winner. So "out of range" and "skipped" are not the same claim:
        # a superseded out-of-range row is never offered to
        # `build_config_patch` at all, the winning row supplies the value,
        # and the key never appears in `config_skipped`. A note that said
        # "it is SKIPPED, runs use the config default" for that row was
        # simply false — the run used the winner's value. That is the
        # same class of bug the four-way branch was written to fix (a
        # scheduled row told "it does reach runs"), reintroduced in new
        # wording and caught in review.
        #
        # So among the rows this NOTE can appear on, only the ACTIVE one
        # may claim the skip and the `config_skipped` stamp. The others
        # say why they do not reach a run at all, which is true whatever
        # their value.
        #
        # "among the rows this note can appear on" is doing real work:
        # `config_skipped` is not active-only in general — an unknown-key
        # row lands there too. It just never carries THIS note, because
        # `value_in_bounds` returns True for a key config no longer
        # defines, so `oob` is always False for that status and the note
        # never renders. The `unknown key` branch below is therefore
        # unreachable today, and is spelled out anyway so a fifth status
        # cannot silently inherit someone else's sentence.
        oob = not value_in_bounds(r.key, r.value)
        refusal = {
            "active": ("so it is SKIPPED: runs use the config default "
                       "instead and record the key under config_skipped."),
            "scheduled": (f"and it does not reach a run yet in any case — "
                          f"it is dated {r.effective_date:%Y-%m-%d}. It "
                          f"will be skipped when that date arrives."),
            "superseded": ("though it is superseded, so it does not reach "
                           "a run either way — a later row supplies this "
                           "setting."),
            "unknown key": ("though config.py no longer defines this key, "
                            "so it does not reach a run either way."),
        }
        overrides.append({
            "row": r, "status": status, "out_of_bounds": oob,
            "out_of_bounds_note": (
                "Outside the allowed range for this setting, "
                f"{refusal[status]} Delete it and re-add the value in "
                "range."),
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
            # Hover text, not a fourth column: the preview already runs
            # three columns inside a multi-column layout, and the bound
            # is reference material, not something read on every visit.
            "allowed": bounds_display(spec),
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
