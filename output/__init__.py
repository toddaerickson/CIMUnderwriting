"""Output writers — memo (.docx), returns model (.xlsx), UW template (.xlsm).

`safe_filename` lives here because all three writers name files from the
same property name and had drifted into three private copies of it: two
sanitized differently, and `excel_writer`'s was uncapped. That last one
was not cosmetic — `generate_excel` is called UNWRAPPED by both
`engine.py` and `run.py`, so a property name long enough to overrun the
filesystem's 255-byte limit aborted the whole run with an OSError, and
capping only the memo's copy (item G's first pass) moved the crash one
line later instead of fixing it.

One definition, per the repo's single-source-of-truth rule.
"""

import hashlib

# Most filesystems stop at 255 BYTES for a single component, and the
# callers prepend a prefix ("SS_Investment_Memo_") and append an
# extension, so the name itself gets a conservative slice of that.
MAX_FILENAME_STEM = 60

# When truncation actually bites, a short digest of the FULL name keeps
# two long deals from colliding. The CLI writes every deal's outputs into
# the directory holding its PDF rather than a per-deal folder, so
# "Fund IV Portfolio - Abilene, TX" and "Fund IV Portfolio - Waco, TX"
# would otherwise share a stem and silently overwrite each other.
_DISAMBIGUATOR_LEN = 6


def safe_filename(name: str) -> str:
    """Sanitize a property name into a filename stem.

    Names that already fit come back unchanged, so nothing that works
    today gets a new filename. Only over-long names gain the digest.
    """
    safe = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_"
                   for c in (name or "")).strip().replace(" ", "_")
    if len(safe) <= MAX_FILENAME_STEM:
        return safe
    digest = hashlib.sha256((name or "").encode("utf-8")).hexdigest()
    keep = MAX_FILENAME_STEM - _DISAMBIGUATOR_LEN - 1
    return f"{safe[:keep].rstrip('_')}_{digest[:_DISAMBIGUATOR_LEN]}"
