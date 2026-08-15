"""The extractor against real CIMs — a characterization snapshot, not a bar.

`tests/fixtures/corpus_baseline.json` records what `scripts/extraction_report.py`
measures on the operator's seven CIMs. This asserts the numbers still match, so
any change to extraction shows up as a diff someone has to explain — the same
discipline `tests/snapshots/*.json` applies to the analysis output.

**It is a snapshot, deliberately not a floor.** "No CIM got worse" is the
assertion this file was first drafted with and it is vacuous here, because
recall and correctness point opposite ways: the change that introduced this
baseline took one CIM from six valued lines to zero, and that was the POINT —
three of its six were dollars-per-SF figures booked as dollars, and the other
three were year-five projections booked as trailing actuals. A floor would have
called that a regression and a snapshot calls it a delta to justify.

The CIMs are confidential broker packages and gitignored (`.gitignore` line 21,
`*.pdf`), so this SKIPS when they are absent — the precedent is
`test_real_template_still_has_the_cells_the_stub_claims`, the suite's other
proprietary-artifact test. CI therefore never runs it. That is the cost of a
corpus nobody may commit, and the synthetic shapes in
`tests/test_extraction_tables.py` are what CI gets instead: they carry the
STRUCTURE of every bug this baseline would catch.
"""
import json
import os
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
BASELINE = REPO / "tests" / "fixtures" / "corpus_baseline.json"

#: Where the CIMs live. Defaults to the repo root, which is where the CLI
#: prompts for them. The override exists because this repo requires every
#: session to work in a linked WORKTREE, whose root has no PDFs in it — a
#: corpus test that can only run in the one tree nobody is allowed to edit
#: would never be run at all.
CORPUS_DIR = os.environ.get("CIM_CORPUS_DIR", str(REPO))


def _corpus():
    from scripts.extraction_report import collect
    return collect(CORPUS_DIR)


@pytest.mark.skipif(not list(pathlib.Path(CORPUS_DIR).glob("*.pdf")),
                    reason=f"no CIM PDFs in {CORPUS_DIR} (they are gitignored)")
def test_the_corpus_still_extracts_what_the_baseline_recorded():
    """Every baseline CIM present on disk must match, metric for metric.

    A CIM in the baseline but missing from disk is skipped rather than
    failed: the operator renames and archives deals, and a stale key is not
    a code regression. A CIM on disk but absent from the baseline is not
    asserted at all — there is nothing to compare it to — but it IS named in
    the failure message, because "the baseline covers everything present" is
    the kind of claim that quietly stops being true.
    """
    baseline = json.loads(BASELINE.read_text())
    measured = _corpus()

    compared = [k for k in baseline if k in measured]
    if not compared:
        pytest.skip("no baseline CIM is present on this machine")

    drift = []
    for key in compared:
        for metric, expected in sorted(baseline[key].items()):
            actual = measured[key].get(metric)
            if actual != expected:
                drift.append(f"  {key[:40]}  {metric}: {expected} -> {actual}")

    new = sorted(set(measured) - set(baseline))
    assert not drift, (
        f"extraction drifted from tests/fixtures/corpus_baseline.json:\n"
        + "\n".join(drift)
        + "\n\nIf the change is intended, regenerate with\n"
        + "  python scripts/extraction_report.py <dir> "
        + "--json tests/fixtures/corpus_baseline.json\n"
        + "and enumerate the delta in the PR body."
        + (f"\n(not compared, absent from the baseline: {new})" if new else ""))


@pytest.mark.skipif(not list(pathlib.Path(CORPUS_DIR).glob("*.pdf")),
                    reason=f"no CIM PDFs in {CORPUS_DIR} (they are gitignored)")
def test_every_financial_line_is_either_priced_or_refused():
    """The invariant the baseline numbers are a consequence of.

    Stated separately because a snapshot can be regenerated into agreement
    with a bug, and this cannot: the parser emits exactly one refusal per
    line it could not assign, so priced + refused must account for every
    line — never a line that is both, never one that is neither.

    Counted rather than matched by label, because a label is not unique: a
    two-property CIM repeats its whole income statement per property and
    again combined, so the same `Real Estate Taxes` can be priced on one
    page and refused on another, legitimately.
    """
    from extract.parser import parse_cim
    from extract.pdf_reader import extract_pdf

    drift = []
    for pdf in sorted(pathlib.Path(CORPUS_DIR).glob("*.pdf")):
        data = parse_cim(extract_pdf(str(pdf)))
        lines = list(data.income_lines) + list(data.expense_lines)
        priced = sum(1 for ln in lines if any(
            v is not None for v in (ln.t12, ln.t3, ln.cim_yr1)))
        refused = len(data.unmapped_financial_lines)
        if priced + refused != len(lines):
            drift.append(f"{pdf.stem[:34]}: {len(lines)} lines, "
                         f"{priced} priced, {refused} refused")
    assert drift == [], (
        "a line was neither priced nor refused (or was both) — the refusal "
        f"log and the period assignment have come apart: {drift}")
