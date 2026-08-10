# CIM Analyst — Agent Instructions

## What this project does
Analyzes a self-storage CIM (PDF) and produces:
1. A completed investment memo (.docx)
2. A returns model (.xlsx)
3. A terminal summary with PASS/FAIL gates and recommendation

## How to run
```bash
python manage.py runserver   # Web app (Django) — upload, assumptions, runs, results
python run.py                # CLI — prompts for a PDF filename in the current directory
```
The web app is the primary interface; the CLI remains for local one-off runs.
The settings editor stores dated deltas (`ConfigOverride` rows) on top of
`config.py` — it never mutates the file. The comps browser reads
`data/cim_comps.db` read-only.

## Simultaneous sessions (MANDATORY)
Multiple Claude sessions can share this one clone, and the primary working
tree is the collision zone: a concurrent session can switch its branch out
from under you, its uncommitted edits show up as foreign dirty state, and a
stray commit lands on the wrong branch. Rule set imported from
fcs-call-reports; enforced by git-tracked hooks in `.claude/settings.json` —
never remove, weaken, or bypass them:

- **SessionStart** `.claude/hooks/session-start-parallel-check.sh` — reports
  linked worktrees, foreign dirty state, and whether solo mode is on.
- **PreToolUse** `.claude/hooks/guard-shared-worktree.py` — DENIES file edits
  and git mutations targeting the primary working tree of this clone.
- **PostToolUse** `.claude/hooks/detect-primary-tree-writes.py` — REPORTS
  writes that landed in the primary tree anyway. The PreToolUse guard only
  inspects Bash commands whose command word is `git`, so `echo >`, `sed -i`,
  `cp`, an interpreter fed by a heredoc, and anything a script does all pass
  it untouched. Deciding that from a shell string is undecidable, so this
  hook does not predict — it snapshots the primary tree and compares after
  each Bash call, which is blind to HOW a write happened and so covers
  vectors nobody enumerated, plus a concurrent session's branch switch.
  **Its limits, because a safety tool you over-trust is worse than none:** it
  detects, it does not prevent (a report means "undo this now"); it cannot
  see gitignored paths at all, since `git status` is its only eye; and with
  concurrent sessions it cannot tell your write from theirs. If it cannot
  read the tree it says MONITORING DEGRADED rather than going quiet.
- Both hooks read `.claude/hooks/_shared_tree.py` for "which tree is primary"
  and "is solo mode on" — one definition, so they can never protect different
  trees. `tests/test_hook_shared_tree.py` is the CI gate on that.

**The mistake this catches, because it is easy to make:** `cd` does NOT
persist between Bash calls. A relative path in a later command therefore
resolves against the primary tree, not your worktree. Use absolute paths and
`git -C <worktree>` in every command that writes.

Working rules:
1. Before any file-mutating work, isolate:
   `git worktree add .claude/worktrees/<slug> -b <branch> origin/main`
   and do everything there. Commit early — branch refs are durable even if
   the worktree dir is lost; uncommitted files are not.
2. The session-start branch line is a point-in-time snapshot. Before every
   commit, re-assert `git branch --show-current` and read `git diff` in that
   exact directory; never commit foreign edits (per-path `git add`, never `-A`).
3. Once your branch is pushed and the PR is open, every remaining ship step is
   working-tree-independent: `gh pr merge --squash`, remote ref delete via gh,
   local ref delete via `git branch -D`. Never `git checkout` in the primary
   tree to "restore" it — that clobbers another session's WIP.
4. Deliberate solo work on the primary clone: launch with `CIM_SOLO=1`, or
   `touch "$(git rev-parse --git-dir)/cim-solo"` — the flag must be set in its
   own Bash call (the guard evaluates pre-tool), and the marker must be
   removed immediately after; leaving it disables the guard for every future
   session.

## When the user provides a CIM PDF
1. Run `python run.py` and provide the filename
2. If extraction is incomplete (CIM formats vary), Claude Code should
   manually read the PDF and fill in the `parsed_data` dict by hand
   from the PDF contents visible in context
3. Review the output memo and model for reasonableness before presenting

## Architecture
```
run.py                     # CLI entry point — file prompt, orchestration
engine.py                  # Analysis orchestration (extract_pdf_data / run_analysis) — the web↔pipeline boundary
config.py                  # Hard-coded investment criteria / thresholds (base for ConfigOverride deltas)
cimweb/                    # Django project (settings, urls, wsgi)
webapp/                    # Django app — views, models (Deal/AnalysisRun/ConfigOverride),
                           #   forms, services (deal folders), results, templates
extract/
  pdf_reader.py            # PDF text + table extraction (pdfplumber)
  parser.py                # Structured data extraction → CIMData dataclass
  location.py              # City/state/ZIP extraction — cover-page-first, broker-address
                           #   suppression, street-line trimming. Shared by parser.py and
                           #   scripts/cims_rename_plan.py; keep it the only copy.
analysis/
  filters.py               # Go/No-Go gate evaluation (7 gates)
  market.py                # Market & location analysis
  physical.py              # Property description, replacement cost
  financials.py            # Historical financial review, expense benchmarks
  rent_analysis.py         # Unit mix & rent analysis
  valuation.py             # THE unlevered projection (project_cash_flows) + scenario NOI forecast, IRR/MOIC
  value_add.py             # Operational improvement identification
  risks.py                 # Risk identification
  checks.py                # Model error-check register — is each input self-consistent?
  fills.py                 # Assumption fill log — which inputs did the CIM never supply?
                           #   Also `require_underwritable`, the three-input refusal
  assumptions.py           # Assumption register — where did EVERY number come from?
                           #   The three above are siblings by design: one assembly each,
                           #   built ONCE at the engine and handed to every surface, so the
                           #   memo, the workbook and the results page cannot disagree.
                           #   They stay separate because a check has a pass/fail axis, a
                           #   fill has none, and the register has a provenance axis instead
model/
  returns_model.py         # Unlevered DCF wrapper + sensitivity grid: Bear/Base/Bull
                           #   + Sources & Uses; sizes the ONE loan off the base case
  debt.py                  # Debt layer — min-of-three sizing (LTV/DSCR/debt yield),
                           #   monthly amortization roll-forward, origination/exit fees
  waterfall.py             # Single-tier LP waterfall — pref accrual, promote on the
                           #   LP-attributable residual, LP net IRR/MOIC
  levered.py               # The seam: levered equity CF, AM fee, reserve draw vs
                           #   capital call, then the waterfall. Assembles only —
                           #   it sizes no loan and distributes no dollar itself.
  solver.py                # Bisection solvers: max price for a 10% UNLEVERED IRR and
                           #   (item E4) for a 15% LP NET IRR — both kept, both shown
output/
  memo_writer.py           # Generates .docx from analysis outputs — the 10-section IC memo
                           #   AND `generate_investor_summary`, the LP-facing 2-page
                           #   condensation (item G). The summary is a second RENDERING
                           #   of the same result dicts, never a second computation, and
                           #   its 2-page limit is held by fixed sections + hard caps
  excel_writer.py          # Generates .xlsx returns model
  template_writer.py       # Pre-fills the XLSM underwriting template's INPUT cells.
                           #   Decides no value of its own — every number reads from the
                           #   resolved assumption set or the run's results, CI-gated by
                           #   an AST walk over its write paths (item E3b)
```

## Investment criteria (non-negotiable)
- Population ≥ 50,000 within 3-mile radius
- No unproven demand: physical occupancy ≥ 75%, and no post-2020 vintage
  still in ramp (< 85% physical). High-physical/low-economic occupancy
  (spread ≥ 10 pts) is the target mismanagement value-add profile, not an
  exclusion — always compare economic vs physical occupancy; a broker
  quoting one occupancy number is almost always quoting physical. A CIM
  that does not state physical occupancy at all is refused outright
  (`analysis.fills.require_underwritable`, CLI exit 2) — it used to render
  as a TBD gate and proceed on an assumed number; see design decision 9.
- Watch: deals whose entire bridge is ECRI in a falling street-rate market —
  the in-place-to-market gap closes from above; verify street-rate trend.
- Asking price ≤ replacement cost
- Base case unlevered IRR ≥ 10% over the hold, NET of transaction costs
  (default 5-year hold, editable 1–10; the config key stays `min_irr_5yr`
  so stored ConfigOverride rows keep resolving)
- Top-50 MSA or strong secondary market
- CIM Year 1 NOI ≤ 115% of TTM actual (flag if exceeded)
- Exit cap rate ≥ entry cap rate in base case

## Expense benchmarks ($/NRSF/yr, stabilized non-CC)
| Category       | Low    | High   |
|----------------|--------|--------|
| Property Taxes | $1.20  | $2.50  |
| Insurance      | $0.12  | $0.25  |
| Utilities      | $0.08  | $0.18  |
| R&M            | $0.20  | $0.40  |
| Advertising    | $0.05  | $0.15  |
| Payroll        | $0.30  | $0.60  |
| G&A            | $0.10  | $0.20  |
| Mgmt Fee       | 3%     | 6% EGR |
| Capital Reserve| $0.15  | $0.25  |
| Total OpEx     | $3.00  | $5.50  |
| OpEx/Revenue   | 35%    | 55%    |

## Replacement cost benchmarks
| Component        | Low    | High   |
|------------------|--------|--------|
| Non-CC $/SF      | $55    | $85    |
| CC $/SF          | $90    | $130   |
| Site work $/SF   | $5     | $12    |
| Soft costs       | 8%     | 12%    |
| Developer profit | 10%    | 15%    |

## Key design decisions
1. **Parser tolerance**: CIM formats vary wildly. The parser extracts what it
   can and flags gaps. Claude Code fills in missing data from PDF context.
2. **Analyst-adjusted NOI**: Never trust CIM expenses at face value. Uses
   max(CIM expense, benchmark midpoint) for lines that appear understated.
3. **The unlevered screen is primary and stays financing-free**: the
   headline IRR and MOIC ignore debt. Total basis = price + CapEx +
   acquisition closing costs + operating reserve; exit is net of
   disposition costs. **Financing costs are deliberately NOT in it** —
   an origination fee inside `total_basis` would move the primary 10%
   IRR gate the moment a deal named a loan, and an unlevered return
   charged a financing fee is not an unlevered return (item E3a,
   operator's call 2026-08-01, reversing E1's handoff). The Sources &
   Uses tie carries the term instead:
   `Total Uses == total_basis + financing_costs`, checked to the cent by
   `analysis.checks.sources_uses_ties`.
   `analysis.valuation.project_cash_flows` is the ONE projection — the
   scenario engine, the sensitivity grid and both solvers call it, and a
   second copy of that loop is how they drifted last time.
4. **Exit cap ≥ entry cap** in base and bear cases.
5. **Exit NOI is TRAILING, not forward**: `project_cash_flows`
   capitalizes the terminal hold year's OWN NOI (`noi_series[-1]`; year 5
   on a 5-year hold). `docs/levered-waterfall-design.md` and its
   reproduction in `tests/test_debt.py` (oracle 5) capitalize **year 6**,
   which is about 3% higher. Both are deliberate and both are tested —
   the debt module is exercised in isolation and never calls the
   canonical projection, while `tests/test_levered.py` pins the trailing
   convention the wiring actually uses. **If a levered figure looks ~3%
   off against the design doc, this is why, and it is not a bug in the
   debt math.** The underwriting judgment — a buyer at the end of year 5
   prices on year 6's NOI, which is the institutional norm — is deferred,
   not settled. Do not switch it silently.
6. **Levered returns are a second lens, on by default**: every deal is
   sized at `config.DEBT_TERMS` and carries an LP net IRR, but the
   unlevered screen above is unaffected by it. The loan is sized ONCE off
   the base case and carried through bear/base/bull — sizing per scenario
   would hand the bear case a smaller loan and flatten its own downside.
   Leverage is allowed to be dilutive and the model says so when it is.
   The lens surfaces on the Returns tab (below the unlevered tables, not
   on a tab of its own), as a level-2 subsection of memo section 6, and
   as the workbook's "Levered Returns" sheet. Its inputs are the
   assumptions page's "Debt & Waterfall" section — per-deal only, never
   settings-page editable, for the same in-place-mutation reason the
   capital block is not (item E3b).
7. **No LP net IRR without its assumption stamp**: five LPA questions are
   still open and each changes the number, so `model.levered` builds the
   resolved set and EVERY surface that prints a levered figure renders it
   beside that figure — the Returns tab, memo section 6, the workbook
   sheet, and the LP-facing investor summary (item G), which is the only
   one that leaves the firm and so the one where the rule binds hardest.
   `memo_writer._is_build` makes that structural rather than incidental:
   it nulls the whole levered payload when the stamp is absent, so no
   block can print a figure the stamp does not cover
   — including the AM fee's rate and base, which is what makes
   "net" mean anything. Three of those five conventions have exactly one
   implemented value (`accrual_base`, `am_fee_treatment`, `catch_up`) and
   the other value RAISES, so they deliberately get no form field: a
   dropdown whose second option crashes the run is a trap, not a setting.
8. **Bisection solvers**: Deterministic, ~20 iterations to 0.1% precision.
   There are now TWO max offers and they are both kept (item E4,
   operator's call 2026-08-01): `solve_max_price` targets the 10%
   UNLEVERED IRR — the price the primary gate is read against — and
   `solve_max_price_levered` targets the fund's 15% LP NET IRR, after
   debt service, the AM fee and the promote. Neither replaces the other;
   they answer different questions and are solved to different bars, so
   every surface prints the target beside the price.
   The levered solver re-sizes the loan at every candidate price. That
   does NOT contradict "sized once off the base case" (decision 6): that
   rule fixes sizing across bear/base/bull at ONE price, whereas here the
   price is the variable and a lender asked to fund a different price
   writes a different loan.
   **Monotonicity is the assumption bisection rests on, and it is
   CI-guarded, not assumed.** The exit-cap floor pushes the wrong way —
   below a certain price the exit cap is coerced up to the entry cap, so
   raising the price lowers it and raises exit value. It never actually
   inverts, because that gain lands at exit and is discounted at the very
   IRR the cheap price produces. `test_levered_objective_is_monotone_over
   _the_underwriting_range` pins that as a red test. The solver also keeps
   every price it evaluated and reports an observed inversion as
   `monotonicity_warning`; `coerced_region` beside it is ORDINARY DATA
   that fires on most deals, so nothing may raise a UI caveat off it.
9. **Occupancy is never assumed.** Physical occupancy joins NRSF and TTM
   NOI as a required underwriting input in
   `analysis.fills.require_underwritable`: a CIM that does not state it is
   refused at `engine.run_analysis` and `run.stage_analyze` (CLI exit 2)
   instead of rendering a TBD gate and proceeding, and the analyst enters
   it by hand on the Assumptions page. It is tested with `is None`, NOT
   the falsy check NRSF/TTM NOI use — a stated 0% is an honestly-reported
   pre-lease-up asset that the 75% demand gate already refuses with the
   right reason; an `is None` miss there would refuse a real asset for the
   wrong one. `VA_DEFAULT_OCCUPANCY` (0.80) and `VA_EGR_ASSUMED_OCCUPANCY`
   (0.85) are deleted outright rather than reconciled to one number — two
   constants answering the same question at different values, and BOTH
   fired in the same run. Measured on the value_add fixture (EGR-forced
   probe: occupancy, the rent override, unit mix and GPR all removed, so
   both constants fire on the same deal): when the two constants AGREE,
   the choice of number barely moves the result (base IRR 0.4812%–0.4841%
   across 0.80/0.85/0.90, 0.29bps of spread — small because the assumed
   occupancy moves the ramp start and the implied in-place rent in
   OFFSETTING directions); it's the two constants DISAGREEING that moves
   it — the mismatch that actually shipped (0.80 vs 0.85) cost 14bps
   (down to 0.3417%), and the widest mismatch (0.80 vs 0.90) cost 27bps
   (down to 0.2160%). Inside that probe the disagreement is 48-92x bigger
   than the agreement-spread, but that ratio is a property of the EGR
   probe, not of occupancy assumptions generally: isolating a SINGLE
   constant (only the stated occupancy blanked; unit mix and the EGR
   route both stay intact) swings base IRR 0.0706%–0.3254% — about 25bps,
   roughly 88x the 0.29bps figure, because the offsetting effect is gone
   once only one constant is live. **So the fix was never "pick the right
   number"** — even the smaller, agreement-case spread is not a safe
   default to lean on, and reconciling to a single value chosen wrong
   would still have shipped a live bug; only deleting the second constant
   closes it.
   `config.XLSM_TEMPLATE_INPUTS["assumed_physical_occupancy"]` (0.90) is
   the one surviving assumed occupancy left anywhere in the codebase, and
   belongs to item E3b, not this decision.
10. **Single-operator and in-process are CHOSEN, not overlooked.** An
    external UI review (2026-08-09) filed both as defects; they are not,
    and the next review will file them again unless the reasoning is
    written down.
    - **No owner FK on `Deal`, and every `get_object_or_404(Deal, pk=pk)`
      is unscoped.** Correct for a system whose signup is closed outright
      (`webapp.auth_adapter.ClosedSignupAdapter.is_open_for_signup`
      returns False) and whose only accounts come from
      `manage.py bootstrap_operator` reading a one-address
      `ALLOWED_EMAILS`. Adding an owner column and scoped querysets now
      would buy nothing and cost a migration on live deal history. It
      becomes real work the day a SECOND operator exists — and on that
      day it is the FIRST thing to do, before the account is created,
      because retrofitting ownership onto rows that predate it means
      guessing who owned what.
    - **Analysis runs in an in-process daemon thread, not a task queue.**
      The thread is the symptom; `webapp.services._patched_config` is the
      cause. It mutates `config.py`'s module-level dicts IN PLACE for the
      duration of a run — never rebinding, because importers hold the
      original dict objects — under a process-local `_ANALYSIS_LOCK`.
      So throughput is one run at a time per process BY CONSTRUCTION, and
      a queue is not a drop-in swap: global mutable config has to be
      threaded through as a parameter first, or every worker inherits the
      same serialization plus its own divergent copy of the patch state.
      This is the same in-place-mutation coupling decision 6 cites for
      why the capital and debt blocks are per-deal and never
      settings-page editable. Sequence any queue work behind that
      refactor; do not start with the queue.
    - **Django already sets `SECURE_CONTENT_TYPE_NOSNIFF` and
      `X_FRAME_OPTIONS`** (both since 3.0), so `cimweb/settings.py` sets
      neither and restating them would only invite drift. The defaults
      are pinned by `test_security_headers_present_in_production_branch`,
      so a future Django flipping one fails CI instead of silently
      dropping the header. An audit calling them "missing" has read the
      settings file, not the response headers.
11. **Every number discloses its provenance** (item T Category 6).
    `analysis/assumptions.py` is the register: every value that moved an
    output, with the one of five provenances that produced it — `deal`
    (entered on the assumptions page), `settings` (a dated
    `ConfigOverride` row), `fallback` (invented; the Category 4 fill log),
    `cim` (stated in the CIM), `config` (the shipped default). Precedence
    is the model's own and each assumption yields exactly ONE row carrying
    the winner, with `was` holding what it displaced; printing both the
    superseded and the applied value is how a reader ends up auditing a
    number the engine never used.
    **It resolves nothing.** Every value is read from live `config` —
    patched in place for the duration of a run, so a live read IS the
    effective value — or through THE resolver the model itself calls
    (`resolve_hold_years`, `resolve_mgmt_fee_target`, `resolve_target_irr`,
    `get_regional_benchmarks`). A second derivation would be item T's own
    defect wearing its badge. The `config_deltas`/`config_defaults`/
    `deal_overrides`/`cim_snapshot` parameters on `run_analysis` are
    PROVENANCE ONLY and change no arithmetic; the delta dicts are read for
    MEMBERSHIP, never for their values, so a delta that disagreed with
    live config could not make the register print a number the run did not
    use. `config_defaults` is captured in `webapp.services` BEFORE
    `_patched_config` mutates anything — inside the lock the live value IS
    the override, and a register asking config what it used to be would
    report "settings override, was 8%, now 8%".
    Surfaces: memo **Appendix B** (B.1 = only the rows a human or a
    fallback produced, B.2 = the whole register), the workbook's Inputs
    sheet, and a collapsed panel on the results Summary tab. Appendix A
    stays beside B rather than folding into it: "what did the model
    invent?" is a sharper question than "what did the model use?", and
    nine invented numbers inside a hundred and forty do not read as an
    answer to it. The LP investor summary is deliberately excluded — it is
    two pages held by a content budget (item G) and already carries the
    fill count.
    **Membership is CI-enforced, not curated**:
    `test_every_settings_editable_key_is_in_the_register_or_declared_out`
    walks `override_key_registry()` (derived live from config.py) and
    fails unless each key produces a row or appears in `NOT_IN_REGISTER`
    with a stated reason. A new config key defaults to FAILING, because a
    completeness claim maintained by memory stops being true the first
    month nobody remembers it. `MARKET_CAP_RATES` is the sole exemption:
    the table holds twelve cells, one of which priced this exit, and the
    resolved anchor is reported instead.

## Manual steps flagged by the program
- Population verification (if not in CIM)
- Comp rent verification (CIM data taken at face value initially)
- Physical condition assessment (requires site visit)
- Property tax reassessment calculation
- New supply pipeline confirmation

## Dependencies
```
pip install -r requirements.txt
```
Requires: pdfplumber, python-docx, openpyxl, numpy-financial
