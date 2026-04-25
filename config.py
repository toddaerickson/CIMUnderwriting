"""
CIM Analyst — Hard-coded investment criteria, expense benchmarks,
replacement cost benchmarks, and scenario defaults.
"""

import os

# ── Go / No-Go Gate Thresholds ──────────────────────────────────────

GATES = {
    "population_3mi": 50_000,
    "min_occupancy": 0.85,
    "max_noi_step_up": 0.15,       # CIM Yr1 NOI vs TTM — flag if exceeded
    "min_irr_5yr": 0.10,
    "min_yield_on_cost": 0.08,
}

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
        "exit_cap":        0.085,
    },
    ScenarioType.BASE: {
        "yr1_noi_bump":    0.05,
        "stabilized_occ":  0.88,
        "rev_cagr_yr1_3":  0.025,
        "rev_cagr_yr4_5":  0.025,
        "exp_growth":      0.03,
        "exit_cap":        0.075,
    },
    ScenarioType.BULL: {
        "yr1_noi_bump":    0.10,
        "stabilized_occ":  0.93,
        "rev_cagr_yr1_3":  0.04,
        "rev_cagr_yr4_5":  0.035,
        "exp_growth":      0.03,
        "exit_cap":        0.065,
    },
}

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
        "exit_cap": 0.075,
        "expense_growth": 0.03,
    },
    ScenarioType.BASE: {
        "target_occupancy": 0.88,        # realistic stabilization
        "months_to_stabilize": 24,
        "rent_growth_to_market": 1.00,   # close full rent gap
        "post_stabilize_rev_growth": 0.03,
        "exit_cap": 0.065,
        "expense_growth": 0.03,
    },
    ScenarioType.BULL: {
        "target_occupancy": 0.92,        # optimistic lease-up
        "months_to_stabilize": 18,
        "rent_growth_to_market": 1.00,
        "post_stabilize_rev_growth": 0.04,
        "exit_cap": 0.055,
        "expense_growth": 0.025,
    },
}

VALUE_ADD_TRIGGERS = {
    "max_occupancy": 0.85,        # below this → value-add deal
    "min_rent_gap_pct": 0.10,     # in-place rent 10%+ below market
}

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
