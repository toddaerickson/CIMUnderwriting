"""Rename-plan generator + prefix-tolerant duplicate detection.

The plan generator's job is to be safe, not clever: it must never overwrite, never
rename a file it could not identify, and never lose the original name. These tests
pin those invariants. The address extraction it relies on lives in
extract/location.py and is covered by tests/test_parser_location.py.
"""
import csv

import pytest

from scripts.cims_rename_plan import (
    analyse,
    build_name,
    build_plan,
    find_collisions,
    load_overrides,
    render_scripts,
)


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


def test_two_left_alone_files_never_collide():
    """Files the plan does not touch already coexist on disk."""
    rows = [{"old": "a.xlsx", "new": "a.xlsx", "skip": 1},
            {"old": "b.xlsx", "new": "b.xlsx", "skip": 1}]
    assert find_collisions(rows) == []


def test_rename_onto_a_left_alone_file_is_a_collision():
    """A left-alone file still occupies its name. Missing this reports a clean
    plan and leaves apply.ps1's runtime Test-Path to hit the clash instead."""
    rows = [{"old": "[SS-TX-Kerrville] Storage OM.pdf",
             "new": "[SS-TX-Kerrville] Storage OM.pdf", "skip": 1},
            {"old": "Storage OM.pdf", "new": "[SS-TX-Kerrville] Storage OM.pdf"}]
    assert len(find_collisions(rows)) == 1


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


def test_unreadable_inner_pdf_is_named_not_silently_abstained(tmp_path):
    """A corrupt or encrypted inner PDF must not abstain with the same wording as
    an archive that genuinely carries no address."""
    import zipfile as zf

    p = tmp_path / "Deal Room.zip"
    with zf.ZipFile(p, "w") as z:
        z.writestr("Deal Room/OM.pdf", b"%PDF-1.4 truncated garbage")
    r = analyse(p, check_placeholders=False)
    assert "inner PDF unreadable" in r["reason"]
    assert "OM.pdf" in r["reason"]


# ── pairing ──────────────────────────────────────────────────────────

def _row(old, ext, st="ZZ", city="ZZ", ac="ZZ", prop=""):
    return dict(old=old, ext=ext, st=st, city=city, ac=ac, prop=prop,
                tags="", confidence="LOW", reason="")


def test_pairing_links_a_data_room_zip_to_its_om():
    from scripts.cims_rename_plan import apply_pairing

    rows = [_row("Kerrville Storwise Deal Room.zip", ".zip"),
            _row("Storwise Kerrville OM.pdf", ".pdf", st="TX", city="Kerrville", ac="SS")]
    apply_pairing(rows)
    assert (rows[0]["city"], rows[0]["st"]) == ("Kerrville", "TX")
    assert rows[0]["confidence"] == "REVIEW"


def test_pairing_never_gives_one_om_another_oms_city():
    """Two unrelated OMs share generic operator annotations; pairing across PDFs
    labelled a Belton deal as Creedmoor on the real sample set."""
    from scripts.cims_rename_plan import apply_pairing

    rows = [_row("Belton open parking Expo Storage Offering Memoradum.pdf", ".pdf"),
            _row("Austin open gravel Creedmoor Boat & RV Storage Property.pdf", ".pdf",
                 st="TX", city="Creedmoor", ac="BRV")]
    apply_pairing(rows)
    assert rows[0]["city"] == "ZZ"
    assert rows[0]["st"] == "ZZ"


def test_pairing_needs_a_substantial_shared_token():
    """Two short filler tokens are not evidence of the same property."""
    from scripts.cims_rename_plan import apply_pairing

    rows = [_row("new big deal room.zip", ".zip"),
            _row("new big OM.pdf", ".pdf", st="TX", city="Kerrville", ac="SS")]
    apply_pairing(rows)
    assert rows[0]["city"] == "ZZ"


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


@pytest.fixture
def comp_db(tmp_path, monkeypatch):
    """A throwaway comps DB, so the real data/cim_comps.db is never touched."""
    from data.comp_db import CompDatabase

    path = tmp_path / "comps.db"
    monkeypatch.setattr("data.comp_db.COMP_DB_PATH", str(path))
    db = CompDatabase()

    def add(pdf_filename, property_name="Expo Storage"):
        import sqlite3
        with sqlite3.connect(path) as conn:
            conn.execute(
                "INSERT INTO properties (property_name, city, state, "
                "analysis_date, pdf_filename) VALUES (?,?,?,?,?)",
                (property_name, "Belton", "TX", "2026-07-01", pdf_filename))
    return db, add


@pytest.mark.django_db
def test_comp_db_matches_a_prefixed_upload_to_a_bare_row(comp_db):
    """The row was analysed before the rename."""
    from webapp.services import find_upload_duplicates

    _, add = comp_db
    add("expo om.pdf")
    hits = find_upload_duplicates("[BRV-TX-Belton] expo om.pdf")
    assert any(h["pdf_filename"] == "expo om.pdf" for h in hits)


@pytest.mark.django_db
def test_comp_db_matches_a_bare_upload_to_a_prefixed_row(comp_db):
    """And the reverse — the direction the exact-filename query cannot see, and
    which the fuzzy property_name LIKE does not cover either, because
    'Expo Storage' does not contain the stem 'expo om'."""
    from webapp.services import find_upload_duplicates

    _, add = comp_db
    add("[BRV-TX-Belton] expo om.pdf")
    hits = find_upload_duplicates("expo om.pdf")
    assert any(h["pdf_filename"] == "[BRV-TX-Belton] expo om.pdf" for h in hits)


@pytest.mark.django_db
def test_comp_db_does_not_match_an_unrelated_file(comp_db):
    from webapp.services import find_upload_duplicates

    _, add = comp_db
    add("expo om.pdf")
    assert find_upload_duplicates("green storage om.pdf") == []


@pytest.mark.django_db
def test_comp_db_hit_is_not_duplicated_across_both_paths(comp_db):
    """An exact-name row is found by find_duplicates and by the stripped sweep;
    it must be reported once."""
    from webapp.services import find_upload_duplicates

    _, add = comp_db
    add("expo om.pdf")
    hits = [h for h in find_upload_duplicates("expo om.pdf")
            if h["pdf_filename"] == "expo om.pdf" and h["match_type"] == "filename"]
    assert len(hits) == 1


@pytest.mark.django_db
def test_unrelated_upload_is_not_a_duplicate():
    from webapp.models import Deal
    from webapp.services import find_upload_duplicates

    Deal.objects.create(deal_id="expo", property_name="Expo Storage",
                        input_files=["expo om.pdf"])
    assert find_upload_duplicates("somewhere else om.pdf") == []
