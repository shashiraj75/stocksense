# Product Integrity Workstream #005 — Global Price-Integrity Audit and Phase GPI-0 Containment

**Status:** Global audit complete. Phase GPI-0 (frontend-only truthfulness containment for the Daily Picks Backtest panel and Live Performance Tracker) implemented. **This is a containment, not a repair** — the underlying backend defects it responds to remain open and are explicitly not fixed by this change.

**Scope note:** this record covers (1) a summary of the global, platform-wide price-integrity audit's confirmed findings, and (2) the frontend containment implemented in direct response to three of them. It does not claim any backend defect described below has been repaired.

## 1. Global audit findings (summary)

A strictly read-only, platform-wide audit ("global price-integrity audit") examined every price-consuming feature in StockSense360 — Daily Picks (both markets), the individual Stock Analysis page, quote endpoints, charts, Screener, Heatmap, Alerts, Paper Trading, Portfolio, Recommendation Consolidation, the landing page, Backtest, Validation, and Outcome Resolution. It confirmed the India Daily Picks session-freshness defect (already contained in Product Integrity Workstream #004) is genuinely India-specific — the same audit found **18/18 US Daily Picks fresh at generation time**, versus 5/17 for India in the same batch.

Beyond that already-contained issue, the audit confirmed new defects directly relevant to this containment:

- **D1 — Validation universe market-routing defect.** `backend/services/validation_engine.py`'s per-stock market-detection heuristic (`_backtest_stock`, lines ~453-454) ignores the caller-supplied `universe` parameter and mis-routes a material fraction of the India universe to the wrong exchange — confirmed to misclassify 47/134 (35%) Nifty-100 symbols and 19/112 (17%) mid-cap symbols, including a confirmed live case (`INFY`) resolving to its NYSE ADR (USD) instead of its NSE line (INR). This directly contaminates published "walk-forward backtest" accuracy statistics for the India universe.
- **D2 — Prediction-entry vs. outcome-resolution price-basis mismatch.** The price shown to users at Daily Picks generation time is preferentially Yahoo's live tick-quote field; the price used to compute a resolved pick's published return% is independently re-fetched from historical daily bars, days to months later, via a structurally different data product. Neither price is persisted for later audit — the `outcomes` table stores only derived return percentages.
- **D3 — Benchmark returns never populated by the live resolver.** `benchmark_return_5d/20d/60d` are only ever set by a one-off migration script, never by the live outcome-resolution pipeline. The frontend defaults a missing benchmark return to `0`, so every "Alpha vs. benchmark" figure computed from a live-resolved outcome is silently comparing against an implicit 0%, not a real one.
- **D4 — Daily Picks Backtest panel is market-blind.** `frontend/src/app/picks/page.tsx`'s `BacktestPanel` queries `["validation", horizon]` → `GET /api/validation/results?horizon=...` with no market/universe parameter at all. The backend endpoint defaults to the India/Nifty-100 universe. **Confirmed: the US Daily Picks tab could display India validation results while labelling them "Alpha vs S&P 500."**
- **D5 — Live Performance Tracker cross-market blend.** `LivePerformanceTracker`'s query (`["picks-performance", horizon]`) also omits market; the backend query it hits intentionally has no market filter and blends India and US resolved picks into one combined statistic, then labels the result against whichever market's benchmark name the currently-selected tab happens to show.

Full detail, file:line citations, and the complete source-to-consumer matrix for every other audited feature are in the audit's own report (delivered directly to the requester; not separately filed as a document by that audit's own instructions).

## 2. Phase GPI-0 — what this containment does

Frontend-only. No backend file, database schema, migration, or Phase 1A/1A.3 code was touched. Nothing was staged, committed, or pushed as part of this change (per instruction, pending explicit approval).

- `frontend/src/components/ValidationIntegrityHold.tsx` (new) — exports `INTEGRITY_HOLD_ACTIVE` (currently `true`) and `ValidationIntegrityHold`, a notice component carrying the required primary/secondary wording and full documentation of the five conditions that must all be true before the hold can be lifted.
- `frontend/src/app/picks/page.tsx` — the two conditional render calls (`{showTruth && <BacktestPanel .../>}`, `{showTruth && <LivePerformanceTracker .../>}`) are replaced with a single unconditional `{INTEGRITY_HOLD_ACTIVE && <ValidationIntegrityHold />}`. Neither `<BacktestPanel>` nor `<LivePerformanceTracker>` is referenced anywhere else in the file, so their `useQuery` hooks cannot mount and their requests (`GET /api/validation/results`, `GET /api/picks/performance`) never fire — this is a render-tree exclusion, not a CSS-only hide. The now-unused "Show Real Accuracy" toggle (`showTruth` state, its button, and the `CheckCircle` icon import it alone used) was removed as dead code once nothing gated by it remained meaningful; the notice is shown unconditionally on both IN and US tabs instead of requiring an opt-in click, consistent with this being a transparency notice rather than an optional detail view.
- `BacktestPanel` and `LivePerformanceTracker` themselves are **untouched and still fully implemented** in `page.tsx`, exactly as required — restoration after the backend fixes below land is a matter of re-adding their render call sites (and, at that point, deciding what UX — toggle or unconditional — the restored panels should use).

## 3. What is explicitly unchanged

- Daily Picks signal generation and scoring — not touched.
- India session-freshness containment (`sessionFreshness.ts`, the stale/unknown card treatment) — not touched, still active.
- The cross-market payload guard (`payloadMarketGuard.ts`) — not touched, still active.
- Paper Trade containment — not touched.
- Market switching, Daily Pick cards, Signal Strength, non-price reasoning — all unchanged.
- validation_engine.py, outcome_logger.py, prediction logging/outcome persistence, database schemas/migrations — none read-write touched; this is a frontend-only change.

## 4. Removal criteria

Do not remove this hold, or restore `BacktestPanel`/`LivePerformanceTracker` to the render tree, until **all** of the following are independently true:

1. `validation_engine.py`'s market-routing heuristic is fixed to use the caller-supplied `universe` instead of re-deriving its own guess (D1).
2. Validation results have been rerun for the correct market universe on both IN and US.
3. The outcome entry-price contract is corrected so the price shown to users at generation time and the price used to resolve an outcome share one reconciled, auditable basis (D2).
4. `benchmark_return_5d/20d/60d` are populated by the live outcome resolver, not left `NULL` and silently defaulted to 0 client-side (D3).
5. Market filtering is added to both `/api/validation/results` and `/api/picks/performance`, and their frontend query keys/requests are updated to pass and key on it (D4, D5).

## Explicit confirmation

- The validation and outcome-resolution defects (D1-D5) described above are **not repaired** by this change — this document does not claim otherwise.
- India Daily Picks backend session-freshness remains contained at the frontend layer (Product Integrity #004) but is **not permanently fixed at the backend** — a backend-side freshness gate remains a separate, not-yet-started follow-up phase.
- Daily Picks scoring, signal generation, India freshness containment, and the cross-market guard were not changed by this phase.
- No backend, database, validation, generation, backfill, or Phase 1A/1A.3 code was changed.
