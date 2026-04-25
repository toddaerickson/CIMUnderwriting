"""
Session state helpers for the Streamlit GUI.

Provides typed accessors and initialization for common session keys.
"""

import streamlit as st
from gui.engine import AnalysisResult


def get_current_result() -> AnalysisResult | None:
    """Get the current single-analysis result."""
    return st.session_state.get("current_result")


def set_current_result(result: AnalysisResult):
    """Store the current single-analysis result."""
    st.session_state["current_result"] = result


def get_batch_results() -> list[AnalysisResult]:
    """Get the batch analysis results list."""
    return st.session_state.get("batch_results", [])


def set_batch_results(results: list[AnalysisResult]):
    """Store batch analysis results."""
    st.session_state["batch_results"] = results


def get_cim_overrides() -> dict:
    """Get the current CIM data overrides from the GUI form."""
    return st.session_state.get("cim_overrides", {})


def set_cim_overrides(overrides: dict):
    """Store CIM data overrides."""
    st.session_state["cim_overrides"] = overrides


def clear_analysis():
    """Clear analysis results (keeps config)."""
    for key in ("current_result", "cim_overrides", "uploaded_pdf_path",
                "unit_mix_editor", "assumptions_unit_mix"):
        st.session_state.pop(key, None)
