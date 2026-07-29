# Dense Model View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the assumptions page with a single vertical driver-first
model view: universal Extracted | Analyst | Final rows, per-line expense
editing, market-screen header strip, and a server-computed htmx live
preview. Spec: `docs/superpowers/specs/2026-07-29-dense-model-view-design.md`.

**Architecture:** Data plumbing first (CIMData fields → form round-trip →
engine precedence), then the preview endpoint (reusing the exact engine
code — no formula duplication), then the template rebuild, then the
two-pass UI review. Each task is an independently testable commit on one
branch (`claude/dense-model-view`); one PR at the end (operator chose
single release).

**Tech Stack:** Django 5 + django-htmx (already installed), Tailwind
(existing build), pytest-django. NO new dependencies.

## Global Constraints

- No new pip dependencies; no DB migration (all new data flows through
  `cim_json` / `assumption_overrides` JSON).
- The 7 gates stay exactly 7 — new signals are risk flags only.
- Save path semantics unchanged: full validation, NOI identity block +
  accept checkbox, `build_overrides` deltas. Preview NEVER writes.
- Suite baseline 184 must never drop; every task ends green.
- `market_rent_psf` is RELABELED "Street Rate ($/SF/mo)" — display only,
  the field name and semantics do not change.
- Coalesce rule everywhere: **Final = analyst override if present, else
  extracted (demographics: else Census)**. Expense lines: benchmark
  adjustment applies on top, surfaced in the Flag column.
- Commit format: conventional, `Co-Authored-By: Claude Fable 5
  <noreply@anthropic.com>` trailer, test-count delta in the body.

---

### Task 1: New CIMData fields + form round-trip

**Files:**
- Modify: `extract/parser.py` (CIMData dataclass, after `market_verified_location`)
- Modify: `webapp/forms.py` (field lists, choices, `__init__`, `SECTION_DEMOGRAPHICS`, `SECTION_INCOME` label)
- Test: `tests/test_web_deals.py`

**Interfaces:**
- Produces: `CIMData.in_place_avg_rent_psf: Optional[float]`,
  `CIMData.street_rate_trend: Optional[str]` (`"rising"|"flat"|"falling"|None`),
  `CIMData.t3_annualized_revenue: Optional[float]`;
  `forms.STREET_RATE_TREND_CHOICES`. Later tasks read these off cim_data
  via `getattr(..., None)`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run it — expect FAIL** (`KeyError: 'in_place_avg_rent_psf'`):
  `python -m pytest tests/test_web_deals.py::test_new_driver_fields_roundtrip_and_old_snapshots -q`

- [ ] **Step 3: Implement**

`extract/parser.py`, directly after `market_verified_location`:

```python
    # Rate positioning & momentum drivers (screening-framework additions).
    # in_place_avg_rent_psf: analyst override; when None the engine
    # derives occupied-weighted $/SF/mo from the unit mix.
    # street_rate_trend: rising | flat | falling (None = unknown) — feeds
    # the ECRI-in-falling-market risk flag. t3_annualized_revenue: T3
    # annualized $ for the momentum screen vs T12.
    in_place_avg_rent_psf: Optional[float] = None
    street_rate_trend: Optional[str] = None
    t3_annualized_revenue: Optional[float] = None
```

`webapp/forms.py`:
1. `CIM_CHAR_FIELDS` gains `"street_rate_trend"`;
   `CIM_FLOAT_FIELDS` gains `"in_place_avg_rent_psf",
   "t3_annualized_revenue"`.
2. Next to `MARKET_VERIFICATION_CHOICES`:

```python
STREET_RATE_TREND_CHOICES = [
    ("", "Unknown"),
    ("rising", "Rising"),
    ("flat", "Flat"),
    ("falling", "Falling"),
]
```

3. In `AssumptionsForm.__init__`, immediately after the
   `market_verification` ChoiceField assignment (same pattern — declared
   in CHAR list for plumbing, re-declared as a constrained dropdown):

```python
        self.fields["street_rate_trend"] = forms.ChoiceField(
            required=False, choices=STREET_RATE_TREND_CHOICES,
            widget=forms.Select(attrs={"class": INPUT_CSS}))
```

4. `SECTION_DEMOGRAPHICS`: change
   `("market_rent_psf", "Market Rent ($/SF/mo)")` →
   `("market_rent_psf", "Street Rate ($/SF/mo)")` and append
   `("in_place_avg_rent_psf", "In-Place Rent ($/SF/mo)")`,
   `("street_rate_trend", "Street-Rate Trend")`,
   `("t3_annualized_revenue", "T3 Annualized Revenue ($)")`.
   (Temporary home — Task 7 rebuilds the sections; keeping the current
   page functional between tasks.)

- [ ] **Step 4: Run the test — PASS; full suite — 185.**
- [ ] **Step 5: Commit** `feat(model-view): rate-positioning + momentum driver fields (T1)`

---

### Task 2: expense_line_overrides — form fields + storage

**Files:**
- Modify: `webapp/forms.py` (`__init__`, `build_initial`, `build_overrides`)
- Test: `tests/test_web_deals.py`

**Interfaces:**
- Consumes: `registry.EXPENSE_KEYS` (list of benchmark keys, e.g.
  `"property_tax"`, `"insurance"`).
- Produces: form fields named `exp_<key>` (FloatField, optional, ≥0);
  `assumption_overrides["expense_line_overrides"]: dict[str, float]` —
  Task 3 consumes this dict; Task 7 renders the fields.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run — FAIL** (unknown field `exp_property_tax`).
- [ ] **Step 3: Implement**

`AssumptionsForm.__init__`, after the RC loop:

```python
        from registry import EXPENSE_KEYS
        for key in EXPENSE_KEYS:
            self.fields[f"exp_{key}"] = forms.FloatField(
                required=False, min_value=0, widget=_num())
```

`build_initial`, before `return initial`:

```python
    for key, val in (saved.get("expense_line_overrides") or {}).items():
        initial[f"exp_{key}"] = val
```

`build_overrides`, after the `noi_reconciliation` block:

```python
    from registry import EXPENSE_KEYS
    exp_o = {k: cleaned[f"exp_{k}"] for k in EXPENSE_KEYS
             if cleaned.get(f"exp_{k}") is not None}
    if exp_o:
        out["expense_line_overrides"] = exp_o
```

- [ ] **Step 4: Test PASS; suite 186.**
- [ ] **Step 5: Commit** `feat(model-view): per-line expense overrides — fields + storage (T2)`

---

### Task 3: Analyst-beats-CIM expense precedence in the engine

**Files:**
- Modify: `analysis/financials.py:17` (`analyze_financials`), `:305`
  (`_map_expense_lines` call site), line dicts
- Modify: `engine.py` (`run_analysis` signature + `analyze_financials` call)
- Modify: `webapp/services.py` (worker passes the dict)
- Test: `tests/test_financials.py`, `tests/test_web_runs.py` (fake sigs)

**Interfaces:**
- Consumes: `expense_line_overrides: dict[str, float]` from Task 2.
- Produces: `analyze_financials(cim_data, comp_db=None,
  expense_line_overrides=None)`; each expense line dict gains
  `"source": "analyst" | "cim" | None`. `run_analysis(...,
  expense_line_overrides=None)` — worker call:
  `expense_line_overrides=overrides.get("expense_line_overrides")`.

- [ ] **Step 1: Write the failing test**

```python
def test_expense_line_override_beats_cim_and_still_benchmarked(mock_cim_data):
    """Analyst value replaces the CIM-extracted line; the benchmark
    floor still applies on top (Final below floor -> floor, flagged)."""
    from analysis.financials import analyze_financials

    # analyst enters a real payroll figure; CIM extracted $0
    fin = analyze_financials(mock_cim_data,
                             expense_line_overrides={"payroll": 12_600.0})
    payroll = next(l for l in fin["expense_analysis"]["lines"]
                   if l["benchmark_key"] == "payroll")
    assert payroll["cim_value"] == 12_600.0     # coalesced input
    assert payroll["source"] == "analyst"
    # analyst enters a value below the floor -> engine floors it, flagged
    fin2 = analyze_financials(mock_cim_data,
                              expense_line_overrides={"payroll": 500.0})
    p2 = next(l for l in fin2["expense_analysis"]["lines"]
              if l["benchmark_key"] == "payroll")
    assert p2["flag"] == "BELOW RANGE"
    assert p2["adjusted_value"] > 500.0
```

(Adjust the exact assertion keys to the real `expense_analysis` shape at
implementation time — the lines list and keys `benchmark_key`,
`cim_value`, `adjusted_value`, `flag` already exist; only `source` is
new. If lines live at `fin["expense_analysis"]["lines"]` under a
different name, follow the code, not this sketch, and fix the test.)

- [ ] **Step 2: Run — FAIL** (unexpected kwarg).
- [ ] **Step 3: Implement**

`analysis/financials.py`:

```python
def analyze_financials(cim_data, comp_db=None,
                       expense_line_overrides=None) -> dict:
```

after `expense_map = _map_expense_lines(cim_data)`:

```python
    # Analyst-entered line values beat CIM-extracted ones (model-view
    # coalesce rule); the benchmark adjustment below applies on top.
    analyst_keys = set()
    if expense_line_overrides:
        for k, v in expense_line_overrides.items():
            expense_map[k] = v
            analyst_keys.add(k)
```

and in BOTH line-dict appends (formula branch + benchmark branch) add:

```python
            "source": ("analyst" if benchmark_key in analyst_keys
                       else ("cim" if cim_value is not None else None)),
```

`engine.py` `run_analysis`: add param `expense_line_overrides=None`; call
becomes `analyze_financials(cim_data, comp_db=comp_db,
expense_line_overrides=expense_line_overrides)`.

`webapp/services.py` worker call gains
`expense_line_overrides=overrides.get("expense_line_overrides"),`.

`tests/test_web_runs.py` `fake_run._fake` and both `_fake` in
`tests/test_web_config.py` gain `expense_line_overrides=None`; assert in
`test_worker_success_updates_run_and_deal` that the worker passes the
saved dict through (add `"expense_line_overrides": {"payroll": 12600.0}`
to the deal's `assumption_overrides` fixture and
`assert fake_run["expense_line_overrides"] == {"payroll": 12600.0}`).

Memo (spec requirement "analyst column source note"): grep
`output/memo_writer.py` for the expense-table rendering; where it prints
each line, append ` (analyst)` when `line.get("source") == "analyst"` —
one-line change, covered by eyeballing the generated memo in Task 9's
walkthrough (memo content has no unit tests today; don't add a docx
parser for one suffix).

- [ ] **Step 4: Suite green (188 ±: new tests + fake assert).**
- [ ] **Step 5: Commit** `feat(engine): analyst expense lines beat CIM-extracted; benchmark still floors (T3)`

---

### Task 4: In-place rent + rent gap in rent_analysis

**Files:**
- Modify: `analysis/rent_analysis.py:9` (`analyze_rents` return dict + new helper)
- Test: `tests/test_valuation.py` or `tests/test_financials.py` (whichever
  already imports rent_analysis; else new section in `tests/test_web_analyze.py`)

**Interfaces:**
- Consumes: Task 1 fields.
- Produces: `analyze_rents(...)` dict gains
  `"in_place_avg_rent_psf": float|None`,
  `"in_place_rent_source": "override"|"derived"|None`,
  `"rent_gap_pct": float|None` (fraction, e.g. 0.18). Task 5 + preview
  (Task 6) consume these.

- [ ] **Step 1: Write the failing test**

```python
def test_in_place_rent_and_gap(mock_cim_data):
    from analysis.rent_analysis import analyze_rents
    from extract.parser import UnitType

    mock_cim_data.unit_mix = [
        UnitType(size_label="10x10", sf=100.0, count=100, rate=95.0),
        UnitType(size_label="10x20", sf=200.0, count=50, rate=160.0),
    ]
    mock_cim_data.market_rent_psf = 1.20   # street rate
    r = analyze_rents(mock_cim_data)
    # (95*100 + 160*50) / (100*100 + 200*50) = 17,500 / 20,000 = 0.875
    assert r["in_place_avg_rent_psf"] == 0.88
    assert r["in_place_rent_source"] == "derived"
    assert r["rent_gap_pct"] == round((1.20 - 0.88) / 1.20, 4)

    mock_cim_data.in_place_avg_rent_psf = 1.00   # analyst override wins
    r2 = analyze_rents(mock_cim_data)
    assert r2["in_place_avg_rent_psf"] == 1.00
    assert r2["in_place_rent_source"] == "override"
```

- [ ] **Step 2: Run — FAIL** (KeyError).
- [ ] **Step 3: Implement** — helper in `analysis/rent_analysis.py`:

```python
def _in_place_rent_psf(cim_data):
    """Occupied-weighted in-place $/SF/mo; analyst override wins."""
    override = getattr(cim_data, "in_place_avg_rent_psf", None)
    if override is not None:
        return override, "override"
    mix = cim_data.unit_mix or []
    tot_sf = sum((u.sf or 0) * (u.count or 0) for u in mix)
    tot_rent = sum((u.rate or 0) * (u.count or 0) for u in mix)
    if tot_sf > 0 and tot_rent > 0:
        return round(tot_rent / tot_sf, 2), "derived"
    return None, None
```

In `analyze_rents`, before BOTH return statements (line ~23 early return
and line ~74 main return), compute once and merge into the dict:

```python
    in_place, in_place_src = _in_place_rent_psf(cim_data)
    street = cim_data.market_rent_psf
    gap = (round((street - in_place) / street, 4)
           if in_place and street and street > 0 else None)
```

and add `"in_place_avg_rent_psf": in_place, "in_place_rent_source":
in_place_src, "rent_gap_pct": gap,` to both returned dicts.

- [ ] **Step 4: Suite green.**
- [ ] **Step 5: Commit** `feat(rents): in-place avg rent (derived/override) + rent gap pct (T4)`

---

### Task 5: Risk flags — ECRI-in-falling-market + negative momentum

**Files:**
- Modify: `analysis/risks.py:14` (`identify_risks` body — append to `risks` before the sort at `:49`)
- Test: `tests/test_gates.py` (risks tests colocate fine) or a new
  `tests/test_risks.py` section if one exists — follow the existing home
  of risk tests (grep `identify_risks` in tests/ first).

**Interfaces:**
- Consumes: `rent_analysis["rent_gap_pct"]` (Task 4), Task 1 fields,
  `config.GATES["rate_bridge_gap_threshold"]` (existing, 0.10).
- Produces: two new risk dicts in the standard shape
  (`category/risk/description/severity/mitigation`). Gates untouched.

- [ ] **Step 1: Write the failing test**

```python
def test_ecri_falling_market_risk(mock_cim_data):
    from analysis.risks import identify_risks
    mock_cim_data.street_rate_trend = "falling"
    risks = identify_risks(mock_cim_data, [], {}, {},
                           {"rent_gap_pct": 0.18})
    assert any("falling street-rate" in r["risk"].lower() and
               r["severity"] == "High" for r in risks["risks"])
    # no flag when trend unknown
    mock_cim_data.street_rate_trend = None
    risks2 = identify_risks(mock_cim_data, [], {}, {},
                            {"rent_gap_pct": 0.18})
    assert not any("falling street-rate" in r["risk"].lower()
                   for r in risks2["risks"])


def test_negative_momentum_risk(mock_cim_data):
    from analysis.risks import identify_risks
    mock_cim_data.ttm_total_revenue = 560_000.0
    mock_cim_data.t3_annualized_revenue = 512_000.0
    risks = identify_risks(mock_cim_data, [], {}, {}, {})
    hit = [r for r in risks["risks"] if "momentum" in r["risk"].lower()]
    assert hit and hit[0]["severity"] == "Medium"
    assert "-8.6%" in hit[0]["description"]
```

(Match `identify_risks`'s real signature order —
`(cim_data, gate_results, financial_analysis, scenario_results,
rent_analysis)` per `engine.py:237-239` — adjust if it differs.)

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** — in `identify_risks`, before the severity sort:

```python
    # ECRI bridge in a falling street-rate market (criteria watch item):
    # the in-place-to-market gap closes from above.
    from config import GATES
    gap = (rent_analysis or {}).get("rent_gap_pct")
    if gap and gap >= GATES["rate_bridge_gap_threshold"] and \
            getattr(cim_data, "street_rate_trend", None) == "falling":
        risks.append({
            "category": "Market",
            "risk": "ECRI bridge in falling street-rate market",
            "description": (f"In-place rents are {gap:.0%} below street "
                            f"while street rates are falling — the gap "
                            f"closes from above; the assumed rent bridge "
                            f"may not exist."),
            "severity": "High",
            "mitigation": ("Verify street-rate trend against operator "
                           "reports and comps; underwrite the bridge to "
                           "current street rates, not peak."),
        })

    # Negative revenue momentum: T3-annualized below T12.
    t3 = getattr(cim_data, "t3_annualized_revenue", None)
    t12 = cim_data.ttm_total_revenue or cim_data.ttm_egr
    if t3 and t12 and t12 > 0 and t3 < t12:
        pct = t3 / t12 - 1
        risks.append({
            "category": "Financial",
            "risk": "Negative revenue momentum (T3 vs T12)",
            "description": (f"T3-annualized revenue ${t3:,.0f} is "
                            f"{pct:.1%} vs T12 ${t12:,.0f} — revenue is "
                            f"rolling over while underwriting assumes "
                            f"growth."),
            "severity": "Medium",
            "mitigation": ("Confirm with monthly operating statements; "
                           "haircut Year-1 revenue growth to the T3 "
                           "run-rate."),
        })
```

- [ ] **Step 4: Suite green.**
- [ ] **Step 5: Commit** `feat(risks): ECRI-in-falling-market + negative-momentum flags (T5)`

---

### Task 6: Preview endpoint (server-computed htmx partial)

**Files:**
- Modify: `analysis/filters.py` (extract `sf_per_capita_inputs` helper from gate 5 — single formula source)
- Modify: `webapp/services.py` (`build_preview_cim`, `model_strip_context`)
- Modify: `webapp/views.py` (`assumptions_preview`), `cimweb/urls.py`
- Create: `webapp/templates/webapp/_model_preview.html`
- Test: `tests/test_web_analyze.py`

**Interfaces:**
- Consumes: Tasks 1–5 outputs; `analysis.filters` gate-5 math.
- Produces: `POST /deals/<pk>/assumptions/preview/` → 200 HTML partial
  (`#model-strip` div + `hx-swap-oob` spans `#exp-used-<key>` /
  `#exp-flag-<key>` + `#noi-chip`). `filters.sf_per_capita(cim_data) ->
  tuple[float|None, str]` (value, problem-reason). Task 7 wires the
  htmx attributes to this URL.

- [ ] **Step 1: Extract the shared SF/capita helper (refactor, tests stay green)**

In `analysis/filters.py`, hoist the gate-5 computation into:

```python
def sf_per_capita(cim_data):
    """(value, problem): value None when inputs missing/invalid; problem
    is the human reason ("" when simply not yet entered)."""
```

Body = the exact try/except + guards currently inline in `evaluate_gates`
(move verbatim; gate 5 calls the helper). Run
`python -m pytest tests/test_gates.py -q` — all existing gate tests must
stay green with zero assertion changes.

- [ ] **Step 2: Write the failing endpoint test**

```python
@pytest.mark.django_db
def test_assumptions_preview_contract(client, django_user_model, settings, tmp_path):
    from webapp.models import AnalysisRun, Deal
    settings.CIM_DEALS_DIR = str(tmp_path)
    user = django_user_model.objects.create_user(username="op", password="x")
    client.force_login(user)
    deal = Deal.objects.create(
        deal_id="pv", property_name="PV",
        cim_json={"property_name": "PV", "state": "TX", "nrsf": 50_000.0,
                  "population_3mi": 75_000, "ttm_egr": 550_000.0})
    runs_before = AnalysisRun.objects.count()

    resp = client.post(f"/deals/{deal.pk}/assumptions/preview/", {
        "competitive_supply_sf_3mi": "300000",
        "population_3mi": "75000",
        "ttm_total_revenue": "560000", "ttm_total_expenses": "220000",
        "ttm_noi": "340000",
    })
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "7.0" in html                     # (300k+0+50k)/75k SF/capita
    assert 'id="model-strip"' in html
    assert 'id="noi-chip"' in html
    # preview must never write
    assert AnalysisRun.objects.count() == runs_before
    deal.refresh_from_db()
    assert deal.assumption_overrides is None

    assert client.get(f"/deals/{deal.pk}/assumptions/preview/").status_code == 405
```

- [ ] **Step 3: Run — FAIL (404).**
- [ ] **Step 4: Implement**

`webapp/services.py`:

```python
def build_preview_cim(deal, cleaned, post):
    """Merged cim_data + overrides for PREVIEW ONLY — never persisted."""
    from webapp.forms import build_overrides
    cim = cim_from_dict(deal.cim_json or {})
    ov = build_overrides(cleaned, post, deal)
    _apply_overrides(cim, dict(ov.get("cim_overrides", {})))
    return cim, ov


def model_strip_context(deal, cim, fin, form):
    from analysis.filters import sf_per_capita
    from webapp.forms import noi_recon_tolerance
    spc, spc_problem = sf_per_capita(cim)
    rev, exp, noi = cim.ttm_total_revenue, cim.ttm_total_expenses, cim.ttm_noi
    if None not in (rev, exp, noi):
        delta = round(rev - exp - noi, 2)
        noi_state = ("ok" if abs(delta) <= noi_recon_tolerance(rev)
                     else f"off by ${abs(delta):,.0f}")
    else:
        noi_state = "—"
    import config as cfg
    return {
        "deal": deal,
        "population_3mi": cim.population_3mi,
        "median_hhi_3mi": cim.median_hhi_3mi,
        "sf_per_capita": spc, "sf_per_capita_problem": spc_problem,
        "sf_per_capita_limit": cfg.GATES["max_sf_per_capita"],
        "noi_state": noi_state,
        "expense_lines": (fin.get("expense_analysis") or {}).get("lines", []),
    }
```

(Verify the exact lines-list location in `analyze_financials`'s return at
implementation time and use that key.)

`webapp/views.py`:

```python
@login_required
@require_POST
def assumptions_preview(request, pk):
    deal = get_object_or_404(Deal, pk=pk)
    form = assumptions_forms.AssumptionsForm(request.POST)
    form.is_valid()   # populate cleaned_data; preview shows, never blocks
    cleaned = getattr(form, "cleaned_data", None) or {}
    cim, ov = services.build_preview_cim(deal, cleaned, request.POST)
    from analysis.financials import analyze_financials
    try:
        fin = analyze_financials(
            cim, expense_line_overrides=ov.get("expense_line_overrides"))
    except Exception:
        logger.exception("preview financials failed")
        fin = {}
    return render(request, "webapp/_model_preview.html",
                  services.model_strip_context(deal, cim, fin, form))
```

`cimweb/urls.py`: `path("deals/<int:pk>/assumptions/preview/",
views.assumptions_preview, name="deal-assumptions-preview"),`

`webapp/templates/webapp/_model_preview.html` (strip + OOB cells):

```html
<div id="model-strip" class="grid grid-cols-2 sm:grid-cols-4 gap-2 text-sm">
  <div><span class="text-xs text-slate-500 block">Population 3-mi</span>
    <span class="font-semibold">{{ population_3mi|default:"—" }}</span></div>
  <div><span class="text-xs text-slate-500 block">SF / capita (≤ {{ sf_per_capita_limit }})</span>
    {% if sf_per_capita is not None %}
      <span class="font-semibold {% if sf_per_capita > sf_per_capita_limit %}text-red-700{% else %}text-emerald-700{% endif %}">{{ sf_per_capita|floatformat:1 }}</span>
    {% else %}<span class="text-slate-400" title="{{ sf_per_capita_problem }}">—</span>{% endif %}</div>
  <div><span class="text-xs text-slate-500 block">Median HHI 3-mi</span>
    <span class="font-semibold">{{ median_hhi_3mi|default:"—" }}</span></div>
  <div><span class="text-xs text-slate-500 block">Rev − Exp = NOI</span>
    <span id="noi-chip" class="font-semibold {% if noi_state != 'ok' and noi_state != '—' %}text-amber-700{% endif %}">{{ noi_state }}</span></div>
</div>
{% for l in expense_lines %}
<span id="exp-used-{{ l.benchmark_key }}" hx-swap-oob="true">${{ l.adjusted_value|floatformat:0 }}</span>
<span id="exp-flag-{{ l.benchmark_key }}" hx-swap-oob="true">{{ l.flag|default:"" }}</span>
{% endfor %}
```

- [ ] **Step 5: Endpoint test PASS; full suite green.**
- [ ] **Step 6: Commit** `feat(model-view): server-computed preview endpoint + shared SF/capita helper (T6)`

---

### Task 7: Template rebuild — the vertical model view

**Files:**
- Modify: `webapp/templates/webapp/assumptions.html` (full-body rewrite inside the existing `<form>`)
- Modify: `webapp/forms.py` (`model_rows` helper), `webapp/views.py`
  (context: snapshot + expense-row seed + initial strip), `webapp/services.py`
  (`expense_benchmark_rows` gains cim/analyst/used columns — extend, don't fork)
- Test: `tests/test_web_analyze.py` (render smoke)

**Interfaces:**
- Consumes: everything above; existing `section_fields`, `scenario_grid`,
  `va_grid`, `rc_grid`, unit-mix partial; PR #13 sticky bar (kept
  byte-identical); `_model_preview.html` include for first paint.
- Produces: the final page. Form gets
  `hx-post="{% url 'deal-assumptions-preview' deal.pk %}"
  hx-trigger="change delay:400ms" hx-target="#model-strip"
  hx-swap="outerHTML"` — inputs stay OUTSIDE the swap targets (only
  `#model-strip`, the OOB `#exp-used-*`/`#exp-flag-*` spans, and
  `#noi-chip` re-render), so focus is never stolen mid-edit.

- [ ] **Step 1: Write the failing render-smoke test**

```python
@pytest.mark.django_db
def test_model_view_renders_all_regions(client, django_user_model, settings, tmp_path):
    settings.CIM_DEALS_DIR = str(tmp_path)
    user = django_user_model.objects.create_user(username="op", password="x")
    client.force_login(user)
    from webapp.models import Deal
    deal = Deal.objects.create(
        deal_id="mv", property_name="MV", extract_status="done",
        cim_json={"property_name": "MV", "state": "TX", "nrsf": 50_000.0,
                  "expense_lines": [], "population_3mi": 75_000})
    resp = client.get(f"/deals/{deal.pk}/assumptions/")
    html = resp.content.decode()
    assert resp.status_code == 200
    for marker in ('id="model-strip"', "Street Rate ($/SF/mo)",
                   "In-Place Rent ($/SF/mo)", "T3 Annualized Revenue",
                   'name="exp_property_tax"', 'id="exp-used-property_tax"',
                   "hx-post", "Save &amp; Run Analysis"):
        assert marker in html, marker
```

(Adjust the deal-detail URL name/extract-state plumbing to match
`_extract_state` expectations — copy the fixture setup from the existing
assumptions-view tests in `tests/test_web_analyze.py`.)

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement the page** — structure inside the existing form,
  top to bottom (sticky bar block UNCHANGED from PR #13):

1. `{% include "webapp/_model_preview.html" %}` (first paint of strip;
   htmx replaces it thereafter). Views: GET path computes the initial
   context via `services.build_preview_cim(deal, {}, QueryDict(""))` +
   `analyze_financials` on saved state, merged into the render context.
2. Property identity block: `section_fields(form, SECTION_PROPERTY, ...)`
   + `market_verification` — compact 3-col grid, unchanged mechanism.
3. Drivers: new `model_rows(form, pairs, snapshot)` in forms.py:

```python
def model_rows(form, pairs, snapshot, source_log=None):
    """Vertical driver rows: label | extracted (read-only) | input.
    source: 'you' when the bound/initial value differs from snapshot;
    'Census' when the snapshot value was tier-2 enrichment (extract-time
    enrichment runs BEFORE the snapshot is saved, so Census fills live
    inside cim_json — the run payload's enrichment.source_log is the
    only way to tell them from CIM-extracted values)."""
    source_log = source_log or {}
    rows = []
    for name, label in pairs:
        snap = snapshot.get(name)
        bf = form[name]
        cur = bf.value()
        if cur not in (None, "", snap):
            src = "you"
        elif snap is not None:
            src = ("Census" if source_log.get(name, {}).get("tier") == 2
                   else "CIM")
        else:
            src = ""
        rows.append({"label": label, "bf": bf, "extracted": snap,
                     "source": src})
    return rows
```

Views pass `source_log` from the latest run's payload when present:
`(deal.runs.first().result_json or {}).get("enrichment") or {}` →
`.get("source_log", {})` (guard `deal.runs.first()` being None).

   Driver pairs list (new constant `SECTION_DRIVERS` replacing the
   Task-1 temporary placement): asking_price, capex_estimate,
   physical_occupancy, economic_occupancy, market_rent_psf ("Street
   Rate"), in_place_avg_rent_psf, street_rate_trend,
   t3_annualized_revenue, competitive_supply_sf_3mi,
   pipeline_supply_sf_3mi. Template renders
   `label | extracted (text-slate-400) | {{ row.bf }} | source tag`.
4. Income & Expenses: totals via existing `sec_income` grid + NOI accept
   checkbox (unchanged); expense table — extend
   `services.expense_benchmark_rows(deal)` to return per-category:
   `{key, label, cim_value, cim_per_sf, low, high, used, flag}` (cim from
   snapshot's `_map_expense_lines`, used/flag from the same
   `analyze_financials` call as the strip). Template row:
   `Category | CIM | [exp_<key> input] | <span id="exp-used-<key>"> |
   <span id="exp-flag-<key>">`.
5. Unit-mix table: existing partial untouched.
6. `<details>` (closed by default now): Scenarios / Value-Add /
   Replacement Cost / Solver — existing grids verbatim.

- [ ] **Step 4: Render test PASS; FULL suite green; `manage.py check` clean.**
- [ ] **Step 5: Commit** `feat(model-view): vertical driver-first page with live preview (T7)`

---

### Task 8: Two-pass UI review (independent agents — hard requirement)

- [ ] **Step 1:** Dispatch UI agent #1 (layout/compaction pass): reads
  `assumptions.html` + `_model_preview.html` + `base.html`; checks
  alignment, sticky interactions, htmx target/OOB correctness, focus
  preservation, label/field visibility. Apply its fixes; suite green.
- [ ] **Step 2:** Dispatch a FRESH agent #2 (adversarial density pass,
  zero shared context): hunts wasted space, scroll cost, obscured
  errors, mobile hamburger collisions, readability sacrifices. Apply
  fixes; suite green.
- [ ] **Step 3: Commit** `fix(ui): model view — layout + adversarial density pass repairs (T8)`

---

### Task 9: Ship

- [ ] **Step 1:** Full suite + `python -m py_compile` on every touched
  file; `git diff main --stat` sanity (only planned files).
- [ ] **Step 2:** Adversary review agent on the full branch diff
  (`/review-as adversary` persona, sonnet); repair critical/moderate;
  re-verify if any were found.
- [ ] **Step 3:** Push branch `claude/dense-model-view`; open PR titled
  `feat(web): dense model view — driver-first page, per-line expenses,
  live preview`; body per house format with test-count delta and the
  spec/plan links.
- [ ] **Step 4:** CI green → squash-merge → sync main → delete branch →
  verify deploy (`/health/` git_sha match watcher).
- [ ] **Step 5:** Post-merge: operator walkthrough on Render; then the
  Abilene extraction post-mortem (operator-sequenced next item).
