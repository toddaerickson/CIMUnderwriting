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
    return render(request, "webapp/home.html")
