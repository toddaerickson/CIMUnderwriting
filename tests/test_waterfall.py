"""Item E2 — the single-tier LP waterfall.

Every oracle here was re-derived from scratch before `model/waterfall.py`
was written — accrual path, promote and LP IRR — so these assert an
independently computed answer rather than a snapshot of the
implementation.

Source: oracles 1-3 of `docs/levered-waterfall-design.md`, reproduced to
the cent (IRRs to four decimal places). All three are the ZERO-co-invest
case: the $1,000,000 is the LP's, so the LP-attributable residual is the
whole residual. The shipped 10% co-invest default is derived separately
in `test_promote_is_charged_on_the_lp_attributable_residual_only`.

Three conventions the oracles depend on but never state are pinned by
their own tests: the accrual is computed on the unreturned capital at the
START of the period, period 0 does not accrue, and capital called at
period k first earns at k+1.
"""

import pytest

import config as cfg
from model.waterfall import (ACCRUAL_BASE_COMMITTED, AM_FEE_NETTED_FROM_LP,
                             COMPOUNDING_ANNUAL, COMPOUNDING_SIMPLE,
                             ORDERING_PREF_FIRST, ORDERING_ROC_FIRST,
                             WaterfallTerms, assumption_stamp,
                             resolve_waterfall_terms, run_waterfall)

CENT = 0.005          # "to the cent" — half a cent of slack for float noise

#: The design doc's fixture: $1,000,000 in, then five distributions.
CONTRIBUTION = 1_000_000.0
DISTRIBUTIONS = [0.0, 50_000.0, 60_000.0, 70_000.0, 80_000.0, 1_500_000.0]

#: Oracles 1-3 carry no GP co-invest — see the module docstring.
NO_COINVEST = dict(pref_rate=0.08, promote_split=0.20, gp_coinvest_pct=0.0)

ORACLE_1 = WaterfallTerms(pref_compounding=COMPOUNDING_ANNUAL, **NO_COINVEST)
ORACLE_2 = WaterfallTerms(pref_compounding=COMPOUNDING_SIMPLE,
                          ordering=ORDERING_ROC_FIRST, **NO_COINVEST)
ORACLE_3 = WaterfallTerms(pref_compounding=COMPOUNDING_SIMPLE,
                          ordering=ORDERING_PREF_FIRST, **NO_COINVEST)


def _run(terms, distributions=None, contributions=CONTRIBUTION):
    return run_waterfall(contributions,
                         DISTRIBUTIONS if distributions is None
                         else distributions, terms)


# ── Oracle 1: annually compounded pref ──────────────────────────────

def test_oracle_1_accrual_balance_path():
    """The design doc's headline series: the accrual account BEFORE each
    year's distribution is applied."""
    result = _run(ORACLE_1)
    before_distribution = [
        row["ending_balance"] + row["tier1_paid"]
        for row in result["periods"][1:]
    ]
    assert before_distribution == pytest.approx(
        [1_080_000.00, 1_112_400.00, 1_136_592.00, 1_151_919.36,
         1_157_672.9088], abs=CENT)


def test_oracle_1_year_5_tier_one_residual_and_promote():
    result = _run(ORACLE_1)
    final = result["periods"][-1]
    assert final["tier1_paid"] == pytest.approx(1_157_672.91, abs=CENT)
    assert final["residual"] == pytest.approx(342_327.09, abs=CENT)
    # The design doc's headline number.
    assert final["gp_promote"] == pytest.approx(68_465.42, abs=CENT)
    assert result["gp"]["promote"] == pytest.approx(68_465.42, abs=CENT)


def test_oracle_1_lp_total_irr_and_moic():
    result = _run(ORACLE_1)
    assert result["lp"]["distributions"] == pytest.approx(1_691_534.58,
                                                          abs=CENT)
    assert result["lp"]["irr"] * 100 == pytest.approx(12.1340, abs=0.0001)
    assert result["lp"]["moic"] == pytest.approx(1.6915, abs=0.0001)


def test_oracle_1_clears_tier_one():
    result = _run(ORACLE_1)
    assert result["tier1_current"] is True
    assert result["unreturned_capital"] == pytest.approx(0.0, abs=CENT)
    assert result["unpaid_pref"] == pytest.approx(0.0, abs=CENT)


# ── Oracle 2: simple pref, return of capital first ──────────────────

def test_oracle_2_accruals_fall_as_capital_is_returned():
    """8% of the unreturned capital at the START of each period. Year 2's
    76,000 is 8% of 950,000 — capital AFTER year 1's return of 50,000.
    Accruing on the ending balance instead would give 71,200 here and
    miss every number after it."""
    result = _run(ORACLE_2)
    accruals = [row["pref_accrued"] for row in result["periods"][1:]]
    assert accruals == pytest.approx(
        [80_000.00, 76_000.00, 71_200.00, 65_600.00, 59_200.00], abs=CENT)
    assert sum(accruals) == pytest.approx(352_000.00, abs=CENT)


def test_oracle_2_promote_and_lp_irr():
    result = _run(ORACLE_2)
    assert result["gp"]["promote"] == pytest.approx(81_600.00, abs=CENT)
    assert result["lp"]["distributions"] == pytest.approx(1_678_400.00,
                                                          abs=CENT)
    assert result["lp"]["irr"] * 100 == pytest.approx(11.9500, abs=0.0001)


# ── Oracle 3: simple pref, preferred return first ───────────────────

def test_oracle_3_capital_is_never_returned_so_the_accrual_is_flat():
    """Pref-first leaves unreturned capital at $1,000,000 until year 5,
    so the accrual is 80,000 every year rather than falling."""
    result = _run(ORACLE_3)
    accruals = [row["pref_accrued"] for row in result["periods"][1:]]
    assert accruals == pytest.approx([80_000.00] * 5, abs=CENT)
    for row in result["periods"][1:-1]:
        assert row["ending_unreturned_capital"] == pytest.approx(1_000_000.00,
                                                                 abs=CENT)


def test_oracle_3_unpaid_pref_entering_the_final_year():
    """Year 5 accrues 80,000 onto the 60,000 still unpaid from year 4."""
    result = _run(ORACLE_3)
    final = result["periods"][-1]
    entering = final["pref_paid"] + final["ending_unpaid_pref"]
    assert entering == pytest.approx(140_000.00, abs=CENT)


def test_oracle_3_promote_and_lp_irr():
    result = _run(ORACLE_3)
    assert result["gp"]["promote"] == pytest.approx(72_000.00, abs=CENT)
    assert result["lp"]["distributions"] == pytest.approx(1_688_000.00,
                                                          abs=CENT)
    assert result["lp"]["irr"] * 100 == pytest.approx(12.0846, abs=0.0001)


# ── Ordering: inert when compounded, material when simple ───────────

def test_ordering_does_not_move_a_compounded_waterfall():
    """The design doc's claim, checked in CI rather than trusted: once
    the pref compounds there is ONE balance, not two claims, so which
    one you notionally pay first cannot change a dollar."""
    roc = _run(WaterfallTerms(pref_compounding=COMPOUNDING_ANNUAL,
                              ordering=ORDERING_ROC_FIRST, **NO_COINVEST))
    pref = _run(WaterfallTerms(pref_compounding=COMPOUNDING_ANNUAL,
                               ordering=ORDERING_PREF_FIRST, **NO_COINVEST))
    assert roc["gp"]["promote"] == pytest.approx(pref["gp"]["promote"],
                                                 abs=CENT)
    assert roc["lp"]["distributions"] == pytest.approx(
        pref["lp"]["distributions"], abs=CENT)
    assert roc["lp"]["cash_flows"] == pytest.approx(pref["lp"]["cash_flows"],
                                                    abs=CENT)


def test_ordering_moves_a_simple_waterfall_by_nine_thousand_six_hundred():
    """$81,600 against $72,000 on the same flows — the reason `ordering`
    is a parameter at all, and question 3 on the LPA list."""
    assert (_run(ORACLE_2)["gp"]["promote"]
            - _run(ORACLE_3)["gp"]["promote"]) == pytest.approx(9_600.00,
                                                                abs=CENT)


# ── GP co-invest: pari passu through tier 1, promote on the LP share ─

def test_promote_is_charged_on_the_lp_attributable_residual_only():
    """At the shipped 10% co-invest the residual is unchanged at
    342,327.09, but only 90% of it is the LP's, so the promote is
    0.20 x 0.90 x 342,327.0912 = 61,618.88 — not oracle 1's 68,465.42.
    Charging the promote on 100% of the residual would pay the GP a
    promote on its own co-invested capital.
    """
    result = _run(WaterfallTerms(pref_compounding=COMPOUNDING_ANNUAL,
                                 pref_rate=0.08, promote_split=0.20,
                                 gp_coinvest_pct=0.10))
    final = result["periods"][-1]
    assert final["residual"] == pytest.approx(342_327.09, abs=CENT)
    assert result["gp"]["promote"] == pytest.approx(61_618.88, abs=CENT)
    assert result["gp"]["distributions"] == pytest.approx(237_618.88, abs=CENT)
    assert result["lp"]["distributions"] == pytest.approx(1_522_381.12,
                                                          abs=CENT)


def test_gp_rides_tier_one_pari_passu():
    """Tier-1 dollars split by co-invest share exactly, in every period —
    that is what "pari passu" has to mean numerically.

    Both sides are asserted against absolute dollars. Backing the GP's
    tier-1 share out of `gp_distribution` by subtracting the promote and
    the GP's residual share is a restatement of the code being tested: it
    collapses to `tier1 * 0.10 == tier1 * 0.10` and cannot fail. It also
    says nothing about the LP side, where the mirror-image error lives.
    """
    result = _run(WaterfallTerms(pref_compounding=COMPOUNDING_ANNUAL,
                                 pref_rate=0.08, promote_split=0.20,
                                 gp_coinvest_pct=0.10))
    # Years 1-4 distribute tier 1 only, so each side is a clean 90/10.
    assert [row["lp_distribution"] for row in result["periods"][1:-1]] == \
        pytest.approx([45_000.0, 54_000.0, 63_000.0, 72_000.0], abs=CENT)
    assert [row["gp_distribution"] for row in result["periods"][1:-1]] == \
        pytest.approx([5_000.0, 6_000.0, 7_000.0, 8_000.0], abs=CENT)
    # The sale year mixes tier 1, the residual and the promote:
    # tier 1 1,157,672.9088 x 0.90 = 1,041,905.62; LP residual
    # 308,094.38 less the 61,618.88 promote.
    final = result["periods"][-1]
    assert final["lp_distribution"] == pytest.approx(1_288_381.12, abs=CENT)
    assert final["gp_distribution"] == pytest.approx(211_618.88, abs=CENT)


def test_lp_returns_are_invariant_to_gp_coinvest():
    """The property that proves the promote basis is right. With the
    promote charged on the LP-attributable residual only, the LP's flows
    scale exactly with its share of the equity — so its IRR and MOIC do
    not move when the GP co-invests more. Under a promote charged on 100%
    of the residual they would, and the LP would be quietly paying the
    GP a promote on the GP's own money.
    """
    baseline = _run(WaterfallTerms(pref_compounding=COMPOUNDING_ANNUAL,
                                   **NO_COINVEST))
    for coinvest in (0.05, 0.10, 0.25, 0.50):
        result = _run(WaterfallTerms(pref_compounding=COMPOUNDING_ANNUAL,
                                     pref_rate=0.08, promote_split=0.20,
                                     gp_coinvest_pct=coinvest))
        assert result["lp"]["irr"] == pytest.approx(baseline["lp"]["irr"],
                                                    abs=1e-9)
        assert result["lp"]["moic"] == pytest.approx(baseline["lp"]["moic"],
                                                     abs=1e-9)


# ── Accrual timing ──────────────────────────────────────────────────

def test_period_zero_does_not_accrue():
    """Capital contributed at close has been in the deal for zero days.

    Asserting `pref_accrued == 0.0` alone proves nothing — at period 0
    the balance is still zero when the accrual step runs, so it would
    read 0.0 with or without the guard. What actually enforces the
    convention is the STEP ORDER (accrue, then call capital), so this
    also pins the observable consequence: the full $1,000,000 sits in
    period 0's ending balance with not one cent of pref on top of it.
    """
    for terms in (ORACLE_1, ORACLE_2, ORACLE_3):
        opening = _run(terms)["periods"][0]
        assert opening["pref_accrued"] == 0.0
        assert opening["ending_balance"] == pytest.approx(1_000_000.00,
                                                          abs=CENT)
        assert opening["ending_unpaid_pref"] == pytest.approx(0.0, abs=CENT)


def test_a_mid_stream_capital_call_starts_accruing_the_following_period():
    """$1,000,000 at close, $200,000 at period 2, nothing distributed:
    1,080,000 -> 1,166,400 (+200,000 = 1,366,400) -> 1,475,712 ->
    1,593,768.96. The call joins the base AFTER period 2's accrual, so
    period 2 still accrues 86,400 on 1,000,000 alone.
    """
    result = run_waterfall([1_000_000.0, 0.0, 200_000.0, 0.0, 0.0],
                           [0.0] * 5,
                           WaterfallTerms(pref_compounding=COMPOUNDING_ANNUAL,
                                          **NO_COINVEST))
    assert result["periods"][2]["pref_accrued"] == pytest.approx(86_400.00,
                                                                 abs=CENT)
    assert result["periods"][-1]["ending_balance"] == pytest.approx(
        1_593_768.96, abs=CENT)


def test_a_mid_stream_call_also_waits_a_period_under_a_simple_pref():
    """Same flows, simple pref: 80,000 / 80,000 (still on 1,000,000) /
    96,000 / 96,000 = 352,000 of accrued unpaid preferred return."""
    result = run_waterfall([1_000_000.0, 0.0, 200_000.0, 0.0, 0.0],
                           [0.0] * 5, ORACLE_2)
    accruals = [row["pref_accrued"] for row in result["periods"][1:]]
    assert accruals == pytest.approx([80_000.00, 80_000.00, 96_000.00,
                                      96_000.00], abs=CENT)
    assert result["unpaid_pref"] == pytest.approx(352_000.00, abs=CENT)
    assert result["unreturned_capital"] == pytest.approx(1_200_000.00,
                                                         abs=CENT)


def test_a_period_zero_distribution_is_paid_before_any_pref_exists():
    """Cash returned at close cannot be preferred return — there is none
    yet — so it reduces capital, and period 1 accrues on the smaller
    base: 8% of 900,000 = 72,000.

    Contributions are spelled out period by period rather than using the
    scalar shorthand, which refuses a period-0 distribution precisely
    because it cannot tell one from a series that starts at year 1.
    """
    result = run_waterfall([1_000_000.0, 0.0], [100_000.0, 0.0],
                           WaterfallTerms(pref_compounding=COMPOUNDING_ANNUAL,
                                          **NO_COINVEST))
    assert result["periods"][0]["capital_returned"] == pytest.approx(
        100_000.00, abs=CENT)
    assert result["periods"][0]["pref_paid"] == 0.0
    assert result["periods"][1]["pref_accrued"] == pytest.approx(72_000.00,
                                                                 abs=CENT)


# ── Deals that never clear the hurdle ───────────────────────────────

def test_a_deal_that_never_returns_capital_pays_no_promote():
    """1,080,000 -> 1,112,400 -> 1,147,392 -> 1,185,183.36, each less
    50,000. Tier 1 is never current, so the residual and the promote are
    zero in every period and the shortfall is stated rather than left to
    read as "nothing above the hurdle".
    """
    result = _run(WaterfallTerms(pref_compounding=COMPOUNDING_ANNUAL,
                                 **NO_COINVEST),
                  distributions=[0.0] + [50_000.0] * 4)
    assert result["gp"]["promote"] == 0.0
    assert all(row["residual"] == 0.0 for row in result["periods"])
    assert result["tier1_current"] is False
    # Every period reports the shortfall, not just the total — E3 renders
    # this as a per-year column, and the simple branch computes it from
    # different state than the compounded one.
    assert [row["tier1_current"] for row in result["periods"]] == [False] * 5
    # roc_first, so each $50,000 pays down capital and the pref piles up
    # behind it: $1,000,000 - $200,000 returned, $1,135,183.36 - $800,000.
    assert result["unreturned_capital"] == pytest.approx(800_000.00, abs=CENT)
    assert result["unpaid_pref"] == pytest.approx(335_183.36, abs=CENT)
    assert result["unreturned_capital"] + result["unpaid_pref"] == \
        pytest.approx(1_135_183.36, abs=CENT)
    assert result["lp"]["moic"] == pytest.approx(0.20, abs=1e-9)


def test_the_tier_one_tolerance_is_pinned_at_both_edges():
    """`BALANCE_TOLERANCE` decides whether a deal cleared its hurdle, and
    every other fixture either clears to exactly $0.00 or misses by more
    than a million — so the threshold could be widened by six orders of
    magnitude undetected. These two sit either side of it.

    Against a compounded balance of $1,469,328.08 at period 5: paying
    $1,464,428.08 leaves $4,900 outstanding (short), paying
    $1,469,328.076 leaves $0.004 (float noise, clear).
    """
    terms = WaterfallTerms(pref_compounding=COMPOUNDING_ANNUAL, **NO_COINVEST)
    balance = 1_000_000.0 * 1.08 ** 5
    assert balance == pytest.approx(1_469_328.08, abs=CENT)

    short = run_waterfall(1_000_000.0, [0.0] * 5 + [balance - 4_900.0], terms)
    assert short["tier1_current"] is False
    assert short["unreturned_capital"] + short["unpaid_pref"] == \
        pytest.approx(4_900.00, abs=CENT)
    assert short["gp"]["promote"] == 0.0

    noise = run_waterfall(1_000_000.0, [0.0] * 5 + [balance - 0.004], terms)
    assert noise["tier1_current"] is True


def test_a_total_loss_reports_no_irr_rather_than_nan():
    """`json.dumps(nan)` is invalid JSON that Postgres JSONB rejects, and
    E3 persists these results."""
    result = run_waterfall(1_000_000.0, [0.0] * 6, ORACLE_1)
    assert result["lp"]["irr"] is None
    assert result["lp"]["moic"] == 0.0


# ── Degenerate but legal terms ──────────────────────────────────────

def test_a_zero_promote_leaves_the_residual_with_the_lp():
    result = _run(WaterfallTerms(pref_compounding=COMPOUNDING_ANNUAL,
                                 pref_rate=0.08, promote_split=0.0,
                                 gp_coinvest_pct=0.0))
    assert result["gp"]["promote"] == 0.0
    assert result["lp"]["distributions"] == pytest.approx(1_760_000.00,
                                                          abs=CENT)


def test_a_zero_pref_makes_tier_one_a_plain_return_of_capital():
    """No hurdle: 1,000,000 of capital back, and the remaining 760,000 is
    residual, of which the GP promotes 20% = 152,000."""
    result = _run(WaterfallTerms(pref_compounding=COMPOUNDING_ANNUAL,
                                 pref_rate=0.0, promote_split=0.20,
                                 gp_coinvest_pct=0.0))
    assert result["unreturned_capital"] == pytest.approx(0.0, abs=CENT)
    assert result["gp"]["promote"] == pytest.approx(152_000.00, abs=CENT)


def test_a_waterfall_with_no_contributed_capital_warns(caplog):
    """Arithmetically consistent — every dollar is residual — but a
    promote on a deal nobody funded is a profit split, not this
    structure, so it does not pass unremarked."""
    with caplog.at_level("WARNING", logger="cim_analyst"):
        result = run_waterfall(0.0, [0.0, 100_000.0], ORACLE_1)
    assert result["lp"]["irr"] is None
    assert result["lp"]["moic"] is None
    assert "no contributed capital" in caplog.text


# ── Conservation: every dollar distributed lands somewhere ──────────

@pytest.mark.parametrize("terms", [ORACLE_1, ORACLE_2, ORACLE_3])
def test_every_dollar_distributed_reaches_the_lp_or_the_gp(terms):
    result = _run(terms)
    for row in result["periods"]:
        assert row["lp_distribution"] + row["gp_distribution"] == \
            pytest.approx(row["distribution"], abs=CENT)
    assert result["lp"]["distributions"] + result["gp"]["distributions"] == \
        pytest.approx(result["total_distributions"], abs=CENT)


@pytest.mark.parametrize("terms", [ORACLE_1, ORACLE_2, ORACLE_3])
def test_tier_one_reconciles_to_its_capital_and_pref_parts(terms):
    """A reconciliation, not a proof — both assertions hold by
    construction (`capital_returned` is DEFINED as `tier1 - pref_paid`).
    Kept because it would catch a future refactor that computes the two
    parts independently, but the split itself is pinned in absolute
    dollars by the two tests below."""
    result = _run(terms)
    for row in result["periods"]:
        assert row["capital_returned"] + row["pref_paid"] == pytest.approx(
            row["tier1_paid"], abs=CENT)
        assert row["ending_unreturned_capital"] + row["ending_unpaid_pref"] \
            == pytest.approx(row["ending_balance"], abs=CENT)


def test_the_compounded_split_follows_the_stated_ordering_in_dollars():
    """The memo rows are read beside the assumption stamp, which says
    "Return of capital first". Applying pref first under that stamp
    printed $0.00 of capital returned for four straight years — every
    dollar right, and unreconcilable against its own stated basis.

    Absolute dollars, because the reconciliation test above is an
    identity: oracle 1 returns capital $50k/$60k/$70k/$80k in years 1-4
    with no pref paid, then $740,000 of remaining capital plus
    $417,672.91 of accrued preferred return at sale.
    """
    result = _run(ORACLE_1)                      # compounded, roc_first
    assert [row["capital_returned"] for row in result["periods"]] == \
        pytest.approx([0.0, 50_000.0, 60_000.0, 70_000.0, 80_000.0,
                       740_000.0], abs=CENT)
    assert [row["pref_paid"] for row in result["periods"]] == \
        pytest.approx([0.0, 0.0, 0.0, 0.0, 0.0, 417_672.91], abs=CENT)


def test_the_compounded_split_flips_with_the_ordering_and_moves_no_dollar():
    """Same flows, `pref_first`: the memo rows invert while every LP and
    GP dollar stays put, which is what "presentation only" has to mean."""
    roc = _run(ORACLE_1)
    pref = _run(WaterfallTerms(pref_compounding=COMPOUNDING_ANNUAL,
                               ordering=ORDERING_PREF_FIRST, **NO_COINVEST))
    # Pref first leaves capital untouched until the sale, so the final
    # period pays the WHOLE $1,000,000 of capital and only the
    # $157,672.91 of pref still accrued against it — not the $417,672.91
    # that roc_first leaves outstanding after returning capital first.
    assert [row["pref_paid"] for row in pref["periods"]] == pytest.approx(
        [0.0, 50_000.0, 60_000.0, 70_000.0, 80_000.0, 157_672.91], abs=CENT)
    assert [row["capital_returned"] for row in pref["periods"]] == \
        pytest.approx([0.0, 0.0, 0.0, 0.0, 0.0, 1_000_000.0], abs=CENT)
    assert pref["lp"]["cash_flows"] == pytest.approx(roc["lp"]["cash_flows"],
                                                     abs=CENT)
    assert pref["gp"]["promote"] == pytest.approx(roc["gp"]["promote"],
                                                  abs=CENT)


# ── Promote paid before a later capital call ────────────────────────

def test_a_promote_survives_a_later_capital_call_and_says_so(caplog):
    """The limit of "no promote until tier 1 is current". A residual
    distribution in period 2 pays the GP $150,048; a $5,000,000 call in
    period 3 re-opens tier 1 and the LP ends at 0.39x. With no clawback
    the GP keeps the promote — the correct model of the operator's fund
    terms — but a `tier1_current: False` sitting beside a positive
    promote is not a statement, so `unrecovered_promote` makes it one.
    """
    with caplog.at_level("WARNING", logger="cim_analyst"):
        result = run_waterfall([1_000_000.0, 0.0, 0.0, 5_000_000.0, 0.0],
                               [0.0, 0.0, 2_000_000.0, 0.0, 500_000.0],
                               WaterfallTerms())
    assert result["tier1_current"] is False
    assert result["gp"]["promote"] == pytest.approx(150_048.00, abs=CENT)
    assert result["unrecovered_promote"] == pytest.approx(150_048.00, abs=CENT)
    assert result["lp"]["moic"] == pytest.approx(0.3889, abs=0.0001)
    assert "no clawback" in caplog.text


def test_promote_taken_at_the_final_distribution_is_not_flagged(caplog):
    """The ordinary single-asset shape: the residual arises at sale and
    no capital can be called after it, so nothing is flagged."""
    with caplog.at_level("WARNING", logger="cim_analyst"):
        result = _run(ORACLE_1)
    assert result["gp"]["promote"] > 0
    assert result["unrecovered_promote"] == 0.0
    assert "clawback" not in caplog.text


def test_a_deal_that_takes_a_call_and_still_makes_the_lp_whole_is_not_flagged(
        caplog):
    """The cap is the whole point of the field. This deal pays a promote
    in period 2, takes a $500,000 call in period 3, then re-clears tier 1
    in full and returns the LP 1.78x. A clawback recovers only up to the
    ending shortfall, and there is none — so an uncapped flag would put a
    clawback caveat on the memo of a deal where nothing was owed.
    """
    with caplog.at_level("WARNING", logger="cim_analyst"):
        result = run_waterfall(
            [1_000_000.0, 0.0, 0.0, 500_000.0, 0.0, 0.0],
            [0.0, 0.0, 2_000_000.0, 0.0, 0.0, 900_000.0],
            WaterfallTerms(gp_coinvest_pct=0.0))
    assert result["tier1_current"] is True
    assert result["unreturned_capital"] == pytest.approx(0.0, abs=CENT)
    assert result["gp"]["promote"] > 0
    assert result["unrecovered_promote"] == 0.0
    assert result["lp"]["moic"] == pytest.approx(1.7799, abs=0.0001)
    assert "clawback" not in caplog.text


def test_a_promote_in_the_same_period_as_the_last_call_is_not_flagged():
    """The boundary of `period < last_call`. Period 2 takes the call AND
    pays the promote, in that order — capital was called before the cash
    went out, so nothing was promoted ahead of it. Without this fixture,
    flipping `<` to `<=` passes the entire suite.
    """
    result = run_waterfall([1_000_000.0, 0.0, 500_000.0],
                           [0.0, 0.0, 3_000_000.0], WaterfallTerms())
    assert result["gp"]["promote"] == pytest.approx(240_048.00, abs=CENT)
    assert result["tier1_current"] is True
    assert result["unrecovered_promote"] == 0.0


# ── The pari-passu shortcut, against a second implementation ────────

def _two_separate_accounts(contribs, dists, terms):
    """A deliberately independent reference implementation.

    `run_waterfall` rolls ONE aggregate accrual account and splits each
    tier-1 payment by `gp_coinvest_pct`. This rolls the LP's and the GP's
    accounts separately and splits tier-1 cash by each partner's share of
    the OUTSTANDING claim — which is what pari passu has to mean when the
    accounts are tracked apart, and which is not obviously the same
    number once contributions arrive unevenly across periods. If the
    shortcut is right they agree to float noise; if it is wrong this is
    what says so.
    """
    coinvest = terms.gp_coinvest_pct
    compounded = terms.pref_compounding == COMPOUNDING_ANNUAL
    books = {"lp": {"share": 1.0 - coinvest}, "gp": {"share": coinvest}}
    for book in books.values():
        book.update(balance=0.0, capital=0.0, pref=0.0, paid=0.0)

    for period, (contribution, cash) in enumerate(zip(contribs, dists)):
        for book in books.values():
            if period > 0:
                if compounded:
                    book["balance"] += book["balance"] * terms.pref_rate
                else:
                    book["pref"] += book["capital"] * terms.pref_rate
            book["capital"] += contribution * book["share"]
            book["balance"] += contribution * book["share"]

        claims = {key: (book["balance"] if compounded
                        else book["capital"] + book["pref"])
                  for key, book in books.items()}
        outstanding = sum(claims.values())
        tier1 = min(cash, outstanding)
        for key, book in books.items():
            paid = tier1 * claims[key] / outstanding if outstanding else 0.0
            book["paid"] += paid
            if compounded:
                book["balance"] -= paid
            elif terms.ordering == ORDERING_ROC_FIRST:
                returned = min(paid, book["capital"])
                book["capital"] -= returned
                book["pref"] -= min(paid - returned, book["pref"])
            else:
                pref_paid = min(paid, book["pref"])
                book["pref"] -= pref_paid
                book["capital"] -= min(paid - pref_paid, book["capital"])

        residual = cash - tier1
        lp_residual = residual * (1.0 - coinvest)
        promote = lp_residual * terms.promote_split
        books["lp"]["paid"] += lp_residual - promote
        books["gp"]["paid"] += residual - lp_residual + promote

    return books["lp"]["paid"], books["gp"]["paid"]


#: Uneven, multi-period contribution schedules — the case where rolling
#: one aggregate account and rolling two could plausibly diverge.
UNEVEN_SCHEDULES = [
    ([1_000_000.0, 0.0, 250_000.0, 0.0, 0.0, 0.0],
     [0.0, 40_000.0, 55_000.0, 90_000.0, 110_000.0, 1_900_000.0]),
    ([600_000.0, 400_000.0, 0.0, 300_000.0, 0.0],
     [0.0, 0.0, 120_000.0, 0.0, 2_100_000.0]),
    ([1_000_000.0, 0.0, 0.0, 0.0],           # never clears the hurdle
     [0.0, 30_000.0, 30_000.0, 30_000.0]),
]


@pytest.mark.parametrize("contribs,dists", UNEVEN_SCHEDULES)
@pytest.mark.parametrize("compounding", [COMPOUNDING_ANNUAL,
                                         COMPOUNDING_SIMPLE])
@pytest.mark.parametrize("ordering", [ORDERING_ROC_FIRST, ORDERING_PREF_FIRST])
@pytest.mark.parametrize("coinvest", [0.0, 0.10, 0.30])
def test_one_aggregate_account_equals_two_separate_ones(contribs, dists,
                                                        compounding, ordering,
                                                        coinvest):
    """The load-bearing shortcut in this module, checked rather than
    argued. It holds because contributions split at a fixed ratio every
    period, so the two claims never leave that ratio."""
    terms = WaterfallTerms(pref_rate=0.08, pref_compounding=compounding,
                           ordering=ordering, promote_split=0.20,
                           gp_coinvest_pct=coinvest)
    result = run_waterfall(contribs, dists, terms)
    lp_expected, gp_expected = _two_separate_accounts(contribs, dists, terms)
    assert result["lp"]["distributions"] == pytest.approx(lp_expected,
                                                          abs=CENT)
    assert result["gp"]["distributions"] == pytest.approx(gp_expected,
                                                          abs=CENT)
    assert result["lp"]["distributions"] + result["gp"]["distributions"] == \
        pytest.approx(sum(dists), abs=CENT)


# ── Input validation ────────────────────────────────────────────────

def test_a_single_number_means_all_equity_at_close():
    scalar = run_waterfall(CONTRIBUTION, DISTRIBUTIONS, ORACLE_1)
    explicit = run_waterfall([CONTRIBUTION] + [0.0] * 5, DISTRIBUTIONS,
                             ORACLE_1)
    assert scalar["lp"]["cash_flows"] == pytest.approx(
        explicit["lp"]["cash_flows"], abs=CENT)


def test_the_scalar_shorthand_refuses_a_series_that_may_start_at_year_one():
    """The shorthand takes its period count from `distributions`, so it
    cannot notice a series that omits the period-0 slot — and that is the
    series the rest of the pipeline hands out, since
    `project_cash_flows` puts the negative basis at index 0 and the
    obvious `cash_flows[1:]` is exactly hold_years long.

    Measured on a 5-year, $4.7M-equity deal, the misread returns LP IRR
    14.1563% against the correct 11.2437% and $102,308 of extra promote,
    silently. Nothing in the values distinguishes the two readings, so
    the ambiguous one is refused.
    """
    years_one_to_five = [180_000.0, 210_000.0, 240_000.0, 270_000.0,
                         7_100_000.0]
    with pytest.raises(ValueError, match="period 0 is the CLOSE date"):
        run_waterfall(4_700_000.0, years_one_to_five, ORACLE_1)

    correct = run_waterfall(4_700_000.0, [0.0] + years_one_to_five, ORACLE_1)
    assert correct["lp"]["irr"] * 100 == pytest.approx(11.2437, abs=0.0001)


def test_a_close_date_distribution_stays_expressible():
    """Refusing the ambiguous shorthand must not make a genuine
    close-date distribution unmodellable — spell the contributions out."""
    result = run_waterfall([1_000_000.0, 0.0], [25_000.0, 1_200_000.0],
                           ORACLE_1)
    assert result["periods"][0]["capital_returned"] == pytest.approx(
        25_000.00, abs=CENT)


def test_mismatched_series_lengths_raise_rather_than_pad():
    """Padding a 5-element distribution list against a 1-element
    contribution list aligns year 1's cash to period 0 and silently
    deletes a year of preferred return."""
    with pytest.raises(ValueError, match="period-indexed"):
        run_waterfall([CONTRIBUTION], [0.0, 50_000.0, 1_500_000.0], ORACLE_1)


def test_distributions_must_be_a_sequence():
    with pytest.raises(TypeError, match="period-indexed sequence"):
        run_waterfall(CONTRIBUTION, 1_500_000.0, ORACLE_1)


@pytest.mark.parametrize("series,why", [
    ("123", "a string iterates to THREE periods of $1, $2 and $3"),
    ({1: "a", 2: "b"}, "a dict iterates its KEYS, so these become $1 and $2"),
    (b"12", "bytes iterate to ints"),
])
def test_things_that_iterate_but_are_not_cash_flow_series_are_refused(series,
                                                                     why):
    """Each of these used to be read as a valid series and produce a
    confident wrong answer rather than an error. `why` documents the
    silent reading that was happening."""
    with pytest.raises(TypeError, match="period-indexed sequence"):
        run_waterfall(CONTRIBUTION, series, ORACLE_1)


def test_a_generator_is_refused_because_it_has_no_length_to_align():
    """Consumed by the first pass, and unmeasurable against the other
    series — so the length check that stops a misaligned pair could not
    run at all."""
    with pytest.raises(TypeError, match="period-indexed sequence"):
        run_waterfall(CONTRIBUTION, (x for x in [0.0, 50_000.0]), ORACLE_1)


def test_a_missing_period_is_refused_rather_than_read_as_zero():
    """`float(None or 0.0)` is 0.0. A missing period in a cash-flow
    series is missing data, and calling it "distributed nothing" is a
    swallowed error — the more so next to a NaN in the same slot, which
    does raise."""
    with pytest.raises(ValueError, match="missing data"):
        run_waterfall(CONTRIBUTION, [0.0, None, 50_000.0], ORACLE_1)
    with pytest.raises(ValueError, match="missing data"):
        run_waterfall([1_000_000.0, ""], [0.0, 50_000.0], ORACLE_1)


def test_a_boolean_does_not_fund_a_deal():
    """bool is a subclass of int, so True would fund $1 of equity."""
    with pytest.raises(ValueError, match="bool is a subclass"):
        run_waterfall(True, [0.0, 50_000.0], ORACLE_1)
    with pytest.raises(ValueError, match="must be a number"):
        run_waterfall([True, False], [0.0, 50_000.0], ORACLE_1)


def test_an_empty_distribution_series_raises():
    with pytest.raises(ValueError, match="nothing to distribute"):
        run_waterfall(CONTRIBUTION, [], ORACLE_1)


def test_negative_flows_are_rejected_rather_than_netted():
    with pytest.raises(ValueError, match="distributions\\[1\\]"):
        run_waterfall(CONTRIBUTION, [0.0, -50_000.0], ORACLE_1)
    with pytest.raises(ValueError, match="contributions\\[1\\]"):
        run_waterfall([1_000_000.0, -5_000.0], [0.0, 50_000.0], ORACLE_1)


def test_nan_flows_are_rejected():
    """Every comparison against NaN is False, so it walks past the sign
    guard and then produces a NaN promote reported as a number."""
    with pytest.raises(ValueError, match="finite"):
        run_waterfall(CONTRIBUTION, [0.0, float("nan")], ORACLE_1)
    with pytest.raises(ValueError, match="finite"):
        run_waterfall([float("inf"), 0.0], [0.0, 50_000.0], ORACLE_1)


# ── Terms that are not this waterfall ───────────────────────────────

def test_a_catch_up_tier_is_refused_not_ignored():
    with pytest.raises(NotImplementedError, match="catch-up"):
        WaterfallTerms(catch_up=True)


def test_a_committed_capital_accrual_base_is_refused():
    with pytest.raises(NotImplementedError, match="committed"):
        WaterfallTerms(accrual_base=ACCRUAL_BASE_COMMITTED)


def test_netting_the_am_fee_from_lp_distributions_is_refused():
    with pytest.raises(NotImplementedError, match="netted_from_lp"):
        WaterfallTerms(am_fee_treatment=AM_FEE_NETTED_FROM_LP)


def test_an_unrecognised_convention_raises_instead_of_defaulting():
    """The design doc's own fixture pays GP promote of 68,465 / 72,000 /
    81,600 depending only on which convention is in force, so a silent
    substitution is a confident wrong LP net IRR."""
    with pytest.raises(ValueError, match="pref_compounding"):
        WaterfallTerms(pref_compounding="monthly")
    with pytest.raises(ValueError, match="ordering"):
        WaterfallTerms(ordering="pro_rata")
    with pytest.raises(ValueError, match="accrual_base"):
        WaterfallTerms(accrual_base="invested")
    with pytest.raises(ValueError, match="am_fee_treatment"):
        WaterfallTerms(am_fee_treatment="deferred")


def test_out_of_range_rates_and_splits_are_rejected():
    with pytest.raises(ValueError, match="pref_rate"):
        WaterfallTerms(pref_rate=-0.08)
    with pytest.raises(ValueError, match="promote_split"):
        WaterfallTerms(promote_split=1.0)
    with pytest.raises(ValueError, match="promote_split"):
        WaterfallTerms(promote_split=-0.20)
    with pytest.raises(ValueError, match="gp_coinvest_pct"):
        WaterfallTerms(gp_coinvest_pct=1.0)


def test_a_whole_number_percent_pref_is_rejected_like_the_split_fields():
    """This codebase displays percentages as whole numbers and stores
    them as decimals, so `pref_rate=8` meaning 8% is the live mistake.
    Unbounded it accrued $8,000,000 of preferred return on $1,000,000 of
    capital in year one and said nothing — while `promote_split=20` on
    the same form correctly raised."""
    with pytest.raises(ValueError, match="0.08, not 8"):
        WaterfallTerms(pref_rate=8)
    with pytest.raises(ValueError, match="0.08, not 8"):
        WaterfallTerms(pref_rate=1.0)
    assert WaterfallTerms(pref_rate=0.08).pref_rate == 0.08


def test_a_numeric_field_given_as_a_string_is_coerced_not_deferred():
    """It used to construct fine and then fail inside `assumption_stamp`
    with "Unknown format code '%' for object of type 'str'" — an error
    naming neither the field nor the caller."""
    terms = WaterfallTerms(pref_rate="0.08", promote_split="0.20")
    assert isinstance(terms.pref_rate, float)
    assert terms.pref_rate == 0.08
    assert run_waterfall(CONTRIBUTION, DISTRIBUTIONS, terms)["gp"]["promote"]
    with pytest.raises(ValueError, match="must be a number"):
        WaterfallTerms(pref_rate="eight percent")


def test_a_boolean_is_not_a_rate():
    """bool is a subclass of int, so True would be a 100% pref."""
    with pytest.raises(ValueError, match="bool is a subclass"):
        WaterfallTerms(pref_rate=True)
    with pytest.raises(ValueError, match="bool is a subclass"):
        WaterfallTerms(gp_coinvest_pct=False)


def test_nan_terms_are_rejected():
    with pytest.raises(ValueError, match="finite"):
        WaterfallTerms(pref_rate=float("nan"))
    with pytest.raises(ValueError, match="finite"):
        WaterfallTerms(promote_split=float("inf"))
    with pytest.raises(ValueError, match="must be a number"):
        WaterfallTerms(gp_coinvest_pct=None)


@pytest.mark.parametrize("build", [
    lambda value: WaterfallTerms(catch_up=value),
    lambda value: resolve_waterfall_terms({"catch_up": value}),
], ids=["direct", "resolved"])
def test_a_stringified_false_does_not_switch_on_the_catch_up(build):
    """`bool("False")` is True, which would fail the run with "a GP
    catch-up tier is not supported" on terms that asked for none.

    Both construction paths, because they disagreed: `resolve_waterfall_
    terms` coerced and direct construction did not, so
    `WaterfallTerms(catch_up="False")` raised at a caller asking for
    exactly the opposite. Direct construction is the path the CLI and
    E3's tests take, and only the resolved path had a test.
    """
    assert build("False").catch_up is False
    assert build("0").catch_up is False
    assert build(0).catch_up is False
    with pytest.raises(NotImplementedError, match="catch-up"):
        build("true")
    with pytest.raises(ValueError, match="must be a boolean"):
        build("maybe")


# ── Resolution from config ──────────────────────────────────────────

def test_resolve_reads_config_and_applies_overrides():
    terms = resolve_waterfall_terms({"pref_rate": 0.09})
    assert terms.pref_rate == 0.09
    assert terms.promote_split == cfg.WATERFALL_TERMS["promote_split"]
    assert terms.pref_compounding == cfg.WATERFALL_TERMS["pref_compounding"]


def test_an_omitted_key_means_the_default_and_an_explicit_zero_means_zero():
    assert resolve_waterfall_terms({}).pref_rate == \
        cfg.WATERFALL_TERMS["pref_rate"]
    assert resolve_waterfall_terms({"pref_rate": None}).pref_rate == \
        cfg.WATERFALL_TERMS["pref_rate"]
    assert resolve_waterfall_terms({"promote_split": 0}).promote_split == 0.0


def test_an_unknown_override_key_is_logged_and_ignored(caplog):
    """A stored override row written by a future version must not take
    down a run on an older one — E1's contract, kept."""
    with caplog.at_level("WARNING", logger="cim_analyst"):
        terms = resolve_waterfall_terms({"second_hurdle": 0.14})
    assert "second_hurdle" in caplog.text
    assert terms.pref_rate == cfg.WATERFALL_TERMS["pref_rate"]


def test_an_unknown_override_value_still_raises():
    """Unknown KEYS are forward compatibility; unknown VALUES for a
    convention field re-price the promote."""
    with pytest.raises(ValueError, match="pref_compounding"):
        resolve_waterfall_terms({"pref_compounding": "quarterly"})


def test_gp_coinvest_comes_from_the_capital_block_not_waterfall_terms():
    """One source of truth: `model.returns_model.resolve_capital_structure`
    reads the same scalar for the Sources & Uses stack."""
    assert "gp_coinvest_pct" not in cfg.WATERFALL_TERMS
    assert resolve_waterfall_terms().gp_coinvest_pct == cfg.GP_COINVEST_PCT


def test_the_deals_own_coinvest_beats_the_config_default():
    """GP co-invest is a PER-DEAL assumption — it is on the assumptions
    page, `resolve_capital_structure` resolves it, and `engine.py` hands
    that value to `build_sources_uses`. Seeding the waterfall from the
    config scalar alone would print a Sources & Uses stack split 25/75
    beside an LP net IRR computed on 10/90: two numbers on one page,
    derived from different equity, neither flagged.
    """
    from model.returns_model import resolve_capital_structure

    capital = resolve_capital_structure({"gp_coinvest_pct": 0.25})
    terms = resolve_waterfall_terms(capital_structure=capital)
    assert terms.gp_coinvest_pct == 0.25
    # And the stack the waterfall is measured against agrees.
    assert capital["gp_coinvest_pct"] == terms.gp_coinvest_pct
    # An explicit override still wins over both.
    assert resolve_waterfall_terms({"gp_coinvest_pct": 0.05},
                                   capital_structure=capital
                                   ).gp_coinvest_pct == 0.05
    # A capital structure that does not name it falls back to config.
    assert resolve_waterfall_terms(capital_structure={}).gp_coinvest_pct == \
        cfg.GP_COINVEST_PCT


def test_dataclass_defaults_do_not_drift_from_config():
    """CLAUDE.md allows a static fallback only when the no-drift
    invariant is CI-guarded; this is that guard.

    It checks the key SETS as well as the values. Comparing only the
    keys present in both is drift-blind in the direction that matters:
    delete or rename `pref_rate` in config and the mirrored dataclass
    default silently becomes the only source of truth, which is exactly
    the divergence the rule exists to prevent — and it would pass a
    value-only comparison.
    """
    import dataclasses

    defaults = {f.name: f.default for f in dataclasses.fields(WaterfallTerms)}
    expected_keys = set(defaults) - {"gp_coinvest_pct"}   # see the test below
    assert set(cfg.WATERFALL_TERMS) == expected_keys, (
        "config.WATERFALL_TERMS and WaterfallTerms describe different term "
        f"sets — config only: {sorted(set(cfg.WATERFALL_TERMS) - expected_keys)}, "
        f"dataclass only: {sorted(expected_keys - set(cfg.WATERFALL_TERMS))}")

    drifted = {key: (defaults[key], value)
               for key, value in cfg.WATERFALL_TERMS.items()
               if defaults[key] != value}
    assert drifted == {}, (
        "WaterfallTerms defaults drifted from config.WATERFALL_TERMS "
        f"(field default, config value): {drifted}")
    assert defaults["gp_coinvest_pct"] == cfg.GP_COINVEST_PCT


def test_config_defaults_are_the_operator_fund_terms():
    """8% pref, 20/80 above it, no catch-up — recorded in
    docs/levered-waterfall-design.md and in the fund-structure memo."""
    assert cfg.WATERFALL_TERMS["pref_rate"] == 0.08
    assert cfg.WATERFALL_TERMS["promote_split"] == 0.20
    assert cfg.WATERFALL_TERMS["catch_up"] is False


# ── The assumption stamp ────────────────────────────────────────────

def test_every_open_lpa_question_appears_in_the_stamp():
    """"Do not let an LP net IRR leave the building without its stamp."
    Five open questions, five rows."""
    stamp = assumption_stamp(resolve_waterfall_terms())
    assert [row["key"] for row in stamp] == [
        "pref_compounding", "accrual_base", "ordering", "am_fee_treatment",
        "promote_basis"]
    assert all(row["question"] and row["label"] for row in stamp)
    assert _run(ORACLE_1)["assumption_stamp"] == assumption_stamp(ORACLE_1)


def test_the_stamp_says_when_ordering_is_inert():
    compounded = {row["key"]: row["label"]
                  for row in assumption_stamp(ORACLE_1)}
    simple = {row["key"]: row["label"] for row in assumption_stamp(ORACLE_2)}
    assert "presentation only" in compounded["ordering"]
    assert "presentation only" not in simple["ordering"]


def test_the_am_fee_row_does_not_claim_a_rate_this_module_never_charged():
    """The fee is charged upstream by E3, and no AM-fee rate exists in
    config. A row reading only "Above the waterfall (deal expense)" beside
    an LP *net* IRR implies a completeness this module cannot deliver —
    1% of committed equity, of invested capital and of asset value are all
    live conventions with different answers."""
    row = {r["key"]: r["label"] for r in assumption_stamp(ORACLE_1)}
    assert "set by the caller" in row["am_fee_treatment"]


def test_the_result_survives_the_json_encoder_that_e3_will_persist_it_with():
    """`webapp.services.json_safe` falls back to `str(obj)` on anything
    it does not recognise, so returning the frozen dataclass under
    "terms" persisted it to JSONB as the string
    "WaterfallTerms(pref_rate=0.08, ...)" — unqueryable, and any consumer
    reading `["terms"]["pref_rate"]` got "string indices must be
    integers". It degraded silently rather than raising.
    """
    import json

    from webapp.services import json_safe

    payload = json_safe(_run(ORACLE_1))
    assert payload["terms"]["pref_rate"] == 0.08
    assert payload["terms"]["promote_split"] == 0.20
    assert WaterfallTerms(**payload["terms"]) == ORACLE_1
    json.dumps(payload, allow_nan=False)      # Postgres JSONB rejects NaN
