"""Display-context builders over AnalysisRun.result_json.

Pure functions: result_json (plain dict) in, template-ready contexts of
preformatted strings out. Percent decimals become display strings HERE
and nowhere else. Templates stay dumb; formatting stays testable.
"""


def fmt_pct(v, digits=1):
    return f"{float(v) * 100:.{digits}f}%" if v is not None else "N/A"


def fmt_money(v):
    return f"${float(v):,.0f}" if v is not None else "N/A"


def fmt_x(v):
    return f"{float(v):.2f}x" if v is not None else "N/A"


SCENARIOS = ("bear", "base", "bull")


# ── Model error-check register (analysis/checks.py) ─────────────────

#: status/severity → template tone token. One mapping for the assumptions
#: strip, the results tab and anything else that renders the register, so a
#: blocking failure can never read as an advisory one on a different page.
_CHECK_TONES = {("fail", "blocking"): "fail", ("fail", "advisory"): "warn",
                ("pass", "blocking"): "ok", ("pass", "advisory"): "ok"}


def check_tone(status: str, severity: str) -> str:
    return _CHECK_TONES.get((status, severity), "skip")


def check_rows(results) -> list[dict]:
    """CheckResult objects → template rows. Accepts the dataclasses (live
    preview) or the stored dicts from result_json (results page)."""
    from analysis import checks

    if results and isinstance(results[0], dict):
        results = checks.from_dicts(results)
    return [{"id": r.id, "label": r.label, "severity": r.severity,
             "status": r.status, "message": r.message, "values": r.values,
             "source": r.source, "tone": check_tone(r.status, r.severity)}
            for r in results or []]


#: register display order — findings first, then verified, then the ones
#: that were not testable. A register read top-down should hit the
#: problems first.
_CHECK_ORDER = {"fail": 0, "pass": 1, "skipped": 2}


def checks_context(r) -> dict:
    rows = sorted(check_rows(r.get("checks") or []),
                  key=lambda row: (_CHECK_ORDER.get(row["status"], 3),
                                   row["label"]))
    return {"check_rows": rows,
            "flagged_checks": [row for row in rows if row["status"] == "fail"],
            "check_summary": r.get("check_summary") or {}}


def _metric_rows(block, metrics):
    rows = []
    for label, key, fmt in metrics:
        rows.append({"label": label,
                     "cells": [fmt((block.get(sc) or {}).get(key))
                               for sc in SCENARIOS]})
    return rows


def header_metrics(deal, r) -> dict:
    base = (r.get("scenario_results") or {}).get("base") or {}
    max_price = (r.get("max_offer") or {}).get("max_price")
    asking = deal.asking_price
    discount = None
    if max_price and asking:
        discount = (asking - max_price) / asking
    return {
        "recommendation": (r.get("gate_summary") or {}).get("recommendation", "N/A"),
        "base_irr": fmt_pct(base.get("irr")),
        "max_price": fmt_money(max_price),
        "discount_to_asking": fmt_pct(discount),
    }


def summary_context(r) -> dict:
    summary = r.get("gate_summary") or {}
    rec = summary.get("recommendation", "N/A")
    if "DECLINE" in rec:
        tone = "fail"
    elif "CONTINGENT" in rec or rec == "N/A":
        tone = "warn"
    else:
        tone = "pass"
    repl = (r.get("physical_analysis") or {}).get("replacement_cost") or {}
    comp = (r.get("physical_analysis") or {}).get("price_vs_replacement") or {}
    repl_rows = [{"type": td.get("type"), "sf": f"{td.get('sf') or 0:,.0f}",
                  "hard_rate": fmt_money(td.get("hard_rate")),
                  "hard_cost": fmt_money(td.get("hard_cost"))}
                 for td in repl.get("facility_type_details") or []]
    delta_label = delta = None
    if comp.get("comparable"):
        d = comp.get("discount_to_replacement")
        if d is not None:
            delta_label = "Discount to Replacement" if d > 0 else "Premium to Replacement"
            delta = fmt_pct(abs(d))
    return {
        "gate_summary": summary, "rec_tone": tone,
        "gates": r.get("gate_results") or [],
        "repl_estimable": bool(repl.get("estimable")),
        "repl_rows": repl_rows,
        "repl_total": fmt_money(repl.get("total_replacement")),
        "repl_delta_label": delta_label, "repl_delta": delta,
    }


def returns_context(r) -> dict:
    scen = r.get("scenario_results") or {}
    va = r.get("va_results") or {}
    sens = r.get("sensitivity") or {}
    sens_rows = []
    prices = sens.get("price_values") or []
    for i, row in enumerate(sens.get("irr_grid") or []):
        price = prices[i] if i < len(prices) else None
        sens_rows.append({"price": fmt_money(price),
                          "cells": [fmt_pct(v) for v in row]})
    return {
        "scenario_rows": _metric_rows(scen, [
            ("Yr1 Yield on Cost", "yield_on_cost", fmt_pct),
            ("5-Year MOIC", "moic", fmt_x),
            ("5-Year IRR", "irr", fmt_pct),
        ]),
        "has_va": bool(va),
        "va_rows": _metric_rows(va, [
            ("Stabilized Yield on Cost", "yield_on_cost", fmt_pct),
            ("5-Year MOIC", "moic", fmt_x),
            ("5-Year IRR", "irr", fmt_pct),
            ("Development Spread", "development_spread",
             lambda v: f"{float(v) * 10000:,.0f} bps" if v is not None else "N/A"),
            ("Stabilized NOI", "stabilized_noi", fmt_money),
        ]),
        "max_offer": fmt_money((r.get("max_offer") or {}).get("max_price")),
        "va_max_offer": fmt_money((r.get("va_max_offer") or {}).get("max_price")),
        "has_va_max_offer": bool((r.get("va_max_offer") or {}).get("max_price")),
        "has_sensitivity": bool(sens.get("irr_grid")),
        "sens_caps": [fmt_pct(c, digits=2) for c in sens.get("cap_values") or []],
        "sens_rows": sens_rows,
    }


def financials_context(r) -> dict:
    fin = r.get("financial_analysis") or {}
    adj = fin.get("adjusted_ttm_noi") or {}
    adjustments = []
    for a in fin.get("adjustments") or []:
        if isinstance(a, dict):
            adjustments.append(
                f"{a.get('category', '')}: {a.get('flag', '')}".strip(": "))
        else:
            adjustments.append(str(a))
    return {
        "cim_noi": fmt_money(adj.get("cim_ttm_noi")),
        "adj_noi": fmt_money(adj.get("analyst_adjusted_noi")),
        "expense_ratio": fmt_pct(
            (fin.get("expense_ratio_check") or {}).get("opex_revenue_ratio")),
        "adjustments": adjustments,
    }


_SEVERITY_TONE = {"High": "high", "Medium": "medium", "Low": "low"}


def risks_context(r) -> dict:
    """Real risk items (analysis.risks.identify_risks) carry `description`
    and title-case severities ("High"/"Medium"/"Low"). Normalize both here
    so the template only compares against tokens this module controls."""
    items = (r.get("risk_analysis") or {}).get("risks") or []
    rows = []
    for item in items:
        severity = item.get("severity", "Low")
        rows.append({
            "risk": item.get("risk"),
            "severity": severity,
            "severity_tone": _SEVERITY_TONE.get(severity, "low"),
            "detail": item.get("description"),
            "mitigation": item.get("mitigation"),
        })
    return {"risks": rows}
