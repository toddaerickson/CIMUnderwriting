"""
Page 4: Comp Database viewer.

Browse historical analyses stored in the SQLite comp database.
"""

import streamlit as st
import pandas as pd


def render():
    st.header("Comp Database")

    try:
        from data.comp_db import CompDatabase
        db = CompDatabase()
        count = db.get_comp_count()
    except Exception as e:
        st.error(f"Could not open comp database: {e}")
        return

    st.metric("Properties in Database", count)

    if count == 0:
        st.info("No properties in database yet. Run an analysis to populate it.")
        return

    # Fetch all comps
    try:
        comps = db.list_comps()
    except Exception:
        # Fallback: query directly
        comps = _fetch_comps_raw(db)

    if not comps:
        st.info("No comp data available.")
        return

    df = pd.DataFrame(comps)

    # Filters
    col1, col2 = st.columns(2)
    with col1:
        if "state" in df.columns:
            states = sorted(df["state"].dropna().unique())
            selected_states = st.multiselect("Filter by State", states)
            if selected_states:
                df = df[df["state"].isin(selected_states)]
    with col2:
        if "nrsf" in df.columns:
            min_sf = st.number_input("Min NRSF", value=0, step=10000)
            if min_sf > 0:
                df = df[df["nrsf"] >= min_sf]

    st.dataframe(df, use_container_width=True, hide_index=True)

    # Export
    csv = df.to_csv(index=False)
    st.download_button(
        "Export to CSV",
        data=csv,
        file_name="comp_database.csv",
        mime="text/csv",
    )


def _fetch_comps_raw(db):
    """Fallback: query comp database directly if list_comps not available."""
    try:
        import sqlite3
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT property_name, city, state, nrsf, total_units, "
            "asking_price, ttm_noi, analysis_date, pdf_filename "
            "FROM properties ORDER BY analysis_date DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []
