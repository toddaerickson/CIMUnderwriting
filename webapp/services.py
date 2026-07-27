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
