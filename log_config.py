"""
Logging configuration for CIM Analyst.

Configures dual output:
  - StreamHandler: minimal format to terminal (preserves current UX)
  - FileHandler: timestamped format to cim_analyst.log (audit trail)
"""

import logging
import os


LOG_FILE = os.path.join(os.path.dirname(__file__) or ".", "cim_analyst.log")


def setup_logging(level=logging.INFO):
    """Configure root logger with stream and file handlers."""
    root = logging.getLogger()
    root.setLevel(level)

    # Skip if already configured (e.g., re-entry)
    if root.handlers:
        return

    # Terminal: minimal format — preserves current print() experience
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(stream_handler)

    # File: structured format for debugging and audit trail
    file_handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(file_handler)
