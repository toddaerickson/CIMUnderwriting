"""Assumptions editor — field definitions, initial building, delta saving.

Percent convention: templates and form data hold WHOLE numbers (type 6
for 6%); snapshots, config defaults, and stored overrides hold decimals.
Conversion happens ONLY in build_initial (×100) and build_overrides
(÷100) — never in custom form fields, so bound redisplay round-trips
the raw submitted strings untouched.
"""
from django import forms

import config as cfg
from registry import ScenarioType

# Mirrors gui/components/assumptions_editor.REQUIRED_FIELDS — parity-
# tested in tests/test_web_analyze.py; consolidated when gui/ retires
# in Phase 5.
REQUIRED_FIELDS = {
    "asking_price", "nrsf", "total_units", "ttm_noi",
    "physical_occupancy", "state", "ttm_egr",
}

SCENARIO_KEYS = [s.value for s in ScenarioType]          # bear, base, bull
SCENARIO_PARAM_LABELS = [
    ("yr1_noi_bump", "Yr1 NOI Bump (%)"),
    ("stabilized_occ", "Stabilized Occ (%)"),
    ("rev_cagr_yr1_3", "Rev CAGR Yr 1–3 (%)"),
    ("rev_cagr_yr4_5", "Rev CAGR Yr 4–5 (%)"),
    ("exp_growth", "Expense Growth (%)"),
    ("exit_cap", "Exit Cap Rate (%)"),
]
SCENARIO_PARAMS = [p for p, _ in SCENARIO_PARAM_LABELS]
VA_PARAM_LABELS = [
    ("target_occupancy", "Target Occ (%)"),
    ("months_to_stabilize", "Months to Stabilize"),
    ("rent_growth_to_market", "Rent Growth to Mkt (%)"),
    ("post_stabilize_rev_growth", "Post-Stab Rev Growth (%)"),
    ("exit_cap", "Exit Cap Rate (%)"),
    ("expense_growth", "Expense Growth (%)"),
]
VA_PARAMS = [p for p, _ in VA_PARAM_LABELS]
VA_NON_PCT = {"months_to_stabilize"}

CIM_CHAR_FIELDS = ["property_name", "address", "city", "state", "msa"]
CIM_INT_FIELDS = ["year_built", "year_expanded", "total_units",
                  "population_1mi", "population_3mi", "population_5mi"]
CIM_FLOAT_FIELDS = ["acreage", "nrsf", "ss_driveup_sf", "ss_enclosed_sf",
                    "brv_enclosed_sf", "brv_covered_sf", "brv_open_sf",
                    "asking_price", "capex_estimate", "ttm_gpr", "other_income",
                    "ttm_egr", "ttm_total_revenue", "ttm_total_expenses",
                    "cim_yr1_noi", "ttm_noi", "median_hhi_3mi", "market_rent_psf"]
CIM_PCT_FIELDS = ["cc_pct", "physical_occupancy", "economic_occupancy", "mgmt_fee_pct"]
CIM_SCALAR_FIELDS = CIM_CHAR_FIELDS + CIM_INT_FIELDS + CIM_FLOAT_FIELDS + CIM_PCT_FIELDS

RC_PCT_KEYS = {"soft_cost_pct", "dev_profit_pct"}
RC_KEYS = [k for hard, site, _ in cfg.FACILITY_TYPES for k in (hard, site)] \
    + ["soft_cost_pct", "dev_profit_pct"]

# Section layouts consumed by section_fields() + the template.
SECTION_PROPERTY = [
    ("property_name", "Property Name"), ("address", "Address"),
    ("city", "City"), ("state", "State"), ("msa", "MSA"),
    ("year_built", "Year Built"), ("year_expanded", "Year Expanded"),
    ("acreage", "Acreage"),
]
SECTION_SIZE = [
    ("nrsf", "NRSF"), ("total_units", "Total Units"), ("cc_pct", "CC (%)"),
    ("physical_occupancy", "Physical Occupancy (%)"),
    ("economic_occupancy", "Economic Occupancy (%)"),
    ("ss_driveup_sf", "SS Drive-Up SF"), ("ss_enclosed_sf", "SS Enclosed SF"),
    ("brv_enclosed_sf", "BRV Enclosed SF"), ("brv_covered_sf", "BRV Covered SF"),
    ("brv_open_sf", "BRV Open Parking SF"),
]
SECTION_INCOME = [
    ("asking_price", "Asking Price ($)"), ("capex_estimate", "CapEx Estimate ($)"),
    ("ttm_gpr", "Gross Potential Rent ($)"), ("other_income", "Other Income ($)"),
    ("ttm_egr", "Effective Gross Revenue ($)"), ("ttm_total_revenue", "Total Revenue ($)"),
    ("ttm_total_expenses", "Total Expenses ($)"), ("cim_yr1_noi", "CIM Year 1 NOI ($)"),
    ("ttm_noi", "TTM NOI ($)"), ("mgmt_fee_pct", "Mgmt Fee (% EGR)"),
]
SECTION_DEMOGRAPHICS = [
    ("population_1mi", "Population 1-mi"), ("population_3mi", "Population 3-mi"),
    ("population_5mi", "Population 5-mi"), ("median_hhi_3mi", "Median HHI 3-mi ($)"),
    ("market_rent_psf", "Market Rent ($/SF/mo)"),
]

INPUT_CSS = "w-full border border-slate-300 rounded px-2 py-1 text-sm"


def _text():
    return forms.TextInput(attrs={"class": INPUT_CSS})


def _num():
    return forms.NumberInput(attrs={"class": INPUT_CSS, "step": "any", "min": "0"})


class AssumptionsForm(forms.Form):
    """Every field optional; blanks mean 'no override' (CIM fields) or
    'keep the config default' (scenario/RC/solver fields)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in CIM_CHAR_FIELDS:
            self.fields[name] = forms.CharField(
                required=False, max_length=2 if name == "state" else 200,
                widget=_text())
        for name in CIM_INT_FIELDS:
            self.fields[name] = forms.IntegerField(
                required=False, min_value=0, widget=_num())
        for name in CIM_FLOAT_FIELDS + CIM_PCT_FIELDS:
            self.fields[name] = forms.FloatField(
                required=False, min_value=0, widget=_num())
        for sc in SCENARIO_KEYS:
            for p in SCENARIO_PARAMS:
                self.fields[f"scen_{sc}_{p}"] = forms.FloatField(
                    required=False, widget=_num())
            for p in VA_PARAMS:
                self.fields[f"va_{sc}_{p}"] = forms.FloatField(
                    required=False, min_value=0, widget=_num())
        for key in RC_KEYS:
            for bound in ("low", "high"):
                self.fields[f"rc_{key}_{bound}"] = forms.FloatField(
                    required=False, min_value=0, widget=_num())
        self.fields["solver_target_irr"] = forms.FloatField(
            required=False, min_value=0, widget=_num())

    def clean_state(self):
        return (self.cleaned_data.get("state") or "").upper()


# ── Initial values (decimals → whole-number display) ────────────────

def _pct_display(v):
    return round(float(v) * 100, 4) if v is not None else None


def build_initial(deal) -> dict:
    snapshot = deal.cim_json or {}
    saved = deal.assumption_overrides or {}
    merged = {**snapshot, **saved.get("cim_overrides", {})}
    initial = {}
    for name in CIM_SCALAR_FIELDS:
        v = merged.get(name)
        initial[name] = _pct_display(v) if name in CIM_PCT_FIELDS else v
    scen_saved = saved.get("scenario_overrides", {})
    for sc in SCENARIO_KEYS:
        current = {**cfg.SCENARIO_DEFAULTS.get(sc, {}), **scen_saved.get(sc, {})}
        for p in SCENARIO_PARAMS:
            initial[f"scen_{sc}_{p}"] = _pct_display(current.get(p))
    va_saved = saved.get("va_scenario_overrides", {})
    for sc in SCENARIO_KEYS:
        current = {**cfg.VALUE_ADD_SCENARIOS.get(sc, {}), **va_saved.get(sc, {})}
        for p in VA_PARAMS:
            v = current.get(p)
            if p in VA_NON_PCT:
                initial[f"va_{sc}_{p}"] = float(v) if v is not None else None
            else:
                initial[f"va_{sc}_{p}"] = _pct_display(v)
    rc_saved = saved.get("replacement_cost_overrides", {})
    for key in RC_KEYS:
        low, high = rc_saved.get(key, cfg.REPLACEMENT_COST[key])
        if key in RC_PCT_KEYS:
            low, high = float(low) * 100, float(high) * 100
        initial[f"rc_{key}_low"] = round(float(low), 4)
        initial[f"rc_{key}_high"] = round(float(high), 4)
    initial["solver_target_irr"] = _pct_display(
        saved.get("solver_target_irr", cfg.SOLVER_TARGET_IRR))
    return initial


def _normalize_unit_mix(raw) -> list[dict]:
    """Snapshot/override unit-mix dicts → canonical editor rows
    (drops zero-count rows and UnitType's width/depth extras)."""
    rows = []
    for u in raw or []:
        count = int(u.get("count") or 0)
        if count <= 0:
            continue
        rows.append({
            "size_label": str(u.get("size_label") or ""),
            "count": count,
            "sf": float(u.get("sf") or 0),
            "rate": float(u.get("rate") or 0),
            "climate_controlled": bool(u.get("climate_controlled")),
        })
    return rows


def unit_mix_rows(deal) -> list[dict]:
    saved = (deal.assumption_overrides or {}).get("cim_overrides", {})
    if saved.get("unit_mix") is not None:
        return _normalize_unit_mix(saved["unit_mix"])
    return _normalize_unit_mix((deal.cim_json or {}).get("unit_mix"))


# ── Template helpers ────────────────────────────────────────────────

def section_fields(form, pairs, missing_required):
    return [{"bf": form[name], "label": label,
             "flag": name in missing_required} for name, label in pairs]


def scenario_grid(form):
    return [{"label": label,
             "cells": [form[f"scen_{sc}_{p}"] for sc in SCENARIO_KEYS]}
            for p, label in SCENARIO_PARAM_LABELS]


def va_grid(form):
    return [{"label": label,
             "cells": [form[f"va_{sc}_{p}"] for sc in SCENARIO_KEYS]}
            for p, label in VA_PARAM_LABELS]


def rc_grid(form):
    """One row per facility type: hard low/high + site low/high."""
    return [{"label": display,
             "cells": [form[f"rc_{hard}_low"], form[f"rc_{hard}_high"],
                       form[f"rc_{site}_low"], form[f"rc_{site}_high"]]}
            for hard, site, display in cfg.FACILITY_TYPES]
