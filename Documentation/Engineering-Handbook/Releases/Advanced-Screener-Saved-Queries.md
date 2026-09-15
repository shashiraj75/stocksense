# Advanced Screener + Saved Queries

**Status:** Implemented.

## 1. Trigger

A competitive review of a webinar/course landing page ("Tradewise"/Superinvesting.ai) surfaced a genuine feature idea worth evaluating: multi-criteria stock screening with saved/preset queries. Investigating what StockSense360 actually had first (rather than assuming) found the "Stock Screener" page was, in reality, only a Top Movers list — no adjustable filter criteria existed anywhere in the product, confirmed by direct code search of both the frontend page and the backend routers.

## 2. Investigation — two real findings, not one

**Finding 1 — a `/filter` endpoint already existed, but was never wired to any UI and was architecturally fragile.** `api/routers/screener.py` had a `GET /filter` route with `min_market_cap`/`max_pe`/`min_roe`/`sector`/`signal` params, but:
- It was never called from any frontend page — confirmed by a direct grep across `frontend/src`.
- Its implementation looped a live `yf.Ticker(...).info` call per symbol across the *entire* market universe on every single request, with a 60s timeout — slow, uncached, and put real per-request load on yfinance for something that could instead reuse infrastructure already proven elsewhere in this codebase.
- Its `signal` parameter was accepted but never actually applied to any filter condition — a dead parameter.

**Finding 2 — the fast, already-proven infrastructure to do this properly already existed.** `stock_fundamentals_cache` (weekly-refreshed, screener.in for India / yfinance-derived for US — the same table Sprint #014's Daily Picks universe stratification and Multibagger's fixed `/screen` endpoint both already use) has rich, pre-computed columns: sector, market cap, P/E, ROE, ROCE, debt/equity, promoter holding/pledge, 3Y/5Y sales and profit growth, OPM, interest coverage, EV/EBITDA, and the Business Quality Engine's own score/grade — all filterable instantly via SQL, no live scraping required.

## 3. Implementation

### Backend

- **`services/fundamentals_cache.py`** — new `query_filtered()`: builds a parameterized, whitelisted-column WHERE clause against `stock_fundamentals_cache` (never string-interpolating caller input — `sector` is the only free-text filter and is always bound as an ILIKE parameter), selecting the correct market-cap column (`market_cap_cr` for India, `market_cap_usd_m` for US) per market, ordered by Business Quality score, capped at 200 results.
- **`api/routers/screener.py`**:
  - `/filter` rewritten to call `query_filtered()` instead of the old live-scraping loop — instant, and fixes the dead `signal` param by removing it (it never did anything; no caller relied on it, confirmed by the same grep that found the endpoint was unused).
  - New saved-screens CRUD (`GET/POST /saved/{user_id}`, `DELETE /saved/{user_id}/{screen_id}`) — Postgres-primary with a JSON-file fallback, deliberately mirroring `api/routers/watchlist.py`'s exact existing shape (same `require_owner` ownership dependency, same rate limiting, same fallback pattern) rather than inventing a new persistence convention.
- **`services/postgres_store.py`** — new `saved_screens` table (`user_id`, `name`, `market`, `filters JSONB`, `created_at`), directly alongside `watchlist`'s own schema block.
- **`services/screener_service.py`** — removed the now-fully-superseded `filter_stocks()` method (confirmed dead after the rewrite; the `signal`-accepting-but-unused param it also had is gone with it) and its now-unused `Optional` import.

### Frontend (`app/screener/page.tsx`)

- A `Top Movers` / `Advanced Filter` toggle — Top Movers behavior is completely unchanged.
- Advanced Filter: a 9-field form (sector, market cap, P/E, ROE, ROCE, debt/equity, 3Y sales growth, 3Y profit growth, Business Quality score) → `Apply Filters` runs the query; results show sector, P/E, ROE, and Business Quality score/grade per stock, each still wrapped in the existing `StockContextMenu` (add to watchlist, etc.).
- **Save this search** (signed-in users only — an explicit "Sign in to save this search" note otherwise) — names and persists the current filter set; saved screens render as chips above the form, click to reload+re-run, trash icon to delete.

## 4. Tests

New `tests/regression/test_screener_filter_and_saved_screens.py` (backend, 12 tests):
- 4 tests directly verifying `query_filtered()`'s SQL-building logic: the correct market-cap column per market, `sector` always bound as a parameter (proven with a SQL-injection-shaped string that must never appear in the SQL text itself, only as a parameter value), only-provided-filters produce clauses, multiple criteria combine with AND.
- 8 tests covering saved-screens ownership enforcement, mirroring `test_paper_trading_authorization.py`'s own JWT + `_conn`-mocking pattern (missing token rejected, cross-user access forbidden, an owner can read/write their own data) — including a real, previously-undiagnosed gotcha found and worked around during test-writing: `_USE_PG` is a module-level constant read once at import time (the same established pattern `watchlist.py` already has), so `patch.dict("os.environ", ...)` alone doesn't route a test through the Postgres path — the module attribute itself must be patched.

Sanity-checked per SES-003 §4: a check was deliberately removed from `query_filtered` (the sector-binding clause), confirmed the corresponding test failed with a clear assertion, then the file was restored byte-identical and the suite re-confirmed green.

**Live verification, not just unit tests**: ran both frontend and backend dev servers locally and drove the actual UI — confirmed Top Movers is unaffected, confirmed the filter form submits and the correct request reaches the backend, and confirmed the error-message path end-to-end (a real `KeyError: DATABASE_URL` from the local dev environment — expected, since this machine has no local Postgres configured — was correctly caught, converted to a safe `"Screener data is temporarily unavailable."` message via the existing `safe_error_message` pattern, and rendered correctly by the new frontend code). Diagnosed and ruled out a false lead in the process: the browser initially showed a generic "No stocks match" for what looked like the same failure — traced to a CORS mismatch from testing on a non-default local port (3006, chosen only because port 3000 was occupied by an unrelated project on this machine), confirmed by temporarily and non-persistently adding that port to `ALLOWED_ORIGINS`, re-verifying the correct error message renders, then reverting that change before committing (confirmed via `git diff` showing no residual change to `api/main.py`).

72/72 targeted backend tests, 878/878 full frontend suite, clean `tsc --noEmit`.

## 5. What this does not do

- Does not touch Daily Picks, Multibagger's fixed screens, or Business Quality/Financial Strength/Growth/Valuation engine internals — this is a read-only consumer of the same already-populated cache table those features already write to.
- Does not add filtering by any live/real-time signal (BUY/SELL) — the removed `signal` param was already dead code, not a feature being cut; a future addition could join against Daily Picks' own cached signal if wanted, not attempted here.
- Does not implement the natural-language/chat-assistant idea from the same competitive review — that is being scoped separately, as its own Design Study, given its materially larger scope (new AI integration, cost controls, a different design-review cycle).

## 6. Rollback

Two independent pieces: the backend filter rewrite (revert `query_filtered`/the `/filter` route to restore the prior live-scraping behavior — not recommended, since it was confirmed unused and fragile) and the saved-screens feature (the `saved_screens` table can simply go unused; nothing else reads from or depends on it). Neither touches any other feature's data or behavior.
