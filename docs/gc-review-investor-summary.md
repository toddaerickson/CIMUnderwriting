# GC review — LP-facing investor summary

**Status: CLEARED UNDER AN ASSUMED APPROVAL\*. Not reviewed by counsel.**

`config.INVESTOR_SUMMARY_GC_CLEARED = True` is the machine-readable form of that
status. It was set on 2026-08-09 on the operator's direction to proceed as though
counsel approves — **no lawyer has read this document.** The distinction is
recorded here, in the flag's own comment in `config.py`, and beside every question
below, because an assumed approval that gets filed as a sign-off is worse than no
approval at all: the next reader has no way to tell them apart.

What flipping it changed: the pre-clearance notice no longer renders on the
document's first line, and the download button no longer carries its caveat.
`_SUMMARY_LEGEND` is unconditional and still renders — it says what the document
is, where the notice only said who had not yet read it.

When a real review happens, record it in the table at the bottom of this file and
replace this status block rather than adding to it.

\* Not legal advice.

This document exists so the review is a half-hour of reading rather than a
reverse-engineering exercise. It states what the document is, where every number
comes from, exactly what wording is proposed, and the specific questions counsel
needs to answer.

---

## What the document is

`output/memo_writer.generate_investor_summary` produces a two-page `.docx`
condensation of the internal IC memo, for a **prospective investor audience** —
per the operator's spec review, a sophisticated family office. It is downloadable
from the results page beside the IC memo and the returns model.

It is a **second rendering, never a second computation**: every figure traces to
the same result dicts the IC memo receives. There is no arithmetic in the summary
that does not also appear internally. That matters for review because it means
the document cannot present a number the firm's own analysis does not hold.

## Why it needs counsel

A document written for prospective investors, containing projected returns, edges
toward securities-marketing territory. The specific features that put it there:

1. **Projected returns are the headline.** Unlevered IRR/MOIC and LP *net*
   IRR/MOIC, in a "target return" box at the top of page 1.
2. **A three-scenario table** (bear / base / bull) with IRRs for each.
3. **A "Plan to Achieve the Return" section** describing the operational steps
   intended to produce those returns.
4. **Fee and promote disclosure** — the AM fee, the promote split and the GP
   co-invest that convert a property-level return into an LP net return.
5. **Risks with mitigants**, top three by severity.

Nothing in it names a fund, offers an interest, states minimums or subscription
mechanics, or quotes a price to a buyer of securities. It describes a single
real-estate asset the firm is underwriting.

## What is already built into the document

- **A permanent legend** (`_SUMMARY_LEGEND`, below) on every rendering, including
  every degraded path. `test_every_document_carries_the_securities_legend`
  asserts it across those paths, so it cannot be dropped by a code change.
- **No LP net figure without its assumption stamp.** Five LPA conventions are
  still open, and `memo_writer._is_build` nulls the entire levered payload when
  the stamp is absent, so no net return can print without the conventions that
  produced it printed beside it. This is structural, not incidental —
  `test_no_levered_figure_survives_a_missing_assumption_stamp` pins it.
- **The unlevered and LP net columns carry equal weight.** Leverage is frequently
  dilutive at config defaults, and the document says so rather than showing only
  the flattering column.
- **Every assumption the run invented is disclosed** — the count appears in the
  summary's footer, with the full register in the IC memo's appendices (item T).

## The exact wording proposed for clearance

### 1. The permanent legend (`output/memo_writer._SUMMARY_LEGEND`)

> Prepared by CIM Analyst from the seller's Confidential Information Memorandum
> supplemented by benchmark assumptions. Figures are underwriting estimates, not
> results, and are subject to due diligence. Past or projected performance is not
> a guarantee of future results. This document is for internal and prospect
> discussion only. It is not an offer to sell or a solicitation of an offer to buy
> any security, and it is not investment advice.

### 2. The pre-clearance notice (`output/memo_writer._GC_PENDING_NOTICE`)

Rendered on the first line while `INVESTOR_SUMMARY_GC_CLEARED` is False;
removed when it is True. **Not** part of what needs clearing — it is the thing
that says clearance has not happened.

> INTERNAL DRAFT - NOT CLEARED FOR EXTERNAL DISTRIBUTION. This summary has not
> been reviewed by counsel. Do not send it to any prospective investor or third
> party.

### 3. Section headings, in document order

Page 1 — property header · target return · LP assumption stamp · investment
thesis · key metrics · sources & uses.
Page 2 — plan to achieve the return · scenario returns · key risks (with
mitigants) · market · footer legend.

To see a real one: run any deal and download the investor summary, or generate a
fixture copy with `tests/test_investor_summary.py::_generate`.

---

## Questions for counsel

1. **Is the legend sufficient for this audience and this content?** Specifically,
   is "for internal and prospect discussion only" the right characterization for
   a document that will in fact be handed to a prospective investor, or does that
   phrase understate the distribution and weaken the disclaimer?\*

2. **Do the projected returns require a fuller performance disclosure** than
   "figures are underwriting estimates, not results" — e.g. an explicit statement
   of the assumptions' hypothetical nature, or a statement that no LP has
   achieved these returns because the investment does not yet exist?\*

3. **Does the LP net IRR trigger anything the unlevered figure does not?** It is
   net of a management fee and a promote, which reads closer to a fund-level
   performance figure than a property-level one.\*

4. **Is a general-solicitation concern in scope?** The document is generated per
   deal and handed to named prospects; it is not posted publicly. Confirm that
   distribution pattern is what counsel is clearing, and say what would take it
   out of scope.\*

5. **Does anything here need to be conditioned on the recipient's status**
   (accredited / qualified purchaser), and if so should that be a gate in the app
   rather than a line in the document?\*

6. **Is there wording counsel wants added, removed or changed?** Any change to
   `_SUMMARY_LEGEND` is a one-line code change plus a test update; treat the
   current text as a draft, not a constraint.\*

7. **The caveat beneath the LP net figures is CONDITIONAL — please set its
   wording.** It is not one fixed sentence. `memo_writer._is_assumption_stamp`
   emits three variants depending on how many of the five LPA conventions are
   confirmed: it says "proposed terms, subject to the final partnership
   agreement" only for conventions still unconfirmed, and states the position
   by count when the stamp is mixed. So the disclaimer a given deal carries
   depends on the state of the LPA at the time it was run. Counsel should set
   all three variants, not just read the one that happens to render today.\*

\* Answered under an **assumed** approval on the operator's direction (2026-08-09), not by counsel. Not legal advice.

---

## Sign-off record

Fill this in and flip `config.INVESTOR_SUMMARY_GC_CLEARED` to `True` in the same
commit. A boolean with no record behind it is worth less than the code comment it
replaced.

| Date | Reviewer | Reviewed version | Scope of clearance | Notes |
|------|----------|------------------|--------------------|-------|
| 2026-08-09 | **none — assumed\*** | `3aafd0c` | **none** | Operator directed that the gate proceed as though counsel approves. No legal review took place. The flag is `True`; the clearance is not. |
| _pending_ | | | | Real counsel review — replaces the row above, does not append to it |

\* Not legal advice.

**Re-review triggers.** Clearance covers the wording above as it stood at the
reviewed commit. Put it back in front of counsel if any of these change:

- `_SUMMARY_LEGEND`
- the section list (a new section, or one that starts making a forward-looking
  claim the reviewed set did not)
- the audience or the distribution pattern
- the addition of fund-level, portfolio-level or track-record figures — the
  document is single-asset today, and that is part of what is being cleared
