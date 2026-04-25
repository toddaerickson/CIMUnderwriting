"""
Page 2: Batch Analysis

Upload multiple CIM PDFs → analyze all → comparison table → download all
"""

import os
import io
import tempfile
import zipfile
import streamlit as st
import pandas as pd
from gui.config_manager import apply_config, restore_config
from gui.session import get_batch_results, set_batch_results


def render():
    st.header("Batch Analysis")
    st.caption("Upload multiple CIM PDFs to analyze and compare properties side-by-side.")

    uploaded_files = st.file_uploader(
        "Upload CIM PDFs", type=["pdf"], accept_multiple_files=True)

    if uploaded_files:
        st.info(f"{len(uploaded_files)} file(s) selected")

        if st.button("Analyze All", type="primary", use_container_width=True):
            apply_config()
            results = []
            progress_bar = st.progress(0, text="Starting batch analysis...")

            for i, uploaded in enumerate(uploaded_files):
                progress_bar.progress(
                    i / len(uploaded_files),
                    text=f"Analyzing {uploaded.name} ({i+1}/{len(uploaded_files)})...")

                # Save to temp file
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf",
                                                  dir=os.getcwd())
                tmp.write(uploaded.getbuffer())
                tmp.close()

                try:
                    from gui.engine import run_full_pipeline
                    result = run_full_pipeline(tmp.name)
                    results.append(result)
                except Exception as e:
                    st.error(f"Failed to analyze {uploaded.name}: {e}")

            progress_bar.progress(1.0, text=f"Batch complete! {len(results)} analyzed.")
            restore_config()
            set_batch_results(results)
            st.rerun()

    # ── Show Results ────────────────────────────────────────────────
    results = get_batch_results()
    if results:
        _render_comparison(results)


def _render_comparison(results):
    """Render comparison table across all analyzed properties."""
    st.subheader(f"Comparison Table ({len(results)} properties)")

    rows = []
    for r in results:
        cim = r.cim_data
        base = r.scenario_results.get("base", {})
        mp = r.max_offer.get("max_price")
        va_mp = r.va_max_offer.get("max_price") if r.va_max_offer else None
        rec = r.gate_summary.get("recommendation", "N/A")

        asking = cim.asking_price or 0
        discount = (asking - mp) / asking if (mp and asking) else None

        rows.append({
            "Property": cim.property_name or "Unknown",
            "City, State": f"{cim.city or ''}, {cim.state or ''}".strip(", "),
            "NRSF": f"{cim.nrsf:,.0f}" if cim.nrsf else "N/A",
            "Asking": f"${asking:,.0f}" if asking else "N/A",
            "$/SF": f"${asking / cim.nrsf:,.0f}" if (asking and cim.nrsf) else "N/A",
            "Adj NOI": f"${r.adjusted_noi:,.0f}" if r.adjusted_noi else "N/A",
            "Entry Cap": f"{(r.adjusted_noi / asking):.1%}" if (r.adjusted_noi and asking) else "N/A",
            "Base IRR": f"{base.get('irr', 0):.1%}" if base.get('irr') else "N/A",
            "Base MOIC": f"{base.get('moic', 0):.2f}x" if base.get('moic') else "N/A",
            "YoC": f"{base.get('yield_on_cost', 0):.1%}" if base.get('yield_on_cost') else "N/A",
            "Max Offer": f"${mp:,.0f}" if mp else "N/A",
            "VA Max": f"${va_mp:,.0f}" if va_mp else "—",
            "Discount": f"{discount:.1%}" if discount else "N/A",
            "Recommendation": rec,
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # ── Download options ────────────────────────────────────────────
    st.divider()
    col1, col2 = st.columns(2)

    with col1:
        csv = df.to_csv(index=False)
        st.download_button(
            "Export Comparison (CSV)",
            data=csv,
            file_name="cim_comparison.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with col2:
        # Zip all output files
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for r in results:
                if r.memo_path and os.path.isfile(r.memo_path):
                    zf.write(r.memo_path, os.path.basename(r.memo_path))
                if r.excel_path and os.path.isfile(r.excel_path):
                    zf.write(r.excel_path, os.path.basename(r.excel_path))
                tp = getattr(r, "template_path", "")
                if tp and os.path.isfile(tp):
                    zf.write(tp, os.path.basename(tp))
        zip_buffer.seek(0)
        st.download_button(
            "Download All Memos & Models (ZIP)",
            data=zip_buffer.getvalue(),
            file_name="cim_batch_output.zip",
            mime="application/zip",
            use_container_width=True,
        )

    # ── Expand individual results ───────────────────────────────────
    st.divider()
    st.subheader("Individual Results")
    for r in results:
        name = r.cim_data.property_name or "Unknown"
        with st.expander(f"{name}"):
            from gui.components.metrics_row import render_key_metrics
            render_key_metrics(r)

            from gui.components.gate_display import render_gates
            render_gates(r.gate_results)

            from gui.components.scenario_table import render_scenario_table
            render_scenario_table(r.scenario_results)

            from gui.components.file_downloads import render_download_buttons
            render_download_buttons(r)
