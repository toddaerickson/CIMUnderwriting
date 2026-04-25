"""Download button helpers for .docx and .xlsx output files."""

import os
import streamlit as st


def render_download_buttons(result):
    """Render download buttons for memo, model, and underwriting template."""
    col1, col2, col3 = st.columns(3)

    if result.memo_path and os.path.isfile(result.memo_path):
        with col1:
            with open(result.memo_path, "rb") as f:
                st.download_button(
                    label="Download Investment Memo (.docx)",
                    data=f.read(),
                    file_name=os.path.basename(result.memo_path),
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True,
                )
    else:
        with col1:
            st.warning("Memo file not found.")

    if result.excel_path and os.path.isfile(result.excel_path):
        with col2:
            with open(result.excel_path, "rb") as f:
                st.download_button(
                    label="Download Returns Model (.xlsx)",
                    data=f.read(),
                    file_name=os.path.basename(result.excel_path),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
    else:
        with col2:
            st.warning("Excel model not found.")

    template_path = getattr(result, "template_path", "")
    if template_path and os.path.isfile(template_path):
        with col3:
            with open(template_path, "rb") as f:
                st.download_button(
                    label="Download UW Template (.xlsm)",
                    data=f.read(),
                    file_name=os.path.basename(template_path),
                    mime="application/vnd.ms-excel.sheet.macroEnabled.12",
                    use_container_width=True,
                )
