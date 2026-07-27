import logging
import os
import shutil

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_htmx.http import HttpResponseClientRedirect

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
    return JsonResponse(
        {"status": "ok" if db_ok else "degraded", "db": db_ok, "git_sha": sha[:12]},
        status=200 if db_ok else 503,
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
    if deal.extract_status == "" and not deal.cim_json:
        return render(request, "webapp/assumptions_wait.html",
                      {"deal": deal, "unavailable": True})
    state = _extract_state(deal)
    if state != "done":
        return render(request, "webapp/assumptions_wait.html",
                      {"deal": deal, "failed": state == "failed"})
    report = deal.extraction_report or {}
    missing_required = set(report.get("missing", [])) & assumptions_forms.REQUIRED_FIELDS
    if request.method == "POST":
        form = assumptions_forms.AssumptionsForm(request.POST)
        if form.is_valid():
            deal.assumption_overrides = assumptions_forms.build_overrides(
                form.cleaned_data, request.POST, deal)
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
    else:
        form = assumptions_forms.AssumptionsForm(
            initial=assumptions_forms.build_initial(deal))
        rows = assumptions_forms.unit_mix_rows(deal)
        status = 200
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
    }, status=status)


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
