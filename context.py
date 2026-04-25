"""
AnalysisContext — single container for all pipeline state.

Replaces the 10-12 loose variables that were passed between pipeline
stages in run.py.  Every stage reads from and writes to this context,
making data flow explicit and testable.
"""

from __future__ import annotations

import copy
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class AnalysisContext:
    """Mutable container for the full analysis pipeline."""

    # ── Inputs ────────────────────────────────────────────────────
    pdf_path: str = ""

    # ── Extraction ────────────────────────────────────────────────
    raw_pdf: dict = field(default_factory=dict)       # from extract_pdf()
    cim_data: Any = None                              # CIMData dataclass
    enrichment: Any = None                            # EnrichmentResult
    rent_survey: Any = None                           # RentSurveyResult

    # ── Pipeline snapshots (Phase 3) ─────────────────────────────
    _snapshots: dict = field(default_factory=dict, repr=False)

    # ── Analysis ──────────────────────────────────────────────────
    financial_analysis: dict = field(default_factory=dict)
    market_analysis: dict = field(default_factory=dict)
    physical_analysis: dict = field(default_factory=dict)
    rent_analysis: dict = field(default_factory=dict)
    value_add_ops: dict = field(default_factory=dict)
    risk_analysis: dict = field(default_factory=dict)

    # ── Valuation ─────────────────────────────────────────────────
    scenario_results: dict = field(default_factory=dict)
    sensitivity: dict = field(default_factory=dict)
    va_results: dict = field(default_factory=dict)
    max_offer: dict = field(default_factory=dict)
    va_max_offer: dict = field(default_factory=dict)

    # ── Gates ─────────────────────────────────────────────────────
    gate_results: list = field(default_factory=list)
    gate_summary: dict = field(default_factory=dict)

    # ── Outputs ───────────────────────────────────────────────────
    memo_path: str = ""
    excel_path: str = ""
    template_path: str = ""

    # ── Derived helpers (read-only convenience) ───────────────────

    @property
    def adjusted_noi(self) -> Optional[float]:
        return self.financial_analysis.get(
            "adjusted_ttm_noi", {}
        ).get("analyst_adjusted_noi")

    @property
    def expense_ratio(self) -> Optional[float]:
        return self.financial_analysis.get(
            "expense_ratio_check", {}
        ).get("opex_revenue_ratio")

    @property
    def asking_price(self) -> float:
        return (self.cim_data.asking_price or 0) if self.cim_data else 0

    @property
    def capex(self) -> float:
        return (self.cim_data.capex_estimate or 0) if self.cim_data else 0

    @property
    def nrsf(self) -> float:
        return (self.cim_data.nrsf or 1) if self.cim_data else 1

    @property
    def property_name(self) -> str:
        return (self.cim_data.property_name or "Unknown_Property") if self.cim_data else "Unknown_Property"

    @property
    def output_dir(self) -> str:
        return os.path.dirname(self.pdf_path) or "."

    # ── Snapshot helpers ──────────────────────────────────────────

    def snapshot(self, label: str):
        """Deep-copy cim_data at this point in the pipeline."""
        if self.cim_data is None:
            return
        self._snapshots[label] = copy.deepcopy(self.cim_data)
        logger.debug("Snapshot '%s' saved (%d fields populated)",
                     label, self.cim_data.extraction_report()["populated"])

    def diff_snapshot(self, label: str) -> dict[str, tuple]:
        """Compare current cim_data against a named snapshot.

        Returns {field_name: (old_value, new_value)} for changed fields.
        """
        prev = self._snapshots.get(label)
        if prev is None or self.cim_data is None:
            return {}
        changes = {}
        for fld in vars(prev):
            if fld.startswith("_"):
                continue
            old = getattr(prev, fld, None)
            new = getattr(self.cim_data, fld, None)
            if old != new:
                changes[fld] = (old, new)
        return changes
