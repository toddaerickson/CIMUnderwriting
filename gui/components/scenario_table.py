"""Bear/Base/Bull scenario comparison table."""

import streamlit as st
import pandas as pd


def render_scenario_table(scenario_results: dict, title: str = "Static Returns"):
    """Render Bear/Base/Bull returns as a formatted table."""
    if not scenario_results:
        st.info("No scenario results available.")
        return

    st.markdown(f"**{title}** (Unlevered, All-Equity)")

    rows = []
    for label, key, fmt in [
        ("Yr1 Yield on Cost", "yield_on_cost", lambda v: f"{v:.1%}" if v else "N/A"),
        ("5-Year MOIC", "moic", lambda v: f"{v:.2f}x" if v else "N/A"),
        ("5-Year IRR", "irr", lambda v: f"{v:.1%}" if v else "N/A"),
    ]:
        row = {"Metric": label}
        for scen in ("bear", "base", "bull"):
            v = scenario_results.get(scen, {}).get(key)
            row[scen.title()] = fmt(v)
        rows.append(row)

    df = pd.DataFrame(rows).set_index("Metric")
    st.dataframe(df, use_container_width=True)


def render_va_scenario_table(va_results: dict):
    """Render value-add scenario table."""
    if not va_results:
        return

    st.markdown("**Value-Add Returns** (Unlevered, All-Equity)")

    rows = []
    for label, key, fmt in [
        ("Stabilized Yield on Cost", "yield_on_cost", lambda v: f"{v:.1%}" if v else "N/A"),
        ("5-Year MOIC", "moic", lambda v: f"{v:.2f}x" if v else "N/A"),
        ("5-Year IRR", "irr", lambda v: f"{v:.1%}" if v else "N/A"),
        ("Development Spread", "development_spread",
         lambda v: f"{v*100:.0f} bps" if v else "N/A"),
        ("Stabilized NOI", "stabilized_noi",
         lambda v: f"${v:,.0f}" if v else "N/A"),
    ]:
        row = {"Metric": label}
        for scen in ("bear", "base", "bull"):
            v = va_results.get(scen, {}).get(key)
            row[scen.title()] = fmt(v)
        rows.append(row)

    df = pd.DataFrame(rows).set_index("Metric")
    st.dataframe(df, use_container_width=True)


def render_sensitivity_table(sensitivity: dict):
    """Render IRR sensitivity grid (price x exit cap)."""
    grid = sensitivity.get("grid")
    if not grid:
        return

    st.markdown("**IRR Sensitivity** (Price x Exit Cap)")

    prices = sensitivity.get("prices", [])
    caps = sensitivity.get("exit_caps", [])

    df = pd.DataFrame(
        [[f"{v:.1%}" if v else "N/A" for v in row] for row in grid],
        index=[f"${p:,.0f}" for p in prices],
        columns=[f"{c:.1%}" for c in caps],
    )
    df.index.name = "Price \\ Exit Cap"
    st.dataframe(df, use_container_width=True)
