"""Gate PASS/FAIL/TBD badge rendering."""

import streamlit as st


def render_gates(gate_results: list):
    """Render go/no-go gates as a colored table."""
    if not gate_results:
        st.info("No gate results available.")
        return

    for g in gate_results:
        result = g["result"]
        name = g["name"]
        actual = g["actual"]

        if result == "PASS":
            icon = ":green[PASS]"
        elif result == "FAIL":
            icon = ":red[FAIL]"
        else:
            icon = ":orange[TBD]"

        col1, col2, col3 = st.columns([0.4, 0.15, 0.45])
        with col1:
            st.markdown(f"**{g['gate']}. {name}**")
        with col2:
            st.markdown(icon)
        with col3:
            st.caption(actual)


def render_recommendation(gate_summary: dict):
    """Render the overall recommendation banner."""
    rec = gate_summary.get("recommendation", "N/A")
    passed = gate_summary.get("passed", 0)
    total = gate_summary.get("total", 0)

    if "PASS" in rec and "CONDITION" not in rec:
        st.success(f"**{rec}** ({passed}/{total} gates passed)")
    elif "CONDITION" in rec:
        st.warning(f"**{rec}** ({passed}/{total} gates passed)")
    else:
        st.error(f"**{rec}** ({passed}/{total} gates passed)")

    for g in gate_summary.get("failed_gates", []):
        st.markdown(f"- :red[{g['name']}]: {g.get('note', '')}")
    for g in gate_summary.get("tbd_gates", []):
        st.markdown(f"- :orange[Verify: {g['name']}]")
