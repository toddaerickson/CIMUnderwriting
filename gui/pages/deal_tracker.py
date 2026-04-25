"""
Page: Deal Tracker

Table of all analyzed deals with key metrics and links to deal folders.
"""

import os
import streamlit as st
import pandas as pd
from gui.deal_manager import list_all_deals, DEALS_DIR


def render():
    st.header("Deal Tracker")

    deals = list_all_deals()

    if not deals:
        st.info("No deals analyzed yet. Upload a CIM on the **Upload & Analyze** tab to get started.")
        return

    st.metric("Total Deals", len(deals))

    # Build dataframe
    rows = []
    for d in deals:
        # Fair value formatting
        fv = d.get("estimated_fair_value")
        asking = d.get("asking_price")

        rows.append({
            "Property": d.get("property_name", "Unknown"),
            "City": d.get("city", ""),
            "State": d.get("state", ""),
            "Asset Type": d.get("asset_type", "Self Storage"),
            "NRSF": d.get("nrsf"),
            "Land (acres)": d.get("acreage"),
            "Asking Price": asking,
            "Est. Fair Value": fv,
            "Recommendation": d.get("recommendation", "N/A"),
            "Date": d.get("analysis_date", ""),
            "Folder": d.get("deal_folder", ""),
        })

    df = pd.DataFrame(rows)

    # ── Filters ──────────────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    with col1:
        states = sorted(df["State"].dropna().unique())
        if states:
            selected_states = st.multiselect("Filter by State", states)
            if selected_states:
                df = df[df["State"].isin(selected_states)]
    with col2:
        recs = sorted(df["Recommendation"].dropna().unique())
        if recs:
            selected_recs = st.multiselect("Filter by Recommendation", recs)
            if selected_recs:
                df = df[df["Recommendation"].isin(selected_recs)]
    with col3:
        asset_types = sorted(df["Asset Type"].dropna().unique())
        if asset_types:
            selected_types = st.multiselect("Filter by Asset Type", asset_types)
            if selected_types:
                df = df[df["Asset Type"].isin(selected_types)]

    # ── Display table ────────────────────────────────────────────────
    # Format currency columns for display
    display_df = df.copy()
    for col in ("Asking Price", "Est. Fair Value"):
        display_df[col] = display_df[col].apply(
            lambda v: f"${v:,.0f}" if pd.notna(v) and v else "—"
        )
    display_df["NRSF"] = display_df["NRSF"].apply(
        lambda v: f"{v:,.0f}" if pd.notna(v) and v else "—"
    )
    display_df["Land (acres)"] = display_df["Land (acres)"].apply(
        lambda v: f"{v:.1f}" if pd.notna(v) and v else "—"
    )

    # Drop raw folder path from visible table
    display_cols = [c for c in display_df.columns if c != "Folder"]
    st.dataframe(
        display_df[display_cols],
        use_container_width=True,
        hide_index=True,
    )

    # ── Deal folder links ────────────────────────────────────────────
    st.subheader("Deal Folders")
    for _, row in df.iterrows():
        folder = row.get("Folder", "")
        name = row.get("Property", "Unknown")
        if folder and os.path.isdir(folder):
            # List files in the deal folder
            files = [f for f in os.listdir(folder) if not f.startswith(".")]
            file_summary = ", ".join(sorted(files)[:5])
            if len(files) > 5:
                file_summary += f" (+{len(files) - 5} more)"
            st.markdown(f"**{name}** — `{folder}`")
            st.caption(f"Files: {file_summary}")

    # ── Export ────────────────────────────────────────────────────────
    st.divider()
    csv = df.drop(columns=["Folder"]).to_csv(index=False)
    st.download_button(
        "Export Deal List to CSV",
        data=csv,
        file_name="deal_tracker.csv",
        mime="text/csv",
    )
