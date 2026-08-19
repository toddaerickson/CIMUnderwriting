"""Model error-check register — every input-integrity check, in one place.

Pure functions, no Django import, so the identical registry runs from the
assumptions form, the live htmx preview, the engine, the memo and the Excel
writer. A check that lives on only one surface is a check the other four
silently skip — which is how the Abilene CIM's `$1` property-tax line reached
a published model with nothing flagged.

Three statuses, not two. `skipped` means the inputs were absent and the check
never ran; rendering that as a pass would claim we looked when we did not.

Severities:
    blocking  — the assumptions form is invalid unless the analyst accepts
                the finding, which is then recorded in the run's overrides
    advisory  — always shown, never blocks

Units are canonical here (decimal percents, dollars) — the same convention
config.py and the CIM snapshot use, NOT the whole-number percents the
assumptions form posts. Callers converting from form units do it on the way
in; see webapp.forms.check_input_from_cleaned.
"""

from dataclasses import dataclass, field, asdict

from analysis.valuation import describe_market_cap
from config import EXPENSE_BENCHMARKS

# ── Severities & statuses ───────────────────────────────────────────

BLOCKING = "blocking"
ADVISORY = "advisory"

PASS = "pass"
FAIL = "fail"
SKIPPED = "skipped"

# ── Tolerances ──────────────────────────────────────────────────────

# Income identity. CIM rounding makes exact equality unrealistic; a miss
# beyond max($1k, 1% of revenue) is a data-entry or extraction error unless
# the analyst explicitly accepts it (legitimate below-the-line items exist).
NOI_RECON_TOLERANCE_ABS = 1_000.0
NOI_RECON_TOLERANCE_PCT = 0.01

# Unit mix vs stated totals. SF is the tighter of the two: NRSF is a measured
# number. GPR is gross potential, so vacancy and concessions are NOT the
# explanation for a large miss — a wider band only absorbs rounding.
UNIT_MIX_SF_TOLERANCE = 0.02
UNIT_MIX_GPR_TOLERANCE = 0.03

# EGR vs GPR: dollar-level float noise only. Equality is legitimate (a fully
# occupied property with no concessions), so the band must not swallow more.
EGR_GPR_TOLERANCE_ABS = 1.0

# A trailing-twelve-month figure covers twelve months by definition; the
# ttm_annualization check's boundary sits exactly there. A unit fact,
# not a tolerance — there is nothing to tune.
TTM_FULL_MONTHS = 12

# Occupancy comparisons are on decimals entered to at most 4 places; this
# absorbs float representation error and nothing an analyst could type.
OCCUPANCY_EPSILON = 1e-9

# An expense line below half its benchmark floor is not a cheap operator,
# it is a wrong number.
EXPENSE_FLOOR_FRACTION = 0.5

# Sources = Uses = the DCF basis. This is an identity over figures the
# pipeline computes itself, so it holds to the cent or something is
# broken — a looser band would let a real disagreement hide inside it.
SOURCES_USES_TOLERANCE_ABS = 0.01

# Rebuilding anchor + spread + drift is float arithmetic over decimals, so
# this absorbs representation error and nothing an analyst could enter.
EXIT_CAP_DERIVATION_EPSILON = 1e-9

# ── Order-of-magnitude tripwires ────────────────────────────────────
# These are NOT benchmark bands, and the distinction is the whole point.
# `opex_per_nrsf_band` asks whether a figure matches what a well-run
# facility spends. These ask something cruder and prior to it: is this
# number even the right ORDER OF MAGNITUDE, or did an input arrive in the
# wrong units — or, as at Abilene, from a different property than the one
# it is being divided by?
#
# The case that motivated them: a four-property portfolio CIM whose
# portfolio NOI was underwritten against ONE property's square footage and
# asking price. That produced $88.60/SF of NOI, a 123% entry cap and a
# 125.7% base-case IRR which PASSED the 10% IRR gate — every downstream
# figure wrong by an order of magnitude, and not one existing check fired,
# because each of them was individually satisfied.
#
# So the bands are deliberately far wider than any deal anyone would
# underwrite. They are not tuned and are not meant to be: a failure here
# means an INPUT IS WRONG, never that a deal is unusual. Anything narrow
# enough to be interesting is `opex_*_band`'s job, one severity down.
#
# Hard-coded rather than settings-editable for the same reason
# EXPENSE_FLOOR_FRACTION above is: an operator tuning the threshold at
# which the model admits it cannot read its own inputs is not a setting,
# it is a way to turn this off. Blocking findings can be accepted per-run
# by the analyst, which is the intended escape hatch for the rare
# legitimate outlier.
#
# The NOI floor sits at $1.00 rather than nearer the benchmark because a
# genuinely pre-stabilized asset in lease-up can post very thin NOI per
# SF, and refusing that deal for the wrong reason is exactly the error
# design decision 9 warns about with occupancy.
PLAUSIBLE_NOI_PER_NRSF = (1.00, 30.00)
PLAUSIBLE_ENTRY_CAP = (0.02, 0.20)

# Total Revenue vs EGR. The accounting chain is
# `GPR - vacancy - concessions = EGR` and `EGR + other income = Total
# Revenue`, so the two are the same rent one small additive term apart;
# real deals sit at 1.00-1.20. This is a units tripwire, not a band on
# other income.
#
# The high side is 3.00 and not tighter because a broker who labels a
# GROSS line "Total Revenue" beside a netted "EGR" produces a ratio of
# 1/economic occupancy honestly: at 2.00 this would refuse every deal
# under 50% economic occupancy, and depressed-occupancy assets are the
# fund's target profile. It is not wider because every mis-scale seen
# misses by at least 4x (12x for a monthly line read as annual, 10x for
# a thousands column or a decimal shift), so 3.00 still catches all of
# them while 4.00 would not.
#
# The low side is 0.33 rather than the accounting bound of 1.00 because
# a revenue line slightly UNDER EGR is a classification question - bad
# debt netted out of one line and not the other - and refusing that
# would be refusing a deal for the wrong reason. Any lower and the
# mirror defect (EGR itself mis-scaled 10x upward) stops being caught.
PLAUSIBLE_REVENUE_TO_EGR = (0.33, 3.00)


def noi_recon_tolerance(revenue: float) -> float:
    """Income-identity tolerance. Canonical definition — webapp.forms
    re-exports this name so the form, the preview and the register can
    never drift to two different tolerances."""
    return max(NOI_RECON_TOLERANCE_ABS,
               NOI_RECON_TOLERANCE_PCT * abs(revenue))


# ── Result & input containers ───────────────────────────────────────

@dataclass(frozen=True)
class CheckResult:
    """One evaluated check. `values` carries the raw inputs and `source`
    names the fields they came from, so every finding is traceable to its
    formula + source + raw inputs without re-deriving anything."""
    id: str
    label: str
    severity: str
    status: str
    message: str
    values: dict = field(default_factory=dict)
    source: str = ""

    @property
    def failed(self) -> bool:
        return self.status == FAIL

    @property
    def blocks(self) -> bool:
        return self.status == FAIL and self.severity == BLOCKING


@dataclass(frozen=True)
class CheckInput:
    """Canonical-unit inputs. Every field optional: a surface that has only
    the income triple still gets a meaningful register, with everything it
    could not see reported as `skipped`."""
    # Income statement
    ttm_gpr: float | None = None
    ttm_egr: float | None = None
    ttm_total_revenue: float | None = None
    ttm_total_expenses: float | None = None
    ttm_noi: float | None = None
    ttm_months: int | None = None   # months of actuals behind the TTM figures
    # The NOI the projection will ACTUALLY capitalize, which is not always
    # `ttm_noi`: analysis.financials._compute_adjusted_noi discards the
    # entered NOI outright whenever total revenue is present and prices the
    # deal on `revenue − adjusted_expenses` instead. The engine passes the
    # figure it computed; the form path, which has no expense analysis yet,
    # derives `revenue − expenses` (see _modelled_noi). None means "no
    # second figure exists", not "zero".
    modelled_noi: float | None = None
    # Pricing. Carried for `entry_cap_plausible` only — no other check
    # reads it, and the register deliberately does not evaluate whether a
    # price is GOOD (that is the gates' and the solvers' job), only
    # whether the price and the NOI describe the same asset.
    asking_price: float | None = None
    # Size & occupancy
    nrsf: float | None = None
    unit_mix: tuple = ()            # dicts with count / sf / rate (monthly $)
    physical_occupancy: float | None = None   # decimal
    economic_occupancy: float | None = None   # decimal
    # Analysis outputs (absent on the form path)
    expense_lines: tuple = ()       # financial_analysis expense_analysis lines
    opex_revenue_ratio: float | None = None
    opex_per_nrsf: float | None = None
    benchmarks: dict | None = None  # state-adjusted bands; None → national
    price_vs_replacement: dict | None = None
    scenarios: dict | None = None
    va_scenarios: dict | None = None   # model.value_add_model, same shape
    market_cap: dict | None = None     # analysis.valuation.resolve_market_cap
    sources_uses: dict | None = None   # model.returns_model.build_sources_uses
    # model.debt.build_debt_schedule (item E3a). Carried so
    # `sources_uses_ties` can cross-validate the financing cost against
    # the module that computed it instead of against the same dict it is
    # already validating — see that check for why self-reference is a
    # hole. Also lets the register report a loan that matures inside the
    # hold, which was previously a log line nobody sees.
    debt: dict | None = None


@dataclass(frozen=True)
class CheckSpec:
    id: str
    label: str
    severity: str
    source: str
    fn: object


# ── Helpers ─────────────────────────────────────────────────────────

def _band(inp: CheckInput, key: str):
    """State-adjusted band when the caller computed one, national otherwise.
    Never a second computation from raw config when a live one exists — that
    is how a printed band and the flag compared against it drift apart."""
    if inp.benchmarks and key in inp.benchmarks:
        return inp.benchmarks[key]
    return EXPENSE_BENCHMARKS[key]


def _modelled_noi(inp: CheckInput):
    """The NOI the model will price on, when that is a different number
    from the one the analyst entered.

    Returns None when there is no second figure to test — either the
    caller supplied none and the income triple is incomplete, or the two
    agree within the identity tolerance, in which case testing both would
    only report the same verdict twice.

    The form path's `revenue − expenses` is an UPPER BOUND on what the
    engine ultimately capitalizes, since analyst-adjusted expenses are the
    max of the CIM line and its benchmark and so only ever rise. That is
    the conservative direction for the ceiling these tripwires exist to
    enforce, and the engine passes its exact figure anyway.
    """
    modelled = inp.modelled_noi
    if modelled is None:
        rev, exp = inp.ttm_total_revenue, inp.ttm_total_expenses
        if rev is None or exp is None:
            return None
        modelled = rev - exp
    if inp.ttm_noi is None:
        return modelled
    if abs(modelled - inp.ttm_noi) <= noi_recon_tolerance(
            inp.ttm_total_revenue or inp.ttm_noi):
        return None
    return modelled


def _psf(v) -> str:
    return f"${v:,.2f}/SF"


def _pct(v) -> str:
    return f"{v:.1%}"


def _cap(v) -> str:
    """Cap rates to three places. `_pct`'s single decimal cannot show a
    7.5 bp/yr drift, and showing the derivation is the point."""
    return f"{v:.3%}"


def _bps(v) -> str:
    """Signed basis points — spread and drift are both modifiers to an
    anchor, so the sign is part of the number."""
    return f"{v:+g} bp"


def _mix_present(inp: CheckInput) -> bool:
    return bool(inp.unit_mix)


def _scenario_label(name) -> str:
    """`registry.ScenarioType` subclasses str but not StrEnum, so
    `str(ScenarioType.BASE)` is `'ScenarioType.BASE'` — and the register
    runs in-process on enum-keyed results, only seeing plain strings when
    a stored run is read back. Normalizing here is what stopped the
    findings reading "Scenariotype.Base"."""
    return str(getattr(name, "value", name))


def _scenario_rows(inp: CheckInput) -> list:
    """(label, scenario dict) for the static scenarios followed by the
    value-add ones.

    Both engines resolve their exit cap through the one
    `analysis.valuation.resolve_exit_cap`, so both belong to every check
    that reads one. The value-add side used to be absent from this
    register entirely — it ran its own coercion and set no flags, so a
    coerced VA cap was invisible on all five surfaces.
    """
    rows = [(_scenario_label(name), scen)
            for name, scen in (inp.scenarios or {}).items()
            if isinstance(scen, dict)]
    rows += [(f"value-add {_scenario_label(name)}", scen)
             for name, scen in (inp.va_scenarios or {}).items()
             if isinstance(scen, dict)]
    return rows


# ── The checks ──────────────────────────────────────────────────────
# Each returns (status, message, values). Identity/label/severity/source
# live on the CheckSpec so they are declared exactly once.

def _income_identity(inp):
    rev, exp, noi = (inp.ttm_total_revenue, inp.ttm_total_expenses,
                     inp.ttm_noi)
    values = {"revenue": rev, "expenses": exp, "noi": noi}
    if None in (rev, exp, noi):
        return (SKIPPED, "Revenue, expenses and NOI are not all present — "
                         "the identity is not testable.", values)
    delta = rev - exp - noi
    tol = noi_recon_tolerance(rev)
    values = {**values, "delta": round(delta, 2), "tolerance": round(tol, 2)}
    if abs(delta) <= tol:
        return (PASS, f"Revenue ${rev:,.0f} − Expenses ${exp:,.0f} = "
                      f"${rev - exp:,.0f}, matching TTM NOI within the "
                      f"${tol:,.0f} tolerance.", values)
    # Wording is load-bearing: webapp.forms raises this verbatim (plus its
    # own remediation sentence) and tests/test_web_deals.py asserts on it.
    return (FAIL, f"Income identity check failed: Revenue ${rev:,.0f} − "
                  f"Expenses ${exp:,.0f} = ${rev - exp:,.0f}, but TTM NOI "
                  f"is entered as ${noi:,.0f} — off by ${abs(delta):,.0f} "
                  f"(tolerance ${tol:,.0f}).", values)


def _unit_mix_sf(inp):
    nrsf = inp.nrsf
    if not nrsf or not _mix_present(inp):
        return (SKIPPED, "No unit mix or no stated NRSF — reconciliation "
                         "not testable.", {"nrsf": nrsf})
    total = sum((u.get("sf") or 0) * (u.get("count") or 0)
                for u in inp.unit_mix)
    delta = total - nrsf
    tol = UNIT_MIX_SF_TOLERANCE * nrsf
    values = {"unit_mix_sf": round(total, 2), "nrsf": nrsf,
              "delta": round(delta, 2), "tolerance": round(tol, 2)}
    if abs(delta) <= tol:
        return (PASS, f"Unit mix totals {total:,.0f} SF against a stated "
                      f"{nrsf:,.0f} NRSF — within {UNIT_MIX_SF_TOLERANCE:.0%}.",
                values)
    return (FAIL, f"Unit mix totals {total:,.0f} SF but NRSF is stated as "
                  f"{nrsf:,.0f} — off by {abs(delta):,.0f} SF "
                  f"({abs(delta) / nrsf:.1%}, tolerance "
                  f"{UNIT_MIX_SF_TOLERANCE:.0%}). Usually a partially "
                  f"extracted unit mix; confirm before trusting any "
                  f"per-SF figure.", values)


def _unit_mix_gpr(inp):
    gpr = inp.ttm_gpr
    if not gpr or not _mix_present(inp):
        return (SKIPPED, "No unit mix or no stated GPR — reconciliation "
                         "not testable.", {"ttm_gpr": gpr})
    total = sum((u.get("count") or 0) * (u.get("rate") or 0) * 12
                for u in inp.unit_mix)
    delta = total - gpr
    tol = UNIT_MIX_GPR_TOLERANCE * gpr
    values = {"unit_mix_gpr": round(total, 2), "ttm_gpr": gpr,
              "delta": round(delta, 2), "tolerance": round(tol, 2)}
    if abs(delta) <= tol:
        return (PASS, f"Unit mix at asking rates annualizes to ${total:,.0f} "
                      f"against a stated GPR of ${gpr:,.0f}.", values)
    return (FAIL, f"Unit mix at asking rates annualizes to ${total:,.0f} but "
                  f"GPR is stated as ${gpr:,.0f} — off by ${abs(delta):,.0f} "
                  f"({abs(delta) / gpr:.1%}, tolerance "
                  f"{UNIT_MIX_GPR_TOLERANCE:.0%}). Both figures are gross "
                  f"potential, so vacancy and concessions do not explain "
                  f"the gap.", values)


def _occupancy_sanity(inp):
    phys, econ = inp.physical_occupancy, inp.economic_occupancy
    values = {"physical_occupancy": phys, "economic_occupancy": econ}
    if phys is None and econ is None:
        return (SKIPPED, "Neither occupancy figure is present.", values)
    problems = []
    for label, v in (("Physical", phys), ("Economic", econ)):
        if v is None:
            continue
        if v < -OCCUPANCY_EPSILON or v > 1 + OCCUPANCY_EPSILON:
            problems.append(f"{label.lower()} occupancy of {_pct(v)} is "
                            f"outside 0–100%")
    if (phys is not None and econ is not None
            and econ - phys > OCCUPANCY_EPSILON):
        problems.append(f"economic occupancy {_pct(econ)} exceeds physical "
                        f"{_pct(phys)} — the mismanagement spread runs the "
                        f"other way, so one of the two is mislabelled")
    if problems:
        return (FAIL, "Occupancy inputs are not coherent: "
                      + "; ".join(problems) + ".", values)
    return (PASS, "Occupancy inputs are in range and economic does not "
                  "exceed physical.", values)


def _egr_le_gpr(inp):
    gpr, egr = inp.ttm_gpr, inp.ttm_egr
    values = {"ttm_gpr": gpr, "ttm_egr": egr}
    if gpr is None or egr is None:
        return (SKIPPED, "GPR or EGR not present.", values)
    if egr <= gpr + EGR_GPR_TOLERANCE_ABS:
        return (PASS, f"EGR ${egr:,.0f} sits at or below GPR ${gpr:,.0f} "
                      f"(vacancy loss ${gpr - egr:,.0f}).", values)
    return (FAIL, f"Effective Gross Revenue ${egr:,.0f} exceeds Gross "
                  f"Potential Rent ${gpr:,.0f} by ${egr - gpr:,.0f}. EGR is "
                  f"GPR net of vacancy and concessions, so it cannot be "
                  f"larger — other income is a separate line and is not "
                  f"part of EGR.", values)


def _revenue_vs_egr_plausible(inp):
    rev, egr = inp.ttm_total_revenue, inp.ttm_egr
    values = {"ttm_total_revenue": rev, "ttm_egr": egr}
    # `not egr` and not `is None`: a zero divisor yields no ratio to
    # judge. The numerator keeps the `is None` test decision 9 asks for,
    # so a stated $0 revenue beside real EGR still FAILs.
    if rev is None or not egr:
        return (SKIPPED, "Total revenue or EGR not present.", values)
    ratio = rev / egr
    values["revenue_to_egr"] = ratio
    low, high = PLAUSIBLE_REVENUE_TO_EGR
    if low <= ratio <= high:
        return (PASS, f"Total revenue ${rev:,.0f} is {ratio:.2f}x EGR "
                      f"${egr:,.0f}, the expected order of magnitude.",
                values)
    if ratio > high:
        return (FAIL, f"RE-READ TOTAL REVENUE FIRST. Total revenue "
                      f"${rev:,.0f} is {ratio:.2f}x Effective Gross "
                      f"Revenue ${egr:,.0f}. Total revenue is EGR plus "
                      f"other income, so a multiple this large is a units "
                      f"error, not a big ancillary line. Total revenue is "
                      f"the figure to re-read: EGR is corroborated by GPR "
                      f"and the unit mix, total revenue by nothing, and it "
                      f"is what the model prices on - NOI, the entry cap, "
                      f"every IRR and the max offer are all inflated by "
                      f"roughly this factor. Usual causes: a column stated "
                      f"in thousands, a decimal shift, a monthly line read "
                      f"as annual, or a portfolio total beside one "
                      f"property's EGR.", values)
    return (FAIL, f"RE-READ EFFECTIVE GROSS REVENUE FIRST. Total revenue "
                  f"${rev:,.0f} is only {ratio:.2f}x EGR ${egr:,.0f}. Total "
                  f"revenue is EGR plus other income, so it cannot sit this "
                  f"far below EGR. EGR is the figure to re-read: total "
                  f"revenue is the larger of the two by construction, so a "
                  f"ratio under {low:.2f} points at EGR being overstated - "
                  f"typically a gross potential rent line entered as EGR, or "
                  f"a units error in the EGR line itself.", values)


def _opex_ratio_band(inp):
    ratio = inp.opex_revenue_ratio
    if ratio is None and inp.ttm_total_expenses is not None and \
            inp.ttm_total_revenue:
        ratio = inp.ttm_total_expenses / inp.ttm_total_revenue
    low, high = _band(inp, "opex_revenue_ratio")
    values = {"opex_revenue_ratio": ratio, "low": low, "high": high}
    if ratio is None:
        return (SKIPPED, "No OpEx/Revenue ratio available.", values)
    if low <= ratio <= high:
        return (PASS, f"OpEx/Revenue of {_pct(ratio)} is inside the "
                      f"{_pct(low)}–{_pct(high)} band.", values)
    side = "below" if ratio < low else "above"
    return (FAIL, f"OpEx/Revenue of {_pct(ratio)} is {side} the "
                  f"{_pct(low)}–{_pct(high)} benchmark band. A ratio below "
                  f"the band usually means expenses are understated, which "
                  f"overstates NOI and every return that follows from it.",
            values)


def _opex_per_nrsf_band(inp):
    per_sf = inp.opex_per_nrsf
    if per_sf is None and inp.ttm_total_expenses is not None and inp.nrsf:
        per_sf = inp.ttm_total_expenses / inp.nrsf
    low, high = _band(inp, "total_opex")
    values = {"opex_per_nrsf": per_sf, "low": low, "high": high}
    if per_sf is None:
        return (SKIPPED, "No total OpEx per NRSF available.", values)
    if low <= per_sf <= high:
        return (PASS, f"Total OpEx of {_psf(per_sf)} is inside the "
                      f"{_psf(low)}–{_psf(high)} band.", values)
    side = "below" if per_sf < low else "above"
    return (FAIL, f"Total OpEx of {_psf(per_sf)} is {side} the "
                  f"{_psf(low)}–{_psf(high)} benchmark band.", values)


def _noi_per_nrsf_plausible(inp):
    """Does the NOI describe the same asset as the NRSF?

    Blocking, where `opex_per_nrsf_band` beside it is advisory: an OpEx
    line outside its band is a judgment to argue with, whereas NOI an
    order of magnitude off its own square footage means the projection,
    every scenario, both solvers and the IRR gate are all being computed
    on a number that is not this property's.
    """
    noi, nrsf = inp.ttm_noi, inp.nrsf
    low, high = PLAUSIBLE_NOI_PER_NRSF
    per_sf = (noi / nrsf) if (noi is not None and nrsf) else None
    values = {"noi_per_nrsf": per_sf, "ttm_noi": noi, "nrsf": nrsf,
              "low": low, "high": high}
    # The modelled NOI is tested FIRST and on its own terms. When it
    # differs from the entered one it is the number every return is
    # computed from, so an entered NOI that looks fine cannot be allowed
    # to answer for it — that is precisely how the portfolio CIM in this
    # module's header comment passed both tripwires written to catch it.
    modelled = _modelled_noi(inp)
    if modelled is not None and nrsf:
        m_per_sf = modelled / nrsf
        values = {**values, "modelled_noi": modelled,
                  "modelled_noi_per_nrsf": m_per_sf}
        if not (low <= m_per_sf <= high):
            return (FAIL, f"The NOI this model prices on is ${modelled:,.0f} "
                          f"— {_psf(m_per_sf)} against {nrsf:,.0f} SF, "
                          f"outside the {_psf(low)}–{_psf(high)} plausible "
                          f"range. That figure is revenue minus expenses, "
                          f"NOT the ${noi:,.0f} TTM NOI entered, which the "
                          f"projection discards whenever total revenue is "
                          f"present. Every return in this model is computed "
                          f"from ${modelled:,.0f}.", values)
    if per_sf is None:
        return (SKIPPED, "Needs both TTM NOI and NRSF.", values)
    if low <= per_sf <= high:
        return (PASS, f"TTM NOI of {_psf(per_sf)} is a plausible figure "
                      f"for {nrsf:,.0f} SF.", values)
    if per_sf > high:
        return (FAIL, f"TTM NOI of ${noi:,.0f} against {nrsf:,.0f} SF is "
                      f"{_psf(per_sf)} — above the {_psf(high)} ceiling for "
                      f"a plausible self-storage asset. The usual cause is "
                      f"a portfolio-level NOI divided by ONE property's "
                      f"square footage, or a monthly figure read as annual. "
                      f"Every return in this model is computed from this "
                      f"number.", values)
    return (FAIL, f"TTM NOI of ${noi:,.0f} against {nrsf:,.0f} SF is "
                  f"{_psf(per_sf)} — below the {_psf(low)} floor for a "
                  f"plausible self-storage asset. A property still in "
                  f"lease-up can legitimately sit here; anything else "
                  f"points at an NOI or NRSF that do not describe the "
                  f"same asset.", values)


def _entry_cap_plausible(inp):
    """Does the NOI describe the same asset as the ASKING PRICE?

    The sibling of `noi_per_nrsf_plausible` on the other axis, and worth
    stating separately because the two fail independently: a portfolio
    NOI against one property's SF trips the first, against one property's
    price trips this one, and the Abilene CIM tripped both.

    It also guards a specific downstream surprise. `project_cash_flows`
    floors the exit cap at the entry cap (decision 4), so an implausible
    entry cap silently reprices the exit for every scenario, while the
    sensitivity grid — which sweeps exit caps UNCOERCED by design — does
    not follow it. The two surfaces then disagree, and the disagreement
    reads as a bug in the grid rather than as the bad input it is.
    """
    noi, price = inp.ttm_noi, inp.asking_price
    low, high = PLAUSIBLE_ENTRY_CAP
    cap = (noi / price) if (noi is not None and price) else None
    values = {"entry_cap": cap, "ttm_noi": noi, "asking_price": price,
              "low": low, "high": high}
    # Tested first, and for the reason given in noi_per_nrsf_plausible:
    # the exit-cap floor this docstring warns about is applied to the
    # MODELLED entry cap, so validating the entered one reports a
    # plausible yield for a deal whose exit was repriced at 121%.
    modelled = _modelled_noi(inp)
    if modelled is not None and price:
        m_cap = modelled / price
        values = {**values, "modelled_noi": modelled,
                  "modelled_entry_cap": m_cap}
        if not (low <= m_cap <= high):
            return (FAIL, f"The NOI this model prices on is ${modelled:,.0f} "
                          f"— an entry cap of {_cap(m_cap)} against an "
                          f"asking price of ${price:,.0f}, outside the "
                          f"{_cap(low)}–{_cap(high)} plausible range. That "
                          f"figure is revenue minus expenses, NOT the "
                          f"${noi:,.0f} TTM NOI entered, which yields a "
                          f"reassuring {_cap(cap)} the projection never "
                          f"uses. The exit cap is floored at this entry "
                          f"cap, so every scenario is repriced by it.",
                    values)
    if cap is None:
        return (SKIPPED, "Needs both TTM NOI and an asking price.", values)
    if low <= cap <= high:
        return (PASS, f"Entry cap of {_cap(cap)} is a plausible going-in "
                      f"yield.", values)
    side = "above" if cap > high else "below"
    hint = ("The usual cause is a portfolio-level NOI against one "
            "property's asking price."
            if cap > high else
            "Check whether the asking price covers more property than the "
            "NOI does.")
    return (FAIL, f"TTM NOI of ${noi:,.0f} against an asking price of "
                  f"${price:,.0f} is an entry cap of {_cap(cap)} — {side} "
                  f"the {_cap(low)}–{_cap(high)} range any real transaction "
                  f"occupies. {hint} Note the exit cap is floored at the "
                  f"entry cap, so this figure also silently reprices the "
                  f"exit in every scenario.", values)


def _expense_line_floor(inp):
    """The `$1 property tax` catcher: any line at zero, absent, or below
    half its own benchmark floor."""
    if not inp.expense_lines:
        return (SKIPPED, "No expense lines analyzed.", {})
    missing, zeroed, under = [], [], []
    detail = {}
    for line in inp.expense_lines:
        category = line.get("category") or line.get("benchmark_key") or "?"
        rng = line.get("benchmark_range")
        if rng:
            actual, fmt = line.get("per_nrsf"), _psf
        else:
            rng = line.get("benchmark_range_pct")
            if not rng:
                continue
            actual, fmt = line.get("cim_pct"), _pct
        low = rng[0]
        detail[category] = actual
        if actual is None:
            missing.append(category)
        elif actual <= 0:
            zeroed.append(f"{category} at {fmt(actual)}")
        elif low and actual < low * EXPENSE_FLOOR_FRACTION:
            under.append(f"{category} at {fmt(actual)} vs a {fmt(low)} floor")
    values = {"lines": detail, "floor_fraction": EXPENSE_FLOOR_FRACTION}
    parts = []
    if zeroed:
        parts.append("zero: " + ", ".join(zeroed))
    if under:
        parts.append("below half the benchmark floor: " + ", ".join(under))
    if missing:
        parts.append("not stated in the CIM: " + ", ".join(missing))
    if not parts:
        return (PASS, "Every expense line is stated and at or above half "
                      "its benchmark floor.", values)
    return (FAIL, "Expense lines that cannot be taken at face value — "
                  + "; ".join(parts) + ". Each is adjusted up to the "
                  "benchmark floor before NOI, but a line this far off is "
                  "usually an extraction or entry error worth fixing at "
                  "the source.", values)


def _market_exit_cap(inp):
    """Where the exit cap came from: market anchor + spread + drift × hold.

    The exit cap used to be a free-standing constant an analyst could read
    off the settings page. It is now derived, so a reader who cannot
    retrace it has lost something — this check exists to hand back the
    parts. It reports the anchor and rebuilds each scenario's cap from its
    own recorded components.

    Advisory, not blocking. The two findings here are that the derivation
    rested on something unconfirmed (an unknown vintage picked the band by
    fallback) or that the parts no longer add up — and while the second
    reads like a pipeline defect, the register keeps `blocking` for the
    identities in `_sources_uses_ties`, which is what actually refuses to
    publish a deal.
    """
    mc = inp.market_cap or {}
    rows = _scenario_rows(inp)
    if not mc and not rows:
        return (SKIPPED, "No market cap anchor and no scenarios — the exit "
                         "cap derivation is not testable.", {})

    values = {"asset_class": mc.get("asset_class"),
              "age_band": mc.get("age_band"),
              "age_band_known": mc.get("age_band_known"),
              "market_cap": mc.get("market_cap"),
              "market_cap_source": mc.get("source"),
              "table_market_cap": mc.get("table_market_cap"),
              "as_of": mc.get("as_of")}

    # Every consumer is handed the SAME resolved anchor, so more than one
    # distinct value here means a consumer was missed when the cap was
    # threaded through — the failure the one-resolve discipline prevents.
    anchors = set()
    if mc.get("market_cap") is not None:
        anchors.add(round(float(mc["market_cap"]), 10))

    scen_values, problems, lines = {}, [], []
    for label, scen in rows:
        detail = scen.get("exit_cap_detail") or {}
        anchor = detail.get("market_cap")
        spread = detail.get("scenario_spread_bps")
        drift_total = detail.get("drift_total_bps")
        requested = scen.get("requested_exit_cap")
        scen_values[label] = {
            "market_cap": anchor,
            "scenario_spread_bps": spread,
            "drift_bps_per_year": detail.get("drift_bps_per_year"),
            "drift_total_bps": drift_total,
            "hold_years": detail.get("hold_years"),
            "requested_exit_cap": requested,
            "applied_exit_cap": scen.get("exit_cap")}
        if None in (anchor, spread, drift_total, requested):
            problems.append(f"{label} does not carry its exit-cap derivation")
            continue
        anchors.add(round(float(anchor), 10))
        rebuilt = (float(anchor)
                   + (float(spread) + float(drift_total)) / 10_000.0)
        if abs(rebuilt - float(requested)) > EXIT_CAP_DERIVATION_EPSILON:
            problems.append(f"{label} publishes {_cap(requested)} but its own "
                            f"parts rebuild to {_cap(rebuilt)}")
            continue
        lines.append(f"{label.title()} {_cap(requested)} = {_cap(anchor)} "
                     f"{_bps(spread)} spread {_bps(drift_total)} drift "
                     f"over {detail.get('hold_years')} yrs")
    values["scenarios"] = scen_values

    if len(anchors) > 1:
        problems.append("scenarios are priced off different market caps ("
                        + ", ".join(_cap(a) for a in sorted(anchors))
                        + "), so at least one consumer did not receive the "
                          "resolved anchor")
    # An analyst-entered rate stands on its own; only a table lookup needs
    # the vintage, and without one it lands in the fallback band by default.
    if mc.get("source") == "table" and mc.get("age_band_known") is False:
        problems.append(f"the year built is unknown, so the anchor came from "
                        f"the {mc.get('age_band')!r} band by fallback rather "
                        f"than from the asset's actual age")

    anchor_txt = ""
    if mc.get("market_cap") is not None:
        anchor_txt = (f"{mc.get('asset_class') or 'Asset'} in the "
                      f"{mc.get('age_band') or '—'} band anchors at "
                      f"{_cap(mc['market_cap'])} "
                      f"({describe_market_cap(mc)}). ")
    if problems:
        return (FAIL, anchor_txt + "The exit cap derivation needs confirming: "
                + "; ".join(problems) + ".", values)
    return (PASS, anchor_txt + ("Derived caps — " + "; ".join(lines) + "."
                                if lines else
                                "No scenario has been projected yet."), values)


def _exit_cap_coercion(inp):
    rows = _scenario_rows(inp)
    if not rows:
        return (SKIPPED, "No scenarios computed.", {})
    coerced, values = [], {}
    for label, scen in rows:
        if not scen.get("exit_cap_coerced"):
            continue
        requested = scen.get("requested_exit_cap")
        applied = scen.get("exit_cap")
        values[label] = {"requested_exit_cap": requested,
                         "applied_exit_cap": applied}
        coerced.append(f"{label.title()} raised from "
                       f"{_cap(requested) if requested is not None else '—'} "
                       f"to {_cap(applied) if applied is not None else '—'}")
    if not coerced:
        return (PASS, "No scenario needed its exit cap raised to meet the "
                      "exit ≥ entry rule.", values)
    return (FAIL, "Exit cap was raised to the entry cap to satisfy the "
                  "exit ≥ entry rule — " + "; ".join(coerced) + ". The "
                  "scenario's returns are computed on the raised cap, not "
                  "on the one entered.", values)


def _sources_uses_ties(inp):
    """Total Uses = Total Sources = the DCF's total basis + financing costs.

    Unlike every other check here, this one tests arithmetic the pipeline
    performed on itself rather than an analyst's input — so a failure is a
    bug, not a bad number, which is exactly why it carries the loud
    severity.

    **Why the identity carries a financing term.** E1 measured that
    handing `build_debt_schedule`'s `financing_costs` to
    `build_sources_uses` broke this check by exactly the origination fee:
    the fee is a use of funds, but `project_cash_flows` computes
    `total_basis = price + capex + acquisition_cost + reserve` and has no
    financing term. E1's handoff prescribed adding one to the projection.
    The operator reversed that on 2026-08-01 (item E3a): an origination
    fee inside `total_basis` makes the PRIMARY unlevered IRR screen move
    the moment a deal names a loan, and an unlevered return charged a
    financing fee is not an unlevered return. So the identity moved and
    the projection did not.

    The check is no weaker for it — it is still exact to the cent, and it
    still refuses to let the capital stack and the returns model disagree.
    It now says which of the two is allowed to differ, and by precisely
    what.

    **The financing term is cross-validated against the debt module, not
    taken from the stack it is checking.** Reading it only from
    `sources_uses` would make the check self-referential and blind to
    exactly the bug class it exists to catch: a caller that forgot to
    pass `financing_costs=debt["financing_costs"]` into
    `build_sources_uses` produces a `total_uses` missing the origination
    fee AND a `financing_costs` of 0 — both wrong the same way, so
    `uses == basis + 0` reconciles and the check would PASS on a deal
    underfunded by the whole fee, which shows up as an equity shortfall
    at closing. Comparing against `inp.debt` closes that.
    """
    su = inp.sources_uses or {}
    if not su:
        return (SKIPPED, "No Sources & Uses computed — no capital stack to "
                         "reconcile.", {})
    uses = su.get("total_uses")
    sources = su.get("total_sources")
    financing = float(su.get("financing_costs") or 0.0)
    debt_financing = (inp.debt or {}).get("financing_costs")
    if (debt_financing is not None
            and abs(float(debt_financing) - financing)
            > SOURCES_USES_TOLERANCE_ABS):
        return (FAIL,
                f"The capital stack reports ${financing:,.2f} of financing "
                f"costs but the sized loan charges ${float(debt_financing):,.2f}"
                f". The stack was built without the debt module's fee, so "
                f"Total Uses is short by the difference and the equity "
                f"required at closing is understated.",
                {"sources_uses_financing_costs": financing,
                 "debt_financing_costs": float(debt_financing),
                 "tolerance": SOURCES_USES_TOLERANCE_ABS})
    bases = sorted({round(float(s["total_basis"]) + financing, 2)
                    for s in (inp.scenarios or {}).values()
                    if isinstance(s, dict) and s.get("total_basis") is not None})
    values = {"total_uses": uses, "total_sources": sources,
              "financing_costs": financing,
              "scenario_bases_plus_financing": bases,
              "tolerance": SOURCES_USES_TOLERANCE_ABS}
    if uses is None or sources is None:
        return (SKIPPED, "Sources & Uses is missing a total.", values)
    problems = []
    if abs(uses - sources) > SOURCES_USES_TOLERANCE_ABS:
        problems.append(f"Uses ${uses:,.2f} vs Sources ${sources:,.2f}")
    if not bases:
        # No DCF to compare against — but a stack that does not balance
        # against ITSELF is still a finding. Reporting `skipped` here
        # would drop a real one on the floor.
        if problems:
            return (FAIL, "The capital stack does not balance: "
                          + "; ".join(problems) + ".", values)
        return (SKIPPED, "Sources and Uses balance, but there is no "
                         "scenario basis to reconcile them against.", values)
    if len(bases) > 1:
        problems.append("scenarios disagree on total basis ("
                        + ", ".join(f"${b:,.2f}" for b in bases) + ")")
    elif abs(uses - bases[0]) > SOURCES_USES_TOLERANCE_ABS:
        problems.append(
            f"Uses ${uses:,.2f} vs DCF basis + financing costs "
            f"${bases[0]:,.2f} (basis ${bases[0] - financing:,.2f} + "
            f"financing ${financing:,.2f})")
    if problems:
        return (FAIL, "The capital stack does not tie to the returns model: "
                      + "; ".join(problems) + ". Every return is computed on "
                      "the DCF basis, so a stack that disagrees with it is "
                      "describing a different deal.", values)
    tail = (f" (DCF basis ${bases[0] - financing:,.0f} + financing costs "
            f"${financing:,.0f})") if financing else ""
    return (PASS, f"Uses, Sources and the DCF basis all equal "
                  f"${uses:,.0f}{tail}.", values)


def _loan_matures_before_exit(inp):
    """The balloon comes due before the sale, and the schedule ignores it.

    `model.debt.build_debt_schedule` already computes this and logs a
    warning, but item E3a is what put a sized loan on every deal, so the
    condition went live at the same moment. A `logger.warning` reaches a
    server log nobody reads while the results page, memo and Excel show a
    levered IRR computed as though the loan amortized happily past its own
    maturity — no refinancing modelled, no exit fee, no rate reset. That
    overstates the levered return on exactly the long-hold deals where it
    matters, so it belongs in the register beside every other assumption
    the analyst is expected to know about.

    ADVISORY, not blocking: amortizing past maturity is a stated modelling
    limitation, not an arithmetic error, and refusing to run the deal over
    it would be the wrong trade.
    """
    debt = inp.debt or {}
    if not debt:
        return (SKIPPED, "No debt schedule — nothing to test for maturity.",
                {})
    if not debt.get("loan"):
        return (SKIPPED, "This deal carries no debt.", {"loan": 0.0})
    # READ the flag `build_debt_schedule` already published; do not
    # re-derive it. An earlier draft recomputed it from
    # `len(annual_debt_service)` and `terms["term_years"]`, which is a
    # SECOND copy of the same comparison sitting one function below
    # `_sources_uses_ties` — the check this same item had to repair for
    # precisely that defect. The two cannot disagree today, which is
    # exactly what makes the drift invisible when one of them changes.
    matures = debt.get("matures_before_exit")
    terms = debt.get("terms") or {}
    term_years = terms.get("term_years")
    hold_years = debt.get("hold_years")
    values = {"term_years": term_years, "hold_years": hold_years,
              "matures_before_exit": matures,
              "payoff_balance": debt.get("payoff_balance")}
    if matures is None or not term_years or not hold_years:
        return (SKIPPED, "Loan term or hold period is unknown.", values)
    if matures:
        return (FAIL,
                f"The loan matures in year {term_years} but the hold runs "
                f"{hold_years} years, so the balloon is due before the sale. "
                f"The schedule amortizes straight past maturity: no "
                f"refinancing, rate reset or prepayment cost is modelled, "
                f"which overstates the levered return.", values)
    return (PASS, f"The loan's {term_years}-year term outlasts the "
                  f"{hold_years}-year hold.", values)


def _price_vs_replacement(inp):
    comp = inp.price_vs_replacement or {}
    if not comp.get("comparable"):
        return (SKIPPED, "Asking price or replacement cost unavailable.", {})
    asking_psf = comp.get("asking_per_sf")
    repl_psf = comp.get("replacement_per_sf")
    discount = comp.get("discount_to_replacement")
    values = {"asking_per_sf": asking_psf, "replacement_per_sf": repl_psf,
              "discount_to_replacement": discount}
    both = (f"{_psf(asking_psf)} asking vs {_psf(repl_psf)} replacement"
            if None not in (asking_psf, repl_psf) else "")
    if comp.get("passes_gate"):
        return (PASS, f"Asking price is at or below replacement cost"
                      + (f" — {both}." if both else "."), values)
    return (FAIL, f"Asking price exceeds estimated replacement cost"
                  + (f" — {both}" if both else "")
                  + (f", a {abs(discount):.1%} premium." if discount is not None
                     else "."), values)


def _ttm_annualization(inp):
    """Item A's deferred check, unblocked by the `ttm_months` field.

    Advisory on purpose: a T-9 annualization is a disclosure problem,
    not necessarily a wrong number — but self-storage revenue is
    seasonal, so a partial year scaled up can overstate (summer-
    weighted) or understate (winter-weighted) the true trailing year,
    and the analyst deserves the flag before trusting GPR/EGR/NOI.
    """
    months = inp.ttm_months
    values = {"ttm_months": months}
    if months is None:
        return (SKIPPED, "The CIM does not state how many months of "
                         "actuals its TTM figures cover.", values)
    if months == TTM_FULL_MONTHS:
        return (PASS, "TTM figures cover a full twelve months of "
                      "actuals — no annualization involved.", values)
    if months > TTM_FULL_MONTHS:
        return (FAIL, f"{months} months of actuals stated — more than a "
                      f"trailing twelve-month period. Check the "
                      f"reporting basis: a figure summed over more than "
                      f"a year is not a TTM figure.", values)
    return (FAIL, f"TTM figures annualized from {months} months of "
                  f"actuals — a partial year scaled up. Self-storage "
                  f"revenue is seasonal, so annualized GPR/EGR/NOI can "
                  f"overstate (summer-weighted) or understate "
                  f"(winter-weighted) the true trailing year.", values)


# ── Registry ────────────────────────────────────────────────────────
# Order is display order. The check list is code, reviewed like code —
# deliberately not a configurable rules engine.

CHECKS = (
    CheckSpec("income_identity", "Revenue − Expenses = NOI", BLOCKING,
              "ttm_total_revenue, ttm_total_expenses, ttm_noi",
              _income_identity),
    CheckSpec("occupancy_sanity", "Occupancy coherence", BLOCKING,
              "physical_occupancy, economic_occupancy", _occupancy_sanity),
    CheckSpec("egr_le_gpr", "EGR ≤ GPR", BLOCKING,
              "ttm_gpr, ttm_egr", _egr_le_gpr),
    CheckSpec("revenue_vs_egr_plausible", "Total revenue vs EGR is the "
              "right order of magnitude", BLOCKING,
              "ttm_total_revenue, ttm_egr", _revenue_vs_egr_plausible),
    CheckSpec("noi_per_nrsf_plausible", "NOI per NRSF is the right order "
              "of magnitude", BLOCKING, "ttm_noi, nrsf",
              _noi_per_nrsf_plausible),
    CheckSpec("entry_cap_plausible", "Entry cap is the right order of "
              "magnitude", BLOCKING, "ttm_noi, asking_price",
              _entry_cap_plausible),
    CheckSpec("sources_uses_ties", "Sources & Uses ties to DCF basis",
              BLOCKING, "sources_uses, scenario_results[*].total_basis",
              _sources_uses_ties),
    CheckSpec("unit_mix_sf", "Unit mix SF vs NRSF", ADVISORY,
              "unit_mix, nrsf", _unit_mix_sf),
    CheckSpec("unit_mix_gpr", "Unit mix rents vs GPR", ADVISORY,
              "unit_mix, ttm_gpr", _unit_mix_gpr),
    CheckSpec("expense_line_floor", "Expense line floors", ADVISORY,
              "financial_analysis.expense_analysis.lines",
              _expense_line_floor),
    CheckSpec("opex_ratio_band", "OpEx / Revenue band", ADVISORY,
              "financial_analysis.expense_ratio_check.opex_revenue_ratio",
              _opex_ratio_band),
    CheckSpec("opex_per_nrsf_band", "Total OpEx $/NRSF band", ADVISORY,
              "financial_analysis.expense_ratio_check.opex_per_nrsf",
              _opex_per_nrsf_band),
    CheckSpec("market_exit_cap", "Exit cap derivation", ADVISORY,
              "market_cap, scenario_results[*].exit_cap_detail, "
              "va_results[*].exit_cap_detail", _market_exit_cap),
    CheckSpec("exit_cap_coercion", "Exit cap ≥ entry cap", ADVISORY,
              "scenario_results[*].requested_exit_cap, "
              "va_results[*].requested_exit_cap", _exit_cap_coercion),
    CheckSpec("price_vs_replacement", "Price vs replacement cost", ADVISORY,
              "physical_analysis.price_vs_replacement", _price_vs_replacement),
    CheckSpec("loan_matures_before_exit", "Loan term vs hold period",
              ADVISORY, "debt.terms.term_years, debt.annual_debt_service",
              _loan_matures_before_exit),
    CheckSpec("ttm_annualization", "TTM annualization basis", ADVISORY,
              "ttm_months", _ttm_annualization),
)

CHECK_IDS = tuple(spec.id for spec in CHECKS)


def run_checks(inp: CheckInput, only=None) -> list[CheckResult]:
    """Evaluate the registry. `only` restricts to a subset of ids — used by
    the assumptions form, which can see the income triple and occupancy but
    not the analysis outputs."""
    results = []
    for spec in CHECKS:
        if only is not None and spec.id not in only:
            continue
        status, message, values = spec.fn(inp)
        results.append(CheckResult(
            id=spec.id, label=spec.label, severity=spec.severity,
            status=status, message=message, values=values,
            source=spec.source))
    return results


def blocking_failures(results) -> list[CheckResult]:
    return [r for r in results if r.blocks]


def failures(results) -> list[CheckResult]:
    return [r for r in results if r.failed]


def summarize(results) -> dict:
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.status == PASS),
        "failed": sum(1 for r in results if r.status == FAIL),
        "skipped": sum(1 for r in results if r.status == SKIPPED),
        "blocking_failed": sum(1 for r in results if r.blocks),
        "advisory_failed": sum(1 for r in results
                               if r.failed and r.severity == ADVISORY),
    }


def to_dicts(results) -> list[dict]:
    """JSON-safe rows for AnalysisRun.result_json."""
    return [asdict(r) for r in results]


def from_dicts(rows) -> list[CheckResult]:
    """Inverse of to_dicts, for display builders reading a stored run.
    Unknown keys are dropped rather than raising — an old run predating a
    field must still render."""
    fields = {"id", "label", "severity", "status", "message", "values",
              "source"}
    return [CheckResult(**{k: v for k, v in row.items() if k in fields})
            for row in rows or []]


# ── Input adapters ──────────────────────────────────────────────────

def _unit_mix_dicts(unit_mix) -> tuple:
    """CIMData carries UnitType dataclasses; overrides and the editor carry
    plain dicts. Normalize both to dicts so the checks never branch."""
    rows = []
    for u in unit_mix or []:
        if isinstance(u, dict):
            rows.append({"count": u.get("count"), "sf": u.get("sf"),
                         "rate": u.get("rate")})
        else:
            rows.append({"count": getattr(u, "count", None),
                         "sf": getattr(u, "sf", None),
                         "rate": getattr(u, "rate", None)})
    return tuple(rows)


def input_from_cim(cim, financial_analysis=None, physical_analysis=None,
                   scenario_results=None, sources_uses=None,
                   va_results=None, market_cap=None,
                   debt=None) -> CheckInput:
    """Build the register's input from a CIMData plus whichever analysis
    outputs the caller has. Bands come from the ratio check the pipeline
    already computed (state-adjusted), never from a second read of raw
    config — two computations of the same band is how a printed band and
    the flag compared against it drift apart."""
    fin = financial_analysis or {}
    ratio_check = fin.get("expense_ratio_check") or {}
    benchmarks = {}
    if ratio_check.get("benchmark_opex_range"):
        benchmarks["total_opex"] = tuple(ratio_check["benchmark_opex_range"])
    if ratio_check.get("benchmark_ratio_range"):
        benchmarks["opex_revenue_ratio"] = tuple(
            ratio_check["benchmark_ratio_range"])
    return CheckInput(
        ttm_gpr=cim.ttm_gpr,
        ttm_egr=cim.ttm_egr,
        ttm_total_revenue=cim.ttm_total_revenue,
        ttm_total_expenses=cim.ttm_total_expenses,
        ttm_noi=cim.ttm_noi,
        ttm_months=cim.ttm_months,
        # The engine knows the exact figure the projection capitalizes, so
        # it passes that rather than letting _modelled_noi re-derive an
        # approximation of it from the income triple.
        modelled_noi=(fin.get("adjusted_ttm_noi") or {}).get(
            "analyst_adjusted_noi"),
        asking_price=cim.asking_price,
        nrsf=cim.nrsf,
        unit_mix=_unit_mix_dicts(cim.unit_mix),
        physical_occupancy=cim.physical_occupancy,
        economic_occupancy=cim.economic_occupancy,
        expense_lines=tuple((fin.get("expense_analysis") or {}).get("lines")
                            or ()),
        opex_revenue_ratio=ratio_check.get("opex_revenue_ratio"),
        opex_per_nrsf=ratio_check.get("opex_per_nrsf"),
        benchmarks=benchmarks or None,
        price_vs_replacement=(physical_analysis or {}).get(
            "price_vs_replacement"),
        scenarios=scenario_results,
        va_scenarios=va_results,
        market_cap=market_cap,
        sources_uses=sources_uses,
        debt=debt,
    )
