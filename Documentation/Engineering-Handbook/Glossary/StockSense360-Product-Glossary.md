# StockSense360 Product Glossary

**Status:** Active — governing.
**Applies to:** documentation, specifications, engineering, frontend, backend, APIs, prompts, AI agents, roadmap, and sprint planning.
**Governed by:** SES-001, SES-004, SES-005.

This is the canonical source of truth for terminology used across StockSense360. No future document should invent an alternative name for a concept already named here. Where this glossary's name differs from a name already in code (a class, a function, a field, a route), the glossary states which one is authoritative for which purpose — see each entry's **Status** line, and §0 for the general rule.

**Status legend used below:**
- **Established** — matches a name already live in code (a class, function, route, or API field) and/or already published in the Engineering Handbook. Use this name; do not introduce a synonym.
- **Proposed** — a name introduced by this glossary for a concept that exists in code but was previously unnamed or inconsistently named. Use this name going forward in documentation; the underlying code is not renamed by this glossary alone (see §0).
- **Proposed — not yet implemented** — names a concept with no corresponding code today. Safe to use in product/roadmap prose; do not imply it exists as a shipped component.

---

## §0. How this glossary relates to code

Per this sprint's explicit rule, **no functional code is renamed or modified to produce this glossary.** Where an "Established" or "Proposed" name here differs from an existing class/function/file name in the codebase (e.g. the glossary's "News & Sentiment Engine" vs. the code's `NewsSentimentService` class), the glossary name governs **new prose** (documentation, specs, prompts, UI copy, sprint plans). The existing code identifier is not required to change to match — that is a separate, explicitly-scoped renaming task if it's ever wanted, tracked under "Future Expansion Notes" / "Files Requiring Future Migration" per entry.

---

## Product

### StockSense360
- **Official Name:** StockSense360
- **Short Description:** The official product name.
- **Purpose:** Top-level brand identity for the entire platform.
- **Primary Owner:** Product
- **Related Components:** Entire repository.
- **Status:** Established (SES-005).
- **Future Expansion Notes:** Never abbreviate as "StockSense" in prose. "StockSense" remains acceptable only when referring to internal repository/package names that already use it (e.g. the `stocksense-ui` frontend package, the `STOCKSENSE_DOCUMENTATION.md` filename) — see SES-005 §6 for the full out-of-scope list.

---

## Core AI Engines

### Prediction Engine
- **Short Description:** Produces probability-based market predictions and the resulting BUY / HOLD / SELL signal for a given stock and horizon.
- **Purpose:** The central scoring engine — combines technical, fundamental, and sentiment factors into one composite signal per stock/horizon.
- **Primary Owner:** AI
- **Related Components:** `backend/services/prediction_engine.py` (`PredictionEngine` class), `backend/services/alpha_engine/`.
- **Status:** Established — exact 1:1 match with the existing `PredictionEngine` class.
- **Future Expansion Notes:** See "Recommendation Engine" below — the BUY/HOLD/SELL verdict is currently produced *by* this engine, not by a separate one.

### Recommendation Engine
- **Short Description:** Produces the Buy / Hold / Sell recommendation shown to users.
- **Purpose:** Conceptually, the decision layer that turns a prediction into an actionable call.
- **Primary Owner:** AI
- **Related Components:** `backend/services/prediction_engine.py` (the `signal` field returned by `PredictionEngine.predict()`).
- **Status:** Proposed — not a separate component today. **Validation finding:** there is no code construct distinct from the Prediction Engine that performs this role; the BUY/HOLD/SELL verdict is one field in the same return value the Prediction Engine already produces. Recommend treating "Recommendation Engine" as **a description of the Prediction Engine's output**, not a second engine, until/unless a real architectural split is built.
- **Future Expansion Notes:** A genuine decoupling (e.g. a separate decision layer that could apply portfolio-aware rules on top of a raw prediction) would align with Roadmap Phase 3 (Portfolio Intelligence) — if built, *that* would be the first real candidate for this name.

### Selection Engine
- **Short Description:** The umbrella term for the full pipeline that screens, scores, ranks, and filters candidate investments — covers the Prediction Engine, the quality/risk scoring layer, and the daily ranking/portfolio-optimization step together.
- **Purpose:** Names the system-level subject of SEAR-001 (the founding engineering audit) and the Roadmap derived from it.
- **Primary Owner:** AI / Backend
- **Related Components:** `backend/services/prediction_engine.py`, `quality_factors.py`, `multibagger_scorecard.py`, `daily_picks.py`, `alpha_engine/`.
- **Status:** Established — already published 10+ times across `Documentation/Engineering-Handbook/{INDEX,README,ROADMAP}.md`, the SEAR-001 audit, and SES-005, all using "Selection Engine" to mean this **entire pipeline**, not one narrow sub-step.
- **Validation finding:** this glossary's source brief described "Selection Engine" more narrowly, as "ranks and filters candidate investments." That narrower activity is real but is a **sub-step** of the already-published, broader meaning — renaming the broad usage now would invalidate SEAR-001, the Roadmap, and SES-005 without re-publishing all three. **Recommendation: keep "Selection Engine" as the broad, already-established umbrella term.** Use **"Ranking & Filtering"** (see below) for the narrower step.
- **Future Expansion Notes:** None — this entry's scope is intentionally the broad one.

### Ranking & Filtering
- **Short Description:** The specific step that z-score-normalizes candidates, ranks them, and applies the shortlist/eligibility cutoff.
- **Purpose:** Names the narrower concept the glossary brief originally attached to "Selection Engine" (see Validation finding above), without conflicting with that term's established broader meaning.
- **Primary Owner:** Backend / AI
- **Related Components:** `daily_picks.py` (`_zscore_and_rank`), `multibagger_scorecard.py` (`annotate_and_rank`).
- **Status:** Proposed — new name introduced by this glossary for an existing, previously-unnamed code pattern.
- **Future Expansion Notes:** None.

### Business Quality Engine
- **Short Description:** Evaluates business durability, capital allocation, management quality, and competitive advantage to produce a long-term quality assessment.
- **Purpose:** Distinguishes genuinely durable, well-run businesses from ones that merely look statistically cheap or technically strong.
- **Primary Owner:** AI
- **Related Components:** `backend/services/quality_factors.py` (`compute_all_quality_factors`, `buffett_munger_score`, `quality_metrics_score`, `altman_zscore_signal`, `sloan_accruals_signal`), `backend/services/multibagger_scorecard.py`.
- **Status:** Proposed — no single module is named "Business Quality Engine" in code today; this name now governs documentation references to the collective behavior of `quality_factors.py`'s quality-oriented sub-scores.
- **Validation finding:** the glossary brief listed this entry **twice**, with two different descriptions ("Scores long-term business quality" and "Evaluates business durability, capital allocation, management quality and competitive advantage"). Merged into one canonical entry using the fuller description; the shorter phrasing is not a separate concept.
- **Future Expansion Notes:** See "Quality Score" below for the corresponding score-level name, which must stay aligned with the live `quality_score` API field rather than diverge into a separate "Business Quality Score" term.

### Portfolio Copilot
- **Short Description:** Analyzes a user's portfolio holdings and suggests actions — flags concentration risk, diversification gaps, and improvement opportunities.
- **Purpose:** Closes the "zero portfolio awareness" gap named as a Critical Issue in SEAR-001.
- **Primary Owner:** AI / Frontend
- **Related Components:** None yet — see Validation finding.
- **Status:** Proposed — not yet implemented. **Validation finding:** confirmed by direct repository search; no code references this name or this behavior. The closest *existing* related work is Roadmap Phase 3 (items 3.1–3.5, Portfolio Intelligence) and Roadmap item 5.1 ("AI Copilot v1").
- **Future Expansion Notes:** When Phase 3/5.1 work begins, this is the name to use for the resulting feature — don't let an implementer invent a different name at build time.

### AI Research Analyst
- **Short Description:** Explains every recommendation using transparent, human-readable reasoning.
- **Purpose:** The "why" behind a signal — the bull case, the bear case, and the factor-by-factor breakdown.
- **Primary Owner:** AI
- **Related Components:** `backend/services/case_generator.py` (bull/bear case generation), the `reasoning` array returned by `PredictionEngine.predict()`.
- **Status:** Proposed — not a separate module. **Validation finding:** this behavior already exists and already has a name in this Handbook — **"Explainability"** (see Platform Features below), used throughout SEAR-001 as a named review area. Recommend using "Explainability" for the *capability*, and reserving "AI Research Analyst" only if/when a literally separate, analyst-styled module or persona is built (e.g. a conversational layer on top of today's static reasoning output — this would also be the natural home for Roadmap item 5.1's AI Copilot).
- **Future Expansion Notes:** Don't use "AI Research Analyst" and "Explainability" interchangeably in new documents — pick one per the distinction above.

### Risk Engine
- **Short Description:** Calculates investment risk independently of business quality.
- **Purpose:** A unified, single risk assessment per stock.
- **Primary Owner:** AI
- **Related Components:** `prediction_engine.py`'s module-level `_compute_risk_penalty()` function, `quality_factors.py`'s `risk_management_score()` and `altman_zscore_signal()`.
- **Status:** Proposed — components exist but are **not unified**. **Validation finding, important:** two different existing numbers both currently relate to "risk" and must not be conflated:
  - `risk_management_score` (a 0–100 quality sub-factor, **higher = safer**) — see "Risk Score" below.
  - `risk_penalty` (points **subtracted** from the composite score, a separate, additive-deduction mechanism) — kept as its own term, "Risk Penalty," not folded into "Risk Score."
  SEAR-001 named exactly this kind of split-mechanism pattern (gate vs. penalty vs. demotion) as a source of confusion; this glossary entry intentionally keeps the two numbers distinctly named rather than presenting them as one "Risk Engine" score, until/unless they're actually unified in code (a Roadmap-level decision, not a documentation one).
- **Future Expansion Notes:** A true unification would be a Selection Engine architecture change — track it as a Roadmap item if wanted, not as a documentation rename.

### Macro Intelligence Engine
- **Short Description:** Evaluates global and domestic macroeconomic conditions (VIX, S&P 500, crude oil, gold, USD/INR, market regime).
- **Purpose:** Supplies the macro adjustment and regime classification that modulate every other factor's weight.
- **Primary Owner:** AI / Backend
- **Related Components:** `backend/services/global_context.py` (`get_global_context`, `_score_global`), `backend/services/alpha_engine/regime_cluster.py`.
- **Status:** Proposed — no naming conflict found; adopted as the documentation-level name for `global_context.py`'s existing behavior. The code module itself keeps its current name (`global_context.py`) — not renamed by this glossary.
- **Future Expansion Notes:** None.

### Technical Analysis Engine
- **Short Description:** Processes technical indicators, momentum, and chart-based signals.
- **Purpose:** Produces the Technical Score and the technical reasoning bullets.
- **Primary Owner:** Backend / AI
- **Related Components:** `backend/services/technical_indicators.py` (`compute_indicators`, `detect_candlestick_patterns`, `get_signal_summary`), `PredictionEngine`'s technical scoring.
- **Status:** Established — matches existing usage (README's "Technical indicators" layer, the `tech_score` API field).
- **Future Expansion Notes:** None.

### Fundamental Analysis Engine
- **Short Description:** Processes financial statements and valuation metrics.
- **Purpose:** Produces the Fundamental Score.
- **Primary Owner:** Backend / AI
- **Related Components:** `PredictionEngine._fundamental_score()`, `backend/services/screener_data.py`, `backend/services/us_fundamentals.py`.
- **Status:** Established — matches the existing `fund_score` API field.
- **Future Expansion Notes:** None.

### News & Sentiment Engine
- **Short Description:** Evaluates corporate news, market sentiment, and events.
- **Purpose:** Produces the Sentiment Score and the bullish/bearish news reasoning bullets.
- **Primary Owner:** Backend / AI
- **Related Components:** `backend/services/news_sentiment.py` (`NewsSentimentService` class).
- **Status:** Established — matches the existing `sentiment_score` API field. **Note:** the code class is named `NewsSentimentService` (suffix "Service," not "Engine") — a minor existing naming inconsistency across the codebase's own internal conventions, flagged here for awareness but not corrected (no code renaming in this glossary's scope).
- **Future Expansion Notes:** None.

---

## Scores

### Quality Score
- **Short Description:** A long-term business quality score reflecting durability, capital allocation, management quality, and competitive advantage.
- **Purpose:** The score-level output of the Business Quality Engine.
- **Primary Owner:** AI
- **Related Components:** `quality_factors.py`'s `compute_all_quality_factors()`; the `quality_score` API field.
- **Status:** Established — matches the live `quality_score` field returned by the API (confirmed directly against production cache data).
- **Validation finding:** the glossary brief proposed "Business Quality Score" as the name. **Recommendation: use "Quality Score"** instead, since it matches the field name already live in the API and frontend — introducing "Business Quality Score" as a parallel term would create exactly the kind of two-names-one-concept ambiguity this glossary exists to prevent. "Business Quality Score" may be used as a *descriptive expansion* in prose ("the Quality Score, which measures business quality...") but is not the official short name.
- **Future Expansion Notes:** None.

### Confidence Score
- **Short Description:** The overall confidence level the AI has in a given recommendation.
- **Purpose:** Tells the user how strongly to weight a given signal.
- **Primary Owner:** AI / Frontend
- **Related Components:** `PredictionEngine._confidence_engine()`; the `confidence` API field; `app/picks/page.tsx`, `app/validation/page.tsx` ("AI Confidence," "Confidence Score Calibration").
- **Status:** Established — matches the existing `confidence` field and existing UI copy.
- **Validation finding — important:** the glossary brief proposed **"Conviction Score"** for this concept. **Do not use "Conviction Score."** Earlier in this engagement, "Confidence" vs. "Conviction" was identified and fixed as exactly the kind of "looks contradictory but isn't" UI pairing the product's transparency principle exists to eliminate — two different words for what users would reasonably assume is the same number. Reintroducing "Conviction Score" now would undo that fix. **"Conviction" is a deprecated/disallowed term for this concept** — see the Deprecated Names table below.
- **Future Expansion Notes:** None.

### Risk Score
- **Short Description:** A 0–100 risk-adjusted-safety assessment (higher = safer), distinct from the separate Risk Penalty deduction.
- **Purpose:** Surfaces downside-risk characteristics (drawdown, volatility-adjusted return) as a standalone factor.
- **Primary Owner:** AI
- **Related Components:** `quality_factors.py`'s `risk_management_score()`.
- **Status:** Proposed — adopted as the official name for `risk_management_score`'s output, explicitly distinguished from "Risk Penalty" (see "Risk Engine" Validation finding above). Do not use "Risk Score" to refer to the risk-penalty deduction — they move in different directions and answer different questions.
- **Future Expansion Notes:** None.

### Valuation Score
- **Short Description:** Relative valuation attractiveness (cheap vs. expensive, relative to sector/market norms).
- **Primary Owner:** AI
- **Related Components:** `quality_factors.py`'s `valuation_score()`.
- **Status:** Established — exact 1:1 match, no conflict found.
- **Future Expansion Notes:** None.

### Technical Score
- **Short Description:** A momentum/technical-strength score derived from indicators and chart patterns.
- **Primary Owner:** AI
- **Related Components:** the `tech_score` API field.
- **Status:** Established.
- **Future Expansion Notes:** None.

### Fundamental Score
- **Short Description:** A financial-strength score derived from valuation, profitability, growth, balance sheet, and governance.
- **Primary Owner:** AI
- **Related Components:** the `fund_score` API field.
- **Status:** Established.
- **Future Expansion Notes:** None.

---

## Platform Features

### Daily Picks
- **Short Description:** Daily investment opportunities — the top 6 BUY ideas per horizon, screened from the full NSE/US universe.
- **Primary Owner:** Product / Backend
- **Related Components:** `backend/services/daily_picks.py`, `/picks` route.
- **Status:** Established.

### Watchlist
- **Short Description:** A user-managed monitoring list of saved stocks with live prices and change%.
- **Primary Owner:** Frontend / Backend
- **Related Components:** `/watchlist` route, `watchlist` Postgres table.
- **Status:** Established. **Validation finding:** the glossary brief proposed the plural "Watchlists" — the existing route and table are singular (`watchlist`). **Recommendation: use "Watchlist" (singular)** as the feature name, matching the live route/table; "watchlists" remains grammatically fine only when referring to multiple users' lists collectively in prose.

### Alerts
- **Short Description:** User notifications — price alerts that trigger when a stock crosses a target.
- **Primary Owner:** Frontend / Backend
- **Related Components:** `/alerts` route, `price_alerts` Postgres table, `backend/services/price_alert_notifier.py`.
- **Status:** Established — exact match, no conflict.

### Screener
- **Short Description:** A filtering engine for discovering investments by PE, ROE, sector, signal, etc.
- **Primary Owner:** Frontend / Backend
- **Related Components:** `/screener` route, `backend/services/screener_service.py`.
- **Status:** Established. **Validation finding:** the glossary brief proposed the plural "Screeners" — the existing route is singular (`/screener`), matching "Daily Picks," "Watchlist," and "Alerts"'s pattern of naming the *feature* after its singular route. **Recommendation: use "Screener" (singular)** as the feature name.

### Explainability
- **Short Description:** The complete, transparent reasoning behind every recommendation — factor breakdown, bull/bear case, and reasoning bullets.
- **Primary Owner:** AI / Product
- **Related Components:** `case_generator.py`, the `reasoning` array, SEAR-001's "Explainability" review area.
- **Status:** Established — already a named review area in SEAR-001; see "AI Research Analyst" above for the related-but-distinct naming decision.

---

## Standards

### SES — StockSense360 Engineering Standards
- **Short Description:** The binding engineering standards family (SES-001 through SES-005 today).
- **Primary Owner:** Engineering
- **Related Components:** `Documentation/Engineering-Handbook/SES/`.
- **Status:** Established — matches existing usage exactly.

### SSDS — StockSense360 System Design Specifications
- **Short Description:** The specification family covering both feature/sprint scoping templates and platform-level system design documents (e.g. SSDS-000, the master architecture document).
- **Primary Owner:** Engineering / Product
- **Related Components:** `Documentation/Engineering-Handbook/SSDS/`.
- **Status:** Established — **superseding ruling.** This glossary originally recommended "Specification & Design Standards" (the expansion published in `README.md` at the time) over this glossary brief's proposed "System Design Specifications," reasoning that the only two SSDS documents that existed then (SSDS-001, SSDS-002) were scoping templates, not system-design documents. That reasoning no longer holds: the SSDS-000 task that commissioned the platform's master architecture document explicitly designated **"StockSense360 System Design Specifications"** as the official, canonical expansion going forward, and the SSDS family now includes an actual system-design document (SSDS-000) alongside the two templates. **"System Design Specifications" is now official.** "Specification & Design Standards" is the deprecated former name — see the Deprecated/Disallowed table below, and `README.md`, updated in the same change that updated this entry.

### SEAR — Selection Engine Engineering Audit
- **Short Description:** The audit format used for deep engineering reviews of a major subsystem (SEAR-001 is the first and so far only instance, covering the Selection Engine).
- **Primary Owner:** Engineering
- **Related Components:** `Documentation/Engineering-Handbook/Architecture/Sprint-001-Selection-Engine-Audit.md`.
- **Status:** Established. **Validation finding:** this glossary's brief proposed expanding "SEAR" as "StockSense360 **Engineering Audit Report**." That conflicts with the expansion already published in `Documentation/Engineering-Handbook/INDEX.md` — "**Selection Engine Engineering Audit**." **Recommendation: keep "Selection Engine Engineering Audit"** as official, since it's already published in INDEX.md, README.md, and ROADMAP.md, and the "001" numbering specifically reflects that it's an audit *of the Selection Engine* (not a generic platform-wide audit report) — the broader expansion would misdescribe its actual scope if a future SEAR-002 audited a different subsystem.

---

## Validation Summary

A repository-wide search (markdown, Python, TypeScript) was run for every proposed term before this glossary was finalized. Findings:

| Term(s) found | Resolution |
|---|---|
| "Conviction Score" (proposed) vs. "Confidence Score" / `confidence` field (live) | **Confidence Score** is official. "Conviction" is deprecated — see table below. This directly preserves an earlier fix in this engagement that resolved the same ambiguity in the UI. |
| "Business Quality Score" (proposed) vs. `quality_score` field (live) | **Quality Score** is official, matching the live API field. "Business Quality Score" is an acceptable descriptive expansion in prose, not a separate official short name. |
| "Watchlists" (proposed, plural) vs. `watchlist` route/table (live, singular) | **Watchlist** (singular) is official. |
| "Screeners" (proposed, plural) vs. `/screener` route (live, singular) | **Screener** (singular) is official. |
| "Selection Engine" used narrowly in this glossary's brief ("ranks and filters candidates") vs. broadly in SEAR-001/ROADMAP/SES-005 (the entire pipeline) | Broad, already-published meaning kept. New term **"Ranking & Filtering"** introduced for the narrow activity. |
| "Recommendation Engine" (proposed) — no corresponding separate code component found | Documented as **the Prediction Engine's output**, not a second engine, until a real architectural split exists. |
| "AI Research Analyst" (proposed) — no corresponding separate code component found; behavior already named "Explainability" in SEAR-001 | **Explainability** is the established term for the capability; "AI Research Analyst" reserved for a possible future literal analyst-persona module. |
| "Risk Engine" (proposed) — found two existing, *different* numbers (`risk_management_score` vs. `risk_penalty`) that both relate to "risk" | Kept as two distinct terms — **Risk Score** (`risk_management_score`) and **Risk Penalty** (`risk_penalty`) — rather than unifying them under one glossary entry without an actual code unification. |
| "Business Quality Engine" listed twice in the glossary brief with two different descriptions | Merged into one canonical entry (see above). |
| SSDS expansion: "System Design Specifications" (this glossary's original brief) vs. "Specification & Design Standards" (published at the time) | **Superseded.** The SSDS-000 commissioning task explicitly designated "StockSense360 System Design Specifications" as canonical going forward, now that the SSDS family includes an actual system-design document (SSDS-000) and not just scoping templates. "Specification & Design Standards" is now the deprecated former name. |
| SEAR expansion: "StockSense360 Engineering Audit Report" (this brief) vs. "Selection Engine Engineering Audit" (already published) | Kept the already-published expansion — reconfirmed as canonical by the SSDS-000 commissioning task. |
| "Daily Picks Engine" (proposed by the SSDS-000 task's Core Engine list) vs. "Daily Picks" (this glossary's existing Platform Feature entry) | The underlying pipeline (`daily_picks.py`) is part of the already-established **Selection Engine**, not a separate "Daily Picks Engine." **Daily Picks** remains the name for the user-facing feature/output. See SSDS-000 §11 for the full reasoning. |
| "Portfolio Copilot," "Macro Intelligence Engine," "Risk Engine," "Technical/Fundamental/News & Sentiment Engine" (suffix), "Valuation Score" | No existing conflicting usage found anywhere in the repository — adopted as proposed, with status noted per entry above. |

### Deprecated / Disallowed Names

| Deprecated name | Use instead | Why |
|---|---|---|
| Conviction Score / Conviction | Confidence Score | Resolves a previously-fixed UI ambiguity; reintroducing it would undo that fix. |
| Business Quality Score (as a short name) | Quality Score | Matches the live API field; avoid a second name for the same field. |
| Watchlists (as the feature name) | Watchlist | Matches the live route/table name. |
| Screeners (as the feature name) | Screener | Matches the live route name. |
| "StockSense" (outside the out-of-scope list in SES-005 §6) | StockSense360 | Per SES-005. |
| StockSense360 Specification & Design Standards (former SSDS expansion) | StockSense360 System Design Specifications | Superseded by the SSDS-000 commissioning task's explicit canonical-naming directive. |
| "StockSense360 Engineering Audit Report" (as the SEAR expansion) | Selection Engine Engineering Audit | Matches the already-published expansion and accurately scopes it to the Selection Engine. |
| "Daily Picks Engine" | Selection Engine (pipeline) / Daily Picks (feature) | Avoids a third name for a pipeline that already has an established umbrella term, while keeping "Daily Picks" for the user-facing feature. |

### Files Identified for Future Migration (not changed in this sprint)

None of these are renamed here — flagged for a future, separately-scoped task if alignment with this glossary's naming is wanted at the code level:

- `backend/services/news_sentiment.py`'s `NewsSentimentService` class — suffix "Service" vs. this glossary's "News & Sentiment **Engine**."
- `backend/services/global_context.py` — module name doesn't reference "Macro Intelligence Engine"; no rename implied by adopting that name in documentation.
- No API field is named `risk_score` today (only `risk_management_score` internally, not exposed as a single top-level field) — if "Risk Score" is wanted as a user-facing concept, exposing it as its own field is a backend task, not a documentation one.
