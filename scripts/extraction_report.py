"""Corpus metrics for the CIM extractor — the referent a recall claim needs.

Field population alone cannot tell you whether an extraction change helped.
A dropped expense line still leaves the field "populated": `analysis.financials`
books it at `bench_low * nrsf`, the LOW end of the benchmark band, so losing a
number RAISES adjusted NOI and moves the primary IRR gate toward PASS. A metric
that counts populated fields reports that as no change at all.

So this reports the numbers that actually move underwriting:

  lines_with_values   FinancialLines carrying at least one period figure
  all_none_lines      lines the parser found but could assign no figure to
  degenerate_tables   tables that collapsed to a single column and so yield
                      NOTHING — `_parse_financial_tables` skips `len(row) < 2`,
                      which is why they are reported apart from the rest rather
                      than folded into a recall percentage they cannot affect
  image_only_pages    pages with no text layer, which no parser change can reach

The CIMs are confidential and gitignored (`.gitignore` line 21, `*.pdf`), so
this writes METRICS and never text. Counts are not confidential; the documents
are. `--json` emits the shape `tests/fixtures/corpus_baseline.json` stores, so
a change can be diffed against a committed prior instead of against a memory.

Usage:
    python scripts/extraction_report.py [DIR] [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Re-exported, not redefined. The trigger that ACTS on a thin text layer
#: (`extract.ocr.needs_ocr`) and this metric REPORTING one must never drift to
#: two different definitions of the same word, so there is one constant and
#: this is an import of it.
from extract.ocr import MIN_CHARS_FOR_TEXT_LAYER  # noqa: E402,F401


def _metrics_for(path: str) -> dict:
    import pdfplumber

    from extract.parser import parse_cim
    from extract.pdf_reader import extract_pdf

    with pdfplumber.open(path) as pdf:
        chars = [len(page.chars) for page in pdf.pages]
        pages = len(pdf.pages)

    raw = extract_pdf(path)
    data = parse_cim(raw)

    degenerate = 0
    for table in raw["tables"]:
        rows = table["data"]
        if rows and max(len(r) for r in rows) <= 1:
            degenerate += 1

    lines = list(data.income_lines) + list(data.expense_lines)
    all_none = sum(1 for ln in lines
                   if ln.t12 is None and ln.t3 is None and ln.cim_yr1 is None)

    report = data.extraction_report()
    return {
        "pages": pages,
        "chars_per_page": int(statistics.mean(chars)) if chars else 0,
        "image_only_pages": sum(1 for c in chars if c < MIN_CHARS_FOR_TEXT_LAYER),
        "tables": len(raw["tables"]),
        "degenerate_tables": degenerate,
        "income_lines": len(data.income_lines),
        "expense_lines": len(data.expense_lines),
        "lines_with_values": len(lines) - all_none,
        "all_none_lines": all_none,
        "fields_populated": report["populated"],
        "fields_total": report["total_fields"],
    }


def collect(directory: str) -> dict:
    """Metrics keyed by FILENAME STEM, sorted — stable across machines.

    Keyed by name rather than by index so a baseline stays meaningful when the
    operator adds an eighth CIM: a new key is a new deal to review, where a
    shifted index would silently re-point every row.
    """
    out = {}
    for path in sorted(Path(directory).glob("*.pdf")):
        out[path.stem] = _metrics_for(str(path))
    return out


def _totals(corpus: dict) -> dict:
    keys = ("lines_with_values", "all_none_lines", "degenerate_tables",
            "tables", "image_only_pages", "pages")
    return {k: sum(c[k] for c in corpus.values()) for k in keys}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("directory", nargs="?", default=".")
    ap.add_argument("--json", dest="json_out", default=None,
                    help="write the metrics to this path instead of a table")
    args = ap.parse_args(argv)

    corpus = collect(args.directory)
    if not corpus:
        print(f"no PDFs in {args.directory}", file=sys.stderr)
        return 1

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(corpus, indent=2, sort_keys=True) + "\n")
        print(f"wrote {args.json_out} ({len(corpus)} CIMs)")
        return 0

    head = (f"{'CIM':<34}{'pgs':>5}{'img':>5}{'tbl':>5}{'degen':>7}"
            f"{'lines':>7}{'valued':>8}{'allNone':>9}{'fields':>9}")
    print(head)
    print("-" * len(head))
    for name, m in corpus.items():
        total_lines = m["lines_with_values"] + m["all_none_lines"]
        print(f"{name[:33]:<34}{m['pages']:>5}{m['image_only_pages']:>5}"
              f"{m['tables']:>5}{m['degenerate_tables']:>7}{total_lines:>7}"
              f"{m['lines_with_values']:>8}{m['all_none_lines']:>9}"
              f"{str(m['fields_populated']) + '/' + str(m['fields_total']):>9}")

    t = _totals(corpus)
    lines = t["lines_with_values"] + t["all_none_lines"]
    print("-" * len(head))
    print(f"{'TOTAL':<34}{t['pages']:>5}{t['image_only_pages']:>5}"
          f"{t['tables']:>5}{t['degenerate_tables']:>7}{lines:>7}"
          f"{t['lines_with_values']:>8}{t['all_none_lines']:>9}")
    if lines:
        print(f"\nall-None rate: {t['all_none_lines']}/{lines} "
              f"({100 * t['all_none_lines'] / lines:.1f}%)")
    if t["tables"]:
        print(f"degenerate tables: {t['degenerate_tables']}/{t['tables']} "
              f"({100 * t['degenerate_tables'] / t['tables']:.1f}%) "
              f"— these yield no lines either way")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
