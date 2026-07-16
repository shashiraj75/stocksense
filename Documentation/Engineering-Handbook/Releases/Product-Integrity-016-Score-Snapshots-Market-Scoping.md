# Product Integrity Workstream #016 — Score Snapshots Market Scoping

**Status:** Deployed to production (2026-07-16, commit `883b304`) — migration confirmed live via direct DB query (market column present, new constraint/index in place, old ones gone).

## 1. Trigger

The last open finding (#15, LOW severity) from the Stock Detail page forensic audit ([Product Integrity #014](Product-Integrity-014-Stock-Detail-Page-Forensic-Audit-HIGH-Severity-Fixes.md)/[#015](Product-Integrity-015-Stock-Detail-Page-Forensic-Audit-MEDIUM-LOW-Severity-Fixes.md)): `score_snapshots` (Postgres) has no `market` column, so a symbol string existing in both India and US universes could show the wrong market's History tab chart. The finding's own source comment claimed this "hasn't been observed in practice" — that claim was checked, not assumed, before starting this migration.

## 2. Production audit finding: the claim was wrong

A direct, read-only production database audit (cross-referencing `score_snapshots`' distinct symbols against the market-tagged `predictions` table) found **225 symbols genuinely existing in both IN and US markets** — including well-known US tickers like AAPL, ADBE, ABT, ABBV, ADP. This is not hypothetical or theoretical; it is confirmed, current production state.

**This raises the severity beyond "wrong display."** `score_snapshots`' unique constraint was `(symbol, horizon, snapshot_date)` — no market — and writes use `ON CONFLICT ... DO UPDATE`. For any of the 225 colliding symbols, if both markets' Daily Picks runs scored that symbol string on the same calendar day, the second market's write would silently overwrite the first's row. This means the bug wasn't only "displays the wrong market's history" — it was potentially **losing one market's score-history data on write**, for as long as this schema has existed. Historical data lost this way cannot be recovered; it was never persisted with enough information to reconstruct which market a given legacy row belongs to.

## 3. What this release does

### 3a. Schema migration (self-applying on backend startup, via `init_db()`)

- `ALTER TABLE score_snapshots ADD COLUMN IF NOT EXISTS market TEXT;` — **nullable, no backfill guess.** Which market each of the 26,068 existing rows (as of this audit) belongs to is genuinely unrecoverable for the ~5% of symbols that collided; guessing a value (e.g. defaulting to `'IN'`) would fabricate history rather than honestly represent it as unknown. Legacy rows stay `market IS NULL`.
- Old unique constraint `(symbol, horizon, snapshot_date)` dropped by name, replaced with `(symbol, market, horizon, snapshot_date)` — via explicit `DROP CONSTRAINT IF EXISTS` / `ADD CONSTRAINT`, not relying on `IF NOT EXISTS` alone (which is a pure name check in Postgres and would have silently no-op'd against the already-existing, wrongly-shaped constraint — the exact #009/#010 pitfall this project already hit once with the Multibagger schema).
- Index rebuilt the same way: old `idx_score_snapshots_symbol` dropped, new `idx_score_snapshots_symbol_market` created including `market`.

### 3b. Write path

`log_score_snapshot()` now requires `market` (no default — a caller that doesn't know its market must not be able to silently guess one). `daily_picks.py`'s `_write_score_snapshots` already receives `market` as a parameter from `generate_picks(market, job_id)`; it just wasn't being threaded through to the persistence call. Now it is.

### 3c. Read paths

- `get_score_history()` (History tab chart) now requires `market`, filtering `WHERE symbol = %s AND horizon = %s AND (market = %s OR market IS NULL)`. The `OR market IS NULL` clause matters: it keeps pre-migration history visible for the ~95% of symbols that never actually collided, rather than making every symbol's history appear to start fresh on the migration date.
- `get_latest_signals_batch()` (Portfolio's Signal-column fallback, a second caller with the identical pre-existing gap, found and fixed in the same pass) — same `market`/`OR market IS NULL` filtering.
- `GET /api/stocks/{symbol}/score-history` gets a new `market` query parameter (default `"IN"`, matching this endpoint's prior unscoped behavior for the vast majority of non-colliding callers).
- `GET /api/predictions/cached-batch`'s `market` (already a request parameter) is now threaded through to `get_latest_signals_batch`.

### 3d. Frontend

`fetchScoreHistory()` takes `market`; the Stock Detail page's `scoreHistory` query key now includes `market`, so switching a symbol's market gets its own cache entry instead of potentially serving a stale cross-market result.

## 4. What this release does not do

- Does not backfill or guess `market` for any existing row. Legacy data for the ~5% of symbols that collided remains genuinely ambiguous — this is disclosed, not hidden.
- Does not attempt to reconstruct which of the potentially-overwritten historical snapshots were lost, or when. That information doesn't exist in the data as persisted.
- Does not change `daily_picks_cache`, `predictions`, or `outcomes` tables — those are already market-scoped (confirmed via the same production audit).

## 5. Tests

- `test_score_snapshots_market_scoping.py` — 12 new tests: schema-SQL assertions (following the same source-inspection convention Product Integrity #010 established for exactly this kind of DROP-then-ADD constraint/index repair), required-parameter signature checks, and mocked-connection behavioral tests confirming the `market`/`OR market IS NULL` filter shape on both read paths and the conflict-target shape on the write path.
- `scoreHistoryMarketScoping.test.ts` — 4 new frontend tests confirming `fetchScoreHistory`'s signature and the query key.
- Two existing test files (`test_postgres_store_latest_signals_batch.py`, `test_predictions_signal_endpoint.py`) updated for the new required `market` parameter.
- Full backend suite: **2169/2169 passed** (2157 baseline + 12 new).
- Full frontend suite: **316/316 passed** (312 baseline + 4 new).
- Typecheck: clean.

## 6. Rollback

Code changes are straightforward to revert (remove the `market` parameter threading). The schema changes (new column, new constraint/index names) are additive/renaming, not destructive — reverting the code does not require reverting the schema; the old code simply wouldn't reference the new column, and the new unique constraint remains a strict superset restriction of the old one (anything that was unique under the old key remains unique under the new one, since market is additional information, not a replacement).

## 7. Post-deploy verification plan

Direct read-only production query to confirm: (a) `score_snapshots.market` column exists, (b) the new constraint/index names exist with the correct definition, (c) the next natural Daily Picks run (both markets) writes rows with `market` populated (not NULL) going forward.
