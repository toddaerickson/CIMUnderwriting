"""Reusable config section editors for the Settings page."""

import streamlit as st
from gui.config_manager import get_config, set_config


def edit_gates():
    """Editor for GATES thresholds."""
    gates = get_config("GATES").copy()
    col1, col2 = st.columns(2)
    with col1:
        gates["population_3mi"] = st.number_input(
            "Min Population (3-mi radius)", value=gates["population_3mi"],
            min_value=0, step=5000)
        gates["min_occupancy"] = st.number_input(
            "Min Physical Occupancy", value=gates["min_occupancy"],
            min_value=0.0, max_value=1.0, step=0.01, format="%.2f")
        gates["max_noi_step_up"] = st.number_input(
            "Max CIM Yr1 NOI Step-Up", value=gates["max_noi_step_up"],
            min_value=0.0, max_value=1.0, step=0.01, format="%.2f")
    with col2:
        gates["min_irr_5yr"] = st.number_input(
            "Min 5-Year IRR", value=gates["min_irr_5yr"],
            min_value=0.0, max_value=0.50, step=0.005, format="%.3f")
        gates["min_yield_on_cost"] = st.number_input(
            "Min Yield on Cost", value=gates["min_yield_on_cost"],
            min_value=0.0, max_value=0.50, step=0.005, format="%.3f")
    set_config("GATES", gates)


def edit_expense_benchmarks():
    """Editor for EXPENSE_BENCHMARKS."""
    bench = get_config("EXPENSE_BENCHMARKS").copy()

    labels = {
        "property_tax": "Property Tax ($/NRSF)",
        "insurance": "Insurance ($/NRSF)",
        "utilities": "Utilities ($/NRSF)",
        "repairs": "R&M ($/NRSF)",
        "advertising": "Advertising ($/NRSF)",
        "payroll": "Payroll ($/NRSF)",
        "ga": "G&A ($/NRSF)",
        "mgmt_fee_pct": "Mgmt Fee (% of EGR)",
        "cap_reserve": "Capital Reserve ($/NRSF)",
        "total_opex": "Total OpEx ($/NRSF)",
        "opex_revenue_ratio": "OpEx/Revenue Ratio",
    }

    for key, label in labels.items():
        low, high = bench[key]
        col1, col2, col3 = st.columns([0.4, 0.3, 0.3])
        with col1:
            st.text(label)
        with col2:
            new_low = st.number_input(f"{key}_low", value=low, label_visibility="collapsed",
                                      step=0.01, format="%.2f", key=f"bench_{key}_low")
        with col3:
            new_high = st.number_input(f"{key}_high", value=high, label_visibility="collapsed",
                                       step=0.01, format="%.2f", key=f"bench_{key}_high")
        bench[key] = (new_low, new_high)

    set_config("EXPENSE_BENCHMARKS", bench)


def edit_replacement_cost():
    """Editor for REPLACEMENT_COST."""
    repl = get_config("REPLACEMENT_COST").copy()

    st.markdown("**Hard Cost per SF**")
    items = [
        ("ss_driveup_per_sf", "Self-Storage Drive-Up"),
        ("ss_enclosed_per_sf", "Self-Storage Enclosed"),
        ("brv_enclosed_per_sf", "Boat/RV Enclosed"),
        ("brv_covered_per_sf", "Boat/RV Covered"),
        ("brv_open_per_sf", "Boat/RV Open Parking"),
    ]
    for key, label in items:
        low, high = repl[key]
        col1, col2, col3 = st.columns([0.4, 0.3, 0.3])
        with col1:
            st.text(label)
        with col2:
            new_low = st.number_input(f"Low", value=float(low), step=1.0, format="%.0f",
                                      key=f"repl_{key}_low")
        with col3:
            new_high = st.number_input(f"High", value=float(high), step=1.0, format="%.0f",
                                       key=f"repl_{key}_high")
        repl[key] = (new_low, new_high)

    st.markdown("**Site Work per SF**")
    site_items = [
        ("ss_driveup_site_per_sf", "Self-Storage Drive-Up"),
        ("ss_enclosed_site_per_sf", "Self-Storage Enclosed"),
        ("brv_enclosed_site_per_sf", "Boat/RV Enclosed"),
        ("brv_covered_site_per_sf", "Boat/RV Covered"),
        ("brv_open_site_per_sf", "Boat/RV Open Parking"),
    ]
    for key, label in site_items:
        low, high = repl[key]
        col1, col2, col3 = st.columns([0.4, 0.3, 0.3])
        with col1:
            st.text(label)
        with col2:
            new_low = st.number_input(f"Low", value=float(low), step=1.0, format="%.0f",
                                      key=f"repl_{key}_low")
        with col3:
            new_high = st.number_input(f"High", value=float(high), step=1.0, format="%.0f",
                                       key=f"repl_{key}_high")
        repl[key] = (new_low, new_high)

    st.markdown("**Soft Costs & Developer Profit**")
    col1, col2 = st.columns(2)
    with col1:
        low, high = repl["soft_cost_pct"]
        new_low = st.number_input("Soft Cost % Low", value=low, step=0.01, format="%.2f",
                                  key="repl_soft_low")
        new_high = st.number_input("Soft Cost % High", value=high, step=0.01, format="%.2f",
                                   key="repl_soft_high")
        repl["soft_cost_pct"] = (new_low, new_high)
    with col2:
        low, high = repl["dev_profit_pct"]
        new_low = st.number_input("Dev Profit % Low", value=low, step=0.01, format="%.2f",
                                  key="repl_dev_low")
        new_high = st.number_input("Dev Profit % High", value=high, step=0.01, format="%.2f",
                                   key="repl_dev_high")
        repl["dev_profit_pct"] = (new_low, new_high)

    # Keep legacy aliases in sync
    repl["non_cc_per_sf"] = repl["ss_driveup_per_sf"]
    repl["cc_per_sf"] = repl["ss_enclosed_per_sf"]
    repl["site_work_per_sf"] = repl["ss_driveup_site_per_sf"]

    set_config("REPLACEMENT_COST", repl)


def edit_scenarios():
    """Editor for SCENARIO_DEFAULTS."""
    from registry import ScenarioType
    scenarios = get_config("SCENARIO_DEFAULTS").copy()

    for scen_type in ScenarioType:
        st.markdown(f"**{scen_type.value.title()} Case**")
        params = scenarios[scen_type].copy()
        col1, col2, col3 = st.columns(3)
        with col1:
            params["yr1_noi_bump"] = st.number_input(
                "Yr1 NOI Bump", value=params["yr1_noi_bump"],
                step=0.01, format="%.2f", key=f"scen_{scen_type.value}_noi_bump")
            params["stabilized_occ"] = st.number_input(
                "Stabilized Occupancy", value=params["stabilized_occ"],
                step=0.01, format="%.2f", key=f"scen_{scen_type.value}_occ")
        with col2:
            params["rev_cagr_yr1_3"] = st.number_input(
                "Rev CAGR Yr 1-3", value=params["rev_cagr_yr1_3"],
                step=0.005, format="%.3f", key=f"scen_{scen_type.value}_rev13")
            params["rev_cagr_yr4_5"] = st.number_input(
                "Rev CAGR Yr 4-5", value=params["rev_cagr_yr4_5"],
                step=0.005, format="%.3f", key=f"scen_{scen_type.value}_rev45")
        with col3:
            params["exp_growth"] = st.number_input(
                "Expense Growth", value=params["exp_growth"],
                step=0.005, format="%.3f", key=f"scen_{scen_type.value}_exp")
            params["exit_cap"] = st.number_input(
                "Exit Cap Rate", value=params["exit_cap"],
                step=0.005, format="%.3f", key=f"scen_{scen_type.value}_exit")
        scenarios[scen_type] = params

    set_config("SCENARIO_DEFAULTS", scenarios)


def edit_value_add():
    """Editor for VALUE_ADD_TRIGGERS and VALUE_ADD_SCENARIOS."""
    triggers = get_config("VALUE_ADD_TRIGGERS").copy()
    col1, col2 = st.columns(2)
    with col1:
        triggers["max_occupancy"] = st.number_input(
            "Max Occupancy Trigger", value=triggers["max_occupancy"],
            step=0.01, format="%.2f", key="va_trig_occ")
    with col2:
        triggers["min_rent_gap_pct"] = st.number_input(
            "Min Rent Gap %", value=triggers["min_rent_gap_pct"],
            step=0.01, format="%.2f", key="va_trig_rent")
    set_config("VALUE_ADD_TRIGGERS", triggers)

    from registry import ScenarioType
    va_scenarios = get_config("VALUE_ADD_SCENARIOS").copy()

    for scen_type in ScenarioType:
        st.markdown(f"**VA {scen_type.value.title()} Case**")
        params = va_scenarios[scen_type].copy()
        col1, col2, col3 = st.columns(3)
        with col1:
            params["target_occupancy"] = st.number_input(
                "Target Occupancy", value=params["target_occupancy"],
                step=0.01, format="%.2f", key=f"va_{scen_type.value}_occ")
            params["months_to_stabilize"] = st.number_input(
                "Months to Stabilize", value=params["months_to_stabilize"],
                step=1, key=f"va_{scen_type.value}_months")
        with col2:
            params["rent_growth_to_market"] = st.number_input(
                "Rent Growth to Market", value=params["rent_growth_to_market"],
                step=0.05, format="%.2f", key=f"va_{scen_type.value}_rent")
            params["post_stabilize_rev_growth"] = st.number_input(
                "Post-Stab Rev Growth", value=params["post_stabilize_rev_growth"],
                step=0.005, format="%.3f", key=f"va_{scen_type.value}_post_rev")
        with col3:
            params["exit_cap"] = st.number_input(
                "Exit Cap", value=params["exit_cap"],
                step=0.005, format="%.3f", key=f"va_{scen_type.value}_exit")
            params["expense_growth"] = st.number_input(
                "Expense Growth", value=params["expense_growth"],
                step=0.005, format="%.3f", key=f"va_{scen_type.value}_exp")
        va_scenarios[scen_type] = params

    set_config("VALUE_ADD_SCENARIOS", va_scenarios)


def edit_solver():
    """Editor for solver parameters."""
    col1, col2, col3 = st.columns(3)
    with col1:
        v = st.number_input("Target IRR", value=get_config("SOLVER_TARGET_IRR"),
                            step=0.005, format="%.3f", key="solver_irr")
        set_config("SOLVER_TARGET_IRR", v)
    with col2:
        v = st.number_input("Tolerance", value=get_config("SOLVER_TOLERANCE"),
                            step=0.0001, format="%.4f", key="solver_tol")
        set_config("SOLVER_TOLERANCE", v)
    with col3:
        v = st.number_input("Max Iterations", value=get_config("SOLVER_MAX_ITERATIONS"),
                            step=5, key="solver_iter")
        set_config("SOLVER_MAX_ITERATIONS", v)
