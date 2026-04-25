"""Metric card layout helpers."""

import streamlit as st


def render_key_metrics(result):
    """Render top-line metric cards from an AnalysisResult."""
    cim = result.cim_data
    base = result.scenario_results.get("base", {})
    mp = result.max_offer.get("max_price")
    rec = result.gate_summary.get("recommendation", "N/A")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Recommendation", rec)
    with col2:
        irr = base.get("irr")
        st.metric("Base Case IRR", f"{irr:.1%}" if irr else "N/A")
    with col3:
        st.metric("Max Offer (10% IRR)", f"${mp:,.0f}" if mp else "N/A")
    with col4:
        if mp and cim.asking_price:
            discount = (cim.asking_price - mp) / cim.asking_price
            st.metric("Discount to Asking", f"{discount:.1%}")
        else:
            st.metric("Discount to Asking", "N/A")


def render_property_header(cim_data):
    """Render property identification header."""
    name = cim_data.property_name or "Unknown Property"
    city_state = f"{cim_data.city or ''}, {cim_data.state or ''}".strip(", ")
    st.subheader(name)
    parts = []
    if city_state:
        parts.append(city_state)
    if cim_data.nrsf:
        parts.append(f"{cim_data.nrsf:,.0f} SF")
    if cim_data.total_units:
        parts.append(f"{cim_data.total_units} units")
    if cim_data.asking_price:
        parts.append(f"Asking: ${cim_data.asking_price:,.0f}")
    if parts:
        st.caption(" | ".join(parts))
