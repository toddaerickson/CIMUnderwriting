"""Rename-plan generator + prefix-tolerant duplicate detection.

The plan generator's job is to be safe, not clever: it must never overwrite, never
rename a file it could not identify, and never lose the original name. These tests
pin those invariants plus the address-extraction cases taken from real CIM covers.
"""
import csv

import pytest

from scripts.cims_rename_plan import (
    analyse,
    build_name,
    build_plan,
    find_collisions,
    find_locations,
    load_overrides,
    render_scripts,
    _tidy_city,
)


# ── city tidying ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expect", [
    ("Industry Drive Bastrop", "Bastrop"),      # ran backwards into the street line
    ("East Pikes Peak Avenue Colorado Springs", "Colorado Springs"),
    ("State Belton", "Belton"),                 # ran backwards into a field label
    ("YORK", "York"),                           # ALL-CAPS cover
    ("DECATU R", "Decatur"),                    # letter-split by the PDF text layer
    ("McKinney", "McKinney"),                   # genuine CamelCase survives
    ("5485 Airport Hwy", ""),                   # pure street fragment -> nothing
])
def test_tidy_city(raw, expect):
    assert _tidy_city(raw) == expect


# ── address extraction ───────────────────────────────────────────────

def test_finds_address_with_no_space_before_zip():
    """Real cover text from Texoma 377 OM.pdf — the PDF sets it with no spaces."""
    hits = find_locations("63CedarMillsRd,Gordonville,TX76245")
    assert ("Gordonville", "TX", "76245") in hits


def test_finds_all_caps_address():
    hits = find_locations("5485 AIRPORT HWY. 21, MAXWELL, TX 78666")
    assert ("Maxwell", "TX", "78666") in hits


def test_spelled_out_state_maps_to_code():
    assert ("Rogers", "AR", "72756") in find_locations("Rogers, Arkansas 72756")


def test_broker_office_address_is_suppressed():
    """The disclaimer page carries the broker's own address, not the property's."""
    assert find_locations("Marcus & Millichap, Encino, CA 91436") == []


def test_broker_suppression_window_is_tight():
    """A broker named far away must not suppress a real address — a wide window
    silently blanks every file whose disclaimer page mentions brokers."""
    text = "Marcus & Millichap" + " filler" * 40 + ", Gordonville, TX 76245"
    assert ("Gordonville", "TX", "76245") in find_locations(text)


# ── naming ───────────────────────────────────────────────────────────

def test_build_name_is_additive_and_idempotent():
    r = {"old": "Storage OM.pdf", "ac": "SS", "st": "TX", "city": "Kerrville", "tags": ""}
    first = build_name(r)
    assert first == "[SS-TX-Kerrville] Storage OM.pdf"
    assert build_name({**r, "old": first}) == first     # never double-prefixes


def test_build_name_keeps_original_when_skipped():
    r = {"old": "notes.xlsx", "ac": "ZZ", "st": "ZZ", "city": "ZZ", "tags": "", "skip": 1}
    assert build_name(r) == "notes.xlsx"


def test_non_pdf_is_left_alone(tmp_path):
    p = tmp_path / "rent roll.xlsx"
    p.write_bytes(b"x")
    r = analyse(p, check_placeholders=False)
    assert r["skip"] == 1
    assert build_name(r) == "rent roll.xlsx"


def test_unreadable_pdf_abstains_to_work_queue(tmp_path):
    p = tmp_path / "scan.pdf"
    p.write_bytes(b"not really a pdf")
    r = analyse(p, check_placeholders=False)
    assert r["confidence"] == "LOW"
    assert r["reason"].startswith("ABSTAIN")
    assert build_name(r).startswith("[ZZ-ZZ-ZZ] ")


# ── collisions ───────────────────────────────────────────────────────

def test_collision_detection_is_casefolded():
    rows = [{"old": "a.pdf", "new": "[SS-TX-Kerrville] X.pdf"},
            {"old": "b.pdf", "new": "[ss-tx-kerrville] x.pdf"}]
    assert len(find_collisions(rows)) == 1


def test_skipped_rows_never_collide():
    rows = [{"old": "x.xlsx", "new": "x.xlsx", "skip": 1},
            {"old": "x.xlsx", "new": "x.xlsx", "skip": 1}]
    assert find_collisions(rows) == []


def test_apply_script_refuses_when_plan_has_collisions(tmp_path):
    rows = [{"old": "a.pdf", "new": "[X] a.pdf", "flags": "", "dup_of": ""}]
    apply_ps, _ = render_scripts(rows, tmp_path, collisions=[("a.pdf", "b.pdf", "[X] a.pdf")])
    assert "refusing to run" in apply_ps
    assert "exit 1" in apply_ps


def test_apply_and_undo_are_inverses(tmp_path):
    rows = [{"old": "a.pdf", "new": "[SS-TX-Kerrville] a.pdf", "flags": "", "dup_of": ""}]
    apply_ps, undo_ps = render_scripts(rows, tmp_path, collisions=[])
    assert "-NewName '[SS-TX-Kerrville] a.pdf'" in apply_ps
    assert "-NewName 'a.pdf'" in undo_ps
    assert "Test-Path" in apply_ps           # never overwrites, even at apply time


def test_duplicates_are_not_renamed(tmp_path):
    for name in ("one.pdf", "two.pdf"):
        (tmp_path / name).write_bytes(b"identical bytes")
    rows, _, dupes = build_plan(sorted(tmp_path.iterdir()), tmp_path, {},
                                check_placeholders=False)
    assert len(dupes) == 1
    assert all("EXACT_DUPLICATE" in r["flags"] for r in rows)
    apply_ps, _ = render_scripts(rows, tmp_path, collisions=[])
    assert "Rename-Item" not in apply_ps
    assert "SKIPPED duplicate" in apply_ps


def test_ledger_records_every_file(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"aaa")
    (tmp_path / "b.pdf").write_bytes(b"bbb")
    _, ledger, _ = build_plan(sorted(tmp_path.iterdir()), tmp_path, {},
                              check_placeholders=False)
    assert {row["name"] for row in ledger} == {"a.pdf", "b.pdf"}
    assert all(len(row["sha256"]) == 64 for row in ledger)


# ── overrides ────────────────────────────────────────────────────────

def test_overrides_come_from_file_not_source(tmp_path):
    """Per-file judgement is folder-specific data; hardcoding it into the script
    carries dead entries to the next folder it is pointed at."""
    csv_path = tmp_path / "ov.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["old", "ac", "st", "city", "reason"])
        w.writeheader()
        w.writerow({"old": "Deal Room.zip", "ac": "SS", "st": "TX",
                    "city": "Kerrville", "reason": "checked the extracted folder"})
    assert load_overrides(str(csv_path))["Deal Room.zip"]["city"] == "Kerrville"
    assert load_overrides(None) == {}


def test_override_renames_a_file_the_scan_abstained_on(tmp_path):
    p = tmp_path / "Deal Room.zip"
    p.write_bytes(b"not a real zip")
    overrides = {"Deal Room.zip": {"ac": "SS", "st": "TX", "city": "Kerrville",
                                   "confidence": "REVIEW"}}
    rows, _, _ = build_plan([p], tmp_path, overrides, check_placeholders=False)
    assert rows[0]["new"] == "[SS-TX-Kerrville] Deal Room.zip"


# ── refusals ─────────────────────────────────────────────────────────

def test_refuses_out_inside_src_without_creating_it(tmp_path):
    """The ledger is the undo source; it cannot live in the folder being renamed.
    Rejecting must also not leave a stray directory inside the synced folder."""
    from scripts.cims_rename_plan import main

    src = tmp_path / "CIMs"
    src.mkdir()
    (src / "a.pdf").write_bytes(b"x")
    out = src / "plan"
    with pytest.raises(SystemExit) as e:
        main(["--src", str(src), "--out", str(out), "--allow-non-windows"])
    assert "REFUSING" in str(e.value)
    assert not out.exists()


def test_refuses_when_placeholder_detection_unavailable(tmp_path, monkeypatch):
    """Off-Windows the check fails open, so an unguarded run would read every
    file and hydrate a cloud-synced folder in full."""
    from scripts import cims_rename_plan as mod

    monkeypatch.setattr(mod, "placeholder_detection_available", lambda: False)
    src = tmp_path / "CIMs"
    src.mkdir()
    (src / "a.pdf").write_bytes(b"x")
    with pytest.raises(SystemExit) as e:
        mod.main(["--src", str(src), "--out", str(tmp_path / "out")])
    assert "hydrate" in str(e.value)


# ── app-side: a renamed CIM still matches its earlier ingest ─────────

def test_strip_cim_prefix():
    from webapp.services import strip_cim_prefix

    assert strip_cim_prefix("[SS-TX-Kerrville] om.pdf") == "om.pdf"
    assert strip_cim_prefix("om.pdf") == "om.pdf"
    assert strip_cim_prefix("[not a prefix].pdf") == "[not a prefix].pdf"
    assert strip_cim_prefix("") == ""


@pytest.mark.django_db
def test_renamed_upload_still_matches_existing_deal():
    """Without prefix-stripping the dupe check goes quiet and a second deal
    folder is created for a CIM already in the system."""
    from webapp.models import Deal
    from webapp.services import find_upload_duplicates

    Deal.objects.create(deal_id="expo", property_name="Expo Storage",
                        input_files=["expo om.pdf"])
    hits = find_upload_duplicates("[BRV-TX-Belton] expo om.pdf")
    assert any(h["match_type"] == "deal_folder" for h in hits)


@pytest.mark.django_db
def test_bare_upload_still_matches_a_prefixed_deal():
    """And the reverse direction, for deals ingested after the rename."""
    from webapp.models import Deal
    from webapp.services import find_upload_duplicates

    Deal.objects.create(deal_id="expo", property_name="Expo Storage",
                        input_files=["[BRV-TX-Belton] expo om.pdf"])
    hits = find_upload_duplicates("expo om.pdf")
    assert any(h["match_type"] == "deal_folder" for h in hits)


@pytest.mark.django_db
def test_unrelated_upload_is_not_a_duplicate():
    from webapp.models import Deal
    from webapp.services import find_upload_duplicates

    Deal.objects.create(deal_id="expo", property_name="Expo Storage",
                        input_files=["expo om.pdf"])
    assert find_upload_duplicates("somewhere else om.pdf") == []
