"""Assumptions editor — field definitions, initial building, delta saving.

Percent convention: templates and form data hold WHOLE numbers (type 6
for 6%); snapshots, config defaults, and stored overrides hold decimals.
Conversion happens ONLY in build_initial (×100), build_overrides (÷100)
and check_input_from_cleaned (÷100, read-only) — never in custom form
fields, so bound redisplay round-trips the raw submitted strings
untouched.
"""
import dataclasses
import logging
import math

from django import forms
from django.utils import timezone

import config as cfg
from analysis import checks
from analysis.checks import noi_recon_tolerance      # noqa: F401 (re-export)
from model import waterfall as wf_mod
from model.debt import resolve_debt_terms
from model.returns_model import (BASIS_AMOUNT, BASIS_LABELS, BASIS_PCT_PRICE,
                                 BASIS_PER_SF, BASIS_PER_UNIT, CAPEX_BASES,
                                 RESERVE_BASES)
from model.waterfall import resolve_waterfall_terms
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
]
SCENARIO_PARAMS = [p for p, _ in SCENARIO_PARAM_LABELS]
VA_PARAM_LABELS = [
    ("target_occupancy", "Target Occ (%)"),
    ("months_to_stabilize", "Months to Stabilize"),
    ("rent_growth_to_market", "Rent Growth to Mkt (%)"),
    ("post_stabilize_rev_growth", "Post-Stab Rev Growth (%)"),
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

# Timing & transaction costs (item B). Both percentages are per-deal
# because transfer-tax states can multiply the acquisition side, and
# broker fees move with deal size — they are defaults, not constants.
TXN_COST_LABELS = [
    ("acquisition_closing_pct", "Acquisition (% price)"),
    ("disposition_cost_pct", "Disposition (% sale)"),
]
TXN_COST_PARAMS = [p for p, _ in TXN_COST_LABELS]

# Capital structure (item D + H). The reserve and GP co-invest are new
# inputs; capex_basis re-reads the EXISTING CapEx box as a rate. Stored
# together under `capital_structure` in the deal's overrides, deltas only
# like every other section.
CAPITAL_KEYS = ("capex_basis", "operating_reserve",
                "operating_reserve_basis", "gp_coinvest_pct")
CAPITAL_DEFAULTS = {
    "capex_basis": lambda: cfg.DEFAULT_CAPEX_BASIS,
    "operating_reserve": lambda: cfg.DEFAULT_OPERATING_RESERVE,
    "operating_reserve_basis": lambda: cfg.DEFAULT_OPERATING_RESERVE_BASIS,
    "gp_coinvest_pct": lambda: cfg.GP_COINVEST_PCT,
}
#: basis → (the cleaned_data field that must be present, its label). A
#: rate with no denominator resolves to $0 downstream by design, so the
#: form refuses the combination while a human is standing in front of it.
BASIS_DRIVER_FIELDS = {
    BASIS_PER_SF: ("nrsf", "NRSF"),
    BASIS_PER_UNIT: ("total_units", "Total Units"),
    BASIS_PCT_PRICE: ("asking_price", "Asking Price"),
}

#: basis field → (the hidden stamp naming the unit the number on screen was
#: RENDERED under, the amount field, its label). Changing a basis selector
#: does not change the number sitting beside it, and that number then means
#: something else entirely: a genuine "2" under "% of price" becomes $2 of
#: CapEx under "$ total", which silently removes real capital from the
#: basis and overstates every return. There is no JavaScript on this page
#: to re-key the field, so the save is refused once, which forces the
#: analyst to read the number under its new unit. The template renders each
#: stamp from the CURRENTLY SELECTED basis, so the second save proceeds.
#: This is a confirmation, not a detector: the live preview swaps only the
#: model strip, so the stamp in the DOM names the basis the PAGE was drawn
#: with whether or not the analyst restated the figure.
BASIS_UNIT_STAMPS = {
    "capex_basis": ("capex_unit_stamp", "capex_estimate", "CapEx"),
    "operating_reserve_basis": ("reserve_unit_stamp", "operating_reserve",
                                "the operating reserve"),
}

# Debt, waterfall and the AM fee (item E3b) — the inputs behind the
# levered lens, which item E3a computes on every deal at config defaults.
# Stored as `debt_terms` / `waterfall_terms` deltas and a top-level
# `am_fee_pct`, the three override keys webapp.services already reads.
#
# NOT settings-page editable, and not `_PATCHED_DICTS` entries: same lane
# and same reason as the capital block above — a patched dict is mutated
# in place for one deal's run, so anything resolving it outside that lock
# reads another deal's terms.
#
# Three debt keys are deliberately absent. `loan_type` has one execution.
# `index_rate`/`spread` are a MODE, not two more boxes:
# `resolve_debt_terms` only clears the seeded fixed rate when a floating
# half arrives WITHOUT an explicit `rate`, and this form prefills `rate`
# from the resolved terms — so a naive floating pair would post a fixed
# rate beside them on every save, hit the "both named, fixed wins"
# branch, and silently ignore what was typed. That needs a fixed/floating
# selector and there is no JavaScript on this page. Config is bank
# fixed-rate paper; floating stays override-only. Those keys are CARRIED
# FORWARD on save (see build_overrides) so an unrelated edit here cannot
# quietly convert a floating deal to the config fixed rate.
DEBT_FORM_LABELS = [
    ("rate", "Interest Rate (%)"),
    ("amort_years", "Amortization (yrs)"),
    ("io_months", "Interest-Only (mos)"),
    ("term_years", "Loan Term (yrs)"),
    ("max_ltv", "Max LTV (%)"),
    ("min_dscr", "Min DSCR (x)"),
    ("min_debt_yield", "Min Debt Yield (%)"),
    ("orig_fee_pct", "Origination Fee (%)"),
    ("exit_fee_pct", "Exit Fee (%)"),
]
DEBT_FORM_KEYS = [k for k, _ in DEBT_FORM_LABELS]
#: Decimal fractions in the model, whole numbers on this page — the same
#: convention as every other percentage here. `min_dscr` is absent on
#: purpose: it is a coverage RATIO (1.25x), which is why `DebtTerms`
#: exempts it from its own >1.0 guard.
DEBT_PCT_KEYS = {"rate", "max_ltv", "min_debt_yield", "orig_fee_pct",
                 "exit_fee_pct"}
DEBT_INT_KEYS = {"amort_years", "io_months", "term_years"}

# Waterfall. `accrual_base`, `am_fee_treatment` and `catch_up` are
# deliberately absent: each has exactly ONE implemented value and
# `WaterfallTerms.__post_init__` raises on the other, so a dropdown whose
# second option crashes the run is a trap, not a setting. They stay in the
# assumption stamp, which is where an open LPA question belongs. Carried
# forward on save for the same reason the debt keys above are.
WF_FORM_LABELS = [
    ("pref_rate", "Preferred Return (%)"),
    ("promote_split", "GP Promote (%)"),
    ("pref_compounding", "Pref Compounding"),
    ("ordering", "Distribution Order"),
]
WF_FORM_KEYS = [k for k, _ in WF_FORM_LABELS]
WF_PCT_KEYS = {"pref_rate", "promote_split"}

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
    # No "($)" on CapEx: its basis selector rides in the same row, so the
    # unit is whatever that selector says (item H).
    ("asking_price", "Asking Price ($)"), ("capex_estimate", "CapEx Estimate"),
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


def submitted_debt_terms(cleaned) -> dict:
    """Debt fields → a partial `debt_terms` override in MODEL units.

    The ONE place the whole-number-percent → decimal conversion happens
    for the debt block, so `clean()` validates exactly the dict
    `build_overrides` will store. Two copies of this conversion is how a
    form validates 6.5 as 6.5% and then saves it as 650%.
    """
    out = {}
    for key in DEBT_FORM_KEYS:
        v = cleaned.get(f"debt_{key}")
        if v in (None, ""):
            continue
        if key in DEBT_PCT_KEYS:
            out[key] = round(float(v) / 100.0, 6)
        elif key in DEBT_INT_KEYS:
            out[key] = int(v)
        else:
            out[key] = round(float(v), 6)
    return out


def submitted_waterfall_terms(cleaned) -> dict:
    """Waterfall fields → a partial `waterfall_terms` override. The two
    selectors carry model tokens as their values, so they pass through."""
    out = {}
    for key in WF_FORM_KEYS:
        v = cleaned.get(f"wf_{key}")
        if v in (None, ""):
            continue
        out[key] = round(float(v) / 100.0, 6) if key in WF_PCT_KEYS else v
    return out


def submitted_am_fee_pct(cleaned):
    v = cleaned.get("am_fee_pct")
    return None if v in (None, "") else round(float(v) / 100.0, 6)


def _text():
    return forms.TextInput(attrs={"class": INPUT_CSS})


def _num(minimum="0"):
    return forms.NumberInput(attrs={"class": INPUT_CSS, "step": "any",
                                    "min": minimum})


def _select():
    return forms.Select(attrs={"class": INPUT_CSS})


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
        # Pro-forma management fee, % of EGR. Sits with the expense
        # benchmarks it modifies rather than in the timing block: it is
        # the fee the model underwrites TO when the CIM understates or
        # omits one. `max_value=100` is the field bound; the semantic
        # bound is the resolver's, and 0 is allowed on purpose (a
        # self-managed property underwritten with no third-party fee).
        self.fields["mgmt_fee_target_pct"] = forms.FloatField(
            required=False, min_value=0, max_value=100, widget=_num())
        # The market cap this asset's class and age band trade at. Blank
        # means "use the config table cell". Exit cap is DERIVED from this
        # (market + scenario spread + obsolescence drift x hold), which is
        # why there is no longer an exit-cap column in the scenario grid.
        # One unit, so no basis selector and no BASIS_UNIT_STAMPS entry.
        self.fields["market_cap_rate"] = forms.FloatField(
            required=False, min_value=0, max_value=100, widget=_num())
        # Timing & round-trip costs. Percent fields follow the module's
        # whole-number convention; hold_years is a plain integer.
        self.fields["hold_years"] = forms.IntegerField(
            required=False, min_value=cfg.HOLD_YEARS_RANGE[0],
            max_value=cfg.HOLD_YEARS_RANGE[1], widget=_num())
        for name in TXN_COST_PARAMS:
            self.fields[name] = forms.FloatField(
                required=False, min_value=0, max_value=100, widget=_num())
        # Capital structure. gp_coinvest_pct follows the whole-number
        # percent convention; operating_reserve is dollars or $/NRSF
        # depending on its basis, so it carries no percent conversion.
        self.fields["operating_reserve"] = forms.FloatField(
            required=False, min_value=0, widget=_num())
        self.fields["gp_coinvest_pct"] = forms.FloatField(
            required=False, min_value=0, max_value=100, widget=_num())
        # Rides in the CapEx driver row itself, with no visible label of
        # its own — the row label is "CapEx Estimate" and this says in
        # what unit — so it carries an explicit accessible name.
        self.fields["capex_basis"] = forms.ChoiceField(
            required=False, choices=[(b, BASIS_LABELS[b]) for b in CAPEX_BASES],
            widget=forms.Select(attrs={"class": INPUT_CSS,
                                       "aria-label": "CapEx basis",
                                       "title": "How the CapEx figure is read"}))
        self.fields["operating_reserve_basis"] = forms.ChoiceField(
            required=False,
            choices=[(b, BASIS_LABELS[b]) for b in RESERVE_BASES],
            widget=forms.Select(attrs={"class": INPUT_CSS}))
        # Debt, waterfall and the AM fee (item E3b). `max_value=100` on the
        # percent fields keeps a 6.5-means-6.5% typo out of a decimal
        # field, and amortization and term cannot be zero, which would
        # otherwise size a loan off a 1200%/yr constant.
        #
        # These bounds do NOT catch everything, and an earlier draft of
        # this comment claimed they did (review finding). `DebtTerms`
        # rejects a decimal `> 1.0`, so 100 here is fine; `WaterfallTerms`
        # requires `pref_rate` and `promote_split` STRICTLY below 1.0,
        # because a 100% promote hands the GP the whole residual. So
        # `wf_promote_split=100` is in bounds, converts to exactly 1.0 and
        # raises — which is precisely why `_validate_levered_terms` calls
        # the real resolvers instead of trusting this list. Two guards
        # with slightly different edges is exactly the drift that makes
        # re-listing a dataclass's rules in a form a bad idea.
        for key in DEBT_FORM_KEYS:
            if key in DEBT_INT_KEYS:
                self.fields[f"debt_{key}"] = forms.IntegerField(
                    required=False, min_value=0 if key == "io_months" else 1,
                    widget=_num("0" if key == "io_months" else "1"))
            else:
                self.fields[f"debt_{key}"] = forms.FloatField(
                    required=False, min_value=0,
                    max_value=100 if key in DEBT_PCT_KEYS else None,
                    widget=_num())
        for key in WF_FORM_KEYS:
            if key in WF_PCT_KEYS:
                self.fields[f"wf_{key}"] = forms.FloatField(
                    required=False, min_value=0, max_value=100, widget=_num())
        self.fields["wf_pref_compounding"] = forms.ChoiceField(
            required=False, widget=_select(),
            choices=[(k, v) for k, v in wf_mod.COMPOUNDING_LABELS.items()])
        self.fields["wf_ordering"] = forms.ChoiceField(
            required=False, widget=_select(),
            choices=[(k, v) for k, v in wf_mod.ORDERING_LABELS.items()])
        self.fields["am_fee_pct"] = forms.FloatField(
            required=False, min_value=0, max_value=100, widget=_num())
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

    def _basis_error(self, message: str):
        """Record a basis problem WITHOUT detaching the basis from
        cleaned_data.

        `add_error(field, ...)` deletes that field from `cleaned_data` —
        Django's documented behavior. The live preview is what gets hurt:
        `assumptions_preview` proceeds on an invalid form BY DESIGN (it
        shows a state rather than blocking) and reads `cleaned_data`
        straight through `build_overrides`, whose basis lookup then falls
        back to the config default. Flagging a basis would therefore
        silently revert the preview to the OLD basis, during exactly the
        change-the-basis interaction these two checks exist to guard
        (re-review finding). A non-field error is just as visible — the
        page renders `{{ form.errors }}` whole — and detaches nothing.
        """
        self.add_error(None, forms.ValidationError(message))

    def _validate_capital_bases(self, cleaned):
        """A rate needs its denominator.

        `model.returns_model.resolve_capital_amount` deliberately returns
        $0 for `$0.50/SF` on a deal with no NRSF rather than inventing a
        number of the wrong magnitude — correct for a stored override
        arriving from the CLI, useless as feedback to the person typing
        it. Caught here instead, where the missing field is on screen.
        """
        for basis_field, (stamp_field, amount_field,
                          label) in BASIS_UNIT_STAMPS.items():
            basis = cleaned.get(basis_field)
            if basis not in BASIS_DRIVER_FIELDS or not cleaned.get(amount_field):
                continue
            driver_field, driver_label = BASIS_DRIVER_FIELDS[basis]
            if not cleaned.get(driver_field):
                self._basis_error(
                    f"{label.capitalize()} is entered as "
                    f"{BASIS_LABELS[basis]}, but {driver_label} is blank — "
                    f"there is nothing to multiply by. Enter {driver_label}, "
                    f"or switch the basis back to "
                    f"{BASIS_LABELS[BASIS_AMOUNT]}.")

    def _confirm_changed_units(self, cleaned):
        """Refuse the first save after a unit change, so the number gets
        read once under its new unit.

        The stamp cannot tell whether the analyst restated the figure —
        the preview swaps only the model strip, so the stamp in the DOM
        still names the basis the page was DRAWN with either way. So this
        does not claim they forgot; it makes the change cost one
        confirmation. The re-render stamps the new selection, so the
        second save proceeds.

        Read off `self.data` rather than declared as a field because it is
        a property of the render, not of the model: a form built directly
        in a test carries no stamp and behaves exactly as it did before
        this existed.
        """
        for basis_field, (stamp_field, amount_field,
                          label) in BASIS_UNIT_STAMPS.items():
            stamp = (self.data or {}).get(stamp_field)
            basis = cleaned.get(basis_field)
            if not stamp or not basis or stamp == basis:
                continue
            self._basis_error(
                f"The unit for {label} changed from "
                f"{BASIS_LABELS.get(stamp, stamp)} to "
                f"{BASIS_LABELS.get(basis, basis)}. The figure beside it "
                f"will now be read as {BASIS_LABELS.get(basis, basis)} — "
                f"check it is stated in that unit, then save again to "
                f"confirm.")

    def _validate_levered_terms(self, cleaned):
        """Validate by calling the REAL resolvers, not by re-listing them.

        Field bounds keep the individual typos out; they do not prove the
        submitted set resolves. `DebtTerms` and `WaterfallTerms` own that
        judgment and already state it in one place, so re-listing their
        rules here is exactly the duplicated-constant divergence this repo
        has a rule against — and it would go stale the first time a guard
        is added. Running the resolver cannot drift from itself.

        Non-field errors, for the reason `_basis_error` documents:
        `add_error(field, ...)` detaches the field from `cleaned_data`,
        which the live preview reads on an invalid form by design.

        `NotImplementedError` is caught alongside `ValueError` because
        `WaterfallTerms` splits its refusals between the two: a bad NUMBER
        raises ValueError, an unimplemented CONVENTION raises
        NotImplementedError. Catching only the first turns the second into
        a 500.
        """
        for label, resolve, submitted in (
                ("Debt terms", resolve_debt_terms,
                 submitted_debt_terms(cleaned)),
                ("Waterfall terms", resolve_waterfall_terms,
                 submitted_waterfall_terms(cleaned))):
            try:
                resolve(submitted)
            except (ValueError, NotImplementedError) as exc:
                self._basis_error(f"{label}: {exc}")

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
        self._validate_capital_bases(cleaned)
        self._confirm_changed_units(cleaned)
        self._validate_levered_terms(cleaned)
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
    # From `cfg`, not `eff`: this one is per-deal only, so there is no
    # ConfigOverride lane for it and no effective_config entry to read.
    initial["mgmt_fee_target_pct"] = _pct_display(
        saved.get("mgmt_fee_target_pct", cfg.MGMT_FEE_TARGET_PCT))
    # Blank unless the analyst set one: the placeholder is the table cell
    # for this deal's class and age band, which build_initial cannot know
    # (it has no cim_data). Seeding a number here would look like a
    # confirmed market read when nobody confirmed anything.
    mc_saved = saved.get("market_cap_rate")
    initial["market_cap_rate"] = (_pct_display(mc_saved)
                                  if mc_saved is not None else None)
    initial["hold_years"] = saved.get("hold_years", cfg.DEFAULT_HOLD_YEARS)
    txn_saved = saved.get("transaction_costs", {})
    for name in TXN_COST_PARAMS:
        initial[name] = _pct_display(
            txn_saved.get(name, eff["TRANSACTION_COSTS"][name]))
    cap_saved = saved.get("capital_structure", {})
    for key in CAPITAL_KEYS:
        initial[key] = cap_saved.get(key, CAPITAL_DEFAULTS[key]())
    initial["gp_coinvest_pct"] = _pct_display(initial["gp_coinvest_pct"])
    # CapEx entered as a percentage is stored as a decimal like every
    # other percent in this app, so it redisplays as a whole number. The
    # conversion is keyed off the SAVED basis, not the posted one, so a
    # value and the basis it was entered under can never be read apart.
    if initial["capex_basis"] == BASIS_PCT_PRICE:
        initial["capex_estimate"] = _pct_display(initial.get("capex_estimate"))
    _debt_waterfall_initial(initial, saved)
    for key, val in (saved.get("expense_line_overrides") or {}).items():
        initial[f"exp_{key}"] = val
    return initial


def _debt_waterfall_initial(initial: dict, saved: dict) -> None:
    """Prefill the levered block from the RESOLVED terms (item E3b).

    Resolved, not `{**config, **saved}`: `resolve_debt_terms` clears the
    seeded fixed rate for a floating-rate override, so a floating deal
    shows a BLANK rate here. That is what lets the round trip work — a
    blank rate posts no `rate`, `build_overrides` carries `index_rate` /
    `spread` forward, and the deal stays floating. Merging config in
    directly would prefill 6.25%, post it, and the resolver's "both named,
    fixed wins" branch would convert the deal to fixed paper on the first
    unrelated save.

    An unresolvable stored override (only reachable by writing
    `assumption_overrides` outside this form) falls back to the plain
    merge so the page still renders. Nothing is swallowed: the RUN
    resolves the same dict unguarded in `webapp.services`, so the failure
    still surfaces, at the one place where it changes an answer.

    BOTH exception types, not just `ValueError` (review finding). The
    three waterfall conventions this form deliberately does not expose —
    `accrual_base`, `am_fee_treatment`, `catch_up` — are exactly the ones
    that raise `NotImplementedError`, and they are exactly the ones that
    reach here by being CARRIED FORWARD from a stored override. Catching
    only ValueError made the fallback above a lie for the single case
    most likely to hit it: the assumptions page 500ed instead.
    """
    debt_saved = saved.get("debt_terms") or {}
    wf_saved = saved.get("waterfall_terms") or {}
    try:
        debt = dataclasses.asdict(resolve_debt_terms(debt_saved))
    except (ValueError, NotImplementedError):
        logger.warning("stored debt_terms override does not resolve — "
                       "prefilling the assumptions form from the raw merge")
        debt = {**cfg.DEBT_TERMS, **debt_saved}
    try:
        wf = dataclasses.asdict(resolve_waterfall_terms(wf_saved))
    except (ValueError, NotImplementedError):
        logger.warning("stored waterfall_terms override does not resolve — "
                       "prefilling the assumptions form from the raw merge")
        wf = {**cfg.WATERFALL_TERMS, **wf_saved}
    for key in DEBT_FORM_KEYS:
        v = debt.get(key)
        initial[f"debt_{key}"] = _pct_display(v) if key in DEBT_PCT_KEYS else v
    for key in WF_FORM_KEYS:
        v = wf.get(key)
        initial[f"wf_{key}"] = _pct_display(v) if key in WF_PCT_KEYS else v
    am = saved.get("am_fee_pct")
    initial["am_fee_pct"] = _pct_display(cfg.AM_FEE_PCT if am in (None, "")
                                         else am)


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


def model_rows(form, pairs, snapshot, source_log=None, extras=None,
               ai_filled=None):
    """Vertical driver rows: label | extracted (read-only) | input [| extra].
    source: 'you' when the bound/initial value differs from snapshot;
    'AI' when the snapshot value was supplied by the B1 gap-filler (item B1);
    'Census' when the snapshot value was tier-2 enrichment (extract-time
    enrichment runs BEFORE the snapshot is saved, so Census fills live
    inside cim_json — the run payload's enrichment.source_log is the
    only way to tell them from CIM-extracted values).

    `ai_filled` is the set of field names the AI gap-filler wrote; those
    read 'AI' rather than 'CIM' until the analyst edits them, so an
    LLM-supplied number is never mistaken for one the CIM stated.

    Percent fields (physical_occupancy etc.) are stored as decimals
    (0.92) in the snapshot but displayed/submitted as whole numbers (92)
    everywhere else on this page (build_initial/build_overrides) — snap
    is converted the same way BEFORE comparing/displaying, or every
    percent driver would show a decimal next to its whole-number input
    and read as "you edited this" on every load.

    `extras` attaches a second widget to a row (field name → bound
    field) — the CapEx basis selector, which has to sit beside the number
    it reinterprets rather than in a settings block three sections away.
    A row whose basis is anything but `amount` shows no extracted value:
    the snapshot holds the CIM's DOLLAR figure and the input now holds a
    rate, so printing them side by side invites a comparison between two
    different units.
    """
    source_log = source_log or {}
    extras = extras or {}
    ai_filled = ai_filled or set()
    rows = []
    for name, label in pairs:
        snap = snapshot.get(name)
        if name in CIM_PCT_FIELDS and snap is not None:
            snap = _pct_display(snap)
        extra_bf = extras.get(name)
        if extra_bf is not None and extra_bf.value() not in (None, "",
                                                             BASIS_AMOUNT):
            snap = None
        bf = form[name]
        cur = bf.value()
        if cur not in (None, "", snap):
            src = "you"
        elif snap is not None:
            if name in ai_filled:
                src = "AI"
            elif source_log.get(name, {}).get("tier") == 2:
                src = "Census"
            else:
                src = "CIM"
        else:
            src = ""
        rows.append({"label": label, "bf": bf, "extra_bf": extra_bf,
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

    capex_basis = cleaned.get("capex_basis") or cfg.DEFAULT_CAPEX_BASIS

    cim_o = {}
    for name in CIM_SCALAR_FIELDS:
        v = cleaned.get(name)
        if v in (None, ""):
            continue
        if name in CIM_PCT_FIELDS:
            v = round(v / 100.0, 6)
        # The one field whose units depend on another field. Canonical
        # storage is a decimal fraction, matching every other percent
        # here; model.returns_model.resolve_capital_amount reads it that
        # way. See build_initial for the inverse.
        elif name == "capex_estimate" and capex_basis == BASIS_PCT_PRICE:
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

    mgmt = cleaned.get("mgmt_fee_target_pct")
    if mgmt is not None:
        mgmt = round(mgmt / 100.0, 6)
        if not _same(mgmt, cfg.MGMT_FEE_TARGET_PCT):
            out["mgmt_fee_target_pct"] = mgmt

    # No default to diff against — the table cell depends on the asset, so
    # any entered figure is by definition an override.
    mc = cleaned.get("market_cap_rate")
    if mc is not None:
        out["market_cap_rate"] = round(mc / 100.0, 6)

    hold = cleaned.get("hold_years")
    if hold is not None and int(hold) != cfg.DEFAULT_HOLD_YEARS:
        out["hold_years"] = int(hold)

    txn = {}
    for name in TXN_COST_PARAMS:
        v = cleaned.get(name)
        if v is None:
            continue
        v = round(v / 100.0, 6)
        if not _same(v, eff["TRANSACTION_COSTS"][name]):
            txn[name] = v
    if txn:
        out["transaction_costs"] = txn

    cap = {}
    for key in CAPITAL_KEYS:
        v = cleaned.get(key)
        if v in (None, ""):
            continue
        if key == "gp_coinvest_pct":
            v = round(v / 100.0, 6)
        if not _same(v, CAPITAL_DEFAULTS[key]()):
            cap[key] = v
    if cap:
        out["capital_structure"] = cap

    # Debt and waterfall (item E3b). Deltas against config like every
    # other section, but MERGED ONTO the keys this form does not own —
    # `loan_type`, `index_rate`, `spread`, `accrual_base`,
    # `am_fee_treatment`, `catch_up`. Rebuilding the section purely from
    # the form would silently delete a CLI-set floating rate on the first
    # unrelated save here: the deal keeps running, at a different cost of
    # debt, with nothing anywhere saying so. Every capital-block key had a
    # field, so this could not arise before; six keys here have none.
    prev = deal.assumption_overrides or {}
    for out_key, form_keys, submitted, defaults in (
            ("debt_terms", DEBT_FORM_KEYS, submitted_debt_terms(cleaned),
             cfg.DEBT_TERMS),
            ("waterfall_terms", WF_FORM_KEYS,
             submitted_waterfall_terms(cleaned), cfg.WATERFALL_TERMS)):
        section = {k: v for k, v in (prev.get(out_key) or {}).items()
                   if k not in form_keys}
        for key, value in submitted.items():
            if not _same(value, defaults.get(key)):
                section[key] = value
        if section:
            out[out_key] = section

    am_fee = submitted_am_fee_pct(cleaned)
    if am_fee is not None and not _same(am_fee, cfg.AM_FEE_PCT):
        out["am_fee_pct"] = am_fee

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
# Shapes inside `config.VALUE_ADD_ASSUMPTIONS`, which is otherwise flat
# shares. Both ranges are (low, high) bands rendered into memo prose by
# `analysis.value_add._pct_band`; the tenure is a whole number of months.
VA_ASSUMPTION_RANGE_KEYS = {"ecri_increase_range", "ancillary_target_share"}
VA_ASSUMPTION_INT_KEYS = {"ecri_tenant_tenure_months"}

# ── Sanity bounds for the settings value box ────────────────────────
#
# The value field was an unbounded CharField: "-5" under Solver Target
# IRR parsed to -0.05, saved clean, and the bisection solver ran on it,
# because nothing between the text input and the solver had an opinion
# about what the number MEANT. Every key gets a (low, high) bound in
# CANONICAL units below; None is open on that side.
#
# These are DEFINITIONAL limits, not underwriting opinions: a share of
# something cannot exceed the whole, a vintage is a year, a count is not
# negative. The judgment quantities — the $/NRSF and $/SF benchmarks —
# stay OPEN above deliberately. Capping them at a round number would be
# re-underwriting through the validator, and no arithmetic in the model
# breaks on a large one; it is only the sign that is nonsense.
#
# What keeps this honest is NOT the table, which anyone can forget to
# extend. `test_every_default_sits_inside_its_own_bounds` reads config.py
# live and fails the moment a new key's own default falls outside the
# bound its shape earns it.
_SHARE = (0.0, 1.0)        # occupancies, ratios, non-negative rates
_GROWTH = (-1.0, 1.0)      # rates a bear case may legitimately run negative
_CAP_RATE = (0.0001, 1.0)  # zero is excluded, unlike every other rate here:
                           #   valuation.py:265 reads `exit_noi / exit_cap if
                           #   exit_cap > 0 else 0`, so a 0% cap does not raise
                           #   — it prints an exit value of zero and an IRR
                           #   computed from it.
_NON_NEG = (0.0, None)     # $/NRSF and $/SF benchmarks — see the note above
_COUNT = (0, None)         # population, SF per capita
_YEAR = (1900, 2100)

# Scenario parameters a bear case may legitimately run NEGATIVE — a
# shrinking-revenue underwrite is a real one, and _SHARE would refuse it.
# Everything else percentage-shaped is a share and cannot go below zero.
GROWTH_PARAM_KEYS = {"yr1_noi_bump", "rev_cagr_yr1_3", "rev_cagr_yr4_5",
                     "exp_growth", "post_stabilize_rev_growth",
                     "expense_growth"}


def _bounds_for(key: str, spec: dict) -> tuple:
    """The (low, high) a key's SHAPE earns it. Derived rather than
    enumerated, so a key added to config.py arrives bounded instead of
    waiting for someone to remember to list it here.

    The three special cases dispatch on the LEAF name, which is only safe
    while those leaves are unique across the registry-covered dicts —
    they are today (audited key by key). A future config key reusing one
    of them would silently inherit the wrong bound, and
    `test_every_default_sits_inside_its_own_bounds` only catches that if
    the wrong bound happens to exclude the new default. Prefix on the
    dotted key, not the leaf, if that risk ever becomes real."""
    leaf = key.split(".")[-1]
    if leaf == "unproven_vintage_year":
        return _YEAR
    if leaf in VA_NON_PCT:                       # months_to_stabilize
        # A stabilization finishing after the longest hold the app allows
        # is not a stabilization, so the ceiling is derived from config
        # rather than written here as 120. NOT for the usual freeze-at-
        # import reason — `HOLD_YEARS_RANGE` is in neither `_PATCHED_DICTS`
        # nor the override registry, so it cannot change at runtime and a
        # module constant would behave identically. The point is only that
        # a second copy of 120 could drift from the first.
        return (0, cfg.HOLD_YEARS_RANGE[1] * 12)
    if leaf in GROWTH_PARAM_KEYS:
        return _GROWTH
    if key.startswith("MARKET_CAP_RATES."):
        return _CAP_RATE
    if spec["int"]:
        return _COUNT
    return _SHARE if spec["pct"] else _NON_NEG


def bounds_display(spec: dict) -> str:
    """The bound in the units the box is typed in, for the error text."""
    lo, hi = spec["bounds"]

    def one(v):
        if spec["pct"]:
            return f"{round(float(v) * 100, 4):g}%"
        if spec["int"]:
            return f"{int(v)}"
        return f"{round(float(v), 4):g}"

    if lo is not None and hi is not None:
        return f"between {one(lo)} and {one(hi)}"
    if lo is not None:
        return f"at least {one(lo)}"
    return f"at most {one(hi)}"


def _within(spec: dict, values) -> bool:
    lo, hi = spec["bounds"]
    return all((lo is None or v >= lo) and (hi is None or v <= hi)
               for v in values)


def value_in_bounds(key: str, value, spec: dict = None) -> bool:
    """True if an ALREADY-STORED value still satisfies its bounds.

    Validation added at the form cannot reach rows saved before it
    existed, and such a row rendering as a plain number is the same
    silence this whole block is about.

    **PR #45 badged a failing row and still applied it; that is now
    reversed and the reversal is written here rather than deleted, so it
    stays visible.** The original reasoning was that "retiring an
    override the operator entered deliberately would move published
    numbers without anyone asking for it" — true, but it weighed a
    silent change against a silent change. An out-of-bounds value is one
    this registry defines as nonsense (#45's own motivating case was
    `-5` typed under Solver Target IRR, stored as `-0.05`, with the
    solver running on it), and continuing to compute on it does not
    preserve anyone's intent — it publishes a number the operator can no
    longer even re-enter through the UI.

    What resolves the tension is that skipping is NOT silent, and the
    channel already existed: `build_config_patch` returns the key in
    `skipped`, the worker stamps it as `config_skipped` on the run
    record, and the settings page badges the row. The row itself is
    still never retired from the database — that part of #45 stands.

    `spec` is accepted so a caller holding a built registry does not pay
    for a rebuild per key; `build_config_patch` checks every delta.
    """
    if spec is None:
        spec = override_key_registry().get(key)
    if spec is None:
        return True                    # already badged "unknown key"
    vals = value if isinstance(value, (list, tuple)) else [value]
    # `bool` before `float`: True is 1.0 and False is 0.0 in Python, so a
    # JSON `true` stored against, say, GATES.min_irr_5yr would read as a
    # 100% IRR gate — in bounds, accepted, and completely wrong. The form
    # cannot produce a bool, but the whole point of this function is the
    # row that never went through the form.
    if any(isinstance(v, bool) for v in vals):
        return False
    try:
        vals = [float(v) for v in vals]
    except (TypeError, ValueError):
        return False
    # NaN fails every comparison in `_within`, so it lands out of bounds
    # rather than sailing through — which is the correct answer for a
    # value that would otherwise propagate as a null to every surface.
    return _within(spec, vals)


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
    # The OpEx load the projection assumes when the financials produce
    # none, and the tolerance that widens `opex_revenue_ratio` above into
    # the clamp (item T Category 3). Both were module constants in
    # registry.py, where no operator could see them and every published
    # IRR depended on them.
    #
    # Its own group rather than sharing the benchmarks': that group is
    # labelled $/NRSF and these two are shares of revenue, so a number
    # typed under the wrong heading would be wrong by three orders of
    # magnitude.
    # The heading is short because the two row labels — "Default" and
    # "Clamp Tolerance" — already say which is which under it. The
    # settings page lays groups out in up to three columns, so a heading
    # long enough to wrap costs a line in every column beside it.
    for k in cfg.EXPENSE_RATIO:
        reg[f"EXPENSE_RATIO.{k}"] = {
            "group": "OpEx / Revenue Ratio",
            "kind": "scalar", "pct": True, "int": False, "label": _label(k)}
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
    # Market cap by asset class and age band. Editable per asset type from
    # the settings page — the ConfigOverride scope key IS the class, so a
    # row scoped to "Boat & RV Storage" reaches exactly its own row here.
    for asset_class, bands in cfg.MARKET_CAP_RATES.items():
        for band in bands:
            reg[f"MARKET_CAP_RATES.{asset_class}.{band}"] = {
                "group": f"Market Cap Rates (as of {cfg.MARKET_CAP_AS_OF})",
                "kind": "scalar", "pct": True, "int": False,
                "label": f"{asset_class} — {_label(band)}"}
    # Population counts, so int and not pct — same treatment
    # `GATES.population_3mi` already gets via GATES_INT_KEYS.
    for k in cfg.POPULATION_TIERS:
        reg[f"POPULATION_TIERS.{k}"] = {
            "group": "Trade-Area Density Tiers (3-mile population)",
            "kind": "scalar", "pct": False, "int": True, "label": _label(k)}
    # Decimal rates (0.95/0.90/0.85), unlike POPULATION_TIERS' counts — so
    # pct, not int. Narrative grading only (config.py's own comment above
    # OCCUPANCY_TIERS); `test_the_occupancy_tiers_stay_ordered` is the
    # composed-value guard bounds can't see.
    for k in cfg.OCCUPANCY_TIERS:
        reg[f"OCCUPANCY_TIERS.{k}"] = {
            "group": "Occupancy Narrative Tiers", "kind": "scalar",
            "pct": True, "int": False, "label": _label(k)}
    for k in cfg.VALUE_ADD_TRIGGERS:
        reg[f"VALUE_ADD_TRIGGERS.{k}"] = {
            "group": "Value-Add Triggers", "kind": "scalar",
            "pct": True, "int": False, "label": _label(k)}
    # The opportunity-sizing layer (item T Category 2). Two keys are
    # bands rendered into memo prose, one is a tenure in months; the rest
    # are plain shares. `config.RENOVATION_COST` is deliberately absent —
    # it moves no number, see the note in webapp.services._PATCHED_DICTS.
    for k in cfg.VALUE_ADD_ASSUMPTIONS:
        is_range = k in VA_ASSUMPTION_RANGE_KEYS
        is_int = k in VA_ASSUMPTION_INT_KEYS
        reg[f"VALUE_ADD_ASSUMPTIONS.{k}"] = {
            "group": "Value-Add Opportunity Assumptions",
            "kind": "range" if is_range else "scalar",
            "pct": not is_int, "int": is_int, "label": _label(k)}
    for k in cfg.TRANSACTION_COSTS:
        reg[f"TRANSACTION_COSTS.{k}"] = {
            "group": "Transaction Costs", "kind": "scalar",
            "pct": True, "int": False, "label": _label(k)}
    reg["SOLVER_TARGET_IRR"] = {
        "group": "Solver", "kind": "scalar", "pct": True, "int": False,
        "label": "Solver Target IRR"}
    # DEFAULT_HOLD_YEARS is deliberately absent: it is bound by value at
    # import in the model modules, so a _patched_config mutation could
    # never reach them (the same reason SOLVER_TARGET_IRR is special-cased
    # in build_config_patch). It is per-deal only, via the field above.
    #
    # Bounds are stamped over the finished registry rather than written
    # into each branch above: a loop that has to remember to add them is
    # a loop that eventually forgets, and an unbounded key is exactly the
    # defect this closes.
    for dotted, spec in reg.items():
        spec["bounds"] = _bounds_for(dotted, spec)
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
    always re-enterable (review finding). Bounds are checked on the
    CANONICAL value, after unit conversion and after rounding, so what
    is validated is exactly what gets stored."""
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
        out = [round(low, 6), round(high, 6)]
        _require_in_bounds(spec, out)
        return out
    v = _parse_num(raw.replace(",", ""))
    if spec["int"]:
        v = int(v)
    else:
        if spec["pct"]:
            v = v / 100.0
        v = round(v, 6)
    _require_in_bounds(spec, [v])
    return v


def _require_in_bounds(spec: dict, values) -> None:
    if not _within(spec, values):
        raise forms.ValidationError(
            f"{spec['label']} must be {bounds_display(spec)}.")


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


class KeyPickerSelect(forms.Select):
    """The key picker, with each option carrying its own allowed range as
    a native tooltip. The range has to be reachable BEFORE the value box
    is typed into, or the only channel is the rejection message. Spelling
    it into the option label instead would nearly double the width of a
    picker already carrying label + dotted key, and the range is
    reference material — read once per key, not on every visit."""

    option_titles = {}

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        title = self.option_titles.get(str(value))
        if title:
            option["attrs"]["title"] = title
        return option


class ConfigOverrideForm(forms.Form):
    key = forms.ChoiceField(widget=KeyPickerSelect)
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
        self.fields["key"].widget.option_titles = {
            key: f"Accepts {bounds_display(spec)}"
            for key, spec in reg.items()}
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
