"""Assumptions editor — field definitions, initial building, delta saving.

Percent convention: templates and form data hold WHOLE numbers (type 6
for 6%); snapshots, config defaults, and stored overrides hold decimals.
Conversion happens ONLY in build_initial (×100), build_overrides (÷100)
and check_input_from_cleaned (÷100, read-only) — never in custom form
fields, so bound redisplay round-trips the raw submitted strings
untouched.
"""
import logging
import math

from django import forms
from django.utils import timezone

import config as cfg
from analysis import checks
from analysis.checks import noi_recon_tolerance      # noqa: F401 (re-export)
from webapp.services import ASSET_TYPES
from registry import ScenarioType

logger = logging.getLogger("cim_analyst.web")

# Single source of truth for required assumption fields (the Streamlit
# editor's copy was consolidated here when gui/ retired in Phase 5).
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

CIM_CHAR_FIELDS = ["property_name", "address", "city", "state", "msa",
                   "market_verification", "street_rate_trend"]
CIM_INT_FIELDS = ["year_built", "year_expanded", "total_units",
                  "population_1mi", "population_3mi", "population_5mi"]
CIM_FLOAT_FIELDS = ["acreage", "nrsf", "ss_driveup_sf", "ss_enclosed_sf",
                    "brv_enclosed_sf", "brv_covered_sf", "brv_open_sf",
                    "asking_price", "capex_estimate", "ttm_gpr", "other_income",
                    "ttm_egr", "ttm_total_revenue", "ttm_total_expenses",
                    "cim_yr1_noi", "ttm_noi", "median_hhi_3mi", "market_rent_psf",
                    "competitive_supply_sf_3mi", "pipeline_supply_sf_3mi",
                    "in_place_avg_rent_psf", "t3_annualized_revenue"]

# Gate-7 analyst resolution: the auto top-50 substring match can't see
# "strong secondary market" (a criteria-sanctioned pass) — the analyst
# records the verification here and the gate resolves on it.
MARKET_VERIFICATION_CHOICES = [
    ("", "Unverified"),
    ("top_50", "Top-50 MSA (verified)"),
    ("strong_secondary", "Strong secondary market"),
    ("neither", "Neither — fails gate"),
]

STREET_RATE_TREND_CHOICES = [
    ("", "Unknown"),
    ("rising", "Rising"),
    ("flat", "Flat"),
    ("falling", "Falling"),
]
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
    ("market_verification", "Market Verification (Gate 7)"),
]
SECTION_SIZE = [
    ("nrsf", "NRSF"), ("total_units", "Total Units"), ("cc_pct", "CC (%)"),
    ("ss_driveup_sf", "SS Drive-Up SF"), ("ss_enclosed_sf", "SS Enclosed SF"),
    ("brv_enclosed_sf", "BRV Enclosed SF"), ("brv_covered_sf", "BRV Covered SF"),
    ("brv_open_sf", "BRV Open Parking SF"),
]
SECTION_INCOME = [
    ("ttm_gpr", "Gross Potential Rent ($)"), ("other_income", "Other Income ($)"),
    ("ttm_egr", "Effective Gross Revenue ($)"), ("ttm_total_revenue", "Total Revenue ($)"),
    ("ttm_total_expenses", "Total Expenses ($)"), ("cim_yr1_noi", "CIM Year 1 NOI ($)"),
    ("ttm_noi", "TTM NOI ($)"), ("mgmt_fee_pct", "Mgmt Fee (% EGR)"),
]
SECTION_DEMOGRAPHICS = [
    ("population_1mi", "Population 1-mi"), ("population_3mi", "Population 3-mi"),
    ("population_5mi", "Population 5-mi"), ("median_hhi_3mi", "Median HHI 3-mi ($)"),
]
# Dense-model-view drivers: the fields that actually move the screen/IRR
# outcome, surfaced as their own vertical block (model_rows()) right after
# Property so the analyst sees them before anything else. Supersedes their
# Task-1 temporary homes in SECTION_INCOME/SECTION_SIZE/SECTION_DEMOGRAPHICS
# — each field lives in exactly one section now.
SECTION_DRIVERS = [
    ("asking_price", "Asking Price ($)"), ("capex_estimate", "CapEx Estimate ($)"),
    ("physical_occupancy", "Physical Occupancy (%)"),
    ("economic_occupancy", "Economic Occupancy (%)"),
    ("market_rent_psf", "Street Rate ($/SF/mo)"),
    ("in_place_avg_rent_psf", "In-Place Rent ($/SF/mo)"),
    ("street_rate_trend", "Street-Rate Trend"),
    ("t3_annualized_revenue", "T3 Annualized Revenue ($)"),
    # Excludes the subject property — matches the SF/capita gate's own
    # inputs (analysis.filters.sf_per_capita_inputs adds subject SF back
    # in separately). Kept short here to fit the dense row; the full
    # "excl. subject" caveat lives in this comment, not the label.
    ("competitive_supply_sf_3mi", "Competitive Supply SF (3-mi)"),
    ("pipeline_supply_sf_3mi", "Pipeline SF (3-mi)"),
]

INPUT_CSS = "w-full border border-slate-300 rounded px-2 py-1 text-sm"

# Every input-integrity check — including the Revenue − Expenses = NOI
# identity this module used to own outright — lives in analysis/checks.py so
# the form, the live preview, the engine, the memo and the Excel writer all
# read one registry. `noi_recon_tolerance` is re-exported above for the
# callers that already import it from here.


def _pct_decimal(v):
    """Whole-number form percent → decimal. Read-only: feeds the check
    register, never a field value (see the module docstring)."""
    return v / 100.0 if v is not None else None


def check_input_from_cleaned(cleaned, unit_mix=None) -> checks.CheckInput:
    """cleaned_data (form units) → the register's canonical units.

    The form can see the income statement, size and occupancy; it cannot see
    the analysis outputs (expense lines, scenarios, replacement cost), so
    those checks come back `skipped` here and run for real in the preview
    and the engine.
    """
    return checks.CheckInput(
        ttm_gpr=cleaned.get("ttm_gpr"),
        ttm_egr=cleaned.get("ttm_egr"),
        ttm_total_revenue=cleaned.get("ttm_total_revenue"),
        ttm_total_expenses=cleaned.get("ttm_total_expenses"),
        ttm_noi=cleaned.get("ttm_noi"),
        nrsf=cleaned.get("nrsf"),
        unit_mix=tuple(unit_mix or ()),
        physical_occupancy=_pct_decimal(cleaned.get("physical_occupancy")),
        economic_occupancy=_pct_decimal(cleaned.get("economic_occupancy")),
    )


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
        from registry import EXPENSE_KEYS
        for key in EXPENSE_KEYS:
            self.fields[f"exp_{key}"] = forms.FloatField(
                required=False, min_value=0, widget=_num())
        self.fields["solver_target_irr"] = forms.FloatField(
            required=False, min_value=0, widget=_num())
        # Declared in CIM_CHAR_FIELDS for the save/initial plumbing, but
        # rendered as a constrained dropdown, not free text.
        self.fields["market_verification"] = forms.ChoiceField(
            required=False, choices=MARKET_VERIFICATION_CHOICES,
            widget=forms.Select(attrs={"class": INPUT_CSS}))
        self.fields["street_rate_trend"] = forms.ChoiceField(
            required=False, choices=STREET_RATE_TREND_CHOICES,
            widget=forms.Select(attrs={"class": INPUT_CSS}))
        self.fields["accept_noi_discrepancy"] = forms.BooleanField(
            required=False,
            widget=forms.CheckboxInput(
                attrs={"class": "rounded border-slate-300"}))
        # Set by clean(): tells the template to reveal the accept control.
        # Named for the identity check it was introduced for; it now reveals
        # the same single control for ANY blocking finding, and
        # blocking_findings says which ones are being accepted.
        self.show_noi_accept = False
        self.blocking_findings = []
        self.check_results = []

    def clean_state(self):
        return (self.cleaned_data.get("state") or "").upper()

    def _derive_income_triple(self, cleaned):
        """Exactly two of Revenue / Expenses / NOI present → derive the
        third. Derivation, not checking: it runs BEFORE the register so a
        derived NOI satisfies the identity instead of tripping it."""
        rev = cleaned.get("ttm_total_revenue")
        exp = cleaned.get("ttm_total_expenses")
        noi = cleaned.get("ttm_noi")
        if sum(v is not None for v in (rev, exp, noi)) != 2:
            return
        if rev is None:
            cleaned["ttm_total_revenue"] = round(noi + exp, 2)
        elif exp is None:
            derived = round(rev - noi, 2)
            if derived < 0:
                raise forms.ValidationError(
                    f"TTM NOI ${noi:,.0f} exceeds Total Revenue "
                    f"${rev:,.0f} — expenses would be negative. "
                    f"Check the two entered values.")
            cleaned["ttm_total_expenses"] = derived
        else:
            cleaned["ttm_noi"] = round(rev - exp, 2)

    def clean(self):
        """Run the model error-check register over the submitted values.

        The form round-trips the merged CIM-snapshot + override values, so
        the register sees the exact numbers the analysis will use. Blocking
        findings invalidate the form unless the analyst ticks the accept
        control, which records every accepted finding via build_overrides.
        Advisory findings are carried on `check_results` for display and
        never block.
        """
        cleaned = super().clean()
        self._derive_income_triple(cleaned)
        # parse_unit_mix needs getlist; self.data is a QueryDict for every
        # real POST but a plain dict when a form is constructed directly.
        # Without a mix the two unit-mix checks report `skipped`, which is
        # the honest answer — they are advisory either way.
        mix = parse_unit_mix(self.data) if hasattr(self.data, "getlist") else None
        self.check_results = checks.run_checks(
            check_input_from_cleaned(cleaned, mix))
        self.blocking_findings = checks.blocking_failures(self.check_results)
        if self.blocking_findings and not cleaned.get("accept_noi_discrepancy"):
            self.show_noi_accept = True
            raise forms.ValidationError(
                [r.message for r in self.blocking_findings]
                + ["Fix the inputs, or tick “Accept the flagged "
                   "discrepancies” to proceed with them recorded."])
        return cleaned


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
    for key, val in (saved.get("expense_line_overrides") or {}).items():
        initial[f"exp_{key}"] = val
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


def _display_value(v):
    """Comma-grouped, trailing-zero-trimmed string for a raw snapshot
    value (model_rows' read-only 'extracted' column); non-numeric values
    (e.g. street_rate_trend's "rising") pass through unchanged."""
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return v
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def model_rows(form, pairs, snapshot, source_log=None):
    """Vertical driver rows: label | extracted (read-only) | input.
    source: 'you' when the bound/initial value differs from snapshot;
    'Census' when the snapshot value was tier-2 enrichment (extract-time
    enrichment runs BEFORE the snapshot is saved, so Census fills live
    inside cim_json — the run payload's enrichment.source_log is the
    only way to tell them from CIM-extracted values).

    Percent fields (physical_occupancy etc.) are stored as decimals
    (0.92) in the snapshot but displayed/submitted as whole numbers (92)
    everywhere else on this page (build_initial/build_overrides) — snap
    is converted the same way BEFORE comparing/displaying, or every
    percent driver would show a decimal next to its whole-number input
    and read as "you edited this" on every load.
    """
    source_log = source_log or {}
    rows = []
    for name, label in pairs:
        snap = snapshot.get(name)
        if name in CIM_PCT_FIELDS and snap is not None:
            snap = _pct_display(snap)
        bf = form[name]
        cur = bf.value()
        if cur not in (None, "", snap):
            src = "you"
        elif snap is not None:
            src = ("Census" if source_log.get(name, {}).get("tier") == 2
                   else "CIM")
        else:
            src = ""
        rows.append({"label": label, "bf": bf,
                     "extracted": _display_value(snap), "source": src})
    return rows


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
            if not (math.isfinite(sf) and math.isfinite(rate)):
                # "inf"/"nan"/"1e400"-style values float-parse cleanly (no
                # ValueError) but would propagate inf/nan into NOI math
                # downstream — treat them as invalid the same way a
                # non-numeric string is.
                raise ValueError("non-finite sf/rate")
        except (ValueError, OverflowError):
            # OverflowError: int(float("inf")) — a Count value that
            # float-parses to infinity ("inf"/"Infinity"/"1e400") raises
            # OverflowError, not ValueError, on the int() conversion.
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
    # Bind a verification to the location it certified — gate 7 treats a
    # verification recorded for a different msa/city as stale, so a later
    # location edit can't inherit a pass it never earned. The pristine PDF
    # snapshot never contains market_verification, so the bound form's
    # prefilled value rides along on EVERY save — compare against the
    # previously SAVED override, not the snapshot: re-stamp only when the
    # analyst actually changed the verification; otherwise carry the old
    # stamp forward so an msa-only edit is detected as stale (adversary
    # re-review finding).
    prev = (deal.assumption_overrides or {}).get("cim_overrides", {})
    if cim_o.get("market_verification"):
        if cim_o["market_verification"] != prev.get("market_verification"):
            cim_o["market_verified_location"] = (
                cleaned.get("msa") or cleaned.get("city") or "")
        elif prev.get("market_verified_location") is not None:
            cim_o["market_verified_location"] = prev["market_verified_location"]
    if cim_o:
        out["cim_overrides"] = cim_o

    # Audit trail for an analyst-accepted Revenue−Expenses≠NOI mismatch:
    # recorded only when the acceptance actually mattered, so the run's
    # applied_overrides carries the discrepancy forever.
    rev, exp, noi = (cleaned.get("ttm_total_revenue"),
                     cleaned.get("ttm_total_expenses"),
                     cleaned.get("ttm_noi"))
    if cleaned.get("accept_noi_discrepancy") and None not in (rev, exp, noi):
        delta = round(rev - exp - noi, 2)
        if abs(delta) > noi_recon_tolerance(rev):
            out["noi_reconciliation"] = {"accepted": True, "delta": delta}

    # The accept control now covers every blocking finding, not just the
    # identity delta above, so the run's applied_overrides records WHICH
    # integrity findings were waived and in what words. Recomputed here
    # rather than read off the form for the same reason the delta above is:
    # build_overrides is called with cleaned_data, not with a form.
    if cleaned.get("accept_noi_discrepancy"):
        accepted = checks.blocking_failures(checks.run_checks(
            check_input_from_cleaned(
                cleaned,
                parse_unit_mix(post) if hasattr(post, "getlist") else None)))
        if accepted:
            out["accepted_checks"] = [{"id": r.id, "message": r.message}
                                      for r in accepted]

    from registry import EXPENSE_KEYS
    exp_o = {k: cleaned[f"exp_{k}"] for k in EXPENSE_KEYS
             if cleaned.get(f"exp_{k}") is not None}
    if exp_o:
        out["expense_line_overrides"] = exp_o

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

GATES_INT_KEYS = {"population_3mi", "unproven_vintage_year",
                  "max_sf_per_capita"}
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
