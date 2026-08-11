"""Optional AI extraction gap-filler (item B1).

After the regex parser runs, this asks an LLM to read the already-extracted
CIM text and return values for the CIMData scalar fields the parser left
missing. DeepSeek by default; any OpenAI-compatible endpoint via config.

The design intent from CLAUDE.md decision #1 ("the parser flags gaps, Claude
fills them from PDF context") existed only as a manual CLI step until now;
this brings it into the web extraction path. Its safety posture is the whole
point of the module:

  * REQUIRED underwriting fields are NEVER AI-filled. `asking_price`, `nrsf`,
    `total_units`, `ttm_noi`, `physical_occupancy`, `state`, `ttm_egr` are
    absent from AI_EXTRACTABLE_FIELDS by construction — an AI value for one
    of them would satisfy `analysis.fills.require_underwritable` and strip the
    assumptions page's red required-flag, re-opening the exact hole design
    decision #9 closed. `tests/test_ai_fill.py` pins the exclusion against the
    live REQUIRED sets.
  * FILL-ONLY-MISSING is the CALLER's contract (see engine.extract_pdf_data):
    a returned value is applied only to a field that is still None. AI never
    overrides a regex-parsed, JSON-override, or analyst value.
  * EXTRACT, DON'T ASSUME. The prompt demands a value only when the CIM states
    it, null otherwise — no inference. Model output is untrusted input.
  * PER-FIELD CANONICAL-UNIT COERCION + BOUNDS. Every value is coerced to the
    field's storage unit (decimal fractions, dollars, $/SF/month) and rejected
    outside a sane range, so an "85" meant as 85% or a price quoted in millions
    is dropped, not stored.
  * DEGRADE SILENTLY. A hard client timeout and a single attempt (no retries);
    ANY failure returns no values so the regex-only result stands. This module
    never raises to its caller, and never logs prompt or response bodies —
    they carry confidential CIM text.
"""
import json
import logging

import config as cfg

logger = logging.getLogger("cim_analyst.ai_fill")


# ── Per-field coercers (to canonical storage units) ──────────────────
def _fraction(v):
    """A ratio stored as a 0–1 decimal (occupancy, cc_pct, mgmt fee).

    A model that answers "85" for 85% is AMBIGUOUS, not off-by-100 — we
    refuse it (return None) rather than guess /100 and store 0.85 the CIM
    never stated. The bounds check below then only sees genuine fractions.
    """
    f = float(v)
    return None if f > 1.5 else f


def _dollars(v):
    return float(v)


def _int_count(v):
    return int(float(v))


def _year(v):
    return int(float(v))


def _text(v):
    s = str(v).strip()
    return s or None


#: Every AI-extractable field: name -> (description-with-units, coercer,
#: (lo, hi) canonical bounds or None). REQUIRED underwriting fields, the
#: analyst-only screening inputs, and the list/table fields are all absent
#: by design — see the module docstring and the exclusion test.
AI_EXTRACTABLE_FIELDS = {
    "property_name": ("The property/facility name.", _text, None),
    "address": ("Street address of the property.", _text, None),
    "city": ("City the property is in.", _text, None),
    "msa": ("Metropolitan Statistical Area name, if stated.", _text, None),
    "year_built": ("Year the facility was originally built (4-digit year).",
                   _year, (1900, 2035)),
    "year_expanded": ("Year of the most recent expansion (4-digit year).",
                      _year, (1900, 2035)),
    "acreage": ("Site size in acres.", _dollars, (0.0, 5000.0)),
    "cc_sf": ("Climate-controlled net rentable square feet.",
              _dollars, (0.0, 5_000_000.0)),
    "non_cc_sf": ("Non-climate-controlled net rentable square feet.",
                  _dollars, (0.0, 5_000_000.0)),
    "cc_pct": ("Share of NRSF that is climate-controlled, as a decimal "
               "fraction 0–1 (e.g. 0.35 for 35%).", _fraction, (0.0, 1.0)),
    "economic_occupancy": ("Economic occupancy as a decimal fraction 0–1 "
                           "(e.g. 0.88 for 88%).", _fraction, (0.0, 1.0)),
    "price_per_sf": ("Asking price per net rentable SF, in dollars.",
                     _dollars, (0.0, 10_000.0)),
    "population_1mi": ("Population within a 1-mile radius (a count).",
                       _int_count, (0, 50_000_000)),
    "population_3mi": ("Population within a 3-mile radius (a count).",
                       _int_count, (0, 50_000_000)),
    "population_5mi": ("Population within a 5-mile radius (a count).",
                       _int_count, (0, 50_000_000)),
    "median_hhi_3mi": ("Median household income within 3 miles, in dollars.",
                       _dollars, (0.0, 1_000_000.0)),
    "ttm_gpr": ("Trailing-12-month gross potential rent, in dollars/year.",
                _dollars, (0.0, 1e9)),
    "ttm_total_revenue": ("Trailing-12-month total revenue, in dollars/year.",
                          _dollars, (0.0, 1e9)),
    "ttm_total_expenses": ("Trailing-12-month total operating expenses, in "
                           "dollars/year.", _dollars, (0.0, 1e9)),
    "cim_yr1_noi": ("The CIM's pro-forma Year 1 NOI, in dollars/year.",
                    _dollars, (0.0, 1e9)),
    "other_income": ("Annual other/ancillary income, in dollars/year.",
                     _dollars, (0.0, 1e9)),
    "mgmt_fee_pct": ("Management fee as a decimal fraction of revenue 0–1 "
                     "(e.g. 0.05 for 5%).", _fraction, (0.0, 1.0)),
    "capex_estimate": ("Estimated capital expenditure / deferred maintenance, "
                       "in dollars.", _dollars, (0.0, 1e9)),
    "market_rent_psf": ("Market asking rent in dollars per SF per MONTH "
                        "(not per year).", _dollars, (0.0, 10.0)),
    "new_supply_mentions": ("Any text describing new competing supply in the "
                            "market.", _text, None),
}

_SYSTEM_PROMPT = (
    "You extract structured data from commercial real-estate offering "
    "memoranda (CIMs) for self-storage properties. Return ONLY values that "
    "are EXPLICITLY STATED in the provided text. If a value is not stated, "
    "return null for that field. Never infer, estimate, calculate, or guess. "
    "Respond with a single JSON object and nothing else."
)


def _coerce(spec, value):
    """Canonical-unit value, or None if coercion or bounds fail."""
    _desc, coercer, bounds = spec
    try:
        v = coercer(value)
    except (TypeError, ValueError):
        return None
    if v is None:
        return None
    if bounds is not None:
        lo, hi = bounds
        if not (lo <= v <= hi):
            return None
    return v


def _build_user_prompt(text, fields):
    lines = [
        "Extract these fields from the CIM text below. Units matter — follow "
        "each field's description exactly. Return a JSON object mapping every "
        "field name to its value, or null when the CIM does not state it.",
        "",
    ]
    for name in fields:
        lines.append(f"- {name}: {AI_EXTRACTABLE_FIELDS[name][0]}")
    lines += ["", "CIM TEXT:", text]
    return "\n".join(lines)


def _call_llm(system, user):
    """Single, timeout-bounded completion → raw content string.

    Isolated so tests monkeypatch it and never import `openai` or hit the
    network. Constructs the client lazily so `openai` is only needed when the
    feature is actually enabled.
    """
    from openai import OpenAI  # lazy: optional dependency, only when enabled

    client = OpenAI(
        api_key=cfg.DEEPSEEK_API_KEY,
        base_url=cfg.AI_EXTRACTION_BASE_URL,
        timeout=cfg.AI_EXTRACTION_TIMEOUT_SECONDS,
        max_retries=0,
    )
    resp = client.chat.completions.create(
        model=cfg.AI_EXTRACTION_MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content


def ai_fill_missing(raw, missing):
    """Ask the LLM for the AI-extractable subset of `missing`.

    Args:
        raw: the dict from extract.pdf_reader.extract_pdf (uses raw["text"]).
        missing: field names the parser left unresolved.

    Returns:
        (values, error): `values` maps field name -> coerced canonical value
        for fields the model supplied and that passed coercion/bounds; `error`
        is a short reason string when the pass could not run (never contains
        CIM content), else None. Never raises.
    """
    fields = [f for f in missing if f in AI_EXTRACTABLE_FIELDS]
    if not fields:
        return {}, None
    if not cfg.DEEPSEEK_API_KEY:
        return {}, "no API key configured"

    text = (raw.get("text") or "")[: cfg.AI_EXTRACTION_MAX_INPUT_CHARS]
    if not text.strip():
        return {}, "no extractable text (scanned PDF?)"

    try:
        content = _call_llm(_SYSTEM_PROMPT, _build_user_prompt(text, fields))
        data = json.loads(content)
        if not isinstance(data, dict):
            return {}, "model did not return a JSON object"
    except Exception as e:  # network, timeout, bad JSON, SDK error — all soft
        # Log the TYPE only; the message could echo prompt/response content.
        logger.warning("AI extraction pass failed: %s", type(e).__name__)
        return {}, f"AI extraction unavailable ({type(e).__name__})"

    values = {}
    for name in fields:
        if data.get(name) is None:
            continue
        coerced = _coerce(AI_EXTRACTABLE_FIELDS[name], data[name])
        if coerced is not None:
            values[name] = coerced
    return values, None
