# Financial Document Extraction — Build vs Buy Decision Record

**Date:** 2026-09-19
**Scope:** CIMs / Offering Memoranda, Credit Memos, Loan Credit Agreements
**Working volume:** ~30 CIMs, ~100 credit memos, ~20 legal documents per month
**Companion:** `docs/doc-extraction-matrices.md` (the extraction schemas)

This is a decision record, not a recommendation to purchase. It states what was
verified, what was not, and what still has to be measured before money is
committed.

---

## Executive summary

- **Four vendor claims that reached this evaluation were wrong.** Two named
  products serve entirely different markets from the ones described (§1). The
  corrections are recorded because the same names will resurface.
- **Inference cost is not the constraint.** Running every document through
  Claude Opus 5 with prompt caching costs roughly **$300/month** at this volume
  (§3). Enterprise platform licensing is one to three orders of magnitude above
  that. Cost is therefore not a reason to choose a cheaper model or a thinner
  schema — analyst review time dominates either way.
- **One hard API constraint shapes the architecture** and is not documented in
  any vendor material: native source citations and structured JSON output are
  mutually exclusive in a single call (§4.2). For class-1 legal fields, where
  the citation *is* the control, citations win and the JSON is assembled
  downstream.
- **The recommendation is a measured bake-off, not a purchase** (§6). The only
  number that should decide this is field-level accuracy on a five-document
  known-answer control set, and nobody has run it yet.

---

## 1. Vendor claims — verified, corrected

Verified 2026-09-18 against current public sources. Confidence stated because
vendor positioning moves and the knowledge cutoff for unverified claims is
May 2026.

| Claim as received | Status | Correction |
|---|---|---|
| "FinChat AI — designed specifically for institutional financial document extraction; upload non-public CIMs and credit memos" | **False** (~95%) | FinChat rebranded to **Fiscal.ai** mid-2025. It is a *public equities* research terminal built on S&P Global Market Intelligence data — ~100,000 listed companies, filings, transcripts, KPIs, screeners. Not a private-document ingestion platform. Do not shortlist. |
| "Kili Technology — out-of-the-box UI for financial institutions; human-in-the-loop review of loan agreements" | **Wrong category** (~90%) | Kili is an **ML data-labelling and annotation platform** (image, video, text, PDF, geospatial). Its human-in-the-loop is annotator QA for training-set construction, not analyst review of deal terms. |
| "Hebbia — Iterative Source Decomposition (ISD)" | **Real term, vendor-defined** (~90%) | ISD is genuinely Hebbia's own terminology for its non-chunking document-reasoning architecture and is the stated mechanism behind sentence-level citations. It is a self-described architecture with no independent benchmark. Test it; do not select on it. |
| "Ocrolus — guarantees 99%+ data accuracy on credit memos" | **Misstated** (~85%) | The 99%+ figure is **character-level OCR accuracy**, not field-level extraction accuracy — a materially different and lower number. Ocrolus's product line is bank statements, paystubs, tax forms and mortgage documents: consumer and small-business lending. Not a credit-agreement tool. |
| "Alkymi — extracting data from OMs and CIMs" | **Understated** (~85%) | Real private-markets document platform (~44 staff, $26M raised, Series B Aug 2025), historically capital notices, account statements and schedules of investments. It launched **Alkymi Private Credit** in Feb 2026 for loan-document workflows. Relevant to the credit-memo leg. |
| "6–10 hours per credit agreement, compressed to under 2–3 minutes" | **Unsourced** | Market-adjacent figures cite 15–25 minutes per agreement for covenant extraction and setup against 1–2 analyst-days manual. The 2–3 minute figure describes a single parse, not a usable extraction. Budget analyst review at **45–90 minutes per agreement**, not zero. |

**Names that belong on a credit/legal shortlist and were absent** (~80% each):
**Xtract Research** (ION Analytics) and **Covenant Review** (Fitch) — lawyer-authored
covenant research rather than extraction software; **Octus** (formerly Reorg) —
credit intelligence; **Harvey**, **Legora** — generative legal AI; **Kira**
(Litera), **Luminance** — mature contract-extraction engines; **Rogo** —
finance-specific analyst agent.

---

## 2. What is already built here

This repository runs a document-to-underwriting pipeline for self-storage CIMs:

| Component | Location |
|---|---|
| PDF text and table extraction | `extract/pdf_reader.py`, `extract/tables.py` |
| Vision-model transcription for pages with no text layer | `extract/ocr.py`, `extract/vision.py` |
| Structured parse to a typed record | `extract/parser.py` |
| Provenance register — every output number tagged with one of six origins | `analysis/assumptions.py` |
| Refusal on missing required inputs, rather than assumption | `analysis/fills.py` |

The provenance register is the part with no commercial equivalent. It answers
*where did this number come from, and did a human or a fallback produce it* at
the level of each individual figure. Sentence-level citations — the strongest
control any platform in §1 offers — answer a narrower question: where in the
document a passage sits. They say nothing about a number the model inferred,
defaulted, or that an analyst later overtyped.

That distinction is the whole argument for §2.E of the matrices document.

---

## 3. Cost model

### 3.1 Stated assumptions

- Credit agreement ≈ 200 pages ≈ **130,000 input tokens**; ~40 extraction prompts.
- Credit memo ≈ 30 pages ≈ **20,000 tokens**; ~30 prompts.
- CIM ≈ 100 pages ≈ **70,000 tokens**; ~25 prompts.
- Output ≈ 1,200–1,500 tokens per prompt.
- Claude Opus 5 at **$5.00 / MTok input, $25.00 / MTok output**.
- Prompt caching: cache writes ≈ 1.25× input rate, cache reads ≈ 0.1× input rate.

### 3.2 The caching effect, on one credit agreement

| Approach | Input | Output | Total |
|---|---|---|---|
| No caching — 40 prompts × full document | 5.2M tok × $5/M = **$26.00** | $1.50 | **$27.50** |
| Cached — document written once, 40 cache reads | write $0.81 + reads $2.60 = **$3.41** | $1.50 | **$4.91** |

**82% reduction, same model, same prompts.** Caching is a prefix match: the
document must be the stable prefix and the varying question must come after the
last cache breakpoint. Verify with `usage.cache_read_input_tokens` — a
persistent zero means a silent invalidator (a timestamp in the system prompt, a
varying tool set, unsorted JSON).

### 3.3 Monthly run-rate, all three document types

| Type | Volume/mo | $/doc (cached) | $/mo |
|---|---|---|---|
| Credit agreements | 20 | $4.91 | $98 |
| Credit memos | 100 | $1.33 | $133 |
| CIMs | 30 | $2.25 | $68 |
| | | **Total** | **≈ $300/mo (~$3,600/yr)** |

Sensitivities: Claude Sonnet 5 at $2/$10 runs roughly **$120/month** for the
same schema — worth measuring on class-1 verbatim fields, not assumed. The
Batch API halves cost for non-interactive work, which is the right vehicle for
the §2.K back-test back-fill over years of historical memos, where latency is
irrelevant.

### 3.4 What this number excludes, and why it still decides the question

Excluded: engineering build, ongoing maintenance, and the analyst review time
that dominates under every option — roughly **20–35 hours/month** at this volume
if review is done properly.

*Speculative (~70%):* enterprise platform licensing at this seat count sits in
the low-to-mid six figures annually; verify directly, as none of the vendors
publish it. The conclusion does not turn on the precise figure. **Inference is
not the cost driver at this volume**, which means cost should not drive model
choice, schema depth, or the decision to skip the verification protocol.

---

## 4. Architecture constraints that are not in any vendor material

### 4.1 Cache window

The default cache TTL is 5 minutes; a 1-hour TTL is available at a different
write multiplier (verify before relying on it). The 40 prompts for one
agreement must therefore fan out **in parallel inside the window**, not run
sequentially over an analyst's afternoon. A serial design silently pays the
uncached rate in §3.2's first row.

### 4.2 Citations and structured outputs are mutually exclusive

Setting `citations: {enabled: true}` on a document block returns cited passages
with API-verified `page_location` anchors — precisely the "no citation, no
acceptance" control the protocol requires. Setting `output_config.format`
returns schema-valid JSON. **Requesting both in one call returns a 400.** Three
resolutions, with a real trade-off:

| Option | Anchor quality | Output shape | Use for |
|---|---|---|---|
| (a) Citations on, parse text downstream | **API-verified** page anchors | Hand-assembled | **Class-1 legal fields** — the citation is the control |
| (b) Structured output with `cited_text` / `page` fields in the schema | Model-reported, unverified | Clean JSON | Class-1 fields where a wrong anchor is recoverable |
| (c) Two passes — structured extract, then a citations-enabled verification call | Verified, at 2× calls | Clean JSON | The §E add-back cap and §G structural set, where an error is expensive |

Recommendation: (a) for the credit-agreement matrix, (b) for credit memos and
CIMs, (c) for the dozen fields in §E and §G that move money.

### 4.3 Document input limits

PDF input caps at 32 MB per request and 600 pages on 1M-context models. A
300-page agreement with schedules fits; a document set assembled from an
original plus five amendments may not — upload via the Files API and reference
by `file_id` rather than re-encoding per call.

### 4.4 Model choice

Default to **Claude Opus 5** ($5/$25, 1M context). Class-1 verbatim extraction
under a strict schema is the workload most likely to hold quality on
**Claude Sonnet 5** ($2/$10) — but that is a measurement, not an assumption, and
§3.3 shows the saving is ~$180/month against a review burden of 20–35 hours.
Measure it on the control set before trading any accuracy for it.

---

## 5. Gates that sit outside the technical evaluation

**NDA and third-party disclosure.** Most CIM non-disclosure agreements restrict
disclosure to defined "Representatives" — usually but not always including
advisors and agents, and not always with a subcontractor flow-down. Uploading a
CIM to a SaaS vendor **is** a disclosure. SOC 2 Type II is an information
security control, not permission. Counsel should confirm the standard NDA form
covers processor use before deployment.
*Failure mode:* a seller's counsel asks where their CIM went during a live process.

**Documentation exposure on synthesized text.** Covered in
`docs/doc-extraction-matrices.md` §0.2. The policy decision — which memo
sections may be machine-drafted — should be made before the tooling, not after.

---

## 6. Recommendation

**Do not select a vendor on marketing.** Run a six-week bake-off on a
**five-document known-answer control set** already abstracted by hand, scored on
**field-level accuracy** — the share of extracted fields exactly right — split
by field class. One internal pipeline arm, one platform arm, same documents,
same schema from the matrices document.

Per document type, the current reading:

| Type | Volume | Leaning | Reasoning |
|---|---|---|---|
| **Self-storage CIMs** | ~30/mo | **Extend this repository** | The schema, gates and provenance register already exist here. No platform offers per-number provenance. |
| **Non-self-storage CIMs** | part of the 30 | **Buy or trial** | The repo's schema does not generalise across asset classes. |
| **Credit memos** | ~100/mo | **Build** | The value is corpus-level and in back-testing (matrices §2.J–2.K), joined to internal servicing data. Nobody sells that, because nobody else has the corpus. |
| **Credit agreements** | ~20/mo | **Buy the research; trial the software** | Covenant extraction depends on a clause taxonomy and precedent corpus that 20 documents/month cannot bootstrap. Xtract Research / Covenant Review supply market context that no extraction tool does. |

Sequencing:

1. **Now** — assemble the control set; freeze the matrices as the extraction
   schema; get counsel's read on the §5 NDA question.
2. **Weeks 1–6** — bake-off, scored on field-level accuracy. Decide on the
   number, not the demo.
3. **After** — back-fill the memo corpus via the Batch API and run the §2.K
   back-test. This is the item with the best expected return and the least
   vendor dependency, and it can start before any purchase decision.

---

## Open questions

- Platform licensing at this seat count — unpublished; requires direct quotes.
- The 1-hour cache TTL write multiplier (§4.1) — verify before designing around it.
- Whether §2.K's join to servicing data is feasible with current internal keys.
- Whether the CIM flow is concentrated in self-storage or spans asset classes;
  this determines the CIM row in §6 and has not been established.
