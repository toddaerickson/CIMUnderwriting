# Levered returns + preferred-return waterfall — research & design

> **Exit-NOI note (2026-08-10):** this document's oracles capitalize
> YEAR 6 — the forward convention. The shipped default is
> `config.EXIT_NOI_CONVENTION = "trailing"` (the terminal hold year's
> own NOI), and the implemented forward branch steps revenue and
> expenses at their separate rates, so it is the same convention as
> these oracles but not the same arithmetic. See CLAUDE.md design
> decision 5.

Research date: 2026-07-28 (web sources; fixture arithmetic verified in Python).
Status: BUILT — shipped as items E1–E4 in the order scoped in
[scoped-backlog.md](scoped-backlog.md) (E1 debt → E2 waterfall → E3 wiring →
E4 solver): `model/debt.py`, `model/waterfall.py`, `model/levered.py`,
`model/solver.py`. This file remains the design record; its numeric oracles
are reproduced in `tests/test_debt.py`. The LPA confirmations at the bottom
were the original stated blocker and stopped being one (PR #21): they
ship as named parameters carrying documented defaults, and every LP net IRR is
displayed with its resolved assumption set. **Most of the LPA has since been
read** — 2026-08-09 and 2026-08-12, and as of the second date NO convention
is outstanding. Every stamp row carries a date or is moot. The LP net IRR is
decision-grade on the conventions; what remains is ordinary underwriting
judgment about the inputs.

Fund terms (from operator; the pref and the AM-fee placement confirmed against
the LPA 2026-08-12): pref **8% levered / 6% unlevered**, per-deal adjustable ·
20/80 promote above pref · no catch-up · ~10% GP co-invest · 1% AM fee **above
the waterfall** · 15% LP **net** IRR target.

**Scope decision (operator, 2026-07-29): ONE TIER.** GP charges a management
fee, co-invests capital upfront, and earns an x% promoted interest above a y%
preferred return. That is the single-hurdle case §B proves is deterministic — a
forward accrual loop, no solver. Pref rate and promote split are parameters; the
tier *count* is not. No configurable multi-tier builder.

## A. Levered model structure

**Debt sizing** = min(LTV cap, DSCR cap, debt-yield cap) — never LTV alone.
Storage terms 2025–26: banks 65–75% LTV, 1.25x DSCR, 20–25yr amort, ~5.5–6.5%
fixed, step-down prepay, recourse; bridge 75–80% LTC, 1–3yr IO, SOFR+265–500bps
(~9–12% all-in), non-recourse; CMBS 65–75% LTV, 30yr amort, defeasance/YM
prepay. Debt yield floor 8–10%. No Fannie/Freddie execution for storage; SBA is
owner-operator only. Sources: terrydalecapital.com Q4-2025 storage rates;
nav.com storage financing; commercialrealestate.loans/self-storage-financing.

**Levered CF**: NOI − debt service − reserves; exit year adds (sale − selling
costs − payoff balance). Equity = price + capex + closing/financing costs −
proceeds. Bridge→perm refi at stabilization: payoff + exit fee; perm sized on
stabilized NOI; refi proceeds above payoff = positive equity CF (pre-tax).

**Common errors to design out**: debt as one line instead of a monthly balance
roll-forward; IO→amort transition payment not recomputed; prepay/exit fees
ignored at sale or refi (CMBS YM can be 3–5% of balance); DSCR tested on the
wrong NOI basis (model trailing AND stabilized); max-leverage sizing with no
covenant headroom.

## B. Preferred return waterfall

- Pref is cumulative; live conventions: simple vs **annually compounded**
  (most common), on contributed/unreturned capital (standard) vs committed
  (rare). $100M × 4yr × 8%: $32M simple vs $36.05M compounded. The LPA reading
  (2026-08-12) landed on the rare one — committed — which in this model is the
  same arithmetic; see question 2 below for why, and for the precondition that
  would separate them.
- Single-asset syndication behaves European-per-deal (return all capital +
  pref before promote). No catch-up → GP never recovers pref leakage; 80/20
  applies to residual only.
- **Ordering (ROC vs pref)**: mathematically irrelevant under a compounded/
  IRR-hurdle pref; changes the answer when pref is simple. Same flows give GP
  promote **$68,465 / $72,000 / $81,600** (compounded / simple-pref-first /
  simple-ROC-first).
- Single hurdle == pref with annual periods → the accrual-account formulation
  (balance ×1.08 − distributions) is exactly the 8% IRR hurdle: deterministic,
  no solver. Iteration only for multi-hurdle/XIRR day-count variants. Keep
  annual end-of-period to match npf.irr conventions.
- Pitfalls: pref on gross vs net of the 1% AM fee; GP co-invest pari passu
  through ROC+pref but promote computed on 100% of residual instead of the
  LP-attributable share (see question 5 — the LPA charges it on all capital,
  and the fund's model workbook settles WHICH arithmetic that means);
  clawback — avoided structurally by paying no promote
  until Tier 1 is current each period.
- Key sources: adventuresincre.com (waterfall model + ROC/pref timing
  article), tacticares.com (simple vs IRR), amundsendavislaw.com and
  velaw.com (LPA drafting), apers.app (GP co-invest mechanics).

## Design

**`model/debt.py`** — `DebtTerms` dataclass (`loan_type`, `rate` or
`sofr+spread`, `amort_years`, `io_months`, `term_years`, `max_ltv`,
`min_dscr`, `min_debt_yield`, `orig_fee_pct`, `exit_fee_pct`, `prepay`).
`size_loan(price, y1_noi, stabilized_noi, terms) -> (loan, binding_constraint)`
(min-of-three); `amort_schedule(...)` monthly roll-forward aggregated to
annual DS + payoff; optional `refi_event(year, new_terms)`.

**`model/waterfall.py`** — pure function, `WaterfallTerms` dataclass
(`pref_rate=0.08`, `pref_compounding="annual"|"simple"`,
`ordering="roc_first"` (simple only), `promote_split=0.20`,
`gp_coinvest_pct=0.10`, catch_up explicitly unsupported).
`run_waterfall(contributions, distributions, terms) -> WaterfallResult` with
per-period LP/GP rows + LP net IRR/MOIC. Forward loop, no solver. GP co-invest
pari passu through tier 1 (confirmed 2026-08-12); promote on all capital,
taken off the top with the remainder split pro rata — GP share `x + (1−x)c`,
per `Underwriting!J250` in the fund's model workbook (confirmed 2026-08-12;
arithmetically what this design already specified). 1% AM fee
deducted as a cash-flow line before the waterfall (flagged assumption).
Unlevered screen stays primary; levered + waterfall is a second lens.

**Unit-test oracles** (flows: −$1,000,000; then 50k, 60k, 70k, 80k, 1,500,000):
1. Compounded 8%, no catch-up, 20% promote: balance path 1,080,000 →
   1,112,400 → 1,136,592 → 1,151,919.36 → 1,157,672.9088; year-5 tier 1 LP
   1,157,672.91; residual 342,327.09 → GP **68,465.42**; LP total
   1,691,534.58; LP IRR **12.1340%**; LP MOIC 1.6915.
2. Simple, ROC-first: pref accruals 80,000/76,000/71,200/65,600/59,200
   (total 352,000); GP **81,600.00**; LP IRR 11.9500%.
3. Simple, pref-first: accrual 80,000/yr; unpaid pref yr5 140,000; GP
   **72,000.00**; LP IRR 12.0846%.
4. Debt schedule ($6.5M, 6.50%, 30yr monthly amort, 24mo IO): IO DS
   422,500.00/yr; amort pmt 41,084.42/mo (492,013.06→ see note: annual
   493,013.06, constant 7.5848%); payoff end month 60 = 6,267,120.72.
5. Sizing + levered IRR: $10M price, $600k Y1 NOI, 65%/1.25x/10% DY @
   6.5%/30yr → constraints 6,500,000 / 6,328,432.78 / 6,000,000 → loan
   6,000,000 (debt-yield bound). Equity 4,100,000; DS 455,088.98; payoff
   5,616,658.65; exit 11,129,031.11 (6.25% cap on Y6 NOI, 3% growth, 1%
   closing); CFs [−4,100,000; 144,911.02; 162,911.02; 181,451.02;
   200,547.22; 5,732,588.78]; levered IRR **9.9952%**, MOIC 1.5664 vs
   unlevered 8.0154%.

## ⚑ LPA confirmations required (answers change the numbers)

Status 2026-08-12: **none is open.** 6 and 7 were answered 2026-07-29;
1 was read 2026-08-09; 2, 4 and the catch-up question were read 2026-08-12, and
that confirmation MOOTED 3. The machine-readable version of this list is
`config.LPA_CONFIRMED` (key → date read) — it, not this paragraph, is what the
assumption stamp renders from, so a row confirmed here and not there still
prints as open to an LP.

1. 8% pref: simple or compounded? At what frequency? (moves GP promote ~19%)
   — **CONFIRMED 2026-08-09**: annually compounded.
2. Accrual base: contributed/unreturned capital or committed? — **CONFIRMED
   2026-08-12**: committed. Under the surrounding clauses (committed equity
   funds at close, a later call accrues from its own date, the base falls as
   capital is returned) this is arithmetically the same accrual the model
   already ran; `test_the_two_accrual_bases_agree_to_the_cent` proves it, and
   `model/waterfall.py` records the precondition that would separate them.
   The pref RATE came with it: 8% levered, 6% unlevered, per-deal adjustable.
3. ROC-vs-pref ordering as written (matters only if simple). — **MOOT**: 1
   confirmed a compounded pref, under which the ordering cannot move a dollar.
   Nobody read the clause; it stopped mattering.
4. 1% AM fee: above the waterfall (deal expense) or netted from LP
   distributions? — **CONFIRMED 2026-08-12**: above the waterfall.
5. GP co-invest: earns pref pari passu, promote on residual net of GP
   pro-rata share? — **CONFIRMED 2026-08-12.** The co-invest earns the pref
   pari passu (as built), and the promote is earned on ALL capital. That
   second phrase reads two ways and they differ by real dollars: promote off
   the top with the remainder split pro rata (GP share `x + (1−x)c` = 28% at
   20/10), or the GP's pro-rata slice first with the promote charged on the
   whole residual on top (GP share `c + x` = 30%). The gap is `x·c·R`.
   **`Self-Storage-Acquisition-Model-v1.3.xlsm`, `Underwriting!J250 =
   I250+(1-I250)*$J$244`, settles it as the first** — which is what this
   design specified and what the build computes, so no number moved.
   Both are implemented: `promote_basis` is the one convention on this list
   with two live values, `promote_then_split` (shipped) and
   `split_then_promote`. The distinguishing property is asserted in both
   directions — under the shipped basis every LP flow carries `(1−c)`, so LP
   IRR, MOIC and the levered max offer are invariant to the GP's co-invest;
   under the alternative they fall.
6. Any clawback/escrow language on interim promote? — answered 2026-07-29: no
   clawback.
7. "15% LP net IRR": net of both fee and promote? — answered 2026-07-29: yes.
8. GP catch-up tier above the pref? — **CONFIRMED 2026-08-12**: none. This was
   not originally on this list (it was filed as a one-tier scope decision), but
   an LP reading "20% promote above an 8% pref" cannot tell from that line
   whether a catch-up sits between them, so it now carries a stamp row of its
   own.
