# Stock Detail Page — Forensic & Scientific Audit

## 0. Status and Purpose

**Status: Audit complete for the current, live Stock Detail page. Sections 21-25 specify future capabilities that do not exist yet.** No production code was modified to produce this audit — every finding below is from direct source-code inspection (`frontend/src/app/stock/[symbol]/page.tsx`, 1989 lines; `backend/services/prediction_engine.py`; `backend/services/technical_indicators.py`; `backend/api/routers/news.py`) plus read-only `GET` checks against production (`stocksense360.com/stock/AAPL?market=US`). Nothing in Sections 21-25 is implemented; each is a functional specification for future work, following this codebase's established documentation-before-implementation discipline (see the [Portfolio](Portfolio-Page-Forensic-Performance-Audit.md), [Daily Picks](Daily-Picks-Page-Forensic-Performance-Audit.md), and [Multibagger](Multibagger-Page-Forensic-Scientific-Audit.md) audits, the [Stock Movement Explanation Engine spec](Research-Analyst-Stock-Movement-Explanation-Spec.md), and [EPIC-008](EPIC-008-AI-Research-Analyst-Concept-and-Safety-Specification.md) for precedent).

## 1. Executive Summary

The Stock Detail page's recommendation logic (target price, stop-loss, entry zone, risk/reward) is genuinely evidence-based and explicitly disciplined against fabrication — the strongest scientific finding of this audit. Technical indicators are real, library-backed computations (the `ta` package), not display-only placeholders. The single largest engineering inefficiency is an unconditional, mount-time prefetch of **all three horizons'** full Prediction Engine output (3x the compute cost of what a user typically views), a deliberate but costly UX tradeoff. The single largest scientific gap is the absence of any confidence interval or tier around point-estimate target prices, and an undisclosed-to-the-user growth-rate fallback assumption in the long-horizon target model. This page is, among the four pages audited this session, the **closest to Research-Analyst-ready** — most of the raw evidence a future conversational layer would need (reasoning arrays, factor attribution, technical signals, confidence) already exists; the missing piece is a synthesizing narrative and explicit evidence-tiering layer, both already specified elsewhere in this codebase (Stock Movement Explanation Engine, EPIC-008).

## 2. What Works Well

- **Target/stop-loss methodology is genuinely evidence-based, not arbitrary.** `_estimate_target()` (`prediction_engine.py:2583+`) is horizon-specific: short-term uses ATR-projected moves, medium blends analyst consensus (`targetMeanPrice`) with the current price, long-term projects off P/E and EPS-growth (capped to a realistic sustainable CAGR, with explicit `None`-vs-`0.0` handling for `earningsGrowth` — a real correctness detail, not a shortcut).
- **Stop-loss/risk-reward logic explicitly discloses honesty over-fabrication.** `_trade_levels()` (`prediction_engine.py:940-1013`)'s own docstring: *"We adjust risk, never fabricate reward beyond what the model actually forecasts."* If a forecast move can't clear the 1.5 minimum R:R even at the tightest allowed stop, the code surfaces the honest sub-1.5 ratio rather than stretching the target.
- **Technical indicators are real, library-backed computations** (`technical_indicators.py`, using the `ta` package) — RSI, MACD, Bollinger Bands, StochRSI, ADX, Williams %R, CCI, VWAP, OBV are genuinely computed, and each signal carries a human-readable, value-embedded reason (e.g., `"Oversold (RSI 28)"`).
- **Fundamentals fetches are correctly tab-lazy and market-gated.** `screener-fundamentals`/`us-fundamentals` queries (`page.tsx:222-235`) are `enabled: tab === "fundamentals" && market === "IN"/"US"` — they do not fire on initial page load, only when the user opens that tab, with a 4-hour `staleTime` matching the backend's own cache TTL.
- **Quote polling matches the backend cache window exactly.** `refetchInterval: 60_000` with the comment *"backend Finnhub cache is 60s — no point polling faster"* (`page.tsx:118-124`).

## 3. What Is Partially Working

- **Live price vs. reference/generation price handling** presumably reuses the same `priceBasis`/actionability utilities audited on Daily Picks, but every call site on this specific page was not independently re-traced in this pass.
- **Signal feedback (`existingVote`) and manual backtest (`runBacktest`) are functional but disconnected** mini-features, not visually or narratively unified with the main recommendation panel.

## 4. What Is Inefficient

- **The single largest finding: every page visit silently prefetches all three horizons' full Prediction Engine output, not just the one being viewed.** `page.tsx:174-186`:
  ```js
  useEffect(() => {
    if (isCrypto) return;
    const allHorizons: Horizon[] = ["short", "medium", "long"];
    for (const h of allHorizons) {
      if (h === horizon) continue;
      queryClient.prefetchQuery({
        queryKey: ["prediction", symbol, market, h],
        queryFn: () => fetchPrediction(symbol, market, h),
        staleTime: 14 * 60_000,
      });
    }
  }, [symbol, market, isCrypto]);
  ```
  A deliberate, documented UX tradeoff (avoiding a "stuck" feeling when switching horizon tabs) — but it means **3x the full multi-engine Prediction Engine computation cost per page visit** (each prediction call is 5-15s server-side per its own comment), for a user who may only ever view one horizon.
- **Up to 8-9 distinct API endpoints fire from one page** (quote, crypto-movers conditionally, prediction ×1 immediate + ×2 prefetched, news, stock-accuracy, factor-attribution, score-history, plus tab-gated fundamentals) — individually reasonably cached, but no single aggregate "stock detail" endpoint exists.

## 5. What Is Scientifically Weak

- **Long-horizon target's EPS-growth fallback chain is a real approximation.** `earningsGrowth` → `revenueGrowth` → a hardcoded `0.08` default (`prediction_engine.py:2620-2624`), capped to `[-0.30, 0.35]` — reasonable, but a fallback default growth rate applied when real fields are missing is a modeling assumption disclosed only in a code comment, never surfaced to the end user (no "this target used an assumed growth rate" indicator).
- **Medium-horizon target's analyst-consensus blend uses a fixed, unjustified 70/30 weight** (`analyst_target * 0.7 + price * 0.3`) with no visible derivation, and no confidence interval around the external `targetMeanPrice` input itself (a single point estimate from analysts, itself uncertain).
- **No confidence interval or probability distribution around any target price anywhere on the page** — every target is a single point number; academic/professional practice would support at minimum a range or confidence-tier label alongside the point target.

## 6. What Is Missing

Confirmed absent from the current page by full-file review:
- Daily movement explanation ("why did this stock move today") — the Stock Movement Explanation Engine this would consume doesn't exist yet.
- Peer/sector comparison.
- An explicit thesis/invalidation section.
- Valuation fairness explanation (cheap/fair/expensive narrative beyond raw multiples).
- Target price confidence (a range or tier, not just a point number).
- Stop-loss reasoning surfaced to the user (the ATR/R:R logic is sound server-side, but nothing on the page explains why the stop is where it is).
- Historical recommendation changes.
- An analyst-style narrative summary tying technicals+fundamentals+sentiment together in prose.
- A rendered factor-contribution chart (the data is fetched via `factor-attribution`; whether it's charted or narrowly consumed was not fully traced).
- An event timeline (earnings dates, corporate actions) integrated with the price chart.

## 7. Page Architecture

```
Stock Detail page (frontend/src/app/stock/[symbol]/page.tsx)
  |-- Quote panel        <- GET /api/stocks/quote/{symbol}          (60s poll)
  |-- Prediction panel   <- GET /api/predictions/{symbol}           (active horizon, +2 prefetched)
  |-- News/Sentiment     <- GET /api/news/{symbol}                  (10min stale)
  |-- Accuracy panel     <- GET /api/predictions/{symbol}/accuracy  (30min stale)
  |-- Factor attribution <- GET /api/factor-attribution             (14min stale)
  |-- Score history      <- GET /api/score-history                 (60min stale)
  |-- Fundamentals tab   <- GET /screener-fundamentals or /us-fundamentals (lazy, tab+market gated, 4h stale)
  |-- Backtest panel     <- GET /api/backtest/{symbol}              (manual trigger only)
  |-- Signal feedback    <- GET/POST /api/feedback/signal/{symbol}  (mount, if logged in)
  |-- Paper Trade CTA    -> opens PaperTradeModal (own prediction fetch on open)
```

## 8. Backend Components

| File | Role |
|---|---|
| `backend/services/prediction_engine.py` | Signal, confidence, target, stop-loss, entry zone, risk/reward — the recommendation core |
| `backend/services/technical_indicators.py` | RSI/MACD/Bollinger/StochRSI/ADX/etc., candlestick patterns, human-readable signal reasons |
| `backend/services/news_sentiment.py` | FinBERT-scored news fetch (consumed via `api/routers/news.py`) |
| `backend/api/routers/predictions.py` | `/api/predictions/{symbol}`, 15-min in-memory cache, background 202-computing pattern |
| `backend/api/routers/stocks.py` | `/quote`, `/screener-fundamentals`, `/us-fundamentals` |
| `backend/api/routers/news.py` | `/api/news/{symbol}` |

## 9. Frontend Components

| File | Role |
|---|---|
| `frontend/src/app/stock/[symbol]/page.tsx` (1989 lines) | All fetching, prediction/quote/news/fundamentals rendering, tab state, horizon prefetch |
| `frontend/src/components/PaperTradeModal.tsx` | Trade execution UI, own on-open prediction fetch (shared with Daily Picks/Portfolio) |
| `frontend/src/utils/api.ts` | `fetchQuote`, `fetchPrediction`, `fetchNews`, `fetchFactorAttribution`, `fetchScoreHistory` |

## 10. API Calls

| Call | Trigger | Confirmed/typical timing | Cache |
|---|---|---|---|
| `GET /api/stocks/quote/{symbol}` | mount, poll every 60s | ~0.5s warm | 60s server / 55s client |
| `GET /api/predictions/{symbol}?horizon=X` | mount (active horizon) | live: 200 OK, **0.7s, 12,496 bytes** (warm) | 15min server / 14min client |
| `GET /api/predictions/{symbol}?horizon=Y,Z` | mount, silently prefetched, other 2 horizons | 5-15s each per code comment (cold) | same |
| `GET /api/news/{symbol}` | mount | live: 200 OK, **0.52s, 7,782 bytes** | 10min client |
| `GET /api/predictions/{symbol}/accuracy` | mount | not measured | 30min client |
| `GET /api/factor-attribution` | mount | not measured | 14min client |
| `GET /api/score-history` | mount | not measured | 60min client |
| `GET /screener-fundamentals` / `/us-fundamentals` | Fundamentals tab opened only | not measured | 4h client/server |
| `GET /api/backtest/{symbol}` | manual "Run Backtest" click only | not measured | none (manual call) |
| `GET/POST /api/feedback/signal/{symbol}` | mount, if logged in | not measured | none set explicitly |

## 11. Recommendation Logic Review

- **BUY/HOLD/SELL logic** — computed in `prediction_engine.py`, horizon-aware; the signal drives both target-direction and trade-level (entry/stop) direction consistently (`_trade_levels`, `page 3`/`page 4` above).
- **Confidence calculation** — used mechanistically, not cosmetically: `conf_factor = max(0.5, confidence / 100)` directly scales the projected target distance in `_estimate_target` — a real, verifiable link between confidence and target magnitude.
- **Target methodology** — horizon-specific (Section 2); short = ATR-projected, medium = analyst-consensus-blended, long = P/E+EPS-growth-projected. Scientifically reasonable but with two disclosed approximations (Section 5).
- **Stop-loss methodology** — ATR-based, tightened toward the target to honestly clear a minimum 1.5 R:R without ever fabricating the reward side (Section 2) — one of the strongest scientific-integrity findings across all pages audited this session.
- **Entry-zone methodology** — a narrow band around current price, direction-adjusted by signal (`entry_low`/`entry_high` computed per-signal in `_trade_levels`).
- **Risk/Reward logic** — `risk_reward_ratio = reward / risk`, honestly reported even when below the 1.5 target (Section 2) rather than adjusted to look better.
- **Horizon handling** — fully distinct methodologies per horizon (not one formula with parameters swapped), a real design strength.
- **Technical signal weighting** — each indicator (RSI, MACD, Bollinger, etc.) contributes an independent signal+reason (`technical_indicators.py:get_signal_summary`); how these aggregate into the final composite score/signal was not re-traced to its exact weighting formula in this pass (a Prediction Engine internals question, not specific to this page).
- **Fundamental weighting** — fundamentals feed into the Prediction Engine's broader scoring (per this codebase's established architecture, Business Quality/Financial Strength/Growth/Valuation engines) but this page audit did not re-trace the exact per-factor weight contribution visible to the user (see Section 6's missing factor-contribution chart).

## 12. Data Quality Assessment

- Technical indicators use a standard, well-tested library (`ta`), not custom/unverified math.
- Long-horizon fallback growth assumptions (Section 5) are disclosed in code comments but not surfaced to the end user.
- News/sentiment payload (7.7KB, FinBERT-scored) — duplicate/stale-news handling and headline-vs-full-content depth were not independently re-verified in this pass (would require reading `news_sentiment.py` directly, not yet done).

## 13. UX Assessment

- No visible target-confidence or stop-loss-reasoning explanation on the page today (Section 6), despite the underlying logic being sound and explicable.
- Multiple independently-loading sections (quote, prediction, news, fundamentals-on-tab, accuracy, factor-attribution, score-history) could produce a "staggered popcorn" loading feel without a coordinated top-level loading state — not independently screenshotted in this audit; a dedicated visual QA pass is recommended before treating this as confirmed.

## 14. Performance Assessment

- **The 3x-horizon prefetch (Section 4) is the single largest performance risk on this page** — multiplies Prediction Engine compute cost 3x per visit, for horizons the user may never view.
- Up to 8-9 distinct endpoints per page load, each individually reasonable, additive in aggregate — no single consolidated "stock detail" payload exists.
- Cold-cache full-page load timing across all 8-9 calls was not exhaustively measured in this pass — a Phase 0 baseline task, per [Sprint 011 §4](Performance-Scalability-UX-Sprint-011-Spec.md#4-frontend-performance-audit).

## 15. Research Analyst Readiness

Among the four pages audited this session, this page is **closest to Research-Analyst-ready.** Most of the raw evidence a future conversational layer (Epic 008) would need already exists and is already computed: per-indicator reasoning with embedded real values (`technical_indicators.py`), factor attribution (fetched, not yet traced to its exact rendering), a confidence score mechanistically tied to target computation, and horizon-specific target/stop-loss/risk-reward figures. What's missing is a synthesizing narrative layer and an explicit confirmed/likely/unknown evidence-tiering presentation — both already specified in the [Stock Movement Explanation Engine spec](Research-Analyst-Stock-Movement-Explanation-Spec.md) and [EPIC-008's Research Answer Contract](EPIC-008-AI-Research-Analyst-Concept-and-Safety-Specification.md#9-research-answer-contract). A future Research Analyst consuming this page's data would not need new backend computation for most of Section 21's "Why Buy/Hold/Sell" questions — only a reasoning/synthesis layer on top of already-computed evidence.

## 16. Intelligence Engine Integration

**Confirmed: this page does not currently consume any Intelligence Engine output** (Instrument Type/Tradability/Liquidity/Data Confidence gates, `backend/services/intelligence_engine/`). The Intelligence Engine today runs only inside Daily Picks' shadow-run pipeline. A future integration would let this page show a stock's Data Confidence score (quote freshness, fundamentals completeness — already computed by `data_confidence.py` for Daily Picks candidates) directly on the Stock Detail page, reusing the existing scoring rather than inventing a second data-quality indicator specific to this page.

## 17. Paper Trading Integration

**Already integrated** — the page's Paper Trade CTA opens `PaperTradeModal`, which fetches its own prediction for the selected horizon on open (the same pattern already flagged on Portfolio/Daily Picks as heavier than strictly necessary — Section 4's inefficiency finding applies here too, compounded by the modal's own separate fetch). Execution price handling (live vs. reference) follows the same documented pattern already verified on Portfolio and Daily Picks.

## 18. Portfolio Integration

**Confirmed: no direct integration exists.** The page does not show whether the viewed stock is already a Portfolio holding, nor a quick "Add to Portfolio" action distinct from the existing Paper Trade CTA. A natural, low-risk future addition once cross-page holding-lookup exists.

## 19. Daily Picks Integration

**Confirmed: no direct integration exists.** The page does not indicate whether the viewed stock currently appears in a recent Daily Pick, nor link to that pick's own reasoning/thesis. A read-only, one-directional cross-reference (never writing back into `daily_picks.py`) would be a natural, low-risk addition, consistent with the same constraint already established in the Stock Movement Explanation Engine spec (§15.2).

## 20. Missing Features

See Section 6 (consolidated list) — daily movement explanation, peer/sector comparison, thesis/invalidation section, valuation fairness narrative, target confidence, stop-loss reasoning, recommendation history, analyst-style summary, rendered factor-contribution chart, event timeline.

---

## 21. Future Capability — AI Equity Research Report

**Status: Planned / Not Started. Documentation only — no implementation, scoring, or UI has been built.** The Stock Detail page should eventually evolve into a full **AI Equity Research Report**, structured as a set of distinct future sections. Every section below must be a consumer of already-validated evidence (Prediction Engine output, technical signals, fundamentals, Business Quality/Growth/Valuation Intelligence engines) — never a new, parallel scoring or reasoning mechanism, per the same "no duplicate scoring engines" principle already established in the [Portfolio Copilot Vision](Portfolio-Page-Forensic-Performance-Audit.md#12-long-term-architecture).

- **Executive Summary** — a one-paragraph synthesis of signal, confidence, and the single strongest piece of supporting/opposing evidence; reuses the existing `reasoning[]` array rather than a new summarization model.
- **Investment Thesis** — the core "why this stock, why now" narrative, grounded in the same evidence the Prediction Engine already scored on.
- **Why Buy? / Why Hold? / Why Sell?** — three distinct narrative templates (not one template with the sign flipped), mirroring the Stock Movement Explanation Engine's own "three distinct reasoning modes" principle.
- **Why Today?** — a direct application of the (not-yet-built) [Stock Movement Explanation Engine](Research-Analyst-Stock-Movement-Explanation-Spec.md) to this specific stock, not a separate implementation.
- **What Changed Since Yesterday?** — requires a persisted daily snapshot of prior recommendations/scores, the same architectural prerequisite already identified for Daily Picks' Yesterday Comparison and Multibagger's Historical Qualification Timeline; a future implementation should evaluate one shared snapshot/diff mechanism across all three rather than building it independently here.
- **Target Price Methodology** — a plain-language explanation of Section 11's already-computed, horizon-specific target logic (Section 5/11) — a display layer over existing computation, not new modeling.
- **Confidence Breakdown** — decompose the single confidence number into its contributing factors (technical/fundamental/sentiment/regime — the same category breakdown RCI already uses for Recommendation Consolidation), rather than inventing a new decomposition scheme.
- **Bull Case / Base Case / Bear Case** — three scenario narratives, each explicitly labeled hypothetical per EPIC-008 §7's "scenario-based education, clearly labelled as hypothetical and not a prediction" permitted-response class — never presented as three equally-likely predictions.
- **Risks** — reuses the existing red-flag/hard-gate evidence already computed by Business Quality/Financial Strength engines and the Multibagger scorecard's Anti-Loss red-flag pattern, applied here per-stock rather than only within Multibagger's screens.
- **Catalysts** — forward-looking events (earnings, product launches) that could move the thesis — requires an event-timeline data source not yet confirmed to exist (see Section 20).
- **Invalidation Conditions** — explicit, named conditions under which the thesis should be abandoned — the single most requested missing feature (Section 6), directly addressable once a thesis narrative exists to invalidate.
- **Peer Comparison / Sector Comparison** — reuses the existing Heatmap sector-grouping infrastructure (`backend/services/heatmap_service.py`) rather than inventing a second peer-grouping taxonomy.
- **Fair Value Discussion / Valuation Discussion** — reuses the Valuation Intelligence Engine's existing sector-relative multiple methodology (already identified in the Multibagger audit as the more scientifically rigorous approach than absolute caps) rather than a new valuation model specific to this page.
- **Recommendation History** — depends on the same persisted-snapshot prerequisite as "What Changed Since Yesterday."
- **Historical Confidence** — a time series of this stock's own confidence score, reusing the existing `score-history` endpoint already fetched by this page today (`fetchScoreHistory`) — a display extension, not new data.
- **Technical Summary / Fundamental Summary** — narrative rollups of already-computed `technical_indicators.py` signals and fundamentals data, not new computation.
- **Financial Quality / Business Quality / Management Quality / Capital Allocation** — direct reuse of the existing Business Quality Engine and Growth Intelligence's Reinvestment Efficiency category (the same reuse principle already specified in the [Multibagger audit's Explainability section](Multibagger-Page-Forensic-Scientific-Audit.md#10-future-capability--multibagger-explainability)), not new scoring stacks built per-stock.
- **Research Citations / Supporting Evidence** — reuses EPIC-008 §6's evidence-disclosure discipline (source category, as-of timestamp, direct-evidence-vs-inference distinction) rather than a separate citation format.
- **AI Analyst Summary** — this is, concretely, a Stock-Detail-specific application of the future Research Analyst (Epic 008), not a separate summarization feature — must inherit EPIC-008's full evidence-grounding, non-advisory, and Research Answer Contract requirements in full.

## 22. Future Capability — Explainability

**Status: Planned / Not Started. Documentation only.** Every recommendation should eventually explain, in the same confirmed/likely/unknown tiered discipline already established for the Stock Movement Explanation Engine:

- Why BUY? / Why HOLD? / Why SELL? — reuses Section 21's three distinct narrative templates.
- Why today? — reuses the Stock Movement Explanation Engine directly.
- Why this target? / Why this stop loss? — reuses Section 11's already-computed, horizon-specific methodology (a display gap today, not a data gap — the single lowest-risk, highest-confidence item in this entire document, mirroring the Daily Picks audit's own "Universe Transparency" finding of the same shape).
- Why this confidence? — reuses Section 21's Confidence Breakdown.
- What would invalidate the thesis? — reuses Section 21's Invalidation Conditions.
- What changed? — reuses Section 21's "What Changed Since Yesterday," dependent on the shared snapshot prerequisite.
- What should I watch next? — a forward-looking synthesis of Catalysts (Section 21) and any pending Invalidation Conditions.

**"Why this target?" and "Why this stop loss?" are recommended as the first scheduled explainability item** — the underlying data and logic are already fully computed and sound (Section 11); this requires only a presentation layer, no new backend work, the same class of "already computed, never surfaced" finding that recurred across every page audited this session (Daily Picks' Universe Transparency, Portfolio's AI Confidence column, Multibagger's "why this qualifies").

## 23. Cross Integration

- **Research Analyst (Epic 008):** the primary future consumer of this entire page's evidence (Section 15/21/22) — must consume, never recompute, this page's existing signal/confidence/target/technical/fundamental evidence.
- **Intelligence Engine:** Data Confidence score reuse (Section 16) — the most concrete, lowest-risk integration available today.
- **Portfolio Copilot:** "is this stock already in my portfolio" cross-reference (Section 18), and eventually "how would adding this affect my portfolio's diversification/risk" once Portfolio Copilot's own scoring (Portfolio Page audit §10-§12) exists.
- **Daily Picks:** read-only "does this stock currently appear in a recent Daily Pick" cross-reference (Section 19), never writing back into Daily Picks' own payload/cache/ranking.
- **Multibagger:** "does this stock currently pass Quality Compounders / Multibagger Discovery / 10-Bagger Early Detection" cross-reference — a natural extension of the Multibagger audit's own "Portfolio integration" future item, applied here in reverse (from the stock's perspective rather than the screen's).
- **Paper Trading:** already integrated (Section 17); future work should reduce the modal's own prediction-refetch cost (Section 4/14) rather than expand its scope.
- **Stock Movement Explanation Engine:** the direct dependency for "Why Today?" (Section 21) and the portfolio-level rollup already specified in that engine's own §14.

## 24. Sequencing Note

Several Section 21-22 items share one prerequisite — a persisted daily snapshot of recommendations/scores (What Changed Since Yesterday, Recommendation History) — already identified as a shared need across Daily Picks, Portfolio, and Multibagger; a future implementation should design **one** shared snapshot/diff mechanism, not four independent ones. "Why this target?"/"Why this stop loss?" (Section 22) and Historical Confidence (Section 21, reusing the already-fetched `score-history` endpoint) are the two lowest-risk, no-new-backend-work items and are recommended as the first scheduled work if this capability is pursued — mirroring the Daily Picks audit's Universe Transparency and the Multibagger audit's "why this qualifies," the same recurring pattern of already-computed-but-unsurfaced evidence found on every page audited this session.

## 25. Explicit Non-Goals of This Document

Consistent with every prior audit this session: no code, endpoint, schema, or UI described in Sections 21-24 is authorized or scoped by this document. Each requires its own separate implementation-sprint approval, per this codebase's established phased-delivery convention.
