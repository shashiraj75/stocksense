# Product Integrity Workstream #025 — Alpha Observations Write Path Bug

**Status:** Implemented and tested. Push/deploy is subject to the pre-push production safety gate documented in this same turn's final report.

## 1. Trigger

User asked, in a follow-up to a conceptual question about how the platform's self-learning system works, to investigate why the `alpha_observations` table — the canonical clean dataset meant to eventually let production learning safely re-enable — had zero rows in production despite the write code appearing correctly wired.

## 2. Investigation and root cause

Traced the full write path: `daily_picks.py`'s per-horizon loop unconditionally builds and attempts to persist one `alpha_observations` row per scored candidate after every Daily Picks run, calling `postgres_store.save_alpha_observations()`. That function called `conn.executemany(...)` directly on a psycopg3 `Connection` object.

**`psycopg3`'s `Connection` class has no `executemany` method — only `Cursor` does.** (`Connection` does have a convenience `execute()`, added in psycopg 3.1, but never gained `executemany()`.) Every single call raised `AttributeError: 'Connection' object has no attribute 'executemany'`.

Confirmed directly, not assumed: reproduced the exact error against the real production database driver, then confirmed `save_alpha_observations()` correctly catches it (by design — this is shadow telemetry, explicitly required to "never interrupt or corrupt the Daily Picks job lifecycle"), logs it as a non-fatal warning, and returns `False`. Because the caller only logs a second warning on `False` and never surfaces it anywhere else, this failure has been invisible — this table has had zero rows since the feature was written, despite running (and failing) on every single Daily Picks generation for both markets, every day.

**Why the existing test suite never caught this**: `test_alpha_observations.py`'s persistence tests all explicitly force the SQLite fallback path (`monkeypatch.setattr(ao, "USE_POSTGRES", False)`) — the real Postgres branch had zero test coverage. Separately confirmed this codebase's usual `MagicMock()`-based mocking pattern for `_get_pool()` would not have caught it either: `MagicMock` fabricates any attribute on request, including a fake `.executemany` that doesn't exist on the real driver — a false green.

**Scope check, confirmed not just theorized**: `git grep`'d every `executemany` call site in the backend. Two other matches exist (`validation_engine.py`'s Postgres path already correctly uses `cur.executemany` on a real cursor; its SQLite path correctly uses `conn.executemany` on a real `sqlite3.Connection`, which genuinely does have that method) — both legitimate, not bugs. Also confirmed no other cursor-only method (`fetchone`/`fetchall`/`fetchmany`) is called directly on a `conn` anywhere in `postgres_store.py`. This is an isolated, single-site bug.

## 3. Fix

`postgres_store.py`'s `save_alpha_observations()`: `with _get_pool().connection() as conn: conn.executemany(...)` → `with _get_pool().connection() as conn, conn.cursor() as cur: cur.executemany(...)`. One call site, minimal diff.

## 4. Blast-radius verification — why this cannot affect any other feature

The user explicitly asked for assurance nothing else would be negatively affected. Verified, not assumed:

- **Nothing in production reads from `alpha_observations` yet** — confirmed via `git grep` across the entire backend for any `SELECT ... FROM alpha_observations`; the only matches are in this table's own test file. `alpha_observations.py`'s own module docstring states this explicitly ("nothing in production reads from it yet"). This fix can therefore only ever start populating a table that currently has zero downstream consumers — it cannot change any ranking, confidence, target price, or any other value a user currently sees.
- **The write path is fully isolated and fail-soft by design**: `save_alpha_observations()`'s own contract (unchanged by this fix) is "never interrupt or corrupt the Daily Picks job lifecycle" — it's wrapped in a try/except at both this layer and the caller's, and its return value only controls a log message, never any branching logic in `generate_picks()`.
- **Full backend suite run after the fix**: 2203/2203 passed (2200 baseline + 3 new) — zero regressions anywhere else in the codebase.
- **The regression test itself directly proves both directions**: reverted the fix locally, confirmed the new test fails with the exact same `AttributeError` message observed against the real production database; restored the fix, confirmed it passes again.

## 5. What this does not do

- Does not re-enable production learning (`LEARNING_ALPHA_PRODUCTION_ENABLED`) — that remains a separate, deliberate policy decision pending a clean canonical dataset and walk-forward validation, per `containment.py`'s own documented criteria. This fix only makes the data-collection groundwork for that eventual decision actually work; it does not itself authorize anything.
- Does not backfill any of the historical rows that were silently lost since this feature was written — there's no way to reconstruct that data after the fact; collection starts fresh from the next successful write.
- Does not touch `factor_ic_history` (also currently empty) — investigated separately and found to be dead code (`log_factor_ic()` has zero call sites anywhere in the backend), a different, unrelated gap not in scope for this fix.
- Does not change ranking, confidence, target prices, portfolio weights, or any other Daily Picks output — confirmed via the blast-radius check above.

## 6. Tests

- New `test_postgres_store_save_alpha_observations.py` — 3 tests using a deliberately strict fake (`_FakePsycopg3Connection`/`_FakePsycopg3Cursor`) that mimics psycopg3's real, narrower attribute surface instead of `MagicMock`'s permissive one: a successful write goes through the cursor and returns `True`; an empty rows list short-circuits without even requesting a connection; a direct reproduction of the original bug's exact failure mode (`AttributeError` on `.executemany`) is caught and degrades to `False`, never raises.
- Full backend suite: **2203/2203 passed** (2200 baseline + 3 new).
- No frontend changes this release.

## 7. Natural-run verification plan

The next Daily Picks generation for either market is the first opportunity to confirm real rows land. Verify via a direct read-only query: `SELECT COUNT(*) FROM alpha_observations` should be non-zero after that run, and `SELECT DISTINCT market, horizon FROM alpha_observations` should show entries for whichever market/horizons that run covered.

## 8. Rollback

Single-file, single-call-site change — reverting restores the exact prior (silently-failing) behavior. No schema, migration, or API contract change; the table itself already existed and was already idle.
