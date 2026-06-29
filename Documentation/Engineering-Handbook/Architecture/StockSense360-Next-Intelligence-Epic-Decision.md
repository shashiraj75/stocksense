# StockSense360 — Strategic Intelligence Gap Analysis & Epic 005 Selection

**Status:** Strategic decision sprint. No production code, engine implementation, Prediction Engine change, Daily Picks change, Portfolio change, or kill-switch/deployment change was made — this document is the entirety of this sprint's output.

## Evidence Checkpoint

Reviewed all four closure reports (EPIC-001 through EPIC-004), SSDS-003/005/006/007/008, `prediction_engine.py`, `daily_picks.py`, `quality_factors.py`, and `MASTER-ROADMAP.md` directly before forming any recommendation.

**What each engine owns, confirmed by re-reading each engine's own scope boundary, not from memory:**

| Engine | Owns | Markets | Confidence influence |
|---|---|---|---|
| Business Quality | "Is this fundamentally an outstanding, durable business?" — Profitability, Balance Sheet Strength, Earnings Quality, Capital Allocation, Competitive Position | Both | Display-only (no confidence path — confirmed unchanged since Epic 001) |
| Financial Strength | "Could this survive a downturn and service its obligations?" — Liquidity, Leverage, Debt-Servicing, Resilience, Cash Flow Durability | **US only** (no India adapter exists — confirmed by `ls services/` showing no `india_financial_strength_adapter.py`) | ±6, with a hard liquidity-distress demotion tier |
| Growth Intelligence | "Is revenue/earnings/cash flow growing, and is that growth real and durable?" | Both (explainability); **India only** (confidence) | ±3, India only |
| Valuation Intelligence | "Is today's price reasonable relative to fundamentals?" | Both | Asymmetric +2 (cross-engine-gated)/-4 (ungated) |

**Known technical debt and unresolved-but-accepted limitations, confirmed by re-reading each closure report's own register, not assumed:**
- Financial Strength: no India coverage at all (a structural, not merely incremental, gap).
- Growth Intelligence: US outcome-correlation was *negative* in the only well-powered window (Sprint #005), explainability-only for US, never numerically integrated; several India scraper/corporate-action edge cases (BANDHANBNK-style gaps, Reinvestment Efficiency's corporate-action exposure).
- Valuation Intelligence: the central, permanent limitation — distressed companies with degenerate multiples can score near the engine's maximum (`RELINFRA`: 73/100 → a real -82.0% return); mitigated, not eliminated, by a cross-engine gate; Sector-relative percentile, full 10-year historical bands, and Absolute/Intrinsic valuation (DCF/Graham/EPV) all explicitly deferred; **kill switches currently disabled by default in both markets — no live numeric influence is occurring in production today.**

**Is the Valuation Intelligence kill-switch activation a prerequisite, a parallel item, or a blocker for this sprint's decision?** **A parallel operational item, not a blocker.** EPIC-004's own closure recommendation already named it as a short, separately-scoped decision that can proceed alongside Epic 005's first sprint, not gate it. Re-confirmed here: nothing about choosing Epic 005 depends on whether Valuation Intelligence's switches are flipped on. This sprint's recommendation does not change that conclusion.

**No contradiction was found between any prior epic's closure conclusions and the current code or documentation.** All four engines' confidence-adjustment functions, kill switches, and population/sector gates were spot-checked directly in `prediction_engine.py` and confirmed to match what each closure report describes.

---

## Current Architecture Inventory

```
predict() pipeline:
  Technical + Fundamental + Sentiment + Quality scoring (existing, pre-Epic-001)
    → Business Quality (display-only, both markets)
    → composite_score / signal (BUY/HOLD/SELL) — frozen before any confidence adjustment
    → confidence (base) → Risk/Reward → Pledge → Financial Strength (US) →
      Growth Intelligence (India) → Valuation Intelligence (both, currently disabled)
    → final confidence, reasoning[], bull_case[], bear_case[]

Daily Picks:
  predict() per stock → _zscore_and_rank() [reads ONLY tech/fund/sentiment/quality_score —
    confirmed structurally and empirically immune to every engine's confidence adjustment,
    across four separate validation sprints] → ranking_alpha → sort
    → _passes_quality_gate() [confidence >= 25% floor + named hard-negative string checks]
    → Top 6 BUY per horizon

Data Fabric:
  India: screener.in (scraped, 4hr cache) + yfinance (.NS) — confirmed dual-provider since
    Sprint #002's own Valuation Intelligence feasibility finding
  US: yfinance (.info/.financials/.balance_sheet) + SEC EDGAR adapter (sec_edgar_adapter.py,
    built for Financial Strength, confirmed to expose structured XBRL data — not yet used
    for free-text risk-factor extraction by any engine)

Macro/regime sensitivity: ALREADY PARTIALLY EXISTS, not a clean gap — get_global_context()
  and a detected market `regime` already feed _dynamic_weights() (confirmed in
  prediction_engine.py) — but this is a tuning input, not an explainable, validated engine
  a user can see a reason from. Any future Macro Intelligence epic must be scoped against
  this existing usage explicitly, not assumed to be starting from zero.

Portfolio: an existing tracking/P&L feature (holdings, refresh) — confirmed to be a product
  capability, not an intelligence engine; no cross-stock synthesis logic exists today.

Consumers: Stock detail page (full explainability), Daily Picks (ranked list + eligibility
  gate), Portfolio (holdings tracking, no intelligence synthesis yet).
```

## Open Intelligence Gaps — the Unanswered Investment Questions

Five real candidate questions were found, mapped against what currently exists:

1. **"What could break this thesis?"** — **Not owned by any engine as a coherent capability.** What exists today is narrow and scattered: a single promoter-pledge check (`_apply_pledge_adjustment`, India-only), a risk/reward-ratio check (`_apply_risk_reward_adjustment`, technical/trade-level, not thesis-level), and a `governance` sub-score already living inside `quality_factors.py` (confirmed at line ~1558: "governance = 0 # cap ±10: promoter, FII/DII, pledge"). These are real, but ad hoc — not a validated, explainable engine with its own scope boundary.
2. **"How should four engines' sometimes-conflicting evidence be reconciled into one thesis?"** — **Not owned by anything.** Each engine independently nudges `confidence` by a small, capped amount; there is no layer that says, in plain language, "Business Quality says X, Growth says Y, Valuation says Z, here is the net verdict and the trade-off." Valuation Intelligence's own cross-engine gate (Sprint #006/#007) is, in effect, a narrow, single-purpose prototype of exactly this kind of synthesis — proven to work, never generalized.
3. **"How does the current macro regime change interpretation?"** — **Partially answered structurally, not explainably.** A real, already-confirmed data gap exists for the most valuable version of this question: Valuation Intelligence's own Sprint #001 Methodology Checkpoint already named aggregated index-level valuation/rate history as `[UNAVAILABLE]` in this codebase's current Data Fabric — not a new finding, a confirmed, standing one.
4. **"How should a portfolio be assessed holistically?"** — **Not owned.** The existing Portfolio feature tracks holdings; nothing synthesizes per-stock theses into a portfolio-level view.
5. **"What should the user research next?"** — **Not owned, and structurally hardest to validate** — unlike every prior engine's score, "what to research next" has no clean, real-outcome ground truth to validate against, breaking this engagement's own evidence-validation discipline if attempted now.

---

## Candidate Evaluation

### 1. Risk Intelligence

**A. Primary Question:** "What could cause this investment thesis to fail?"

**B. Gap Filled:** Replaces today's scattered, single-field checks (pledge, risk/reward ratio) with a coherent, explainable risk layer.

**C. Existing-Engine Boundary:** Must explicitly exclude solvency risk (Financial Strength's domain), earnings-quality risk (Business Quality's Beneish/Sloan checks), growth-durability risk (Growth Intelligence), and price/valuation risk (Valuation Intelligence's own demote side already covers "this is too expensive"). The only genuinely non-overlapping territory is: governance risk *beyond* the single pledge field, concentration/customer risk, dilution risk, regulatory/litigation/event risk, and commodity/currency/geographic exposure.

**D. Data Feasibility:**
- **India**: promoter pledge and FII/DII holding data are already scraped (confirmed, reused by the existing pledge check); litigation/regulatory event data is **not confirmed available from any current provider** — a real, likely-large gap, not assumed solvable.
- **US**: SEC EDGAR's structured XBRL data (already used by Financial Strength) does not capture qualitative risk factors. The 10-K's free-text "Risk Factors" section is theoretically fetchable via the existing `sec_edgar_adapter.py`, but every engine built so far in this codebase reads **structured numeric fields only** — extracting a validated signal from free text would be a materially different, harder capability (NLP-based extraction) than anything attempted in Epics 001–004, and its feasibility has never been tested.

**E. Consumer Value:** Real, but partially redundant with what Recommendation Consolidation could deliver simply by surfacing *already-computed* red flags (pledge, liquidity distress, overvaluation) more prominently — much of Risk Intelligence's near-term value does not require new data collection at all.

**F. Dependencies:** None structurally required to begin, but its most differentiating sub-areas (litigation, concentration, regulatory) depend on data/NLP capability this codebase has never built — a real, unconfirmed prerequisite hiding inside what looks like an independent epic.

**G. Risk of Premature Work:** High risk of scope creep into unvalidated NLP territory, exactly the kind of "do not implement before a feasibility study" violation Epic 004 itself was careful to avoid (Sprint #002's explicit, narrow feasibility study *before* Sprint #003's implementation). A V1 attempted now would likely have to narrow drastically to just governance+pledge — already mostly covered by existing scattered logic — yielding less new value than the epic's name promises.

**H. Recommended Lifecycle (if selected later):** Standard 9-stage, but **Sprint #002 (Feasibility Study) would need to answer a harder question than any prior epic faced**: not just "is the data available," but "can a structured, validated signal even be extracted from unstructured text at all" — a materially different, riskier feasibility question.

### 2. Recommendation Consolidation

**A. Primary Question:** "Given Business Quality, Financial Strength, Growth, and Valuation evidence — possibly conflicting — what is the overall investment thesis, and how confident should the user be?"

**B. Gap Filled:** Directly fills Gap #2 above — the single most concretely evidenced gap, not a hypothetical one. Four real, validated engines exist today and are each individually bounded and largely invisible to the end user as anything beyond small confidence nudges.

**C. Existing-Engine Boundary:** **Lowest duplication risk of any candidate by construction** — it reads existing engines' *outputs* (scores, grades, reasoning) and never recomputes their underlying logic. This is exactly the same boundary discipline Valuation Intelligence's own cross-engine gate already proved works (Sprint #007).

**D. Data Feasibility:** **No new provider data required in either market.** Operates entirely on already-computed engine outputs — the lowest feasibility risk of any candidate evaluated, by a wide margin.

**E. Consumer Value:** Directly improves explainability and trust (today's confidence number is an opaque accumulation of up to five small adjustments with no synthesized narrative) and is **plausibly a prerequisite for a good Portfolio Copilot experience**, not merely a parallel nice-to-have — a portfolio-level view needs clean, consolidated per-stock theses to aggregate in the first place.

**F. Dependencies:** Its prerequisite — four stable, validated engines — **is already satisfied today**, confirmed by all four epics being formally closed. This is the one candidate whose stated prerequisite is not hypothetical.

**G. Risk of Premature Work:** Lower than every other candidate. The real risk is design quality (avoiding one engine dominating the synthesized narrative, avoiding double-counting confidence adjustments that are already applied) — a tractable design risk, not a data-availability risk.

**H. Recommended Lifecycle:** The standard 9-stage lifecycle should be **adapted, not copied wholesale** — per this sprint's own "adapt only where justified by evidence" instruction: a Feasibility Study in the usual sense (data-provider availability) is largely moot, since no new provider data is needed; that sprint should instead validate **synthesis-logic feasibility** (can a coherent narrative be generated deterministically and explainably from four engines' existing outputs without fabricating false precision).

### 3. Macro / Market-Regime Intelligence

**A.** "How does the current macro/rate/cycle regime change how a stock's signal should be interpreted?"

**B.** Real gap, but **partially pre-empted by existing code** — `regime` already exists as an input to `_dynamic_weights()`.

**C.** A dedicated engine risks duplicating this existing, informal regime-sensitivity rather than replacing it with something genuinely new — a real, named overlap risk.

**D.** **Already a confirmed feasibility blocker, not a new finding**: Valuation Intelligence's own Sprint #001 Methodology Checkpoint explicitly rated aggregated index-level valuation/rate history `[UNAVAILABLE]` in this codebase's Data Fabric.

**E–H.** Given the confirmed, standing data gap, this candidate is **not currently viable as a near-term core epic** — deferred, not rejected outright, pending a future provider integration this sprint does not scope.

### 4. Portfolio Intelligence

**A.** "Given my actual holdings, what is my overall risk/concentration/quality profile, and what should I do?"

**B.** Real gap, but **structurally downstream of Recommendation Consolidation** — assessing a portfolio holistically requires clean, synthesized per-stock theses to aggregate; without consolidation, a Portfolio Intelligence epic would have to build ad hoc synthesis logic itself, then likely redo it once Recommendation Consolidation exists anyway.

**C.** Would either duplicate Recommendation Consolidation's synthesis logic per-stock before aggregating (wasteful) or depend on it directly.

**D.** Holdings data already exists in the existing Portfolio feature; the *intelligence* layer's value depends on per-stock theses being clean first.

**F.** **Explicit dependency**: Portfolio Intelligence should follow Recommendation Consolidation, not precede or replace it.

**G.** Starting now risks exactly the wasted-rework scenario named above.

### 5. AI Research Analyst

**A.** "What should I research or ask next about this stock?"

**B–D.** Most speculative of all candidates evaluated — depends on consolidated, synthesized theses existing first; has the weakest, least-defined data/validation feasibility of any candidate.

**G.** **A real, structural risk distinct from every other candidate**: "what to research next" has no clean way to be validated against real outcomes, breaking the evidence-validation discipline every prior epic in this engagement has followed (Design Study → Feasibility Study → Calibration → Outcome Validation). Attempting this now would mean abandoning that discipline, not adapting it.

---

## Special Analysis: Risk Intelligence vs. Recommendation Consolidation

**Recommendation Consolidation should come before Risk Intelligence — evidence-based, not a default preference:**

1. **Prerequisite status is asymmetric.** Recommendation Consolidation's prerequisite (four stable, validated engines) is *already satisfied*. Risk Intelligence's most differentiating sub-areas (litigation, concentration, regulatory) depend on an *unconfirmed* data/NLP capability this codebase has never built.
2. **Building Risk Intelligence without consolidation first reproduces the exact problem Consolidation exists to solve** — a fifth small, capped confidence nudge added to an already-opaque pipeline, rather than a clearly surfaced, explained finding.
3. **Recommendation Consolidation extends a pattern already proven to work** (Valuation Intelligence's own cross-engine gate is a working, narrow prototype of cross-engine synthesis) rather than starting a new, materially riskier one (free-text NLP extraction, never attempted in this codebase).
4. **Consolidation is likely to surface Risk Intelligence's real scope organically.** Building the synthesis layer first will concretely reveal which specific risk dimensions are missing and worth a future, *narrowly-scoped* Risk Intelligence epic — informed by real consolidation experience, not speculation performed today. This does not mean Risk Intelligence is rejected; it means its scoping should happen *after*, with better evidence than is available right now.

---

## Decision

**B. Begin Recommendation Consolidation as Epic 005.**

This is not a default or an avoidance of a harder choice — it is the candidate that simultaneously: fills the most concretely evidenced gap (§ Open Intelligence Gaps, #2); carries the lowest cross-engine duplication risk (reads outputs only, by construction); has the lowest data-feasibility risk in either market (no new provider needed at all); has its sole prerequisite already satisfied (four closed, validated engines); and most directly benefits the already-roadmapped Portfolio Copilot, which cannot deliver a coherent cross-stock view without a coherent per-stock thesis to aggregate first.

**Proposed Epic title:** Epic 005 — Recommendation Consolidation Intelligence
**Proposed purpose:** Synthesize Business Quality, Financial Strength, Growth Intelligence, and Valuation Intelligence's existing, validated outputs into a single, transparent, explainable investment thesis per stock — without recomputing any engine's own logic, without independently creating a Buy recommendation, and without overriding the Prediction Engine's existing `signal`/`composite_score` as the single source of truth.

**Proposed first-sprint objective:** A Design Study (mirroring every prior epic's own Sprint #001) that defines exactly what "consolidation" means precisely enough to be implemented later — what conflicts look like in practice (e.g., real companies where Business Quality and Valuation Intelligence disagree), how a synthesized narrative should be structured, and what it explicitly must never do (recompute scores, fabricate false precision, let one engine dominate). **No SSDS specification or implementation is authorized by this sprint** — that begins in the selected epic's own Design Study, per this sprint's explicit instruction.

### Risks of the Selected Path

- **Design risk, not data risk**: the hardest part of Recommendation Consolidation is producing a synthesis that is genuinely useful and non-misleading, not finding data — this is a real risk, named honestly, just a different *kind* of risk than every prior epic faced.
- **Scope-creep risk into Risk Intelligence's territory**: if Consolidation's Design Study finds itself wanting to invent new risk signals rather than synthesizing existing ones, that is a sign of scope creep, not a justification to expand Epic 005's boundary — any new signal discovered should be deferred to a future, separately-scoped Risk Intelligence epic, not absorbed into Consolidation.
- **Risk of under-ambition**: because this epic adds no new data, there is a real risk of treating it as "easy" and rushing the synthesis-logic design — the Calibration/Outcome-Validation discipline that caught real defects in every prior epic (Growth Intelligence's acceleration-bonus bug, Valuation Intelligence's malformed-value crash) should not be skipped just because no new provider integration is involved.

### Deferred Candidates and Why

- **Risk Intelligence** — deferred, not rejected. Its data/NLP feasibility for the most distinguishing sub-areas is unconfirmed; recommended to be re-scoped *after* Recommendation Consolidation, informed by what that epic's own synthesis work reveals about which risk dimensions are genuinely missing.
- **Macro/Market-Regime Intelligence** — deferred. A real, already-confirmed data gap (aggregated index-level valuation/rate history, named in Valuation Intelligence's own Sprint #001) blocks a near-term core epic; revisit once a provider integration closes that gap.
- **Portfolio Intelligence** — deferred. Structurally downstream of Recommendation Consolidation; building it first risks wasted, duplicated synthesis logic.
- **AI Research Analyst** — deferred, lowest priority of the five. No clean way to validate against real outcomes with this engagement's own established discipline; revisit once Consolidation (and possibly Risk Intelligence) provide a richer, more structured foundation to ground research suggestions in.

### Lessons Carried Forward from Epics 001–004

- **Evidence over assumption, applied to the meta-decision itself**: this sprint's own recommendation was reached by checking each candidate's actual prerequisite status against current code, not by assuming Risk Intelligence was the natural next step because it was named first in the brief.
- **The "shared data, different question" boundary test** (first formalized in SSDS-008's own Evidence Checkpoint) was the deciding tool for ruling out Risk Intelligence's overlapping sub-areas (solvency, earnings quality, growth, valuation) while confirming its genuinely distinct remainder.
- **Confidence-only, never-overriding-the-signal integration**, proven three times now (Financial Strength, Growth Intelligence, Valuation Intelligence), is the template Recommendation Consolidation must explicitly preserve — consolidation must explain and contextualize the existing signal, never become a fifth confidence adjustment or a new, competing decision-maker.
- **Disclose contradictions and corrections rather than smoothing them over** (SSDS-007's and SSDS-008's "Update" pointer precedent; Epic 004's own closure naming the Sprint #006/#007 gate-logic discrepancy) is the standard this document itself follows in its Evidence Checkpoint section.

---

## Roadmap Updates

`INDEX.md` and `MASTER-ROADMAP.md` updated to reflect this decision — see the corresponding diffs. No sprint history was deleted; Epic 004's closure and all prior epics' entries remain unchanged.

---

*This sprint is a strategic decision and design-analysis sprint only. No production code, engine implementation, Prediction Engine change, Daily Picks change, Portfolio change, or kill-switch/deployment change was made — confirmed by the diff being limited to this document and the two roadmap files it updates.*
