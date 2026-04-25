"""
Page 3: Settings — Full config.py parameter editor.

All edits are stored in session state and applied at analysis time.
No files are modified on disk.
"""

import streamlit as st
from gui.config_manager import reset_config
from gui.components.config_editors import (
    edit_gates,
    edit_expense_benchmarks,
    edit_replacement_cost,
    edit_scenarios,
    edit_value_add,
    edit_solver,
)


def render():
    st.header("Settings")
    st.caption("Edit analysis parameters. Changes apply to the current session only.")

    if st.button("Reset All to Defaults"):
        reset_config()
        st.success("All settings reset to defaults.")
        st.rerun()

    with st.expander("Go / No-Go Gate Thresholds", expanded=False):
        edit_gates()

    with st.expander("Expense Benchmarks ($/NRSF/yr)", expanded=False):
        edit_expense_benchmarks()

    with st.expander("Replacement Cost Benchmarks", expanded=False):
        edit_replacement_cost()

    with st.expander("Scenario Assumptions (Bear / Base / Bull)", expanded=False):
        edit_scenarios()

    with st.expander("Value-Add Triggers & Scenarios", expanded=False):
        edit_value_add()

    with st.expander("Solver Parameters", expanded=False):
        edit_solver()
