"""CIMData field override form for the GUI."""

import streamlit as st


def render_cim_editor(cim_data) -> dict:
    """
    Render editable fields for CIMData. Returns dict of overrides
    (only fields the user changed).
    """
    overrides = {}

    with st.expander("Property Info", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            v = st.text_input("Property Name", value=cim_data.property_name or "")
            if v:
                overrides["property_name"] = v
            v = st.text_input("Address", value=cim_data.address or "")
            if v:
                overrides["address"] = v
            v = st.text_input("City", value=cim_data.city or "")
            if v:
                overrides["city"] = v
        with col2:
            v = st.text_input("State (2-letter)", value=cim_data.state or "", max_chars=2)
            if v:
                overrides["state"] = v.upper()
            v = st.text_input("MSA", value=cim_data.msa or "")
            if v:
                overrides["msa"] = v
            v = st.number_input("Year Built", value=cim_data.year_built or 0,
                                min_value=0, max_value=2030, step=1)
            if v > 0:
                overrides["year_built"] = v

    with st.expander("Size & Occupancy", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            v = st.number_input("NRSF (Net Rentable SF)", value=float(cim_data.nrsf or 0),
                                min_value=0.0, step=1000.0, format="%.0f")
            if v > 0:
                overrides["nrsf"] = v
            v = st.number_input("Total Units", value=int(cim_data.total_units or 0),
                                min_value=0, step=1)
            if v > 0:
                overrides["total_units"] = v
            v = st.number_input("CC % (0-1)", value=float(cim_data.cc_pct or 0),
                                min_value=0.0, max_value=1.0, step=0.01, format="%.2f")
            overrides["cc_pct"] = v
        with col2:
            v = st.number_input("Physical Occupancy (0-1)",
                                value=float(cim_data.physical_occupancy or 0),
                                min_value=0.0, max_value=1.0, step=0.01, format="%.2f")
            if v > 0:
                overrides["physical_occupancy"] = v
            v = st.number_input("Economic Occupancy (0-1)",
                                value=float(cim_data.economic_occupancy or 0),
                                min_value=0.0, max_value=1.0, step=0.01, format="%.2f")
            if v > 0:
                overrides["economic_occupancy"] = v
            v = st.number_input("Acreage", value=float(cim_data.acreage or 0),
                                min_value=0.0, step=0.1, format="%.2f")
            if v > 0:
                overrides["acreage"] = v

    with st.expander("Facility Type SF Breakdown", expanded=False):
        st.caption("Set SF for each facility type present (for replacement cost)")
        col1, col2 = st.columns(2)
        with col1:
            v = st.number_input("Self-Storage Drive-Up SF",
                                value=float(cim_data.ss_driveup_sf or 0),
                                min_value=0.0, step=1000.0, format="%.0f")
            if v > 0:
                overrides["ss_driveup_sf"] = v
            v = st.number_input("Self-Storage Enclosed SF",
                                value=float(cim_data.ss_enclosed_sf or 0),
                                min_value=0.0, step=1000.0, format="%.0f")
            if v > 0:
                overrides["ss_enclosed_sf"] = v
            v = st.number_input("Boat/RV Enclosed SF",
                                value=float(cim_data.brv_enclosed_sf or 0),
                                min_value=0.0, step=1000.0, format="%.0f")
            if v > 0:
                overrides["brv_enclosed_sf"] = v
        with col2:
            v = st.number_input("Boat/RV Covered SF",
                                value=float(cim_data.brv_covered_sf or 0),
                                min_value=0.0, step=1000.0, format="%.0f")
            if v > 0:
                overrides["brv_covered_sf"] = v
            v = st.number_input("Boat/RV Open Parking SF",
                                value=float(cim_data.brv_open_sf or 0),
                                min_value=0.0, step=1000.0, format="%.0f")
            if v > 0:
                overrides["brv_open_sf"] = v

    with st.expander("Financials", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            v = st.number_input("Asking Price ($)", value=float(cim_data.asking_price or 0),
                                min_value=0.0, step=100000.0, format="%.0f")
            if v > 0:
                overrides["asking_price"] = v
            v = st.number_input("TTM NOI ($)", value=float(cim_data.ttm_noi or 0),
                                min_value=0.0, step=10000.0, format="%.0f")
            if v > 0:
                overrides["ttm_noi"] = v
            v = st.number_input("TTM GPR ($)", value=float(cim_data.ttm_gpr or 0),
                                min_value=0.0, step=10000.0, format="%.0f")
            if v > 0:
                overrides["ttm_gpr"] = v
        with col2:
            v = st.number_input("TTM EGR ($)", value=float(cim_data.ttm_egr or 0),
                                min_value=0.0, step=10000.0, format="%.0f")
            if v > 0:
                overrides["ttm_egr"] = v
            v = st.number_input("CIM Year 1 NOI ($)", value=float(cim_data.cim_yr1_noi or 0),
                                min_value=0.0, step=10000.0, format="%.0f")
            if v > 0:
                overrides["cim_yr1_noi"] = v
            v = st.number_input("CapEx Estimate ($)", value=float(cim_data.capex_estimate or 0),
                                min_value=0.0, step=10000.0, format="%.0f")
            if v > 0:
                overrides["capex_estimate"] = v
            v = st.number_input("Mgmt Fee % (0-1)", value=float(cim_data.mgmt_fee_pct or 0),
                                min_value=0.0, max_value=0.15, step=0.005, format="%.3f")
            if v > 0:
                overrides["mgmt_fee_pct"] = v

    with st.expander("Demographics & Market", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            v = st.number_input("Population 1-mi", value=int(cim_data.population_1mi or 0),
                                min_value=0, step=1000)
            if v > 0:
                overrides["population_1mi"] = v
            v = st.number_input("Population 3-mi", value=int(cim_data.population_3mi or 0),
                                min_value=0, step=1000)
            if v > 0:
                overrides["population_3mi"] = v
            v = st.number_input("Population 5-mi", value=int(cim_data.population_5mi or 0),
                                min_value=0, step=1000)
            if v > 0:
                overrides["population_5mi"] = v
        with col2:
            v = st.number_input("Median HHI 3-mi ($)", value=float(cim_data.median_hhi_3mi or 0),
                                min_value=0.0, step=1000.0, format="%.0f")
            if v > 0:
                overrides["median_hhi_3mi"] = v
            v = st.number_input("Market Rent $/SF/mo",
                                value=float(cim_data.market_rent_psf or 0),
                                min_value=0.0, step=0.05, format="%.2f")
            if v > 0:
                overrides["market_rent_psf"] = v

    return overrides
