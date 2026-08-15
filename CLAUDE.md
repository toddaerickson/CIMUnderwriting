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
  **Exactly one file is exempt**: `<primary .git>/cim-session-notes.md`, the
  board the carry-over section below REQUIRES every session to maintain by
  hand. It is not a hole — `is_session_notes` matches that one basename, in a
  directory that must RESOLVE to the primary git dir, and never through a
  symlink. Everything else in the git dir stays denied, `.git/config` and
  `.git/hooks` emphatically so. Before the exemption the guard and this file
  contradicted each other, and it was not a decision about the notes: the
  check reduces a path to its containing DIRECTORY, so the filename never
  reached it. The only route left was the CIM_SOLO hatch, which switches the
  guard off clone-wide — a global bypass as the standing workflow for a
  routine act, which is how an operator learns to flip solo mode casually.
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

## Context resets (long unattended sessions)

`autoCompactWindow` in `.claude/settings.json` is set to **300,000 tokens** —
compaction fires there rather than at the model's default. No hook does this
and none can: every hook event is documented as unable to trigger a compact or
a clear, so the setting is the mechanism and the hooks below only preserve
state across it.

- **PreCompact** `.claude/hooks/precompact-carryover.py` — snapshots the
  DERIVABLE state (which worktree, which branch, commits not yet on
  origin/main, uncommitted paths, the worktree list) to
  `<primary .git>/cim-carryover-<sessionhash>`. It never blocks: exit 2 on
  this event blocks the compaction the operator asked for, so every path
  exits 0.
- **SessionStart** `.claude/hooks/session-start-carryover.py` — restores it,
  but only when `source` is `clear` or `compact`. `resume` already has the
  real transcript and `startup` is a genuinely new session; re-injecting
  beside either is noise arguing with the record.
- `<primary .git>/cim-session-notes.md` is the half that matters and the half
  no hook writes. **Maintain it by hand during long runs** — which phase of a
  plan is in flight, what was decided and why, what is next. The snapshot
  covers what a machine can rebuild; the notes cover what it cannot. It is one
  file per clone, not per session, deliberately: concurrent sessions sharing a
  task board is a feature, and keying it by session would lose it at exactly
  the moment `/clear` mints a new session id.
- Both carry-over hooks read their paths from `_shared_tree.py` for the same
  reason the guard pair does. The notes filename must never share
  `CARRYOVER_PREFIX`, or the snapshot sweep deletes it;
  `test_the_notes_file_is_not_matched_as_a_snapshot` pins that. The GUARD now
  reads `CARRYOVER_NOTES` from there too, so a rename cannot silently
  re-deny the file — three hooks, one definition.

## Shipping work (worktree → PR → CI → merge → cleanup)

These rules used to sit under "Compact instructions", where they read as
guidance about compaction. They are not — they are how work leaves this clone.
Rules 1, 3 and 4 are cited BY NUMBER in the guard's runtime deny strings and in
`tests/test_hook_shared_tree.py`, so extend this list; never renumber it.

1. Before any file-mutating work, isolate:
   `git worktree add .claude/worktrees/<slug> -b <branch> origin/main`
   and do everything there. Commit early — branch refs are durable even if
   the worktree dir is lost; uncommitted files are not.
2. The session-start branch line is a point-in-time snapshot. Before every
   commit, re-assert `git branch --show-current` and read `git diff` in that
   exact directory; never commit foreign edits (per-path `git add`, never `-A`).
3. Once your branch is pushed and the PR is open, every remaining ship step is
   working-tree-independent: `gh pr merge --squash`, after which the remote
   head ref deletes itself (`delete_branch_on_merge` is on). Local cleanup is
   `git worktree remove <path>` — unforced, the form the guard allows and the
   one git refuses on the main tree outright — then a BEST-EFFORT
   `git branch -d`. **Expect `-d` to refuse after a squash merge**: the squash
   is a different commit, so git's reachability test reports the branch as not
   merged, and `-D` — which exists to bypass exactly that check — is denied by
   the guard on purpose, because once the worktree is gone the ref is the only
   durable copy of the work (rule 1). The stale local ref is therefore the
   expected end state, not a cleanup step you failed; it is harmless, so leave
   it. The guard's deny message does offer CIM_SOLO for pruning it, and a tidy
   branch list is not worth opening that window. Never `git checkout` in the
   primary tree to "restore" it — that clobbers another session's WIP.
4. Deliberate solo work on the primary clone: launch with `CIM_SOLO=1`, or
   `touch "$(git rev-parse --git-dir)/cim-solo"` — the flag must be set in its
   own Bash call (the guard evaluates pre-tool), and the marker must be
   removed immediately after; leaving it disables the guard for every future
   session.
5. **A PR THIS SESSION OPENED is yours to merge once CI is green** — squash,
   whatever files it touches, no second ask (operator's standing call,
   2026-08-15). It is written here, git-tracked, precisely so it cannot be
   re-narrowed by a stale line in the session notes; scoping it per-PR there is
   what had three separate sessions re-asking for permission already granted.
   A PR another session or the operator opened is never yours to merge: you do
   not hold the context that produced it, and "it looks fine" is not review.
   Ask. **And "I opened it" is not derivable from GitHub** — every PR in this
   repo is authored by the same account, so `gh pr view --json author` cannot
   tell yours from anyone's. The evidence is your own worktree plus the line
   you wrote in the session notes (rule 7). After a compact, if the notes do
   not say you opened it, you did not.
6. **Green means all three jobs concluded SUCCESS on the head SHA** — `test`,
   `page-budget` and `smoke-pg` in `.github/workflows/test.yml`. Read the three
   job conclusions, never the rollup: `main` carries no required status checks,
   so nothing server-side refuses a red or untested merge. The reading IS the
   gate.
   **A skipped job is not a pass, and this has already fooled a session here.**
   `test` is gated on
   `if: github.event.action != 'edited' || github.event.changes.base != null`
   and `smoke-pg` declares `needs: test`, so merely editing a PR's title or
   body mints a run in which TWO of the three never execute, leaving only
   `page-budget`. That run's overall conclusion is still `success`, and
   `gh pr checks <n>` prints `skipping` for the other two and EXITS 0 — so the
   exit code, the rollup and "the newest run" all report green on a PR nothing
   tested. Verified on #79: run 31893593150 ran all three; the two body-edit
   runs after it skipped `test` and `smoke-pg`. Gate on the newest run that
   actually RAN the suite.
7. **Then clean up, in this order**: `gh pr merge --squash`, `git worktree
   remove <your worktree>`, best-effort `git branch -d` (rule 3), and write
   what landed into `<primary .git>/cim-session-notes.md` — the PR number, the
   squash commit, what is still open. That note is also the only durable record
   of which session opened which PR, which is what rule 5 leans on.
   **Do not pass `--delete-branch`.** Its remote half is redundant: this repo
   has `delete_branch_on_merge` on, and `git ls-remote --heads origin` shows
   only `main`. Its LOCAL half makes gh run git inside this clone, which the
   guard cannot see at all — it classifies only Bash segments whose command
   WORD is `git` — so the flag quietly performs the `branch -D` the guard
   spent six versions learning to refuse. Reaching for an unobserved path
   because a guarded one said no is how a safety tool becomes decorative.
8. **A merged change under `.claude/` is not live until the primary tree
   syncs.** `settings.json` resolves hook paths through `${CLAUDE_PROJECT_DIR}`,
   so your worktree runs your copy while a session rooted in the primary clone
   keeps running its own — a hook fix can be merged and firing nowhere that
   matters. Not hypothetical: #82 taught the guard to allow the one file rule 7
   orders you to write, and the primary tree went on denying it.
   You may run the sync yourself, but only when all five hold, CHECKED and not
   assumed: `git -C <primary> status --porcelain` is EMPTY; its current branch
   is `main`; `git -C <primary> rev-list --count origin/main..HEAD` is `0`;
   `git worktree list` shows no linked worktree but your own; and
   `<primary .git>/cim-solo` does NOT already exist — if it does, another
   session is inside its own hatch and your `rm` would strip its protection
   mid-flight. Then `git fetch origin --prune` FIRST, or you sync to a stale
   remote-tracking ref and read the no-op as success. Then
   `touch "$(git -C <primary> rev-parse --git-dir)/cim-solo"` in its OWN Bash
   call (rule 4), then ONE call: `git -C <primary> pull --ff-only; rm -f
   <marker>` — `;` and not `&&`, so a failed pull still clears the marker —
   then a third call confirming it is gone.
   Expect the PostToolUse detector to report that the primary tree's HEAD
   moved, in your session and in every concurrent one. For THIS operation that
   report is the receipt, not a collision to undo.
   If any precondition fails, do not stash and do not force. A stale hook copy
   in the primary tree is degraded enforcement, not a blocked session, and it
   never affects work done in a worktree branched from `origin/main`. Say so in
   the session notes and leave the sync to the operator.

# Compact instructions

When compacting this session, preserve above all else: the absolute path of
the worktree being committed into and its branch name; which phase of the
in-flight plan is done versus pending; any deliberate deviation from a plan
and the reason for it; test failures and their output; and decisions the
operator made by hand (they cannot be re-derived from the code). Drop file
listings, full test output for passing runs, and exploratory reads that led
nowhere.

**The mistake this catches, because it is easy to make:** `cd` does NOT
persist between Bash calls. A relative path in a later command therefore
resolves against the primary tree, not your worktree. Use absolute paths and
`git -C <worktree>` in every command that writes.

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
  waterfall.py             # Single-tier LP waterfall — pref accrual, promote off the
                           #   top then a pro-rata split, LP net IRR/MOIC
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
5. **Exit NOI is a named convention, default TRAILING** (settled
   2026-08-10; it was "deferred, not settled" until then).
   `config.EXIT_NOI_CONVENTION` (`"trailing"` | `"forward"`) governs
   BOTH exit engines — `project_cash_flows` and the value-add model's
   own exit — through the one selector
   `analysis.valuation.resolve_exit_noi`, read at CALL time (a scalar
   reached by `from config import` freezes at import). Trailing
   capitalizes the terminal hold year's OWN NOI (`noi_series[-1]`);
   forward is one more step of the SAME rev/exp series at the rates in
   force for year N+1 — NOT NOI×(1+g), because the two series grow at
   different rates (measured on the standard fixture: base differential
   ≈ +2.2% exit NOI, bear ≈ +0.4%, bull ≈ +3.8%).
   The default is trailing so no published number moved when the name
   arrived; flipping it is a deliberate operator act whose delta must be
   enumerated (item T's snapshot discipline), and the convention is a
   register row so every run discloses which one priced its exit. It is
   deliberately NOT settings-editable or per-deal — comparability across
   the pipeline. `docs/levered-waterfall-design.md` and its reproduction
   in `tests/test_debt.py` (oracle 5) capitalize **year 6** — the
   forward convention in a SINGLE-RATE fixture, about 3% higher there;
   same convention, different arithmetic from the implemented forward
   branch, so do not expect them to tie to the cent. **If a levered
   figure looks ~3% off against the design doc, this is why, and it is
   not a bug in the debt math.** `tests/test_levered.py` pins the
   trailing default; `tests/test_exit_noi_convention.py` pins the
   forward branch in both engines.
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
7. **No LP net IRR without its assumption stamp**: six LPA questions
   each change the number — as of 2026-08-12 **five CONFIRMED and one
   made MOOT by a confirmation; none open** — so `model.levered` builds the
   resolved set and EVERY surface that prints a levered figure renders it
   beside that figure — the Returns tab, memo section 6, the workbook
   sheet, and the LP-facing investor summary (item G), which is the only
   one that leaves the firm and so the one where the rule binds hardest.
   `memo_writer._is_build` makes that structural rather than incidental:
   it nulls the whole levered payload when the stamp is absent, so no
   block can print a figure the stamp does not cover
   — including the AM fee's rate and base, which is what makes
   "net" mean anything. `am_fee_treatment` and `catch_up` have exactly one
   implemented value and the other RAISES, so they deliberately get no
   form field: a dropdown whose second option crashes the run is a trap,
   not a setting.
   **Which questions have actually been READ is state, not memory** (item
   E4): `config.LPA_CONFIRMED` maps a question key to the date the
   operator confirmed it, and `model.waterfall.assumption_stamp` stamps
   every row `confirmed` / `moot` / `open`. Three states because "the LPA
   says this" and "this cannot move the number given something else the
   LPA says" are different claims — `pref_compounding` is confirmed
   (annually compounded, 2026-08-09) and that MOOTS `ordering`, since
   ROC-before-pref only moves a dollar under a simple pref. Nobody read
   the ordering clause; it stopped mattering. A key absent from
   `LPA_CONFIRMED` stays `open`, so a new convention cannot inherit
   someone else's confirmation. The LP-facing caveat follows the rows
   that still need it rather than blanketing all six — and after
   2026-08-12 that is NO rows, so the LP document's "proposed terms,
   subject to the final partnership agreement" clause stopped rendering
   at all. **That is the caveat variant nobody has reviewed against a
   rendered document** — `docs/gc-review-investor-summary.md` question 7
   asked counsel to set all three variants and got an ASSUMED approval
   on the one that rendered that day. Nothing may hard-code "all
   confirmed": the counts come from `LPA_CONFIRMED` at render time, so a
   seventh question or an amended LPA reopens them with no code change.
   **The 2026-08-12 reading, because two of its results are easy to
   misread.** (a) The LPA says the pref accrues on COMMITTED capital,
   where the build had assumed contributed/unreturned — and NO NUMBER
   MOVED, because the operator's reading of the clauses (what is
   committed is funded at close; a later call accrues from its own date;
   the base falls as capital is returned) describes the accrual this
   model already ran. The two values are one arithmetic here, pinned by
   `test_the_two_accrual_bases_agree_to_the_cent` and NOT by assertion —
   and the equivalence has a stated precondition (no uncalled commitment
   is expressible) that a future commitment schedule would break. The
   stored value changed anyway: a stamp that says "contributed" beside
   terms that say "committed" is a disclosure disagreeing with the
   document it discloses. (b) `catch_up` became a SIXTH stamp row rather
   than staying a silent scope decision — a catch-up moves the promote
   materially and "20% promote on the residual" does not tell an LP
   whether one exists. "No catch-up **at this time**" is exactly what a
   dated confirmation records.
   (c) **The promote reading is the one that nearly moved money, and the
   reason it did not is worth more than the answer.** The LPA charges the
   promote on ALL capital, and the GP's co-invest earns the pref
   alongside the LPs'. The second half was already the model — tier 1 has
   always been pari passu. The first half reads TWO ways and they differ
   by real dollars: with `c` the co-invest, `x` the promote split and `R`
   the residual, the promote is either `x·R·(1−c)` — taken off the top,
   remainder split pro rata, GP share `x + (1−x)c` — or `x·R`, charged on
   the whole residual on top of the GP's pro-rata slice, GP share `c + x`.
   At 20% and 10% that is 28% versus 30%, and the `x·c·R` between them
   comes out of the LP.
   **The fund's own model workbook settles it**:
   `Self-Storage-Acquisition-Model-v1.3.xlsm`, `Underwriting!J250 =
   I250+(1-I250)*$J$244` — `x + (1−x)c`, which is the arithmetic the
   build already ran. So `promote_basis` is CONFIRMED and NO NUMBER
   MOVED. The first implementation of this row picked the other reading
   and moved every levered figure in the repo; the operator sent it to
   the workbook, which is the same correction the 8%/6% pref rate got
   three days earlier — and the AM-fee base in (d) below made it
   **three times in six days that the XLSM has been the more considered
   artifact. Consult the workbook BEFORE resolving an ambiguous fund
   term in code, not after.**
   Both bases stay implemented and tested
   (`PROMOTE_BASIS_PROMOTE_THEN_SPLIT` shipped,
   `PROMOTE_BASIS_SPLIT_THEN_PROMOTE` beside it) rather than the loser
   being deleted: only one existed before, nothing in the codebase stated
   the other, and that silence is exactly what let the ambiguity hide.
   The property that distinguishes them is asserted in both directions —
   under the shipped basis every LP flow carries the factor `(1−c)` so
   the LP's IRR, MOIC and the levered max offer are INVARIANT to the GP's
   co-invest; under the alternative they fall.
   (d) **The AM fee is charged on LP EQUITY, not on invested equity**
   (`config.AM_FEE_BASE = "lp_equity"`, operator 2026-08-14: *"a GP does
   not charge an asset management fee on their own personal or internal
   co-investment"*). This is the one row in this decision where a
   confirmation MOVED MONEY: the build charged the fee on GP+LP from
   2026-08-01, overstating it by exactly the co-invest share — 11.1% too
   much at a 10% co-invest — on every deal since. `invested_equity`
   RAISES now; it was not renamed and no second base survives, per
   decision 9. The workbook had it right the whole time
   (`Underwriting!G244` is a dropdown reading "% of LP Equity",
   `H245 = K61*G245/12`, and `K61` is LP Equity), which is why (c)'s
   lesson now reads *three times*.
   **Two consequences worth stating, because they read as bugs.**
   The unlevered screen does not move at all — decision 3 keeps the fee
   out of `total_basis`, so a fee change that touched an unlevered figure
   would be the defect. And the levered max offer now RISES with the GP's
   co-invest, which does NOT contradict the invariance asserted just
   above: that invariance is a property of the PROMOTE, and the fee is a
   separate deduction that shrinks as the GP funds more of the stack.
   `tests/test_solver.py` therefore isolates the two — the promote's
   invariance is asserted at `am_fee_pct=0.0`, and the fee's effect gets
   a test of its own.
   **The pref rate is two rates** (`config.PREF_RATE_LEVERED` 8% /
   `PREF_RATE_UNLEVERED` 6%), resolved per deal by
   `model.waterfall.resolve_pref_rate` and overridable per deal on the
   assumptions page. It is keyed on the deal's INTENT to lever
   (`DebtTerms.is_levered`, i.e. `max_ltv > 0`), never on the loan the
   sizer produced: a deal can name 65% leverage and size to $0 on a weak
   debt yield, and reading the outcome would also step the rate mid-solve
   inside `solve_max_price_levered`, which re-prices the loan at every
   candidate — the discontinuity decision 8's monotonicity guard exists
   to forbid. `pref_rate` therefore has NO key in
   `config.WATERFALL_TERMS`; a third answer there would be the
   two-constants-disagreeing failure decision 9 already paid for.
   **This rule was in the repo as a bug for months**: the v1.2 XLSM
   shipped `IF(H64>0, 0.08, IF(H64=0, 0.06, "n/a"))` and
   `output/template_writer.py` overrode it with a comment calling
   leverage-dependence "not a term in the LPA". The template was right.
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
`requirements.txt` is the authority. The pipeline needs pdfplumber,
python-docx, openpyxl, numpy-financial; the web app (the primary interface)
additionally needs the pinned Django stack — Django, django-allauth,
django-environ, django-htmx, whitenoise, gunicorn, psycopg — plus
pytest/pytest-django for the suite.
