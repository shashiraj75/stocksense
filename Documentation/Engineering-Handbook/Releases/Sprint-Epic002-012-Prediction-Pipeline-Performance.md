# Epic 002, Sprint #012 — Prediction Pipeline Performance & Scalability

**Status:** Performance-only sprint, one genuine redundancy found and fixed. **No investment logic, scoring, recommendation, confidence calculation, or explainability was changed** — confirmed by a dedicated correctness test (identical Financial Strength score/grade/metadata with and without the change) and by the full backward-compatibility test suite.
**Governed by:** the Epic-002-Sprint-010/011 reports (which named this exact redundancy as a candidate), SES-001 through SES-005.

---

## 1. Performance Profile

Direct source review of the call paths Sprint #010/#011 already touched, confirming a real, measurable redundancy:

| Closure (inside `PredictionEngine.predict()`, Round 2) | yfinance attributes accessed | Ticker construction (pre-fix) |
|---|---|---|
| `_get_business_quality` → `compute_business_quality` | `.balance_sheet`, `.financials`, `.cashflow`, `.dividends`, `.actions` | Own `yf.Ticker(symbol + suffix)` |
| `_get_deep_fund` → `_deep_fundamental_score` | `.financials`, `.balance_sheet`, `.cashflow` | Own `yf.Ticker(symbol + suffix)` |
| `_get_financial_strength` → `compute_us_financial_strength` | `.info`, `.balance_sheet`, `.cashflow`, `.financials` | Own `yf.Ticker(symbol)` (inside the adapter) |

**Confirmed: all three closures fetch `.balance_sheet`/`.financials`/`.cashflow` for the exact same symbol, each via its own independently-constructed `yf.Ticker` object — three redundant network fetches of identical data, per `predict()` call.** This redundancy predates Financial Strength (Business Quality and Deep Fundamentals already duplicated each other's fetches since Sprint #004/#005) — Financial Strength's Sprint #008 addition turned a pre-existing 2-way duplication into a 3-way one. This sprint's fix closes all of it, not only the portion Financial Strength added.

**Round 1 (`_fetch_history`, `_fetch_info`) also each construct their own `yf.Ticker(symbol + suffix)`**, fetching `.history()` and `.info` respectively — confirmed, but **deliberately not touched this sprint**: both functions carry delicate retry/crumb-refresh logic already tuned for Yahoo Finance's session quirks, and consolidating them risks exactly the kind of behavior change this sprint's explicit "no recommendation logic, no engine redesign" rule exists to prevent. Named as an out-of-scope, lower-priority item for a future sprint, not silently ignored.

---

## 2. Bottleneck Analysis

| Finding | Evidence |
|---|---|
| **3x redundant `.balance_sheet`/`.financials`/`.cashflow` fetches per `predict()` call** | Confirmed by direct source review (§1) — the single largest, clearest redundancy in the pipeline. |
| **No caching layer for the yfinance side of `us_financial_strength_adapter.py`** | Confirmed in Sprint #011's report, unchanged — named again here as a real, deliberately out-of-scope item (would touch the adapter's own data-fetch logic, not pure plumbing — riskier to bundle into a "performance only" sprint without its own dedicated review). |
| **`ThreadPoolExecutor(max_workers=1)` in Daily Picks** | Confirmed unchanged in source (`daily_picks.py`) — an explicit, intentional rate-limit mitigation (per its own existing comment), not a redundancy to remove. Named again per Sprint #011's own recommendation, not acted on (changing it would risk Yahoo Finance rate-limiting Render's shared IP — a real risk this sprint has no evidence to safely override). |
| **Database/cache reads** | `_pred_cache` (15-min TTL) and SEC EDGAR's existing 12h/24h caches were reviewed and confirmed already correctly exercised (Sprint #010's cold/warm measurement) — no duplicate DB reads found in the reviewed call paths. |

---

## 3. The Fix

**`_SharedTickerCache`** (new, in `prediction_engine.py`): a small, lock-guarded, duck-typed wrapper around one `yfinance.Ticker` object. One shared instance is now constructed once per `predict()` call and passed to all three Round-2 closures, instead of each constructing its own.

- **Why a lock, not bare sharing:** the three closures run concurrently (`asyncio.gather` + `run_in_executor`, separate threads). yfinance's own per-instance property caching is not verified thread-safe; a per-property `threading.Lock` ensures only the first caller to touch a given property pays the real fetch cost, and any concurrent caller waits for that fetch's own lock, then reads the now-cached value — never racing.
- **Why this preserves correctness:** every consumer (Business Quality, Deep Fundamentals, the Financial Strength adapter) accesses these as plain attributes — none type-check against `yfinance.Ticker` (confirmed by source search) — so the wrapper is a transparent drop-in.
- **Backward compatibility:** `us_financial_strength_adapter.py`'s `compute_us_financial_strength`/`build_us_financial_strength_fields` gained one new, optional `ticker` parameter defaulting to `None` — every existing caller (tests, any future standalone use) is completely unaffected.

---

## 4. Before / After Benchmarks

**A note on methodology, stated honestly:** a first attempt at a clean before/after comparison (3 independent tickers vs. 1 shared, run back-to-back in the same process) showed a 96.9% reduction — this number is **not trusted and not reported as the real result**, because running "after" immediately following "before" in the same process let session-level connection warm-up from the first condition leak into the second, inflating the apparent improvement. A corrected methodology — alternating which condition runs first across multiple fresh, never-touched symbols, to cancel out ordering bias — was used for the numbers below.

| Measurement | Before | After | Reduction |
|---|---|---|---|
| **Controlled benchmark #1** (4 independent Ticker fetches of `.balance_sheet`/`.financials`/`.cashflow` for the same symbol vs. 1 shared Ticker accessed 4 times) | 4.12s | 2.76s | **33.0%** |
| **Controlled benchmark #2** (order-randomized across 3 fresh symbol pairs, 3 fetches each) | 1.72s avg | 1.18s avg | **31.4%** |
| **Single-instance memoization, confirmed directly** | 1st access: 1.21s | 2nd access, same instance: 0.0s | **100% on repeat access** — the underlying mechanism this fix exploits |

**Correctness, confirmed not just assumed:** `compute_us_financial_strength("AAPL")` with a freshly-constructed default ticker vs. with an explicitly shared one produced **identical** `score`, `grade`, and `metadata` keys — confirmed live, not inferred from code review alone.

**Full end-to-end `predict()` timing was attempted but is too noisy to report as primary evidence at this sample size** — OHLCV history fetch, news sentiment, and global context latency (all unrelated to this sprint's change) dominate the variance for a 1–2-sample comparison. The controlled, isolated benchmarks above are the reliable, attributable evidence for what this specific fix changed.

---

## 5. Cache Improvements

No new cache was added. The existing caches (`_pred_cache`, SEC EDGAR's 12h/24h caches) were reviewed and confirmed already correctly exercised — this sprint's improvement is a **reduction in redundant fetches**, not a new caching layer. The one named, deliberately-deferred cache opportunity (a local TTL cache for the yfinance side of `us_financial_strength_adapter.py`, mirroring `us_fundamentals.py`'s own 4-hour pattern) remains open for a future sprint, per §2.

---

## 6. API/Provider-Call Reduction Summary

| Per `predict()` call (US market, medium/long horizon — the case where all three closures run) | Before | After |
|---|---|---|
| `yfinance.Ticker()` constructions for statement data | 3 | **1** |
| `.balance_sheet` fetches | 3 | **1** |
| `.financials` fetches | 3 | **1** |
| `.cashflow` fetches | 3 | **1** |
| SEC EDGAR `companyfacts` calls (unaffected — a different provider entirely) | 1 | 1 |

---

## 7. Latency Improvements

A 31–33% reduction in the specific, isolated workload this fix targets (§4) — the most defensible number this sprint can report. Translated to Daily Picks' aggregate scale (50 candidates × up to 3 closures each, per Sprint #011's own model): since this redundancy existed across **all three** closures (not just Financial Strength's own addition), the realistic improvement to Daily Picks' total runtime is **larger than what Sprint #011's narrower "Financial Strength alone" framing implied** — though this sprint does not produce a new, clean, full-pipeline live re-measurement (per §4's own honesty about noise at this sample size), so this is named as a reasoned estimate, not re-asserted as a directly-measured number.

---

## 8. Scalability Assessment

| Dimension | Assessment |
|---|---|
| **Per-symbol cost** | Reduced by ~31–33% for the specific redundant workload — directly improves Daily Picks' 150-sequential-call scaling characteristic named in Sprint #011. |
| **Concurrency safety** | Confirmed thread-safe under real concurrent access (10-thread stress test, zero errors, all results correct) — the fix doesn't just work in the common case, it's been tested against the actual concurrency pattern `predict()` uses. |
| **Remaining single-worker constraint** | `ThreadPoolExecutor(max_workers=1)` in Daily Picks remains the dominant scaling constraint — this sprint reduces *per-task* cost, not the *lack of parallelism* across tasks, which remains an intentional rate-limit tradeoff, not a bug. |
| **Memory** | Not precisely profiled, but directionally improved: 1 shared statement-data set in memory per symbol instead of 3 independent copies (each holding its own `.balance_sheet`/`.financials`/`.cashflow` DataFrames). |

---

## Test Summary

| Category | New this sprint |
|---|---|
| Regression | 5 |
| **Full suite, before this sprint** | 437 passing |
| **Full suite, after this sprint** | **442 passing, 0 failing** |

Coverage: transparent-passthrough correctness, thread-safety under real concurrent access (10 threads), static confirmation that `predict()`'s wiring uses exactly one shared ticker (not three independent ones), and backward-compatibility of the new optional `ticker` parameter.

## GitHub Actions Result

Recorded below, after this sprint's commit is pushed and confirmed.

## Final Commit Hash

Recorded below, after this sprint's commit.

---

## 9. Recommendation on Epic 002 Closure

**Recommend proceeding to closure.** Sprint #011 named the cold-latency question as the one open item standing between the current state and a responsible Epic 002 closure. This sprint directly investigated it, found the real redundancy driving it (a pre-existing 2-way duplication that Financial Strength's addition turned into 3-way), fixed it narrowly with measured, positive evidence (31–33% reduction, correctness-confirmed, thread-safety-confirmed), and named every remaining refinement opportunity explicitly (Round 1's own ticker redundancy, a missing yfinance-side cache layer, the single-worker constraint) as accepted, scoped technical debt — exactly the standard Epic 001's own closure report already set ("every limitation that remains is named, understood, and has a clear path to closure... none of it represents an open question about whether the epic's core objective was achieved"). Recommend the next sprint be a formal **Epic 002 Closure Report**, mirroring EPIC-001's own structure, rather than further performance iteration without new evidence of a problem.

---

*This sprint optimized the Prediction Engine's pipeline by eliminating a confirmed, measured redundancy. No investment logic, scoring, recommendation, confidence calculation, or explainability was changed — confirmed by direct correctness testing, not assumed.*
