import logging
import os

from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render

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
    from django.shortcuts import redirect

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
