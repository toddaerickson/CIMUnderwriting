"""
Logging configuration for CIM Analyst.

Configures dual output:
  - StreamHandler: minimal format to terminal (preserves current UX)
  - FileHandler: timestamped format to cim_analyst.log (audit trail)
"""

import logging
import os


LOG_FILE = os.path.join(os.path.dirname(__file__) or ".", "cim_analyst.log")


#: urllib3 logs the full request line — query string included — at DEBUG on
#: every SUCCESSFUL request. The Census API key travels as a query parameter,
#: so any DEBUG-level sink captures it; measured against the live API, not
#: inferred. Nothing here ships at DEBUG, which is what makes this latent
#: rather than active — but `setup_logging` attaches a FileHandler AT DEBUG, so
#: the moment anyone raises the level to chase a demographics bug the key is
#: appended to cim_analyst.log on disk.
THIRD_PARTY_INFO_ONLY = ("urllib3", "urllib3.connectionpool")


def pin_third_party_loggers():
    """Hold noisy third-party loggers at INFO regardless of the root level.

    Pinned rather than filtered deliberately: a logger's own level gates record
    CREATION, so the leaking record is never built and no handler added later —
    by a future `LOGGING` dict, by pytest, by a debugger — can re-expose it. A
    filter on today's handlers would only cover today's handlers.

    Idempotent, and safe before or after `setup_logging`. It does override a
    developer who deliberately set urllib3 to DEBUG; that is the intent, and
    `tests/test_enrichment.py` documents the trade by unpinning explicitly to
    characterize what is being suppressed.
    """
    for name in THIRD_PARTY_INFO_ONLY:
        third_party = logging.getLogger(name)
        if third_party.level < logging.INFO:
            third_party.setLevel(logging.INFO)


def setup_logging(level=logging.INFO):
    """Configure root logger with stream and file handlers."""
    root = logging.getLogger()
    root.setLevel(level)

    # Before the early return below, not after: on re-entry the handlers are
    # already attached but the pin may not have been applied by whoever
    # configured logging first.
    pin_third_party_loggers()

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
