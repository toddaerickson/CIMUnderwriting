"""
CIM Analyst — Hard-coded investment criteria, expense benchmarks,
replacement cost benchmarks, and scenario defaults.
"""

import os

# ── Go / No-Go Gate Thresholds ──────────────────────────────────────

GATES = {
    "population_3mi": 50_000,
    # "No unproven demand" gate (formerly "no lease-up"):
    # exclude deals that have never demonstrated demand — sub-75% physical
    # occupancy, or a post-2020 vintage still in ramp. Deals with proven
    # demand but weak economics (high physical / low economic occupancy)
    # deliberately pass: mismanagement is the target value-add profile.
    "min_physical_occupancy": 0.75,   # hard floor — below this, demand is unproven
    "stabilized_occupancy": 0.85,     # ramp test threshold for new-vintage builds
    "unproven_vintage_year": 2021,    # post-2020 builds must have stabilized
    "econ_phys_spread_flag": 0.10,    # econ occ this far below phys → mismanagement value-add
    "rate_bridge_gap_threshold": 0.10,  # in-place this far below market + stabilized occ → ECRI-only bridge flag
    "max_noi_step_up": 0.15,       # CIM Yr1 NOI vs TTM — flag if exceeded
    "min_irr_5yr": 0.10,
    "min_yield_on_cost": 0.08,
    # Oversupply screen: (subject + competitive + pipeline NRSF within
    # 3 mi) / 3-mi population. National equilibrium runs ~7-8 SF/capita;
    # above this threshold the market is oversupplied.
    "max_sf_per_capita": 10,
}

# ── Trade-area density tiers (3-mile population) ────────────────────
#
# NARRATIVE tiers, not screens. `GATES["population_3mi"]` is the only
# population number that passes or fails a deal; these two grade how the
# trade area READS above it, and they are settings-editable so the grading
# can be tuned per strategy without touching the gate.
#
# `preferred_density` is ONE threshold with two faces: at or above it,
# `market.py` reports density as a demand positive; below it, `risks.py`
# raises "Limited trade area population". They were two separate 75,000
# literals in two modules and could drift apart — a market that was not a
# positive but also not a risk, or both at once.
POPULATION_TIERS = {
    "preferred_density": 75_000,   # demand positive at/above, risk below
    "strong_density": 100_000,     # top narrative tier — "strong demand driver"
}

# ── Median-household-income narrative tiers ─────────────────────────
#
# The same lane as POPULATION_TIERS: these GRADE a trade area's income,
# they do not pass or fail a deal. No gate reads them.
#
# They were four bare literals across two functions in `analysis/market.py`
# and they did not agree with each other: the narrative graded at
# 75k/50k while the positives/negatives list graded at 65k/45k, so the
# same deal could read "middle-income, adequate purchasing power" in one
# paragraph and "below-average household income" in the next. Naming them
# does not reconcile that — `narrative_*` and `signal_*` stay separate
# because a three-band prose grade and a two-sided positive/negative
# signal are different questions — but it makes the disagreement visible
# and overridable instead of buried.
#
# `adequate` (50_000) EQUALS `GATES["population_3mi"]` by coincidence of
# value only. They are unrelated quantities and
# `test_the_hhi_thresholds_are_not_the_population_gate` exists to keep a
# later edit from "consolidating" them.
HHI_TIERS = {
    "narrative_affluent": 75_000,   # "supports premium pricing"
    "narrative_adequate": 50_000,   # "adequate purchasing power"; also hhi_adequate
    "signal_above_average": 65_000,  # risk-list positive
    "signal_below_average": 45_000,  # risk-list negative
}

# ── Risk-register trigger thresholds ────────────────────────────────
#
# When `analysis/risks.py` RAISES a risk, and at what severity. Like the
# tiers above these grade rather than gate — no deal passes or fails on
# them — but they were bare literals inside three separate `if`
# statements, which is the shape the 2026-08-01 audit was written about.
#
# `replacement_premium_high` (0.15) EQUALS `GATES["max_noi_step_up"]` by
# coincidence of value only; they are unrelated quantities, and that
# coincidence is precisely what made the pre-Category-1 duplicates
# hazardous. Keep them apart.
RISK_TRIGGERS_THRESHOLDS = {
    "bear_irr_floor": 0.05,             # bear IRR below this raises a risk
    "scenario_spread_wide": 0.08,       # base-minus-bear spread above this is "wide"
    "replacement_premium_high": 0.15,   # premium to replacement cost: High vs Medium
}

# ── Occupancy narrative tiers ───────────────────────────────────────
#
# NARRATIVE tiers, not screens — the same lane as POPULATION_TIERS above.
# `GATES["min_physical_occupancy"]` is the only occupancy number that
# passes or fails a deal; these three grade how an occupancy READS in the
# memo's demand narrative and risk list.
#
# `healthy` (0.85) EQUALS `GATES["stabilized_occupancy"]` today and is
# deliberately a separate key: one asks "does this read as stable demand?"
# and the other asks "has this post-2020 vintage ever stabilized?".
# Collapsing them would tie the memo's prose to a screening threshold, so
# that tuning the narrative silently re-screens deals.
# `test_occupancy_narrative_tiers_are_config_not_the_stabilization_gate`
# is what stops that.
#
# Ordering invariant: over_occupied >= strong >= healthy. These are
# settings-editable, so two independently valid edits can invert the pair
# and produce a band no occupancy can land in — the same composed-value
# hole `registry.EXPENSE_RATIO_LIMITS` closed for the expense clamp.
# `test_the_occupancy_tiers_stay_ordered` is the guard.
OCCUPANCY_TIERS = {
    "over_occupied": 0.95,   # above → rate suppression risk (rents too low)
    "strong":        0.90,   # at/above → "demand exceeds supply"
    "healthy":       0.85,   # at/above → "stable demand"
}

# Sensitivity-grid green band. PRESENTATION ONLY — nothing screens on it.
# The grid's other boundary is `GATES["min_irr_5yr"]`, so a cell reads
# green above this, yellow down to the gate, red below it. Kept an
# independent value rather than gate + 200bps: the spread between "clears
# the gate" and "genuinely strong" is a judgment, not an arithmetic fact.
IRR_STRONG_THRESHOLD = 0.12

# ── Expense Benchmarks ($/NRSF/yr, stabilized non-climate-controlled) ─

EXPENSE_BENCHMARKS = {
    "property_tax":      (1.20, 2.50),
    "insurance":         (0.12, 0.25),
    "utilities":         (0.08, 0.18),
    "repairs":           (0.20, 0.40),
    "advertising":       (0.05, 0.15),
    "payroll":           (0.30, 0.60),
    "ga":                (0.10, 0.20),
    "mgmt_fee_pct":      (0.03, 0.06),   # as % of EGR
    "cap_reserve":       (0.15, 0.25),
    "total_opex":        (3.00, 5.50),
    "opex_revenue_ratio": (0.35, 0.55),
}

# ── The OpEx/Revenue ratio the PROJECTION runs on ───────────────────
# `registry.clamp_expense_ratio` is the ONE resolver, and these were its
# two shadow defaults — `DEFAULT_EXPENSE_RATIO = 0.40` and
# `EXPENSE_RATIO_CLAMP = (0.25, 0.65)` living in registry.py, where the
# settings page could not see them and no operator could reach them.
# They drive `analysis.valuation.project_cash_flows`, so they move every
# published IRR; a modeling input of that weight does not belong in a
# constants module (item T Category 3).
#
# **The clamp is DERIVED from `opex_revenue_ratio` above, not restated.**
# (0.35, 0.55) widened by 0.10 is exactly the (0.25, 0.65) registry.py
# held — the same pair, now with its relation to the band written down
# instead of inferred. That relation is the point: the band is
# settings-editable, and a hard-coded clamp beside an edited band would
# clip ratios the operator had just declared credible. Widen-by-tolerance
# also says what the clamp is FOR — the band is the range a stabilized
# asset is UNDERWRITTEN to, the clamp is the range a stated ratio is
# BELIEVED in, and the second is deliberately looser than the first.
#
# `default` is NOT derived and must not become so. It is the ratio used
# when the financials yield none at all, and 0.40 sits low in the band —
# below the 0.45 midpoint — because an unknown expense load underwritten
# at the middle of the band is an assumption dressed as an average. What
# it *should* be is a per-value decision item T's scope excludes; this
# move only gives it a home. `test_the_expense_ratio_default_sits_inside
# _the_benchmark_band` is the CI guard that a band edit cannot orphan it.
EXPENSE_RATIO = {
    "default": 0.40,
    "clamp_tolerance": 0.10,   # clamp = opex_revenue_ratio band ± this
}

# The ONE management-fee target, as % of EGR — the pro-forma fee the model
# underwrites TO. `mgmt_fee_pct` above is the benchmark BAND, the range a
# STATED fee is judged against; this is the single value used when the CIM
# states a fee below the band or states none at all, and the value
# `analysis/value_add.py` renegotiates an above-market fee down to.
#
# 6% is the TOP of the band, and that is the point: a CIM that omits its
# management fee is the common case, and underwriting the omission at the
# most expensive credible number is the conservative read. It was 5% —
# in-band but arbitrary, and unreachable by anyone tuning a deal — until
# the operator set it here on 2026-08-04.
#
# Per-deal editable on the assumptions page (`mgmt_fee_target_pct`), which
# supersedes this default for that one run. Deliberately NOT a
# `_PATCHED_DICTS` entry: it is a plain scalar, and modules that bind a
# scalar by value at import can never see a patch — the same reason
# SOLVER_TARGET_IRR and DEFAULT_HOLD_YEARS travel as parameters.
MGMT_FEE_TARGET_PCT = 0.06

# ── Replacement Cost Benchmarks ─────────────────────────────────────
# Per-SF hard costs by facility type (2025/2026 construction costs).
# Each type has its own hard-cost and site-work range. Soft costs
# and developer profit apply uniformly across all types.

REPLACEMENT_COST = {
    # ── Hard cost per SF by facility type ──
    # Self-Storage: Drive-Up (single-story PEMB, roll-up doors, no HVAC)
    "ss_driveup_per_sf":      (55, 85),
    # Self-Storage: Enclosed Multi-Story (steel/concrete, HVAC, elevator)
    "ss_enclosed_per_sf":     (90, 130),
    # Boat/RV: Enclosed (large clear-span PEMB, 18-24 ft clear, tall doors)
    "brv_enclosed_per_sf":    (45, 70),
    # Boat/RV: Covered canopy (steel columns + metal roof, no walls)
    "brv_covered_per_sf":     (15, 30),
    # Boat/RV: Open parking (paving, fencing, lighting, cameras, security)
    "brv_open_per_sf":        (4, 10),

    # ── Site work per SF by facility type ──
    "ss_driveup_site_per_sf":     (5, 12),
    "ss_enclosed_site_per_sf":    (5, 12),
    "brv_enclosed_site_per_sf":   (8, 15),
    "brv_covered_site_per_sf":    (6, 12),
    "brv_open_site_per_sf":       (0, 0),    # included in hard cost above

    # ── Soft costs & developer profit (apply to all types) ──
    "soft_cost_pct":    (0.08, 0.12),
    "dev_profit_pct":   (0.10, 0.15),

    # ── Legacy aliases (backward compat for existing overrides) ──
    "non_cc_per_sf":    (55, 85),     # maps to ss_driveup_per_sf
    "cc_per_sf":        (90, 130),    # maps to ss_enclosed_per_sf
    "site_work_per_sf": (5, 12),      # default site work
}

# Ordered list of facility-type keys for iteration
FACILITY_TYPES = [
    # (config_hard_key, config_site_key, display_name)
    ("ss_driveup_per_sf",   "ss_driveup_site_per_sf",   "Self-Storage Drive-Up"),
    ("ss_enclosed_per_sf",  "ss_enclosed_site_per_sf",   "Self-Storage Enclosed"),
    ("brv_enclosed_per_sf", "brv_enclosed_site_per_sf",  "Boat/RV Enclosed"),
    ("brv_covered_per_sf",  "brv_covered_site_per_sf",   "Boat/RV Covered"),
    ("brv_open_per_sf",     "brv_open_site_per_sf",      "Boat/RV Open Parking"),
]

# ── Scenario Assumptions ────────────────────────────────────────────

from registry import ScenarioType

SCENARIO_DEFAULTS = {
    ScenarioType.BEAR: {
        "yr1_noi_bump":    0.00,     # flat from TTM
        "stabilized_occ":  0.82,
        "rev_cagr_yr1_3":  0.015,
        "rev_cagr_yr4_5":  0.015,
        "exp_growth":      0.03,
    },
    ScenarioType.BASE: {
        "yr1_noi_bump":    0.05,
        "stabilized_occ":  0.88,
        "rev_cagr_yr1_3":  0.025,
        "rev_cagr_yr4_5":  0.025,
        "exp_growth":      0.03,
    },
    ScenarioType.BULL: {
        "yr1_noi_bump":    0.10,
        "stabilized_occ":  0.93,
        "rev_cagr_yr1_3":  0.04,
        "rev_cagr_yr4_5":  0.035,
        "exp_growth":      0.03,
    },
}

# ── Exit Cap: market anchor + obsolescence drift ────────────────────
# The exit cap is DERIVED, not entered:
#
#   exit_cap = market_cap(class, age band)
#              + scenario spread
#              + drift_bps_per_year * hold_years
#
# It used to be a free-standing per-scenario constant (7.5% base), which
# priced a 2003 drive-up facility and a 2022 climate-controlled build at
# the same exit. Age and class are what the market actually prices, and
# an asset keeps ageing through the hold — hence the drift.

# Current market cap by asset class and age band. Rows are the three
# values of registry.ASSET_TYPES (which webapp.services re-exports as the
# settings-page scope vocabulary); columns are the keys of
# registry.AGE_BANDS. Spelled out here rather than imported so this table
# reads as data — tests/test_exit_cap.py carries the no-drift assertion
# against both, the same guard tests/test_web_config.py puts on the
# ASSET_TYPES dropdown.
#
# This is a STARTING POINT the analyst confirms, not live data. Cap rates
# move with the rate environment; MARKET_CAP_AS_OF is printed in the memo
# and the check register so a stale table is visible rather than silent.
MARKET_CAP_AS_OF = "2026-Q3"

MARKET_CAP_RATES = {
    "Self Storage": {
        "new":   0.0575,
        "mid":   0.0625,
        "aging": 0.0675,
        "old":   0.0750,
    },
    "Climate-Controlled Self Storage": {
        "new":   0.0550,
        "mid":   0.0600,
        "aging": 0.0650,
        "old":   0.0725,
    },
    "Boat & RV Storage": {
        "new":   0.0650,
        "mid":   0.0700,
        "aging": 0.0750,
        "old":   0.0825,
    },
}

# Obsolescence drift, bps per year of HOLD. The operator's rule is 5-10
# bps/yr; bear assumes the asset dates fastest. Age at acquisition is
# already priced by the band above, so this covers only ageing in hand.
EXIT_CAP_DRIFT_BPS = {
    ScenarioType.BEAR: 10.0,
    ScenarioType.BASE: 7.5,
    ScenarioType.BULL: 5.0,
}

# The scenario's view of the market at exit, in bps on top of the market
# cap. This is the axis that carries "caps widen / caps compress" and is
# what keeps a bear case punitive: drift alone spans only ~25 bps over a
# five-year hold, where the old fixed triple spanned 200.
EXIT_CAP_SCENARIO_SPREAD_BPS = {
    ScenarioType.BEAR: 100.0,
    ScenarioType.BASE: 0.0,
    ScenarioType.BULL: -100.0,
}

# Fallback when the vintage is unknown. The oldest band is deliberate:
# an unknown-age asset should not be priced as if it were new.
MARKET_CAP_UNKNOWN_AGE_BAND = "old"


# ── Top-50 MSAs (simplified list for gate check) ────────────────────

TOP_50_MSAS = [
    "New York", "Los Angeles", "Chicago", "Dallas", "Houston",
    "Washington", "Philadelphia", "Miami", "Atlanta", "Boston",
    "Phoenix", "San Francisco", "Riverside", "Detroit", "Seattle",
    "Minneapolis", "San Diego", "Tampa", "Denver", "St. Louis",
    "Baltimore", "Orlando", "Charlotte", "San Antonio", "Portland",
    "Sacramento", "Pittsburgh", "Las Vegas", "Austin", "Cincinnati",
    "Kansas City", "Columbus", "Indianapolis", "Cleveland", "San Jose",
    "Nashville", "Virginia Beach", "Providence", "Milwaukee", "Jacksonville",
    "Memphis", "Oklahoma City", "Louisville", "Richmond", "New Orleans",
    "Hartford", "Raleigh", "Salt Lake City", "Birmingham", "Buffalo",
]

# ── State Property Tax Multipliers ──────────────────────────────────
# Property tax rates vary dramatically by state. The national benchmark
# range ($1.20–$2.50/NRSF) is multiplied by these factors to produce
# state-adjusted ranges. Factors derived from effective commercial
# property tax rates relative to the national median.
#
# < 1.0 = lower-tax state (benchmark range shifts down)
# > 1.0 = higher-tax state (benchmark range shifts up)

STATE_PROPERTY_TAX_MULTIPLIER = {
    # Very low tax states (effective rate < 0.6% of value)
    "AL": 0.45,   # Alabama — low assessments, low mill rates
    "CO": 0.55,   # Colorado — ~6.7% assessment ratio, low effective rate
    "HI": 0.40,   # Hawaii — very low effective rates
    "WV": 0.50,   # West Virginia — low assessments
    "WY": 0.50,   # Wyoming — low overall burden
    "SC": 0.55,   # South Carolina — favorable commercial assessment
    "UT": 0.60,   # Utah — moderate-low
    "AR": 0.55,   # Arkansas — low effective rate
    "LA": 0.55,   # Louisiana — low assessments
    "MS": 0.60,   # Mississippi — low effective rate
    "NM": 0.60,   # New Mexico — low overall
    "OK": 0.65,   # Oklahoma — below average

    # Low-to-moderate tax states
    "AZ": 0.70,   # Arizona
    "NC": 0.70,   # North Carolina
    "TN": 0.65,   # Tennessee — no income tax but moderate property tax
    "ID": 0.70,   # Idaho
    "MT": 0.70,   # Montana
    "NV": 0.70,   # Nevada — no income tax, moderate property tax
    "GA": 0.75,   # Georgia
    "MO": 0.75,   # Missouri
    "VA": 0.75,   # Virginia
    "IN": 0.80,   # Indiana
    "KY": 0.75,   # Kentucky
    "ND": 0.75,   # North Dakota
    "SD": 0.80,   # South Dakota — no income tax, moderate property tax

    # Average tax states (0.85–1.15 = roughly national median)
    "CA": 0.90,   # California — Prop 13 limits
    "FL": 1.00,   # Florida — no income tax, average property tax
    "MD": 0.95,   # Maryland
    "MN": 1.05,   # Minnesota
    "OR": 0.95,   # Oregon
    "WA": 0.95,   # Washington — no income tax, moderate property tax
    "KS": 1.10,   # Kansas
    "IA": 1.10,   # Iowa
    "NE": 1.10,   # Nebraska
    "OH": 1.10,   # Ohio
    "MI": 1.10,   # Michigan
    "PA": 1.10,   # Pennsylvania
    "WI": 1.15,   # Wisconsin

    # High tax states (effective rate > 1.5% of value)
    "MA": 1.20,   # Massachusetts
    "NY": 1.30,   # New York — high outside NYC, very high in NYC suburbs
    "VT": 1.30,   # Vermont
    "NH": 1.40,   # New Hampshire — no income tax, very high property tax
    "CT": 1.45,   # Connecticut — very high mill rates
    "IL": 1.50,   # Illinois — notoriously high, esp. Cook County
    "NJ": 1.60,   # New Jersey — highest effective rate in US
    "TX": 1.55,   # Texas — no income tax, very high property tax (2-3%+)
}

# ── Income-Based Property Tax Formulas ────────────────────────────
# For states where the $/SF benchmark approach is too crude, use an
# income-capitalization method instead:
#   1. Estimated Value = NOI / cap_rate
#   2. Assessed Value  = Estimated Value × assessment_ratio
#   3. Property Tax    = Assessed Value × tax_rate
#
# When a state has a formula, it replaces the $/SF benchmark entirely.
# The $/SF multiplier in STATE_PROPERTY_TAX_MULTIPLIER is ignored for
# states that have a formula here.

STATE_PROPERTY_TAX_FORMULAS = {
    "TX": {
        "cap_rate": 0.07,            # 7% cap rate for value estimation
        "assessment_ratio": 0.73,    # Assessed at 73% of estimated value
        "tax_rate": 0.022,           # 2.2% of assessed value
    },
}

# ── Value-Add Scenario Assumptions ─────────────────────────────────
# Used when property triggers value-add criteria (sub-85% occupancy,
# in-place rents significantly below market, etc.)

VALUE_ADD_SCENARIOS = {
    ScenarioType.BEAR: {
        "target_occupancy": 0.85,        # status quo — no occupancy improvement
        "months_to_stabilize": 30,
        "rent_growth_to_market": 0.85,   # achieve 85% of rent gap
        "post_stabilize_rev_growth": 0.02,
        "expense_growth": 0.03,
    },
    ScenarioType.BASE: {
        "target_occupancy": 0.88,        # realistic stabilization
        "months_to_stabilize": 24,
        "rent_growth_to_market": 1.00,   # close full rent gap
        "post_stabilize_rev_growth": 0.03,
        "expense_growth": 0.03,
    },
    ScenarioType.BULL: {
        "target_occupancy": 0.92,        # optimistic lease-up
        "months_to_stabilize": 18,
        "rent_growth_to_market": 1.00,
        "post_stabilize_rev_growth": 0.04,
        "expense_growth": 0.025,
    },
}

VALUE_ADD_TRIGGERS = {
    "max_occupancy": 0.85,        # below this → value-add deal
    "min_rent_gap_pct": 0.10,     # in-place rent 10%+ below market
}

# ── Value-Add Opportunity Assumptions (item T Category 2) ───────────
# The policy layer behind `analysis/value_add.py` — which operational
# gaps count as an opportunity, and how much of each is underwritten as
# recoverable. Every value below was a literal inside that module, which
# meant the settings page could move `VALUE_ADD_TRIGGERS` (what makes a
# deal value-add) while the size of the upside stayed frozen.
#
# These are NOT the same numbers as VALUE_ADD_SCENARIOS above, and the
# overlap is the trap. That dict is the monthly lease-up ENGINE's
# per-scenario inputs (`model/value_add_model.py`); this one sizes the
# narrative opportunities in section 7 of the memo. `occupancy_target`
# here (0.93) is deliberately higher than any `target_occupancy` there
# (0.85/0.88/0.92): the engine underwrites what the deal is priced at,
# this states what a well-run asset reaches. Reconciling them is item T
# Category 5's call, not this block's.
#
# Every value EQUALS the literal it replaced, so shipping this block
# moved no number — the characterization snapshots reproduce
# byte-for-byte. What the values SHOULD be is a separate, per-value
# decision (scoped-backlog item T, "Out of scope").
VALUE_ADD_ASSUMPTIONS = {
    # Occupancy upside. Below this physical occupancy the gap to it is
    # credited as revenue upside, scaled by the current occupancy.
    "occupancy_target": 0.93,

    # Economic-occupancy recovery. The share of the physical-to-economic
    # spread assumed recoverable through concession burn-off, tighter
    # collections and repricing below-street in-place rents. A HAIRCUT on
    # a measured gap, not a measurement — the other half is assumed
    # structural. `GATES["econ_phys_spread_flag"]` is what makes the
    # spread visible in the first place; this is what it is worth.
    "spread_recovery_share": 0.50,

    # Revenue management / ECRI. Only credible on an asset already full
    # enough to raise rents without bleeding occupancy, hence the floor.
    # The uplift is a share of EGR; the increase range and tenure are
    # narrative, rendered as f-strings so the prose cannot drift from the
    # number the model actually books.
    "ecri_min_occupancy": 0.88,
    "ecri_egr_uplift": 0.03,
    "ecri_increase_range": (0.08, 0.10),
    "ecri_tenant_tenure_months": 6,

    # Ancillary revenue (tenant insurance, late/admin fees, merchandise).
    # Below `ancillary_min_share` of total revenue, the line is treated as
    # under-exploited; `ancillary_target_share` is the band the narrative
    # points at and `ancillary_revenue_uplift` is what is actually booked.
    # The booked figure is deliberately BELOW the bottom of the target
    # band — reaching the band is the plan, three points is the underwrite.
    "ancillary_min_share": 0.05,
    "ancillary_target_share": (0.05, 0.08),
    "ancillary_revenue_uplift": 0.03,
}

# ── Renovation / CapEx Schedule (item T Category 2) ─────────────────
# The physical-improvement checklist section 7 renders, with its age
# triggers and cost ranges. PRESENTATION ONLY: `identify_value_add` sums
# `est_annual_impact` over the revenue and expense opportunities and
# never over these, so no capex item moves NOI, an IRR, or a gate. That
# is why the block is a plain module dict and NOT a
# `webapp.services._PATCHED_DICTS` entry — see `VALUE_ADD_ASSUMPTIONS`
# above, which is one, because it does move money.
#
# `min_age` is years since `year_built`, evaluated by `registry.asset_age`.
# An item with no `min_age` is always listed. `per_sf` costs multiply
# NRSF; `amount` costs are flat dollars. Order here is render order.
#
# ⚑ THE AGE LADDER IS STILL THREE LADDERS, ON PURPOSE — see
# ASSET_AGE_LADDERS below, which is the register.
RENOVATION_COST = {
    "roof": {
        "item": "Roof Replacement / Repair",
        "description": "Property is {age} years old — inspect roof condition.",
        "min_age": 20,
        "high_priority_age": 30,
        "per_sf": (1.50, 3.00),
        "priority": "Medium",
    },
    "led_lighting": {
        "item": "LED Lighting Upgrade",
        "description": "Convert to LED lighting for energy savings.",
        "min_age": 15,
        "per_sf": (0.30, 0.75),
        "priority": "Medium",
    },
    "security": {
        "item": "Security System Upgrade",
        "description": "Upgrade cameras, access control, and gate systems.",
        "min_age": 10,
        "amount": (15_000, 50_000),
        "priority": "Medium",
    },
    "signage": {
        "item": "Signage & Curb Appeal",
        "description": "Evaluate signage visibility and property aesthetics.",
        "amount": (5_000, 25_000),
        "priority": "Low",
    },
    "website": {
        "item": "Website & Digital Presence",
        "description": "Optimize online listings, website, and SEO.",
        "amount": (2_000, 10_000),
        "priority": "Medium",
    },
}

# ── Risk Triggers (item T Category 2) ───────────────────────────────
# Thresholds `analysis/risks.py` raises a narrative risk on, which are
# NOT gates — nothing here passes or fails a deal. Kept out of
# `_PATCHED_DICTS` on the same line as RENOVATION_COST: this PR made
# settings-editable exactly the things that move a dollar, and a risk
# paragraph does not. It reaches `risk_count` and the memo's "why this
# could fail" list, so it is a fair candidate for a later category.
RISK_TRIGGERS = {
    "aging_plant_age": 25,     # years — deferred-maintenance risk
}

# ── The asset-age register (item T Category 2) ──────────────────────
#
# THE POINT OF THIS BLOCK: there are three building-age ladders in this
# repo, they disagree, and until now they disagreed in three different
# files. They still disagree. What changed is that the disagreement is
# now declarable, greppable, and CI-guarded —
# `tests/test_config_single_source.py::test_no_age_threshold_survives
# _as_a_bare_literal` walks `analysis/`, `model/` and `output/` and
# fails on any age compared against a bare numeric literal, so a
# FOURTH ladder cannot appear quietly.
#
#   registry.AGE_BANDS          5 / 15 / 30   the canonical one.
#                               `analysis/physical.py` reconciled onto it
#                               (its comment says why), and the exit-cap
#                               market table keys off it, which is what
#                               made it load-bearing.
#   RENOVATION_COST[*].min_age  20 / 15 / 10  roof / LED / security,
#                               plus 30 for the roof's High priority.
#   RISK_TRIGGERS               25            aging physical plant.
#
# Reconciling them to ONE schedule was measured and DEFERRED on
# 2026-08-05 (operator's call). Snapping the triggers to band boundaries
# would newly flag 6-10 year assets for security and LED, newly flag
# 16-20 year assets for a roof, and — the one that decided it — would
# STOP 26-30 year assets flagging deferred maintenance. That is
# re-underwriting, which item T's own scope excludes ("this item moves
# values into config and labels them; what the values *should* be is a
# separate, per-value decision"). Do not snap them as a tidy-up.
ASSET_AGE_LADDERS = (
    "registry.AGE_BANDS", "RENOVATION_COST", "RISK_TRIGGERS",
)

# ── The occupancy register (item T Category 5) ──────────────────────
# Every occupancy LEVEL in the model, and the question each answers.
# They are deliberately NOT one number: Category 5 registered them the
# way Category 2 registered the three age ladders, because collapsing
# them is re-underwriting and item T's scope excludes that.
#
# "Level", not "number", and the distinction is the register's whole
# point. Two occupancy-denominated thresholds are deliberately OUT:
# `GATES["econ_phys_spread_flag"]` and `GATES["rate_bridge_gap_
# threshold"]` (both 0.10) measure the DIFFERENCE between two
# occupancies, not a point on the scale. Registering them would make
# this "anything occupancy-adjacent", and a register that means
# everything cannot say a fourth ladder appeared. The AST guard below
# is scoped to match: it flags an occupancy compared to a bare literal,
# which is what a LEVEL looks like in code.
#
#   GATES["min_physical_occupancy"]              is demand proven at all?
#   GATES["stabilized_occupancy"]                has a post-2020 vintage
#                                                ever stabilized?
#   OCCUPANCY_TIERS                              how does this occupancy
#                                                READ? (narrative only)
#   SCENARIO_DEFAULTS[*]["stabilized_occ"]       what the static DCF
#                                                assumes per scenario
#   VALUE_ADD_TRIGGERS["max_occupancy"]          below this the deal is
#                                                a value-add deal
#   VALUE_ADD_SCENARIOS[*]["target_occupancy"]   where the lease-up
#                                                engine ramps TO
#   VALUE_ADD_ASSUMPTIONS["occupancy_target"]    what a well-run asset
#                                                reaches (opportunity
#                                                sizing, not the engine)
#   VALUE_ADD_ASSUMPTIONS["ecri_min_occupancy"]  full enough to push
#                                                rents without bleeding
#   XLSM_TEMPLATE_INPUTS["assumed_physical_occupancy"]
#                                                the workbook's fallback
#                                                — item E3b's, and the
#                                                LAST assumed occupancy
#                                                left anywhere
#
# There is no "assumed occupancy" in the Python model and that is
# deliberate: see `model/value_add_model.py`'s Category 5 note.
OCCUPANCY_KEYS = (
    'GATES["min_physical_occupancy"]',
    'GATES["stabilized_occupancy"]',
    "OCCUPANCY_TIERS",
    'SCENARIO_DEFAULTS[*]["stabilized_occ"]',
    'VALUE_ADD_TRIGGERS["max_occupancy"]',
    'VALUE_ADD_SCENARIOS[*]["target_occupancy"]',
    'VALUE_ADD_ASSUMPTIONS["occupancy_target"]',
    'VALUE_ADD_ASSUMPTIONS["ecri_min_occupancy"]',
    'XLSM_TEMPLATE_INPUTS["assumed_physical_occupancy"]',
)

# ── Comp Database Parameters ───────────────────────────────────────

COMP_DB_PATH = os.environ.get(
    "COMP_DB_PATH",
    os.path.join(os.path.dirname(__file__) or ".", "data", "cim_comps.db"),
)
COMP_DB_MIN_COMPS = 3           # require at least 3 comps before using DB benchmarks
COMP_DB_NRSF_RANGE = (0.5, 2.0) # match properties within 50%-200% of subject NRSF

# ── Census API ─────────────────────────────────────────────────────

CENSUS_API_KEY = os.environ.get("CENSUS_API_KEY", "")

# ── IRR Solver Parameters ───────────────────────────────────────────

SOLVER_TARGET_IRR = 0.10
SOLVER_TOLERANCE = 0.001
SOLVER_MAX_ITERATIONS = 50

# ── The bisection bracket, for all three solvers ────────────────────
# Every solver in `model/solver.py` searches the same axis — purchase
# price — and each one carried its own bracket. Two of them disagreed:
# the static and levered solvers stopped at a 3% implied entry cap, the
# value-add solver went to 2%. Nothing recorded that, and the difference
# is not cosmetic: bisection answers only within the bracket it is
# given, so a root above `high` comes back as a price at the ceiling
# with `converged=False` — a number, in the same shape as an answer.
#
# The bracket is expressed as IMPLIED ENTRY CAPS on the analyst-adjusted
# TTM NOI, which is how it was written in all three places: `low` is the
# price at a 20% cap (so cheap that any structure clears any target),
# `high` the price at `dear_entry_cap` (so dear that none does).
#
# **2% wins, and it was MEASURED, not reasoned.** The value-add engine
# grows NOI well above TTM, so a lease-up deal's max price legitimately
# implies an entry cap below 3% on the TTM figure — which is what
# "value-add" means.
#
# The case, stated precisely enough to re-run — a review could not
# reproduce an earlier, vaguer version of this paragraph and reasonably
# concluded it was false. Take the `value_add` characterization fixture,
# run `analyze_financials`, and set
# `financial_analysis["adjusted_ttm_noi"]["analyst_adjusted_noi"]` to 30%
# of what it returns ($346,140 -> $103,842). That dict is the lever:
# `solve_max_price_value_add` reads it BEFORE `cim.ttm_noi`, and
# `analyze_financials` derives it from the T12 expense lines, so scaling
# the CIM's own NOI or its occupancy moves nothing. Pinned by
# `test_the_measurement_that_chose_the_two_percent_bracket`, so this is
# a fact CI checks rather than a paragraph asking to be believed:
#
#   3% bracket -> max_price $3,461,400, converged=False, achieved 13.48%
#   2% bracket -> max_price $4,261,173, converged=True,  achieved 10.01%
#
# The 3% run returns its own ceiling — the price is the bracket, to the
# dollar — and calls a deal that clears 13.5% a 10% max offer, $799,773
# (18.8%) light. Nothing on any surface reads `converged`, so it arrives
# looking exactly like an answer. That is the failure mode this key
# exists to avoid, and it is why the wider bound is not a preference.
#
# The cost of the wider bracket is bounded and small, also measured:
# across all three fixtures it moved the static and levered max offers by
# ≤ 0.8%, every case still converging inside `SOLVER_TOLERANCE` of its
# target. Bisection halves 50 times, so the extra span is spent in the
# first iteration or two.
#
# **A fixed bracket can still bind, and 2% only moves the wall.** Below
# roughly $70k of adjusted NOI the same fixture truncates at 2% as well.
# The genuine fix is a bracketing sweep — expand `high` until the
# objective actually crosses the target — which belongs to all three
# solvers and is not this item. Until then `model.solver` LOGS a
# truncated answer rather than returning it silently.
#
# NOT settings-editable, and not a `_PATCHED_DICTS` entry, for the same
# reason `SOLVER_TOLERANCE` and `SOLVER_MAX_ITERATIONS` beside it are
# not: this is the numeric method's search window, not an underwriting
# assumption. Nothing here should ever change what a deal is worth —
# only whether the solver can find it.
SOLVER_BOUNDS = {
    "cheap_entry_cap": 0.20,
    "dear_entry_cap": 0.02,
    # Used only when TTM NOI is missing or non-positive, where an
    # implied cap rate has no meaning. A raw dollar window, deliberately
    # enormous, because there is nothing to scale it against.
    "zero_noi_low_price": 100_000,
    "zero_noi_high_price": 50_000_000,
}

# ── Sensitivity grid axes ───────────────────────────────────────────
# `model.returns_model._build_sensitivity` held these as two literal
# lists of nine offsets each. The span and the step are the two numbers
# actually being chosen; the lists were their expansion, and an expansion
# is where a typo hides (one offset off by 25bps changes a column label
# and the IRR under it, and reads as a rounding artifact).
#
# Offsets run from −span to +span in `step` increments, so `span` must
# divide evenly by `step` — `_axis_offsets` raises if it does not, rather
# than silently dropping the end of an axis.
#
# PRESENTATION ONLY, like `RENOVATION_COST`: the grid displays IRRs the
# projection already computes and nothing screens on it, so it is not
# settings-editable and not a `_PATCHED_DICTS` entry. Its COLORING
# thresholds are a different matter and live with the gates —
# `GATES["min_irr_5yr"]` and `IRR_STRONG_THRESHOLD`, both editable.
SENSITIVITY_GRID = {
    "price_span": 0.10,        # ± this share of the asking price
    "price_step": 0.025,
    "exit_cap_span": 0.0100,   # ± this much cap rate, in decimal
    "exit_cap_step": 0.0025,
}

# The LEVERED solver's target (item E4): the maximum price at which the
# fund still clears its LP net IRR, after debt service, the AM fee and
# the promote. 15% is the fund's stated LP net target — a different
# number from SOLVER_TARGET_IRR, measured on a different cash-flow
# stream, so the two are separate keys rather than one shared "target".
#
# Deliberately NOT in webapp.forms.override_key_registry, unlike
# SOLVER_TARGET_IRR. It is a levered input, and every levered input is
# per-deal only (CLAUDE.md key design decision 6) — the settings page
# edits the unlevered screen, which the levered lens must not move.
SOLVER_TARGET_LP_NET_IRR = 0.15

# ── Transaction Costs & Hold Period ─────────────────────────────────
# Round-trip friction. Omitting these overstates every unlevered IRR by
# roughly 30-60 bps, and the IRR gate is evaluated on that figure.
#
# These are DEFAULTS, not constants: transfer-tax states (PA, DE, NY,
# WA...) can push acquisition costs several times the national norm, and
# broker fees move with deal size. Both are per-deal editable from the
# assumptions form and from the settings editor.

TRANSACTION_COSTS = {
    "acquisition_closing_pct": 0.010,   # title, legal, diligence, transfer
    "disposition_cost_pct":    0.015,   # broker plus closing
}

# Hold period, in years. Drives the projection length everywhere — the
# static DCF, the sensitivity grid, the bisection solver, the value-add
# monthly engine, and the sale month written into the XLSM template.
DEFAULT_HOLD_YEARS = 5
HOLD_YEARS_RANGE = (1, 10)

# ── Capital Structure (Sources & Uses) ──────────────────────────────
# Inputs to model.returns_model.build_sources_uses. Deliberately plain
# module scalars rather than a dict added to
# webapp.services._PATCHED_DICTS: a patched dict is mutated IN PLACE for
# the duration of one deal's run, so anything resolving it outside that
# run's lock reads another deal's values — the bug item B shipped and the
# review caught. These travel as parameters instead, resolved once at the
# services boundary, which is the same lane DEFAULT_HOLD_YEARS and
# SOLVER_TARGET_IRR already occupy. Consequence: per-deal editable, not
# editable from the settings page.

# GP capital invested alongside the LPs, as a share of total equity.
# 10% is the market term recorded in docs/levered-waterfall-design.md;
# item E2 reads the same number for the waterfall's pari-passu tier.
GP_COINVEST_PCT = 0.10

# Upfront operating / working-capital reserve funded at close. Zero by
# default so no published return moves when this ships. NOT the
# `cap_reserve` expense benchmark, which is an annual OpEx line — same
# word, different thing, and confusing them is the obvious failure mode.
DEFAULT_OPERATING_RESERVE = 0.0
DEFAULT_OPERATING_RESERVE_BASIS = "amount"

# How the CapEx box is read (item H). "amount" reproduces the historical
# behavior exactly.
DEFAULT_CAPEX_BASIS = "amount"

# ── Debt Terms (item E1) ────────────────────────────────────────────
# Defaults for model.debt.DebtTerms — a senior fixed-rate acquisition
# loan on a stabilized storage asset. Market terms as of 2025-26, from
# docs/levered-waterfall-design.md: banks 65-75% LTV, 1.25x DSCR, 20-25yr
# amortization, ~5.5-6.5% fixed, step-down prepay, debt-yield floor 8-10%.
#
# Every value below is BANK paper, deliberately. The design doc describes
# three executions and they do not mix: CMBS is the one that amortizes
# over 30 years, and it pays for that with defeasance / yield-maintenance
# prepay rather than the step-down assumed at `exit_fee_pct` below. An
# earlier draft of this block took the 30-year CMBS amortization while
# quoting the bank line above it and assuming step-down prepay — a blend
# no lender offers, and a more favorable payment than either product
# actually gives. Change these as a SET, to one real execution.
#
# Out of _PATCHED_DICTS for the reason recorded above the capital block:
# a patched dict is mutated in place for one deal's run, so anything
# resolving it outside that run's lock reads another deal's terms. Debt
# terms travel as parameters, resolved once via
# model.debt.resolve_debt_terms.
#
# `rate` is the all-in rate. A floating-rate deal sets `index_rate` and
# `spread` instead and leaves `rate` None; there is deliberately no
# forward curve here, because a checked-in rate path is a hardcoded
# constant that goes stale.
#
# WIRED BY ITEM E3a. E1 left a note here reading "leverage must be
# OPT-IN per deal". The operator decided otherwise on 2026-08-01: the
# levered lens is ON by default, sized from this block, because the fund
# mandate is an LP NET IRR and a levered lens nobody switches on answers
# the question nobody asked. The note is rewritten rather than deleted so
# the reversal is visible to whoever reads this block next.
#
# What that costs, stated plainly: these defaults now price a real loan on
# every deal, so they are not a neutral placeholder any more. The unlevered
# screen is unaffected by design — financing costs stay out of
# `total_basis` (see the E3a plan) — but every LP net IRR in the app is
# computed from the numbers below until a deal overrides them in E3b.
DEBT_TERMS = {
    "loan_type": "senior_fixed",
    "rate": 0.0625,
    "index_rate": None,
    "spread": None,
    "amort_years": 25,        # bank paper; CMBS would be 30
    "io_months": 0,
    "term_years": 10,
    "max_ltv": 0.65,
    "min_dscr": 1.25,
    "min_debt_yield": 0.10,
    "orig_fee_pct": 0.01,     # 1 point at close — a use of funds
    "exit_fee_pct": 0.0,      # senior fixed uses step-down prepay, not an
                              # exit fee; bridge paper is where this bites
}

# ── Waterfall Terms (item E2) ───────────────────────────────────────
# Defaults for model.waterfall.WaterfallTerms — ONE tier: an 8%
# preferred return on unreturned capital, then a 20% promoted interest
# to the GP on the residual. Operator fund terms, recorded in
# docs/levered-waterfall-design.md. No catch-up, no clawback, no second
# hurdle; the tier COUNT is a scope decision, not a setting.
#
# `gp_coinvest_pct` is deliberately NOT a key here. It lives in the
# capital block above as GP_COINVEST_PCT, because
# model.returns_model.resolve_capital_structure already reads it for the
# Sources & Uses stack — a second copy is exactly the silent divergence
# the single-source-of-truth rule forbids.
#
# Four of these five values are open LPA questions (docs/scoped-backlog.md
# item E). They ship as stamped defaults: model.waterfall.assumption_stamp
# renders the resolved set, and no LP net IRR is displayed without it.
# `accrual_base="committed"` and `am_fee_treatment="netted_from_lp"` are
# real market conventions this module does NOT implement and will raise
# on, rather than quietly running the default.
#
# Out of _PATCHED_DICTS for the reason recorded above the capital block:
# a patched dict is mutated in place for one deal's run, so anything
# resolving it outside that run's lock reads another deal's terms.
# Waterfall terms travel as parameters, resolved once via
# model.waterfall.resolve_waterfall_terms.
#
# WIRED BY ITEM E3a via model.levered.build_levered_returns.
WATERFALL_TERMS = {
    "pref_rate": 0.08,
    "pref_compounding": "annual",   # "annual" | "simple" — ~19% of promote
    "ordering": "roc_first",        # only bites when the pref is simple
    "promote_split": 0.20,          # GP share of the LP-attributable residual
    "accrual_base": "contributed",  # contributed/unreturned, not committed
    "am_fee_treatment": "above_waterfall",   # a deal expense, charged by E3
    "catch_up": False,              # scoped out; True raises
}

# ── Which LPA questions have actually been read (item E4) ───────────
# The five conventions above shipped as BUILD DEFAULTS — plausible
# choices standing in for a document nobody had opened. `model.waterfall.
# assumption_stamp` prints all five beside every LP net IRR, and the rule
# is that a stamped figure is a labeled assumption, not a decision-grade
# number.
#
# This dict is the difference between the two. A key here means the LPA
# was read on that question and the value above is what it says — so the
# stamp can stop calling it "proposed". A question NOT here stays open,
# and the default is FAILING-open: adding a convention to WATERFALL_TERMS
# without adding it here leaves it correctly labelled as an assumption
# rather than silently inheriting someone else's confirmation.
#
# The value is the date the operator confirmed it, kept because "who
# said so and when" is the whole content of a confirmation. Do not add a
# key here from a plan doc, a design note, or an inference — only from
# the executed partnership agreement.
LPA_CONFIRMED = {
    # Operator confirmed 2026-08-09: the pref compounds annually. This is
    # the question worth the most — the design doc measured ~19% of GP
    # promote riding on it — and confirming it MOOTS `ordering` for free,
    # since ROC-before-pref only moves a dollar when the pref is simple.
    "pref_compounding": "2026-08-09",
}

# ── Asset-Management Fee (item E3a) ─────────────────────────────────
# The GP's 1% annual management fee. Charged ABOVE the waterfall — it
# reduces distributable cash before the LP/GP split — which is open LPA
# question 4's default, stamped on every run by
# model.levered.build_levered_returns.
#
# WHAT THE FEE IS CHARGED ON was left open by E2 on purpose: the design
# doc names the rate and never the base, and "committed equity",
# "invested capital" and "asset value" are all live conventions. The
# operator chose INVESTED EQUITY on 2026-08-01. On the E3a plan's oracle-A
# fixture the alternatives differ by ~2.4x ($41,600/yr on equity vs
# ~$100,000/yr on asset value), straight through to LP net IRR — so this
# is a number that decides an answer, not a formatting detail.
#
# MEASURED AT THE START OF EACH PERIOD, before that period's own capital
# call. That is not a rounding convention, it is what makes the fee
# computable: a shortfall triggers a call, the call raises invested
# equity, and an end-of-period base would raise the fee, which deepens
# the shortfall — a loop with no fixed point. It also matches the pref
# accrual in model.waterfall, which accrues on the START-of-period
# balance and does not accrue at period 0.
#
# Plain scalars passed as parameters, never a _PATCHED_DICTS entry — see
# the reason recorded above the capital block.
AM_FEE_PCT = 0.01
AM_FEE_BASE = "invested_equity"     # the only base implemented; others raise

# ── Partnership entity labels ───────────────────────────────────────
# Names written into the XLSM's partnership block (C253 / C254). Labels,
# not math. They live here because `output/template_writer.py` read
# GP_NAME from the environment, and an env var is not an assumption
# anybody can audit — the same objection that retired GP_EQUITY_SHARE,
# GP_AM_FEE_RATE and GP_PROMOTE_PCT in item E3b.
GP_ENTITY_NAME = os.environ.get("GP_NAME", "Marathon CRE")
LP_ENTITY_NAME = "LP Group"

# ── General Counsel gate on the investor summary (item G) ───────────
# The LP-facing summary is the one document this pipeline produces that
# is written FOR someone outside the firm, which edges it toward
# securities marketing. Item G recorded that the build is not blocked but
# the DISTRIBUTION is, behind the operator's General Counsel — and then
# left that gate living in a backlog paragraph and a code comment, which
# are the two places the analyst clicking "Investor Summary (.docx)"
# will never look.
#
# This flag is that gate, as state rather than prose. While it is False
# the document carries a notice on its own first line and the download
# button carries a caveat; flipping it to True removes both and leaves
# `_SUMMARY_LEGEND`, which is permanent and unconditional either way.
#
# **Flip this only on a real sign-off**, and record who cleared what and
# when in `docs/gc-review-investor-summary.md` — a boolean with no
# audit trail behind it is worth less than the comment it replaced. The
# wording GC reviews is `_SUMMARY_LEGEND` plus the section headings
# enumerated in that document; changing either after clearance puts the
# document back in front of counsel.
#
# Deliberately NOT settings-page editable and NOT a _PATCHED_DICTS entry:
# a legal clearance is not a per-deal underwriting assumption, and an
# analyst must not be able to clear it from the same screen that edits
# cap rates.
INVESTOR_SUMMARY_GC_CLEARED = False

# ── XLSM Underwriting Template Inputs (item E3b) ────────────────────
# Assumptions the .xlsm underwriting template asks for that the Python
# model has no equivalent of. They are here, and not literals inside
# `output/template_writer.py`, because a number the template picks for
# itself is a second underwriting opinion that nothing reconciles — the
# defect the transparency audit raised and item E3b closed.
#
# Every value below EQUALS the literal it replaced, so shipping this
# block moved no XLSM cell. Revisiting the values is item T's call, not
# this block's (scoped-backlog rule 1: behavior-preserving).
#
# `assumed_physical_occupancy` is the one that is not merely a template
# input: it is what gets underwritten when the CIM never states
# occupancy. `template_writer._physical_occupancy` logs a warning every
# time it is used, so a deal priced on an assumption instead of a fact
# says so — silence was the audit's actual complaint.
#
# Out of _PATCHED_DICTS for the reason recorded above the capital block:
# a patched dict is mutated IN PLACE for one deal's run, so anything
# resolving it outside that run's lock reads another deal's values.
XLSM_TEMPLATE_INPUTS = {
    "credit_loss_in_place":     0.01,    # G147 — % of potential income
    "credit_loss_stabilized":   0.01,    # I147
    "bank_fee_pct_in_place":    0.0100,  # G155 — merchant fees, % of EGR
    "bank_fee_pct_stabilized":  0.0125,  # I155
    "capex_start_month":        1,       # E30 — deferred-maintenance timing
    "capex_duration_months":    6,       # F30 — spread evenly over N months
    "assumed_physical_occupancy": 0.90,  # only when the CIM omits it
}

# ── Regional Expense Adjustments ──────────────────────────────────
# Multipliers applied to national EXPENSE_BENCHMARKS by region.
# Derived from ISS Self-Storage Expense Guidebook and SSA Operating
# Survey data. States not listed default to 1.0 (national average).
#
# Each region has multipliers for expense categories where costs
# deviate meaningfully from the national average. Categories not
# listed default to 1.0.

EXPENSE_REGIONS = {
    # State → region name mapping
    "state_to_region": {
        # Sun Belt
        "TX": "sun_belt", "FL": "sun_belt", "AZ": "sun_belt",
        "NV": "sun_belt", "GA": "sun_belt", "SC": "sun_belt",
        "NC": "sun_belt", "TN": "sun_belt",
        # Northeast
        "NY": "northeast", "NJ": "northeast", "CT": "northeast",
        "MA": "northeast", "PA": "northeast", "NH": "northeast",
        "VT": "northeast", "ME": "northeast", "RI": "northeast",
        "MD": "northeast", "DE": "northeast",
        # Midwest
        "OH": "midwest", "MI": "midwest", "IL": "midwest",
        "IN": "midwest", "WI": "midwest", "MN": "midwest",
        "IA": "midwest", "MO": "midwest", "KS": "midwest",
        "NE": "midwest", "ND": "midwest", "SD": "midwest",
        # Mountain West
        "CO": "mountain", "UT": "mountain", "ID": "mountain",
        "MT": "mountain", "WY": "mountain", "NM": "mountain",
        # Pacific
        "CA": "pacific", "WA": "pacific", "OR": "pacific", "HI": "pacific",
        # Southeast (lower cost)
        "AL": "southeast", "MS": "southeast", "LA": "southeast",
        "AR": "southeast", "KY": "southeast", "WV": "southeast",
        "VA": "southeast", "OK": "southeast",
    },
    # Region → category multipliers (1.0 = national average)
    "regions": {
        "sun_belt": {
            "insurance":    1.50,  # Hurricane/wind exposure
            "utilities":    1.25,  # Higher A/C costs
            "payroll":      0.85,  # Lower wage markets
            "repairs":      0.90,  # Less freeze/thaw damage
            "advertising":  1.10,  # Competitive markets
            "ga":           0.90,
        },
        "northeast": {
            "insurance":    1.15,  # Higher rebuild costs
            "utilities":    1.20,  # Heating costs
            "payroll":      1.40,  # High wage markets
            "repairs":      1.25,  # Freeze/thaw, snow removal
            "advertising":  1.05,
            "ga":           1.20,  # Higher professional costs
        },
        "midwest": {
            "insurance":    0.90,
            "utilities":    1.10,  # Moderate heating
            "payroll":      0.90,
            "repairs":      1.10,  # Freeze/thaw
            "advertising":  0.85,
            "ga":           0.90,
        },
        "mountain": {
            "insurance":    0.80,  # Low catastrophe risk
            "utilities":    0.90,
            "payroll":      0.85,
            "repairs":      1.00,
            "advertising":  0.80,  # Less competition
            "ga":           0.85,
        },
        "pacific": {
            "insurance":    1.30,  # Earthquake/wildfire
            "utilities":    1.15,
            "payroll":      1.45,  # Highest wage markets
            "repairs":      1.10,
            "advertising":  1.15,  # Very competitive
            "ga":           1.25,
        },
        "southeast": {
            "insurance":    1.20,  # Some storm exposure
            "utilities":    1.10,  # A/C
            "payroll":      0.75,  # Lowest wage markets
            "repairs":      0.85,
            "advertising":  0.75,
            "ga":           0.80,
        },
    },
}


def get_regional_benchmarks(state: str) -> dict:
    """
    Return expense benchmarks adjusted for regional factors.

    Args:
        state: 2-letter state code (e.g., "TX")

    Returns:
        dict with same keys as EXPENSE_BENCHMARKS, values adjusted
        by regional multipliers. Property tax uses STATE_PROPERTY_TAX_MULTIPLIER
        (already handled in financials.py), so it's NOT adjusted here.
    """
    adjusted = {}
    region_name = EXPENSE_REGIONS["state_to_region"].get(state.upper(), None)
    region_mults = EXPENSE_REGIONS["regions"].get(region_name, {}) if region_name else {}

    for key, (low, high) in EXPENSE_BENCHMARKS.items():
        # Property tax handled separately via STATE_PROPERTY_TAX_MULTIPLIER
        if key == "property_tax":
            adjusted[key] = (low, high)
            continue
        # Percentage-based benchmarks (mgmt fee, opex ratios) stay national
        if key in ("mgmt_fee_pct", "opex_revenue_ratio"):
            adjusted[key] = (low, high)
            continue

        mult = region_mults.get(key, 1.0)
        adjusted[key] = (round(low * mult, 2), round(high * mult, 2))

    # Recompute total_opex from individual line items
    from registry import EXPENSE_KEYS
    total_low = sum(adjusted[k][0] for k in EXPENSE_KEYS)
    total_high = sum(adjusted[k][1] for k in EXPENSE_KEYS)
    adjusted["total_opex"] = (round(total_low, 2), round(total_high, 2))

    return adjusted
