#!/usr/bin/env python3
"""
CIM Analyst — Streamlit GUI

Launch with:
    streamlit run gui/app.py
"""

import sys
import os

# Ensure project root is on sys.path so all imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
from gui.config_manager import init_config

# ── Page config (must be first Streamlit call) ──────────────────────
st.set_page_config(
    page_title="CIM Analyst",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Hide the default Streamlit "app" navigation link
st.markdown(
    '<style>[data-testid="stSidebarNav"] {display: none;}</style>',
    unsafe_allow_html=True,
)

# Initialize config overrides in session state
init_config()

# ── Sidebar Navigation ────────────────────────────────────────────
st.sidebar.title("CIM Analyst")
st.sidebar.caption("Self-Storage Investment Screening")
st.sidebar.divider()

# Workflow pages
workflow_pages = ["New Analysis", "Deal Pipeline", "Comps"]
selection = st.sidebar.radio(
    "Navigation", workflow_pages, label_visibility="hidden"
)

# Settings separated below a divider
st.sidebar.divider()
if st.sidebar.button("Settings", use_container_width=True):
    st.session_state["nav_override"] = "Settings"

st.sidebar.divider()
st.sidebar.caption("v1.0")

# Allow Settings button to override radio selection
if st.session_state.get("nav_override") == "Settings":
    selection = "Settings"
    st.session_state.pop("nav_override", None)

# ── Route to selected page ──────────────────────────────────────────
if selection == "New Analysis":
    from gui.pages.upload_analyze import render
    render()
elif selection == "Deal Pipeline":
    from gui.pages.deal_tracker import render
    render()
elif selection == "Comps":
    from gui.pages.comp_database import render
    render()
elif selection == "Settings":
    from gui.pages.settings import render
    render()
