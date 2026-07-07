# Research Analyst — Stock Movement Explanation Engine: Functional Specification

## 0. Status and Purpose

**Status: Planned / Not Started. This is a functional specification only — no production code, API route, database schema, UI component, or scoring logic described in this document exists today.** Nothing in this document authorizes implementation, deployment, model-provider selection, or a financial-advice claim of any kind.

This document specifies, in advance of any build, what a "why did this stock move today?" capability would do, how it would reason, what it would and would not claim, and how it would fit the platform's existing architecture. It formalizes and extends the roadmap entry in [`MASTER-ROADMAP.md`](../../MASTER-ROADMAP.md) ("Planned Cross-Cutting Initiative — Stock Movement Explanation Engine") into a full engineering specification, and is written to be consistent with, and subordinate to, [EPIC-008 — AI Research Analyst: Concept and Safety Specification](EPIC-008-AI-Research-Analyst-Concept-and-Safety-Specification.md).

This feature is deliberately named **"Research Analyst — Stock Movement Explanation Engine"** rather than a standalone widget name, because its intended home is the future AI Research Analyst / Intelligence Engine layer (Section 15), not an isolated stock-detail-page feature. It must not be built as, or before, its architectural placement (Section 15) is resolved.

## 1. Vision and Objectives

**Vision.** When a user opens a stock detail page and sees the day's price move, they should be able to ask — and be answered — "why?" in the same evidence-tiered, non-fabricating, non-advisory voice the rest of the platform already uses.

**Objectives:**
- Detect and label today's price-move direction (Up / Down / Flat) using data the platform already computes.
- Explain the likely drivers of that move using evidence the platform already has or can reliably retrieve — price action, volume, sector movement, index movement, news, corporate announcements/earnings, technical levels, sentiment, and macro context.
- Keep every explanation honestly tiered into **confirmed facts**, **likely explanations**, and **unknowns** — never a single confident narrative.
- Attach an explicit confidence level to every explanation, distinct from the Prediction Engine's own signal confidence.
- Never present the explanation as investment advice.

**Non-objective.** This is not a new prediction signal, not a replacement for the Prediction Engine's BUY/HOLD/SELL logic, and not a recommendation surface. It explains a fact that already happened (today's price move); it does not forecast the next one.

## 2. User Journeys

**Journey A — Curious retail user, normal move.** User opens a stock detail page, sees "+2.4% today," clicks (or the section is already expanded as) "Why did this stock move today?" and reads a plain-language, tiered explanation with a confidence level and disclaimer. No further action implied.

**Journey B — User has a Daily Pick that moved sharply.** User owns/watches a stock that appeared in a recent Daily Pick; it moves >3% intraday. The explanation surfaces both the day's drivers and (where available) whether the move is consistent with, or in tension with, the pick's own stated thesis (read-only cross-reference, never a re-scoring).

**Journey C — No identifiable driver ("flat" or "unexplained").** User opens a stock that moved a small amount with no news, no sector move, no index move. The explanation must render the "unknown / no major catalyst identified" state explicitly, rather than manufacturing sector/technical noise into a false narrative.

**Journey D — Paper Trading review.** A user reviewing a closed or open Paper Trade position (Section 15.4) wants to understand why a position moved on a given day, e.g. during a post-trade review. The same explanation, keyed by symbol + trade date, is reused rather than a separate paper-trade-specific engine being built.

**Journey E — Future Research Analyst conversation.** Once Epic 008 exists, a user asks the conversational analyst "why did X move today?" in natural language; the analyst's answer is grounded in this engine's own structured evidence output (Section 9), not a freeform LLM guess (see Section 15.1).

## 3. Functional Requirements

**FR-1 — Direction detection.** Classify today's move as Up / Down / Flat using a configurable, evidence-grounded threshold (not a fixed ±0.1% cutoff assumed without data — see Section 12 open question on threshold calibration by asset volatility class).

**FR-2 — Multi-source driver evaluation.** Evaluate, independently, each of: price action, volume-vs-average, sector movement, index movement, news, earnings/corporate announcements, technical levels, sentiment, and applicable macro factors (Section 5). Each source produces its own evidence item(s), never a single blended score.

**FR-3 — Tiered evidence classification.** Every driver candidate must be classified as `confirmed`, `likely`, or `unknown` before it can appear in an explanation (Section 8).

**FR-4 — Confidence scoring.** Produce one explicit confidence level (Section 9) per explanation, computed from evidence coverage and tier composition, not asserted freehand.

**FR-5 — Reasoning-mode differentiation.** The narrative structure must differ for Up / Down / Flat outcomes (not a single template with the sign flipped) — see the existing roadmap entry's "Three distinct reasoning modes."

**FR-6 — Explicit unknown state.** When no supporting evidence exists for a driver category, the category must render an explicit "no data / no news found" state rather than being silently omitted (silent omission reads as "nothing else mattered," which is itself an unverified claim).

**FR-7 — Disclaimer.** Every rendered explanation must carry the standard, reused (not newly invented) platform disclaimer language — "analytical explanation only, not financial advice" — consistent with Daily Picks' and Paper Trading's existing disclaimer conventions.

**FR-8 — Snapshot integrity.** An explanation must be reproducible from the same as-of data it was generated from (mirroring RCI's own snapshot-vs-live requirement, [Recommendation Consolidation Traceability and Versioning](Recommendation-Consolidation-Traceability-and-Versioning.md)) — a user reloading the page minutes later should not see a silently different explanation for the same trading day.

**FR-9 — No prediction leakage.** The explanation must never restate or repackage the Prediction Engine's forward-looking signal as if it were a cause of today's already-realized move (a BUY signal is not evidence for why the stock moved today; it is a separate, forward-looking artifact).

## 4. Non-Functional Requirements

- **Truthfulness over completeness.** An incomplete but honest explanation (heavy on "unknowns") is always preferable to a complete-looking but partially fabricated one. This is the same standing principle already enforced for `TRACKING_ONLY_SYMBOLS` (see `STOCKSENSE_DOCUMENTATION.md`'s "Caught live" ETF-fabrication entry).
- **Fail-open, never-fabricate.** Missing data must produce an `unknown` evidence tier, never a manufactured negative or positive signal — the same discipline already applied in `backend/services/intelligence_engine/candidate_data.py` (`has_volume` defaults to unknown/true, never a fabricated `False`).
- **No new production contract changes without disclosure.** Any integration must prefer reading data already computed by existing engines (Daily Picks, Heatmap, Prediction Engine, Intelligence Engine) over adding new fetches, per this codebase's established low-blast-radius integration pattern (see Phase 3B-B's `candidate_data.py` precedent).
- **Zero impact on existing production surfaces until explicitly activated.** Must be buildable and testable behind a kill switch with zero effect on Daily Picks, Prediction Engine, RCI, or Paper Trading unless and until separately approved for each.
- **Auditability.** Every explanation must retain enough structured metadata (Section 9, Section 16) to reconstruct after the fact why it said what it said — the same auditability bar EPIC-008 §14 sets for a future conversational analyst.
- **Latency.** See Section 13.
- **Market and horizon correctness.** Must never blend India and US data, currencies, or trading calendars into a single explanation; must respect each market's own trading-session hours and holiday calendar for defining "today."

## 5. Data Sources

| Source | Existing platform asset to reuse | Status today |
|---|---|---|
| Price action (OHLC, % change) | Existing per-symbol quote data already fetched for Prediction Engine / Stock Detail page | Available |
| Volume vs. average | Existing volume field(s) already retrieved for prediction/quality inputs | Available, not currently retained/compared to a rolling average outside prediction internals — needs a small derivation, not a new fetch |
| Sector movement | `backend/services/heatmap_service.py` sector grouping | Available |
| Index movement | `MarketStatusBar` / `IndexBar` (Nifty 50/Sensex, S&P 500/NASDAQ) | Available |
| News (headline-level, dated) | **No confirmed reliable, per-article-timestamped news source today** | **Unconfirmed — feasibility question, see Section 12** |
| Earnings / corporate announcements | Partially available via existing fundamentals refresh cadence; a dated "announcement happened today" feed is not confirmed | **Unconfirmed — feasibility question** |
| Technical levels (support/resistance, moving averages, 52-week range) | Existing technical-indicator computation used elsewhere in the platform | Available |
| Sentiment | Existing VADER-based sentiment pipeline | Available |
| Macro factors | Existing `global_context` block (`dxy`, `vix`, `usdinr`, `india_vix`, etc.) already computed for Daily Picks | Available |
| Prediction/quality context (read-only, never re-derived) | Prediction Engine output, Intelligence Engine data-confidence output | Available, read-only |

The two "Unconfirmed" rows are the feature's single largest open feasibility risk and must be resolved by a dedicated Design Study (mirroring Epic 003's own India Feasibility Study precedent) before any News/Sentiment-driver code is written. Until resolved, the News/Earnings driver categories must render as `unknown` by design, not be stubbed with fabricated content.

## 6. Explanation Pipeline

```
1. Input resolution
   - symbol, market, trading date (defaults to latest completed/most-recent session)
   - resolve today's OHLC, % change vs. prior close, volume vs. rolling average

2. Direction classification
   - Up / Down / Flat, using a configurable threshold band

3. Parallel evidence collection (each independent, each fail-open)
   - Sector evidence      (Heatmap)
   - Index evidence       (MarketStatusBar/IndexBar)
   - Technical evidence   (existing indicator computation)
   - Sentiment evidence   (VADER pipeline)
   - Macro evidence       (global_context, only where applicable to the stock)
   - News evidence        (only once Section 5's feasibility question is resolved)
   - Earnings/corporate evidence (only once Section 5's feasibility question is resolved)

4. Evidence tiering
   - Each collected item classified confirmed / likely / unknown (Section 8)

5. Confidence scoring
   - Computed from tier composition + source coverage (Section 9)

6. Narrative assembly
   - Reasoning-mode-specific template (Up/Down/Flat) fills in tiered evidence
   - Explicit unknown-state rendering where a category has no evidence

7. Disclaimer + metadata attachment
   - Standard disclaimer, as-of timestamp, source list, confidence level

8. Response return (read-only; nothing here mutates any existing engine's output)
```

No step in this pipeline is permitted to call back into, or mutate, the Prediction Engine's cache, Daily Picks' payload, or RCI's composer — it only reads already-computed values, mirroring the read-only composer pattern established by [RCI's Live Stock Analysis Integration Readiness decision](Recommendation-Consolidation-Live-Stock-Analysis-Integration-Readiness.md).

## 7. AI Reasoning Methodology

**Deterministic-first, generative-last.** Steps 1–5 of the pipeline (Section 6) are deterministic, rule-based computations over structured data — the same style as the existing rule-based Explainability Layer (`case_generator.py`), not an LLM guessing at causes. Only the narrative-assembly step (6) may eventually use a constrained natural-language template or, in a later phase, a grounded LLM layer — and even then, only to phrase already-computed, already-tiered evidence in plain language, never to invent new evidence or override a tier classification the deterministic layer already assigned.

This mirrors EPIC-008 §3's rule: "must consume validated evidence rather than recreate, override, or secretly recalculate engine outputs." The Stock Movement Explanation Engine's own deterministic tiering output is exactly the kind of "validated evidence" a future conversational Research Analyst (Epic 008) would consume, not recompute.

**No model call may alter a fact.** If a future generative-narrative layer is introduced (Phase 4, Section 17), it operates strictly as a renderer over the already-computed evidence tiers and confidence level — a numeric or tier value the deterministic layer produced must never be silently changed by the generative layer's own phrasing choices. This is a hard architectural boundary, not a style preference.

## 8. Evidence Ranking Model

Each candidate driver is scored on two independent axes before being tiered:

- **Directional consistency** — does this evidence point the same direction as the observed price move? (e.g., sector up + stock up = consistent; sector down + stock up = inconsistent, and must be disclosed as a divergence, not hidden.)
- **Attribution strength** — how directly does this evidence explain *this specific stock's* move, versus explaining the whole market/sector generically? (e.g., a stock-specific dated headline > sector move > broad index move, in attribution strength, all else equal.)

**Tiering rule (must be applied mechanically, not by narrative judgment):**
- `confirmed` — the underlying fact itself is directly observed and real (today's actual price, volume, sector %, index %, or a dated news/earnings item that genuinely exists) — this tier describes the *fact*, not the *causal link*.
- `likely` — a plausible causal link between a `confirmed` fact and the price move, stated as a hypothesis (e.g., "sector-wide weakness likely contributed") — never asserted as certain.
- `unknown` — no supporting evidence exists in any evaluated category, or the only available evidence is directionally inconsistent with no resolving explanation.

**Ranking for display** (which drivers appear first in "Main Drivers"): stock-specific dated evidence (news/earnings) first if present and confirmed; then technical/volume evidence; then sector; then index; then macro — ordered by attribution strength, not by which was computed first. An `unknown` state is always shown, never omitted, per FR-6.

## 9. Confidence Scoring

**Confidence is a category (High / Medium / Low), not a numeric score presented with false precision** — consistent with RCI's own decision (SSDS-009) to decompose confidence into visible dimensions rather than one new blended number.

**Computation inputs:**
- Count and tier composition of collected evidence (more `confirmed` evidence → higher confidence; evidence composed mostly of `unknown` → Low by construction).
- Whether any `confirmed` evidence is stock-specific (news/earnings) versus only market/sector-level (stock-specific evidence raises confidence; sector/index-only evidence caps confidence at Medium, since it explains the *market*, not necessarily *this stock*).
- Presence of directionally inconsistent evidence (any unresolved inconsistency caps confidence at Low).

**This confidence level is distinct from, and must never be confused with or silently blended into, the Prediction Engine's own signal confidence** — the two answer different questions ("how well is today's move explained" vs. "how confident is the forward-looking signal") and must be labeled separately anywhere both appear on the same page.

## 10. API Contract

**Illustrative only — no route exists today.** A future read-only endpoint, e.g. `GET /api/stocks/{symbol}/movement-explanation?market=IN|US&date=YYYY-MM-DD` (date optional, defaults to latest session), would return:

```json
{
  "symbol": "string",
  "market": "IN | US",
  "trading_date": "YYYY-MM-DD",
  "as_of": "ISO-8601 timestamp",
  "direction": "up | down | flat",
  "price_move_pct": 0.0,
  "price_move_summary": "string",
  "drivers": [
    {
      "category": "sector | index | technical | sentiment | macro | news | earnings",
      "tier": "confirmed | likely | unknown",
      "statement": "string",
      "source_as_of": "ISO-8601 timestamp | null"
    }
  ],
  "technical_explanation": "string | null",
  "news_sentiment_explanation": "string | null",
  "sector_market_context": "string | null",
  "conclusion": "string",
  "confidence_level": "high | medium | low",
  "confidence_rationale": "string",
  "disclaimer": "string (fixed, reused text)",
  "engine_version": "string",
  "generation_job_id": "string | null"
}
```

Contract rules, mirroring RCI's and the Intelligence Engine's own established conventions: all fields optional/nullable on the consumer side; unknown future fields must be ignored by any client; a failure to compute this endpoint must never affect any other endpoint or page section (it is purely additive, read-only, and independently cacheable); no field may recompute or override a Prediction Engine or RCI value — it may only reference them by read.

## 11. UI/UX Proposal

**Placement:** a new, collapsible section on the Stock Detail page titled **"Why did this stock move today?"**, positioned below the price/AI-signal header block, consistent with RCI's own Evidence Summary placement precedent (below the key-metrics card).

**Structure (mirrors the roadmap entry's original mockup):**

```
Today's Price Move
  +2.4% (₹1,245.60 → ₹1,275.50) on 1.8x average volume

Main Drivers
  1. [confirmed] Sector (IT) up 1.9% today across the board
  2. [likely]    Weak rupee (₹95.2/USD) — tailwind for IT exporters
  3. [unknown]   No company-specific news found for today

Technical Explanation
  Price broke above its 50-day moving average on above-average volume.

News/Sentiment Explanation
  No dated news found for this stock today — sentiment score unchanged from yesterday.

Sector/Market Context
  IT sector +1.9% · Nifty 50 +0.6% — stock outperformed both.

Conclusion
  The move appears to be primarily sector- and macro-driven rather than
  company-specific; no confirmed company-specific catalyst was found.

Confidence Level: Medium
  (sector/index/technical data confirmed; no company-specific news to
  corroborate — see "unknowns" above)

Disclaimer: This explanation is analytical and educational only — it is
not investment advice, and does not predict future price movement.
```

**Visual tiering requirement:** `[confirmed]` / `[likely]` / `[unknown]` tags must be visually distinct (not just textual), mirroring RCI's own UI design decision (Recommendation Consolidation Live Stock Analysis UI Design) that the tier distinction must be visible in the UI, not just internal to the reasoning logic.

**Reuse, don't invent:** reuse the existing `DisclosurePanel` accessible expand/collapse primitive built for RCI's Evidence Summary (`frontend/src/components/EvidenceSummary.tsx`'s supporting infrastructure) rather than building a second collapsible-panel component.

## 12. Edge Cases

- **No evidence in any category** — render the full "unknown" state across all driver categories; confidence must be Low; must not suppress the section, since "we looked and found nothing" is itself meaningful information, distinct from not having looked at all.
- **Directionally inconsistent evidence** (e.g., sector down, stock up) — must be disclosed as a named divergence ("stock moved against its sector today"), never silently dropped or silently resolved into a false consensus, mirroring EPIC-008 §10's rule on disagreement between evidence sources.
- **Market holiday / no trading session** — must not fabricate a "flat" explanation for a day with no session; must render an explicit "market was closed" state.
- **Stock recently listed (insufficient history for average volume / technical levels)** — technical and volume-comparison categories must degrade to `unknown`, not to a fabricated baseline.
- **Circuit-breaker / trading halt days** (India upper/lower circuit, US market-wide halts) — must be named explicitly as a distinct, confirmed structural fact, not folded into a generic "high volatility" narrative.
- **`TRACKING_ONLY_SYMBOLS` / ETF-classified instruments** — must inherit the same early-return/non-fabrication discipline already enforced elsewhere for these symbols; this feature must not become a second place where the ETF-fabrication defect class could reappear.
- **Multiple conflicting news items on the same day** — must present them as multiple distinct `confirmed` items, not merge into a single averaged narrative.
- **Stale or delayed quote data** — must disclose staleness (reusing the existing quote-freshness concept already computed in `backend/services/intelligence_engine/candidate_data.py`'s `quote_age_days`) rather than presenting a stale price silently as today's move.
- **Threshold calibration for "flat"** — the exact % band that separates "flat" from "up/down" likely needs to vary by the stock's own historical volatility (a low-volatility utility stock's "normal" daily range differs from a high-beta small-cap's) rather than one fixed global threshold — an open calibration question for the Design Study phase (Section 17), not resolved by this document.

## 13. Performance Targets

- Deterministic pipeline (Sections 6 steps 1–7, excluding any future generative-narrative layer) should target **well under 500ms** added latency per request, since every input in Section 5 marked "Available" is already computed elsewhere in the platform and should be read, not re-fetched.
- If a News/Earnings provider is added (pending Section 5's feasibility resolution), its latency budget and caching strategy (reusing the platform's existing 4-hour-cache precedent used by India's screener.in fetch) must be evaluated separately before it is added to the request-time path — a slow news fetch must not block the deterministic portion of the response; graceful degradation to `unknown` for that category must be the fallback, not a blocking wait.
- Must not measurably regress Stock Detail page load time; this section should load lazily/asynchronously after the primary price/signal content renders, the same pattern RCI's Evidence Summary already uses.

## 14. Future Enhancements

- A grounded generative-narrative rendering layer (Section 7), strictly constrained to phrasing already-tiered evidence.
- Historical "movement explanation" review — letting a user look back at why a stock moved on a past date (directly reusable by Paper Trading's post-trade review, Section 15.4).
- Cross-stock/sector-wide "what moved the sector today" rollup, built from the same per-stock evidence collection rather than a separate computation.
- A user-feedback loop ("was this explanation helpful / accurate?") feeding into future accuracy validation, mirroring the evaluation-harness discipline EPIC-008 §15 requires for the eventual conversational layer.
- Eventually, full absorption into the AI Research Analyst as a queryable capability rather than a separate fixed-layout section (Section 15.1).

## 15. Integration With

### 15.1 Intelligence Engine

**This is the primary architectural point of this specification: the Stock Movement Explanation Engine must be built as a future consumer/component of `backend/services/intelligence_engine/`, not as a standalone, independently-hardcoded widget.**

Concretely:
- The confirmed/likely/unknown evidence-tiering discipline (Section 8) is the same tiered-evidence discipline the Intelligence Engine's gates already established (Instrument Type, Tradability, Liquidity, Data Confidence — see [Current-Release-Status.md](../Operations/Current-Release-Status.md)). A future implementation should evaluate reusing the Intelligence Engine's existing `data_confidence.py` scoring primitives (quote freshness, fundamentals completeness) rather than reinventing an equivalent confidence model from scratch.
- The Intelligence Engine's own fail-open, never-fabricate principle (missing data → unknown/neutral, never a fabricated negative) is the same principle this document requires in Sections 4 and 12 — a single shared discipline, not two parallel implementations of the same idea.
- Any future implementation plan for this feature must first evaluate whether its evidence-collection layer (Section 6, steps 1–4) should literally live inside `backend/services/intelligence_engine/` as a new module (e.g. a hypothetical `movement_explanation.py`), consuming the same candidate/telemetry infrastructure, before assuming it needs its own separate package.

### 15.2 Daily Picks

Read-only, one-directional integration only: this engine may read a stock's presence in a recent Daily Pick and its stated thesis (for Journey B, Section 2) but must **never** write back into `daily_picks.py`'s payload, cache, or ranking. No change to `generate_picks()`'s return contract is authorized by this document — the same "no production function contract changes without disclosure" discipline already applied in Intelligence Engine Phase 3B-B (`candidate_data.py`).

### 15.3 Portfolio Copilot

**Portfolio Copilot does not exist in this codebase today** (confirmed, unchanged, across SSDS-000 §3, the Product Glossary, and `MASTER-ROADMAP.md`'s Epic table). There is nothing to integrate into yet. Once built (Epic 007), a future enhancement could let a user ask "why did my portfolio move today?" as an aggregation of this engine's per-holding output — but that is out of scope for this specification and must not be assumed as a dependency for any phase in Section 17.

### 15.4 Paper Trading

Paper Trading (`backend/api/routers/paper_trading.py`) is a real, implemented feature. A future integration would let a user reviewing a Paper Trade position query this engine for a specific symbol + historical trading date (Journey D, Section 2) — strictly read-only, never influencing simulated fills, position sizing, or trade triggers. No change to Paper Trading's execution or notification logic is authorized by this document.

## 16. Technical Architecture

```
frontend/src/components/StockDetail/
  MovementExplanationSection.tsx   (new; reuses DisclosurePanel)

backend/services/intelligence_engine/     <-- proposed home, see §15.1
  movement_explanation/
    pipeline.py           (orchestrates §6's 8 steps)
    evidence_sources.py   (sector/index/technical/sentiment/macro collectors — all read-only)
    news_provider.py      (only once §5's feasibility question is resolved; isolated so it
                            can be entirely absent/disabled without breaking the rest)
    tiering.py            (§8's confirmed/likely/unknown classification, pure function)
    confidence.py          (§9's confidence category computation, pure function)
    narrative.py          (§6 step 6 template assembly; §7's deterministic-first rule)

backend/api/routers/
  movement_explanation.py   (new, additive router; GET-only; §10's contract)
```

**Design constraints carried over from every prior Intelligence Engine phase in this codebase:**
- Every module above must be independently unit-testable as a pure function wherever possible (mirroring `tradability_gate.py`/`liquidity_gate.py`'s pattern).
- The router must degrade to a clear "unavailable" response rather than a 500 error if any upstream source is missing — the same pattern `GET /api/picks/intelligence-shadow` already uses (`{"available": false, ...}`).
- No module in this proposed package may be imported by `daily_picks.py`, `prediction_engine.py`, or `paper_trading.py`'s execution path — only the reverse (those may optionally, later, choose to read this engine's output), preserving the same one-directional dependency discipline already established for the Intelligence Engine's shadow-run design.
- Feature-gated end-to-end by a new, dedicated flag (e.g. `MOVEMENT_EXPLANATION_ENABLED`), defaulting to disabled, independent of `INTELLIGENCE_ENGINE_SHADOW_ENABLED` — this is a new, separately-approved capability, not automatically covered by the existing shadow-run flag.

## 17. Phased Implementation Roadmap

No phase below is authorized by this document. Each requires its own separate approval, per this codebase's established phased-delivery convention (mirroring EPIC-008 §16/§17's own model).

- **Phase 0 — News/Earnings Data Feasibility Study.** Resolve Section 5's two "Unconfirmed" rows with live evidence (not assumption), for both India and US, before any code in Phases 2+ that depends on them is written. Mirrors Epic 003's India Feasibility Study precedent. Deterministic-only evidence (sector/index/technical/sentiment/macro) does not depend on this phase and could proceed in parallel with `unknown` news/earnings categories.
- **Phase 1 — Design Study and Contract Finalization.** Finalize the exact evidence schema, tiering thresholds (including the volatility-adjusted "flat" band, Section 12), and confidence-computation rules against real historical data, the same rigor Epic 004/005's own Design Studies applied before implementation.
- **Phase 2 — Deterministic Pipeline Implementation (shadow-only).** Implement Sections 6–9 as a new, flag-gated, read-only module inside (or alongside) the Intelligence Engine package (Section 16), computing but not yet surfacing explanations — the same "shadow-only, zero production impact" discipline the Intelligence Engine's own V1 rollout used.
- **Phase 3 — API and UI Exposure (controlled).** Wire the read-only API (Section 10) and UI section (Section 11) behind the feature flag, to a small controlled audience, mirroring RCI's own phased activation precedent.
- **Phase 4 — Generative Narrative Layer (optional, later).** Only after Phases 1–3 are validated, consider a constrained generative-narrative rendering layer (Section 7), subject to the same evaluation-harness and safety review EPIC-008 requires for any generative text surface.
- **Phase 5 — Research Analyst Integration.** Once Epic 008 reaches a phase where it can consume structured evidence (008C or later, per EPIC-008 §16), expose this engine's tiered evidence as one of Epic 008's permitted evidence sources (EPIC-008 §6), rather than duplicating this reasoning inside the conversational layer.

**Dependencies and sequencing, restated:** this initiative depends on the Intelligence Engine's existing evidence/confidence primitives (Section 15.1), a resolved News/Earnings feasibility question (Phase 0), and — for Phase 5 only — Epic 008 reaching 008C. It is not sequenced ahead of, or in place of, any currently in-progress Epic.
