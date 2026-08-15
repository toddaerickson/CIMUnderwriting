"""Deal model + import command + list view tests."""
import datetime
import json

import pytest


@pytest.mark.django_db
def test_deal_str_and_defaults():
    from webapp.models import Deal

    d = Deal.objects.create(deal_id="test-storage", property_name="Test Storage")
    assert str(d) == "Test Storage"
    assert d.recommendation == "N/A"
    assert d.input_files == []


@pytest.mark.django_db
def test_import_deals_idempotent(tmp_path, settings):
    from django.core.management import call_command
    from webapp.models import Deal

    meta = {
        "deal_id": "expo-storage", "property_name": "Expo Storage",
        "city": "Belton", "state": "TX", "asset_type": "Boat & RV",
        "nrsf": 45000.0, "acreage": 5.2, "asking_price": 3_500_000,
        "estimated_fair_value": 3_100_000, "recommendation": "PURSUE",
        "analysis_date": "2026-07-01", "memo_path": "memo.docx",
        "excel_path": "model.xlsx", "input_files": ["om.pdf"],
    }
    folder = tmp_path / "expo-storage"
    folder.mkdir()
    (folder / "deal_meta.json").write_text(json.dumps(meta))
    settings.CIM_DEALS_DIR = str(tmp_path)

    call_command("import_deals")
    call_command("import_deals")  # idempotent

    assert Deal.objects.count() == 1
    d = Deal.objects.get(deal_id="expo-storage")
    assert d.state == "TX"
    assert d.analysis_date.isoformat() == "2026-07-01"
    assert d.deal_dir == str(folder)


@pytest.mark.django_db
def test_import_deals_skips_malformed_folder(tmp_path, settings):
    from django.core.management import call_command
    from webapp.models import Deal

    bad_meta = {
        "deal_id": "aaa-bad", "property_name": "Bad Storage",
        "nrsf": [1, 2, 3],  # non-numeric FloatField value -> TypeError on save
    }
    good_meta = {
        "deal_id": "zzz-good", "property_name": "Good Storage",
        "city": "Waco", "state": "TX", "asset_type": "Self Storage",
        "nrsf": 30000.0, "acreage": 3.0, "asking_price": 2_000_000,
        "estimated_fair_value": 1_900_000, "recommendation": "PASS",
        "analysis_date": "2026-07-05", "memo_path": "memo.docx",
        "excel_path": "model.xlsx", "input_files": ["om.pdf"],
    }
    bad_folder = tmp_path / "aaa-bad"
    bad_folder.mkdir()
    (bad_folder / "deal_meta.json").write_text(json.dumps(bad_meta))
    good_folder = tmp_path / "zzz-good"
    good_folder.mkdir()
    (good_folder / "deal_meta.json").write_text(json.dumps(good_meta))
    settings.CIM_DEALS_DIR = str(tmp_path)

    call_command("import_deals")  # must not raise

    assert Deal.objects.count() == 1
    d = Deal.objects.get()
    assert d.deal_id == "zzz-good"


@pytest.mark.django_db
def test_deal_list_filters(client, django_user_model):
    from webapp.models import Deal

    user = django_user_model.objects.create_user(username="op", password="x")
    client.force_login(user)
    Deal.objects.create(deal_id="alpha", property_name="Alpha Storage",
                        state="TX", recommendation="PURSUE")
    Deal.objects.create(deal_id="bravo", property_name="Bravo Storage",
                        state="CO", recommendation="DECLINE")

    resp = client.get("/deals/?state=TX")
    assert resp.status_code == 200
    assert b"Alpha Storage" in resp.content
    assert b"Bravo Storage" not in resp.content


@pytest.mark.django_db
def test_deal_ordering_null_dates_sort_last():
    from webapp.models import Deal

    dated = Deal.objects.create(deal_id="dated", property_name="Dated",
                                analysis_date=datetime.date(2026, 1, 1))
    undated = Deal.objects.create(deal_id="undated", property_name="Undated")
    ids = [d.pk for d in Deal.objects.all()]
    assert ids == [dated.pk, undated.pk]
    # SQLite already sorts NULLs last under DESC, so the behavioral
    # assertion above cannot catch a reversion of nulls_last=True — the
    # Postgres-only bug this ordering exists to fix (review finding).
    # Pin the compiled SQL: dropping the F(...nulls_last=True) ordering
    # removes the modifier. Backend behavior is enforced in the PG smoke.
    assert "NULLS LAST" in str(Deal.objects.all().query).upper()


@pytest.mark.django_db
def test_deal_id_supports_200_chars():
    from webapp.models import Deal

    # SQLite ignores declared VARCHAR widths and create() skips
    # full_clean(), so the insert below passes at any max_length (review
    # finding). Pin the declared width so a revert to 120 fails here;
    # storage-layer enforcement is the PG smoke's length-boundaries step.
    assert Deal._meta.get_field("deal_id").max_length == 200
    slug = "s" * 200
    Deal.objects.create(deal_id=slug, property_name="Long")
    assert Deal.objects.filter(deal_id=slug).exists()


@pytest.mark.django_db
def test_import_deals_normalizes_state_and_skips_oversize_ids(tmp_path, settings):
    import json as jsonlib

    from django.core.management import call_command

    from webapp.models import Deal

    ok = tmp_path / "ok"
    ok.mkdir()
    (ok / "deal_meta.json").write_text(jsonlib.dumps(
        {"deal_id": "ok", "property_name": "OK", "state": "texas"}))
    huge = tmp_path / "huge"
    huge.mkdir()
    (huge / "deal_meta.json").write_text(jsonlib.dumps(
        {"deal_id": "x" * 250, "property_name": "Huge"}))
    settings.CIM_DEALS_DIR = str(tmp_path)

    call_command("import_deals")
    # "texas" is NOT truncated to the fabricated code "TE" — a non-2-letter
    # state imports as blank (visible gap) with a warning (review finding)
    assert Deal.objects.get(deal_id="ok").state == ""
    assert Deal.objects.count() == 1                       # oversize skipped


@pytest.mark.django_db
def test_build_deal_meta_import_deals_round_trip_no_drift(tmp_path, settings):
    """Every key build_deal_meta emits must be either imported onto the
    Deal row or on the explicit exempt list. A new meta key that isn't
    classified fails this test — that's the point."""
    import json as jsonlib

    from django.core.management import call_command

    from engine import AnalysisResult
    from tests.test_web_runs import _sample_cim
    from webapp.services import build_deal_meta
    from webapp.models import Deal

    cim = _sample_cim()
    result = AnalysisResult(pdf_path="x.pdf")
    result.cim_data = cim
    result.memo_path = "/tmp/Expo_memo.docx"
    result.excel_path = "/tmp/Expo_model.xlsx"
    result.max_offer = {"max_price": 3_100_000.0}
    result.gate_summary = {"recommendation": "PURSUE"}
    folder = tmp_path / "expo"
    folder.mkdir()
    meta = build_deal_meta(cim, result, str(folder), input_files=["expo.pdf"])
    meta["deal_id"] = "expo"
    (folder / "deal_meta.json").write_text(jsonlib.dumps(meta, default=str))
    settings.CIM_DEALS_DIR = str(tmp_path)

    call_command("import_deals")
    d = Deal.objects.get(deal_id="expo")
    IMPORTED = {"deal_id", "property_name", "city", "state", "asset_type",
                "nrsf", "acreage", "asking_price", "estimated_fair_value",
                "recommendation", "analysis_date", "memo_path", "excel_path",
                "input_files"}
    # LITERAL by design: build_deal_meta today emits exactly the keys in
    # IMPORTED (verified at plan-review time). If it ever grows a key,
    # this assertion fires and the new key must be classified — either
    # mapped in import_deals + IMPORTED, or added here as exempt.
    EXEMPT_DISPLAY_ONLY = set()
    assert d.property_name == meta["property_name"]
    assert d.asking_price == meta["asking_price"]
    assert d.memo_filename == meta["memo_path"]
    assert d.input_files == meta["input_files"]
    unclassified = set(meta) - IMPORTED - EXEMPT_DISPLAY_ONLY
    assert unclassified == set(), (
        f"build_deal_meta grew keys import_deals doesn't map: {unclassified}")


# ── Revenue − Expenses = NOI identity (AssumptionsForm.clean) ──────────

def _income_form(**vals):
    from webapp.forms import AssumptionsForm
    return AssumptionsForm(data={k: str(v) for k, v in vals.items()})


def test_noi_identity_consistent_triple_passes():
    form = _income_form(ttm_total_revenue=560_000, ttm_total_expenses=220_000,
                        ttm_noi=340_000)
    assert form.is_valid(), form.errors
    assert form.show_noi_accept is False


def test_noi_identity_blocks_beyond_tolerance():
    form = _income_form(ttm_total_revenue=560_000, ttm_total_expenses=220_000,
                        ttm_noi=400_000)
    assert not form.is_valid()
    err = str(form.non_field_errors())
    assert "off by $60,000" in err
    assert form.show_noi_accept is True


def test_noi_identity_within_tolerance_passes():
    # delta $500 < tolerance max($1k, 1% × $560k = $5.6k)
    form = _income_form(ttm_total_revenue=560_000, ttm_total_expenses=220_000,
                        ttm_noi=339_500)
    assert form.is_valid(), form.errors


@pytest.mark.django_db
def test_noi_identity_accept_records_discrepancy():
    from django.http import QueryDict
    from webapp.forms import build_overrides
    from webapp.models import Deal

    form = _income_form(ttm_total_revenue=560_000, ttm_total_expenses=220_000,
                        ttm_noi=400_000, accept_noi_discrepancy="on")
    assert form.is_valid(), form.errors
    deal = Deal.objects.create(deal_id="recon", property_name="Recon",
                               cim_json={"ttm_noi": 340_000.0})
    out = build_overrides(form.cleaned_data, QueryDict(""), deal)
    assert out["noi_reconciliation"] == {"accepted": True, "delta": -60_000.0}
    # the stated (accepted) NOI still overrides the snapshot value
    assert out["cim_overrides"]["ttm_noi"] == 400_000.0


@pytest.mark.django_db
def test_noi_identity_accept_within_tolerance_not_recorded():
    from django.http import QueryDict
    from webapp.forms import build_overrides
    from webapp.models import Deal

    form = _income_form(ttm_total_revenue=560_000, ttm_total_expenses=220_000,
                        ttm_noi=340_000, accept_noi_discrepancy="on")
    assert form.is_valid(), form.errors
    deal = Deal.objects.create(deal_id="recon2", property_name="Recon2",
                               cim_json={})
    out = build_overrides(form.cleaned_data, QueryDict(""), deal)
    assert "noi_reconciliation" not in out


def test_noi_identity_derives_missing_third():
    form = _income_form(ttm_total_revenue=560_000, ttm_total_expenses=220_000)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["ttm_noi"] == 340_000.0

    form = _income_form(ttm_total_expenses=220_000, ttm_noi=340_000)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["ttm_total_revenue"] == 560_000.0

    form = _income_form(ttm_total_revenue=560_000, ttm_noi=340_000)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["ttm_total_expenses"] == 220_000.0


def test_noi_identity_derived_negative_expenses_blocks():
    form = _income_form(ttm_total_revenue=300_000, ttm_noi=400_000)
    assert not form.is_valid()
    assert "negative" in str(form.non_field_errors())


# ── The rest of the blocking register (analysis/checks.py) ────────────
# The identity check above is one entry in the register now; these are the
# other two blocking entries, which reach the form through the same path.

def test_form_blocks_when_economic_occupancy_exceeds_physical():
    form = _income_form(physical_occupancy=80, economic_occupancy=92)
    assert not form.is_valid()
    assert "exceeds physical" in str(form.non_field_errors())
    assert form.show_noi_accept is True
    assert [f.id for f in form.blocking_findings] == ["occupancy_sanity"]


def test_form_blocks_when_egr_exceeds_gpr():
    form = _income_form(ttm_gpr=720_000, ttm_egr=800_000)
    assert not form.is_valid()
    assert "cannot be larger" in str(form.non_field_errors())
    assert [f.id for f in form.blocking_findings] == ["egr_le_gpr"]


def test_form_reports_every_blocking_finding_at_once():
    form = _income_form(ttm_total_revenue=560_000, ttm_total_expenses=220_000,
                        ttm_noi=400_000, ttm_gpr=720_000, ttm_egr=800_000,
                        physical_occupancy=80, economic_occupancy=92)
    assert not form.is_valid()
    assert {f.id for f in form.blocking_findings} == {
        "income_identity", "occupancy_sanity", "egr_le_gpr"}


def test_form_percent_fields_reach_the_register_as_decimals():
    """Whole-number form percents (92) must not read as 9200% occupancy."""
    form = _income_form(physical_occupancy=92, economic_occupancy=80)
    assert form.is_valid(), form.errors
    by_id = {r.id: r for r in form.check_results}
    assert by_id["occupancy_sanity"].status == "pass"
    assert by_id["occupancy_sanity"].values["physical_occupancy"] == 0.92


def test_form_carries_advisory_findings_without_blocking():
    # OpEx/Revenue of 220k/560k = 39.3% passes; 20k/560k = 3.6% does not.
    form = _income_form(ttm_total_revenue=560_000, ttm_total_expenses=20_000,
                        ttm_noi=540_000)
    assert form.is_valid(), form.errors
    flagged = [r.id for r in form.check_results if r.status == "fail"]
    assert "opex_ratio_band" in flagged
    assert form.blocking_findings == []


@pytest.mark.django_db
def test_accepting_records_every_waived_finding():
    from django.http import QueryDict
    from webapp.forms import build_overrides
    from webapp.models import Deal

    form = _income_form(ttm_total_revenue=560_000, ttm_total_expenses=220_000,
                        ttm_noi=400_000, physical_occupancy=80,
                        economic_occupancy=92, accept_noi_discrepancy="on")
    assert form.is_valid(), form.errors
    deal = Deal.objects.create(deal_id="acc", property_name="Acc", cim_json={})
    out = build_overrides(form.cleaned_data, QueryDict(""), deal)

    assert {c["id"] for c in out["accepted_checks"]} == {"income_identity",
                                                         "occupancy_sanity"}
    assert "off by $60,000" in out["accepted_checks"][0]["message"]
    # the identity's own audit record is unchanged
    assert out["noi_reconciliation"] == {"accepted": True, "delta": -60_000.0}


@pytest.mark.django_db
def test_nothing_is_recorded_when_there_was_nothing_to_accept():
    from django.http import QueryDict
    from webapp.forms import build_overrides
    from webapp.models import Deal

    form = _income_form(ttm_total_revenue=560_000, ttm_total_expenses=220_000,
                        ttm_noi=340_000, accept_noi_discrepancy="on")
    assert form.is_valid(), form.errors
    deal = Deal.objects.create(deal_id="acc2", property_name="Acc2",
                               cim_json={})
    out = build_overrides(form.cleaned_data, QueryDict(""), deal)
    assert "accepted_checks" not in out


@pytest.mark.django_db
def test_market_verification_roundtrip_and_old_snapshots():
    """New ChoiceField survives the save/initial plumbing; snapshots
    written before the field existed resolve to None, not a crash."""
    from django.http import QueryDict
    from webapp.forms import AssumptionsForm, build_initial, build_overrides
    from webapp.models import Deal

    deal = Deal.objects.create(deal_id="mv", property_name="MV",
                               cim_json={"property_name": "MV"})  # pre-field snapshot
    assert build_initial(deal)["market_verification"] is None

    form = AssumptionsForm(data={"market_verification": "top_50",
                                 "msa": "Abilene, TX"})
    assert form.is_valid(), form.errors
    out = build_overrides(form.cleaned_data, QueryDict(""), deal)
    assert out["cim_overrides"]["market_verification"] == "top_50"
    # the verification is bound to the location it certified
    assert out["cim_overrides"]["market_verified_location"] == "Abilene, TX"

    assert not AssumptionsForm(data={"market_verification": "bogus"}).is_valid()


@pytest.mark.django_db
def test_msa_edit_alone_does_not_restamp_verification_location():
    """Adversary re-review reproduction: the prefilled verification rides
    along on every save (snapshot never contains it) — an msa-only edit
    must carry the OLD stamp forward, not re-bless the new location."""
    from django.http import QueryDict
    from webapp.forms import AssumptionsForm, build_overrides
    from webapp.models import Deal

    deal = Deal.objects.create(deal_id="stamp", property_name="S",
                               cim_json={"property_name": "S"})
    # Save 1: analyst verifies Dallas as top-50
    f1 = AssumptionsForm(data={"market_verification": "top_50",
                               "msa": "Dallas-Fort Worth, TX"})
    assert f1.is_valid(), f1.errors
    deal.assumption_overrides = build_overrides(f1.cleaned_data,
                                                QueryDict(""), deal)
    assert (deal.assumption_overrides["cim_overrides"]
            ["market_verified_location"] == "Dallas-Fort Worth, TX")
    deal.save()

    # Save 2: only the msa changes; verification rides along prefilled
    f2 = AssumptionsForm(data={"market_verification": "top_50",
                               "msa": "Abilene, TX"})
    assert f2.is_valid(), f2.errors
    out2 = build_overrides(f2.cleaned_data, QueryDict(""), deal)
    # stamp carried forward — gate 7 will flag Abilene as unverified
    assert (out2["cim_overrides"]["market_verified_location"]
            == "Dallas-Fort Worth, TX")

    # Save 3: analyst re-verifies explicitly (changed value) → re-stamp
    deal.assumption_overrides = out2
    deal.save()
    f3 = AssumptionsForm(data={"market_verification": "strong_secondary",
                               "msa": "Abilene, TX"})
    assert f3.is_valid(), f3.errors
    out3 = build_overrides(f3.cleaned_data, QueryDict(""), deal)
    assert out3["cim_overrides"]["market_verified_location"] == "Abilene, TX"


@pytest.mark.django_db
def test_new_driver_fields_roundtrip_and_old_snapshots():
    """Rate/momentum drivers survive save/initial plumbing; snapshots
    written before the fields existed resolve to None."""
    from django.http import QueryDict
    from webapp.forms import AssumptionsForm, build_initial, build_overrides
    from webapp.models import Deal

    deal = Deal.objects.create(deal_id="drv", property_name="D",
                               cim_json={"property_name": "D"})
    init = build_initial(deal)
    assert init["in_place_avg_rent_psf"] is None
    assert init["street_rate_trend"] is None
    assert init["t3_annualized_revenue"] is None

    form = AssumptionsForm(data={"in_place_avg_rent_psf": "1.15",
                                 "street_rate_trend": "falling",
                                 "t3_annualized_revenue": "540000"})
    assert form.is_valid(), form.errors
    out = build_overrides(form.cleaned_data, QueryDict(""), deal)
    assert out["cim_overrides"]["in_place_avg_rent_psf"] == 1.15
    assert out["cim_overrides"]["street_rate_trend"] == "falling"
    assert out["cim_overrides"]["t3_annualized_revenue"] == 540000.0

    assert not AssumptionsForm(data={"street_rate_trend": "sideways"}).is_valid()


@pytest.mark.django_db
def test_expense_line_overrides_roundtrip():
    from django.http import QueryDict
    from webapp.forms import AssumptionsForm, build_initial, build_overrides
    from webapp.models import Deal

    deal = Deal.objects.create(deal_id="exp", property_name="E",
                               cim_json={"property_name": "E"})
    form = AssumptionsForm(data={"exp_property_tax": "55405",
                                 "exp_payroll": "12600"})
    assert form.is_valid(), form.errors
    out = build_overrides(form.cleaned_data, QueryDict(""), deal)
    assert out["expense_line_overrides"] == {"property_tax": 55405.0,
                                             "payroll": 12600.0}
    # blanks mean no override; empty dict key entirely absent
    f2 = AssumptionsForm(data={})
    assert f2.is_valid()
    assert "expense_line_overrides" not in build_overrides(
        f2.cleaned_data, QueryDict(""), deal)
    # negative rejected by field validation
    assert not AssumptionsForm(data={"exp_insurance": "-5"}).is_valid()
    # saved values round-trip into initial
    deal.assumption_overrides = out
    deal.save()
    assert build_initial(deal)["exp_property_tax"] == 55405.0


@pytest.mark.django_db
def test_model_rows_extracted_and_source_columns():
    """model_rows()/_display_value() had zero direct tests — they ARE the
    auditability trace surface (CLAUDE.md: every value a user sees should
    be traceable to its formula + source + raw inputs), rendered on both
    the Drivers and Demographics sections of the assumptions page.
    Verified against two mutants in a scratch copy, each 203/203 with
    every other test green: (a) deleting the CIM_PCT_FIELDS decimal->
    whole-number conversion before the snap/current comparison — makes
    every untouched percent driver misreport 'source': 'you' on first
    load; (b) reducing _display_value to `return v` — breaks the comma-
    grouped Extracted column."""
    from webapp.forms import (SECTION_DRIVERS, AssumptionsForm, build_initial,
                              model_rows)
    from webapp.models import Deal

    deal = Deal.objects.create(
        deal_id="mr", property_name="MR",
        cim_json={"property_name": "MR", "physical_occupancy": 0.92,
                  "asking_price": 3_500_000.0})
    form = AssumptionsForm(initial=build_initial(deal))
    rows = {r["label"]: r for r in
            model_rows(form, SECTION_DRIVERS, deal.cim_json, {})}
    assert rows["Physical Occupancy (%)"]["extracted"] == "92"
    assert rows["Physical Occupancy (%)"]["source"] == "CIM"
    assert rows["Asking Price ($)"]["extracted"] == "3,500,000"


# ── Capital structure + CapEx basis (items D / H) ──────────────────────

def _capital_form(**vals):
    from webapp.forms import AssumptionsForm
    return AssumptionsForm(data={k: str(v) for k, v in vals.items()})


@pytest.mark.django_db
def test_capital_structure_saves_deltas_only():
    from django.http import QueryDict
    from webapp.forms import build_overrides
    from webapp.models import Deal
    import config as cfg

    deal = Deal.objects.create(deal_id="cap", property_name="Cap", cim_json={})
    at_defaults = _capital_form(
        capex_basis=cfg.DEFAULT_CAPEX_BASIS,
        operating_reserve=cfg.DEFAULT_OPERATING_RESERVE,
        operating_reserve_basis=cfg.DEFAULT_OPERATING_RESERVE_BASIS,
        gp_coinvest_pct=cfg.GP_COINVEST_PCT * 100)
    assert at_defaults.is_valid(), at_defaults.errors
    assert "capital_structure" not in build_overrides(
        at_defaults.cleaned_data, QueryDict(""), deal)

    changed = _capital_form(nrsf=50_000, operating_reserve=1.50,
                            operating_reserve_basis="per_sf",
                            gp_coinvest_pct=20)
    assert changed.is_valid(), changed.errors
    out = build_overrides(changed.cleaned_data, QueryDict(""), deal)
    assert out["capital_structure"] == {"operating_reserve": 1.50,
                                        "operating_reserve_basis": "per_sf",
                                        "gp_coinvest_pct": 0.20}


@pytest.mark.django_db
def test_pct_of_price_capex_round_trips_through_decimal_storage():
    """The one field whose units depend on another field: the form posts a
    whole-number percent, storage is a decimal like every other percent
    here, and redisplay must land back on the number that was typed."""
    from django.http import QueryDict
    from webapp.forms import build_initial, build_overrides
    from webapp.models import Deal

    deal = Deal.objects.create(deal_id="capex-pct", property_name="CapexPct",
                               cim_json={"asking_price": 5_000_000.0})
    form = _capital_form(asking_price=5_000_000, capex_estimate=2,
                         capex_basis="pct_price")
    assert form.is_valid(), form.errors
    out = build_overrides(form.cleaned_data, QueryDict(""), deal)
    assert out["cim_overrides"]["capex_estimate"] == 0.02
    assert out["capital_structure"]["capex_basis"] == "pct_price"

    deal.assumption_overrides = out
    deal.save()
    assert build_initial(deal)["capex_estimate"] == 2.0


def test_a_rate_without_its_denominator_is_rejected():
    """resolve_capital_amount returns $0 for a rate with no driver rather
    than inventing a magnitude — useless as feedback to the person typing
    it, so the form refuses the combination while they are still here."""
    form = _capital_form(capex_estimate=0.50, capex_basis="per_sf")
    assert not form.is_valid()
    assert "NRSF is blank" in str(form.non_field_errors())
    assert form.cleaned_data["capex_basis"] == "per_sf"   # not detached

    ok = _capital_form(capex_estimate=0.50, capex_basis="per_sf", nrsf=50_000)
    assert ok.is_valid(), ok.errors


def test_a_rate_basis_hides_the_extracted_dollar_figure():
    """The snapshot holds the CIM's DOLLARS; the input now holds a RATE.
    Printing them side by side invites a comparison between two units."""
    from webapp.forms import SECTION_DRIVERS, AssumptionsForm, model_rows

    snapshot = {"capex_estimate": 50_000.0, "asking_price": 5_000_000.0}
    for basis, expected in (("amount", "50,000"), ("per_sf", None)):
        form = AssumptionsForm(initial={"capex_basis": basis})
        rows = {r["label"]: r for r in model_rows(
            form, SECTION_DRIVERS, snapshot, {},
            extras={"capex_estimate": form["capex_basis"]})}
        row = rows["CapEx Estimate"]
        assert row["extracted"] == expected
        assert row["extra_bf"] is not None


def test_changing_a_unit_costs_one_confirmation():
    """A "2" typed under "% of price" becomes $2 of CapEx under "$ total"
    — silently removing real capital from the basis and overstating every
    return. There is no JavaScript on the page to re-key the field, so the
    first save after a unit change is refused (review finding)."""
    form = _capital_form(capex_estimate=2, capex_basis="amount",
                         capex_unit_stamp="pct_price")
    assert not form.is_valid()
    assert "will now be read as $ total" in str(form.non_field_errors())
    # The basis must SURVIVE the error. add_error(field, ...) would delete
    # it from cleaned_data, and the live preview — which proceeds on an
    # invalid form by design — would then read the OLD basis back out of
    # build_overrides' default (re-review finding).
    assert form.cleaned_data["capex_basis"] == "amount"

    # The template stamps the CURRENT selection, so the restated save goes
    # through — the refusal is one round trip, not a trap.
    assert _capital_form(capex_estimate=100_000, capex_basis="amount",
                         capex_unit_stamp="amount").is_valid()


def test_an_unchanged_basis_is_never_refused():
    for basis, stamp in (("amount", "amount"), ("per_sf", "per_sf")):
        form = _capital_form(nrsf=50_000, capex_estimate=0.50,
                             capex_basis=basis, capex_unit_stamp=stamp)
        assert form.is_valid(), form.errors


def test_the_reserve_carries_the_same_unit_guard():
    form = _capital_form(nrsf=50_000, operating_reserve=75_000,
                         operating_reserve_basis="per_sf",
                         reserve_unit_stamp="amount")
    assert not form.is_valid()
    assert "operating reserve" in str(form.non_field_errors())
    assert form.cleaned_data["operating_reserve_basis"] == "per_sf"



@pytest.mark.django_db
def test_the_live_preview_keeps_the_new_basis_while_a_confirmation_is_open():
    """assumptions_preview proceeds on an invalid form by design. If the
    basis error detached capex_basis from cleaned_data, build_overrides
    would silently fall back to the config default and the preview would
    compute for the OLD unit — during exactly the interaction the guard
    exists to cover (re-review finding)."""
    from django.http import QueryDict
    from webapp.forms import AssumptionsForm, build_overrides
    from webapp.models import Deal

    deal = Deal.objects.create(deal_id="prev", property_name="Prev",
                               cim_json={"nrsf": 50_000.0})
    post = QueryDict("nrsf=50000&capex_estimate=0.75&capex_basis=per_sf"
                     "&capex_unit_stamp=amount")
    form = AssumptionsForm(post)
    assert not form.is_valid()          # confirmation is outstanding
    out = build_overrides(form.cleaned_data, post, deal)
    assert out["capital_structure"]["capex_basis"] == "per_sf"


# ── The pipeline table's own copy (QA findings B5 / B1-a / B1-b) ────


@pytest.fixture
def _operator(client, django_user_model):
    user = django_user_model.objects.create_user(username="op2", password="x")
    client.force_login(user)
    return user


@pytest.mark.django_db
def test_an_empty_pipeline_and_an_empty_FILTER_say_different_things(
        client, _operator):
    """One cell used to state one fact for two situations. Filtering to
    nothing told the operator their pipeline was empty when it was not,
    and offered a fix — start a New Analysis — for a problem they did
    not have."""
    from webapp.models import Deal

    truly_empty = client.get("/deals/").content.decode()
    assert "No deals yet" in truly_empty

    Deal.objects.create(deal_id="alpha", property_name="Alpha Storage",
                        state="TX", recommendation="PURSUE")
    filtered = client.get("/deals/?state=CO").content.decode()
    assert "No deals match these filters" in filtered
    assert "No deals yet" not in filtered
    # ...and the offered action is the one that actually helps here.
    assert "clear them" in filtered


@pytest.mark.django_db
def test_the_empty_state_stops_leaking_a_shell_command(client, _operator):
    """MUTATION: put `manage.py import_deals` back in either branch.

    It is not an action available to anyone reading the page in a
    browser, and it names a one-time bootstrap that has already run on
    this deployment — so it read as an instruction the operator could
    not follow."""
    from webapp.models import Deal

    assert "manage.py" not in client.get("/deals/").content.decode()
    Deal.objects.create(deal_id="alpha", property_name="Alpha", state="TX")
    assert "manage.py" not in client.get(
        "/deals/?state=CO").content.decode()


@pytest.mark.django_db
def test_the_table_separates_thousands(client, _operator):
    """A QA pass found `48762` and `$3500000` on the same page as
    `$58,051,289`, which came out of `webapp.results` already formatted.
    These three columns are the ones that reach the page raw."""
    from webapp.models import Deal

    Deal.objects.create(deal_id="alpha", property_name="Alpha Storage",
                        nrsf=48_762.0, asking_price=3_500_000.0,
                        estimated_fair_value=2_950_000.0)
    body = client.get("/deals/").content.decode()
    assert "48,762" in body
    assert "$3,500,000" in body
    assert "$2,950,000" in body
    assert "48762" not in body
    assert "3500000" not in body


@pytest.mark.django_db
def test_a_missing_figure_shows_a_dash_not_a_void(client, _operator):
    """A blank cell beside populated ones reads as a broken template —
    a browser QA pass filed exactly that against a deal whose stored run
    never wrote the field. The dash says "no value", which is true."""
    from webapp.models import Deal

    Deal.objects.create(deal_id="alpha", property_name="Alpha Storage",
                        recommendation="PURSUE")
    body = client.get("/deals/").content.decode()
    assert body.count("&mdash;") >= 3      # NRSF, Asking, Est. Fair Value


def test_every_page_heading_uses_the_display_face():
    """QA finding A5: three `<h1>`s computed to Public Sans while the
    sidebar wordmark rendered in Fraunces, so the page title read as
    lighter chrome than the nav beside it.

    A sweep rather than three assertions, and it lives here beside the
    deal-list copy tests because that is the page the defect surfaced
    on. `base.html` loads both faces and `tailwind.config.js` defines
    both tokens; nothing enforced that a heading reached for the display
    one, so each new page inherited the miss from whichever template it
    was copied off. A new template now fails this by default."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    bare = []
    for tpl in (root / "webapp" / "templates").rglob("*.html"):
        for tag in re.findall(r"<h1\b[^>]*>", tpl.read_text()):
            if "font-display" not in tag:
                bare.append(f"{tpl.relative_to(root)}: {tag}")
    assert not bare, "headings not on the display face: " + "; ".join(bare)
