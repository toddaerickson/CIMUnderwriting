"""Display-context builders over AnalysisRun.result_json.

Pure functions: result_json (plain dict) in, template-ready contexts of
preformatted strings out. Percent decimals become display strings HERE
and nowhere else. Templates stay dumb; formatting stays testable.
"""

from itertools import zip_longest

import config as cfg
from model.debt import binding_constraint_label, displayed_rate

#: Bump when a run begins persisting a payload key that one of the results
#: surfaces renders a whole BLOCK from. `webapp.services` stamps it on
#: every payload it writes; a run without it predates the stamp entirely.
#:
#: This exists because "the block is missing" and "the run is old" are
#: different facts and only the second one is actionable. Every gated
#: block below is `{% if %}`-ed on its own payload key, which is correct —
#: `levered_context` degrades to `has_levered: False` rather than a table
#: of N/A, and `has_va_max_offer` hides a card the deal genuinely has no
#: answer for. The consequence is that a run stored before those features
#: renders as a COMPLETE page that is quietly missing four sections, with
#: nothing on screen saying so. A QA pass (2026-08-14) read deal 2's
#: Summary tab, found two of its six blocks, and filed the four absences
#: as a rendering defect; they were a 2026-07-28 run predating every one
#: of them.
#:
#: Keyed on the version and NOT on absence alone, because absence is
#: legitimate on a current run: `levered` is empty when the covenants
#: sized no loan, `va_results` when there is no value-add case. A banner
#: that fired on those would be crying wolf on healthy deals, which is
#: how a caveat stops being read.
RESULT_PAYLOAD_VERSION = 1

#: payload key -> the section heading a reader would go looking for.
#: Labels match the on-screen headings, so "Capital" sends someone to the
#: block called Capital rather than to a key name only this file uses.
_VERSIONED_BLOCKS = (
    ("checks", "Model Checks"),
    ("sources_uses", "Capital (Sources & Uses)"),
    ("assumption_fill_log", "Assumptions Filled"),
    ("assumption_register", "Assumption Register"),
    ("levered", "Levered Returns (LP Net)"),
)


def legacy_context(r) -> dict:
    """Does this stored run predate blocks the page would otherwise show?

    Returns nothing to render when the payload carries a current version —
    a modern run missing `levered` priced no loan, and that is reported by
    the Returns tab in its own words, not as staleness.
    """
    if (r.get("payload_version") or 0) >= RESULT_PAYLOAD_VERSION:
        return {"run_is_legacy": False}
    missing = [label for key, label in _VERSIONED_BLOCKS if not r.get(key)]
    # An unversioned run that happens to carry every block gets no banner:
    # there is nothing to warn a reader about, and the version alone is
    # bookkeeping they cannot act on.
    return {"run_is_legacy": bool(missing), "legacy_missing": missing}


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


def fill_log_context(r) -> dict:
    """Assumption fill log → template rows (item T Category 4).

    Sibling of `checks_context` and deliberately its own panel, not a
    line in the amber `run_warnings` banner: that banner is a flat list
    of strings for things the run REFUSED, and flattening a three-column
    provenance table into bullets on all four tabs would lose the two
    columns that make it auditable.

    Ordering comes from `fills.collect`, which sorted by source key when
    the run was recorded. Re-sorting here would give a stored run and a
    live one two different orders for the same log.
    """
    from analysis import fills

    rows = fills.from_dicts(r.get("assumption_fill_log") or [])
    return {"fill_rows": [{"field": f.field,
                           "value": fills.format_value(f),
                           "source": f.source_label,
                           "label": f.label} for f in rows]}


def register_context(r) -> dict:
    """Assumption register → template rows (item T Category 6).

    Third sibling of `checks_context` and `fill_log_context`, and the
    widest of the three: the check register asks whether the inputs are
    self-consistent, the fill log which of them the CIM never supplied,
    and this one where EVERY number came from.

    Two row lists off one register rather than two passes over the data,
    because they must never disagree about which rows are which. Ordering
    comes from `assumptions.collect`, which grouped them when the run was
    recorded — re-sorting here would give a stored run and a live one two
    different orders for the same register.
    """
    import dataclasses

    from analysis import assumptions

    rows = assumptions.from_dicts(r.get("assumption_register") or [])

    def _row(a):
        return {"key": a.key, "group": a.group, "label": a.label,
                "value": assumptions.format_value(a),
                "source": a.provenance_label,
                "chosen": a.chosen,
                # `was` renders through the SAME formatter as `value`, by
                # swapping the field on a copy: it is the same quantity in
                # the same unit, and a second rendering path is how one of
                # them ends up printing 0.8 where the other prints 80%.
                "was": (assumptions.format_value(
                    dataclasses.replace(a, value=a.was))
                    if a.was is not None else ""),
                "detail": a.detail}

    all_rows = [_row(a) for a in rows]
    return {"register_rows": all_rows,
            "register_chosen": [row for row in all_rows if row["chosen"]],
            "register_counts": assumptions.summarize(rows)}


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


def capital_context(r) -> dict:
    """Sources & Uses for the summary tab's Capital block.

    Uses and Sources render as ONE two-column table rather than two
    stacked ones — they are the same total read two ways, and stacking
    them costs a full block of vertical space to say so. Rows are zipped
    to the longer side, so adding a debt tranche in item E1 does not need
    a template change.
    """
    su = r.get("sources_uses") or {}
    if not su:
        return {"has_capital": False}
    uses = su.get("uses") or []
    sources = su.get("sources") or []

    def cell(line, total):
        if line is None:
            return None
        amount = line.get("amount")
        # A $0 line is kept, not dropped — "no reserve was underwritten"
        # and "there is no reserve row" are different statements, and the
        # second one is how an omission goes unnoticed. It is rendered
        # quietly instead, so the eye skips it without the fact leaving
        # the page. In the default all-equity case that is three of the
        # five Uses rows, so it is the difference between a dense block
        # and a wall of zeros.
        return {"label": line.get("label"), "amount": fmt_money(amount),
                "share": fmt_pct((amount / total) if total else None),
                "zero": not amount}

    total_uses = su.get("total_uses") or 0
    total_sources = su.get("total_sources") or 0
    rows = [{"use": cell(u, total_uses), "source": cell(s, total_sources)}
            for u, s in zip_longest(uses, sources)]
    return {
        "has_capital": True,
        "capital_rows": rows,
        "total_uses": fmt_money(total_uses),
        "total_sources": fmt_money(total_sources),
        "total_equity": fmt_money(su.get("total_equity")),
        "gp_equity": fmt_money(su.get("gp_equity")),
        "lp_equity": fmt_money(su.get("lp_equity")),
        "gp_coinvest_pct": fmt_pct(su.get("gp_coinvest_pct"), digits=0),
        "capital_balanced": bool(su.get("balanced")),
        "capital_delta": fmt_money(abs(su.get("delta") or 0)),
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
        "repl_bridge": _replacement_bridge(repl),
        "repl_total": fmt_money(repl.get("total_replacement")),
        # None, not "N/A" — the template hangs "/SF" off this and a run
        # with no NRSF would otherwise read "N/A/SF".
        "repl_per_sf": (fmt_money(repl["replacement_per_sf"])
                        if repl.get("replacement_per_sf") is not None
                        else None),
        "repl_delta_label": delta_label, "repl_delta": delta,
    }


def _replacement_bridge(repl: dict) -> list[dict]:
    """Hard cost → total replacement, one row per addend.

    The table above lists hard cost per facility type and the line under
    it printed the total, with site work, soft costs and the developer's
    profit crossed silently in between — on the QA deal a 38.8% jump the
    page never accounted for. Asking price is screened against that
    total (`analysis.physical._compare_to_asking`), so the gap sat under
    a gate with nothing on screen explaining it.

    A row is emitted only for a component the stored run actually
    carries. Runs predating the facility-type build-up have no
    `site_work`/`soft_costs`, and a bridge of four `N/A`s would disclose
    less than the single total it replaced. The percentages are the
    resolved midpoints the run used, not the config band — the band is
    two numbers and only one of them priced this deal.
    """
    rows = []
    for key, label, pct_key in (
            ("hard_cost", "Hard Cost (all facility types)", None),
            ("site_work", "Site Work", None),
            ("subtotal", "Subtotal — Hard + Site", None),
            ("soft_costs", "Soft Costs", "soft_cost_pct"),
            ("dev_profit", "Developer Profit", "dev_profit_pct")):
        value = repl.get(key)
        if value is None:
            continue
        pct = repl.get(pct_key) if pct_key else None
        rows.append({
            "label": label if pct is None else f"{label} @ {fmt_pct(pct)}",
            "value": fmt_money(value),
            # The two running totals read as sums of what precedes them,
            # so they carry the weight rather than sitting flush with the
            # addends.
            "is_subtotal": key == "subtotal",
        })
    return rows


def _hold_years(scenarios: dict, noi_key: str = "noi_projection") -> int:
    """Hold length a stored run was computed on, read off the run itself.

    Runs predating item B carry neither key; they were always five years,
    which is what config.DEFAULT_HOLD_YEARS still is. Read, don't assume:
    the fallback tracks the config value rather than repeating a literal.
    """
    for scen in (scenarios or {}).values():
        if isinstance(scen, dict):
            hold = scen.get("hold_years") or len(scen.get(noi_key) or [])
            if hold:
                return hold
    return cfg.DEFAULT_HOLD_YEARS


def _unconverged(solved) -> bool:
    """True when a solver produced a price it never actually converged on.

    Runs predating the flag carry no `converged` key; those are read as
    converged rather than retroactively flagged, since the absence is a
    missing field, not evidence of a failure.
    """
    if not solved or solved.get("max_price") is None:
        return False
    return solved.get("converged") is False


def returns_context(r) -> dict:
    scen = r.get("scenario_results") or {}
    base = scen.get("base") or {}
    va = r.get("va_results") or {}
    sens = r.get("sensitivity") or {}
    sens_rows = []
    prices = sens.get("price_values") or []
    for i, row in enumerate(sens.get("irr_grid") or []):
        price = prices[i] if i < len(prices) else None
        sens_rows.append({"price": fmt_money(price),
                          "cells": [fmt_pct(v) for v in row]})
    # The hold is variable, so these labels cannot say "5-Year". An
    # analyst comparing two deals underwritten at different holds would
    # otherwise misread the annualization basis straight off the primary
    # results screen (review finding, item B).
    hold = _hold_years(scen)
    va_hold = _hold_years(va, noi_key="annual_noi")
    return {
        "scenario_rows": _metric_rows(scen, [
            ("Yr1 Yield on Cost", "yield_on_cost", fmt_pct),
            (f"{hold}-Year MOIC", "moic", fmt_x),
            (f"{hold}-Year IRR", "irr", fmt_pct),
        ]),
        "has_va": bool(va),
        "va_rows": _metric_rows(va, [
            ("Stabilized Yield on Cost", "yield_on_cost", fmt_pct),
            (f"{va_hold}-Year MOIC", "moic", fmt_x),
            (f"{va_hold}-Year IRR", "irr", fmt_pct),
            ("Development Spread", "development_spread",
             lambda v: f"{float(v) * 10000:,.0f} bps" if v is not None else "N/A"),
            ("Stabilized NOI", "stabilized_noi", fmt_money),
        ]),
        "max_offer": fmt_money((r.get("max_offer") or {}).get("max_price")),
        # The bisection returns a price whatever happens; `converged` is
        # the only thing separating an answer from a bound it never got
        # away from. It reached the Excel tab but nothing on the web page,
        # so a non-answer read exactly like an answer. Item D adds an
        # unbounded reserve dial, which makes an unreachable target IRR
        # materially easier to hit than transaction costs alone could.
        "max_offer_unconverged": _unconverged(r.get("max_offer")),
        "va_max_offer_unconverged": _unconverged(r.get("va_max_offer")),
        "va_max_offer": fmt_money((r.get("va_max_offer") or {}).get("max_price")),
        "has_va_max_offer": bool((r.get("va_max_offer") or {}).get("max_price")),
        # The levered max offer (item E4) — a pure read, like every other
        # figure here. Absent on runs stored before E4 and on deals that
        # priced no loan, so the card is gated rather than showing N/A.
        "levered_max_offer": fmt_money(
            (r.get("levered_max_offer") or {}).get("max_price")),
        "has_levered_max_offer": bool(
            (r.get("levered_max_offer") or {}).get("max_price")),
        "levered_max_offer_unconverged": _unconverged(
            r.get("levered_max_offer")),
        # The target is shown on the card because it is NOT the unlevered
        # target and a reader comparing two prices must be able to see
        # they were solved to different bars.
        "levered_max_offer_target": fmt_pct(
            (r.get("levered_max_offer") or {}).get("target_irr")),
        # Only the observed-inversion flag reaches the UI. `coerced_region`
        # is ordinary and fires on most deals; caveating on it would train
        # the reader to ignore the badge (see model/solver.py).
        "levered_max_offer_warning": (
            r.get("levered_max_offer") or {}).get("monotonicity_warning"),
        "has_sensitivity": bool(sens.get("irr_grid")),
        "sens_caps": [fmt_pct(c, digits=2) for c in sens.get("cap_values") or []],
        "sens_rows": sens_rows,
        # The grid sweeps exit caps WITHOUT the entry-cap floor that
        # `project_cash_flows` applies — deliberately, because coercing
        # here would raise every cell below the entry cap to it and
        # flatten the axis the table exists to show
        # (model.returns_model._build_sensitivity says so).
        #
        # The consequence only bites when the base case WAS floored: the
        # two surfaces are then priced at different exit caps, the grid
        # cannot reconcile to the headline IRR at any cell, and a reader
        # comparing the two reasonably concludes the grid is broken. A QA
        # pass did exactly that (2026-08-14) and filed it as a defect in
        # the grid; it is neither surface being wrong, it is one input
        # producing an entry cap the floor then binds on.
        #
        # Reported HERE as well as in the check register, which already
        # carries `exit_cap_coercion`: that register renders on the
        # Summary tab, and the person comparing a tile to a table is
        # standing on Returns.
        "sens_base_coerced": bool(base.get("exit_cap_coerced")),
        "sens_requested_cap": fmt_pct(base.get("requested_exit_cap"),
                                      digits=2),
        "sens_applied_cap": fmt_pct(base.get("exit_cap"), digits=2),
    }


def levered_context(r) -> dict:
    """The levered second lens (item E3b) — a pure read of what the run
    already persisted at `debt` and `levered`.

    NOTHING is recomputed here. The LP net IRR belongs to the assumption
    set stamped with the run; a figure re-derived today against whatever
    config says now is a different number wearing the run's date.

    Absent on a run that never priced a loan (no NOI or no asking price)
    and on every run stored before item E3a, which is why the whole block
    is gated on `has_levered` rather than degrading into a table of N/A.
    """
    debt = r.get("debt") or {}
    levered = r.get("levered") or {}
    base = levered.get("base") or {}
    if not levered or not base:
        return {"has_levered": False}

    scen = r.get("scenario_results") or {}
    terms = debt.get("terms") or {}
    hold = _hold_years(scen)

    def cell(sc, key, fmt):
        return fmt((levered.get(sc) or {}).get(key))

    # Leverage is allowed to be DILUTIVE, and on this repo's config
    # defaults it frequently is — the ~7.4% loan constant sits above a
    # typical yield on cost. A reader who assumes a levered number must
    # beat its unlevered one reads that as a bug, so the page says it
    # first. Compared per scenario against the unlevered IRR the same
    # scenario published.
    dilutive = []
    for sc in SCENARIOS:
        lp = (levered.get(sc) or {}).get("lp_net_irr")
        unl = (scen.get(sc) or {}).get("irr")
        if lp is not None and unl is not None and lp < unl:
            dilutive.append(sc.title())

    return {
        "has_levered": True,
        "levered_rows": [
            {"label": f"{hold}-Year LP Net IRR",
             "cells": [cell(sc, "lp_net_irr", fmt_pct) for sc in SCENARIOS]},
            {"label": f"{hold}-Year LP MOIC",
             "cells": [cell(sc, "lp_moic", fmt_x) for sc in SCENARIOS]},
            {"label": "GP Promote",
             "cells": [cell(sc, "gp_promote", fmt_money) for sc in SCENARIOS]},
            {"label": "AM Fee (total)",
             "cells": [cell(sc, "am_fee_total", fmt_money) for sc in SCENARIOS]},
        ],
        # One loan, sized once off the base case and carried through all
        # three scenarios — so this strip is scenario-independent and says
        # so rather than being repeated three times.
        "loan_rows": [
            ("Loan Amount", fmt_money(debt.get("loan"))),
            ("Bound By", binding_constraint_label(debt)),
            ("All-In Rate", fmt_pct(displayed_rate(terms), digits=2)),
            ("Amortization", _years(terms.get("amort_years"))),
            ("Interest-Only", _months(terms.get("io_months"))),
            ("Loan Term", _years(terms.get("term_years"))),
            ("LTV", fmt_pct(debt.get("ltv"))),
            ("Year-1 DSCR", fmt_x(debt.get("dscr_year_1"))),
            ("Debt Yield", fmt_pct(debt.get("debt_yield"))),
            ("Origination Fee", fmt_money(debt.get("origination_fee"))),
            ("Payoff at Exit", fmt_money(debt.get("payoff_balance"))),
            ("Equity Required", fmt_money(base.get("total_equity"))),
        ],
        "levered_years": [
            {"year": row.get("year"),
             "noi": fmt_money(row.get("noi")),
             "debt_service": fmt_money(row.get("debt_service")),
             "am_fee": fmt_money(row.get("am_fee")),
             "levered_cf": fmt_money(row.get("levered_cf")),
             "distribution": fmt_money(row.get("distribution")),
             "capital_call": fmt_money(row.get("capital_call")),
             "dscr": fmt_x(row.get("dscr"))}
            for row in base.get("years") or []],
        # The scope contract's rule: no LP net IRR leaves the building
        # without its stamp. Each row changes the number, and the rows
        # carry their own confirmed/moot/open state — do not restate a
        # count here, it goes stale the day the operator reads a clause.
        "levered_stamp": [{"question": row.get("question"),
                           "label": row.get("label")}
                          for row in base.get("assumption_stamp") or []],
        "levered_called_capital": bool(base.get("called_capital_after_close")),
        "levered_capital_calls": fmt_money(base.get("capital_calls_total")),
        "levered_reserve_drawn": fmt_money(base.get("reserve_drawn_total")),
        "levered_matures_early": bool(debt.get("matures_before_exit")),
        "levered_dilutive": ", ".join(dilutive),
        "levered_no_loan": not (debt.get("loan") or 0),
        # Promote the GP keeps that a clawback would have recovered.
        # Zero whenever the LP ends whole, which is the common case —
        # shown only when it is not.
        "levered_unrecovered_promote": fmt_money(
            (base.get("waterfall") or {}).get("unrecovered_promote")),
        "levered_has_unrecovered_promote": bool(
            (base.get("waterfall") or {}).get("unrecovered_promote")),
    }


def _years(v) -> str:
    return f"{int(v)} yrs" if v else "N/A"


def _months(v) -> str:
    """0 is a real answer here — 'no IO period' — not a missing one."""
    return f"{int(v)} mos" if v is not None else "N/A"


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
