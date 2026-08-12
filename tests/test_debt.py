"""Item E1 — the debt layer.

Every oracle here was recomputed from scratch before `model/debt.py` was
written, so these assert an independently derived answer rather than a
snapshot of the implementation. Sources:

* Oracles 4 and 5 of `docs/levered-waterfall-design.md`, reproduced to the
  cent (IRR to four decimals).
* Hand-built constraint fixtures for the sizing tests. Finding one that
  makes DSCR bind takes care: at a 6.5%/30yr constant of 7.5848%, a 1.25x
  DSCR cap is `noi x 10.547` while a 10% debt-yield cap is `noi x 10`, so
  debt yield always wins. DSCR only binds below a
  `min_dscr x constant` = 9.481% debt yield — hence the 8% floor in that
  fixture, which is the bottom of the 8-10% band the design doc records.

The two conventions the design doc leaves implicit are pinned by
`test_io_payment_is_sized_on_full_amortization_term` and by this module
building oracle 5's cash flows directly instead of borrowing the
pipeline's defaults (which carry item B's 1.5% disposition cost).

⚑ **ORACLE 5 USES THE FORWARD EXIT-NOI CONVENTION, WHICH IS NOT THE
PIPELINE'S.** It capitalizes YEAR 6 NOI, faithfully reproducing the
design doc, and that is about 3% higher than what
`analysis.valuation.project_cash_flows` produces — the projection
capitalizes the terminal hold year's OWN (year-5) NOI. Both are
deliberate; the split now has a config name — `EXIT_NOI_CONVENTION`
(decision 5, settled 2026-08-10, default "trailing") — and this
module's year-6 oracle is the FORWARD convention in a single-rate
fixture. Note it is NOT the same arithmetic as the implemented forward
branch, which steps revenue and expenses at their separate rates; same
convention, its own fixture, so do not expect the two to tie to the
cent. This module is allowed the design doc's convention because it
tests the debt layer in ISOLATION and never calls the canonical
projection. Item E3a, which does wire the two together, pins the
trailing default in `tests/test_levered.py`. If you are here because a
levered number looks 3% off, that is this, and it is not a bug in the
debt math.
"""

import pytest

import config as cfg
from model.debt import (CONSTRAINT_DEBT_YIELD, CONSTRAINT_DSCR, CONSTRAINT_LTV,
                        NOI_BASIS_STABILIZED, NOI_BASIS_YEAR_1, DebtTerms,
                        amortization_schedule, build_debt_schedule,
                        monthly_payment, resolve_debt_terms, size_loan)

CENT = 0.005          # "to the cent" — half a cent of slack for float noise

#: Oracle 4's loan: $6.5M, 6.50%, 30yr amortization, 24 months IO.
ORACLE_4 = DebtTerms(rate=0.065, amort_years=30, io_months=24, term_years=10)

#: Oracle 5's loan: same rate and amortization, no IO. Fees are stated
#: explicitly at zero because oracle 5 charges none — the DebtTerms
#: default mirrors config's 1 point, which would otherwise leak in.
ORACLE_5 = DebtTerms(rate=0.065, amort_years=30, io_months=0, term_years=10,
                     max_ltv=0.65, min_dscr=1.25, min_debt_yield=0.10,
                     orig_fee_pct=0.0, exit_fee_pct=0.0)


# ── Payment arithmetic ──────────────────────────────────────────────

def test_monthly_payment_matches_oracle_4():
    assert monthly_payment(6_500_000, 0.065, 30) == pytest.approx(41_084.42,
                                                                  abs=CENT)


def test_monthly_payment_at_zero_rate_is_straight_line():
    """A 0% loan must not divide by zero — it repays principal evenly."""
    assert monthly_payment(1_200_000, 0.0, 10) == pytest.approx(10_000.0,
                                                                abs=CENT)


def test_monthly_payment_of_nothing_is_nothing():
    assert monthly_payment(0, 0.065, 30) == 0.0
    assert monthly_payment(-5_000, 0.065, 30) == 0.0


# ── Oracle 4: amortization roll-forward with an IO period ───────────

def test_oracle_4_interest_only_years_pay_interest_and_no_principal():
    sched = amortization_schedule(6_500_000, ORACLE_4, hold_years=5)
    assert sched["io_monthly_payment"] * 12 == pytest.approx(422_500.00,
                                                             abs=CENT)
    # Years 1-2 are inside the 24-month IO period: interest only, balance flat.
    assert sched["annual_debt_service"][0] == pytest.approx(422_500.00, abs=CENT)
    assert sched["annual_debt_service"][1] == pytest.approx(422_500.00, abs=CENT)
    assert sched["annual_principal"][0] == pytest.approx(0.0, abs=CENT)
    assert sched["annual_principal"][1] == pytest.approx(0.0, abs=CENT)
    assert sched["ending_balances"][1] == pytest.approx(6_500_000.00, abs=CENT)


def test_oracle_4_amortizing_years_and_payoff():
    sched = amortization_schedule(6_500_000, ORACLE_4, hold_years=5)
    assert sched["monthly_payment"] == pytest.approx(41_084.42, abs=CENT)
    for year in (2, 3, 4):                       # years 3-5, zero-indexed
        assert sched["annual_debt_service"][year] == pytest.approx(493_013.06,
                                                                   abs=CENT)
    assert sched["ending_balances"] == pytest.approx(
        [6_500_000.00, 6_500_000.00, 6_427_347.84, 6_349_830.04,
         6_267_120.72], abs=CENT)
    # The design doc's headline number.
    assert sched["payoff_balance"] == pytest.approx(6_267_120.72, abs=CENT)


def test_oracle_4_annual_interest_series():
    sched = amortization_schedule(6_500_000, ORACLE_4, hold_years=5)
    assert sched["annual_interest"] == pytest.approx(
        [422_500.00, 422_500.00, 420_360.90, 415_495.25, 410_303.74], abs=CENT)


def test_debt_service_reconciles_to_interest_plus_principal():
    """The roll-forward's own identity, asserted every year."""
    sched = amortization_schedule(6_500_000, ORACLE_4, hold_years=5)
    for ds, interest, principal in zip(sched["annual_debt_service"],
                                       sched["annual_interest"],
                                       sched["annual_principal"]):
        assert ds == pytest.approx(interest + principal, abs=CENT)


def test_principal_paid_reconciles_to_the_balance_drop():
    sched = amortization_schedule(6_500_000, ORACLE_4, hold_years=5)
    assert sum(sched["annual_principal"]) == pytest.approx(
        6_500_000 - sched["payoff_balance"], abs=CENT)


def test_io_payment_is_sized_on_full_amortization_term():
    """The convention the design doc leaves implicit.

    After the IO period the payment is the one computed on the stated
    `amort_years` (360 months), NOT re-amortized over the 336 months that
    remain. Sizing on 360 is what reproduces the oracle's payoff; the
    re-amortized alternative is a different, more expensive product.
    """
    sched = amortization_schedule(6_500_000, ORACLE_4, hold_years=5)
    reamortized = monthly_payment(6_500_000, 0.065, 28)
    assert sched["monthly_payment"] == pytest.approx(
        monthly_payment(6_500_000, 0.065, 30), abs=CENT)
    assert sched["monthly_payment"] < reamortized


# ── Oracle 5: sizing, then the levered cash flows ───────────────────

def test_oracle_5_reports_all_three_constraints():
    sized = size_loan(10_000_000, 600_000, ORACLE_5)
    caps = {c["key"]: c["amount"] for c in sized["constraints"]}
    assert caps[CONSTRAINT_LTV] == pytest.approx(6_500_000.00, abs=CENT)
    assert caps[CONSTRAINT_DSCR] == pytest.approx(6_328_432.78, abs=CENT)
    assert caps[CONSTRAINT_DEBT_YIELD] == pytest.approx(6_000_000.00, abs=CENT)


def test_oracle_5_takes_the_minimum_and_names_the_binding_constraint():
    sized = size_loan(10_000_000, 600_000, ORACLE_5)
    assert sized["loan"] == pytest.approx(6_000_000.00, abs=CENT)
    assert sized["binding_constraint"] == CONSTRAINT_DEBT_YIELD
    assert sized["binding_basis"] == NOI_BASIS_YEAR_1


def test_oracle_5_debt_service_and_payoff():
    sched = amortization_schedule(6_000_000, ORACLE_5, hold_years=5)
    for ds in sched["annual_debt_service"]:
        assert ds == pytest.approx(455_088.98, abs=CENT)
    assert sched["payoff_balance"] == pytest.approx(5_616_658.65, abs=CENT)


def test_oracle_5_levered_irr():
    """End-to-end: sized loan -> debt service -> levered IRR of 9.9952%.

    Cash flows are built here rather than through `project_cash_flows`
    because the oracle's exit is GROSS — no disposition cost — while the
    pipeline defaults to item B's 1.5%. The "1% closing" in the design
    doc's parenthetical is the acquisition cost inside the equity figure.
    """
    # A hard import, not importorskip: numpy_financial is a pinned
    # requirement, and this is the ONLY end-to-end levered-IRR oracle in
    # the suite. Skipping it on a missing dependency would turn the one
    # test that proves the debt math composes into a silent pass.
    import numpy_financial as npf

    price, y1_noi, growth, exit_cap = 10_000_000, 600_000, 0.03, 0.0625
    sized = size_loan(price, y1_noi, ORACLE_5)
    sched = amortization_schedule(sized["loan"], ORACLE_5, hold_years=5)

    equity = price + price * 0.01 - sized["loan"]
    assert equity == pytest.approx(4_100_000.00, abs=CENT)

    noi = [y1_noi * (1 + growth) ** i for i in range(6)]
    exit_value = noi[5] / exit_cap
    assert exit_value == pytest.approx(11_129_031.11, abs=CENT)

    flows = [-equity]
    for year in range(5):
        cash = noi[year] - sched["annual_debt_service"][year]
        if year == 4:
            cash += exit_value - sched["payoff_balance"]
        flows.append(cash)

    assert flows == pytest.approx(
        [-4_100_000.00, 144_911.02, 162_911.02, 181_451.02, 200_547.22,
         5_732_588.78], abs=CENT)
    assert npf.irr(flows) * 100 == pytest.approx(9.9952, abs=0.0001)
    assert sum(flows[1:]) / equity == pytest.approx(1.5664, abs=0.0001)


# ── Sizing: each constraint binds in turn ───────────────────────────

def test_ltv_binds_when_the_asset_is_rich_in_income():
    sized = size_loan(10_000_000, 1_200_000, ORACLE_5)
    assert sized["loan"] == pytest.approx(6_500_000.00, abs=CENT)
    assert sized["binding_constraint"] == CONSTRAINT_LTV
    assert sized["binding_basis"] is None       # LTV has no NOI basis


def test_dscr_binds_below_a_nine_point_five_percent_debt_yield_floor():
    terms = DebtTerms(rate=0.065, amort_years=30, term_years=10,
                      max_ltv=0.65, min_dscr=1.25, min_debt_yield=0.08)
    sized = size_loan(12_000_000, 700_000, terms)
    assert sized["loan"] == pytest.approx(7_383_171.58, abs=CENT)
    assert sized["binding_constraint"] == CONSTRAINT_DSCR


def test_sizing_never_uses_ltv_alone():
    """The design doc's first rule. LTV alone would lend 6.5M here."""
    sized = size_loan(10_000_000, 600_000, ORACLE_5)
    assert sized["loan"] < 10_000_000 * ORACLE_5.max_ltv


# ── Sizing across both NOI bases ────────────────────────────────────

def test_stabilized_noi_defaults_to_year_one():
    """Omitting stabilized NOI collapses to the single-basis case.

    Comparing the two loans alone would be tautological — an equal
    stabilized NOI takes the identical code path — so this pins the
    observable consequence instead: no stabilized row is added, and the
    constraint list stays exactly the single-basis one.
    """
    one = size_loan(10_000_000, 600_000, ORACLE_5)
    both = size_loan(10_000_000, 600_000, ORACLE_5, stabilized_noi=600_000)
    assert one["loan"] == pytest.approx(both["loan"], abs=CENT)
    for sized in (one, both):
        assert all(c["basis"] != NOI_BASIS_STABILIZED
                   for c in sized["constraints"])
        assert len(sized["constraints"]) == 3      # LTV + DSCR + debt yield


def test_a_richer_stabilized_noi_does_not_raise_the_loan():
    """Model trailing AND stabilized; size on the weaker test.

    A value-add deal underwritten to $900k stabilized still only supports
    the loan its $600k in-place NOI covers — which is what a bank does,
    and is the "DSCR tested on the wrong NOI basis" error designed out.
    """
    sized = size_loan(10_000_000, 600_000, ORACLE_5, stabilized_noi=900_000)
    assert sized["loan"] == pytest.approx(6_000_000.00, abs=CENT)
    assert sized["binding_constraint"] == CONSTRAINT_DEBT_YIELD
    assert sized["binding_basis"] == NOI_BASIS_YEAR_1


def test_both_bases_are_reported_when_they_differ():
    sized = size_loan(10_000_000, 600_000, ORACLE_5, stabilized_noi=900_000)
    bases = {c["basis"] for c in sized["constraints"] if c["basis"]}
    assert bases == {NOI_BASIS_YEAR_1, NOI_BASIS_STABILIZED}
    stabilized = {c["key"]: c["amount"] for c in sized["constraints"]
                  if c["basis"] == NOI_BASIS_STABILIZED}
    assert stabilized[CONSTRAINT_DEBT_YIELD] == pytest.approx(9_000_000.00,
                                                              abs=CENT)


# ── DSCR is tested on the payment that actually comes due ───────────

def test_partial_io_still_sizes_on_the_amortizing_payment():
    """Sizing a partial-IO loan on its IO payment overlevers by ~17%.

    $600k NOI at 1.25x supports $6.33M against the amortizing constant but
    $7.38M against interest-only. The amortizing payment arrives before
    maturity, so it is the one the loan has to cover.
    """
    partial_io = DebtTerms(rate=0.065, amort_years=30, io_months=24,
                           term_years=10, max_ltv=0.95, min_dscr=1.25,
                           min_debt_yield=0.05)
    sized = size_loan(10_000_000, 600_000, partial_io)
    assert sized["loan"] == pytest.approx(6_328_432.78, abs=CENT)
    assert sized["binding_constraint"] == CONSTRAINT_DSCR


def test_full_term_io_sizes_on_the_interest_only_payment():
    """A loan that is IO for its whole life never makes an amortizing
    payment, so testing one would understate what it supports."""
    full_io = DebtTerms(rate=0.065, amort_years=30, io_months=60,
                        term_years=5, max_ltv=0.95, min_dscr=1.25,
                        min_debt_yield=0.05)
    sized = size_loan(10_000_000, 600_000, full_io)
    assert sized["loan"] == pytest.approx(7_384_615.38, abs=CENT)


# ── Degenerate inputs must not invent debt ──────────────────────────

def test_negative_noi_supports_no_debt():
    sized = size_loan(10_000_000, -50_000, ORACLE_5)
    assert sized["loan"] == 0.0
    assert all(c["amount"] >= 0 for c in sized["constraints"])


def test_zero_price_supports_no_debt():
    sized = size_loan(0, 600_000, ORACLE_5)
    assert sized["loan"] == 0.0
    assert sized["binding_constraint"] == CONSTRAINT_LTV


def test_a_zero_loan_produces_a_zero_schedule():
    sched = amortization_schedule(0, ORACLE_5, hold_years=5)
    assert sched["payoff_balance"] == 0.0
    assert sched["annual_debt_service"] == [0.0] * 5
    assert sched["ending_balances"] == [0.0] * 5
    # There was no loan, so nothing was amortized. Without this the
    # `loan > 0` half of the guard is unpinned and a zero loan could
    # report itself as fully repaid.
    assert sched["fully_amortized"] is False


def test_clearing_both_coverage_floors_lends_the_full_ltv():
    """Zero floors mean "no such covenant", so only LTV remains.

    Pinned deliberately: this is a real path to a full-LTV loan with no
    coverage test at all, and it should be a decision someone made rather
    than a surprise. The alternative reading — zero as a zero ceiling —
    would refuse debt on every deal that omits a covenant.
    """
    terms = DebtTerms(rate=0.065, amort_years=30, term_years=10,
                      max_ltv=0.65, min_dscr=0, min_debt_yield=0)
    sized = size_loan(10_000_000, 600_000, terms)
    assert sized["loan"] == pytest.approx(6_500_000.00, abs=CENT)
    assert sized["binding_constraint"] == CONSTRAINT_LTV
    assert [c["key"] for c in sized["constraints"]] == [CONSTRAINT_LTV]


def test_max_ltv_zero_means_no_debt_not_no_covenant():
    """The asymmetry that makes the zero-floor rule safe. `max_ltv` is a
    CAP, so zero lends nothing; if it were read like the coverage floors
    ("no such test") an all-cash mandate would silently become a fully
    levered one. Nothing else pinned this direction.
    """
    terms = DebtTerms(rate=0.065, amort_years=25, term_years=10, max_ltv=0,
                      min_dscr=1.25, min_debt_yield=0.10)
    sized = size_loan(10_000_000, 600_000, terms)
    assert sized["loan"] == 0.0
    assert sized["binding_constraint"] == CONSTRAINT_LTV


def test_actual_metrics_are_reported_at_the_sized_loan():
    sized = size_loan(10_000_000, 600_000, ORACLE_5)
    assert sized["ltv"] == pytest.approx(0.60)
    assert sized["debt_yield"] == pytest.approx(0.10)
    assert sized["sizing_dscr"] == pytest.approx(600_000 / 455_088.98,
                                                 abs=1e-6)


def test_sizing_dscr_and_actual_year_one_dscr_are_reported_separately():
    """They legitimately differ on a partial-IO loan, and shipping only
    the covenant next to a schedule showing the other reads as a
    contradiction: 1.25 beside a debt service that implies 1.56.
    """
    partial_io = DebtTerms(rate=0.065, amort_years=25, io_months=24,
                           term_years=10, max_ltv=0.95, min_dscr=1.25,
                           min_debt_yield=0.05)
    built = build_debt_schedule(10_000_000, 600_000, partial_io, hold_years=5)

    # Sized against the amortizing constant — the covenant binds exactly.
    assert built["sizing_dscr"] == pytest.approx(1.25, abs=1e-6)
    # Year 1 pays interest only, so actual coverage is more generous.
    assert built["dscr_year_1"] == pytest.approx(
        600_000 / built["annual_debt_service"][0], abs=1e-9)
    assert built["dscr_year_1"] > built["sizing_dscr"]


def test_year_one_dscr_equals_the_covenant_when_there_is_no_io():
    """With no IO the two ratios must agree — otherwise one of them is
    measuring something nobody asked for."""
    built = build_debt_schedule(10_000_000, 600_000, ORACLE_5, hold_years=5)
    assert built["dscr_year_1"] == pytest.approx(built["sizing_dscr"],
                                                 abs=1e-9)


# ── Schedule edge cases ─────────────────────────────────────────────

def test_zero_rate_schedule_repays_principal_evenly():
    terms = DebtTerms(rate=0.0, amort_years=10, term_years=10)
    sched = amortization_schedule(1_200_000, terms, hold_years=2)
    assert sched["annual_debt_service"] == pytest.approx([120_000.0] * 2,
                                                         abs=CENT)
    assert sched["annual_interest"] == pytest.approx([0.0, 0.0], abs=CENT)
    assert sched["payoff_balance"] == pytest.approx(960_000.0, abs=CENT)


def test_a_loan_that_fully_amortizes_stops_at_zero():
    """Balance must not go negative, and there is nothing to pay after."""
    terms = DebtTerms(rate=0.06, amort_years=3, term_years=5)
    sched = amortization_schedule(500_000, terms, hold_years=5)
    assert sched["fully_amortized"] is True
    assert sched["payoff_balance"] == pytest.approx(0.0, abs=CENT)
    assert sched["ending_balances"][2] == pytest.approx(0.0, abs=CENT)
    assert sched["annual_debt_service"][3] == pytest.approx(0.0, abs=CENT)
    assert sched["annual_debt_service"][4] == pytest.approx(0.0, abs=CENT)
    assert all(b >= 0 for b in sched["ending_balances"])


def test_hold_beyond_maturity_is_reported_not_silently_amortized():
    """A balloon due before the sale is a refinancing E1 does not model."""
    terms = DebtTerms(rate=0.065, amort_years=30, term_years=3)
    sched = amortization_schedule(6_000_000, terms, hold_years=5)
    assert sched["matures_before_exit"] is True


def test_a_hold_inside_the_term_does_not_flag_maturity():
    sched = amortization_schedule(6_000_000, ORACLE_5, hold_years=5)
    assert sched["matures_before_exit"] is False
    assert sched["fully_amortized"] is False


def test_selling_in_the_maturity_year_is_not_a_balloon():
    """The boundary the other two fixtures straddle without touching.
    hold == term means the loan is repaid at sale, exactly on time — off
    by one here would warn on every deal held to maturity.
    """
    terms = DebtTerms(rate=0.065, amort_years=25, term_years=5)
    assert amortization_schedule(6_000_000, terms,
                                 hold_years=5)["matures_before_exit"] is False
    assert amortization_schedule(6_000_000, terms,
                                 hold_years=6)["matures_before_exit"] is True


def test_schedule_length_follows_the_hold_period():
    for hold in (1, 3, 7, 10):
        sched = amortization_schedule(6_000_000, ORACLE_5, hold_years=hold)
        assert len(sched["annual_debt_service"]) == hold
        assert len(sched["ending_balances"]) == hold
        assert len(sched["annual_interest"]) == hold
        assert len(sched["annual_principal"]) == hold


# ── Fees ────────────────────────────────────────────────────────────

def test_origination_is_charged_on_the_loan_and_exit_on_the_payoff():
    terms = DebtTerms(rate=0.065, amort_years=30, term_years=10,
                      max_ltv=0.65, min_dscr=1.25, min_debt_yield=0.10,
                      orig_fee_pct=0.01, exit_fee_pct=0.005)
    built = build_debt_schedule(10_000_000, 600_000, terms, hold_years=5)
    assert built["loan"] == pytest.approx(6_000_000.00, abs=CENT)
    assert built["origination_fee"] == pytest.approx(60_000.00, abs=CENT)
    assert built["exit_fee"] == pytest.approx(28_083.29, abs=CENT)


def test_financing_costs_are_the_close_dated_fees_only():
    """`financing_costs` feeds Sources & Uses, which is a close-date
    statement — the exit fee is paid at sale and is not a use of funds."""
    terms = DebtTerms(rate=0.065, amort_years=30, term_years=10,
                      max_ltv=0.65, min_dscr=1.25, min_debt_yield=0.10,
                      orig_fee_pct=0.01, exit_fee_pct=0.005)
    built = build_debt_schedule(10_000_000, 600_000, terms, hold_years=5)
    assert built["financing_costs"] == pytest.approx(
        built["origination_fee"], abs=CENT)
    assert built["exit_fee"] > 0


def test_explicitly_zeroed_fees_cost_nothing():
    """ORACLE_5 states both fees at zero, matching the design doc's
    oracle. Not a statement about the default — see below."""
    built = build_debt_schedule(10_000_000, 600_000, ORACLE_5, hold_years=5)
    assert built["origination_fee"] == 0.0
    assert built["exit_fee"] == 0.0
    assert built["financing_costs"] == 0.0


def test_the_resolved_default_does_charge_an_origination_fee():
    """The config default is 1 point at close, so a deal that never
    touches the fee fields still pays it. Asserted because the opposite —
    a silent 0% origination — was the drift this PR's review caught."""
    terms = resolve_debt_terms()
    built = build_debt_schedule(10_000_000, 600_000, terms, hold_years=5)
    assert terms.orig_fee_pct == pytest.approx(cfg.DEBT_TERMS["orig_fee_pct"])
    assert built["origination_fee"] > 0
    assert built["financing_costs"] == pytest.approx(
        built["loan"] * cfg.DEBT_TERMS["orig_fee_pct"], abs=CENT)


def test_build_debt_schedule_carries_sizing_and_schedule_together():
    built = build_debt_schedule(10_000_000, 600_000, ORACLE_5, hold_years=5)
    assert built["binding_constraint"] == CONSTRAINT_DEBT_YIELD
    assert built["payoff_balance"] == pytest.approx(5_616_658.65, abs=CENT)
    assert built["annual_debt_service"][0] == pytest.approx(455_088.98,
                                                            abs=CENT)



# ── Terms resolution ────────────────────────────────────────────────

def test_rate_may_be_expressed_as_index_plus_spread():
    terms = DebtTerms(index_rate=0.0425, spread=0.0225, amort_years=30,
                      term_years=3)
    assert terms.all_in_rate() == pytest.approx(0.065)


def test_an_explicit_rate_wins_over_index_plus_spread():
    terms = DebtTerms(rate=0.07, index_rate=0.0425, spread=0.0225)
    assert terms.all_in_rate() == pytest.approx(0.07)


def test_an_unresolvable_rate_raises_rather_than_defaulting_to_zero():
    """A silent 0% loan produces a spectacular IRR and no error."""
    with pytest.raises(ValueError, match="rate"):
        DebtTerms(amort_years=30, term_years=10).all_in_rate()


def test_half_a_floating_pair_raises_rather_than_pricing_the_other_half_at_zero():
    """A spread with no index is a 2.25% loan nobody offered."""
    with pytest.raises(ValueError, match="index_rate"):
        DebtTerms(spread=0.0225, amort_years=30, term_years=10).all_in_rate()
    with pytest.raises(ValueError, match="spread"):
        DebtTerms(index_rate=0.0425, amort_years=30,
                  term_years=10).all_in_rate()


def test_resolve_debt_terms_can_actually_reach_a_floating_rate():
    """The regression that motivated the fix.

    config seeds a fixed `rate`, and the ordinary merge rule only
    overwrites keys the caller named — so an index/spread override used to
    leave the seeded fixed rate standing, `all_in_rate` short-circuited on
    it, and the floating terms were discarded in silence. This is the
    documented way to underwrite a bridge loan, so it has to work.
    """
    terms = resolve_debt_terms({"index_rate": 0.05, "spread": 0.02})
    assert terms.rate is None
    assert terms.all_in_rate() == pytest.approx(0.07)
    assert terms.all_in_rate() != pytest.approx(cfg.DEBT_TERMS["rate"])


def test_a_floating_override_of_one_half_still_clears_the_seeded_fixed_rate():
    """Half a pair must raise, not silently fall back to config's fixed
    rate — falling back would be the same silent substitution."""
    terms = resolve_debt_terms({"spread": 0.02})
    assert terms.rate is None
    with pytest.raises(ValueError, match="index_rate"):
        terms.all_in_rate()


def test_naming_a_fixed_rate_and_a_floating_pair_keeps_the_fixed_rate():
    terms = resolve_debt_terms({"rate": 0.08, "index_rate": 0.05,
                                "spread": 0.02})
    assert terms.all_in_rate() == pytest.approx(0.08)


def test_dataclass_defaults_do_not_drift_from_config():
    """`DebtTerms`'s field defaults mirror config.DEBT_TERMS, and a
    duplicated value drifts — `orig_fee_pct` already had (0.0 against
    config's 0.01) before this test existed. CLAUDE.md allows a static
    fallback only when the no-drift invariant is CI-guarded; this is that
    guard.

    `rate` is the deliberate exception: it stays None on the dataclass so
    a bare `DebtTerms` raises rather than quietly pricing a loan.
    """
    import dataclasses

    defaults = {f.name: f.default for f in dataclasses.fields(DebtTerms)}
    drifted = {
        key: (defaults[key], value)
        for key, value in cfg.DEBT_TERMS.items()
        if key != "rate" and key in defaults and defaults[key] != value
    }
    assert drifted == {}, (
        "DebtTerms defaults drifted from config.DEBT_TERMS "
        f"(field default, config value): {drifted}")


def test_the_config_block_describes_one_real_loan_product():
    """Bank paper amortizes over 20-25 years and prepays step-down; CMBS
    amortizes over 30 and pays defeasance/yield-maintenance. An earlier
    draft took CMBS amortization with a bank exit-fee assumption — a
    blend no lender offers, and a cheaper payment than either product.
    """
    assert cfg.DEBT_TERMS["amort_years"] <= 25, (
        "30-year amortization is the design doc's CMBS term; pairing it "
        "with the step-down prepay assumed by exit_fee_pct=0 prices a "
        "loan that does not exist")


def test_resolve_debt_terms_uses_config_defaults():
    terms = resolve_debt_terms()
    assert terms.all_in_rate() == pytest.approx(cfg.DEBT_TERMS["rate"])
    assert terms.max_ltv == pytest.approx(cfg.DEBT_TERMS["max_ltv"])
    assert terms.min_dscr == pytest.approx(cfg.DEBT_TERMS["min_dscr"])


def test_omitting_a_key_means_default_never_zero():
    """Same contract as resolve_transaction_costs / resolve_capital_structure."""
    terms = resolve_debt_terms({"max_ltv": 0.75})
    assert terms.max_ltv == pytest.approx(0.75)
    assert terms.min_dscr == pytest.approx(cfg.DEBT_TERMS["min_dscr"])
    assert terms.all_in_rate() == pytest.approx(cfg.DEBT_TERMS["rate"])


def test_explicit_zero_is_honoured():
    terms = resolve_debt_terms({"min_debt_yield": 0.0})
    assert terms.min_debt_yield == 0.0


def test_an_unknown_override_key_is_ignored_not_fatal():
    """The documented forward-compatibility promise: an override row
    written by a future version must not take down a run on an older one.
    It was documented in a comment and never tested."""
    terms = resolve_debt_terms({"max_ltv": 0.70,
                                "mezzanine_rate": 0.12,   # not a field
                                "prepay_lockout_months": 24})
    assert terms.max_ltv == pytest.approx(0.70)
    assert terms.all_in_rate() == pytest.approx(cfg.DEBT_TERMS["rate"])
    assert not hasattr(terms, "mezzanine_rate")


def test_fractional_year_overrides_are_rejected_not_truncated():
    """int(25.9) would silently re-price the loan on a 25-year schedule."""
    for key in ("amort_years", "term_years", "io_months"):
        with pytest.raises(ValueError, match="whole number"):
            resolve_debt_terms({key: 25.9})
    # A whole number expressed as a float stays fine.
    assert resolve_debt_terms({"amort_years": 20.0}).amort_years == 20


# ── Terms that are not loans are rejected at the boundary ───────────

def test_a_zero_amortization_term_is_rejected():
    """Otherwise the sizing constant becomes 1200%/yr and the loan comes
    back two orders of magnitude too small, with no error."""
    with pytest.raises(ValueError, match="amort_years"):
        DebtTerms(rate=0.065, amort_years=0, term_years=10)


def test_a_zero_loan_term_is_rejected():
    """Otherwise both the full-IO test and the maturity warning switch
    themselves off silently."""
    with pytest.raises(ValueError, match="term_years"):
        DebtTerms(rate=0.065, amort_years=30, term_years=0)


def test_negative_terms_are_rejected():
    with pytest.raises(ValueError, match="io_months"):
        DebtTerms(rate=0.065, amort_years=30, term_years=10, io_months=-12)
    with pytest.raises(ValueError, match="max_ltv"):
        DebtTerms(rate=0.065, amort_years=30, term_years=10, max_ltv=-0.65)
    with pytest.raises(ValueError, match="min_dscr"):
        DebtTerms(rate=0.065, amort_years=30, term_years=10, min_dscr=-1.25)


def test_monthly_payment_rejects_a_zero_amortization_term_directly():
    with pytest.raises(ValueError, match="amort_years"):
        monthly_payment(6_000_000, 0.065, 0)


def test_a_zero_length_hold_is_rejected():
    """An empty schedule reports payoff == loan, i.e. debt that was never
    serviced and repaid in full."""
    with pytest.raises(ValueError, match="hold_years"):
        amortization_schedule(6_000_000, ORACLE_5, hold_years=0)


def test_a_fractional_hold_is_rejected_not_truncated():
    """int(5.9) silently drops eleven months of debt service, and would
    suppress a maturity warning falling in the dropped months."""
    with pytest.raises(ValueError, match="whole number"):
        amortization_schedule(6_000_000, ORACLE_5, hold_years=5.9)
    # A whole number expressed as a float is fine.
    sched = amortization_schedule(6_000_000, ORACLE_5, hold_years=5.0)
    assert len(sched["annual_debt_service"]) == 5


def test_nan_terms_are_rejected():
    """Every comparison against NaN is False, so it walks past a `< 0`
    guard and then produces a confident answer: a NaN min_dscr used to
    size a $0 loan and report DSCR as the binding covenant."""
    nan = float("nan")
    with pytest.raises(ValueError, match="finite"):
        DebtTerms(rate=0.065, amort_years=25, term_years=10, min_dscr=nan)
    with pytest.raises(ValueError, match="finite"):
        DebtTerms(rate=nan, amort_years=25, term_years=10)
    with pytest.raises(ValueError, match="finite"):
        DebtTerms(rate=0.065, amort_years=25, term_years=10,
                  max_ltv=float("inf"))


def test_nan_sizing_inputs_are_rejected():
    nan = float("nan")
    terms = DebtTerms(rate=0.065, amort_years=25, term_years=10)
    with pytest.raises(ValueError, match="finite"):
        size_loan(nan, 600_000, terms)
    with pytest.raises(ValueError, match="finite"):
        size_loan(10_000_000, nan, terms)
    with pytest.raises(ValueError, match="finite"):
        size_loan(10_000_000, 600_000, terms, stabilized_noi=nan)


def test_a_negative_rate_is_rejected_like_every_other_negative_term():
    """The rate trio was the one group not sign-checked, so a negative
    rate produced a plausible payment on a loan that pays you to hold it."""
    with pytest.raises(ValueError, match="rate"):
        DebtTerms(rate=-0.05, amort_years=25, term_years=10)
    with pytest.raises(ValueError, match="spread"):
        DebtTerms(index_rate=0.04, spread=-0.01, amort_years=25,
                  term_years=10)


def test_config_defaults_sit_inside_the_researched_market_bands():
    """Guards against a default drifting outside the BANK terms the design
    doc recorded (65-75% LTV, 1.25x DSCR, 8-10% debt yield, 20-25yr amort,
    5.5-6.5% fixed).

    The amortization bound is 25, not 30. An earlier draft of this test
    said 20-30 — wide enough to accept the CMBS amortization the block had
    mistakenly taken, which is the exact drift it existed to catch. A band
    that admits both products guards neither.
    """
    assert 0.60 <= cfg.DEBT_TERMS["max_ltv"] <= 0.80
    assert 1.15 <= cfg.DEBT_TERMS["min_dscr"] <= 1.40
    assert 0.07 <= cfg.DEBT_TERMS["min_debt_yield"] <= 0.11
    assert 20 <= cfg.DEBT_TERMS["amort_years"] <= 25
    assert 0.04 <= cfg.DEBT_TERMS["rate"] <= 0.12




# ── The two override gaps E1's review deferred to item E3a ──────────
# E1 left both unfixed on purpose: nothing outside `tests/` could reach
# `resolve_debt_terms` with caller-supplied data, so neither was live.
# Item E3a adds the first real override call sites —
# `engine.run_analysis(debt_terms=...)` and the stored per-deal override
# read in `webapp.services` — so they are live now and fixed here.

def test_a_percent_where_a_decimal_belongs_raises_instead_of_pricing_it():
    """`rate=6.5` meaning 6.5% used to construct cleanly as 650%/yr.

    Measured before the fix: a $6.5M loan priced at $3,520,833.33/mo
    against a correct $43,888.47/mo — about 80x, silently, in the payment
    that feeds every levered return in the model. `min_dscr` is
    deliberately NOT guarded: it is a coverage RATIO and 1.25x is the
    market term this repo underwrites to.
    """
    with pytest.raises(ValueError, match="DECIMAL fractions"):
        resolve_debt_terms({"rate": 6.5})
    with pytest.raises(ValueError, match="DECIMAL fractions"):
        DebtTerms(rate=6.5)
    for field, bad in (("max_ltv", 65), ("min_debt_yield", 10),
                       ("orig_fee_pct", 1.5), ("exit_fee_pct", 50),
                       ("index_rate", 4.25), ("spread", 2.25)):
        with pytest.raises(ValueError, match="DECIMAL fractions"):
            resolve_debt_terms({field: bad})

    # A coverage ratio above 1.0 is the normal case and must still build.
    assert resolve_debt_terms({"min_dscr": 1.25}).min_dscr == 1.25
    # And the real terms are untouched.
    assert resolve_debt_terms({"rate": 0.065}).all_in_rate() == 0.065


def test_an_infinite_integer_override_raises_this_modules_error():
    """`int(float('inf'))` raises OverflowError, not the clean ValueError
    every other bad term here produces — so an infinite override escaped
    the error contract, and `DebtTerms.__post_init__`'s own finiteness
    check never ran because the coercion blew up first."""
    for bad in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValueError, match="finite number"):
            resolve_debt_terms({"amort_years": bad})
        with pytest.raises(ValueError, match="finite number"):
            resolve_debt_terms({"io_months": bad})
        with pytest.raises(ValueError, match="finite number"):
            resolve_debt_terms({"term_years": bad})


def test_resolving_an_already_resolved_dict_changes_nothing():
    """`webapp.services` stamps the RESOLVED debt terms and hands the same
    dict to the engine, which resolves once more. That only keeps "the
    stamp equals what ran" true if resolving is idempotent — and the
    floating-rate mode switch is the part that could break it, since
    supplying `index_rate`/`spread` without `rate` CLEARS the seeded fixed
    rate. Feeding the resolved dict back names all three, so the branch
    has to reach the same answer a second time."""
    import dataclasses

    for override in (None, {"rate": 0.07},
                     {"index_rate": 0.0425, "spread": 0.0225},
                     {"io_months": 24, "max_ltv": 0.70}):
        once = resolve_debt_terms(override)
        twice = resolve_debt_terms(dataclasses.asdict(once))
        assert once == twice, override
