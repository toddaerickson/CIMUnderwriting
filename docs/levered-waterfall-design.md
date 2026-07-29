# Levered returns + preferred-return waterfall — research & design

Research date: 2026-07-28 (web sources; fixture arithmetic verified in Python).
Status: DESIGN INPUT — not yet built. Blocked on the LPA confirmations at the
bottom before the waterfall can be considered accurate.

Fund terms assumed (from operator): 8% pref, 20/80 promote above pref, no
catch-up, ~10% GP co-invest, 1% AM fee, 15% LP **net** IRR target.

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
  (rare). $100M × 4yr × 8%: $32M simple vs $36.05M compounded.
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
  LP-attributable share; clawback — avoided structurally by paying no promote
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
pari passu through tier 1; promote on LP-attributable residual only. 1% AM fee
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

1. 8% pref: simple or compounded? At what frequency? (moves GP promote ~19%)
2. Accrual base: contributed/unreturned capital (assumed) or committed?
3. ROC-vs-pref ordering as written (matters only if simple).
4. 1% AM fee: above the waterfall (deal expense) or netted from LP
   distributions?
5. GP co-invest: earns pref pari passu, promote on residual net of GP
   pro-rata share (assumed standard)?
6. Any clawback/escrow language on interim promote?
7. "15% LP net IRR": net of both fee and promote (assumed yes)?
