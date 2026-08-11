"""Market-anchored exit cap.

The exit cap used to be a free-standing constant — 0.085 / 0.075 / 0.065 in
`SCENARIO_DEFAULTS`, plus a second triple 100 bp tighter in
`VALUE_ADD_SCENARIOS`. A 2003 drive-up facility and a 2022 climate-controlled
build exited at the same rate, and the two engines disagreed with each other.
It is now derived:

    exit_cap = market_cap(class, age band) + scenario spread
               + drift_bps_per_year × hold_years

Every expected value below is computed by hand from that rule against an
anchor the test passes in explicitly. Nothing here reads
`config.MARKET_CAP_RATES` for an expected number: that table is an
operator-maintained starting point and is supposed to move, so a test that
asserted its contents would fail on a routine settings edit while proving
nothing about the arithmetic.
"""

import datetime

import pytest

import config as cfg
from analysis import checks as C
from analysis.valuation import (COERCED_SCENARIOS, project_cash_flows,
                                resolve_exit_cap, resolve_market_cap,
                                run_scenarios)
from registry import (AGE_BAND_LABELS, ASSET_TYPES, DEFAULT_ASSET_TYPE,
                      ScenarioType, age_band, asset_age)

ANCHOR = 0.0625            # 6.25%, the mid-band self-storage rate
FIVE = 5


# ── 1. The rule, hand-computed ───────────────────────────────────────

@pytest.mark.parametrize("scen,spread,drift,expected", [
    # anchor + spread/10000 + drift * hold / 10000
    (ScenarioType.BEAR, 100.0, 10.0, 0.0625 + 0.0100 + 0.0050),   # 7.75%
    (ScenarioType.BASE,   0.0,  7.5, 0.0625 + 0.0000 + 0.00375),  # 6.625%
    (ScenarioType.BULL, -100.0, 5.0, 0.0625 - 0.0100 + 0.0025),   # 5.50%
])
def test_exit_cap_is_anchor_plus_spread_plus_drift(scen, spread, drift,
                                                   expected):
    d = resolve_exit_cap(ANCHOR, scen, FIVE)
    assert d["exit_cap"] == pytest.approx(expected, abs=1e-12)
    assert d["market_cap"] == ANCHOR
    assert d["scenario_spread_bps"] == spread
    assert d["drift_bps_per_year"] == drift
    assert d["drift_total_bps"] == drift * FIVE
    assert d["hold_years"] == FIVE


def test_the_three_caps_straddle_the_anchor_by_about_225_bps():
    """The spread between bear and bull is what keeps the bear case
    punitive. Under the retired constants it was 200 bp; the drift term
    widens it slightly because bear ages faster than bull."""
    caps = {s: resolve_exit_cap(ANCHOR, s, FIVE)["exit_cap"]
            for s in ScenarioType}
    assert caps[ScenarioType.BULL] < caps[ScenarioType.BASE] \
        < caps[ScenarioType.BEAR]
    assert (caps[ScenarioType.BEAR] - caps[ScenarioType.BULL]) \
        == pytest.approx(0.0225, abs=1e-12)


@pytest.mark.parametrize("hold", [1, 3, 5, 7, 10])
def test_drift_is_per_year_of_hold(hold):
    """The asset ages while it is owned, so the drift multiplies by the
    hold. Age at ACQUISITION is priced separately, by the band."""
    d = resolve_exit_cap(ANCHOR, ScenarioType.BASE, hold)
    assert d["drift_total_bps"] == pytest.approx(7.5 * hold, abs=1e-12)
    assert d["exit_cap"] == pytest.approx(ANCHOR + 7.5 * hold / 10_000,
                                          abs=1e-12)


def test_a_longer_hold_never_prices_a_tighter_exit():
    caps = [resolve_exit_cap(ANCHOR, ScenarioType.BASE, h)["exit_cap"]
            for h in range(1, 11)]
    assert caps == sorted(caps)


def test_spread_and_drift_can_be_overridden_per_call():
    d = resolve_exit_cap(ANCHOR, ScenarioType.BASE, FIVE,
                         spread_bps=25.0, drift_bps=0.0)
    assert d["exit_cap"] == pytest.approx(ANCHOR + 0.0025, abs=1e-12)
    assert d["drift_total_bps"] == 0.0


def test_an_unknown_scenario_gets_no_spread_and_no_drift():
    """A caller naming a scenario the tables do not carry must land on the
    anchor itself rather than silently inheriting another scenario's."""
    d = resolve_exit_cap(ANCHOR, "stress", FIVE)
    assert d["exit_cap"] == pytest.approx(ANCHOR, abs=1e-12)


# ── 2. The anchor: class × age band ──────────────────────────────────

def test_market_cap_reads_the_table_by_class_and_band():
    mc = resolve_market_cap("Self Storage", 2015,
                            as_of=datetime.date(2026, 1, 1))
    assert mc["source"] == "table"
    assert mc["asset_class"] == "Self Storage"
    assert mc["age_band"] == "mid"                    # 11 years old
    assert mc["age_band_known"] is True
    assert mc["market_cap"] == cfg.MARKET_CAP_RATES["Self Storage"]["mid"]
    assert mc["as_of"] == cfg.MARKET_CAP_AS_OF


def test_an_analyst_rate_beats_the_table():
    mc = resolve_market_cap("Self Storage", 2015, market_cap=0.0501,
                            as_of=datetime.date(2026, 1, 1))
    assert mc["market_cap"] == 0.0501
    assert mc["source"] == "analyst"
    # the table value is still reported, so the memo can say what was
    # overridden rather than just what was used
    assert mc["table_market_cap"] == cfg.MARKET_CAP_RATES["Self Storage"]["mid"]


def test_an_older_asset_prices_at_a_wider_cap():
    kw = {"as_of": datetime.date(2026, 1, 1)}
    rates = [resolve_market_cap("Self Storage", yr, **kw)["market_cap"]
             for yr in (2024, 2015, 2003, 1985)]
    assert rates == sorted(rates)


def test_an_unknown_vintage_falls_back_and_says_so():
    """The band must not be guessed silently: the fallback is the widest
    band, and `age_band_known` is what the check reads to flag it."""
    mc = resolve_market_cap("Self Storage", None)
    assert mc["age_band"] == cfg.MARKET_CAP_UNKNOWN_AGE_BAND
    assert mc["age_band_known"] is False
    assert mc["market_cap"] == cfg.MARKET_CAP_RATES["Self Storage"][
        cfg.MARKET_CAP_UNKNOWN_AGE_BAND]


def test_an_unrecognised_class_falls_back_to_the_default_class():
    mc = resolve_market_cap("Cold Storage", 2015,
                            as_of=datetime.date(2026, 1, 1))
    assert mc["asset_class"] == DEFAULT_ASSET_TYPE


def test_no_table_cell_and_no_analyst_rate_refuses():
    """Rather than invent one. Every exit value in the run hangs off it."""
    with pytest.raises(ValueError):
        resolve_market_cap("Self Storage", 2015, base={"Self Storage": {}},
                           as_of=datetime.date(2026, 1, 1))


def test_the_pristine_base_table_is_honoured_over_the_live_module():
    """`base` is how webapp.services keeps a concurrent run's patched
    table out of this deal — see the isolation gate in test_web_config."""
    mc = resolve_market_cap("Self Storage", 2015,
                            base={"Self Storage": {"mid": 0.0999}},
                            as_of=datetime.date(2026, 1, 1))
    assert mc["market_cap"] == 0.0999


# ── 3. No drift between the table and the class vocabulary ───────────

def test_market_cap_table_covers_exactly_the_declared_asset_types():
    """Single source of truth: `registry.ASSET_TYPES` is the class
    vocabulary — the settings-page scope dropdown, the ConfigOverride
    scope key and these table rows all key off it. A class present in one
    and absent from the other prices an exit off the fallback row without
    anyone noticing."""
    assert set(cfg.MARKET_CAP_RATES) == set(ASSET_TYPES)


def test_every_class_carries_every_age_band():
    bands = set(AGE_BAND_LABELS)
    for asset_class, row in cfg.MARKET_CAP_RATES.items():
        assert set(row) == bands, asset_class


def test_the_fallback_band_exists_in_every_class():
    for asset_class, row in cfg.MARKET_CAP_RATES.items():
        assert cfg.MARKET_CAP_UNKNOWN_AGE_BAND in row, asset_class


def test_drift_and_spread_tables_cover_every_scenario():
    for scen in ScenarioType:
        assert scen in cfg.EXIT_CAP_DRIFT_BPS
        assert scen in cfg.EXIT_CAP_SCENARIO_SPREAD_BPS


def test_drift_stays_inside_the_five_to_ten_bp_rule():
    """The operator's rule is 5-10 bp/yr for depreciation and
    obsolescence. A table edit outside that is a policy change, not a
    tuning tweak, and should fail here first."""
    for scen, bps in cfg.EXIT_CAP_DRIFT_BPS.items():
        assert 5.0 <= bps <= 10.0, scen


def test_the_exit_cap_is_no_longer_a_scenario_parameter():
    """It is derived. A stray `exit_cap` key back in either scenario dict
    would be read by nothing and silently diverge from the published cap."""
    for scen_params in cfg.SCENARIO_DEFAULTS.values():
        assert "exit_cap" not in scen_params
    for scen_params in cfg.VALUE_ADD_SCENARIOS.values():
        assert "exit_cap" not in scen_params


# ── 4. Age bands ─────────────────────────────────────────────────────

@pytest.mark.parametrize("year,expected", [
    (2026, "new"), (2021, "new"),
    (2020, "mid"), (2011, "mid"),
    (2010, "aging"), (1996, "aging"),
    (1995, "old"), (1970, "old"),
])
def test_age_band_boundaries(year, expected):
    assert age_band(year, as_of=datetime.date(2026, 1, 1)) == expected


def test_age_band_is_none_when_the_vintage_is_unknown():
    assert age_band(None) is None
    assert asset_age(None) is None


def test_as_of_makes_the_band_reproducible():
    """Re-deriving a stored run must not drift a band as the calendar
    moves — that is why `as_of` exists."""
    assert age_band(2011, as_of=datetime.date(2026, 1, 1)) == "mid"
    assert age_band(2011, as_of=datetime.date(2027, 1, 1)) == "aging"


def test_asset_age_never_goes_negative():
    assert asset_age(2030, as_of=datetime.date(2026, 1, 1)) == 0


def test_the_memo_age_narrative_keys_off_the_same_bands():
    """`analysis.physical` carried its own copy of the 5/15/30 ladder for
    the memo's prose. Now that the ladder prices the exit, a second copy
    would let the memo call an asset "mid-life" while the model priced it
    in the aging band."""
    from analysis.physical import _AGE_NARRATIVE, _age_narrative

    assert set(_AGE_NARRATIVE) == set(AGE_BAND_LABELS)
    assert _age_narrative(None) == "Year built not available."
    assert "mid-life" in _age_narrative(
        datetime.date.today().year - 10)
    assert "significant age" in _age_narrative(
        datetime.date.today().year - 40)


# ── 5. The projection refuses to invent a cap ────────────────────────

def test_project_cash_flows_requires_an_exit_cap():
    with pytest.raises(ValueError):
        project_cash_flows(ttm_noi=300_000, price=4_000_000, capex=0,
                           params=cfg.SCENARIO_DEFAULTS[ScenarioType.BASE])


def test_the_sensitivity_axis_override_still_bypasses_it():
    """The grid sweeps the cap, so it supplies its own."""
    r = project_cash_flows(
        ttm_noi=300_000, price=4_000_000, capex=0,
        params=cfg.SCENARIO_DEFAULTS[ScenarioType.BASE],
        coerce_exit_cap=False, exit_cap_override=0.055)
    assert r["exit_cap"] == 0.055


# ── 6. One anchor reaches every consumer ─────────────────────────────

class _FakeUnit:
    def __init__(self):
        self.count, self.sf, self.rate = 100, 100.0, 1.00
        self.climate_controlled = False
        self.label = "10x10"


class _FakeCIM:
    def __init__(self):
        self.nrsf = 50_000
        self.physical_occupancy = 0.70
        self.market_rent_psf = 1.20
        self.in_place_avg_rent_psf = 1.00
        self.ttm_total_expenses = 250_000
        self.unit_mix = [_FakeUnit()]
        self.ttm_noi = 300_000
        self.asking_price = 4_000_000


VA_FIN = {"expense_analysis": {"total_adjusted_expenses": 250_000}}


def test_both_engines_price_the_same_asset_at_the_same_cap():
    """The defect this change exists to fix. The value-add engine read its
    exit cap off a SECOND config triple 100 bp tighter than the static
    one, so one asset had two published exits and nothing reconciled them.
    Different engines — monthly with a lease-up ramp vs annual — but one
    resolver, so the caps must be identical, not merely close."""
    from model.value_add_model import run_value_add_scenarios

    mc = resolve_market_cap("Self Storage", 2015,
                            as_of=datetime.date(2026, 1, 1))
    static = run_scenarios(adjusted_ttm_noi=300_000, asking_price=8_000_000,
                           nrsf=50_000, market_cap=mc)
    va = run_value_add_scenarios(
        cim_data=_FakeCIM(), financial_analysis=VA_FIN,
        asking_price=8_000_000, capex=0, market_cap=mc)

    for scen in ScenarioType:
        # requested, not applied: the exit >= entry floor is a separate
        # rule and legitimately fires on one engine and not the other,
        # because they compute different Year 1 NOIs and so different
        # entry caps. What must agree is the DERIVED cap.
        assert static[scen]["requested_exit_cap"] == pytest.approx(
            va[scen]["requested_exit_cap"], abs=1e-12), scen
        assert static[scen]["exit_cap_detail"]["market_cap"] == \
            va[scen]["exit_cap_detail"]["market_cap"]


def test_the_value_add_engine_records_its_coercion():
    """It used to run a hand-rolled copy of the floor that set no flags,
    so a coerced VA cap was invisible to the check register while the
    static side reported its own."""
    from model.value_add_model import run_value_add_scenarios

    va = run_value_add_scenarios(
        cim_data=_FakeCIM(), financial_analysis=VA_FIN,
        asking_price=1_500_000,          # a very high entry cap
        capex=0, market_cap=resolve_market_cap(market_cap=ANCHOR))
    base = va[ScenarioType.BASE]
    assert base["exit_cap_coerced"] is True
    assert base["requested_exit_cap"] == pytest.approx(
        ANCHOR + 7.5 * 5 / 10_000, abs=1e-12)
    assert base["exit_cap"] > base["requested_exit_cap"]
    assert ScenarioType.BULL not in COERCED_SCENARIOS


def test_the_solver_prices_its_exit_off_the_same_anchor():
    """A wider market cap is a lower exit value, so a lower max offer.

    Both anchors are deliberately ABOVE the entry cap the solver lands on.
    Below it the exit >= entry floor binds in the base case and every
    anchor collapses onto the same coerced cap — real behaviour, but it
    would make this test pass for the wrong reason.
    """
    from model.solver import solve_max_price

    tight = solve_max_price(adjusted_ttm_noi=300_000,
                            market_cap=resolve_market_cap(market_cap=0.090))
    wide = solve_max_price(adjusted_ttm_noi=300_000,
                           market_cap=resolve_market_cap(market_cap=0.110))
    assert wide["max_price"] < tight["max_price"]


def test_the_sensitivity_grid_centres_on_the_derived_base_cap():
    from model.returns_model import build_returns_model

    mc = resolve_market_cap(market_cap=ANCHOR)
    sens = build_returns_model(
        adjusted_ttm_noi=300_000, asking_price=4_000_000, nrsf=50_000,
        capex=0, market_cap=mc)["sensitivity"]
    centre = resolve_exit_cap(ANCHOR, ScenarioType.BASE)["exit_cap"]
    assert sens["base_exit_cap"] == pytest.approx(centre, abs=1e-12)
    caps = sens["cap_values"]
    assert caps[len(caps) // 2] == pytest.approx(centre, abs=1e-9)
    assert caps == sorted(caps)


# ── 7. The check register ────────────────────────────────────────────

def _run(**kw):
    return C.run_checks(C.CheckInput(**kw), only={"market_exit_cap"})[0]


def _scen(anchor=ANCHOR, scen=ScenarioType.BASE, hold=FIVE, **extra):
    d = resolve_exit_cap(anchor, scen, hold)
    mc = resolve_market_cap(market_cap=anchor)
    row = {"exit_cap_detail": {**mc, **d},
           "requested_exit_cap": d["exit_cap"],
           "exit_cap": d["exit_cap"],
           "exit_cap_coerced": False}
    row.update(extra)
    return row


def test_check_skips_with_neither_anchor_nor_scenarios():
    assert _run().status == C.SKIPPED


def test_check_passes_and_prints_the_derivation():
    mc = resolve_market_cap("Self Storage", 2015,
                            as_of=datetime.date(2026, 1, 1))
    r = _run(market_cap=mc,
             scenarios={s: _scen(mc["market_cap"], s) for s in ScenarioType})
    assert r.status == C.PASS
    assert r.severity == C.ADVISORY
    assert "mid" in r.message and cfg.MARKET_CAP_AS_OF in r.message
    assert r.values["asset_class"] == "Self Storage"
    assert r.values["scenarios"]["base"]["drift_bps_per_year"] == 7.5
    # scenario names render as 'base', not 'ScenarioType.BASE'
    assert "Scenariotype" not in r.message


def test_check_flags_an_unknown_vintage():
    mc = resolve_market_cap("Self Storage", None)
    r = _run(market_cap=mc, scenarios={ScenarioType.BASE:
                                       _scen(mc["market_cap"])})
    assert r.status == C.FAIL
    assert "year built is unknown" in r.message
    assert r.values["age_band_known"] is False


def test_an_analyst_rate_needs_no_vintage():
    """The band only drives a TABLE lookup. When the analyst typed the
    rate, an unknown year built is not a finding."""
    mc = resolve_market_cap(None, None, market_cap=ANCHOR)
    r = _run(market_cap=mc, scenarios={ScenarioType.BASE: _scen()})
    assert r.status == C.PASS


def test_check_catches_a_consumer_that_missed_the_anchor():
    """The one-resolve discipline: every consumer is handed the same dict.
    Two distinct anchors means one of them was not."""
    mc = resolve_market_cap(market_cap=ANCHOR)
    r = _run(market_cap=mc,
             scenarios={ScenarioType.BASE: _scen(ANCHOR),
                        ScenarioType.BEAR: _scen(0.09, ScenarioType.BEAR)})
    assert r.status == C.FAIL
    assert "different market caps" in r.message


def test_check_catches_a_cap_that_does_not_rebuild_from_its_parts():
    mc = resolve_market_cap(market_cap=ANCHOR)
    broken = _scen()
    broken["requested_exit_cap"] += 0.01        # published, but unexplained
    r = _run(market_cap=mc, scenarios={ScenarioType.BASE: broken})
    assert r.status == C.FAIL
    assert "rebuild" in r.message


def test_check_flags_a_scenario_carrying_no_derivation():
    """A stored run from before the cap was derived, re-checked."""
    mc = resolve_market_cap(market_cap=ANCHOR)
    r = _run(market_cap=mc, scenarios={"base": {"exit_cap": 0.075}})
    assert r.status == C.FAIL
    assert "does not carry its exit-cap derivation" in r.message


def test_the_register_sees_value_add_scenarios_too():
    """`va_results` reaches the register now, so a VA coercion is
    reported instead of being invisible on all five surfaces."""
    r = C.run_checks(
        C.CheckInput(va_scenarios={ScenarioType.BASE: _scen(
            exit_cap_coerced=True, exit_cap=0.11)}),
        only={"exit_cap_coercion"})[0]
    assert r.status == C.FAIL
    assert "value-add base" in r.message.lower()
    assert "value-add base" in r.values


def test_the_register_carries_both_engines_at_once():
    r = C.run_checks(
        C.CheckInput(scenarios={ScenarioType.BASE: _scen()},
                     va_scenarios={ScenarioType.BASE: _scen()},
                     market_cap=resolve_market_cap(market_cap=ANCHOR)),
        only={"market_exit_cap"})[0]
    assert set(r.values["scenarios"]) == {"base", "value-add base"}


# ── 8. Both orchestrations resolve, and resolve the same way ─────────

def test_the_cli_resolves_an_anchor_for_every_consumer():
    """`run.py` is a SECOND orchestration — it does not call
    `engine.run_analysis` — so it has to resolve the anchor itself. It
    did not at first, and every CLI deal silently priced off the fallback
    'old' band no matter what the CIM said the year built was.

    Reads the source rather than driving the CLI, which wants a PDF and a
    comp DB. What it pins is the wiring: an anchor resolved once, and no
    consumer left reading the default.
    """
    import inspect

    import run as cli

    src = inspect.getsource(cli.stage_valuate)
    assert "resolve_market_cap(" in src
    # build_returns_model, run_value_add_scenarios, solve_max_price,
    # solve_max_price_value_add and solve_max_price_levered. An exact
    # count so a sixth consumer added without the anchor fails here
    # rather than in a published memo. This assertion has now caught what
    # it was built to catch once: solve_max_price_levered joined
    # stage_valuate when the CLI gained its levered payload, and the
    # count going 4 → 5 is the tripwire firing correctly, not breaking.
    assert src.count("market_cap=ctx.market_cap") == 5


def test_both_orchestrations_name_the_same_consumers():
    """engine.run_analysis and run.stage_valuate must hand the anchor to
    the same set of engines. A consumer added to one and not the other is
    how the web app and the CLI would start pricing a deal differently."""
    import inspect

    import engine
    import run as cli

    engine_src = inspect.getsource(engine.run_analysis)
    cli_src = inspect.getsource(cli.stage_valuate)
    for consumer in ("build_returns_model", "run_value_add_scenarios",
                     "solve_max_price", "solve_max_price_levered"):
        assert consumer in engine_src, consumer
        assert consumer in cli_src, consumer
    # and neither leaves a solver to fall back to the default anchor
    assert "market_cap" in engine_src and "market_cap" in cli_src


# ── 9. Provenance survives the trip through the engine ───────────────

def test_a_resolved_anchor_survives_the_engine_with_its_source(
        mock_cim_data, tmp_path, monkeypatch):
    """REGRESSION (review finding, PR #31) — do not delete.

    webapp.services must resolve the anchor BEFORE taking the analysis
    lock, off the pristine table, so it hands the engine an already
    resolved dict. It used to hand over just the RATE, which re-entered
    `resolve_market_cap`'s "an explicit market_cap always wins" branch —
    that branch cannot tell a typed rate from a resolved one, so every web
    run was stamped `source: "analyst"`.

    The damage was not cosmetic: `_market_exit_cap` gates its
    unknown-vintage finding on `source == "table"`, so the finding could
    never fire on the primary interface — silently passing the exact case
    the check was built to catch. Nothing caught it because the web tests
    monkeypatch `run_analysis` out, so this drives the real one.
    """
    monkeypatch.setattr("data.comp_db.COMP_DB_PATH", str(tmp_path / "c.db"))
    from engine import AnalysisResult, run_analysis

    mock_cim_data.year_built = None                 # unknown-vintage case
    resolved = resolve_market_cap("Self Storage", None)
    assert resolved["source"] == "table" and resolved["age_band_known"] is False

    result = AnalysisResult(pdf_path=str(tmp_path / "none.pdf"))
    result.cim_data = mock_cim_data
    run_analysis(result, output_dir=str(tmp_path), market_cap=resolved)

    assert result.market_cap["source"] == "table"
    assert result.market_cap["age_band_known"] is False
    # and the check register can therefore still raise the finding
    finding = next(c for c in result.checks if c["id"] == "market_exit_cap")
    assert finding["status"] == C.FAIL
    assert "year built is unknown" in finding["message"]


def test_the_rate_only_path_is_still_an_analyst_override(
        mock_cim_data, tmp_path, monkeypatch):
    """The other half of the seam: `market_cap_rate` MEANS "the analyst
    typed this". That is why handing it a resolved table rate was wrong,
    and why the dict parameter exists."""
    monkeypatch.setattr("data.comp_db.COMP_DB_PATH", str(tmp_path / "c.db"))
    from engine import AnalysisResult, run_analysis

    result = AnalysisResult(pdf_path=str(tmp_path / "none.pdf"))
    result.cim_data = mock_cim_data
    run_analysis(result, output_dir=str(tmp_path), market_cap_rate=0.0499)

    assert result.market_cap["market_cap"] == 0.0499
    assert result.market_cap["source"] == "analyst"


def test_the_web_worker_hands_the_engine_the_resolved_dict():
    """The seam above, pinned at the call site: webapp.services resolves
    once and passes `market_cap=`, never `market_cap_rate=<resolved>`."""
    import inspect

    from webapp import services

    src = inspect.getsource(services._analysis_worker)
    assert "market_cap=market_cap," in src
    assert "market_cap_rate=" not in src


def test_an_analyst_override_says_what_it_overrode():
    """`as_of` dates the TABLE. Printed unconditionally it claimed a table
    vintage for a number with no table basis."""
    from analysis.valuation import describe_market_cap

    table = resolve_market_cap("Self Storage", 2015,
                               as_of=datetime.date(2026, 1, 1))
    assert describe_market_cap(table) == f"table as of {cfg.MARKET_CAP_AS_OF}"

    typed = resolve_market_cap("Self Storage", 2015, market_cap=0.0501,
                               as_of=datetime.date(2026, 1, 1))
    txt = describe_market_cap(typed)
    assert txt.startswith("analyst-entered")
    assert "overriding" in txt
    # the table rate it displaced is named, and the as-of is attached to
    # THAT rather than presented as the applied rate's vintage
    assert "6.250%" in txt
    assert "table rate as of" in txt

    # no table cell to compare against → no invented provenance
    bare = resolve_market_cap(None, None, market_cap=0.06,
                              base={"Self Storage": {}})
    assert describe_market_cap(bare) == "analyst-entered"


def test_the_va_solver_derives_its_cap_from_the_scenario_it_is_solving():
    """`compute_va_irr_at_price` hardcoded `name=ScenarioType.BASE` while
    the caller picked `params` by its own scenario argument. Once `name`
    started driving the exit-cap spread and drift — and it already drove
    the exit ≥ entry coercion, which bull is exempt from — solving for
    bull would have used base's cap and base's floor."""
    from model.value_add_model import compute_va_irr_at_price

    mc = resolve_market_cap(market_cap=ANCHOR)
    kw = dict(cim_data=_FakeCIM(), financial_analysis=VA_FIN,
              price=8_000_000, capex=0, market_cap=mc)
    bull = compute_va_irr_at_price(
        params=cfg.VALUE_ADD_SCENARIOS[ScenarioType.BULL],
        scenario=ScenarioType.BULL, **kw)
    base = compute_va_irr_at_price(
        params=cfg.VALUE_ADD_SCENARIOS[ScenarioType.BULL],
        scenario=ScenarioType.BASE, **kw)
    # Same params, different scenario label → different cap → different
    # IRR. Bull's cap is 112.5 bp tighter, so it must price higher.
    assert bull is not None and base is not None
    assert bull > base


def test_market_exit_cap_is_advisory_not_blocking():
    """Blocking is reserved for identities the pipeline computes on
    itself (see `_sources_uses_ties`). An anchor is an input."""
    spec = next(s for s in C.CHECKS if s.id == "market_exit_cap")
    assert spec.severity == C.ADVISORY
    assert "market_exit_cap" in C.CHECK_IDS
