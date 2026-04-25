"""
Assumptions Editor — full-page tabbed form for reviewing and editing
all CIM data and analysis assumptions before running the pipeline.

Replaces the old cim_data_editor.py with a richer, tab-ordered layout.
"""

import pandas as pd
import streamlit as st

from gui.config_manager import get_config
from registry import ScenarioType


def render_assumptions_editor(cim_data, extraction_report: dict) -> dict:
    """
    Render the full assumptions editor with 6 tabs.

    Args:
        cim_data: CIMData dataclass (extracted values)
        extraction_report: dict with 'missing' list of field names

    Returns:
        dict with keys: cim_overrides, scenario_overrides,
        va_scenario_overrides, solver_target_irr
    """
    if not extraction_report:
        extraction_report = {}
    missing = set(extraction_report.get("missing", []))
    missing_count = len(missing)

    required_missing = missing & REQUIRED_FIELDS
    if missing_count > 0:
        msg = f"{missing_count} fields not found in CIM."
        if required_missing:
            msg += (f" **{len(required_missing)} required** for IRR modeling"
                    " (marked with :red[**!**]).")
        st.info(msg)

    tabs = st.tabs([
        "Property",
        "Size & Occupancy",
        "Unit Mix",
        "Income & Expenses",
        "Scenarios",
        "Demographics",
    ])

    overrides = {}

    with tabs[0]:
        overrides.update(_render_property_tab(cim_data, missing))

    with tabs[1]:
        size_ov, repl_cost_ov = _render_size_tab(cim_data, missing)
        overrides.update(size_ov)

    with tabs[2]:
        unit_mix = _render_unit_mix_tab(cim_data)
        if unit_mix:
            overrides["unit_mix"] = unit_mix

    with tabs[3]:
        overrides.update(_render_income_expenses_tab(cim_data, missing))

    with tabs[4]:
        scenario_overrides, va_overrides, target_irr = _render_scenarios_tab()

    with tabs[5]:
        overrides.update(_render_demographics_tab(cim_data, missing))

    return {
        "cim_overrides": overrides,
        "scenario_overrides": scenario_overrides,
        "va_scenario_overrides": va_overrides,
        "solver_target_irr": target_irr,
        "replacement_cost_overrides": repl_cost_ov,
    }


# ── Tab 1: Property ──────────────────────────────────────────────────

def _render_property_tab(cim_data, missing: set) -> dict:
    o = {}

    # Row 1: Name, Address
    c1, c2 = st.columns(2)
    with c1:
        v = _text("Property Name", cim_data.property_name, "property_name", missing)
        if v:
            o["property_name"] = v
    with c2:
        v = _text("Address", cim_data.address, "address", missing)
        if v:
            o["address"] = v

    # Row 2: City, State, MSA
    c1, c2, c3 = st.columns(3)
    with c1:
        v = _text("City", cim_data.city, "city", missing)
        if v:
            o["city"] = v
    with c2:
        v = _text("State (2-letter)", cim_data.state, "state", missing, max_chars=2)
        if v:
            o["state"] = v.upper()
    with c3:
        v = _text("MSA", cim_data.msa, "msa", missing)
        if v:
            o["msa"] = v

    # Row 3: Year Built, Year Expanded, Acreage
    c1, c2, c3 = st.columns(3)
    with c1:
        v = _int("Year Built", cim_data.year_built, "year_built", missing,
                 min_value=0, max_value=2030)
        if v and v >= 1950:
            o["year_built"] = v
    with c2:
        v = _int("Year Expanded", cim_data.year_expanded, "year_expanded", missing,
                 min_value=0, max_value=2030)
        if v and v > 0:
            o["year_expanded"] = v
    with c3:
        v = _num("Acreage", cim_data.acreage, "acreage", missing,
                 step=0.1, fmt="%.2f")
        if v and v > 0:
            o["acreage"] = v

    return o


# ── Tab 2: Size & Occupancy ─────────────────────────────────────────

def _render_size_tab(cim_data, missing: set) -> tuple[dict, dict]:
    """Returns (cim_overrides, replacement_cost_overrides)."""
    o = {}

    # Row 1: NRSF, Total Units, CC%
    c1, c2, c3 = st.columns(3)
    with c1:
        v = _num("NRSF (Net Rentable SF)", cim_data.nrsf, "nrsf", missing,
                 step=1000.0, fmt="%.0f")
        if v and v > 0:
            o["nrsf"] = v
    with c2:
        v = _int("Total Units", cim_data.total_units, "total_units", missing)
        if v and v > 0:
            o["total_units"] = v
    with c3:
        v = _pct("CC", cim_data.cc_pct, "cc_pct", missing)
        o["cc_pct"] = v

    # Row 2: Occupancy
    c1, c2 = st.columns(2)
    with c1:
        v = _pct("Physical Occupancy", cim_data.physical_occupancy,
                 "physical_occupancy", missing)
        if v and v > 0:
            o["physical_occupancy"] = v
    with c2:
        v = _pct("Economic Occupancy", cim_data.economic_occupancy,
                 "economic_occupancy", missing)
        if v and v > 0:
            o["economic_occupancy"] = v

    # Facility SF Breakdown
    st.divider()
    st.caption("Facility SF Breakdown (for replacement cost)")

    c1, c2 = st.columns(2)
    with c1:
        v = _num("SS Drive-Up SF", cim_data.ss_driveup_sf, "ss_driveup_sf", missing,
                 step=1000.0, fmt="%.0f")
        if v and v > 0:
            o["ss_driveup_sf"] = v
        v = _num("BRV Enclosed SF", cim_data.brv_enclosed_sf, "brv_enclosed_sf", missing,
                 step=1000.0, fmt="%.0f")
        if v and v > 0:
            o["brv_enclosed_sf"] = v
    with c2:
        v = _num("SS Enclosed SF", cim_data.ss_enclosed_sf, "ss_enclosed_sf", missing,
                 step=1000.0, fmt="%.0f")
        if v and v > 0:
            o["ss_enclosed_sf"] = v
        v = _num("BRV Covered SF", cim_data.brv_covered_sf, "brv_covered_sf", missing,
                 step=1000.0, fmt="%.0f")
        if v and v > 0:
            o["brv_covered_sf"] = v

    v = _num("BRV Open Parking SF", cim_data.brv_open_sf, "brv_open_sf", missing,
             step=1000.0, fmt="%.0f")
    if v and v > 0:
        o["brv_open_sf"] = v

    # Replacement Cost Benchmarks (per-deal overrides)
    repl = _render_replacement_cost_overrides()

    return o, repl


def _render_replacement_cost_overrides() -> dict:
    """Editable replacement cost benchmarks for this deal."""
    from config import REPLACEMENT_COST, FACILITY_TYPES

    st.divider()
    st.caption("Replacement Cost Benchmarks ($/SF — override per deal)")

    overrides = {}

    # Hard cost + site work per facility type
    for hard_key, site_key, display_name in FACILITY_TYPES:
        hard_low, hard_high = REPLACEMENT_COST[hard_key]
        site_low, site_high = REPLACEMENT_COST[site_key]

        st.markdown(f"**{display_name}**")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            hl = st.number_input("Hard $/SF Low", value=float(hard_low),
                                 step=5.0, format="%.0f",
                                 key=f"rc_{hard_key}_low")
        with c2:
            hh = st.number_input("Hard $/SF High", value=float(hard_high),
                                 step=5.0, format="%.0f",
                                 key=f"rc_{hard_key}_high")
        with c3:
            sl = st.number_input("Site $/SF Low", value=float(site_low),
                                 step=1.0, format="%.0f",
                                 key=f"rc_{site_key}_low")
        with c4:
            sh = st.number_input("Site $/SF High", value=float(site_high),
                                 step=1.0, format="%.0f",
                                 key=f"rc_{site_key}_high")

        overrides[hard_key] = (hl, hh)
        overrides[site_key] = (sl, sh)

    # Soft costs & dev profit
    st.markdown("**Soft Costs & Developer Profit**")
    soft_low, soft_high = REPLACEMENT_COST["soft_cost_pct"]
    dev_low, dev_high = REPLACEMENT_COST["dev_profit_pct"]

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        scl = st.number_input("Soft Cost % Low", value=soft_low * 100,
                              step=1.0, format="%.0f",
                              key="rc_soft_low")
    with c2:
        sch = st.number_input("Soft Cost % High", value=soft_high * 100,
                              step=1.0, format="%.0f",
                              key="rc_soft_high")
    with c3:
        dpl = st.number_input("Dev Profit % Low", value=dev_low * 100,
                              step=1.0, format="%.0f",
                              key="rc_dev_low")
    with c4:
        dph = st.number_input("Dev Profit % High", value=dev_high * 100,
                              step=1.0, format="%.0f",
                              key="rc_dev_high")

    overrides["soft_cost_pct"] = (scl / 100, sch / 100)
    overrides["dev_profit_pct"] = (dpl / 100, dph / 100)

    return overrides


# ── Tab 3: Unit Mix ──────────────────────────────────────────────────

def _render_unit_mix_tab(cim_data) -> list | None:
    st.caption("Edit unit mix below. Use + to add rows, checkbox to delete.")

    # Build initial DataFrame from CIMData
    rows = []
    for u in (cim_data.unit_mix or []):
        rows.append({
            "Label": str(u.size_label or ""),
            "Count": int(u.count or 0),
            "SF": int(u.sf or 0),
            "Rent/Mo": float(u.rate or 0.0),
            "CC": bool(u.climate_controlled),
        })
    if not rows:
        rows.append({"Label": "", "Count": 0, "SF": 0, "Rent/Mo": 0.0, "CC": False})

    df = pd.DataFrame(rows)

    edited = st.data_editor(
        df,
        num_rows="dynamic",
        column_config={
            "Label": st.column_config.TextColumn("Unit Size", width="small"),
            "Count": st.column_config.NumberColumn("Count", min_value=0, step=1),
            "SF": st.column_config.NumberColumn("Sq Ft", min_value=0, step=25),
            "Rent/Mo": st.column_config.NumberColumn(
                "Rent/Mo ($)", min_value=0.0, format="$%.2f"
            ),
            "CC": st.column_config.CheckboxColumn("Climate Ctrl"),
        },
        use_container_width=True,
        key="unit_mix_editor",
    )

    # Summary metrics
    valid = edited[edited["Count"] > 0]
    if not valid.empty:
        total_units = int(valid["Count"].sum())
        total_sf = (valid["Count"] * valid["SF"]).sum()
        total_rent = (valid["Count"] * valid["Rent/Mo"]).sum()
        avg_psf = total_rent / total_sf * 12 if total_sf > 0 else 0

        c1, c2, c3 = st.columns(3)
        c1.metric("Total Units", f"{total_units:,}")
        c2.metric("Total SF", f"{total_sf:,.0f}")
        c3.metric("Avg Rent/SF/Yr", f"${avg_psf:,.2f}")

    # Convert back to override format
    result = []
    for _, row in edited.iterrows():
        if row["Count"] > 0:
            result.append({
                "size_label": row["Label"],
                "count": int(row["Count"]),
                "sf": float(row["SF"]),
                "rate": float(row["Rent/Mo"]),
                "climate_controlled": bool(row["CC"]),
            })

    return result if result else None


# ── Tab 4: Income & Expenses ────────────────────────────────────────

def _render_income_expenses_tab(cim_data, missing: set) -> dict:
    o = {}

    left, right = st.columns([1, 1])

    with left:
        st.markdown("**Pricing**")
        c1, c2 = st.columns(2)
        with c1:
            v = _num("Asking Price ($)", cim_data.asking_price, "asking_price",
                     missing, step=100000.0, fmt="%.0f")
            if v and v > 0:
                o["asking_price"] = v
        with c2:
            v = _num("CapEx Estimate ($)", cim_data.capex_estimate, "capex_estimate",
                     missing, step=10000.0, fmt="%.0f")
            if v and v > 0:
                o["capex_estimate"] = v

        st.markdown("**Income (TTM)**")
        c1, c2 = st.columns(2)
        with c1:
            v = _num("Gross Potential Rent ($)", cim_data.ttm_gpr, "ttm_gpr",
                     missing, step=10000.0, fmt="%.0f")
            if v and v > 0:
                o["ttm_gpr"] = v
            v = _num("Other Income ($)", cim_data.other_income, "other_income",
                     missing, step=1000.0, fmt="%.0f")
            if v and v > 0:
                o["other_income"] = v
        with c2:
            v = _num("Effective Gross Revenue ($)", cim_data.ttm_egr, "ttm_egr",
                     missing, step=10000.0, fmt="%.0f")
            if v and v > 0:
                o["ttm_egr"] = v
            v = _num("Total Revenue ($)", cim_data.ttm_total_revenue,
                     "ttm_total_revenue", missing, step=10000.0, fmt="%.0f")
            if v and v > 0:
                o["ttm_total_revenue"] = v

        st.markdown("**Expenses & NOI**")
        c1, c2 = st.columns(2)
        with c1:
            v = _num("Total Expenses ($)", cim_data.ttm_total_expenses,
                     "ttm_total_expenses", missing, step=10000.0, fmt="%.0f")
            if v and v > 0:
                o["ttm_total_expenses"] = v
            v = _num("CIM Year 1 NOI ($)", cim_data.cim_yr1_noi, "cim_yr1_noi",
                     missing, step=10000.0, fmt="%.0f")
            if v and v > 0:
                o["cim_yr1_noi"] = v
        with c2:
            v = _num("TTM NOI ($)", cim_data.ttm_noi, "ttm_noi",
                     missing, step=10000.0, fmt="%.0f")
            if v and v > 0:
                o["ttm_noi"] = v
            v = _pct("Mgmt Fee", cim_data.mgmt_fee_pct, "mgmt_fee_pct", missing)
            if v and v > 0:
                o["mgmt_fee_pct"] = v

    with right:
        _render_expense_benchmarks(cim_data)

    return o


def _render_expense_benchmarks(cim_data):
    """Show read-only expense benchmark comparison."""
    st.markdown("**Expense Benchmarks**")
    st.caption("Reference ranges adjusted for state/region")

    state = (cim_data.state or "").upper()
    nrsf = cim_data.nrsf or 1

    from config import get_regional_benchmarks, EXPENSE_BENCHMARKS
    benchmarks = get_regional_benchmarks(state) if state else EXPENSE_BENCHMARKS

    from registry import EXPENSE_CATEGORIES
    rows = []
    # Build CIM expense lookup
    cim_exp = {}
    for line in (cim_data.expense_lines or []):
        if line.t12 and nrsf > 0:
            cim_exp[line.label.lower()] = line.t12 / nrsf

    for cat in EXPENSE_CATEGORIES:
        low, high = benchmarks.get(cat.key, (0, 0))
        # Try to find matching CIM value
        cim_val = None
        for kw in cat.parse_keywords:
            for label, val in cim_exp.items():
                if kw in label:
                    cim_val = val
                    break
            if cim_val:
                break

        rows.append({
            "Category": cat.display_name,
            "CIM $/SF": f"${cim_val:.2f}" if cim_val else "—",
            "Low": f"${low:.2f}",
            "High": f"${high:.2f}",
        })

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        height=330,
    )


# ── Tab 5: Scenarios ────────────────────────────────────────────────

def _render_scenarios_tab() -> tuple[dict, dict, float]:
    """Returns (scenario_overrides, va_overrides, target_irr)."""

    defaults = get_config("SCENARIO_DEFAULTS")
    scenarios = {}

    st.markdown("**Bear / Base / Bull Assumptions**")
    bear, base, bull = st.columns(3)
    cols = {ScenarioType.BEAR: bear, ScenarioType.BASE: base, ScenarioType.BULL: bull}

    # (key, label, step_pct, format) — all are percentages displayed as whole numbers
    param_defs = [
        ("yr1_noi_bump", "Yr1 NOI Bump (%)", 1.0, "%.1f"),
        ("stabilized_occ", "Stabilized Occ (%)", 1.0, "%.1f"),
        ("rev_cagr_yr1_3", "Rev CAGR Yr 1-3 (%)", 0.5, "%.1f"),
        ("rev_cagr_yr4_5", "Rev CAGR Yr 4-5 (%)", 0.5, "%.1f"),
        ("exp_growth", "Expense Growth (%)", 0.5, "%.1f"),
        ("exit_cap", "Exit Cap Rate (%)", 0.25, "%.2f"),
    ]

    for scenario_type, col in cols.items():
        with col:
            st.caption(scenario_type.value.upper())
            d = defaults.get(scenario_type, {})
            s = {}
            for key, label, step, fmt in param_defs:
                raw = float(d.get(key, 0))
                val = st.number_input(
                    label,
                    value=raw * 100,
                    step=step,
                    format=fmt,
                    key=f"scen_{scenario_type.value}_{key}",
                )
                s[key] = val / 100.0  # convert back to decimal
            scenarios[scenario_type] = s

    # Value-Add section
    with st.expander("Value-Add Assumptions", expanded=False):
        va_defaults = get_config("VALUE_ADD_SCENARIOS")
        va_scenarios = {}

        # is_pct: True = display as whole %, False = display as-is
        va_params = [
            ("target_occupancy", "Target Occ (%)", 1.0, "%.1f", True),
            ("months_to_stabilize", "Months to Stabilize", 1.0, "%.0f", False),
            ("rent_growth_to_market", "Rent Growth to Mkt (%)", 5.0, "%.0f", True),
            ("post_stabilize_rev_growth", "Post-Stab Rev Grw (%)", 0.5, "%.1f", True),
            ("exit_cap", "Exit Cap Rate (%)", 0.25, "%.2f", True),
            ("expense_growth", "Expense Growth (%)", 0.5, "%.1f", True),
        ]

        vb, vba, vbu = st.columns(3)
        va_cols = {ScenarioType.BEAR: vb, ScenarioType.BASE: vba, ScenarioType.BULL: vbu}

        for scenario_type, col in va_cols.items():
            with col:
                st.caption(f"VA {scenario_type.value.upper()}")
                d = va_defaults.get(scenario_type, {})
                s = {}
                for key, label, step, fmt, is_pct in va_params:
                    raw = float(d.get(key, 0))
                    display = raw * 100 if is_pct else raw
                    val = st.number_input(
                        label,
                        value=display,
                        step=step,
                        format=fmt,
                        key=f"va_{scenario_type.value}_{key}",
                    )
                    s[key] = val / 100.0 if is_pct else val
                va_scenarios[scenario_type] = s

    # Solver target
    st.divider()
    target_irr_pct = st.number_input(
        "Solver Target IRR (%)",
        value=float(get_config("SOLVER_TARGET_IRR") or 0.10) * 100,
        step=0.5,
        format="%.1f",
        key="solver_target_irr",
    )
    target_irr = target_irr_pct / 100.0

    return scenarios, va_scenarios, target_irr


# ── Tab 6: Demographics ─────────────────────────────────────────────

def _render_demographics_tab(cim_data, missing: set) -> dict:
    o = {}

    # Row 1: Population
    c1, c2, c3 = st.columns(3)
    with c1:
        v = _int("Population 1-mi", cim_data.population_1mi, "population_1mi",
                 missing, step=1000)
        if v and v > 0:
            o["population_1mi"] = v
    with c2:
        v = _int("Population 3-mi", cim_data.population_3mi, "population_3mi",
                 missing, step=1000)
        if v and v > 0:
            o["population_3mi"] = v
    with c3:
        v = _int("Population 5-mi", cim_data.population_5mi, "population_5mi",
                 missing, step=1000)
        if v and v > 0:
            o["population_5mi"] = v

    # Row 2: HHI, Market rent
    c1, c2 = st.columns(2)
    with c1:
        v = _num("Median HHI 3-mi ($)", cim_data.median_hhi_3mi, "median_hhi_3mi",
                 missing, step=1000.0, fmt="%.0f")
        if v and v > 0:
            o["median_hhi_3mi"] = v
    with c2:
        v = _num("Market Rent $/SF/mo", cim_data.market_rent_psf, "market_rent_psf",
                 missing, step=0.05, fmt="%.2f")
        if v and v > 0:
            o["market_rent_psf"] = v

    return o


# ── Required Fields (must be populated for IRR modeling) ─────────────

REQUIRED_FIELDS = {
    "asking_price",
    "nrsf",
    "total_units",
    "ttm_noi",
    "physical_occupancy",
    "state",
    "ttm_egr",
}


# ── Input Helpers ────────────────────────────────────────────────────

def _label(label: str, field_name: str, missing: set) -> str:
    """Add red exclamation to label if field is required and missing."""
    if field_name in missing and field_name in REQUIRED_FIELDS:
        return f":red[**!**] {label}"
    return label


def _help_text(field_name: str, missing: set) -> str | None:
    """Return help tooltip if field is missing from extraction."""
    if field_name not in missing:
        return None
    if field_name in REQUIRED_FIELDS:
        return "Required for IRR modeling \u2014 enter manually"
    return "Not found in CIM \u2014 enter manually"


def _text(label: str, value, field_name: str, missing: set, **kwargs) -> str:
    return st.text_input(
        _label(label, field_name, missing),
        value=value or "",
        help=_help_text(field_name, missing),
        **kwargs,
    )


def _num(label: str, value, field_name: str, missing: set,
         min_value: float = 0.0, max_value: float = None,
         step: float = 1.0, fmt: str = "%.2f") -> float:
    kw = dict(min_value=min_value, step=step, format=fmt,
              help=_help_text(field_name, missing))
    if max_value is not None:
        kw["max_value"] = max_value
    return st.number_input(_label(label, field_name, missing),
                           value=float(value or 0), **kw)


def _pct(label: str, value, field_name: str, missing: set,
         step: float = 0.5, fmt: str = "%.1f",
         max_pct: float = 100.0) -> float:
    """Percentage input: user enters whole numbers (e.g. 6 for 6%), returns decimal (0.06)."""
    display_val = float(value or 0) * 100
    kw = dict(min_value=0.0, max_value=max_pct, step=step, format=fmt,
              help=_help_text(field_name, missing))
    v = st.number_input(_label(f"{label} (%)", field_name, missing),
                        value=display_val, **kw)
    return v / 100.0


def _int(label: str, value, field_name: str, missing: set,
         min_value: int = 0, max_value: int = None, step: int = 1) -> int:
    kw = dict(min_value=min_value, step=step,
              help=_help_text(field_name, missing))
    if max_value is not None:
        kw["max_value"] = max_value
    return st.number_input(_label(label, field_name, missing),
                           value=int(value or 0), **kw)
