"""
Page 1: Single CIM Analysis

Upload PDF → Review/Override extracted data → Run analysis → View results → Download
"""

import os
import tempfile
import streamlit as st
from gui.config_manager import apply_config, restore_config
from gui.session import get_current_result, set_current_result


def render():
    st.header("Analyze CIM")

    # ── Step 1: Upload ──────────────────────────────────────────────
    uploaded = st.file_uploader("Upload CIM PDF", type=["pdf"])

    if uploaded is not None:
        # Save to temp file
        if "uploaded_pdf_path" not in st.session_state or \
           st.session_state.get("uploaded_pdf_name") != uploaded.name:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf",
                                              dir=os.getcwd())
            tmp.write(uploaded.getbuffer())
            tmp.close()
            st.session_state["uploaded_pdf_path"] = tmp.name
            st.session_state["uploaded_pdf_name"] = uploaded.name
            # Clear previous results when new file uploaded
            st.session_state.pop("current_result", None)
            st.session_state.pop("extracted_result", None)

        pdf_path = st.session_state["uploaded_pdf_path"]

        # ── Step 2: Extract ─────────────────────────────────────────
        if "extracted_result" not in st.session_state:
            with st.spinner("Extracting PDF data..."):
                from gui.engine import extract_pdf_data
                result = extract_pdf_data(pdf_path)
                st.session_state["extracted_result"] = result

        result = st.session_state["extracted_result"]
        cim_data = result.cim_data
        report = result.extraction_report

        # Show extraction summary
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Confidence", f"{report.get('confidence_pct', 0)}%")
        with col2:
            st.metric("Fields Populated",
                      f"{report.get('populated', 0)}/{report.get('total_fields', 0)}")
        with col3:
            missing_count = len(report.get("missing", []))
            st.metric("Missing Fields", missing_count)

        if report.get("missing"):
            with st.expander(f"Missing fields ({missing_count})", expanded=False):
                st.write(", ".join(report["missing"]))

        if result.errors:
            for err in result.errors:
                st.warning(err)

        # ── Step 3: Override form ───────────────────────────────────
        st.subheader("Review & Override Data")
        from gui.components.cim_data_editor import render_cim_editor
        overrides = render_cim_editor(cim_data)

        # ── Step 4: Run Analysis ────────────────────────────────────
        st.divider()
        if st.button("Run Analysis", type="primary", use_container_width=True):
            # Apply any config edits from Settings page
            apply_config()

            # Apply GUI form overrides to cim_data
            if overrides:
                from gui.engine import _apply_overrides
                _apply_overrides(cim_data, overrides)

            progress_bar = st.progress(0, text="Starting analysis...")

            def _progress(step, total, msg):
                progress_bar.progress(step / total, text=msg)

            from gui.engine import run_analysis
            final_result = run_analysis(result, progress=_progress)
            progress_bar.progress(1.0, text="Analysis complete!")

            restore_config()
            set_current_result(final_result)
            st.rerun()

    # ── Step 5: Show Results ────────────────────────────────────────
    result = get_current_result()
    if result and result.gate_results:
        _render_results(result)


def _render_results(result):
    """Render full analysis results dashboard."""
    st.divider()

    from gui.components.metrics_row import render_key_metrics, render_property_header
    render_property_header(result.cim_data)
    render_key_metrics(result)

    tab_summary, tab_returns, tab_fin, tab_risks, tab_download = st.tabs(
        ["Summary", "Returns", "Financials", "Risks", "Downloads"])

    with tab_summary:
        from gui.components.gate_display import render_recommendation, render_gates
        render_recommendation(result.gate_summary)
        st.subheader("Go / No-Go Gates")
        render_gates(result.gate_results)

        # Replacement cost
        repl = result.physical_analysis.get("replacement_cost", {})
        if repl.get("estimable"):
            st.subheader("Replacement Cost Estimate")
            import pandas as pd
            rows = []
            for td in repl.get("facility_type_details", []):
                rows.append({
                    "Facility Type": td["type"],
                    "SF": f"{td['sf']:,.0f}",
                    "Hard $/SF": f"${td['hard_rate']:,.0f}",
                    "Hard Cost": f"${td['hard_cost']:,.0f}",
                    "Site $/SF": f"${td['site_rate']:,.0f}" if td['site_rate'] > 0 else "Incl.",
                    "Site Cost": f"${td['site_cost']:,.0f}" if td['site_cost'] > 0 else "—",
                })
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total Replacement Cost",
                          f"${repl['total_replacement']:,.0f}")
            with col2:
                st.metric("Per SF",
                          f"${repl['replacement_per_sf']:,.0f}" if repl.get('replacement_per_sf') else "N/A")
            with col3:
                comp = result.physical_analysis.get("price_vs_replacement", {})
                if comp.get("comparable"):
                    d = comp["discount_to_replacement"]
                    label = "Discount" if d > 0 else "Premium"
                    st.metric(f"{label} to Replacement", f"{abs(d):.1%}")

    with tab_returns:
        from gui.components.scenario_table import (
            render_scenario_table, render_va_scenario_table, render_sensitivity_table)
        render_scenario_table(result.scenario_results)

        if result.va_results:
            st.divider()
            render_va_scenario_table(result.va_results)

        # Max offer
        mp = result.max_offer.get("max_price")
        if mp:
            st.divider()
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Max Offer — Static (10% IRR)", f"${mp:,.0f}")
                if result.max_offer.get("implied_entry_cap"):
                    st.caption(
                        f"Implied Entry Cap: {result.max_offer['implied_entry_cap']:.1%}")
            with col2:
                va_mp = result.va_max_offer.get("max_price")
                if va_mp:
                    st.metric("Max Offer — Value-Add (10% IRR)", f"${va_mp:,.0f}")
                    if result.va_max_offer.get("implied_entry_cap"):
                        st.caption(
                            f"Implied Entry Cap: {result.va_max_offer['implied_entry_cap']:.1%}")

        if result.sensitivity:
            st.divider()
            render_sensitivity_table(result.sensitivity)

    with tab_fin:
        _render_financials(result)

    with tab_risks:
        _render_risks(result)

    with tab_download:
        from gui.components.file_downloads import render_download_buttons
        render_download_buttons(result)


def _render_financials(result):
    """Render financial analysis details."""
    fin = result.financial_analysis
    if not fin:
        st.info("No financial analysis available.")
        return

    # Income summary
    income = fin.get("income_summary", {})
    if income:
        st.subheader("Income Summary")
        import pandas as pd
        rows = []
        for label, key in [("Gross Potential Rent", "gpr"),
                           ("Vacancy Loss", "vacancy_loss"),
                           ("Effective Gross Revenue", "egr"),
                           ("Other Income", "other_income"),
                           ("Total Revenue", "total_revenue")]:
            val = income.get(key)
            if val is not None:
                rows.append({"Line Item": label, "Amount": f"${val:,.0f}"})
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Expense analysis
    adj = fin.get("adjusted_ttm_noi", {})
    if adj:
        st.subheader("Adjusted TTM NOI")
        col1, col2 = st.columns(2)
        with col1:
            cim_noi = adj.get("cim_ttm_noi")
            if cim_noi:
                st.metric("CIM TTM NOI", f"${cim_noi:,.0f}")
        with col2:
            analyst_noi = adj.get("analyst_adjusted_noi")
            if analyst_noi:
                st.metric("Analyst-Adjusted NOI", f"${analyst_noi:,.0f}")

    # Expense adjustments
    adjustments = fin.get("adjustments", [])
    if adjustments:
        st.subheader("Expense Adjustments")
        import pandas as pd
        rows = []
        for a in adjustments:
            rows.append({
                "Category": a.get("category", ""),
                "CIM Amount": f"${a.get('cim_amount', 0):,.0f}",
                "Benchmark": f"${a.get('benchmark', 0):,.0f}",
                "Adjusted": f"${a.get('adjusted', 0):,.0f}",
                "Flag": a.get("flag", ""),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_risks(result):
    """Render risk analysis."""
    risks = result.risk_analysis
    if not risks:
        st.info("No risk analysis available.")
        return

    risk_list = risks.get("risks", [])
    if not risk_list:
        st.success("No significant risks identified.")
        return

    import pandas as pd
    rows = []
    for r in risk_list:
        severity = r.get("severity", "")
        rows.append({
            "Risk": r.get("risk", ""),
            "Severity": severity,
            "Detail": r.get("detail", ""),
            "Mitigation": r.get("mitigation", ""),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
