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
