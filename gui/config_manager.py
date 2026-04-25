"""
Runtime config override system for the Streamlit GUI.

Deep-copies all config.py dicts into session state on startup.
Settings page edits modify session-state copies.
get_active_config() patches the config module at analysis time.
"""

import copy
import streamlit as st
import config as _cfg


# All config keys we manage
_CONFIG_KEYS = [
    "GATES",
    "EXPENSE_BENCHMARKS",
    "REPLACEMENT_COST",
    "FACILITY_TYPES",
    "SCENARIO_DEFAULTS",
    "VALUE_ADD_SCENARIOS",
    "VALUE_ADD_TRIGGERS",
    "STATE_PROPERTY_TAX_MULTIPLIER",
    "STATE_PROPERTY_TAX_FORMULAS",
    "EXPENSE_REGIONS",
    "TOP_50_MSAS",
    "SOLVER_TARGET_IRR",
    "SOLVER_TOLERANCE",
    "SOLVER_MAX_ITERATIONS",
    "COMP_DB_MIN_COMPS",
    "COMP_DB_NRSF_RANGE",
]


def init_config():
    """Initialize session state with deep copies of all config values."""
    if "config_initialized" in st.session_state:
        return
    for key in _CONFIG_KEYS:
        val = getattr(_cfg, key, None)
        if val is not None:
            st.session_state[f"cfg_{key}"] = copy.deepcopy(val)
    st.session_state["config_initialized"] = True


def get_config(key: str):
    """Get a config value from session state (or original if not yet initialized)."""
    ss_key = f"cfg_{key}"
    if ss_key in st.session_state:
        return st.session_state[ss_key]
    return getattr(_cfg, key, None)


def set_config(key: str, value):
    """Set a config value in session state."""
    st.session_state[f"cfg_{key}"] = value


def reset_config(key: str = None):
    """Reset config to original values. If key is None, reset all."""
    if key:
        val = getattr(_cfg, key, None)
        if val is not None:
            st.session_state[f"cfg_{key}"] = copy.deepcopy(val)
    else:
        for k in _CONFIG_KEYS:
            val = getattr(_cfg, k, None)
            if val is not None:
                st.session_state[f"cfg_{k}"] = copy.deepcopy(val)


def apply_config():
    """
    Patch the live config module with session-state values.

    Call this before running analysis so all modules pick up GUI edits.
    """
    for key in _CONFIG_KEYS:
        ss_key = f"cfg_{key}"
        if ss_key in st.session_state:
            setattr(_cfg, key, st.session_state[ss_key])


def restore_config():
    """
    Restore the config module to its original disk values.

    Call after analysis completes to avoid side effects.
    """
    import importlib
    importlib.reload(_cfg)
