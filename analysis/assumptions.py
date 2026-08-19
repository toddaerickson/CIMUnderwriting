"""Assumption register — every number this run used, and who chose it.

Item T Category 6, and the item's last scope clause. Categories 1-3 closed
the duplicated constant; Category 4 closed the silent fallback. What
neither closed is the **undisclosed provenance of a value that is not
wrong**: `GATES["min_irr_5yr"]` at 10% and the same gate at 8% both render
as a number with nothing beside it saying that one is the shipped default
and the other is a dated `ConfigOverride` row somebody wrote in June. The
memo printed the number. It did not print who chose it.

Every input needed already existed and was already persisted — a deal's
overrides (`Deal.assumption_overrides`, deltas by construction), the
effective settings rows (`services.resolve_config_overrides`, stamped into
`AnalysisRun.applied_overrides`), the pristine extraction
(`Deal.cim_json`), the fill log, and `config.py` itself. Nothing joined
them, so the stamp was auditable only by querying the database. This
module joins them and hands the result to the surfaces.

## It reports; it resolves nothing

Every value here is read from what the run already resolved: live `config`
(patched in place for the duration of a run, so a live read IS the
effective value), the resolved dicts the engine already receives as
parameters, or — where a value needs deriving — THE resolver the model
itself calls (`resolve_hold_years`, `resolve_mgmt_fee_target`,
`get_regional_benchmarks`, ...). A register that re-derived any of them
would be a second answer to a question the pipeline already answered,
which is the duplicated-constant defect wearing this item's own badge.

It is assembled ONCE at the engine and handed to every surface, the same
discipline `analysis.checks` and `analysis.fills` follow and for the same
reason: three surfaces computing provenance independently is how they come
to disagree about a deal.

## One winner per assumption

Precedence is the model's actual precedence — a deal override beats a
settings row beats the shipped default — and each assumption produces ONE
row carrying the winner. Printing the superseded value beside the applied
one is how a reader ends up auditing a number the engine never used, which
is the defect `webapp.services` already guards against when it pops
`SOLVER_TARGET_IRR` out of the applied stamp on a per-deal override.

`was` carries what the winner displaced, and only that: it is what makes an
analyst correction auditable ("NRSF 60,000 — entered for this deal; the CIM
said 58,400") rather than merely disclosed.

## What is NOT in the register

`NOT_IN_REGISTER` below, with reasons, and
`test_every_settings_editable_key_is_in_the_register_or_declared_out`
fails on anything that is in neither. A new config key defaults to FAILING
that test, because a completeness claim that has to be manually maintained
stops being true the first month nobody remembers it.
"""

from dataclasses import asdict, dataclass

import config as cfg
from analysis.fills import (UNIT_BPS, UNIT_COUNT, UNIT_DOLLARS, UNIT_MONTHS,
                            UNIT_PCT, UNIT_PSF, UNIT_PSF_MO, UNIT_PSF_YR,
                            UNIT_SF, UNIT_TEXT, UNIT_VINTAGE, UNIT_YEARS,
                            format_number)
from analysis.fills import from_dicts as fills_from_dicts

# ── Provenance vocabulary ───────────────────────────────────────────
# Closed set, and DECLARATION ORDER IS PRECEDENCE: the first of these that
# applies to an assumption is the one that produced its value.

DEAL = "deal"
SETTINGS = "settings"
FALLBACK = "fallback"
CIM = "cim"
# Measured by this system from public data, for THIS property's location —
# the Census ACS demographics `extract.enrichment` fetches. It sits after
# CIM because that is the precedence the model actually runs: the resolver
# takes a CIM-stated population over a measured one (tier 1 beats tier 2)
# and only measures what the document left empty. It sits before CONFIG
# because a measurement is evidence about this deal, not a shipped
# default. It is NOT in `CHOSEN` — see that constant.
EXTERNAL = "external"
CONFIG = "config"

#: What each provenance means, in one clause, for the memo legend and the
#: results-page column. `PROVENANCE_KEYS` is DERIVED from this so the
#: vocabulary and its labels can never drift — the same rule
#: `fills.SOURCE_KEYS` follows, and for the same reason.
PROVENANCE_LABELS = {
    DEAL: "entered for this deal",
    SETTINGS: "settings override",
    FALLBACK: "filled from a default",
    CIM: "stated in the CIM",
    EXTERNAL: "measured from public data",
    CONFIG: "model default",
}
PROVENANCE_KEYS = tuple(PROVENANCE_LABELS)

#: The provenances that mean a human or a fallback — not the shipped
#: model — produced this number. This is the first memo table and the
#: results-page summary line: what is unusual about THIS run.
#:
#: `EXTERNAL` is deliberately NOT here (operator, 2026-08-18). This tuple
#: answers "did a human or a fallback produce this number?", and a
#: measurement is neither; widening it to "anything but the CIM and the
#: defaults" would change what B.1 promises a reader. The measurement
#: still discloses itself wherever the full register renders — which is
#: the memo's B.2, the workbook's Inputs sheet and the results panel.
CHOSEN = (DEAL, SETTINGS, FALLBACK)

#: Neither the CIM's own figures nor the shipped defaults produced these.
#: A HEADLINE counts from this; the tables count from `CHOSEN`.
#:
#: Keeping `EXTERNAL` out of `CHOSEN` is right (above), but `chosen == 0`
#: was also standing in for "then there is nothing here but the CIM and the
#: defaults" — and a run whose population was MEASURED makes that false.
#: The results panel printed "all model defaults" over a Census figure, and
#: memo B.1 printed "every input came from the CIM as stated" about a
#: number the CIM never stated. Two different questions were being asked
#: through one constant, and the surfaces that asked the second one got a
#: wrong answer the day a sixth provenance existed. `CIM` stays out of this
#: tuple for the same reason it stays out of `CHOSEN`: both sentences name
#: the CIM explicitly, so a CIM row is disclosed by the wording, not by the
#: count.
NOT_FROM_DEFAULTS = CHOSEN + (EXTERNAL,)

# ── Groups, in render order ─────────────────────────────────────────

G_DEAL = "Deal Inputs"
G_GATES = "Investment Gates"
G_RETURNS = "Return Targets"
G_TIMING = "Hold & Transaction Costs"
G_EXIT = "Exit Cap"
G_SCENARIOS = "Scenarios"
G_VA_SCEN = "Value-Add Scenarios"
G_VA = "Value-Add Opportunity"
G_EXPENSES = "Expense Benchmarks"
G_RC = "Replacement Cost"
G_CAPITAL = "Capital Structure"
G_DEBT = "Debt & Waterfall"
G_TIERS = "Narrative Tiers"
G_SOLVER = "Solver Mechanics"

GROUP_ORDER = (G_DEAL, G_GATES, G_RETURNS, G_TIMING, G_EXIT, G_SCENARIOS,
               G_VA_SCEN, G_VA, G_EXPENSES, G_RC, G_CAPITAL, G_DEBT,
               G_TIERS, G_SOLVER)

# ── Membership ──────────────────────────────────────────────────────

#: Settings-editable keys deliberately absent from the register, each with
#: the reason. The guard test reads this dict, so an entry here is a
#: DECISION on the record, not an omission — which is the whole difference
#: between a register and a list somebody stopped updating.
#:
#: Prefixes, matched with `startswith`. `MARKET_CAP_RATES` is the only
#: entry and it is the whole point of the mechanism: the table holds twelve
#: cells, ONE of which priced this exit. "Every number that moved an
#: output" excludes the other eleven by its own wording, and the resolved
#: anchor — with the class and age band it was looked up by — is reported
#: in `G_EXIT` instead.
NOT_IN_REGISTER = {
    "MARKET_CAP_RATES.": (
        "the table holds one cell per asset class and age band; only the "
        "resolved anchor moved this run's exit, and it is reported under "
        f"'{G_EXIT}' with the class and band it was looked up by"),
}

#: CIM fields that drive the model, with the label and unit each reads in.
#: NOT every `CIMData` attribute: a register of things that did not move an
#: output teaches a reader to skim the one that did.
#: `test_every_numeric_cim_form_field_is_registered` holds this against
#: `webapp.forms`' analyst-editable list so a new input box cannot appear
#: on the assumptions page and silently miss the appendix.
CIM_FIELDS = (
    ("nrsf", "Rentable Square Feet", UNIT_SF),
    ("total_units", "Unit Count", UNIT_COUNT),
    ("acreage", "Acreage", UNIT_TEXT),
    # A vintage is a label, not a magnitude — 2015 must not render
    # "2,015". Both `UNIT_COUNT` and `UNIT_TEXT` group thousands, which is
    # why this needed a unit of its own rather than a different existing
    # one; see `_VINTAGE_KEYS` for the config-side twin.
    ("year_built", "Year Built", UNIT_VINTAGE),
    ("year_expanded", "Year Expanded", UNIT_VINTAGE),
    ("asking_price", "Asking Price", UNIT_DOLLARS),
    ("capex_estimate", "CapEx Estimate", UNIT_DOLLARS),
    ("physical_occupancy", "Physical Occupancy", UNIT_PCT),
    ("economic_occupancy", "Economic Occupancy", UNIT_PCT),
    ("cc_pct", "Climate-Controlled Share", UNIT_PCT),
    ("ttm_gpr", "TTM Gross Potential Rent", UNIT_DOLLARS),
    ("ttm_egr", "TTM Effective Gross Revenue", UNIT_DOLLARS),
    ("other_income", "Other Income", UNIT_DOLLARS),
    ("ttm_total_revenue", "TTM Total Revenue", UNIT_DOLLARS),
    ("ttm_total_expenses", "TTM Total Expenses", UNIT_DOLLARS),
    ("ttm_noi", "TTM NOI", UNIT_DOLLARS),
    ("ttm_months", "TTM Months of Actuals", UNIT_MONTHS),
    ("cim_yr1_noi", "CIM Year-1 NOI", UNIT_DOLLARS),
    ("t3_annualized_revenue", "T3 Annualized Revenue", UNIT_DOLLARS),
    ("in_place_avg_rent_psf", "In-Place Rent", UNIT_PSF_MO),
    ("market_rent_psf", "Market Rent", UNIT_PSF_MO),
    ("mgmt_fee_pct", "Management Fee (as reported)", UNIT_PCT),
    ("population_1mi", "1-Mile Population", UNIT_COUNT),
    ("population_3mi", "3-Mile Population", UNIT_COUNT),
    ("population_5mi", "5-Mile Population", UNIT_COUNT),
    ("median_hhi_3mi", "3-Mile Median HHI", UNIT_DOLLARS),
    ("competitive_supply_sf_3mi", "Competitive Supply (3mi)", UNIT_SF),
    ("pipeline_supply_sf_3mi", "Pipeline Supply (3mi)", UNIT_SF),
    ("ss_driveup_sf", "Self-Storage Drive-Up SF", UNIT_SF),
    ("ss_enclosed_sf", "Self-Storage Enclosed SF", UNIT_SF),
    ("brv_enclosed_sf", "Boat/RV Enclosed SF", UNIT_SF),
    ("brv_covered_sf", "Boat/RV Covered SF", UNIT_SF),
    ("brv_open_sf", "Boat/RV Open SF", UNIT_SF),
)

#: Labels for the config keys the register renders. Kept beside the keys
#: rather than derived from them: `_label`-style title-casing turns
#: `min_dscr` into "Min Dscr" and `ecri_egr_uplift` into "Ecri Egr
#: Uplift", and an appendix an IC reader has to decode is an appendix that
#: does not discharge the disclosure.
LABELS = {
    # Gates
    "population_3mi": "Minimum 3-Mile Population",
    "min_physical_occupancy": "Minimum Physical Occupancy",
    "stabilized_occupancy": "Stabilized Occupancy (vintage test)",
    "unproven_vintage_year": "Unproven-Vintage Cutoff",
    "econ_phys_spread_flag": "Econ/Physical Spread Flag",
    "rate_bridge_gap_threshold": "Rate-Bridge Gap Threshold",
    "max_noi_step_up": "Maximum CIM NOI Step-Up",
    "min_irr_5yr": "Minimum Unlevered IRR",
    "min_yield_on_cost": "Minimum Yield on Cost",
    "max_sf_per_capita": "Maximum SF per Capita",
    # Scenarios
    "yr1_noi_bump": "Year-1 NOI Bump",
    "stabilized_occ": "Stabilized Occupancy",
    "rev_cagr_yr1_3": "Revenue CAGR, Years 1-3",
    "rev_cagr_yr4_5": "Revenue CAGR, Years 4-5",
    "exp_growth": "Expense Growth",
    # Value-add scenarios
    "target_occupancy": "Target Occupancy",
    "months_to_stabilize": "Months to Stabilize",
    "rent_growth_to_market": "Rent Growth to Market",
    "post_stabilize_rev_growth": "Post-Stabilization Revenue Growth",
    "expense_growth": "Expense Growth",
    # Value-add triggers and assumptions
    "max_occupancy": "Value-Add Trigger: Occupancy Below",
    "min_rent_gap_pct": "Value-Add Trigger: Rent Gap Above",
    "occupancy_target": "Occupancy Target",
    "spread_recovery_share": "Econ/Physical Spread Recovery",
    "ecri_min_occupancy": "ECRI Minimum Occupancy",
    "ecri_egr_uplift": "ECRI EGR Uplift",
    "ecri_increase_range": "ECRI Increase Range",
    "ecri_tenant_tenure_months": "ECRI Tenant Tenure",
    "ancillary_min_share": "Ancillary Minimum Share",
    "ancillary_target_share": "Ancillary Target Share",
    "ancillary_revenue_uplift": "Ancillary Revenue Uplift",
    # Expense benchmarks
    "property_tax": "Property Taxes",
    "insurance": "Insurance",
    "utilities": "Utilities",
    "repairs": "Repairs & Maintenance",
    "advertising": "Advertising",
    "payroll": "Payroll",
    "ga": "General & Administrative",
    "mgmt_fee_pct": "Management Fee (band)",
    "cap_reserve": "Capital Reserve",
    "total_opex": "Total OpEx",
    "opex_revenue_ratio": "OpEx / Revenue Ratio",
    # Replacement cost
    "soft_cost_pct": "Soft Costs",
    "dev_profit_pct": "Developer Profit",
    # Debt
    "loan_type": "Loan Type",
    "rate": "Interest Rate",
    "index_rate": "Index Rate",
    "spread": "Spread",
    "amort_years": "Amortization",
    "io_months": "Interest-Only Period",
    "term_years": "Loan Term",
    "max_ltv": "Maximum LTV",
    "min_dscr": "Minimum DSCR",
    "min_debt_yield": "Minimum Debt Yield",
    "orig_fee_pct": "Origination Fee",
    "exit_fee_pct": "Exit Fee",
    # Waterfall
    "pref_rate": "Preferred Return",
    "pref_compounding": "Pref Compounding",
    "ordering": "Distribution Ordering",
    "promote_split": "Promote",
    "accrual_base": "Pref Accrual Base",
    "am_fee_treatment": "AM Fee Treatment",
    "catch_up": "GP Catch-Up",
    # Capital structure
    "capex_basis": "CapEx Basis",
    "operating_reserve": "Operating Reserve",
    "operating_reserve_basis": "Operating Reserve Basis",
    "gp_coinvest_pct": "GP Co-Invest",
    # Transaction costs
    "acquisition_closing_pct": "Acquisition Closing Costs",
    "disposition_cost_pct": "Disposition Costs",
    # Tiers
    "preferred_density": "Preferred 3-Mile Density",
    "strong_density": "Strong 3-Mile Density",
    "over_occupied": "Over-Occupied Threshold",
    "strong": "Strong Occupancy",
    "healthy": "Healthy Occupancy",
    # Expense ratio
    "default": "Assumed OpEx / Revenue",
    "clamp_tolerance": "Clamp Tolerance",
    # Solver
    "cheap_entry_cap": "Bracket: Cheap Entry Cap",
    "dear_entry_cap": "Bracket: Dear Entry Cap",
    "zero_noi_low_price": "Bracket: Zero-NOI Low Price",
    "zero_noi_high_price": "Bracket: Zero-NOI High Price",
    "price_span": "Sensitivity: Price Span",
    "price_step": "Sensitivity: Price Step",
    "exit_cap_span": "Sensitivity: Exit Cap Span",
    "exit_cap_step": "Sensitivity: Exit Cap Step",
}

#: Percentage-shaped keys inside otherwise mixed dicts. Declared rather
#: than sniffed from the value: 0.05 is a 5% rate and also a plausible
#: dollar figure, and a formatter that guesses will eventually print a
#: coupon as five cents.
_PCT_KEYS = {
    "min_physical_occupancy", "stabilized_occupancy", "econ_phys_spread_flag",
    "rate_bridge_gap_threshold", "max_noi_step_up", "min_irr_5yr",
    "min_yield_on_cost", "yr1_noi_bump", "stabilized_occ", "rev_cagr_yr1_3",
    "rev_cagr_yr4_5", "exp_growth", "target_occupancy",
    "rent_growth_to_market", "post_stabilize_rev_growth", "expense_growth",
    "max_occupancy", "min_rent_gap_pct", "occupancy_target",
    "spread_recovery_share", "ecri_min_occupancy", "ecri_egr_uplift",
    "ancillary_min_share", "ancillary_revenue_uplift", "rate", "index_rate",
    "spread", "max_ltv", "min_debt_yield", "orig_fee_pct", "exit_fee_pct",
    "pref_rate", "promote_split", "gp_coinvest_pct",
    "acquisition_closing_pct", "disposition_cost_pct", "over_occupied",
    "strong", "healthy", "default", "clamp_tolerance", "cheap_entry_cap",
    "dear_entry_cap", "price_span", "price_step", "exit_cap_span",
    "exit_cap_step", "soft_cost_pct", "dev_profit_pct", "mgmt_fee_pct",
    "opex_revenue_ratio", "ecri_increase_range", "ancillary_target_share",
}
_COUNT_KEYS = {"population_3mi", "max_sf_per_capita", "preferred_density",
               "strong_density"}
_MONTH_KEYS = {"months_to_stabilize", "ecri_tenant_tenure_months", "io_months"}
_YEAR_KEYS = {"amort_years", "term_years"}
_DOLLAR_KEYS = {"zero_noi_low_price", "zero_noi_high_price"}
#: A coverage ratio: neither a magnitude nor a rate. `UNIT_TEXT`'s `,.4g`
#: prints 1.25 as itself, where `UNIT_COUNT` would render it "1".
_PLAIN_KEYS = {"min_dscr"}
#: Vintage years. `UNIT_TEXT` is NOT enough — its `,.4g` groups thousands
#: too, so 2021 came out "2,021" until a rendered memo was actually read.
_VINTAGE_KEYS = {"unproven_vintage_year"}


def _unit_for(key: str) -> str:
    if key in _VINTAGE_KEYS:
        return UNIT_VINTAGE
    if key in _PLAIN_KEYS:
        return UNIT_TEXT
    if key in _PCT_KEYS:
        return UNIT_PCT
    if key in _MONTH_KEYS:
        return UNIT_MONTHS
    if key in _YEAR_KEYS:
        return UNIT_YEARS
    if key in _DOLLAR_KEYS:
        return UNIT_DOLLARS
    if key in _COUNT_KEYS:
        return UNIT_COUNT
    return UNIT_TEXT


def _label_for(key: str) -> str:
    """A declared label, or a readable fallback that says it is one.

    Falling back to title-case rather than raising: a key added to config
    between releases must still APPEAR in the appendix, ugly, rather than
    crash the memo or — far worse — be silently skipped, which is the one
    failure mode this module exists to prevent.
    """
    return LABELS.get(key) or key.replace("_", " ").title()


@dataclass(frozen=True)
class Assumption:
    """One number this run used, with the reason it had that value.

    `key` is the dotted config path (`GATES.min_irr_5yr`) for a config-
    backed assumption and `cim.<field>` / `fill.<field>` for the two
    deal-level namespaces. It is the de-duplication identity, so the
    namespaces matter: a stated market rent produces `cim.market_rent_psf`
    and an absent one produces `fill.market_rent_psf`, which is exactly one
    row either way.
    """
    key: str
    label: str
    group: str
    value: object
    provenance: str
    unit: str = UNIT_TEXT
    was: object = None
    detail: str = ""

    @property
    def provenance_label(self) -> str:
        return PROVENANCE_LABELS.get(self.provenance, self.provenance)

    @property
    def chosen(self) -> bool:
        """True when something other than the shipped model produced it."""
        return self.provenance in CHOSEN


def format_value(a: "Assumption") -> str:
    """The one rendering of an assumption's value.

    A (low, high) band renders as one string rather than two columns: the
    band IS the assumption, and splitting it invites a reader to compare
    one deal's low against another's high.
    """
    value = a.value
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return (f"{format_number(value[0], a.unit)} – "
                f"{format_number(value[1], a.unit)}")
    return format_number(value, a.unit)


def to_dicts(rows) -> list[dict]:
    """JSON-safe rows for `AnalysisRun.result_json`."""
    return [asdict(r) for r in rows]


def from_dicts(rows) -> list[Assumption]:
    """Inverse of `to_dicts`, for surfaces reading a stored run.

    Unknown keys dropped, every optional field defaulted — a run recorded
    before the next column existed must still render, or adding a column
    500s the results page for every deal already in the database. Same
    contract as `fills.from_dicts`, for the same reason.
    """
    known = {"key", "label", "group", "value", "provenance", "unit",
             "was", "detail"}
    out = []
    for row in rows or []:
        kwargs = {k: v for k, v in (row or {}).items() if k in known}
        if "key" not in kwargs or "provenance" not in kwargs:
            continue
        kwargs.setdefault("label", kwargs["key"])
        kwargs.setdefault("group", G_DEAL)
        kwargs.setdefault("value", None)
        # A stored band round-trips through JSON as a list; the formatter
        # accepts either, so nothing is coerced back to a tuple here.
        out.append(Assumption(**kwargs))
    return out


def summarize(rows) -> dict:
    """Counts by provenance, for the memo's headline and the UI summary.

    Every declared provenance is present with a zero, so a caller can
    render "0 entered for this deal" rather than reasoning about a missing
    key — a count silently absent reads as a count of none anyway, and
    only one of those two is true on purpose.

    Two derived keys ride along, and they are NOT interchangeable:
    `chosen` is what the highlighted tables list, `not_from_defaults` is
    what a headline may claim. See both constants.
    """
    counts = {k: 0 for k in PROVENANCE_KEYS}
    for row in rows:
        if row.provenance in counts:
            counts[row.provenance] += 1
    counts["total"] = len(rows)
    counts["chosen"] = sum(counts[k] for k in CHOSEN)
    counts["not_from_defaults"] = sum(counts[k] for k in NOT_FROM_DEFAULTS)
    return counts


# ── Assembly ────────────────────────────────────────────────────────

class _Register:
    """Accumulator that knows the two delta dicts, so every row's
    provenance is decided in ONE place rather than at each append."""

    def __init__(self, config_deltas, config_defaults, deal_overrides):
        self.deltas = config_deltas or {}
        self.defaults = config_defaults or {}
        self.deal = deal_overrides or {}
        self.rows = []

    # -- provenance --------------------------------------------------

    def deal_section(self, name):
        """The per-deal override section, or None. Deltas by construction
        (`forms.build_overrides`), so presence IS 'the analyst set this'."""
        return self.deal.get(name) or None

    def add(self, key, group, value, *, label=None, unit=None,
            deal_hit=False, was=None, detail=""):
        """One row, with provenance decided by the declaration order of
        the vocabulary: a deal override beats a settings row beats the
        shipped default."""
        if deal_hit:
            provenance = DEAL
        elif key in self.deltas:
            provenance, was = SETTINGS, self.defaults.get(key, was)
        else:
            provenance, was = CONFIG, None
        self.rows.append(Assumption(
            key=key, label=label or _label_for(key.rsplit(".", 1)[-1]),
            group=group, value=value, provenance=provenance,
            unit=unit if unit is not None else _unit_for(key.rsplit(".", 1)[-1]),
            was=was, detail=detail))

    def add_row(self, row):
        self.rows.append(row)

    # -- config sections --------------------------------------------

    def add_dict(self, name, source, group, *, deal_section=None,
                 skip=(), unit=None):
        """Every key of a config dict as its own row.

        `deal_section` names the per-deal override section that shadows
        this dict, checked PER KEY — transaction costs merge per parameter
        rather than replacing wholesale, so a deal that overrode one of
        the two must not relabel the other as analyst-entered.
        """
        overrides = self.deal_section(deal_section) if deal_section else None
        for key, value in source.items():
            if key in skip:
                continue
            hit = bool(overrides) and key in overrides
            self.add(f"{name}.{key}", group,
                     overrides[key] if hit else value,
                     unit=unit, deal_hit=hit,
                     was=value if hit else None)


def _scenario_value(custom, scenario, param, default):
    """A per-deal scenario value, tolerating either key spelling.

    The form stores sections keyed by `ScenarioType.value`; config keys
    them by the enum itself. Both spellings reach here — the web path
    passes the stored dict straight through — and guessing wrong would
    silently report the config default for a run that used the analyst's.
    """
    for key in (scenario, getattr(scenario, "value", scenario)):
        section = (custom or {}).get(key)
        if isinstance(section, dict) and param in section:
            return section[param], True
    return default, False


def collect(*, cim_data=None, cim_snapshot=None, config_deltas=None,
            config_defaults=None, deal_overrides=None, fill_log=None,
            scenarios=None, va_scenarios=None, expense_line_overrides=None,
            hold_years=None, transaction_costs=None, market_cap=None,
            capital_structure=None, debt_terms=None, waterfall_terms=None,
            am_fee_pct=None, mgmt_fee_target_pct=None,
            solver_target_irr=None, enrichment_log=None) -> list[Assumption]:
    """The run's whole assumption register, ordered and de-duplicated.

    Callers pass whatever they have; a missing section contributes nothing
    rather than raising. The CLI passes neither `config_deltas` nor
    `deal_overrides` because it HAS neither — it has no `ConfigOverride`
    table and no assumptions page — so its register is `config` / `cim` /
    `fallback` / `external`, which is the truth about a CLI run and is
    tested as such.

    `enrichment_log` is `extract.enrichment`'s own source log for this run,
    and like `config_deltas` and `cim_snapshot` it is PROVENANCE ONLY: it
    changes no arithmetic and can move no value, because the numbers it
    describes are already on `cim_data`. A caller that has none loses the
    `external` provenance and nothing else.
    """
    from analysis.financials import resolve_mgmt_fee_target
    from analysis.valuation import resolve_hold_years, resolve_transaction_costs
    from model.returns_model import resolve_capital_structure
    from registry import EXPENSE_KEYS

    reg = _Register(config_deltas, config_defaults, deal_overrides)

    _add_cim_rows(reg, cim_data, cim_snapshot, enrichment_log)
    _add_fill_rows(reg, fill_log)

    reg.add_dict("GATES", cfg.GATES, G_GATES)

    # Return targets. The solver target resolves through the model's own
    # resolver, never a second `if x is None` here — item T Category 3
    # made that function the one answer and a 0% target is a real entry
    # that a truthiness check swallows.
    from model.solver import resolve_target_irr
    reg.add("SOLVER_TARGET_IRR", G_RETURNS, resolve_target_irr(solver_target_irr),
            deal_hit=(deal_overrides or {}).get("solver_target_irr") is not None,
            was=cfg.SOLVER_TARGET_IRR, unit=UNIT_PCT,
            label="Max-Offer Target (unlevered)")
    reg.add("SOLVER_TARGET_LP_NET_IRR", G_RETURNS, cfg.SOLVER_TARGET_LP_NET_IRR,
            unit=UNIT_PCT, label="Max-Offer Target (LP net)")
    reg.add("IRR_STRONG_THRESHOLD", G_RETURNS, cfg.IRR_STRONG_THRESHOLD,
            unit=UNIT_PCT, label="Strong-Return Threshold")

    # Hold and transaction costs — resolved values, not deltas, for the
    # reason webapp.services stamps them that way: item B changed every
    # published IRR, so a run reporting nothing because it sat on the
    # defaults would be indistinguishable from a pre-item-B run.
    reg.add("DEFAULT_HOLD_YEARS", G_TIMING, resolve_hold_years(hold_years),
            deal_hit=(deal_overrides or {}).get("hold_years") is not None,
            was=cfg.DEFAULT_HOLD_YEARS, unit=UNIT_YEARS, label="Hold Period")
    reg.add_dict("TRANSACTION_COSTS",
                 resolve_transaction_costs(transaction_costs), G_TIMING,
                 deal_section="transaction_costs")

    _add_exit_cap_rows(reg, market_cap)

    for scen, params in cfg.SCENARIO_DEFAULTS.items():
        for param, default in params.items():
            value, hit = _scenario_value(scenarios, scen, param, default)
            reg.add(f"SCENARIO_DEFAULTS.{scen.value}.{param}", G_SCENARIOS,
                    value, deal_hit=hit, was=default if hit else None,
                    label=f"{scen.value.title()} — {_label_for(param)}")
    for scen, params in cfg.VALUE_ADD_SCENARIOS.items():
        for param, default in params.items():
            value, hit = _scenario_value(va_scenarios, scen, param, default)
            reg.add(f"VALUE_ADD_SCENARIOS.{scen.value}.{param}", G_VA_SCEN,
                    value, deal_hit=hit, was=default if hit else None,
                    label=f"{scen.value.title()} — {_label_for(param)}")

    reg.add_dict("VALUE_ADD_TRIGGERS", cfg.VALUE_ADD_TRIGGERS, G_VA)
    reg.add_dict("VALUE_ADD_ASSUMPTIONS", cfg.VALUE_ADD_ASSUMPTIONS, G_VA)

    _add_expense_rows(reg, cim_data, expense_line_overrides, EXPENSE_KEYS,
                      mgmt_fee_target_pct, resolve_mgmt_fee_target)
    _add_replacement_cost_rows(reg)

    capital = resolve_capital_structure(capital_structure)
    reg.add_dict("CAPITAL_STRUCTURE", capital, G_CAPITAL,
                 deal_section="capital_structure")
    _add_debt_rows(reg, debt_terms, waterfall_terms, am_fee_pct, deal_overrides)

    reg.add_dict("POPULATION_TIERS", cfg.POPULATION_TIERS, G_TIERS)
    reg.add_dict("OCCUPANCY_TIERS", cfg.OCCUPANCY_TIERS, G_TIERS)

    reg.add_dict("SOLVER_BOUNDS", cfg.SOLVER_BOUNDS, G_SOLVER)
    reg.add_dict("SENSITIVITY_GRID", cfg.SENSITIVITY_GRID, G_SOLVER)

    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    seen, out = set(), []
    for row in reg.rows:
        if row.key in seen:
            continue
        seen.add(row.key)
        out.append(row)
    out.sort(key=lambda r: order.get(r.group, len(order)))
    return out


def _add_cim_rows(reg, cim_data, cim_snapshot, enrichment_log=None):
    """The deal's own numbers, and whether the analyst corrected one.

    `cim_snapshot` is the PRISTINE extraction (`Deal.cim_json`), so a field
    that differs from it was edited on the assumptions page — and a field
    the snapshot carried as EMPTY was typed in there, which is the same act
    and reads the same way. The CLI passes no snapshot and edits nothing, so
    every stated field is `cim` there — which is true, not a degraded mode.

    A field with no value contributes NOTHING: it moved no output, and a
    fallback that stood in for it is already a row of its own.

    **Not everything in the snapshot came out of the document.** Census
    enrichment runs BEFORE the snapshot is saved, so a measured population
    sits inside `cim_json` looking exactly like an extracted one — and this
    filed it as "stated in the CIM", a claim the document never made, on
    the very number Gate 1 is decided by. That is the same defect
    `_add_ocr_row` below exists to close, arriving from the other side: one
    is a `cim` claim that overstates how the figure was read, this is a
    `cim` claim about a figure the CIM does not contain at all.

    `enrichment_log` is the only witness to the difference; with none, every
    stated field is `cim`, which is exactly what a run that never enriched
    means. WHICH fields can be measured is read from the log and never
    listed here: a field list would be a second answer to a question
    `extract.enrichment` already answers, and it goes stale the first time
    that module learns to measure one more thing.
    """
    if cim_data is None:
        return
    from extract.enrichment import MEASURED_TIER, origin_for

    _add_ocr_row(reg, cim_data)
    snapshot = cim_snapshot or {}
    log = enrichment_log or {}
    for field, label, unit in CIM_FIELDS:
        value = getattr(cim_data, field, None)
        if value is None or value == "":
            continue
        prior = snapshot.get(field)
        entry = origin_for(log, field, value)
        if entry is not None and entry.get("tier") == MEASURED_TIER:
            # Measured is tested FIRST, and the order is load-bearing. When
            # extraction's enrichment pass fails and the analyst then fixes
            # the address, re-enrichment measures the field at ANALYSIS time
            # — so the snapshot holds None and the "analyst filled it" branch
            # below would otherwise claim a number the analyst never typed.
            # `origin_for` has already refused any entry whose logged value
            # is not this one, so an overtyped figure cannot reach here.
            provenance, was, detail = EXTERNAL, None, _measurement_detail(log, entry)
        elif prior is not None and prior != value:
            provenance, was, detail = DEAL, prior, "corrected on the assumptions page"
        elif prior is None and field in snapshot:
            # The analyst filled a field extraction left EMPTY. This read as
            # `cim` — "stated in the CIM" — for as long as the register has
            # existed, because `edited` needed a prior to differ from and
            # None differs from nothing. It is the same lie the measured
            # population told, arriving from the third side: a number the
            # document does not contain, credited to the document.
            #
            # `field in snapshot` is what separates it from schema drift.
            # The snapshot is `dataclasses.asdict`, so every CIMData field
            # of that vintage is a key even when its value is None; a field
            # ABSENT means the snapshot predates it and the origin is simply
            # unknowable — testing `prior is None` alone would relabel every
            # such field on every deal stored before it existed.
            provenance, was, detail = (
                DEAL, None, "entered on the assumptions page; extraction found none")
        else:
            provenance, was, detail = CIM, None, ""
        reg.add_row(Assumption(
            key=f"cim.{field}", label=label, group=G_DEAL, value=value,
            provenance=provenance, unit=unit, was=was, detail=detail))


def _measurement_detail(log, entry):
    """How the measurement was taken, in one clause.

    The ring's CENTRE is half the claim and it is logged separately: a
    3-mile ring drawn around a matched building and one drawn around a ZIP
    code's centroid are different statements about the same property, which
    is why `extract.enrichment` stamps the centre's origin beside the
    coordinates in the first place. A row naming the API and staying silent
    about the centre would disclose the easier half.
    """
    source = str(entry.get("source") or "external source").strip()
    centre = str((log.get("lat") or {}).get("source") or "").strip()
    return f"{source} — ring centred on the {centre}" if centre else source


def _add_ocr_row(reg, cim_data):
    """Whether any of this deal's numbers were read off a machine
    transcription rather than the PDF's own text layer.

    **This row is a qualifier, not a value that moved an output**, which
    stretches this module's own definition — so the reason it is here anyway:
    a `cim` provenance means "stated in the CIM", and a figure recovered by
    `extract.ocr` from a scanned page is a weaker claim than the same figure
    lifted from an embedded text layer. Leaving the two indistinguishable
    would make the register's central promise — that a number's source is on
    the page beside it — quietly false on exactly the decks where it matters
    most.

    It is ONE document-level row rather than a flag on each affected row, and
    that is a limit of the pipeline rather than a choice: the parser attributes
    no page to a scalar field. `FinancialLine.page` exists, but its own comment
    says a page "is not an identity" — two statements share one and one
    statement spans two — and no page at all reaches `nrsf`,
    `physical_occupancy` or `asking_price`, which come from document-wide
    regexes over the joined text. A per-row flag would therefore have to be
    guessed, and a guessed provenance is worse than an honest coarse one.
    The `detail` below says exactly that, so no reader infers precision the
    row does not have.

    Contributes nothing when no page was transcribed — which is every deck
    with a text layer, and every deck at all while `CIM_OCR_ENABLED` is off.
    """
    pages = getattr(cim_data, "ocr_pages", None) or []
    if not pages:
        return
    listed = ", ".join(str(p) for p in pages)
    # The label is a property OF THE DOCUMENT, deliberately, because B.2 prints
    # the provenance without the `detail` beside it: "Machine-Transcribed
    # Pages | 2 | stated in the CIM" reads as a claim the CIM makes, which it
    # does not. "Pages Without a Text Layer | 2 | stated in the CIM" reads
    # correctly — the source document is where the fact comes from. The full
    # statement is the memo's own Appendix B sentence.
    reg.add_row(Assumption(
        key="cim.ocr_pages", label="Pages Without a Text Layer", group=G_DEAL,
        value=len(pages), provenance=CIM, unit=UNIT_COUNT,
        detail=(f"page(s) {listed} carried no text layer and were read by "
                f"machine transcription; which values came from them cannot "
                f"be stated, as the parser attributes no page to a field")))


def _add_fill_rows(reg, fill_log):
    """The Category 4 fill log, joined in as one provenance class.

    Not a second copy of it — `fills.collect` remains the one assembly and
    this reads its output. Appendix A still renders the fills on their own
    because "what did the model invent?" and "what did the model use?" are
    different questions; this makes the register COMPLETE, so an auditor
    who reads only it has still seen every number.
    """
    for fill in fills_from_dicts(fill_log):
        reg.add_row(Assumption(
            key=f"fill.{fill.field}", label=_label_for(fill.field),
            group=G_DEAL, value=fill.value_used, provenance=FALLBACK,
            unit=fill.unit, detail=fill.label))


def _add_exit_cap_rows(reg, market_cap):
    """The resolved anchor, not the twelve-cell table it came from.

    See `NOT_IN_REGISTER`. An analyst-entered cap is a deal override; a
    table lookup is the model default, and the class and age band it was
    looked up by are the detail that makes the number checkable.
    """
    mc = market_cap or {}
    rate = mc.get("market_cap")
    if rate is not None:
        analyst = mc.get("source") == "analyst"
        detail = "" if analyst else " / ".join(
            str(v) for v in (mc.get("asset_class"), mc.get("age_band")) if v)
        reg.add_row(Assumption(
            key="MARKET_CAP_RATES.resolved", label="Market Cap Rate (anchor)",
            group=G_EXIT, value=rate,
            provenance=DEAL if analyst else CONFIG, unit=UNIT_PCT,
            detail=detail))
    reg.add("EXIT_CAP_DRIFT_BPS", G_EXIT,
            cfg.EXIT_CAP_DRIFT_BPS.get(_base_scenario()), unit=UNIT_BPS,
            label="Exit Cap Drift (base, per year)")
    reg.add("EXIT_CAP_SCENARIO_SPREAD_BPS", G_EXIT,
            cfg.EXIT_CAP_SCENARIO_SPREAD_BPS.get(_base_scenario()),
            unit=UNIT_BPS, label="Exit Cap Scenario Spread (base)")
    # Which year's NOI the exit capitalizes (decision 5, settled
    # 2026-08-10) — governs BOTH exit engines. Not settings-editable, so
    # `override_key_registry()` never demands this row; it is here by the
    # levered-constants rule (silence is not permission), pinned by its
    # own test.
    reg.add("EXIT_NOI_CONVENTION", G_EXIT, cfg.EXIT_NOI_CONVENTION,
            unit=UNIT_TEXT, label="Exit NOI Convention",
            detail="trailing = terminal hold year's own NOI; "
                   "forward = year N+1's")


def _base_scenario():
    from registry import ScenarioType
    return ScenarioType.BASE


def _add_expense_rows(reg, cim_data, expense_line_overrides, expense_keys,
                      mgmt_fee_target_pct, resolve_mgmt_fee_target):
    """The benchmark bands the run actually charged against.

    REGIONAL, not national, when the deal has a state — `financials.py`
    underwrites against `get_regional_benchmarks(state)`, so reporting the
    national table would print a band the run never used. Resolved by
    calling that same function, never by reapplying its multipliers here.
    """
    state = getattr(cim_data, "state", "") or ""
    benchmarks = (cfg.get_regional_benchmarks(state) if state
                  else cfg.EXPENSE_BENCHMARKS)
    detail = f"regionally adjusted for {state.upper()}" if state else ""
    overrides = reg.deal_section("expense_line_overrides") or {}
    for key, band in benchmarks.items():
        reg.add(f"EXPENSE_BENCHMARKS.{key}", G_EXPENSES, band,
                unit=UNIT_PCT if key in _PCT_KEYS else UNIT_PSF_YR,
                detail=detail if key not in ("mgmt_fee_pct",
                                             "opex_revenue_ratio",
                                             "property_tax") else "")
    # An analyst-entered expense line beats both the CIM and the band, so
    # it is its own row rather than a footnote on the benchmark above.
    for key in expense_keys:
        if key in overrides:
            reg.add_row(Assumption(
                key=f"expense_line.{key}", label=f"{_label_for(key)} (entered)",
                group=G_EXPENSES, value=overrides[key], provenance=DEAL,
                unit=UNIT_DOLLARS,
                detail="analyst entry, before the benchmark adjustment"))
    reg.add("MGMT_FEE_TARGET_PCT", G_EXPENSES,
            resolve_mgmt_fee_target(mgmt_fee_target_pct),
            deal_hit=(reg.deal.get("mgmt_fee_target_pct") is not None),
            was=cfg.MGMT_FEE_TARGET_PCT, unit=UNIT_PCT,
            label="Pro-Forma Management Fee")
    reg.add_dict("EXPENSE_RATIO", cfg.EXPENSE_RATIO, G_EXPENSES)


def _add_replacement_cost_rows(reg):
    """Hard and site cost per facility type, plus the two percentages.

    Driven from `config.FACILITY_TYPES`, which is the canonical ordered
    list — that is also what keeps the three legacy aliases
    (`non_cc_per_sf`, `cc_per_sf`, `site_work_per_sf`) out without this
    module restating which keys are aliases. A second copy of that list is
    the duplicated constant item T exists to delete.
    """
    overrides = reg.deal_section("replacement_cost_overrides") or {}
    for hard_key, site_key, display in cfg.FACILITY_TYPES:
        for key, suffix in ((hard_key, "hard cost"), (site_key, "site work")):
            band = cfg.REPLACEMENT_COST.get(key)
            if band is None:
                continue
            hit = key in overrides
            reg.add(f"REPLACEMENT_COST.{key}", G_RC,
                    overrides[key] if hit else band,
                    unit=UNIT_PSF, deal_hit=hit, was=band if hit else None,
                    label=f"{display} — {suffix}")
    for key in ("soft_cost_pct", "dev_profit_pct"):
        hit = key in overrides
        band = cfg.REPLACEMENT_COST[key]
        reg.add(f"REPLACEMENT_COST.{key}", G_RC,
                overrides[key] if hit else band, unit=UNIT_PCT,
                deal_hit=hit, was=band if hit else None)


def _add_debt_rows(reg, debt_terms, waterfall_terms, am_fee_pct,
                   deal_overrides):
    """The levered lens's inputs — per-deal only, never settings-editable.

    That is item E3b's decision, not this module's, and it is why these
    keys are absent from `override_key_registry()` and so from the
    settings-key guard. They still move an output, so they are still in
    the register: the guard's silence about a key is not permission to
    omit it.
    """
    from model.debt import resolve_debt_terms
    from model.waterfall import resolve_waterfall_terms
    import dataclasses

    from model.waterfall import resolve_pref_rate

    resolved_debt = (debt_terms if isinstance(debt_terms, dict)
                     else dataclasses.asdict(resolve_debt_terms(debt_terms)))
    is_levered = float(resolved_debt.get("max_ltv") or 0.0) > 0.0
    resolved_wf = (waterfall_terms if isinstance(waterfall_terms, dict)
                   else dataclasses.asdict(resolve_waterfall_terms(
                       waterfall_terms, is_levered=is_levered)))
    deal_debt = (deal_overrides or {}).get("debt_terms") or {}
    deal_wf = (deal_overrides or {}).get("waterfall_terms") or {}
    # `pref_rate` has no key in config.WATERFALL_TERMS — it resolves from
    # the deal's leverage — so the defaults dict is extended with what
    # THE resolver would have returned for this deal. Without it, a deal
    # that names its own pref would report `was: None`, which reads as
    # "this displaced nothing" when it displaced 8% (or 6%). Read from
    # the resolver, never recomputed here: decision 11's rule.
    wf_defaults = dict(cfg.WATERFALL_TERMS,
                       pref_rate=resolve_pref_rate(is_levered))
    for name, resolved, deal_section, defaults in (
            ("DEBT_TERMS", resolved_debt, deal_debt, cfg.DEBT_TERMS),
            ("WATERFALL_TERMS", resolved_wf, deal_wf, wf_defaults)):
        for key, value in resolved.items():
            # `gp_coinvest_pct` rides on the waterfall's resolved dict but
            # belongs to the capital stack, which already reported it. One
            # number, one row — see `resolve_waterfall_terms`.
            if key == "gp_coinvest_pct":
                continue
            hit = key in deal_section
            reg.add(f"{name}.{key}", G_DEBT, value, deal_hit=hit,
                    was=defaults.get(key) if hit else None)
    reg.add("AM_FEE_PCT", G_DEBT,
            cfg.AM_FEE_PCT if am_fee_pct is None else am_fee_pct,
            deal_hit=(deal_overrides or {}).get("am_fee_pct") is not None,
            was=cfg.AM_FEE_PCT, unit=UNIT_PCT, label="Asset Management Fee")
    reg.add("AM_FEE_BASE", G_DEBT, cfg.AM_FEE_BASE, unit=UNIT_TEXT,
            label="AM Fee Base")
