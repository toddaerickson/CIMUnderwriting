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

# Initialize config overrides in session state
init_config()

# ── Navigation ──────────────────────────────────────────────────────
pages = [
    "Upload & Analyze",
    "Deal Tracker",
    "Default Inputs",
    "Comp Database",
]

st.sidebar.title("CIM Analyst")
st.sidebar.markdown("Self-Storage Investment Analysis")
st.sidebar.divider()

selection = st.sidebar.radio("Navigation", pages, label_visibility="collapsed")

st.sidebar.divider()
st.sidebar.caption("CIM Analyst v1.0")

# ── Route to selected page ──────────────────────────────────────────
if selection == "Upload & Analyze":
    from gui.pages.upload_analyze import render
    render()
elif selection == "Deal Tracker":
    from gui.pages.deal_tracker import render
    render()
elif selection == "Default Inputs":
    from gui.pages.settings import render
    render()
elif selection == "Comp Database":
    from gui.pages.comp_database import render
    render()
