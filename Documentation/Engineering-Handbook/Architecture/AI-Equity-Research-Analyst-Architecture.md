# AI Equity Research Analyst — Architecture and Product Specification

## 0. Status and Purpose

**Status: Planned / Not Started. Strategic architecture and product specification only — no implementation, model-provider integration, endpoint, schema, or UI described in this document exists today.** Nothing here authorizes coding, deployment, or a financial-advice claim of any kind.

This document is the **consolidating architecture** for a capability this codebase has already named and partially specified across several prior documents: it formalizes and unifies [EPIC-008 — AI Research Analyst: Concept and Safety Specification](EPIC-008-AI-Research-Analyst-Concept-and-Safety-Specification.md) (the safety/evidence/response-class rules, which remain fully binding and unmodified by this document), the [Stock Movement Explanation Engine spec](Research-Analyst-Stock-Movement-Explanation-Spec.md) ("why did this move today," now one input among many here), and the future-capability sections already written into the [Stock Detail](Stock-Detail-Page-Forensic-Scientific-Audit.md#21-future-capability--ai-equity-research-report), [Portfolio](Portfolio-Page-Forensic-Performance-Audit.md#11-portfolio-copilot-vision), [Daily Picks](Daily-Picks-Page-Forensic-Performance-Audit.md#13-future-capability--daily-picks-research-analyst), and [Multibagger](Multibagger-Page-Forensic-Scientific-Audit.md#10-future-capability--multibagger-explainability) forensic audits. **This document does not supersede EPIC-008** — where the two overlap, EPIC-008's safety rules govern; this document adds the cross-page architecture EPIC-008 deliberately left unspecified (EPIC-008 §16/17 name 008C "Read-Only Stock Research Conversations" as a future phase without detailing its page-by-page integration — that detail is this document's job).

## 1. Vision

**What is the AI Equity Research Analyst?** A single, evidence-grounded intelligence layer that reads the same validated data every existing StockSense360 engine already produces (Prediction Engine, Business Quality, Financial Strength, Growth Intelligence, Valuation Intelligence, RCI, the Intelligence Engine's gates, technical indicators, news/sentiment) and synthesizes it into institutional-style research narratives, on demand and continuously updated — never a new, independent scoring engine (the same "no duplicate scoring engines" principle already established in the [Portfolio Copilot Vision §12](Portfolio-Page-Forensic-Performance-Audit.md#12-long-term-architecture)).

**Mission.** Make every BUY/HOLD/SELL signal, every target price, every risk flag, and every screen result explainable in plain language, with the same confirmed/likely/unknown evidence discipline already established for the Stock Movement Explanation Engine — never a confident-sounding narrative that outruns the underlying evidence.

**Primary objectives:**
- Explain, not predict-differently: every output is a narrative synthesis of evidence already computed by existing engines, never a competing signal.
- Be the single intelligence layer behind six consumer surfaces (Section 8), rather than six separate, duplicated explanation mechanisms.
- Preserve every safety, evidence-grounding, and non-advisory rule EPIC-008 already specifies, in full, without exception.

**Target users:** retail investors using Stock Detail/Daily Picks/Multibagger for research; Portfolio/Paper Trading users wanting to understand their own positions; users relying on Alerts who need a "why" attached to a notification.

**Differentiation.** Unlike a generic LLM chat wrapper, every claim this analyst makes must trace to a named, already-validated platform evidence item (EPIC-008 §6) — it cannot invent a fact, a news event, or a number the platform did not itself compute or retrieve. This is the platform's core differentiator: analyst-quality synthesis without analyst-quality hallucination risk.

## 2. Executive Summary (Future Design)

A future per-stock Executive Summary block would render, for each request:

| Field | Source (already computed today) |
|---|---|
| Investment Thesis | Synthesized from `reasoning[]`, factor attribution, Business Quality/Growth/Valuation Intelligence outputs |
| Rating | Prediction Engine's existing BUY/HOLD/SELL signal — never recomputed |
| Confidence | Prediction Engine's existing confidence score, decomposed per Section 6 |
| Target | `prediction_engine.py`'s existing horizon-specific `_estimate_target()` output |
| Stop Loss | `prediction_engine.py`'s existing `_trade_levels()` output |
| Risk/Reward | Existing `risk_reward_ratio` — reported honestly even when sub-1.5, per the Stock Detail audit's own finding |
| Time Horizon | The user's selected horizon (short/medium/long) — no new horizon model |
| Expected Return | Derived arithmetically from target vs. current price — not a new forecast |
| Key Catalysts | Requires a new event-timeline data source (Section 4, confirmed not to exist today) |
| Major Risks | Existing red-flag/hard-gate evidence (Business Quality rejection, Multibagger Anti-Loss flags, RCI unresolved risk flags) |
| AI Summary | The synthesizing narrative layer itself — the one genuinely new capability this document specifies |

Every field above already has a data source except Key Catalysts (needs new data) and AI Summary (needs new synthesis logic) — the Executive Summary is overwhelmingly a **presentation layer over existing evidence**, not a new computation stack.

## 3. Investment Thesis

- **Why BUY? / Why HOLD? / Why SELL?** — three distinct narrative templates (never one template with the sign flipped), mirroring the Stock Movement Explanation Engine's "three distinct reasoning modes" principle and the Stock Detail audit's §21 specification.
- **Why TODAY?** — a direct application of the (not-yet-built) [Stock Movement Explanation Engine](Research-Analyst-Stock-Movement-Explanation-Spec.md) to the specific stock/thesis, not a separate implementation.
- **What changed?** — requires a persisted daily snapshot of prior recommendations/scores — the single shared architectural prerequisite already identified independently by the Portfolio, Daily Picks, Multibagger, and Stock Detail audits (see Section 12's dependency note: **one** shared snapshot/diff mechanism, not four).
- **Recommendation persistence / history** — depends on the same snapshot prerequisite; once it exists, "what changed" and "recommendation history" are the same underlying data viewed at different time granularities (yesterday vs. full timeline).

## 4. Research Sections (Future Design)

Each future section reuses an already-implemented engine or data source — no new scoring engine is introduced anywhere in this list:

| Section | Reuses |
|---|---|
| Technical Analysis | `backend/services/technical_indicators.py` (RSI/MACD/Bollinger/StochRSI/ADX/etc., already real, library-backed) |
| Fundamental Analysis | Existing fundamentals cache (`stock_fundamentals_cache`), screener/yfinance adapters |
| Financial Statement Analysis | Existing revenue/profit/margin/debt/cash-flow fields already fetched for Multibagger/Prediction Engine |
| Valuation Analysis | Valuation Intelligence Engine (SSDS-008) — sector-relative multiples, not new absolute caps |
| Quality Analysis | Business Quality Engine (SSDS-003) |
| Management Analysis | Existing promoter-pledge/holding fields (IN) — a genuinely new data source needed for US management-quality narrative (no equivalent field confirmed to exist today) |
| Capital Allocation | Growth Intelligence Engine's existing Reinvestment Efficiency category |
| Business Moat | **Genuinely new** — no moat-classification field exists anywhere in this codebase today; requires its own feasibility study before being scoped (mirroring Epic 003's India Feasibility Study precedent) |
| Growth Drivers | Growth Intelligence Engine's existing metric catalogue |
| Competitive Position / Peer Comparison | Reuses the existing Heatmap sector/industry grouping (`backend/services/heatmap_service.py`), not a new peer taxonomy |
| Industry Position / Sector Analysis | Same Heatmap infrastructure |
| Macro Sensitivity | Existing `global_context` block (`dxy`, `vix`, `usdinr`, `india_vix`) already computed for Daily Picks |
| News Intelligence / Sentiment | Existing FinBERT-scored news pipeline (`backend/services/news_sentiment.py`) |
| Corporate Actions | **Not confirmed to exist as a distinct, dated data source today** — flagged as an open feasibility question, same status as the Stock Movement Explanation Engine's own unresolved news/earnings-timestamp question |

## 5. Intelligence Engine Integration

The Research Analyst must consume, never recompute, the Intelligence Engine's existing gate outputs (`backend/services/intelligence_engine/`):

- **Opportunity Score** — does not exist today as a named field; the closest existing analog is the Prediction Engine's composite score plus RCI's Engine Agreement dimension — a future "Opportunity Score" should be evaluated as a synthesis of these existing signals, not a new independent computation.
- **Risk Tier** — does not exist today as a labeled tier; the closest existing analog is the combination of Business Quality's hard-gate status, Financial Strength's liquidity-distress veto, and Multibagger's Anti-Loss red-flag pattern — a future Risk Tier should synthesize these, not duplicate them.
- **Quality Gate / Tradability / Liquidity / Data Confidence** — already computed today by the Intelligence Engine's own gates (Instrument Type, Tradability, Liquidity, Data Confidence — confirmed live in production, per `Current-Release-Status.md`) — direct reuse, no new computation.
- **Universe Classification** — already computed by the Instrument Type Gate.
- **Technical / Fundamental / Macro / News Signals** — all already computed (Section 4) — the Research Analyst's job is synthesis and narrative, not re-detection.

**Confirmed integration gap (from the Stock Detail audit):** the Stock Detail page does not currently consume any Intelligence Engine output at all — the Intelligence Engine today runs only inside Daily Picks' shadow pipeline. Wiring Intelligence Engine outputs into the per-stock evidence set available to a future Research Analyst is a genuine prerequisite, not yet done.

## 6. Explainability

Every recommendation must answer, in the same confirmed/likely/unknown tiered discipline already established for the Stock Movement Explanation Engine and required by [EPIC-008 §9's Research Answer Contract](EPIC-008-AI-Research-Analyst-Concept-and-Safety-Specification.md#9-research-answer-contract):

- **Why?** — the direct answer, grounded in named evidence.
- **Why now?** — reuses the Stock Movement Explanation Engine.
- **What changed?** — reuses Section 3's snapshot-dependent capability.
- **Evidence** — the specific platform evidence used, each tagged with source category and as-of timestamp (EPIC-008 §6).
- **Confidence** — a decomposition of the existing confidence score into contributing factors (technical/fundamental/sentiment/regime), reusing RCI's existing category-based confidence-dimension approach rather than inventing a new decomposition scheme.
- **Supporting data** — direct citations back to underlying fields (EPIC-008 §14's auditability requirement: evidence identifiers, model/version identifier, response timestamp, data-as-of timestamp).
- **What could invalidate this thesis?** — the single most-requested missing feature across every prior audit this session; directly addressable once a thesis narrative exists.

**"Why this target?" and "why this stop loss?" are the lowest-risk, highest-confidence explainability items across this entire architecture** — the underlying logic (`_estimate_target`, `_trade_levels`) is already fully computed and sound (Stock Detail audit §2/§11); this requires only a presentation/narration layer, no new backend computation.

## 7. Future AI Features

- **Bull Case / Base Case / Bear Case** — three scenario narratives, each explicitly labeled hypothetical per [EPIC-008 §7](EPIC-008-AI-Research-Analyst-Concept-and-Safety-Specification.md#7-permitted-response-classes)'s "scenario-based education, clearly labelled as hypothetical and not a prediction" — never three equally-confident predictions.
- **Scenario probabilities** — **explicitly out of scope as a numeric output** unless and until a rigorous, validated methodology exists; assigning a false-precision percentage to a qualitative scenario would violate EPIC-008 §12's "no false precision" rule. If pursued, must be evidence-backed (e.g., historical frequency of similar setups), not an LLM-guessed number.
- **Historical recommendation timeline / historical confidence timeline / recommendation evolution** — all depend on the shared snapshot/diff prerequisite (Section 3/12); "historical confidence timeline" specifically can reuse the Stock Detail page's already-fetched `score-history` endpoint today, the lowest-risk item in this whole feature group.
- **Price movement explanation (daily/weekly/monthly)** — the daily case reuses the Stock Movement Explanation Engine directly; weekly/monthly aggregation is a natural rollup of daily explanations once those exist, not a separately-computed capability.

## 8. Portfolio Integration

- **Portfolio Copilot:** the Research Analyst is Portfolio Copilot's primary reasoning layer once portfolio context exists (Epic 007) — per [EPIC-008 §11](EPIC-008-AI-Research-Analyst-Concept-and-Safety-Specification.md#11-personalization-and-portfolio-boundaries), no portfolio-aware answer is permitted before Epic 007 is delivered and separately approved. "AI Chat for Portfolio" (Portfolio Copilot Vision §11) **is**, concretely, Epic 008's 008E phase applied to Portfolio — not a separate feature.
- **Daily Picks:** the Research Analyst answers "why was this stock selected today" and "what could invalidate this thesis" for a Daily Pick, reading the pick's existing `reasoning[]`/`quality_factors` — read-only, one-directional, never writing back into `daily_picks.py`'s payload/cache/ranking (the same constraint the Stock Movement Explanation Engine spec already establishes, §15.2).
- **Multibagger:** answers "why did this stock qualify for Quality Compounders" and "why did a near-miss stock not qualify," reusing the scorecard's existing `checks[]` array (Multibagger audit §10) rather than a new explanation mechanism.
- **Paper Trading:** a Research Analyst summary attached to an open/closed Paper Trade position, explaining the original thesis and what's changed since entry — read-only, never influencing simulated fills or trade triggers.
- **Alerts:** every alert notification should be able to link to a Research Analyst explanation of *why* the alert fired — reusing whatever evidence triggered the alert condition, not a separate alert-specific narrative generator.
- **Watchlists:** the same per-stock Research Analyst surface used on Stock Detail, applied to any watchlisted symbol — no separate watchlist-specific version needed.

## 9. Daily Intelligence

"What changed today" at both the per-stock and portfolio level is the direct convergence point of several already-specified capabilities:
- **Why price moved** — Stock Movement Explanation Engine (stock-level); Portfolio Daily Performance & Attribution Intelligence's own "why did my portfolio move today" rollup (portfolio-level).
- **What changed fundamentally / technically** — depends on the shared daily-snapshot prerequisite (Section 3/12) diffing yesterday's fundamentals/technical-signal snapshot against today's.
- **News changes** — the existing FinBERT news pipeline's own freshness/staleness handling, not new logic.
- **Macro changes** — the existing `global_context` block's own day-over-day delta (already computed as `changes` in `GlobalContext`, confirmed in the Daily Picks page's own type definitions) — a genuinely existing, currently-unused-for-this-purpose data source.
- **Risk changes** — new red flags appearing/disappearing (Business Quality hard-gate status change, Multibagger Anti-Loss flag change, RCI unresolved-risk-flag change) — all reuse existing evidence, diffed against yesterday's snapshot.

## 10. Educational Intelligence

Teach the investor using only already-computed, evidence-backed content — never new advisory judgment:
- **Key strengths / weaknesses** — reuses the existing `reasoning[]` array's BUY/SELL-tagged items, reframed pedagogically.
- **Risks** — reuses Section 6's evidence.
- **What to monitor / important dates / upcoming events** — depends on the same not-yet-confirmed event-timeline/corporate-actions data source named in Section 4; an open feasibility question, not yet resolved.

## 11. Future Report Layout

A professional AI-generated equity research report, assembled entirely from Sections 2-10's already-scoped content — no new section introduces content not already specified above:

```
Executive Summary        (Section 2)
Investment Thesis        (Section 3)
Business Overview        (Section 4 — Fundamental/Business Moat/Management/Capital Allocation)
Financials                (Section 4 — Financial Statement Analysis)
Technicals                (Section 4 — Technical Analysis)
Valuation                 (Section 4 — Valuation Analysis)
News                       (Section 4 — News Intelligence/Sentiment)
Risk Analysis             (Section 6/9 — Risk changes, invalidation conditions)
Catalysts                 (Section 2/10 — upcoming events, pending feasibility)
Timeline                  (Section 7 — historical recommendation/confidence timeline)
Evidence                  (Section 6 — citations, source/as-of disclosure)
Conclusion                 (Section 3 — thesis restated, user-decision boundary per EPIC-008 §9)
```

Every report must end with the user-decision boundary statement EPIC-008 §9 requires on every such answer — "the platform provides analysis and the user makes the investment decision" — not only when risk is elevated.

## 12. Implementation Roadmap

No phase below is authorized by this document; each requires its own separate review and approval, per this codebase's established phased-delivery convention (mirroring EPIC-008 §16/§17's own phased model, which this roadmap is subordinate to — no phase here may begin before its corresponding EPIC-008 prerequisite, Section 17, is satisfied).

- **Phase 1 — Shared Snapshot & Diff Foundation.** Design and implement **one** persisted daily-snapshot mechanism, shared across Portfolio, Daily Picks, Multibagger, and Stock Detail (the single most-repeated dependency across all four prior forensic audits) — without this, "what changed," recommendation history, and historical confidence timelines are not buildable anywhere. Purely a data-persistence and diff-computation phase; no narrative/LLM component yet.
- **Phase 2 — Deterministic Explainability Layer (no generative text).** Build the "why this target/why this stop loss/why this qualifies/why this move" presentation layers identified as lowest-risk across every prior audit — all reuse already-computed data, require only display logic and rule-based templating (the same deterministic-first, generative-last principle already established in the Stock Movement Explanation Engine spec §7). This phase alone resolves most of Section 6's Explainability requirements without any AI/LLM component.
- **Phase 3 — Intelligence Engine Wiring.** Extend Intelligence Engine gate consumption (Section 5) beyond Daily Picks' shadow pipeline to Stock Detail, Multibagger, and Portfolio — a prerequisite for any future Opportunity Score/Risk Tier synthesis, and independently valuable (surfacing Data Confidence on Stock Detail, per that page's own audit finding).
- **Phase 4 — Conversational Research Analyst (Epic 008, 008C onward).** Only after Phases 1-3 establish the deterministic evidence and explainability foundation, and only after EPIC-008's own prerequisites (Section 17 of that document — Release 12B validation, stable Epic 006 evidence, model-provider/data-provider/privacy/security/compliance decisions, an evaluation harness, feature flags/rollback/observability) are separately satisfied, begin 008C "Read-Only Stock Research Conversations" as the generative synthesis layer over Phases 1-3's foundation. Portfolio-aware conversation (008E) remains additionally gated on Epic 007.

**Dependencies, summarized:** Phase 2 depends on Phase 1's snapshot foundation for anything involving "since yesterday." Phase 3 is independent of Phases 1-2 and can proceed in parallel. Phase 4 depends on all of Phases 1-3 plus every EPIC-008 §17 prerequisite plus, for portfolio-aware scope specifically, Epic 007. No phase is sequenced ahead of, or in place of, any currently in-progress Epic.

## 13. Explicit Non-Goals of This Document

Consistent with EPIC-008 §20 and every prior audit this session: no code, endpoint, schema, model-provider selection, or UI described in this document is authorized. This document does not itself constitute approval for any phase in Section 12 — each requires its own separate, written approval per EPIC-008 §19's Definition of Readiness, which remains fully binding and unmodified.
