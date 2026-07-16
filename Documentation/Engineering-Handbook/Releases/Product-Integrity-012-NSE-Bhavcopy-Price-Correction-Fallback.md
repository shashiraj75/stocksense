# Product Integrity Workstream #012 — NSE Bhavcopy Last-Resort Price Correction

**Status:** Deployed to production (2026-07-16, commit `b83b12a`). Natural-run verification pending — see Section 6, updated 2026-07-16 evening with a confirmed reproduction of the gap this release targets.

**Builds directly on [Product Integrity #011](Product-Integrity-011-India-Session-Freshness-Backend-Gate.md)**, which added retry-within-existing-budget and honest `is_stale` labeling but explicitly deferred a real second data source ("out of scope for this pass... a new, unproven provider dependency"). This release adds that second source as a narrowly-scoped last-resort correction, not a replacement for yfinance.

## 1. Trigger

A follow-up conversation after #011 shipped: why does this system rely on Yahoo Finance for India close prices at all, when NSE publishes its own official daily archive ("bhavcopy")? #011's own doc had already used bhavcopy manually (via `curl`) to forensically confirm the original stale-price bug was real — but nothing in the live pipeline used it. This release wires it in as an actual runtime fallback.

## 2. Feasibility verification (done before writing any code)

Two real risks were identified and resolved with evidence, not assumption, before implementation:

- **Reachability from Railway's production network.** NSE has a documented history of requiring session-cookie warm-up and sometimes blocking datacenter/cloud IPs on its interactive `nseindia.com` API (`backend/services/nse_client.py` already works around this for other endpoints). The bhavcopy archive is served from a separate static host (`nsearchives.nseindia.com`), which was hypothesized to be less restrictive. **Verified directly**: a read-only probe run from inside the actual Railway production container (via `railway ssh`, with the user's explicit approval to register an SSH key) confirmed `HTTP_CODE: 200`, `SIZE: 368865` bytes for the real July 15, 2026 file — not simulated, not from a local machine, from production infrastructure itself.
- **URL/column format.** The working URL is `https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{DDMMYYYY}.csv`, requiring only a `User-Agent` and `Referer` header — no cookie warm-up needed, unlike `nse_client.py`'s interactive API. Column order was verified against real returned data: `SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, ...` — `CLOSE_PRICE` (not `LAST_PRICE`, which is a different, easily-confused adjacent column) is the correct official closing price field this integration reads.

## 3. What this release adds

### 3a. `backend/services/nse_bhavcopy.py` (new)

`get_bhavcopy_close(symbol: str, session_date: date) -> float | None`. Fetches and parses the whole day's CSV once per `session_date` (not once per symbol — a Daily Picks run checks ~400 symbols against the same date, so per-symbol fetching would mean ~400 redundant HTTP requests), cached in a module-level dict guarded by a `threading.Lock`, matching this codebase's established `bse_data.py`/`nse_client.py` style. Successful parses cache for 24h (a published session's bhavcopy is immutable); failures cache for only 5 minutes, so a transient network blip doesn't poison the whole day but a single generation run's ~400 lookups still don't hammer NSE. Filters to `SERIES == "EQ"` only (excludes gilts, ETF/SME series sharing the same file). Never raises — every failure mode (404, network error, parse error, symbol not found) returns `None` identically, and callers already treat `None` as "no correction available, fall back to existing behavior."

### 3b. Wiring into `prediction_engine.py`'s `predict()`

Right after `_fetch_history`'s existing 3-attempt yfinance retry budget (added in #011) is exhausted, if the last bar is still behind the expected NSE session (India only), one `get_bhavcopy_close()` lookup is attempted as a final cross-check. When it succeeds, `current_price` — which feeds `_estimate_target`, `_trade_levels`, and every downstream trade-level calculation, not just the display label — is corrected to NSE's official close, and `price_reference` reports `source: "nse_bhavcopy"`, `is_stale: false` (not `null` — this is a *known*-fresh price, not an unchecked one). **This is a deliberate, meaningful behavior change beyond #011's scope**: when the correction fires, entry/target/stop-loss levels for that pick are computed from the corrected price, not the stale one. #011 explicitly stated it "does not change... entry/target/stop-loss" — that statement no longer holds when this release's correction actually fires; it holds only when bhavcopy is unavailable (in which case the code falls back to #011's original behavior unchanged).

### 3c. What is explicitly NOT touched

- **OHLC history / technical indicators.** Bhavcopy only supplies one day's closing price, never history — `df` (the yfinance-sourced OHLC history feeding RSI, moving averages, technical scoring) is completely unaffected. A corrected pick's *price* reflects the true close; its *technical indicators* are still computed from whatever history yfinance returned, which may itself still show a stale last bar. This is a disclosed, accepted limitation — fully correcting indicators would require rebuilding OHLC history from bhavcopy day-by-day, a materially larger integration explicitly out of scope here.
- **US Daily Picks.** Bhavcopy is NSE-specific; `_bhavcopy_close` is only ever computed when `_expected_session is not None`, which #011 already scoped to `market == "IN"` exclusively.
- **Universe selection, scoring weights, confidence formulas.** Only the reference price input changes; every downstream formula is unchanged code, just fed a (when applicable) more accurate input.

## 4. Tests

- `test_nse_bhavcopy.py` — 11 tests, all mocked (no real network calls): CSV parsing (correct `CLOSE_PRICE` column, `EQ`-series filtering), cache behavior (one fetch per date regardless of symbol-lookup count, negative-cache TTL for failures), 404-not-retried vs network-exception-retried distinction, case-insensitive symbol lookup, URL format.
- `test_nse_bhavcopy_price_correction_wiring.py` — 6 structural tests (following the same source-inspection convention #011 used for `predict()`'s internals, since full end-to-end mocking of `predict()` remains impractical): correction only attempted when the bar is actually stale, `current_price` correctly prioritizes `_bhavcopy_close` at both construction sites, `price_reference.source`/`is_stale` correctly reflect bhavcopy when used, bhavcopy never appears inside `_fetch_history` (confirms OHLC history is untouched), and the import follows this file's existing lazy-import convention.
- Full backend suite: **2157/2157 passed** (2140 baseline + 17 new).
- No frontend changes this release.

## 5. Rollback

Revert the four wiring edits in `prediction_engine.py` (the `_bhavcopy_close` computation block plus the two `current_price`/`price_reference` sites) and delete `nse_bhavcopy.py`. Purely additive — no schema, no migration, no removed/renamed field. Reverting restores exactly #011's prior behavior (retry + honest `is_stale` labeling, no second data source).

## 6. Natural-run verification plan

The next natural India Daily Picks generation is the first opportunity to observe real bhavcopy corrections firing in production. Verify via `GET /api/picks/daily?market=IN`: check for any pick with `generation_reference_source: "nse_bhavcopy"` (would only appear on symbols where yfinance was still stale after all 3 retries) and confirm `generation_reference_is_stale: false` for those. Also worth a spot-check that `entry_low`/`target`/trade levels for a bhavcopy-corrected pick are plausible relative to the corrected price, not the stale one — the first live confirmation that the correction actually propagates into trade-level math as designed, not just the label.

**2026-07-16 evening update — confirmed the gap this release targets is real, and confirmed this fix had not yet run when it happened.** A user screenshot review of the Picks page showed HDFCBANK and LUPIN's `generation_reference_as_of` at **Jul 14** (2 sessions stale) and CUB's at **Jul 15** (1 session stale) — all from the batch generated at **05:21 AM IST / 23:51 UTC Jul 15**, i.e. this specific batch predates both this fix (`b83b12a`, committed 08:03 UTC Jul 16) and #011 (`ec3cef0`, committed 07:09 UTC Jul 16) by several hours; the generation trigger that produced this very PI-011/#012 work is the same batch this investigation re-confirmed. Live yfinance queries at investigation time (evening Jul 16) confirmed both symbols' Jul 15 bars existed by then — the staleness was provider-side lag at the specific moment of generation, not a permanent gap, exactly the failure mode this release exists to catch. A same-day manual re-trigger was attempted and correctly blocked by the existing `already_fresh` idempotency guard (no `force` parameter exists, deliberately) — so the actual first live test of this fix remains the next scheduled 2:07 AM IST run. **Action for that check:** confirm HDFCBANK and LUPIN specifically (the two symbols confirmed stale today) come back with `generation_reference_source: "nse_bhavcopy"` or a same-session `yahoo_daily_history` reference, not another Jul-14/Jul-15 repeat.
