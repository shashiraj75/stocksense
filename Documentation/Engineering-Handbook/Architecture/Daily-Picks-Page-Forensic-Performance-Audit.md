# Daily Picks Page — Forensic Performance & UX Audit

## 0. Status and Purpose

**Status: Audit complete for the current, live Daily Picks page. Sections 11-16 specify future capabilities that do not exist yet.** No production code was modified to produce this audit — every finding below is from direct source-code inspection (full read of `frontend/src/app/picks/page.tsx`, `backend/api/routers/picks.py`, `PaperTradeModal.tsx`) plus read-only `GET` checks against production. Nothing in Sections 11-16 is implemented; each is a functional specification for future work, following this codebase's established documentation-before-implementation discipline (see the [Portfolio Page audit](Portfolio-Page-Forensic-Performance-Audit.md), the [Stock Movement Explanation Engine spec](Research-Analyst-Stock-Movement-Explanation-Spec.md), and [EPIC-008](EPIC-008-AI-Research-Analyst-Concept-and-Safety-Specification.md) for precedent).

**Note on the audited URL:** the requested target `stocksense360.com/daily-picks` returns HTTP 404. Confirmed live and by source (`frontend/src/app/picks/page.tsx`) that the real, live route is **`/picks`**. This audit covers `/picks`, the actual Daily Picks page.

## 1. What Works Well

- **Single-payload horizon design.** `GET /api/picks/daily?market=X` returns all three horizons (`short`/`medium`/`long`) in one response (`page.tsx:721-726`); switching horizon tabs is a pure client-side filter with zero extra network calls.
- **Only the active horizon's cards mount.** `PickCard` renders only for `picks = data.picks[horizon]` (`page.tsx:940`) — the other two horizons' cards, and their per-card quote queries, never mount until the user switches tabs.
- **Adaptive polling that respects generation state.** `refetchInterval: (query) => query.state.data?.generating ? 60_000 : 5 * 60_000` (`page.tsx:725`) — polls faster only while a generation job is actively running.
- **Truthfulness-first live-quote handling.** The `liveQuote`/`priceBasis`/`actionability` logic (`page.tsx:363-410`) requires a proven, timestamped, basis-compatible quote before claiming "price has moved since generation," falling back to a neutral, non-claiming state otherwise — a deliberate anti-fabrication design.
- **Backtest/Performance panels are lazy and cheap.** `BacktestPanel`/`LivePerformanceTracker` only fetch when `showTruth` is toggled (`page.tsx:906-910`), with long `staleTime` (60min/30min).
- **Paper Trade execution price is explicit about its basis.** `PaperTradeModal`'s `referencePrice` prop documents that execution price is not the recommendation's generation price (`PaperTradeModal.tsx:19-26`) — the same reference-vs-current distinction already established for Portfolio.

## 2. What Is Partially Working

- **Per-card quote fetching has no staggering.** Each `PickCard` independently fires `useQuery(["quote", symbol, market], fetchQuote, staleTime: 5min)` (`page.tsx:375-378`) — unlike Portfolio's `useStaggeredQueries`, there is no concurrency throttle. Safe today only because "Top 6" keeps card counts small.
- **Confusing dual "how many stocks" framing.** The subtitle says *"Top 6 AI-selected BUY calls per horizon · screened from N eligible stocks in the current quality-filtered universe"* (`page.tsx:793-798`), while a separate line says *"{n_buy} BUY signals from {n_scored} stocks"* (`page.tsx:847-848`) — three different denominators (universe screened → scored candidates → BUY signals → Top 6 shown) with no explanation of how they relate.
- **Universe-degradation state exists but isn't surfaced.** Confirmed live via `GET /api/picks/status?market=IN`: `universe_used: "static_fallback"`, `universe_degraded: true`, `universe_selection_reason: "screener_rate_limit_exhausted"` — real signal today, but nothing in `page.tsx` reads or displays it.

## 3. What Is Inefficient

- **No staggering for per-card quotes** (Section 2) — a latent, not yet triggered, inefficiency.
- **Large combined payload for a single-tab view.** `GET /api/picks/daily?market=IN` measured live at **86,083 bytes, 2.07s**, containing full `reasoning[]`, `quality_factors`, `factor_zscores` for all three horizons even though only one is visible at a time — a deliberate tradeoff for instant tab switching, not a free one.
- **`PaperTradeModal` fetches a full prediction on open** (`PaperTradeModal.tsx:82-88`) for the selected horizon — the same full multi-engine payload pattern flagged on the Portfolio page, here gated behind modal-open rather than page-load.
- **`/api/picks/status`'s rich diagnostic payload is unused by the frontend.** Job phase, universe telemetry, heartbeat fields (`picks.py:93-125`) are computed and returned but nothing in `page.tsx` ever calls `/status` — the operationally-rich endpoint and the user-facing page are currently disconnected.

## 4. What Is Missing

Confirmed absent from the current page by full-file review:
- Why this stock was selected today (beyond the existing top-3 `reasoning` snippets)
- Why price moved today (no link to the Stock Movement Explanation Engine — which itself doesn't exist yet)
- Cap bucket / risk tier labeling
- Sector diversity explanation across the Top 6
- Universe quality context surfaced from `/status`'s existing fields
- Confidence breakdown (`factor_zscores`/`quality_factors.breakdown` exist in the payload but aren't rendered as a labeled breakdown)
- Explicit invalidation/risk-warning conditions
- Compare-with-yesterday: repeat/new/removed picks, rank changes, target/stop-loss changes — no historical diffing exists anywhere in this page or its backend endpoints

## 5. Exact Files/Components Involved

| Layer | File | Role |
|---|---|---|
| Frontend page | `frontend/src/app/picks/page.tsx` (988 lines) | All fetching, rendering, horizon/market tabs |
| Frontend component | `PickCard` (`page.tsx:363-714`) | Per-pick card, live quote, entry-zone actionability, Paper Trade CTA |
| Frontend component | `BacktestPanel` (`page.tsx:158-`) | Lazy, toggle-gated backtest stats |
| Frontend component | `LivePerformanceTracker` (`page.tsx:~270-360`) | Lazy, toggle-gated live performance table |
| Frontend component | `frontend/src/components/PaperTradeModal.tsx` | Trade execution UI, own prediction fetch |
| Frontend util | `frontend/src/utils/priceBasis.ts`, `frontend/src/utils/actionability.ts` | Price-basis/entry-zone truthfulness logic |
| Backend router | `backend/api/routers/picks.py` | `/daily`, `/status`, `/performance`, `/intelligence-shadow`, `/generate` |
| Backend service | `backend/services/daily_picks.py` | Generation, caching, `_generating`/`_last_error` state |

## 6. API Calls and Timings

| Call | Trigger | Confirmed timing | Cache |
|---|---|---|---|
| `GET /api/picks/daily?market=X` | mount; polls every 60s while generating, else every 5min | live: 2.07s, 86,083 bytes (India) | client `staleTime: 55s`; server reads cached data |
| `GET /api/stocks/quote/{symbol}` ×(cards shown) | mount, per card, no stagger | 5.8s cold / 0.5s warm (same endpoint as Portfolio) | 60s server / 5min client |
| `GET /api/validation/results?horizon=X` | only when "Truth" toggled | not measured (small) | 60min client |
| `GET /api/picks/performance?horizon=X&window_days=90` | only when "Truth" toggled | not measured | 30min client |
| `GET /api/picks/status?market=X` | not called by this page | confirmed live: fast, rich diagnostic payload | n/a — currently unused by the frontend |

## 7. UX Issues

- Dual/triple "how many stocks" framing (Section 2) is genuinely confusing.
- No page-level surfacing of universe degradation even though the backend already computes it.
- No historical "is this new today / same as yesterday / target changed" indicator.
- Entry-zone/actionability logic is sophisticated but its rendered output was not independently visually verified in this audit — recommend a dedicated visual QA pass.

## 8. Recommendation-Quality Issues

- Confidence is shown as a single number; `factor_zscores`/`quality_factors.breakdown` already exist in the payload but aren't exposed as a breakdown — a display gap, not a data gap.
- No "why does this differ from yesterday" — target/stop-loss changes between generation runs are invisible.
- Risk labeling relies on `score_band` styling only — no explicit volatility/cap-size-based risk tier.

## 9. Performance Risks

- Per-card quote fan-out with no stagger — safe only because of the small "Top 6" count; would reproduce Portfolio's own confirmed connection-cap problem if per-horizon counts grow.
- 86KB/2s combined payload will grow linearly with any future per-pick field additions unless a lighter summary shape is introduced for initial paint.

## 10. Priority-Ranked Improvement List

1. **High** — surface `universe_degraded`/`universe_used` from the already-computed `/api/picks/status` response on the page.
2. **High** — resolve the "Top 6 / screened from N / n_buy of n_scored" wording ambiguity into one clear selection-funnel explanation (see Section 11).
3. **Medium** — add staggered concurrency (`useStaggeredQueries`, already built for Portfolio) to per-card quote fetching.
4. **Medium** — add a "changed since yesterday" indicator (Section 13) — requires persisting a prior day's picks for comparison.
5. **Low** — expose the existing `factor_zscores`/`quality_factors.breakdown` data as a visible confidence breakdown.
6. **Low** — reduce `PaperTradeModal`'s open-time prediction fetch to only what target/stop-loss suggestions need, once a lightweight prediction-summary endpoint exists (same recommendation as the Portfolio audit).

## 11. Suggested Implementation Plan

- **Phase 0 (measurement):** capture Lighthouse/network-waterfall baselines for `/picks` across both markets and all three horizons, per [Sprint 011 §4](Performance-Scalability-UX-Sprint-011-Spec.md#4-frontend-performance-audit)'s existing Daily Picks scope.
- **Phase 1 (low-risk, additive):** surface `/status`'s universe fields (#1); clarify selection-funnel wording (#2) — copy/display-only, no new backend endpoint.
- **Phase 2 (frontend hardening):** apply `useStaggeredQueries` to per-card quotes (#3).
- **Phase 3 (new capability):** design a daily pick-history comparison (#4) — requires a persisted prior-day snapshot, the same architectural gap named in the Portfolio Daily Performance spec's historical-timeline section; worth solving once, shared across both features (see Section 13).
- **Phase 4 (display-only):** surface existing factor-breakdown data (#5).

No phase above is authorized by this document; each requires its own separate implementation-sprint approval.

---

## 12. Future Capability — Daily Picks Selection Funnel

**Status: Planned / Not Started. Documentation only.** Directly resolves Section 2's confirmed wording ambiguity by specifying a single, consistent funnel view rather than scattered, differently-scoped numbers:

```
Full universe (all listed NSE/US symbols)
        |  Instrument Type Gate (Intelligence Engine — ETFs, non-tradable
        v  instruments excluded)
Eligible universe (tradable, liquidity/data-confidence gates passed)
        |  Alpha Engine scoring
        v
AI-scored candidates (n_scored)
        |  BUY-signal threshold
        v
BUY candidates (n_buy)
        |  Top-N ranking
        v
Top picks shown (Top 6 per horizon)
```

Every stage should reuse an already-computed number rather than introduce a new one: `screened_from` (existing `/daily` field), `n_scored`/`n_buy` (existing per-horizon `alpha_engine` metadata), and the Intelligence Engine's own Instrument Type/Tradability/Liquidity/Data Confidence gate counts (`Current-Release-Status.md`) for the "eligible universe" stage — no new backend computation, only a unified presentation of numbers that already exist across `/daily` and `/intelligence-shadow`.

## 13. Future Capability — Daily Picks Research Analyst

**Status: Planned / Not Started. Documentation only.** A per-pick conversational/explanatory capability answering: why was this stock selected today, why now, what changed, what are the risks, what could invalidate the thesis, and what should the user watch tomorrow.

**Not a new engine.** This must be a Daily-Picks-specific application of the future **Research Analyst (Epic 008)**, consuming the same evidence contract (`EPIC-008 §6`) and Research Answer Contract structure (`EPIC-008 §9` — direct answer, evidence used, risks/counter-evidence, freshness/limitations, what-would-change-the-conclusion, user-decision boundary) already specified for stock-level conversations — not a second, Daily-Picks-only explanation mechanism. "Why did this move today" specifically should reuse the [Stock Movement Explanation Engine](Research-Analyst-Stock-Movement-Explanation-Spec.md) rather than a duplicate implementation. "What could invalidate the thesis" should read from the same `reasoning[]`/`quality_factors` evidence already in the `/daily` payload, reframed as a forward-looking risk statement rather than a new score.

## 14. Future Capability — Daily Picks Yesterday Comparison

**Status: Planned / Not Started. Documentation only.** New picks today, repeated picks, removed picks, rank changes, target/stop-loss changes, and confidence changes versus the prior generation run.

**Foundational prerequisite, shared with Portfolio.** This requires persisting each day's picks in a comparable, queryable form — the same daily-snapshot architectural gap already identified in the [Portfolio Daily Performance & Attribution Intelligence spec §10.6](Portfolio-Page-Forensic-Performance-Audit.md#106-historical-timeline) and its accompanying [Sprint 011 §20.1 snapshot architecture](Performance-Scalability-UX-Sprint-011-Spec.md#201-portfolio-performance-strategy). A future implementation should evaluate a **shared** daily-snapshot mechanism (Daily Picks' own generation already produces a dated record) rather than building a second, parallel one solely for this comparison — Daily Picks arguably already has an easier path here than Portfolio, since each generation run is already a natural daily snapshot boundary; what's missing is a diff computation and its surfacing in the UI, not the underlying data collection.

## 15. Future Capability — Daily Picks Timeline / Diary

**Status: Planned / Not Started. Documentation only.** A persisted daily record: market regime, universe health, selected picks, dominant sectors, best opportunity, key risks, and an AI daily briefing — effectively a day-by-day history of what Daily Picks said and why, browsable later.

Depends on Section 14's persisted comparison data as its raw material; "market regime" and "universe health" should read from fields the backend already computes (`regime` in `/daily`'s response, `universe_used`/`universe_degraded` in `/status`) rather than new computation. The "AI daily briefing" narrative component is the natural convergence point of Section 13 (Research Analyst) applied at the whole-day level rather than per-pick.

## 16. Future Capability — Daily Market Intelligence Summary

**Status: Planned / Not Started. Documentation only.** A page-level summary distinct from any single pick: overall bullish/bearish/sideways regime, overall opportunity level, why today's specific mix of picks was selected, sector themes, macro drivers, and main risks.

**Reuse, not a new computation layer.** Regime label already exists (`data.regime.label`/`.description`, confirmed in `DailyPicksResponse` type, `page.tsx:46`) but is not currently rendered anywhere in the page per this audit's file review — the most immediate, lowest-risk step is simply surfacing the already-computed `regime` field, before any new "why today's mix" narrative is attempted. Macro drivers should reuse the existing `global_context` block already computed for Daily Picks generation (the same block named as a reusable input in the Stock Movement Explanation Engine spec) rather than a new macro-data fetch. Sector themes should reuse the existing Heatmap sector-grouping infrastructure (`backend/services/heatmap_service.py`), consistent with every other feature in this codebase that needs sector grouping.

## 17. Future Capability — Universe Transparency

**Status: Planned / Not Started. Documentation only.** Directly resolves Section 2/3's confirmed gap: `/api/picks/status` already computes `universe_used`, `universe_degraded`, `screener_raw_count`, `universe_candidate_count`, `universe_selection_attempts`, `universe_selection_reason`, `universe_selection_error_category` (`picks.py:100-119`, confirmed live via production check) — none of it reaches the Daily Picks page today. A future implementation should:
- Fetch `/api/picks/status` alongside `/api/picks/daily` (an additional, already-existing, cheap call — not a new endpoint) and surface a compact universe-health indicator (e.g., a small badge: "Full universe" vs. "Degraded — screener rate-limited, using static fallback").
- Show raw universe count, eligible count, and excluded count with reasons, reusing Section 12's Selection Funnel presentation rather than a separate, disconnected display.
- Treat this as the same transparency principle already applied elsewhere in this codebase (e.g. the Intelligence Engine's own fail-open, never-fabricate, always-disclose discipline) — a degraded universe state must be shown, not silently absorbed into an unchanged-looking Top 6.

This is the single **lowest-risk, highest-confidence** item across all of Sections 12-17: the data already exists, is already computed, and requires no new backend logic — only a new frontend fetch and a small UI addition. Recommend this be the first future-capability item scheduled, ahead of the others in this document.
