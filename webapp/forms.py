"""Assumptions editor — field definitions, initial building, delta saving.

Percent convention: templates and form data hold WHOLE numbers (type 6
for 6%); snapshots, config defaults, and stored overrides hold decimals.
Conversion happens ONLY in build_initial (×100) and build_overrides
(÷100) — never in custom form fields, so bound redisplay round-trips
the raw submitted strings untouched.
"""
import logging

from django import forms
from django.utils import timezone

import config as cfg
from webapp.services import ASSET_TYPES
from registry import ScenarioType

logger = logging.getLogger("cim_analyst.web")

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


def build_initial(deal, eff=None) -> dict:
    if eff is None:
        from webapp import services      # lazy: avoids a module cycle
        eff = services.effective_config(deal.asset_type)
    snapshot = deal.cim_json or {}
    saved = deal.assumption_overrides or {}
    merged = {**snapshot, **saved.get("cim_overrides", {})}
    initial = {}
    for name in CIM_SCALAR_FIELDS:
        v = merged.get(name)
        initial[name] = _pct_display(v) if name in CIM_PCT_FIELDS else v
    scen_saved = saved.get("scenario_overrides", {})
    for sc in SCENARIO_KEYS:
        current = {**eff["SCENARIO_DEFAULTS"].get(sc, {}), **scen_saved.get(sc, {})}
        for p in SCENARIO_PARAMS:
            initial[f"scen_{sc}_{p}"] = _pct_display(current.get(p))
    va_saved = saved.get("va_scenario_overrides", {})
    for sc in SCENARIO_KEYS:
        current = {**eff["VALUE_ADD_SCENARIOS"].get(sc, {}), **va_saved.get(sc, {})}
        for p in VA_PARAMS:
            v = current.get(p)
            if p in VA_NON_PCT:
                initial[f"va_{sc}_{p}"] = float(v) if v is not None else None
            else:
                initial[f"va_{sc}_{p}"] = _pct_display(v)
    rc_saved = saved.get("replacement_cost_overrides", {})
    for key in RC_KEYS:
        low, high = rc_saved.get(key, eff["REPLACEMENT_COST"][key])
        if key in RC_PCT_KEYS:
            low, high = float(low) * 100, float(high) * 100
        initial[f"rc_{key}_low"] = round(float(low), 4)
        initial[f"rc_{key}_high"] = round(float(high), 4)
    initial["solver_target_irr"] = _pct_display(
        saved.get("solver_target_irr", eff["SOLVER_TARGET_IRR"]))
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


# ── POST parsing + delta computation ────────────────────────────────

def parse_unit_mix(post) -> list[dict] | None:
    """Parallel getlist arrays → canonical rows; count ≤ 0 rows dropped.
    Returns None when the form carried no unit-mix inputs at all."""
    labels = post.getlist("um_label")
    if not labels:
        return None
    counts = post.getlist("um_count")
    sfs = post.getlist("um_sf")
    rates = post.getlist("um_rate")
    ccs = post.getlist("um_cc")
    rows = []
    for label, count, sf, rate, cc in zip(labels, counts, sfs, rates, ccs):
        try:
            count = int(float(count or 0))
            sf = float(sf or 0)
            rate = float(rate or 0)
        except ValueError:
            logger.warning(
                "unit-mix row dropped on save (non-numeric values): "
                "label=%r count=%r sf=%r rate=%r", label, count, sf, rate)
            continue
        if count <= 0:
            continue
        rows.append({"size_label": label.strip(), "count": count, "sf": sf,
                     "rate": rate, "climate_controlled": cc == "1"})
    return rows


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return round(float(a), 6) == round(float(b), 6)
    return a == b


def _rounded_sections(defaults, params) -> dict:
    return {sc: {p: round(float(defaults[sc][p]), 6) for p in params}
            for sc in SCENARIO_KEYS}


def _submitted_sections(cleaned, prefix, params, defaults, non_pct=()) -> dict:
    """Grid values → decimal dicts; blanks fall back to config defaults."""
    out = {}
    for sc in SCENARIO_KEYS:
        d = defaults.get(sc, {})
        s = {}
        for p in params:
            v = cleaned.get(f"{prefix}_{sc}_{p}")
            if v is None:
                s[p] = round(float(d.get(p, 0)), 6)
            elif p in non_pct:
                s[p] = round(float(v), 6)
            else:
                s[p] = round(float(v) / 100.0, 6)
        out[sc] = s
    return out


def build_overrides(cleaned, post, deal, eff=None) -> dict:
    """Deltas only — see the Phase 3 plan's Design Decisions. Sections
    equal to their defaults are omitted entirely."""
    if eff is None:
        from webapp import services      # lazy: avoids a module cycle
        eff = services.effective_config(deal.asset_type)
    snapshot = deal.cim_json or {}
    out = {}

    cim_o = {}
    for name in CIM_SCALAR_FIELDS:
        v = cleaned.get(name)
        if v in (None, ""):
            continue
        if name in CIM_PCT_FIELDS:
            v = round(v / 100.0, 6)
        snap = snapshot.get(name)
        if snap is None or not _same(v, snap):
            cim_o[name] = v
    mix = parse_unit_mix(post)
    if mix is not None and mix != _normalize_unit_mix(snapshot.get("unit_mix")):
        cim_o["unit_mix"] = mix
    if cim_o:
        out["cim_overrides"] = cim_o

    scen = _submitted_sections(cleaned, "scen", SCENARIO_PARAMS, eff["SCENARIO_DEFAULTS"])
    if scen != _rounded_sections(eff["SCENARIO_DEFAULTS"], SCENARIO_PARAMS):
        out["scenario_overrides"] = scen
    va = _submitted_sections(cleaned, "va", VA_PARAMS, eff["VALUE_ADD_SCENARIOS"],
                             non_pct=VA_NON_PCT)
    if va != _rounded_sections(eff["VALUE_ADD_SCENARIOS"], VA_PARAMS):
        out["va_scenario_overrides"] = va

    rc = {}
    for key in RC_KEYS:
        low = cleaned.get(f"rc_{key}_low")
        high = cleaned.get(f"rc_{key}_high")
        if low is None or high is None:
            continue
        if key in RC_PCT_KEYS:
            low, high = low / 100.0, high / 100.0
        cur = [round(float(low), 6), round(float(high), 6)]
        d_low, d_high = eff["REPLACEMENT_COST"][key]
        if cur != [round(float(d_low), 6), round(float(d_high), 6)]:
            rc[key] = cur
    if rc:
        out["replacement_cost_overrides"] = rc

    tgt = cleaned.get("solver_target_irr")
    if tgt is not None:
        tgt = round(tgt / 100.0, 6)
        if not _same(tgt, eff["SOLVER_TARGET_IRR"]):
            out["solver_target_irr"] = tgt

    return out


# ── Phase 5: config override registry + form ────────────────────────

GATES_INT_KEYS = {"population_3mi", "unproven_vintage_year"}
EXPENSE_PCT_KEYS = {"mgmt_fee_pct", "opex_revenue_ratio"}
RC_LEGACY_ALIASES = {"non_cc_per_sf", "cc_per_sf", "site_work_per_sf"}
# total_opex is recomputed from the line items by get_regional_benchmarks
# (config.py:394-396) — an override would show in the preview but never
# reach a run with a known state. Derived, not editable.
EXPENSE_DERIVED_KEYS = {"total_opex"}


def _label(key: str) -> str:
    return key.split(".")[-1].replace("_", " ").title()


def dotted_get(root, dotted_key: str):
    """Resolve 'GATES.min_irr_5yr' against a config-shaped tree — the
    config module itself or an effective_config() mapping. ScenarioType
    is a str Enum, so plain [] lookups work at every level."""
    node = root
    for part in dotted_key.split("."):
        node = node[part] if isinstance(node, dict) else getattr(node, part)
    return node


def override_key_registry() -> dict:
    """Editable threshold keys, derived LIVE from config.py so the picker
    can never drift from the real constants. Values are stored/applied in
    canonical config units; `pct` keys display as whole-number percents.
    """
    reg = {}
    for k in cfg.GATES:
        reg[f"GATES.{k}"] = {
            "group": "Gates", "kind": "scalar",
            "pct": k not in GATES_INT_KEYS, "int": k in GATES_INT_KEYS,
            "label": _label(k)}
    for k in cfg.EXPENSE_BENCHMARKS:
        if k in EXPENSE_DERIVED_KEYS:
            continue
        reg[f"EXPENSE_BENCHMARKS.{k}"] = {
            "group": "Expense Benchmarks ($/NRSF/yr)", "kind": "range",
            "pct": k in EXPENSE_PCT_KEYS, "int": False, "label": _label(k)}
    for k in cfg.REPLACEMENT_COST:
        if k in RC_LEGACY_ALIASES:
            continue                     # synced automatically by the patcher
        reg[f"REPLACEMENT_COST.{k}"] = {
            "group": "Replacement Cost ($/SF)", "kind": "range",
            "pct": k in RC_PCT_KEYS, "int": False, "label": _label(k)}
    for top, group in (("SCENARIO_DEFAULTS", "Scenarios"),
                       ("VALUE_ADD_SCENARIOS", "Value-Add Scenarios")):
        for scen, params in getattr(cfg, top).items():
            for p in params:
                is_int = p in VA_NON_PCT
                reg[f"{top}.{scen.value}.{p}"] = {
                    "group": group, "kind": "scalar",
                    "pct": not is_int, "int": is_int,
                    "label": f"{scen.value.title()} {_label(p)}"}
    for k in cfg.VALUE_ADD_TRIGGERS:
        reg[f"VALUE_ADD_TRIGGERS.{k}"] = {
            "group": "Value-Add Triggers", "kind": "scalar",
            "pct": True, "int": False, "label": _label(k)}
    reg["SOLVER_TARGET_IRR"] = {
        "group": "Solver", "kind": "scalar", "pct": True, "int": False,
        "label": "Solver Target IRR"}
    return reg


def _parse_num(raw_part: str) -> float:
    # NB: forms.py already has a `_num()` field factory — don't shadow it.
    try:
        return float(raw_part.replace("%", "").replace("$", "").strip())
    except ValueError:
        raise forms.ValidationError("Enter numbers only.")


def parse_override_value(key: str, raw: str):
    """Display units in ('12' or '1.40, 2.60'), canonical units out
    (0.12 or [1.4, 2.6]). Comma is the range separator ONLY for range
    keys; scalars strip thousands separators so the displayed format is
    always re-enterable (review finding)."""
    spec = override_key_registry().get(key)
    if spec is None:
        raise forms.ValidationError("Unknown setting key.")
    raw = str(raw).replace("–", ",")
    if spec["kind"] == "range":
        parts = [p for p in (s.strip() for s in raw.split(",")) if p]
        if len(parts) != 2:
            raise forms.ValidationError("Enter two numbers: low, high.")
        low, high = _parse_num(parts[0]), _parse_num(parts[1])
        if spec["pct"]:
            low, high = low / 100.0, high / 100.0
        if low > high:
            raise forms.ValidationError("Low must be ≤ high.")
        return [round(low, 6), round(high, 6)]
    v = _parse_num(raw.replace(",", ""))
    if spec["int"]:
        return int(v)
    if spec["pct"]:
        v = v / 100.0
    return round(v, 6)


def format_override_value(key: str, value) -> str:
    """Canonical units in, display string out (inverse of parse — no
    thousands separators, so any displayed value re-parses verbatim)."""
    spec = override_key_registry().get(key)

    def one(v, pct):
        if pct:
            return f"{round(float(v) * 100, 4):g}%"
        if spec and spec["int"]:
            return f"{int(v)}"
        return f"{round(float(v), 4):g}"

    pct = bool(spec and spec["pct"])
    if isinstance(value, (list, tuple)):
        return f"{one(value[0], pct)} – {one(value[1], pct)}"
    return one(value, pct)


class ConfigOverrideForm(forms.Form):
    key = forms.ChoiceField()
    value = forms.CharField(max_length=60)
    asset_type = forms.ChoiceField(required=False)
    # timezone.localdate, NOT datetime.date.today: Render's system clock
    # is UTC — after ~6pm Chicago, today() is tomorrow, and a freshly
    # added override would be silently "scheduled"/inert (review finding).
    effective_date = forms.DateField(initial=timezone.localdate,
                                     widget=forms.DateInput(attrs={"type": "date"}))
    note = forms.CharField(max_length=200, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        reg = override_key_registry()
        groups = {}
        for key, spec in reg.items():
            groups.setdefault(spec["group"], []).append(
                (key, f"{spec['label']} ({key})"))
        self.fields["key"].choices = [
            (g, opts) for g, opts in groups.items()]
        self.fields["asset_type"].choices = (
            [("", "All asset types")] + [(a, a) for a in ASSET_TYPES])

        base = "border border-slate-300 rounded px-2 py-1 text-sm block"
        self.fields["key"].widget.attrs["class"] = base + " max-w-[22rem]"
        self.fields["value"].widget.attrs.update(
            {"class": base + " w-32", "placeholder": "12  or  1.4, 2.6"})
        self.fields["asset_type"].widget.attrs["class"] = base
        self.fields["effective_date"].widget.attrs["class"] = base
        self.fields["note"].widget.attrs.update(
            {"class": base + " w-44", "placeholder": "why"})

    def clean(self):
        cleaned = super().clean()
        key, raw = cleaned.get("key"), cleaned.get("value")
        if key and raw is not None:
            cleaned["parsed_value"] = parse_override_value(key, raw)
        return cleaned

    def save(self):
        from webapp.models import ConfigOverride
        return ConfigOverride.objects.create(
            key=self.cleaned_data["key"],
            value=self.cleaned_data["parsed_value"],
            asset_type=self.cleaned_data.get("asset_type") or "",
            effective_date=self.cleaned_data["effective_date"],
            note=self.cleaned_data.get("note") or "")
