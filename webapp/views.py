import logging
import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_htmx.http import HttpResponseClientRedirect

from webapp import services
from webapp.models import Deal

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
    return HttpResponse("Assumptions editor lands in Task 5.")  # replaced in Task 5
