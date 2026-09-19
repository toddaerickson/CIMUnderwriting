# Financial Document Extraction Matrices

Operating templates for AI-assisted extraction from **CIMs / Offering Memoranda**,
**Credit Memos**, and **Loan Credit Agreements**. Written for a platform-agnostic
deployment (Hebbia, Alkymi, Claude API, or an internal pipeline) at a working
volume of roughly 30 CIMs, 100 credit memos and 20 legal documents per month.

The build/buy decision, vendor assessment and cost model live in
`docs/doc-extraction-build-vs-buy.md`. This file is the *what to extract*; that
file is the *what to run it on*.

---

## 0. The field-class model — read before using any matrix below

Every field in every matrix belongs to exactly one of three classes, and each
class gets a different control. Collapsing them is the root cause of
confidently-cited wrong answers.

| Class | Example | Control | May a model own it? |
|---|---|---|---|
| **1 — Verbatim** | "Maximum First Lien Net Leverage: 6.50x, stepping to 6.00x from Q4-2027" | Citation-gated. Reject any row without a section/page anchor. | **Yes.** This is where the time saving is real. |
| **2 — Computed** | EV/EBITDA, covenant headroom at close, pro-forma leverage, basket capacity | Extract the **inputs** only; compute outside the model. | **No.** |
| **3 — Synthesized** | "Top three structural strengths", "why to lend" | Model produces the cited evidence table; a person writes the conclusion. | **No** — and not only for accuracy reasons (see §0.2). |

### 0.1 Never ask a model for arithmetic

A prompt of the form *"what is the implied EV and the EV/EBITDA multiple?"*
presents a class-2 field as class-1. The answer arrives with a citation to a
real table and is indistinguishable from an audited number. Ask instead for the
**components** — the EV build-up lines, the EBITDA line, each add-back — and
multiply in the spreadsheet.

### 0.2 A machine-drafted credit thesis is a documentation exposure

A credit memo is an examined artifact: regulator, auditor, IC record, and in a
workout, discovery. A "strengths" section drafted by a model and signed by an
analyst creates a problem independent of whether the text is correct. The
recommended split — model produces the **evidence table** (concentration %,
retention, capex/revenue, margin history, each cited); the analyst writes the
prose — retains most of the time saving and none of the exposure.

### 0.3 Standing instruction — append to every extraction prompt

> Quote the operative language verbatim and cite section and page. If the term
> is defined, also quote the definition and its section. If the provision is
> modified by any "notwithstanding", proviso, or cross-referenced section,
> quote that too. If the document does not contain the item, return
> **NOT PRESENT** — never infer, never estimate, never supply a
> market-standard value.

The final clause is load-bearing. An unconstrained model fills a missing MFN
provision with the market-standard 50bps / 6-month formulation and cites it to
a section that says something else.

### 0.4 Acceptance gate

Before committing to any platform or pipeline, take five documents already
abstracted by hand, run them blind, and measure **field-level accuracy** — the
share of extracted fields that are exactly right. Not the vendor's OCR figure,
which is character-level and unrelated. Below roughly 95% on class-1 fields the
review burden erases the time saving.

---

## 1. Credit Agreement / Legal Document Matrix

### A. Document integrity — run first; everything downstream is void if this is wrong

| Field | Prompt | Accuracy trap |
|---|---|---|
| Instrument & date | Identify document type (Credit Agreement / Amendment No. __ / Amended & Restated / Intercreditor / Security / Guarantee), execution date, closing date, governing law. | An A&R agreement and its predecessor read near-identically for 200 pages. |
| Amendment stack | List every amendment, waiver, consent, joinder or side letter referenced. State explicitly whether this document is a **conformed copy** and as of what date. | **The single most common error.** Pricing, covenant levels and baskets are the most-amended provisions; extracting from the original is confidently wrong. |
| Credit parties | Borrower(s), Holdings/Parent, Guarantors, Restricted vs Unrestricted Subsidiaries, Administrative Agent, Collateral Agent, Arrangers. | "Company" is frequently defined as Holdings, not the Borrower — covenant tests then run at a different entity than assumed. |
| Schedules & exhibits | List all schedules and exhibits; flag any referenced but not attached (esp. Schedule 2.01 Commitments, disclosure schedules, the Compliance Certificate exhibit). | The Compliance Certificate exhibit often carries the operative covenant arithmetic. Schedules ship as separate files and get left out of the upload. |

### B. Facilities & structure

| Field | Prompt | Accuracy trap |
|---|---|---|
| Tranche table | For each facility: name, type (Revolver / TLA / TLB / DDTL / Incremental), commitment, currency, maturity date, scheduled amortisation %/yr, borrower of record, lien priority. | Springing maturity tied to junior debt sits in the definition of Maturity Date, not the tranche table. |
| Incremental / accordion | Extract capacity separately as: (i) fixed "free-and-clear" amount, (ii) grower prong and its basis, (iii) ratio-based capacity and the exact test level, (iv) whether (i) and (iii) may be used **cumulatively**, (v) MFN protection — bps, tranches covered, and any **sunset**. | MFN sunsets (6–12 months) and carve-outs (acquisition debt, different-maturity debt, an MFN-free dollar basket) gut the protection. Without the carve-outs the field is worthless. |
| Delayed draw | Availability period, ticking fee schedule, conditions to draw, whether pro forma covenant compliance is required at draw. | |

### C. Pricing & fees

| Field | Prompt | Accuracy trap |
|---|---|---|
| Rate construct | Benchmark (Term SOFR / Daily Simple SOFR / Base Rate), tenor options, **credit spread adjustment**, opening margin per tranche, **benchmark floor**. | The CSA is usually inside the definition of "Adjusted Term SOFR", not the pricing section. The floor applies to the benchmark, not the all-in rate — a common modelling error. |
| Pricing grid | Full grid: levels, test metric (First Lien Net vs Total Net vs Secured Net Leverage — quote which), margin at each level, effective date of changes, any post-close holiday. | The grid's test metric frequently differs from the financial covenant's test metric. |
| Fees | OID/upfront, unused/commitment fee and whether it steps, LC fronting and issuance, agency, ticking, amendment fees paid to date. | |
| Default rate | Increment, automatic vs at Required Lenders' election, applies to all obligations or overdue amounts only. | |

### D. Amortisation, mandatory prepayment, call protection

| Field | Prompt | Accuracy trap |
|---|---|---|
| Excess Cash Flow sweep | Sweep %, leverage-based step-downs and levels, and the **full definition of Excess Cash Flow including every deduction**. | The deductions are where the sweep dies — voluntary prepayments, capex, permitted investments and "amounts committed to be spent" routinely net ECF to zero. Report the deduction list or do not report the sweep. |
| Asset sale prepayment | Threshold, reinvestment period, extension for amounts "committed to reinvest", step-downs by leverage, treatment of non-loan-party proceeds. | 365 + 180 day reinvestment structures make this a timing provision, not a prepayment. |
| Call protection | Soft call premium and duration, and the definition of **Repricing Transaction** — specifically whether it captures amendments and not only refinancings. | If amendments are excluded the protection is nominal. |

### E. Financial covenants and the EBITDA definition — highest value, highest risk

| Field | Prompt | Accuracy trap |
|---|---|---|
| Covenant inventory | Each maintenance and incurrence covenant: metric, level, step-down schedule with dates, first test date, frequency. For a springing revolver covenant: trigger %, whether LCs are deducted from the drawn amount, whether tested at quarter-end only. | "Undrawn ≤35% including LCs" vs "excluding LCs" changes when the covenant exists at all. |
| Equity cure | Cures permitted (per four quarters / over the life), whether proceeds increase EBITDA or reduce debt, whether over-curing is prohibited, whether cured quarters count toward consecutive-quarter tests. | A cure that adds to EBITDA carries forward four quarters and cures future periods too. |
| **EBITDA definition** | Quote the full definition. Itemise every add-back as a separate row. | — |
| **Add-back cap** | For cost savings / synergies / run-rate adjustments: state the cap as a %, and **state explicitly whether that % is calculated before or after giving effect to the add-backs themselves**. State the look-forward period (18/24/36 months) and the evidentiary standard ("reasonably identifiable and factually supportable" vs "expected"). | **Most often missed.** A cap of "20% of Consolidated EBITDA *after giving effect to such adjustments*" is not a 20% cap — it permits add-backs worth 25% of unadjusted EBITDA (1/(1−0.20) − 1). Uncapped, or capped post-giving-effect with a 36-month look-forward, is a different credit. |
| Debt / netting definitions | Definition of Consolidated Total Debt: whether cash netting is permitted, whether capped, whether netted cash must be unrestricted and held by loan parties, whether LCs and earnouts are included. | Unlimited uncapped cash netting converts a leverage covenant into a liquidity covenant. |

### F. Negative covenants and baskets

Run one pass per covenant: **Indebtedness, Liens, Restricted Payments,
Investments, Asset Sales, Affiliate Transactions, Junior Debt Prepayment,
Negative Pledge.**

> *Prompt:* For [covenant], list every exception/basket. For each, return:
> section, fixed dollar prong, grower prong (% and basis — EBITDA or Total
> Assets), ratio prong and its exact test level, and all conditions (no
> Default, pro forma compliance, loan-party-only). State whether baskets may be
> reclassified between provisions and whether unused capacity carries forward.

| Trap | Why |
|---|---|
| Grower baskets | "Greater of $50M and 25% of LTM EBITDA" — capture **both prongs**. EBITDA growth (including via the §E add-backs) silently expands every basket in the document. |
| Summing baskets | Do not sum to a total-capacity figure. Reclassification and cumulative-use provisions make a naive sum both over- and under-stated. This is class-2 — build it deliberately. |
| **Available Amount / Builder basket** | Extract separately: starting amount, builder components (50% CNI, retained ECF, equity contributions, returns on investments), and the **separate conditions applying to its use for Restricted Payments vs junior debt prepayment vs investments**. Usually the largest single leakage channel. |

### G. Structural protections — the leakage set

| Field | Prompt |
|---|---|
| Unrestricted Subsidiary designation | May the Borrower designate Unrestricted Subsidiaries? Against which basket is the designation charged? Is there an express prohibition on transferring **material IP** to an Unrestricted Subsidiary (a "J.Crew blocker")? Quote it or return NOT PRESENT. |
| Non-loan-party capacity | Aggregate cap on investments in, and asset transfers to, non-guarantor / non-loan-party subsidiaries. |
| Uptier / Serta protection | Do the pro rata sharing and payment waterfall provisions appear in the **sacred rights** list requiring all-lender or each-affected-lender consent? Are "open market purchases" of loans by the Borrower or Sponsor permitted, and on what terms? Quote the amendment section verbatim. |
| Drop-down protection | Restrictions on transferring collateral to a restricted non-guarantor subsidiary, and whether guarantees are automatically released on such a transfer. |
| Collateral & guarantee coverage | Assets pledged, excluded assets, the 65%-of-voting-stock CFC limitation, any guarantor coverage test (e.g. ≥80% of Consolidated EBITDA) and the cure mechanic if it fails. |

### H. Defaults and remedies

| Field | Prompt |
|---|---|
| EoD schedule | Payment grace periods (principal vs interest), covenant grace, representation defaults, judgment threshold, ERISA, change of control. |
| Cross-provisions | Cross-**default** or cross-**acceleration**? Threshold Amount. Does it capture hedging and non-recourse debt? |
| Change of Control | Full definition: ownership %, board composition change, whether a Sponsor exit triggers, whether a Permitted Holders construct applies. |
| MAE | Definition of Material Adverse Effect and every provision conditioned on it. |

### I. Agency, voting, transfer

| Field | Prompt |
|---|---|
| Voting | Required Lenders threshold; full sacred-rights list; provisions requiring each-affected-lender consent; class voting mechanics. |
| Assignment | Minimum assignment amounts, Borrower consent and deemed-consent period, Agent consent, **Disqualified Lender list** and whether it is made available to lenders, permitted assignments to Sponsor/Affiliates and the cap. |
| Other | Yank-a-bank / replacement lender, defaulting lender waterfall, erroneous payment ("Revlon") provision. |

### J. Synthesis — analyst-authored, model-evidenced

| Output | How it is produced |
|---|---|
| Off-market provision register | Model flags each extracted provision deviating from the internal precedent set; analyst assesses direction and materiality. |
| Covenant headroom at close | **Computed externally** from §E inputs. Cushion % to each covenant, and separately at unadjusted EBITDA. |
| Structural protection scorecard | Binary presence/absence from §G against the house standard. Purely extractive — safe to automate. |
| "Why to lend" narrative | Analyst-authored, citing the above. |

### Verification protocol specific to legal documents

Five checks no platform performs for you. Make them a pre-upload checklist.

1. **Conform before you extract.** Process the conformed agreement plus every
   amendment, or upload the original *and* all amendments together and require
   the tool to state which document governs each extracted term.
2. **Force defined-term resolution.** Require the definition alongside every
   operative provision. "Consolidated EBITDA", "Total Assets", "Permitted
   Investments" carry the actual terms.
3. **Sweep for overrides.** Search *notwithstanding / provided that / except as
   set forth in / subject to Section* near every extracted provision. Overrides
   typically sit 40+ pages from the clause they modify.
4. **Verify schedules were ingested.** A tool cannot tell you a page you never
   gave it is missing.
5. **Run the §0.4 known-answer control set.**

---

## 2. Credit Memo Matrix

### 2.0 Why this one is structurally different

A credit memo is not a CIM or an agreement, and treating it as another
extraction target under-uses it:

- **It contains conclusions, not only facts.** Every field needs a
  fact-vs-judgment tag, and every number needs its **basis** — third-party
  appraisal, quality-of-earnings report, field exam, or management projection.
  A memo that says "stabilised NOI of $4.2M" is making a different claim
  depending on which of those produced the figure.
- **It is already structured.** Per-document extraction value is low. The
  payoff is at the **corpus** level (§2.J) and in **back-testing** (§2.K) —
  which is why the 100/month volume is an asset, not a cost centre.
- **Direction matters.** Inbound memos (syndication, participation, portfolio
  or loan-sale diligence) are underwritten. Outbound memos (your own) are
  QC'd pre-IC and indexed post-close.

### 2.A Memo identity & control

| Field | Prompt | Trap |
|---|---|---|
| Memo type & stage | Classify: initial screening / full underwriting / annual review / amendment or waiver request / watch-list or workout memo. Extract memo date, author, credit committee date. | An amendment memo restates original terms as background. Extracting those as current terms is the memo analogue of the unconformed-agreement error. |
| Version | Is this the final approved version or a draft? Extract any revision marks, "pre-read" or "revised" designations. | |
| Deal identifiers | Borrower legal name, internal deal/obligor number, facility ID, industry code, originating office. | Required for the §2.J joins — without a stable key the corpus is unqueryable. |

### 2.B Borrower, sponsor, guarantors

| Field | Prompt |
|---|---|
| Obligor structure | Borrower legal entity, parent, guarantors and guarantee type (full / limited / bad-boy / payment vs collection), and any non-recourse carve-outs. |
| Sponsor / ownership | Sponsor or owner, fund and vintage, ownership %, equity cheque, prior transactions with the institution. |
| Management | Key persons, tenure, prior relationship, whether a key-person provision exists. |

### 2.C Capital structure, sources & uses

| Field | Prompt | Trap |
|---|---|---|
| Sources & Uses | Extract the complete table as presented. Include equity, each debt tranche, seller paper, earnouts, rollover equity, fees and expenses. | Rollover equity and seller notes are frequently presented as "equity" in the S&U and as debt in the covenant calculation. Extract as presented; reconcile separately. |
| Pro forma capital structure | Each layer: amount, pricing, maturity, lien position, our hold vs syndicated amount. | |
| Our position | Commitment, expected final hold, syndication or participation plan, agent vs participant role. | |

### 2.D Financials — historical, adjusted, projected

| Field | Prompt | Trap |
|---|---|---|
| Historical | Revenue, EBITDA, capex, working capital and FCF for each period presented, with period labels (FY/LTM/annualised) stated verbatim. | "LTM" and "annualised" are different claims. An annualised stub presented as LTM overstates a seasonal or ramping business. |
| Adjustments bridge | Extract the reported-to-adjusted EBITDA bridge line by line, with each add-back's amount and stated justification. | Class-1 per line, class-2 in aggregate. Do not accept a model-computed total. |
| Projections | Base case by year: revenue, EBITDA, capex, debt service, FCF, leverage. **Extract whose projection this is** — management, sponsor, or the underwriter's own case. | The single most important tag in the memo. A management case presented as the underwriting case is how a memo overstates its own conservatism. |
| Downside case | Whether a downside/stress case is presented, the stress applied, and the resulting minimum DSCR / maximum leverage. | A memo with no downside case is a finding, not a missing field — record NOT PRESENT and surface it. |

### 2.E Underwriting basis — the provenance layer

> *Prompt:* For each material figure in the memo (valuation, EBITDA, NOI,
> collateral value, projected cash flow), identify the stated source: a named
> third-party report (appraisal / quality-of-earnings / field exam /
> environmental / engineering), audited financials, management-prepared
> financials, or the underwriter's own assumption. Return the report author and
> date where named. Where no source is stated, return **SOURCE NOT STATED**.

This mirrors the provenance register this repository already applies to its own
outputs (`analysis/assumptions.py`): every number carries where it came from,
and "the memo said so" is not a source. A memo whose central valuation traces
to an unnamed assumption is a different credit from one tracing to a dated
third-party appraisal, and no other field in the matrix exposes that.

| Trap | Why |
|---|---|
| Stale third-party reports | Extract the report **date**, not only its existence. An appraisal 30 months old supporting a current advance rate is a finding. |
| Reliance language | Whether the memo states the institution is a named addressee entitled to rely on the report. |

### 2.F Credit thesis as stated

> *Prompt:* Extract verbatim the memo's stated rationale for the credit —
> strengths, competitive position, repayment sources. Return each as a separate
> item, tagged as: **supported** (an adjacent cited figure or third-party
> report backs it), or **asserted** (no support given in the memo).

Extraction, not synthesis — the memo's conclusions are facts *about the memo*.
The supported/asserted tag is the entire value of the pass: it measures the
memo's own evidentiary discipline, and it is the input to §2.K.

| Field | Prompt |
|---|---|
| Primary repayment source | Extract the stated primary source and the stated secondary/tertiary sources. |
| Exit / takeout | Stated refinancing or exit assumption and the assumed market conditions supporting it. |

### 2.G Risk register & mitigants

> *Prompt:* Extract every risk the memo identifies. For each return: the risk
> as stated, its stated severity if given, the stated mitigant, and whether the
> mitigant is **structural** (a covenant, reserve, guarantee, or condition
> precedent that appears in §2.H), **operational** (monitoring, reporting), or
> **assertional** (a statement that the risk is low). Flag any risk with no
> stated mitigant.

| Trap | Why |
|---|---|
| Assertional mitigants | "Management has a strong track record" is not a mitigant. The three-way tag is what makes the register queryable and back-testable. |
| Orphan mitigants | A mitigant described as structural must tie to an actual term in §2.H. A structural mitigant with no corresponding covenant is a drafting gap and a finding. |

### 2.H Approved terms, structure & conditions

| Field | Prompt |
|---|---|
| Approved terms | Facility amount, pricing, tenor, amortisation, fees, as approved (distinguish from as requested). |
| Covenants | Each financial covenant with level, test frequency, first test date, and stated cushion to the base case. |
| Collateral & support | Collateral, advance rate, guarantees, reserves, cash management / lockbox, deposit account control. |
| Conditions precedent | Full CP list, marked as satisfied / outstanding / waived. |
| **Policy exceptions** | Extract every stated exception to credit policy, the policy provision exceeded, the magnitude of the exception, and the stated justification and approver. Return NOT PRESENT only if the memo affirmatively states there are none. |

Policy exceptions are the field examiners open to first, and the field most
often buried in narrative rather than tabulated. Extracting it across the
corpus produces an exception register that most institutions maintain by hand
or not at all.

### 2.I Governance

| Field | Prompt |
|---|---|
| Approvals | Approvers, titles, dates, approval authority level relied on, whether the amount is within delegated authority or required escalation. |
| Dissent & conditions | Any recorded dissent, abstention, or approval granted subject to conditions — quote verbatim. |

### 2.J Corpus-level queries — where the 100/month actually pays

Per-memo extraction saves perhaps 20 minutes. The corpus answers questions that
are currently unanswerable at any cost:

- **Exposure**: aggregate commitment by industry, sponsor, geography, structure
  type, and by primary repayment source — across memos, not just the loan system.
- **Term drift**: how covenant cushions, advance rates, EBITDA add-back
  percentages and pricing have moved quarter over quarter on comparable credits.
- **Precedent retrieval**: "show every memo where we underwrote a springing
  covenant at this leverage in this industry, and what we priced."
- **Policy exception register**: every exception granted, by type, approver and
  magnitude, with trend.
- **Evidentiary discipline**: share of §2.F items tagged *asserted* rather than
  *supported*, by author and over time.
- **Risk taxonomy**: which risks we flag most, which most often carry only
  assertional mitigants.

Corpus queries are class-2 or class-3 — the extraction is per-memo and
citation-gated; the aggregation happens in a database, not in a prompt.

### 2.K Back-test schema — the highest-value use in the set

Join the extracted register from memos written 2–5 years ago to realised
performance. Fields required for the join: deal identifier (§2.A), risk
register with mitigant tags (§2.G), projection case and its owner (§2.D),
covenant levels and stated cushion (§2.H), and policy exceptions (§2.H).

Questions it answers:

1. **Projection bias** — realised EBITDA vs underwritten base case, by author,
   industry and sponsor. Distribution, not average.
2. **Which risk flags predicted losses** — of credits that later downgraded,
   defaulted or were restructured, which §2.G risks had been identified at
   underwriting, and which losses came from risks never flagged.
3. **Whether mitigants worked** — did structural mitigants get exercised, and
   did they recover value. Did assertional mitigants correlate with worse
   outcomes (the expected result; measure it rather than assume it).
4. **Whether policy exceptions cost money** — realised loss rate on credits
   with exceptions vs without, controlling for grade.

*Speculative (~75%):* items 1 and 2 are the most defensible ROI in this entire
programme, and neither requires a vendor platform — they require the extraction
schema above plus a join to the servicing system.

### 2.L Outbound QC pass (your own memos, pre-IC)

A single checklist run against a draft memo before it goes to committee:

- Does every material figure have a stated source (§2.E), or does it return
  SOURCE NOT STATED?
- Does every risk in §2.G carry a mitigant, and is each structural mitigant
  actually present in §2.H?
- Is the projection case's owner stated (§2.D)?
- Is a downside case present?
- Are policy exceptions tabulated rather than narrative?
- Do the S&U, the pro forma capital structure and the covenant calculation
  treat rollover equity and seller paper consistently?

This is the one place in the whole programme where a model's output goes
straight to a person with no analyst in between, because the output is a list
of questions, not a list of facts.

---

## 3. CIM / Offering Memorandum Matrix

### 3.1 Corporate / M&A CIMs

| Section | Field | Prompt | Trap |
|---|---|---|---|
| Transaction | Borrower & sponsor | Exact legal name of the target, parent entities, and the sponsor leading the transaction. | |
| | Sources & Uses | Extract the complete S&U table — equity contribution, each debt facility, transaction fees. | Class-1 per line. |
| | Transaction multiple | Extract the **components**: enterprise value build-up, the LTM Adjusted EBITDA line, and each add-back. | **Do not ask for the multiple.** Class-2 — compute it. |
| Financial | Adjusted EBITDA bridge | Extract reported EBITDA and every management adjustment separately, with the stated justification and whether each is recurring or one-time. | CIMs present several EBITDA variants (Management Adjusted, Pro-Forma Adjusted, Bank Defined). Name which one every downstream field uses. |
| | Historical detail | Revenue, gross margin, EBITDA, capex, working capital by period, with period labels verbatim. | |
| Thesis | Evidence table | Customer concentration (top 5 as % of revenue), revenue retention, contract lengths, pricing history, market share basis. Cited. | Class-3 if phrased as "summarise the strengths". Phrase it as an evidence request. |
| Risk | Risk / mitigant | Risks disclosed and any management mitigant, quoted. | A CIM's risk section is marketing; absence of a risk is not evidence of its absence. |

### 3.2 Real-estate CIMs

Real-estate offering memoranda take a different schema entirely — NRSF, unit
mix, physical vs economic occupancy, in-place vs market rent, expense lines per
square foot, replacement cost. For self-storage this repository already encodes
that schema end to end (`extract/parser.py`, `analysis/`, `config.py`), with
its own gate logic and provenance register, and it should not be re-specified
here.

The one field-class point that carries over: the repository already refuses to
proceed on an assumed physical occupancy (`analysis.fills.require_underwritable`)
rather than rendering a TBD and continuing. That is §0.3's NOT PRESENT rule
implemented as a hard stop, and it is the right default for any extraction
surface where a missing input would otherwise be silently invented.
