"""Item T's last acceptance criterion: no bare numeric modeling literal.

    "A grep/AST sweep finds no numeric modeling literals outside
     `config.py` and `registry.py`'s non-valuation constants — enforced
     by a CI test, not by inspection."

Five hand-written per-family guards stood in for this (age, occupancy,
solver bracket, fabricated NOI, 1-SF property). Family-by-family they
meet the bar; together they do not, because each was added by the PR that
created its family. **A literal family nobody has thought of yet ships
unguarded** — which is the one thing a universal sweep provides and a
pile of specific guards cannot.

Those five stay. This is a backstop, not a replacement: they name their
family, explain why it matters, and fail with a message about THAT
family. Deleting good tests to satisfy a doc phrase would trade a precise
signal for a generic one.

WHAT IS SWEPT: `analysis/` and `model/` — the modeling layer. `output/`
is excluded by a rule stated below, not by oversight.

HOW EXEMPTION WORKS. By KIND wherever possible, by name only where not.
An allowlist of 200 line numbers is a second copy of the code that rots
on the first refactor; a rule like "a literal inside `round()` is a
display precision, not an assumption" keeps holding. Every by-name entry
carries a reason, in the shape `NOT_IN_REGISTER` already uses.

The list is the interesting artifact. Read `ALLOWED` top to bottom and it
says exactly which numbers in this pipeline are still stated in code
rather than owned by config, and why each one is defensible.
"""
import ast
import pathlib

import pytest

import config as cfg

REPO = pathlib.Path(__file__).resolve().parent.parent

#: The modeling layer. `output/` is NOT swept, and that is a judgement
#: rather than an omission: its numbers are overwhelmingly LAYOUT — point
#: sizes, column widths in inches, row counts, character budgets — and a
#: sweep that flagged 174 of those would be turned off within a month.
#: The modeling values `output/` once held (the 10% IRR threshold, its
#: labels, the sensitivity colours) were moved to config by Category 1
#: and are pinned by their own tests, and `output/template_writer.py`
#: carries a stricter AST gate of its own from item E3b — no numeric
#: literal at all in its write paths. What remains uncovered is a NEW
#: modeling literal appearing in `memo_writer` or `excel_writer`; that is
#: a real gap, recorded here rather than papered over.
SWEPT = ("analysis", "model")

#: Values that carry no underwriting meaning in any context: identity and
#: absorbing elements, the two-way split, and percent. A rule about these
#: is safe in a way a rule about 0.85 never is.
TRIVIAL = {0, 1, 2, -1, 0.0, 1.0, 0.5, 100}

#: Conversions between units, not assumptions about deals. Twelve months
#: make a year whatever the underwriting says; ten thousand basis points
#: make one whole. Naming these in config would invite an operator to
#: "tune" them.
UNIT_CONSTANTS = {12, 10_000, 10_000.0, 365, 1_000_000}

#: A literal inside these calls is a display precision or a loop bound,
#: not a threshold.
STRUCTURAL_CALLS = {"round", "range", "enumerate", "zip", "repeat"}

#: `{"gate": 4}` is an identifier for gate four, not a number four.
IDENTIFIER_KEYS = {"gate", "id", "order", "index", "tier", "level"}

#: Everything the rules above do not reach, keyed by (module, value) so
#: the entry survives the line moving. A reason is mandatory — an
#: allowlist of bare numbers is how the last audit's findings got there.
ALLOWED = {
    ("analysis/market.py", 5):
        "market-score tier: 'Strong' at 5 of 6 available points. The tiers "
        "are the scale's own shape, not a threshold anyone would override — "
        "moving them to config invites a 7 on a 6-point scale.",
    ("analysis/market.py", 3):
        "market-score tier: 'Moderate'. Same scale as above.",
    ("analysis/risks.py", 3):
        "list-length caps on the risk register (top-3 summaries, 3+ "
        "adjustments reads as 'several') and the default sort rank for an "
        "unrecognised severity. Presentation, and each is local to its line.",
    ("analysis/valuation.py", 3):
        "the year the revenue-growth ladder steps: rev_growth_1_3 through "
        "year 3, rev_growth_4_5 after. The boundary is NAMED BY THE CONFIG "
        "KEYS themselves, so config already owns it — a second statement as "
        "GROWTH_LADDER_SPLIT_YEAR could disagree with the key names, which "
        "is strictly worse than the literal.",
    ("model/solver.py", 1e-09):
        "float-comparison epsilon for 'did bisection land on its bracket "
        "edge'. A tolerance on IEEE754, not on a deal.",
    ("model/returns_model.py", 1e-12):
        "float-comparison epsilon for the sensitivity grid's span/step "
        "divisibility check. Same class as above.",
    ("model/value_add_model.py", 12):
        "the stabilisation window is measured in months and compared "
        "against a year; the 12s here are the month/year conversion, "
        "already covered by UNIT_CONSTANTS in every other module.",
    # ── Dataclass field defaults that RESTATE config ────────────────
    # These are the audit's most corrosive shape — a value config owns,
    # restated in code — and they are allowlisted only because
    # `test_dataclass_defaults_still_match_config` below pins each one
    # equal to its config key. Delete that test and these become live
    # shadow defaults again.
    ("model/debt.py", 25): "DebtTerms.amort_years default; == DEBT_TERMS['amort_years']",
    ("model/debt.py", 10): "DebtTerms.term_years default; == DEBT_TERMS['term_years']",
    ("model/debt.py", 0.65): "DebtTerms.max_ltv default; == DEBT_TERMS['max_ltv']",
    ("model/debt.py", 1.25): "DebtTerms.min_dscr default; == DEBT_TERMS['min_dscr']",
    ("model/debt.py", 0.1): "DebtTerms.min_debt_yield default; == DEBT_TERMS['min_debt_yield']",
    ("model/debt.py", 0.01): "DebtTerms.orig_fee_pct default; == DEBT_TERMS['orig_fee_pct']",
    ("model/waterfall.py", 0.08): "WaterfallTerms.pref_rate default; == config.PREF_RATE_LEVERED",
    ("model/waterfall.py", 0.2): "WaterfallTerms.promote_split default; == WATERFALL_TERMS['promote_split']",
    ("model/waterfall.py", 0.1):
        "WaterfallTerms.gp_coinvest_pct default; == config.GP_COINVEST_PCT, "
        "which config keeps OUT of WATERFALL_TERMS on purpose so the capital "
        "block owns it — making this dataclass default a third statement of "
        "the same number, and the one most worth pinning",
}


def _upper_snake(target) -> bool:
    return (isinstance(target, ast.Name)
            and target.id.isupper()
            and not target.id.startswith("_"))


def _collect_exempt(tree) -> set:
    """ids of Constant nodes exempted by CONTEXT rather than by value."""
    exempt = set()

    def mark(node):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant):
                exempt.add(id(sub))

    for node in ast.walk(tree):
        # A NAMED module constant is the pattern this sweep wants people
        # to use. Flagging `MONTHS_PER_YEAR = 12` would push the number
        # into config where an operator could edit it — the opposite of
        # the intent.
        if isinstance(node, ast.Assign) and any(_upper_snake(t) for t in node.targets):
            mark(node.value)
        elif isinstance(node, ast.AnnAssign) and _upper_snake(node.target) and node.value:
            mark(node.value)
        elif isinstance(node, ast.Call):
            name = (node.func.id if isinstance(node.func, ast.Name)
                    else getattr(node.func, "attr", None))
            if name in STRUCTURAL_CALLS:
                for arg in node.args:
                    mark(arg)
        elif isinstance(node, ast.Subscript):
            mark(node.slice)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value in IDENTIFIER_KEYS:
                    mark(value)
    return exempt


def _bare_literals(path):
    """(lineno, value, source line) for every unexplained numeric literal."""
    source = path.read_text()
    tree = ast.parse(source)
    lines = source.splitlines()
    exempt = _collect_exempt(tree)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        value = node.value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value in TRIVIAL or value in UNIT_CONSTANTS or id(node) in exempt:
            continue
        rel = str(path.relative_to(REPO))
        if (rel, value) in ALLOWED:
            continue
        out.append((node.lineno, value, lines[node.lineno - 1].strip()))
    return out


def _modules():
    for root in SWEPT:
        for path in sorted((REPO / root).rglob("*.py")):
            yield path


def test_no_bare_numeric_modeling_literal_survives_the_sweep():
    """Item T's last acceptance criterion, finally enforced by a test.

    A failure here is not automatically "move it to config". It is
    "decide, and write the decision down": either the number belongs to
    config, or it belongs to one of the KIND rules at the top of this
    module, or it earns an `ALLOWED` entry with a reason a reviewer can
    disagree with. What it may not do is sit in the code unexplained,
    which is the state the 2026-08-01 audit found roughly fifty of.
    """
    findings = []
    for path in _modules():
        for lineno, value, line in _bare_literals(path):
            findings.append(f"  {path.relative_to(REPO)}:{lineno}  "
                            f"{value!r}  |  {line}")
    assert not findings, (
        f"{len(findings)} bare numeric modeling literal(s):\n"
        + "\n".join(findings)
        + "\n\nMove each to config.py, bring it under a kind rule, or add an "
          "ALLOWED entry with a reason. See this module's docstring.")


@pytest.mark.parametrize("module,value", sorted(ALLOWED, key=str))
def test_every_allowlist_entry_still_describes_a_real_literal(module, value):
    """An allowlist that outlives its literal is a lie that grows.

    Once the value is gone, the entry silently starts exempting nothing —
    or worse, exempts a NEW literal that happens to share the value and
    was never reviewed. That is the exact failure the register's own
    `NOT_IN_REGISTER` completeness guard exists to prevent.
    """
    path = REPO / module
    assert path.exists(), f"ALLOWED names {module}, which no longer exists"
    tree = ast.parse(path.read_text())
    present = any(isinstance(n, ast.Constant)
                  and not isinstance(n.value, bool)
                  and isinstance(n.value, (int, float))
                  and n.value == value
                  for n in ast.walk(tree))
    assert present, (
        f"ALLOWED exempts {value!r} in {module}, which no longer contains it — "
        f"delete the entry rather than leaving it to exempt a future literal "
        f"nobody reviewed")


def test_every_allowlist_entry_carries_a_reason():
    for key, reason in ALLOWED.items():
        assert isinstance(reason, str) and len(reason) > 40, (
            f"{key} needs a reason a reviewer can disagree with, not a note")


def test_dataclass_defaults_still_match_config():
    """The load-bearing half of the debt/waterfall allowlist entries.

    `DebtTerms` and `WaterfallTerms` restate config's numbers as field
    defaults. In practice `resolve_debt_terms` / `resolve_waterfall_terms`
    supply every field from config, so the defaults fire only when a
    caller constructs the dataclass directly — which tests do constantly.
    That makes them exactly the audit's "duplicated key": a settings
    override moves the resolved path and leaves the default behind, and
    nothing announces the divergence.

    Pinning them equal is cheaper than deleting them (the dataclasses
    would lose their standalone usability) and strictly safer than
    trusting them to be kept in step by hand.
    """
    from model.debt import DebtTerms
    from model.waterfall import WaterfallTerms

    debt = DebtTerms()
    for field, key in (("amort_years", "amort_years"),
                       ("term_years", "term_years"),
                       ("max_ltv", "max_ltv"),
                       ("min_dscr", "min_dscr"),
                       ("min_debt_yield", "min_debt_yield"),
                       ("orig_fee_pct", "orig_fee_pct")):
        assert getattr(debt, field) == cfg.DEBT_TERMS[key], (
            f"DebtTerms.{field} drifted from config.DEBT_TERMS[{key!r}]")

    wf = WaterfallTerms()
    for field, key in (("promote_split", "promote_split"),):
        assert getattr(wf, field) == cfg.WATERFALL_TERMS[key], (
            f"WaterfallTerms.{field} drifted from config.WATERFALL_TERMS[{key!r}]")
    # `pref_rate` has no WATERFALL_TERMS key by design — the fund charges
    # 8% levered and 6% unlevered, so the default is a RESOLUTION, not a
    # constant. The dataclass mirrors the levered rate for direct
    # construction, which still needs pinning, just to a different name.
    assert wf.pref_rate == cfg.PREF_RATE_LEVERED, (
        "WaterfallTerms.pref_rate drifted from config.PREF_RATE_LEVERED")
    # Deliberately NOT a WATERFALL_TERMS key: config records that
    # `gp_coinvest_pct` lives in the capital block as GP_COINVEST_PCT
    # because `resolve_capital_structure` already reads it for Sources &
    # Uses, and a second copy is the divergence the single-source rule
    # forbids. The dataclass default is a third statement of it, which is
    # why it needs pinning here more than the others, not less.
    assert wf.gp_coinvest_pct == cfg.GP_COINVEST_PCT, (
        "WaterfallTerms.gp_coinvest_pct drifted from config.GP_COINVEST_PCT")


# ── The output layer, by shape rather than by value ──────────────────
#
# `output/` is not in SWEPT, and the docstring above says why: its
# literals are overwhelmingly layout, and a sweep flagging 174 point
# sizes gets switched off within a month. But "not swept" left a stated
# gap — a NEW modeling literal appearing in `memo_writer` or
# `excel_writer` was unguarded.
#
# The closing observation: in this layer, a modeling literal and a layout
# literal have different SHAPES. Layout flows into constructors and
# arithmetic (`Pt(9)`, `widths[i] * 72`); an underwriting threshold
# flows into an ORDERING COMPARISON against data (`if irr > 0.10:`).
# Every modeling literal Category 1 actually evicted from this layer —
# the recommendation threshold, the strong-IRR label cut, the
# sensitivity colour bands — was comparison-shaped. And today the layer
# contains ZERO ordering comparisons against a non-trivial literal, so
# the guard ratchets from clean rather than from an allowlist.

OUTPUT_SWEPT = ("output",)


def _comparison_literals(path):
    """(lineno, value, source line) for each non-trivial numeric literal
    on either side of `<`, `<=`, `>`, `>=` — the shape of a threshold."""
    source = path.read_text()
    lines = source.splitlines()
    out = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Compare):
            continue
        for op, comparator in zip(node.ops, node.comparators):
            if not isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                continue
            for side in (node.left, comparator):
                if (isinstance(side, ast.Constant)
                        and isinstance(side.value, (int, float))
                        and not isinstance(side.value, bool)
                        and side.value not in TRIVIAL
                        and side.value not in UNIT_CONSTANTS):
                    out.append((side.lineno, side.value,
                                lines[side.lineno - 1].strip()))
    return out


def test_no_threshold_comparison_hides_in_the_output_layer():
    """An output writer deciding `if irr > 0.10:` is a modeling opinion in
    a rendering module — the exact defect class Category 1 evicted (the
    recommendation threshold, the sensitivity colour bands, all
    comparison-shaped). The layer is clean today; this keeps it so.

    A failure means the comparison's literal belongs in config next to
    GATES / IRR_STRONG_THRESHOLD / SOLVER_TARGET_IRR, where the values
    this guard's predecessors evicted now live. It does NOT mean the
    comparison itself is wrong — move the number, keep the logic.
    """
    findings = []
    for root in OUTPUT_SWEPT:
        for path in sorted((REPO / root).rglob("*.py")):
            for lineno, value, line in _comparison_literals(path):
                findings.append(f"  {path.relative_to(REPO)}:{lineno}  "
                                f"{value!r}  |  {line}")
    assert not findings, (
        f"{len(findings)} threshold-shaped literal(s) in the output layer:\n"
        + "\n".join(findings)
        + "\n\nA rendering module comparing data against a bare number is "
          "holding a modeling opinion. Move the number to config.")


def test_the_comparison_finder_would_actually_catch_something():
    """Same discipline as the main sweep's self-test: prove the finder
    fires on the shape it exists for, and stays quiet on layout."""
    probe = ("from docx.shared import Pt\n"
             "def render(doc, irr, w):\n"
             "    doc.font.size = Pt(9)\n"          # layout — quiet
             "    col = w * 72\n"                    # layout arithmetic — quiet
             "    if irr > 0.10:\n"                  # threshold — caught
             "        doc.add_run('STRONG')\n")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "probe.py"
        p.write_text(probe)
        found = [v for _, v, _ in _comparison_literals(p)]
    assert found == [0.10], found


def test_the_sweep_would_actually_catch_something():
    """A guard nobody has seen fail is a guard nobody knows works.

    Runs the finder over a synthetic module rather than trusting that an
    empty result means the rules are sound — an over-broad exemption rule
    produces exactly the same green.
    """
    import tempfile

    probe = ("CAP = 0.055\n"                 # named constant — exempt
             "def f(x):\n"
             "    return x * 0.0725\n")      # bare threshold — must be caught
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "probe.py"
        p.write_text(probe)
        tree = ast.parse(probe)
        exempt = _collect_exempt(tree)
        found = [n.value for n in ast.walk(tree)
                 if isinstance(n, ast.Constant)
                 and isinstance(n.value, float)
                 and id(n) not in exempt]
    assert found == [0.0725], found
