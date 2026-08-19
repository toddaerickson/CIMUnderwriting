"""A measured number is not a stated one.

The defect: Census enrichment fills `population_3mi` and `median_hhi_3mi`
BEFORE the pristine snapshot is saved, so a measured demographic sits in
`Deal.cim_json` looking exactly like an extracted one — and every surface
called it "stated in the CIM". On the number Gate 1 is decided by, for a
document that never mentioned it.

Three separate things had to be true for that to happen, and each gets
tests here, because fixing any two still leaves the memo lying:

1. the register had no VOCABULARY for a measurement (design decision 11's
   provenance set was closed at five);
2. the extract-time source log was never PERSISTED, so no later reader
   could have told the difference even with the word for it;
3. the analysis-time enrichment pass OVERWROTE what the extract-time pass
   knew — it finds the field already filled, resolves it at tier 1 and
   reports "CIM/override", which is true of what it was handed and false
   about where the number came from.

The surface assertions are the point (this file's siblings learned that
the hard way: a register that records perfectly and renders nowhere
delivers nothing). Each names the deliberate break that turns it red.

Section 6 is the follow-up: two more ways the same sentence — "stated in
the CIM" — got said about a number the CIM never stated, plus a guard on
the next one. Fixing the measurement left both standing, and one of them
was CAUSED by the fix.
"""

import pytest

from analysis import assumptions as A
from extract.enrichment import (MEASURED_TIER, merge_source_logs,
                                origin_for)
from tests.test_characterization import stabilized_deal
from tests.test_config_single_source import _memo_text, _run

#: A log shaped exactly as `extract.enrichment` writes one: the two
#: demographics it measured, plus the ring centre it measured them around.
MEASURED_LOG = {
    "lat": {"tier": 2, "source": "ZCTA centroid", "value": 32.221},
    "lon": {"tier": 2, "source": "ZCTA centroid", "value": -110.926},
    "population_3mi": {"tier": 2, "source": "Census API", "value": 87_450},
    "median_hhi_3mi": {"tier": 2, "source": "Census API", "value": 64_300.0},
}


def _measured_deal():
    """The stabilized fixture with its demographics MEASURED, not stated —
    value and snapshot agreeing, which is what enrichment-before-save
    produces and what made the mislabel invisible."""
    cim = stabilized_deal()
    cim.population_3mi = 87_450
    cim.median_hhi_3mi = 64_300.0
    snapshot = {"population_3mi": 87_450, "median_hhi_3mi": 64_300.0,
                "nrsf": cim.nrsf}
    return cim, snapshot


def _row(rows, key):
    return next(r for r in rows if r.key == key)


# ── 1. The vocabulary ───────────────────────────────────────────────

def test_a_measured_population_is_not_reported_as_stated_in_the_cim():
    """The defect itself, at the register.

    MUTATION: drop the `enrichment_log` argument at either
    `assumptions.collect`'s call site, or delete the `EXTERNAL` branch in
    `_add_cim_rows` — the row goes back to "stated in the CIM" and every
    number in the memo stays identical.
    """
    cim, snapshot = _measured_deal()
    rows = A.collect(cim_data=cim, cim_snapshot=snapshot,
                     enrichment_log=MEASURED_LOG)

    pop = _row(rows, "cim.population_3mi")
    assert pop.provenance == A.EXTERNAL
    assert pop.provenance_label == "measured from public data"
    assert pop.provenance_label != A.PROVENANCE_LABELS[A.CIM]
    # A field the document really did state is untouched by any of this.
    assert _row(rows, "cim.nrsf").provenance == A.CIM


def test_the_measurement_row_discloses_the_ring_it_measured():
    """A 3-mile ring around a matched building and one around a ZIP's
    centroid are different claims about the same property — which is why
    `enrichment` stamps the centre separately in the first place. Naming
    the API and staying silent about the centre discloses the easier half.

    MUTATION: return `source` alone from `_measurement_detail`.
    """
    cim, snapshot = _measured_deal()
    rows = A.collect(cim_data=cim, cim_snapshot=snapshot,
                     enrichment_log=MEASURED_LOG)
    assert _row(rows, "cim.population_3mi").detail == (
        "Census API — ring centred on the ZCTA centroid")


def test_a_measurement_is_not_counted_among_the_rows_a_human_chose():
    """Operator's call, 2026-08-18: `CHOSEN` answers "did a human or a
    fallback produce this?", and a measurement is neither. Widening it
    would change what memo table B.1 promises a reader.

    MUTATION: add `EXTERNAL` to `CHOSEN` — B.1 grows rows whose intro
    sentence still enumerates only three provenances.
    """
    cim, snapshot = _measured_deal()
    rows = A.collect(cim_data=cim, cim_snapshot=snapshot,
                     enrichment_log=MEASURED_LOG)
    counts = A.summarize(rows)

    assert A.EXTERNAL not in A.CHOSEN
    assert counts["external"] == 2
    assert not _row(rows, "cim.population_3mi").chosen
    # The count identity the memo's lead sentence rests on still holds
    # with a sixth provenance in the vocabulary.
    assert counts["total"] == sum(counts[k] for k in A.PROVENANCE_KEYS)


def test_the_vocabulary_stays_closed_and_derived():
    """Decision 11's set is closed and its labels are the one source for
    the keys. A sixth member must not have been added by widening one and
    forgetting the other."""
    assert A.PROVENANCE_KEYS == tuple(A.PROVENANCE_LABELS)
    assert A.EXTERNAL in A.PROVENANCE_KEYS
    assert A.PROVENANCE_LABELS[A.EXTERNAL].strip()
    # Precedence is declaration order: the resolver takes a CIM-stated
    # population over a measured one (tier 1 beats tier 2), and a
    # measurement beats a shipped default.
    order = list(A.PROVENANCE_KEYS)
    assert order.index(A.CIM) < order.index(A.EXTERNAL) < order.index(A.CONFIG)


def test_without_a_log_every_stated_field_is_still_cim():
    """Not a degraded mode — it is what a run that never enriched means,
    and what every deal extracted before the log existed will report
    forever. It must not guess."""
    cim, snapshot = _measured_deal()
    rows = A.collect(cim_data=cim, cim_snapshot=snapshot)
    assert _row(rows, "cim.population_3mi").provenance == A.CIM
    assert A.summarize(rows)["external"] == 0


# ── 2. The log describes ONE value ──────────────────────────────────

def test_an_analyst_correction_is_not_credited_to_the_census():
    """The log entry records where one particular number came from. Type a
    population over the measured one and the entry stops describing it —
    reporting it anyway credits the Census with an analyst's figure.

    MUTATION: compare only the field NAME in `origin_for`, ignoring
    `value`.
    """
    cim, snapshot = _measured_deal()
    cim.population_3mi = 61_000          # analyst overtyped it

    rows = A.collect(cim_data=cim, cim_snapshot=snapshot,
                     enrichment_log=MEASURED_LOG)
    pop = _row(rows, "cim.population_3mi")
    assert pop.provenance == A.DEAL
    assert pop.was == 87_450, "the correction must still say what it displaced"


def test_the_gate_reports_no_source_for_a_number_nobody_measured():
    """Gate 1 prints the origin beside the population it decided on. Same
    entry, same staleness, same wrong credit — so it asks the same
    question through the same function.

    MUTATION: restore the direct `source_log[...]["source"]` lookup in
    `analysis.filters` — the gate credits "Census API" for a figure the
    analyst typed.
    """
    from analysis.filters import evaluate_gates

    cim, _ = _measured_deal()
    measured = evaluate_gates(cim, {}, None, source_log=MEASURED_LOG)[0]
    assert measured["source"] == "Census API"

    cim.population_3mi = 61_000
    overtyped = evaluate_gates(cim, {}, None, source_log=MEASURED_LOG)[0]
    assert overtyped["actual"] == "61,000"
    assert not overtyped["source"]


def test_origin_for_survives_a_json_round_trip():
    """The log reaches the register through a database column, so the
    comparison meets an int that left as an int and a float that left as a
    float — and `median_hhi_3mi` is the float."""
    import json

    stored = json.loads(json.dumps(MEASURED_LOG))
    assert origin_for(stored, "median_hhi_3mi", 64_300.0)["tier"] == MEASURED_TIER
    assert origin_for(stored, "population_3mi", 87_450)["tier"] == MEASURED_TIER
    assert origin_for(stored, "population_3mi", 87_451) is None
    assert origin_for(stored, "nrsf", 60_000) is None
    assert origin_for({}, "population_3mi", 87_450) is None


# ── 3. The second pass must not erase the first ─────────────────────

def test_a_second_enrichment_pass_does_not_overwrite_a_measurement():
    """The web path enriches twice, and the second pass sees the first
    pass's output already on `cim_data` — resolving it at tier 1,
    "CIM/override". Left alone that is the mislabel arriving by a second
    route, immune to every fix above.

    MUTATION: make `merge_source_logs` a plain `{**prior, **later}`.
    """
    from extract.enrichment import DataResolver

    first, second = {}, {}
    DataResolver(first).resolve("population_3mi", tier1_value=None,
                                tier2_fn=lambda: 87_450)
    DataResolver(second).resolve("population_3mi", tier1_value=87_450,
                                 tier2_fn=lambda: 87_450)
    assert second["population_3mi"]["tier"] == 1, "the shadowing this guards"

    merged = merge_source_logs(first, second)
    assert merged["population_3mi"]["tier"] == MEASURED_TIER
    assert merged["population_3mi"]["source"] == "Census API"


def test_a_later_pass_that_really_measured_wins():
    """The mirror image, and the reason the rule is "a later TIER-1 entry
    never displaces a measurement" rather than "the stored log wins": an
    analyst who corrects the address gets a genuinely new measurement, and
    that one IS the newer fact."""
    prior = {"population_3mi": {"tier": 2, "source": "Census API",
                                "value": 87_450}}
    later = {"population_3mi": {"tier": 2, "source": "Census API",
                                "value": 91_000}}
    assert merge_source_logs(prior, later)["population_3mi"]["value"] == 91_000
    # And a field only the later pass saw is simply added.
    assert "lat" in merge_source_logs(prior, {"lat": {"tier": 2}})


# ── 4. The wires ────────────────────────────────────────────────────

def test_the_engine_carries_a_stored_log_through_to_the_register(tmp_path):
    """The web path's whole wire in one call: `run_analysis` is handed the
    log the extract-time pass produced, and the register it assembles must
    use it. This run does not re-enrich (the fields are already filled),
    which is exactly the case that had no log at all.

    MUTATION: drop `enrichment_log=source_log` from the `collect` call in
    `engine.run_analysis`.
    """
    cim, snapshot = _measured_deal()
    result = _run(cim, tmp_path, cim_snapshot=snapshot,
                  enrichment_log=MEASURED_LOG)

    rows = A.from_dicts(result.assumption_register)
    assert _row(rows, "cim.population_3mi").provenance == A.EXTERNAL
    gate = next(g for g in result.gate_results if g["gate"] == 1)
    assert gate["source"] == "Census API"


def test_the_memo_says_measured_where_it_used_to_say_stated(tmp_path):
    """The surface the whole item exists for. An IC reader looks up the
    population in Appendix B and must not be told the seller stated it.

    MUTATION: any of the above — this is the assertion they all serve.
    """
    cim, snapshot = _measured_deal()
    result = _run(cim, tmp_path, cim_snapshot=snapshot,
                  enrichment_log=MEASURED_LOG)
    text = _memo_text(result.memo_path)

    assert "3-Mile Population" in text
    assert A.PROVENANCE_LABELS[A.EXTERNAL] in text


def test_the_cli_register_can_report_a_measurement_it_took(tmp_path):
    """The CLI enriches exactly once and needs no storage — its log still
    describes the values on `ctx.cim_data`. The sibling test
    `test_a_cli_register_never_claims_a_settings_or_deal_override` pins
    what the CLI cannot have; this pins what it can.

    MUTATION: drop `enrichment_log=source_log` from `run.py`'s `collect`
    call — the CLI's memo silently re-acquires the defect the web app's
    just lost, which is the exact split the fill log shipped with once.
    """
    rows = A.collect(cim_data=_measured_deal()[0],
                     enrichment_log=MEASURED_LOG)
    provenances = {r.provenance for r in rows}
    assert A.EXTERNAL in provenances
    assert A.SETTINGS not in provenances and A.DEAL not in provenances


# ── 5. Persistence — the half nothing could reconstruct ─────────────

@pytest.mark.django_db
def test_the_extract_worker_stores_the_log_beside_the_snapshot(tmp_path,
                                                               monkeypatch):
    """It has to be saved by the pass that measured. Nothing downstream
    can rebuild it: the analysis-time pass finds the field filled and
    reports tier 1.

    MUTATION: remove the `enrichment_log` key from `_extract_worker`'s
    `updates` — extraction still succeeds, every stored number is
    identical, and the deal reports its measured demographics as
    CIM-stated for the rest of its life.
    """
    from engine import AnalysisResult
    from webapp import services
    from webapp.models import Deal

    cim, _ = _measured_deal()

    class _Enrichment:
        source_log = MEASURED_LOG

    def _fake_extract(path):
        result = AnalysisResult(pdf_path=path)
        result.cim_data = cim
        result.extraction_report = {}
        result.enrichment = _Enrichment()
        return result

    monkeypatch.setattr(services, "extract_pdf_data", _fake_extract)
    deal = Deal.objects.create(deal_id="probe", property_name="Probe",
                               extract_requested_at=None)
    services._extract_worker(deal.pk, str(tmp_path / "deal.pdf"), None)

    deal.refresh_from_db()
    assert deal.enrichment_log, "the measuring pass saved no log"
    assert deal.enrichment_log["population_3mi"]["tier"] == MEASURED_TIER
    assert deal.cim_json["population_3mi"] == 87_450, "the snapshot too"


@pytest.mark.django_db
def test_the_assumptions_page_labels_a_measured_demographic(tmp_path):
    """The page had the label and could never reach it: it read the log
    off the LATEST RUN, and a deal with no run yet has none while a run
    that finds the demographics already filled stores no enrichment block
    at all. Both showed "CIM".

    MUTATION: read `source_log` from the run alone, as before.
    """
    from webapp import forms as f
    from webapp.models import Deal

    deal = Deal.objects.create(
        deal_id="probe2", property_name="Probe",
        cim_json={"population_3mi": 87_450, "nrsf": 60_000},
        enrichment_log=MEASURED_LOG)

    form = f.AssumptionsForm(initial={"population_3mi": 87_450,
                                      "nrsf": 60_000})
    rows = f.model_rows(form, [("population_3mi", "3-Mile Population"),
                               ("nrsf", "Rentable SF")],
                        deal.cim_json, deal.enrichment_log)
    by_label = {r["label"]: r["source"] for r in rows}
    assert by_label["3-Mile Population"] == "Census"
    assert by_label["Rentable SF"] == "CIM"


# ── 6. The two the first pass left behind ───────────────────────────
#
# Both were reproduced against merged `main` before being fixed, and both
# are the SAME defect as the one above wearing different clothes: a number
# the document does not contain, credited to the document. The first is
# older than the measurement work — an analyst typing into an empty field
# has always read as "stated in the CIM". The second is the price of
# keeping `EXTERNAL` out of `CHOSEN`, which is still the right call: the
# tables were fine, the SENTENCES around them were counting the wrong set.
# Found independently in PR #102, verified here rather than taken on its
# word.

def test_a_number_the_analyst_typed_into_an_empty_field_is_not_the_cims():
    """Extraction found no market rent; the analyst supplied one. That is
    an act of judgement by a human — the whole thing `CHOSEN` exists to
    surface — and it rendered as "stated in the CIM".

    MUTATION: restore `edited = ... and prior is not None and ...` as the
    only route to `DEAL`. The row silently re-acquires the CIM's name.
    """
    cim, snapshot = _measured_deal()
    cim.market_rent_psf = 1.35
    snapshot["market_rent_psf"] = None       # extraction found nothing

    row = _row(A.collect(cim_data=cim, cim_snapshot=snapshot),
               "cim.market_rent_psf")
    assert row.provenance == A.DEAL
    assert row.provenance_label != A.PROVENANCE_LABELS[A.CIM]
    assert row.detail, "a filled-in field must say where it came from"
    # Nothing was displaced, so nothing may be reported as replaced —
    # "Replaced: —" and "Replaced: 0" are different claims.
    assert row.was is None


def test_a_field_the_snapshot_never_carried_stays_cim():
    """Schema drift, not an analyst. `Deal.cim_json` is `asdict`, so a
    field of that snapshot's vintage is a KEY even when its value is None;
    a field missing entirely means the deal was stored before the parser
    knew the field, and its origin is simply unknowable.

    MUTATION: test `prior is None` alone, without `field in snapshot` —
    every deal stored before a given field existed starts claiming the
    analyst typed it.
    """
    cim, snapshot = _measured_deal()
    cim.market_rent_psf = 1.35
    snapshot.pop("market_rent_psf", None)

    row = _row(A.collect(cim_data=cim, cim_snapshot=snapshot),
               "cim.market_rent_psf")
    assert row.provenance == A.CIM


def test_a_field_measured_after_the_analyst_fixed_the_address_reads_measured():
    """Why the measured branch is tested FIRST. Extraction's enrichment
    pass failed (no geocode), so the snapshot carries None; the analyst
    corrected the ZIP and the analysis-time pass really measured the
    population. Nobody typed that number.

    MUTATION: move the `EXTERNAL` branch below the FILLED-AN-EMPTY-FIELD
    branch — the Census's figure is reported as one the analyst entered,
    which is the mislabel of this whole module with the credit reversed.
    (Below the CORRECTION branch is harmless and does not turn this red:
    a correction needs a prior to differ from, and this case has none.
    The measured branch has to clear the branch above it, not both.)
    """
    cim, snapshot = _measured_deal()
    snapshot["population_3mi"] = None

    row = _row(A.collect(cim_data=cim, cim_snapshot=snapshot,
                         enrichment_log=MEASURED_LOG),
               "cim.population_3mi")
    assert row.provenance == A.EXTERNAL
    assert "Census API" in row.detail


def test_a_measured_only_run_is_not_headlined_as_all_model_defaults():
    """The results panel, rendered. With `chosen` driving the headline, a
    run whose population was measured said "all model defaults" over a
    Census figure — every row of it correct, and the one line most readers
    stop at false.

    MUTATION: put `register_counts.chosen` back in the summary line.
    """
    from django.template.loader import render_to_string

    from webapp.results import register_context

    cim, snapshot = _measured_deal()
    rows = A.collect(cim_data=cim, cim_snapshot=snapshot,
                     enrichment_log=MEASURED_LOG)
    ctx = register_context({"assumption_register": A.to_dicts(rows)})
    assert ctx["register_counts"]["chosen"] == 0, "the case that broke it"
    assert ctx["register_counts"]["not_from_defaults"] == 2

    html = render_to_string("webapp/_tab_summary.html", ctx)
    assert "all model defaults" not in html
    assert "2 not from the CIM or the model defaults" in html


def test_the_memo_does_not_tell_a_measured_run_it_read_everything_in_the_cim():
    """Appendix B's B.1 empty case, which is reachable exactly when the
    headline above is: no deal entry, no settings row, no fallback — and a
    measurement. It read "every input came from the CIM as stated".

    MUTATION: drop the `measured` branch in `_add_assumption_register`.
    """
    from docx import Document

    from output.memo_writer import _add_assumption_register

    cim, snapshot = _measured_deal()
    rows = A.collect(cim_data=cim, cim_snapshot=snapshot,
                     enrichment_log=MEASURED_LOG)
    doc = Document()
    _add_assumption_register(doc, A.to_dicts(rows))
    text = "\n".join(p.text for p in doc.paragraphs)

    assert "every input came from the CIM as stated" not in text
    assert "measured from public data for this location" in text
    # The count the lead sentence quotes is the one B.1 actually lists,
    # so the two cannot contradict each other.
    assert "0 came from something other than the model's shipped defaults" \
        in text


def test_no_caller_wires_a_tier_the_register_cannot_name():
    """Green today; red the day enrichment learns a third source.

    `_add_cim_rows` maps exactly ONE tier to a provenance — 2, the
    measurement. Tiers 3 (the comp DB) and 4 (a static default) are
    implemented in `DataResolver.resolve` and wired by NOBODY, so every
    other tier falling through to "stated in the CIM" is unreachable
    today. That is a fact about the call sites, and nothing in the
    register would notice it changing: wire `tier3_fn` for a demographic
    and a comp-database figure starts reporting itself as the seller's.
    A comp-DB value is not `external` by default either — it is this
    system's own prior data, and #93 is a whole PR about how stale that
    can be — so the mapping is a decision, not a default, and this test
    exists to force it to be made once rather than discovered later.
    """
    import ast
    import inspect

    from extract.enrichment import DataResolver

    params = inspect.signature(DataResolver.resolve).parameters
    assert {"tier3_fn", "tier4_default"} <= set(params), \
        "the tiers were renamed — this guard is watching nothing"

    src = inspect.getsource(inspect.getmodule(DataResolver))
    calls = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "resolve"]
    assert calls, "no `resolve` call sites found — the walk missed them"

    wired = {kw.arg for c in calls for kw in c.keywords} & {"tier3_fn",
                                                            "tier4_default"}
    assert not wired, (
        f"{sorted(wired)} is now wired for a CIMData field. Give the tier a "
        f"provenance in `analysis.assumptions._add_cim_rows` first — "
        f"unmapped, it renders as 'stated in the CIM'.")
