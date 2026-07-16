# Product Integrity Workstream #011 — India Session-Freshness Backend Gate

**Status:** Deployed to production (2026-07-16, commit `ec3cef0`).

**Scope note:** this closes the backend half of the gap Product Integrity #004 explicitly deferred ("a backend-side freshness gate... out of scope for this phase... a separate not-yet-scheduled follow-up phase"). It does not change Daily Picks scoring, ranking, confidence, entry/target/stop-loss formulas, universe selection, or the frontend's own independent client-side freshness disclosure (Product Integrity #004), which remains the display source of truth and is unaffected by this release. Scoped to India (`market == "IN"`) only, matching the disclosed issue; a US-side audit of the identical pattern is explicitly flagged as future work, not silently assumed unnecessary.

## 1. Trigger

A production screenshot review (2026-07-16, ~12:14 PM IST) showed every India Daily Pick displaying "Price reference is stale — this pick used market data from Tue, 14 Jul, 2026, not the latest completed NSE session," despite generation having run normally that morning (~5:21 AM IST) and Wednesday July 15 being a genuine NSE trading day (verified against the holiday calendar — not a holiday).

## 2. Root cause

`backend/services/prediction_engine.py`'s `PredictionEngine.predict()` fetches each stock's price history via a plain `yf.Ticker(symbol).history(...)` call and uses the last row as the reference price. For a meaningful fraction of NSE symbols, Yahoo/yfinance's published data lags one trading session behind at fetch time. This exact pattern was already forensically confirmed in [Product Integrity #004](Product-Integrity-004-India-Daily-Picks-Session-Freshness-Containment.md) (a 2026-07-14 audit found ~70% of India picks carrying a stale reference on a sampled day, confirmed as per-symbol provider lag — not a uniform indexing bug, not a caching bug in this codebase, not corporate-action-related). That workstream built the frontend disclosure banner (correctly, and it is still working exactly as designed) but explicitly left the backend unable to detect or react to the same condition at generation time.

**Not a bhavcopy-fallback situation:** an initial investigation hypothesis assumed an NSE bhavcopy fallback already existed in this codebase (reasoning by analogy with `bse_data.py`'s BSE fundamentals fallback). A direct repo-wide grep found no bhavcopy integration anywhere — `bse_data.py` fetches *fundamentals* (P/E, ROE, etc.) from BSE's own API, not price/OHLC data, and does not touch bhavcopy at all. Building a new bhavcopy-based price-fallback integration from scratch was judged out of scope for this pass (new, unproven provider dependency) — the fix below uses retry against the existing provider plus honest labeling, not a new data source.

## 3. What this release adds

### 3a. `get_expected_latest_completed_nse_session()` (new, `backend/services/market_hours.py`)

A direct Python port of frontend `marketHours.ts`'s `getExpectedLatestCompletedSession()` (Product Integrity #004) — the first backend equivalent of that algorithm. Given a timestamp, walks backward day-by-day over the same weekend/holiday calendar `is_market_open()` already uses (fixed holidays, `NSE_EXTRA_HOLIDAYS`, weekend), returning the most recent date that should already have a completed, published NSE session bar. Naive datetimes are treated as IST; the function is pure (no I/O), so it's directly unit-testable — 11 tests cover close-boundary edge cases, weekend/holiday skipping, and timezone handling.

### 3b. Retry-on-stale in `_fetch_history()` (`prediction_engine.py`, inside `predict()`)

`_expected_session` is computed once per `predict()` call (India only; `None` for US — no assumption made there). Within `_fetch_history`'s existing 3-attempt retry loop (previously retried only on an empty DataFrame or an exception), a **non-empty but unexpectedly-behind** bar now also triggers a retry, gated on `attempt < 2` so the existing attempt budget is reused rather than extended — the final attempt is always accepted regardless of staleness, preserving the existing bounded-retry philosophy and never risking an unbounded loop. This gives genuinely transient per-symbol Yahoo lag a real chance to resolve within the same generation run, which a purely disclosure-based fix never could.

### 3c. Honest `price_reference` labeling (both construction sites in `predict()`)

`price_reference` now carries `is_stale` (`True`/`False`/`None`) and `expected_session` (ISO date or `None`), computed once and reused at generation time rather than only reconstructed later from `as_of` alone. `is_stale` is `None` — never a false "fresh" claim — whenever no expected-session check was actually performed (i.e. for US, where this gate does not yet exist). `daily_picks.py` copies these through to the published pick as `generation_reference_is_stale` / `generation_reference_expected_session`, alongside the existing `generation_reference_price`/`source`/`price_basis`/`as_of` fields — purely additive; no existing field was renamed or removed.

### 3d. `_predict_stock` error-passthrough fix (`daily_picks.py`)

A secondary, related gap found during this investigation: `predict()` returns error-shaped dicts (`{"error": ..., "code": "DATA_PROVIDER_UNAVAILABLE"}`) from several paths (timeout, empty history, insufficient data) — such a dict is truthy and has no `"signal"` key, so it previously slipped past both of `_predict_stock`'s existing checks (`if not result`, `if result.get("signal") == "REJECTED"`) and got appended into the day's candidate pool with mostly `None`/default fields instead of being cleanly excluded. Added an explicit `if result.get("error")` check, using the same "log and return `None`" pattern the existing `REJECTED` gate already uses.

## 4. What this release does not do

- Does not fetch price data from any new provider (no bhavcopy integration built).
- Does not exclude/drop a symbol from the day's candidate pool solely for being stale — the retry gives it a real chance to resolve, and if it's still stale after retries, the pick is generated and correctly labeled (both to the backend's own persisted record and the frontend's existing independent disclosure), not silently dropped or silently accepted as fresh. Excluding stale-priced symbols entirely was considered and explicitly rejected as a materially bigger, riskier product-composition decision (could reduce picks quantity/coverage unpredictably) than this pass's scope.
- Does not change the frontend's `sessionFreshness.ts`/`marketHours.ts` display logic — it already correctly derives staleness independently from `generation_reference_as_of` and continues to be the display source of truth. The new backend fields are additive provenance for future consumers, not a replacement.
- Does not audit or fix the identical potential pattern for US Daily Picks (`market == "US"`) — explicitly flagged as follow-up work, not silently assumed fine.
- Does not change Daily Picks scoring, ranking, confidence, entry/target/stop-loss, or universe selection.
- Does not touch GPI-0, Phase 1A/1A.3, or any Multibagger code from the prior four releases this session.

## 5. Tests

- `test_market_hours_expected_session.py` — 11 tests, pure date-arithmetic coverage (close boundary, weekend skip, known 2026 holidays, naive/UTC datetime handling).
- `test_india_session_freshness_backend_gate.py` — 10 tests: structural verification of the retry/staleness wiring inside `predict()` (following this codebase's own established convention for testing deeply-embedded logic inside that method — see `test_prediction_engine_shared_ticker_performance.py`'s identical approach) plus fully behavioral tests of the `_predict_stock` error-passthrough fix (predict() itself mocked out, no yfinance/network involved).
- Full backend suite: **2140/2140 passed** (2119 baseline + 21 new).
- No frontend changes this release — full frontend suite not re-run (no files touched).

## 6. Rollback

Revert `backend/services/market_hours.py`'s new function, `backend/services/prediction_engine.py`'s retry/labeling additions, and `backend/services/daily_picks.py`'s two new field-copy lines plus the `_predict_stock` error check. All changes are additive (new fields, new function, an extra retry branch within an existing loop) — no schema, no migration, no removed/renamed field, safe to revert independently of any other recent release.

## 7. Natural-run verification plan

The next natural India Daily Picks generation (scheduled ~21:56 UTC / 3:26 AM IST, per the frozen `daily_picks_in.yml` cron) is the first opportunity to observe this fix's actual effect on the live stale-rate. Verify via `GET /api/picks/daily?market=IN`: check whether `generation_reference_is_stale`/`generation_reference_expected_session` are now present and populated, and whether the proportion of `is_stale: true` picks is lower than the ~70%+2026-07-16 baseline this workstream was triggered by (a full elimination is not expected or claimed — retry improves the odds for transient lag, it cannot fix a symbol Yahoo genuinely hasn't published yet).
