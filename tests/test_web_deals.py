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
