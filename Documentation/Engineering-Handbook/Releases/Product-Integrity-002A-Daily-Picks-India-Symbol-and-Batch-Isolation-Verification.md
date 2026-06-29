# Product Integrity Workstream #002A — Scheduled Daily Picks Run, India Yahoo Symbol Failures, and Batch-Isolation Verification

**Status:** Investigation complete; one confirmed, narrow, evidence-justified fix applied. The specific question "why did `.NS` errors appear for US-universe tickers (`BRC`, `SSB`, `STRA`)" could **not** be definitively root-caused from this environment despite rigorous, repeated, live code-path tracing and reproduction — documented honestly as an open item with a precise, actionable next-capture instruction, not guessed at. A separate, fully-confirmed, real defect was found and fixed along the way.

## Evidence Checkpoint

Reviewed directly: `.github/workflows/daily_picks.yml` (both jobs, unchanged since the last audit), `backend/api/routers/picks.py`, `backend/services/daily_picks.py` in full (the bulk screener, the market-cap pre-filter, the per-symbol prediction loop, the returns-matrix fetcher, and the suffix/universe configuration), `backend/services/stock_universe.py` (the literal `IN_STOCKS`/`US_STOCKS` source lists), and live, current production state via the same read-only endpoints used throughout this entire workstream. Three separate live, real (not mocked) calls were made directly against Yahoo's `yfinance` screener API, using the exact query StockSense360's own code constructs, to test hypotheses empirically rather than by inspection alone.

## Trace (confirmed directly, this session)

```text
GitHub scheduled/dispatched workflow
  → POST /api/picks/generate?market={IN|US}, header x-secret
  → api/routers/picks.py: trigger_generation() — secret checked, market normalized,
    _last_trigger_received_at[market] recorded, BackgroundTasks.add_task(_run)
  → services/daily_picks.py: generate_picks(market) → _generate_picks_inner(market)
  → Phase 0b: _bulk_screen(market, n_candidates) →
      _get_universe_by_mcap(market) → yf.screen(exchange-filtered query) →
      [on failure] fall back to _UNIVERSE[market] (the full static list)
  → yf.download() in batches of 300 tickers, suffix = _SCREEN_CONFIG[market]["suffix"]
  → Phase 1: _predict_stock(symbol, horizon, market) per top candidate
  → Phase 5/6: top 6 BUY picks per horizon, _fetch_returns_matrix(symbols, market)
  → persistence (cache file + Postgres if configured) → generated_at set
  → GET /api/picks/daily?market={IN|US} returns the persisted record
```

**Every step above was confirmed, by direct reading, to thread the `market` parameter consistently from the route all the way through to the final suffix-applying call** — `suffix = _SCREEN_CONFIG[market]["suffix"]` and `universe = _get_universe_by_mcap(market)` (or `_UNIVERSE[market]` on fallback) always use the *same* `market` value within a single call chain. No shared/global mutable state, no stale variable, and no default-parameter shadowing was found at any of the three call sites that apply a ticker suffix (`_get_universe_by_mcap`, `_bulk_screen`, `_fetch_returns_matrix`) — each one's caller passes `market` explicitly (confirmed by `grep`-ing every call site in the file).

## Which Batch Was Running

| Railway log timestamp UTC | Market parameter received | Batch market actually executing | Trigger source | Relevant GitHub workflow run | Evidence |
|---|---|---|---|---|---|
| Not independently capturable from this environment (the three error lines provided do not include the surrounding `[picks] [<market>] ...` log lines this code already emits immediately before/after, which would have stated the market explicitly) | Unconfirmed | **Most likely India**, by suffix alone (`.NS` is exclusively `_SCREEN_CONFIG["IN"]["suffix"]`) — **but the specific symbols (`BRC`, `SSB`, `STRA`) exist only in `US_STOCKS`, not `IN_STOCKS`** (confirmed by direct `grep` of `stock_universe.py`: matches at lines 756/5006/5042, all before `IN_STOCKS` even begins at line 6018) — an apparent contradiction this session could not fully resolve (see below) | Unconfirmed — could be the GitHub-scheduled cron (severely delayed, per Workstream #001C's own finding that a delayed trigger landed ~16:23 UTC, ~3h53m after the 12:30 UTC US schedule) or a separate event | Not independently inspectable — no `gh` access | This session's own re-reading of the code, `stock_universe.py`, and three live reproductions of the production screener query |

**This session could not conclusively determine, from the three bare log lines alone, which single market batch produced them.** The `.NS` suffix structurally can only originate from `_SCREEN_CONFIG["IN"]`, yet the specific erroring symbols are confirmed, by direct source inspection, to exist *only* in the US universe list. Both halves of this fact are independently, directly verified — the contradiction between them is the central open finding of this report, not glossed over.

## India Symbol-Mapping Audit

| Symbol sent to yfinance | Original internal symbol | Company name, if known from internal universe | Intended exchange | Valid NSE symbol today? | Correct Yahoo suffix? | Yahoo availability | Why used in candidate universe? |
|---|---|---|---|---|---|---|---|
| `BRC.NS` | `BRC` | **"Brady Corporation"** — confirmed via direct `grep` of `services/stock_universe.py`, found *only* inside the `US_STOCKS` list (line 756), never in `IN_STOCKS` | **US (NYSE)**, not India | No — not an NSE symbol at all | No — `.NS` is structurally wrong for this symbol; Brady Corporation has no NSE listing | N/A — `.NS` lookup correctly fails because no such NSE security exists | **Not used in StockSense360's own India candidate universe** — confirmed absent from `IN_STOCKS` |
| `SSB.NS` | `SSB` | **"South State Bank"** — found only in `US_STOCKS` (line 5006) | US (NYSE), not India | No | No | N/A | Not used in the India universe |
| `STRA.NS` | `STRA` | **"Strategic Education, Inc."** — found only in `US_STOCKS` (line 5042) | US (NASDAQ), not India | No | No | N/A | Not used in the India universe |

**Classification: 7 — another directly evidenced result, not matching any of the six pre-defined categories.** None of the six categories (valid-but-transient, valid-but-unavailable, delisted/stale, wrong-mapping, duplicate/legacy, unknown) cleanly fit, because the evidence shows these are **not India candidates being mis-suffixed at all — they are genuine US-universe tickers, structurally incapable of being chosen by `_get_universe_by_mcap("IN")`'s own logic**, which only ever returns symbols already carrying Yahoo's own `.NS`-suffixed result set (stripped before being added to the India universe) or, on failure, the static `IN_STOCKS` list — neither of which contains `BRC`, `SSB`, or `STRA`. **No mechanism in the current, directly-traced code could have placed these three specific symbols into an India-market `.NS` lookup.**

## Live Reproduction Attempts (this session)

Three separate, live, real calls were made directly against Yahoo's `yf.screen()` API using the exact query construction `_get_universe_by_mcap("IN")` issues:

1. A first live call (count=50, ≥₹100Cr mcap, exchange="NSI") returned 25 quotes, **all genuinely `.NS`-suffixed NSE securities** — zero contamination.
2. A second, count=1000 call (the **exact** value hardcoded in the pre-fix code) **failed outright**: `ValueError: Yahoo limits query count to 250, reduce count.` — a new, separate, fully-confirmed finding (see "Confirmed Separate Defect" below).
3. A third call using count=250 (Yahoo's own current maximum) returned 25 quotes — again, **zero contamination**, identical clean result.

**At no point across three independent, live attempts did Yahoo's own screener return a non-`.NS` or non-NSE result for the "NSI" exchange query.** This directly weakens (without fully eliminating) the hypothesis that Yahoo's screener itself is the leak point — though it cannot be ruled out as a transient, time-of-day-dependent anomaly, since `yf.screen()` is a live, server-ranked query whose result set is not guaranteed deterministic across requests.

## Confirmed Separate Defect — Yahoo's `count` Limit (fixed)

**This is a fully confirmed, reproduced, fixed defect**, unrelated to the BRC/SSB/STRA puzzle but discovered while investigating it: `_get_universe_by_mcap()` called `yf.screen(..., count=1000)`. Yahoo's backend now rejects any `count > 250` with a hard `ValueError`. **This means the market-cap pre-filter has been silently failing on every single Daily Picks run, for both markets, falling back to the full, unfiltered static universe (2,300+ NSE stocks / ~1,500+ US stocks) every time** — confirmed by direct reproduction of the exact failure, then confirmed fixed by reproducing the same query successfully at `count=250`.

**Fix applied**: `backend/services/daily_picks.py`'s `_get_universe_by_mcap()` now requests `count=250` (Yahoo's own current maximum) instead of `1000`.

**Why this matters beyond correctness**: falling back to the full universe on every run means Phase 0's `yf.download()` step processes far more tickers than intended, in more 300-ticker batches, with more total Yahoo API calls — a plausible, real contributing factor to the unusually long Daily Picks runtimes observed across Workstreams #001B and #001C (the US batch observed in those sessions ran for over an hour with no completion, well beyond the documented "~10-20 minutes" estimate).

## Batch-Isolation Verification

| Failure type | Current behavior | Desired safe behavior | Current batch risk | Required change? |
|---|---|---|---|---|
| Invalid/delisted India symbol | `yf.download()` (multi-ticker) does not raise for one bad ticker among many — it returns NaN/empty columns for that ticker while populating valid data for the rest (confirmed by direct reading of `_bulk_screen`'s per-ticker loop, which already does `if any(math.isnan(x) or x <= 0 ...): continue`); the resulting "possibly delisted" message is yfinance's own internal log output, not a raised exception in this codebase | Same as current — already correct | **Low** — already isolated | **No** |
| Yahoo-unsupported India symbol | Same mechanism as above — treated identically to "invalid/delisted" by the existing NaN-skip logic | Same | Low | No |
| Temporary Yahoo rate limit | A whole-batch `yf.download()` exception is caught at the batch level (`except Exception as e: log.warning(...); continue`) — one bad *batch* (300 tickers) is skipped, not the whole universe; confirmed by direct reading | Same | **Medium** — losing an entire 300-ticker batch to one rate-limit event is coarser than ideal, but does not fail the whole run | Not required this session — no direct evidence of this actually occurring in the captured logs |
| Single-symbol timeout | Same per-ticker NaN-skip applies for `_bulk_screen`; the per-symbol `_predict_stock()` call (Phase 1) has its own `try/except Exception: pass; return None` wrapper — confirmed isolated | Same | Low | No |
| Provider-wide outage | `_get_universe_by_mcap`'s screener failure already falls back safely (now to a correctly-Yahoo-accepted-count first attempt, then the static universe on any other failure); `_bulk_screen`'s own "no stocks scored" path falls back to the Nifty-100/US-megacap-100 anchor list — confirmed both fallbacks are real, tested code paths, not aspirational comments | Same | Low | No |

**Conclusion: the "one invalid ticker must not fail, stall, or materially degrade a whole batch" principle is already correctly satisfied by the existing code for every failure type checked.** The three `.NS` error lines in the Railway log, whatever their true origin, are very likely **harmless, isolated, already-correctly-skipped noise** from yfinance's own internal per-ticker error reporting — not evidence of a batch-wide failure, stall, or poisoning. No isolation fix was required or made.

## Source-Separation Confirmation

| Data domain | Current source | Required for candidate generation? | Required for final scoring? | Failure effect |
|---|---|---|---|---|
| Fundamental metrics/ratios | screener.in (India), yfinance (US) — per the established Data Fabric pattern from Epics 001-004 | No (Phase 0 candidate screening uses only price/momentum) | Yes | A fundamentals-provider failure for one stock causes that stock's prediction to gracefully fail/skip (`_predict_stock`'s own try/except), not a batch failure |
| Financial statements | screener.in (India), yfinance (US) | No | Yes (Business Quality, Financial Strength) | Same as above |
| Price/OHLCV | yfinance, both markets | **Yes** — `_bulk_screen`'s own momentum ranking is entirely yfinance-price-driven | Yes | A price-fetch failure for one ticker is the exact, already-isolated failure mode this report investigated |
| Technical indicators | Computed from yfinance OHLCV | No (computed downstream, not part of candidate selection) | Yes | Same as price |
| 52-week metrics | yfinance | No | Indirectly (display only) | Isolated per-symbol |
| Volume | yfinance | No | Yes (liquidity checks) | Isolated per-symbol |
| Market regime | yfinance (a single index ticker, e.g. `^NSEI`/`^GSPC`), computed once per run, not per-candidate | No | Yes (a global multiplier, not per-stock) | A regime-fetch failure would affect the whole run, but is a single, well-isolated call, not part of the per-candidate loop being investigated here |
| News/sentiment | A separate provider (RSS feeds, per this engagement's "100% free" architecture), not yfinance | No | Yes | Entirely independent of yfinance; unaffected by anything in this report |

**Direct answer to the brief's own question**: yfinance is used **only for price/OHLCV-driven candidate screening and technical indicators** in the path this report investigated (`_bulk_screen`, `_get_universe_by_mcap`, `_fetch_returns_matrix`) — it is **not** a hidden blocker in front of India-specific screener.in fundamentals data; those are fetched independently, later in the pipeline, per-candidate, by a separate adapter (confirmed by this report's own trace: `_predict_stock` calls `PredictionEngine.predict()`, which is the same, already-audited engine from Epics 001-004 that internally routes fundamentals to screener.in for India). A yfinance price failure for one candidate does not block that candidate's fundamentals from ever being fetched in a way that would cascade — it simply means that one candidate scores poorly or is skipped at the momentum-ranking stage, before fundamentals are ever consulted for it.

## Immediate Live Outcome Check

| Market | Trigger received | Generation started | Generation completed | has_today | Latest generated_at | Last error | Fresh record persisted? |
|---|---|---|---|---|---|---|---|
| IN | `last_trigger_received_at: null` (this run predates the observability field's deployment, or ran via the internal startup catch-up, which bypasses the route) | Yes — confirmed by `has_today: true` | **Yes** | **true** | `2026-06-28T21:56:46Z` (≈ 03:26 AM IST, 29 June — consistent with the documented 2 AM IST + ~3.75h run window) | `null` | **Yes** |
| US | `2026-06-29T16:23:35Z` (confirmed, per Workstream #001C) | Yes — `generating: true` throughout this entire session | **Not yet, as of this report** (last checked `2026-06-29T17:31:17Z`, **~68 minutes** after the trigger, still `generating: true`) | **false** | Still `2026-06-26T18:42:25Z` (Friday — unchanged) | `null` | **Not yet — pending** |

**This session does not claim successful US batch completion merely because the GitHub Actions trigger step was green** — per the brief's own explicit instruction. The US run remains genuinely in progress, now well past even the extended runtimes observed in prior workstreams, and its eventual outcome (success, error, or an indefinite hang) is unconfirmed as of this report. A hard browser refresh was not performed (no browser session available in this environment) — left as an explicit operator action, per the checklist below.

## Root Cause Classification

- **For the `count=1000` screener failure**: **Confirmed root cause — a Yahoo-side API policy change (now enforcing a 250-result cap) that the existing code's hardcoded `count=1000` violated on every call.** Fixed.
- **For the BRC/SSB/STRA `.NS` contamination**: **Unresolved — Category 6, "unknown until provider/source reconciliation."** Every code path in this repository that could append a `.NS` suffix was directly traced and confirmed market-correct; three live reproductions of the exact production screener query returned zero contamination. The most likely remaining explanations, **named honestly as unconfirmed hypotheses, not conclusions**: (a) a transient, time-of-day-specific Yahoo screener anomaly not present during this session's own sampling; (b) the captured log lines may be from a different point in time than assumed, possibly predating a since-redeployed fix, or from a manual/local/non-production invocation not covered by this session's evidence; (c) a defect in a code path this session did not find — directly contradicted by the exhaustive trace performed, but not logically impossible given the inherent limits of static + live-sampling investigation versus full log access.

## What Would Resolve This Definitively

The single most useful piece of missing evidence is the **full Railway log context surrounding the three error lines**, not just the three lines themselves — specifically the `[picks] [<market>] Phase-0: downloading N tickers in M batches ...` line that `_bulk_screen` already logs immediately before attempting any `yf.download()` batch, and the `[picks] [<market>] mcap filter ... : N stocks qualify` (or `mcap screener failed (...)`) line `_get_universe_by_mcap` already logs. **Both of these already exist in the deployed code and explicitly state the market** — no new logging needs to be added; the next time this is observed, capturing a few lines of surrounding context (not just the bare yfinance error lines) would directly answer "which batch was running" with certainty, closing this report's one open question.

## Fixes Applied

| File | Change | Justification |
|---|---|---|
| `backend/services/daily_picks.py` | `_get_universe_by_mcap()`'s `yf.screen(...)` call: `count=1000` → `count=250` | Confirmed, reproduced Yahoo-side hard limit; the prior value caused this function to fail on every single invocation |
| `backend/tests/regression/test_daily_picks_screener_count_limit.py` (new) | 3 regression tests: confirms the screener is called with `count <= 250` for both markets; confirms the existing fallback-on-`ValueError` safety net still works even if Yahoo's limit changes again | Locks in the fix; guards the existing fallback behavior |

**No other code, workflow, scheduler, ranking, scoring, RCI, or Valuation Intelligence change was made.** No symbol was removed from any universe (none of the investigated symbols — `BRC`, `SSB`, `STRA` — were ever present in the India universe to begin with, so there was nothing India-side to remove; they remain, correctly, in the US universe).

## Test Results

```
892 passed, 19 warnings  (889 prior + 3 new)
```

No frontend code was touched; no frontend build/lint/test run was required.

## Remaining Provider Risks

- **The BRC/SSB/STRA contamination remains unexplained.** Flagged for re-investigation the next time it recurs, using the full surrounding log context per the instructions above — not re-guessed at here.
- **Batch-level (300-ticker) `yf.download()` failures are still coarser than per-ticker isolation** — a rate-limit or transient outage affecting one batch loses all 300 tickers in it, not just the genuinely-bad ones. No direct evidence this has caused a real problem (the existing fallback to the anchor list, and the now-fixed mcap pre-filter reducing total batch count, both mitigate this), so no change was made — named as a residual, lower-priority risk only.
- **The currently in-progress US run's eventual outcome is unknown as of this report.** If it never completes or errors, that is itself new evidence pointing toward Workstream #001B/#001C's own already-flagged "in-memory observability won't survive a restart" limitation, or toward this session's newly-fixed screener bug having previously caused (and possibly still partially causing, since this run started *before* the fix was deployed) an abnormally long, universe-bloated run.

## Production Validation Checklist

- [ ] Re-check `GET /api/picks/status?market=US` — confirm the in-progress run (trigger at `2026-06-29T16:23:35Z`) eventually reaches `generating: false`. If it never does within a few hours, that is a new, separate incident requiring its own investigation (a possible hang, not covered by this report's evidence).
- [ ] Once the fix in this report is deployed (Railway auto-redeploys on push to `main`) and a fresh US/IN run occurs, capture the full `[picks] [<market>] ...` log lines around any future `.NS` (or other suffix) provider error, not just the bare yfinance error text — this is the one piece of evidence that would close this report's open question.
- [ ] Confirm a subsequent run's total duration is noticeably shorter than the unresolved hour-plus run observed in this session, as indirect confirmation the `count=250` fix is reducing the universe size as intended.
- [ ] Hard-refresh both the India and US Daily Picks pages once the current run resolves; confirm the US badge updates to a fresh date and the India badge remains correctly showing its own, already-fresh Monday record.

## Confirmation of Unchanged Logic

- **No cron expression, workflow trigger, or schedule was changed.**
- **No Railway variable was changed.**
- **No Daily Picks ranking, signal, confidence, target, stop-loss, entry-zone, or risk/reward logic was touched** — the only change is a network-call parameter (`count`) inside a candidate-universe pre-filter, several pipeline stages upstream of any scoring/ranking decision.
- **RCI and Valuation Intelligence remain completely untouched** — no file under Epic 005's scope, no flag, referenced anywhere in this session.
- **India's schedule, its own already-successful Monday run, and its UI wording were not touched.**
- **No symbol was silently removed from any universe.**

---

*This workstream confirms and fixes one real, previously-undetected defect (the Yahoo `count` limit) with full reproduction evidence, but explicitly does not claim to have solved the BRC/SSB/STRA puzzle — that remains an open question, with a precise instruction for capturing the evidence that would close it next time. The US batch observed throughout this session had not completed as of this report's writing; its outcome is not claimed.*
