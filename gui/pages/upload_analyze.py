"""
Page: Upload & Analyze

Upload CIM PDF (required) + optional rent roll / financial statements.
Creates a deal subfolder, runs analysis, saves outputs alongside inputs.
"""

import os
import streamlit as st
from gui.config_manager import apply_config, restore_config
from gui.session import get_current_result, set_current_result
from gui.deal_manager import (
    create_deal_folder, save_uploaded_file, write_deal_meta,
    build_deal_meta,
)


def render():
    st.subheader("Upload Documents")

    # ── File uploaders (3-column row) ──────────────────────────────

    # Hide the "Limit 200MB..." helper text on file uploaders
    st.markdown(
        "<style>div[data-testid='stFileUploader'] section > span {display:none;}</style>",
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        cim_file = st.file_uploader(
            "CIM",
            type=["pdf"],
            key="upload_cim",
        )
        # Make the uploaded-file tile clickable to open the PDF
        if cim_file is not None:
            cim_file.seek(0)
            pdf_bytes = cim_file.read()
            byte_list = ",".join(str(b) for b in pdf_bytes)
            st.html(f"""
                <style>
                /* Hide the document placeholder icon */
                [data-testid="stFileUploaderFile"] svg:first-of-type {{
                    display: none !important;
                }}
                /* Pointer cursor on the file tile */
                [data-testid="stFileUploaderFile"] {{
                    cursor: pointer;
                }}
                [data-testid="stFileUploaderFile"]:hover {{
                    opacity: 0.8;
                }}
                </style>
                <script>
                (function() {{
                    var bytes = new Uint8Array([{byte_list}]);
                    var blob = new Blob([bytes], {{type: 'application/pdf'}});
                    var url = URL.createObjectURL(blob);
                    function attach() {{
                        var tiles = document.querySelectorAll('[data-testid="stFileUploaderFile"]');
                        if (tiles.length === 0) {{ setTimeout(attach, 300); return; }}
                        tiles[0].addEventListener('click', function(e) {{
                            if (!e.target.closest('button')) {{ window.open(url, '_blank'); }}
                        }});
                    }}
                    attach();
                }})();
                </script>
            """)
    with c2:
        rent_roll_file = st.file_uploader(
            "Rent Roll (optional)",
            type=["pdf", "xlsx", "xls", "csv"],
            key="upload_rent_roll",
        )
    with c3:
        financials_file = st.file_uploader(
            "Financials (optional)",
            type=["pdf", "xlsx", "xls", "csv"],
            key="upload_financials",
        )

    if cim_file is None:
        st.info("Upload a CIM PDF to begin analysis.")
        # Still show results from previous analysis if available
        result = get_current_result()
        if result and result.gate_results:
            _render_results(result)
        return

    # ── Handle new upload ────────────────────────────────────────────
    prev_name = st.session_state.get("uploaded_pdf_name")
    if prev_name != cim_file.name:
        # New file uploaded — reset state
        st.session_state["uploaded_pdf_name"] = cim_file.name
        st.session_state.pop("extracted_result", None)
        st.session_state.pop("current_result", None)
        st.session_state.pop("deal_folder", None)
        st.session_state.pop("dupe_resolved", None)

    # ── Duplicate check ─────────────────────────────────────────────
    if not st.session_state.get("dupe_resolved"):
        from data.comp_db import CompDatabase
        comp_db = CompDatabase()

        # Extract a rough property name from filename for fuzzy matching
        fname_stem = os.path.splitext(cim_file.name)[0]
        dupes = comp_db.find_duplicates(
            filename=cim_file.name,
            property_name=fname_stem,
        )

        # Also check deal folders
        from gui.deal_manager import list_all_deals
        existing_deals = list_all_deals()
        for deal in existing_deals:
            deal_inputs = deal.get("input_files", [])
            if cim_file.name in deal_inputs:
                if not any(d["pdf_filename"] == cim_file.name for d in dupes):
                    dupes.append({
                        "property_name": deal.get("property_name", ""),
                        "city": deal.get("city", ""),
                        "state": deal.get("state", ""),
                        "analysis_date": deal.get("analysis_date", ""),
                        "pdf_filename": cim_file.name,
                        "match_type": "deal_folder",
                    })

        if dupes:
            st.warning(f"This file or property may already exist in the database "
                       f"({len(dupes)} match{'es' if len(dupes) > 1 else ''} found).")
            import pandas as pd
            st.dataframe(
                pd.DataFrame(dupes)[["property_name", "city", "state",
                                      "analysis_date", "match_type"]],
                use_container_width=True, hide_index=True,
            )
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Load Existing Record", use_container_width=True):
                    # Find the matching deal folder and load its result
                    for deal in existing_deals:
                        if cim_file.name in deal.get("input_files", []):
                            st.session_state["deal_folder"] = deal["deal_folder"]
                            st.info(f"Loaded: {deal.get('property_name', cim_file.name)}")
                            break
                    st.session_state["dupe_resolved"] = True
                    st.rerun()
            with c2:
                if st.button("Continue as New (v2)", type="primary",
                             use_container_width=True):
                    # Append v2 to the tracked name so deal folder gets a new name
                    base, ext = os.path.splitext(cim_file.name)
                    st.session_state["uploaded_pdf_name"] = f"{base} v2{ext}"
                    st.session_state["dupe_resolved"] = True
                    st.rerun()
            return  # Wait for user choice before proceeding
        else:
            st.session_state["dupe_resolved"] = True

    # ── Extract ──────────────────────────────────────────────────────
    if "extracted_result" not in st.session_state:
        with st.spinner("Extracting PDF data..."):
            # Save CIM to a temp location for extraction
            import tempfile
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf",
                                              dir=os.getcwd())
            tmp.write(cim_file.getbuffer())
            tmp.close()
            st.session_state["uploaded_pdf_path"] = tmp.name

            from gui.engine import extract_pdf_data
            result = extract_pdf_data(tmp.name)
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

    # ── Assumptions editor ────────────────────────────────────────────
    st.subheader("Review & Edit Assumptions")
    from gui.components.assumptions_editor import render_assumptions_editor
    assumptions = render_assumptions_editor(cim_data, report)

    # ── Run Analysis ─────────────────────────────────────────────────
    st.divider()
    if st.button("Run Analysis", type="primary", use_container_width=True):
        apply_config()

        # Apply replacement cost overrides to config before analysis
        repl_overrides = assumptions.get("replacement_cost_overrides", {})
        if repl_overrides:
            from gui.config_manager import set_config, get_config
            rc = get_config("REPLACEMENT_COST").copy()
            rc.update(repl_overrides)
            set_config("REPLACEMENT_COST", rc)

        # Apply GUI form overrides
        cim_overrides = assumptions.get("cim_overrides", {})
        if cim_overrides:
            from gui.engine import _apply_overrides
            _apply_overrides(cim_data, cim_overrides)

        # Create deal folder using the property name (after overrides applied)
        property_name = cim_data.property_name or "Unknown_Property"
        deal_folder = create_deal_folder(property_name)
        st.session_state["deal_folder"] = deal_folder

        # Save uploaded files to deal folder
        input_files = []
        cim_file.seek(0)
        save_uploaded_file(deal_folder, cim_file)
        input_files.append(cim_file.name)

        if rent_roll_file:
            rent_roll_file.seek(0)
            save_uploaded_file(deal_folder, rent_roll_file)
            input_files.append(rent_roll_file.name)

        if financials_file:
            financials_file.seek(0)
            save_uploaded_file(deal_folder, financials_file)
            input_files.append(financials_file.name)

        st.session_state["input_files"] = input_files

        # Run analysis with output_dir = deal folder
        progress_bar = st.progress(0, text="Starting analysis...")

        def _progress(step, total, msg):
            progress_bar.progress(step / total, text=msg)

        from gui.engine import run_analysis
        final_result = run_analysis(
            result, progress=_progress, output_dir=deal_folder,
            custom_scenarios=assumptions.get("scenario_overrides"),
            custom_va_scenarios=assumptions.get("va_scenario_overrides"),
        )
        progress_bar.progress(1.0, text="Analysis complete!")

        # Write deal metadata
        meta = build_deal_meta(cim_data, final_result, deal_folder,
                               input_files=input_files)
        write_deal_meta(deal_folder, meta)

        # Clean up temp PDF
        tmp_path = st.session_state.get("uploaded_pdf_path")
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        restore_config()
        set_current_result(final_result)
        st.rerun()

    # ── Show results ─────────────────────────────────────────────────
    result = get_current_result()
    if result and result.gate_results:
        _render_results(result)


def _render_results(result):
    """Render full analysis results (reuses single_analysis patterns)."""
    st.divider()

    from gui.components.metrics_row import render_key_metrics, render_property_header
    render_property_header(result.cim_data)
    render_key_metrics(result)

    # Show deal folder location
    deal_folder = st.session_state.get("deal_folder")
    if deal_folder:
        st.success(f"Deal saved to: `{deal_folder}`")

    tab_summary, tab_returns, tab_fin, tab_risks, tab_download = st.tabs(
        ["Summary", "Returns", "Financials", "Risks", "Downloads"])

    with tab_summary:
        from gui.components.gate_display import render_recommendation, render_gates
        render_recommendation(result.gate_summary)
        st.subheader("Go / No-Go Gates")
        render_gates(result.gate_results)

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
                })
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            col1, col2 = st.columns(2)
            with col1:
                st.metric("Total Replacement Cost",
                          f"${repl['total_replacement']:,.0f}")
            with col2:
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

        mp = result.max_offer.get("max_price")
        if mp:
            st.divider()
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Max Offer — Static (10% IRR)", f"${mp:,.0f}")
            with col2:
                va_mp = result.va_max_offer.get("max_price")
                if va_mp:
                    st.metric("Max Offer — Value-Add (10% IRR)", f"${va_mp:,.0f}")

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

    adjustments = fin.get("adjustments", [])
    if adjustments:
        st.subheader("Expense Adjustments")
        for a in adjustments:
            if isinstance(a, str):
                st.write(f"- {a}")
            elif isinstance(a, dict):
                st.write(f"- {a.get('category', '')}: {a.get('flag', '')}")


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
        rows.append({
            "Risk": r.get("risk", ""),
            "Severity": r.get("severity", ""),
            "Detail": r.get("detail", ""),
            "Mitigation": r.get("mitigation", ""),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
