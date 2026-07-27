"""
Product Integrity #016 — score_snapshots market scoping.

Root cause: score_snapshots had no market column, so a symbol string
existing in both IN and US universes shared one row per (symbol, horizon,
snapshot_date). A direct production audit found 225 such symbols (e.g.
AAPL, ADBE, ABT) — not hypothetical, confirmed via the predictions table.
Since writes used ON CONFLICT DO UPDATE keyed only on (symbol, horizon,
snapshot_date), the second market to write on a given day silently
overwrote the first.

This adds a nullable market column (no backfill guess for pre-migration
rows — which market they belong to is genuinely unrecoverable for the
symbols that collided) and market-scopes both the write path
(log_score_snapshot) and both read paths (get_score_history,
get_latest_signals_batch), matching market IS NULL so pre-migration rows
for symbols that never collided keep working unchanged.
"""
from unittest.mock import MagicMock

import pytest

import services.postgres_store as pg


def _mock_pool_returning(rows):
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = rows
    conn_ctx = MagicMock()
    conn_ctx.__enter__.return_value = conn
    conn_ctx.__exit__.return_value = False
    pool = MagicMock()
    pool.connection.return_value = conn_ctx
    return pool


# ─── schema ──────────────────────────────────────────────────────────────

def test_market_column_added_nullable_no_default():
    assert "ALTER TABLE score_snapshots ADD COLUMN IF NOT EXISTS market TEXT;" in pg.SCHEMA_SQL


def test_old_unique_constraint_dropped_by_name():
    """DROP-then-ADD, not IF NOT EXISTS alone — IF NOT EXISTS is a pure
    name check in Postgres and would silently no-op against an
    already-existing, wrongly-shaped constraint (the #009/#010 pitfall
    this project already hit once with the Multibagger schema).

    Real PostgreSQL Verification, CI Execution phase: this pair of
    statements moved out of the bare SCHEMA_SQL string into
    _migrate_score_snapshots_market_constraint(), guarded by an
    information_schema existence check — Postgres has no
    `ADD CONSTRAINT IF NOT EXISTS`, so the unguarded version failed with
    "already exists" on every init_db() call after the first, aborting the
    rest of that single-statement SCHEMA_SQL execute() silently. Same
    DROP-then-ADD text, same behavior, now actually idempotent."""
    import inspect
    src = inspect.getsource(pg._migrate_score_snapshots_market_constraint)
    assert (
        "ALTER TABLE score_snapshots DROP CONSTRAINT IF EXISTS "
        "score_snapshots_symbol_horizon_snapshot_date_key"
    ) in src


def test_new_unique_constraint_includes_market():
    import inspect
    src = inspect.getsource(pg._migrate_score_snapshots_market_constraint)
    assert "ADD CONSTRAINT score_snapshots_symbol_market_horizon_snapshot_date_key" in src
    assert "UNIQUE (symbol, market, horizon, snapshot_date)" in src


def test_old_index_dropped_and_new_one_includes_market():
    assert "DROP INDEX IF EXISTS idx_score_snapshots_symbol;" in pg.SCHEMA_SQL
    assert (
        "CREATE INDEX IF NOT EXISTS idx_score_snapshots_symbol_market "
        "ON score_snapshots(symbol, market, horizon, snapshot_date);"
    ) in pg.SCHEMA_SQL


# ─── log_score_snapshot (write path) ────────────────────────────────────

def test_log_score_snapshot_requires_market_positionally():
    """No default value — a caller that doesn't know its market must not
    be able to silently guess one, since a wrong guess is exactly how the
    pre-#016 collisions happened."""
    import inspect
    sig = inspect.signature(pg.log_score_snapshot)
    params = list(sig.parameters.values())
    assert params[2].name == "market"
    assert params[2].default is inspect._empty


def test_log_score_snapshot_includes_market_in_insert_and_conflict_target(monkeypatch):
    captured = {}

    def _fake_pool():
        pool = _mock_pool_returning([])
        real_conn = pool.connection.return_value.__enter__.return_value

        def _execute(sql, params):
            captured["sql"] = sql
            captured["params"] = params
        real_conn.execute = _execute
        return pool

    monkeypatch.setattr(pg, "_get_pool", _fake_pool)
    pg.log_score_snapshot(
        snapshot_date="2026-07-15", symbol="AAPL", market="US", horizon="medium",
        composite_score=70.0,
    )
    assert "ON CONFLICT (symbol, market, horizon, snapshot_date)" in captured["sql"]
    assert captured["params"][0] == "2026-07-15"
    assert captured["params"][1] == "AAPL"
    assert captured["params"][2] == "US"
    assert captured["params"][3] == "medium"


# ─── get_score_history (History tab chart) ──────────────────────────────

def test_get_score_history_requires_market_positionally():
    import inspect
    sig = inspect.signature(pg.get_score_history)
    params = list(sig.parameters.values())
    assert params[1].name == "market"
    assert params[1].default is inspect._empty


def test_get_score_history_filters_by_market_or_null(monkeypatch):
    captured = {}

    def _fake_pool():
        pool = _mock_pool_returning([])
        real_conn = pool.connection.return_value.__enter__.return_value
        fetch_result = real_conn.execute.return_value

        def _execute(sql, params):
            captured["sql"] = sql
            captured["params"] = params
            return fetch_result
        real_conn.execute = _execute
        return pool

    monkeypatch.setattr(pg, "_get_pool", _fake_pool)
    pg.get_score_history("AAPL", "US", "medium", 90)
    assert "(market = %s OR market IS NULL)" in captured["sql"]
    assert captured["params"] == ("AAPL", "medium", "US", "90")


# ─── get_latest_signals_batch (Portfolio Signal column) ─────────────────

def test_get_latest_signals_batch_requires_market_positionally():
    import inspect
    sig = inspect.signature(pg.get_latest_signals_batch)
    params = list(sig.parameters.values())
    assert params[1].name == "market"
    assert params[1].default is inspect._empty


def test_get_latest_signals_batch_filters_by_market_or_null(monkeypatch):
    captured = {}

    def _fake_pool():
        pool = _mock_pool_returning([])
        real_conn = pool.connection.return_value.__enter__.return_value
        fetch_result = real_conn.execute.return_value

        def _execute(sql, params):
            captured["sql"] = sql
            captured["params"] = params
            return fetch_result
        real_conn.execute = _execute
        return pool

    monkeypatch.setattr(pg, "_get_pool", _fake_pool)
    pg.get_latest_signals_batch(["AAPL"], "US", "medium")
    assert "(market = %s OR market IS NULL)" in captured["sql"]
    assert captured["params"] == (["AAPL"], "medium", "US")


# ─── caller wiring ───────────────────────────────────────────────────────

def test_daily_picks_write_path_passes_market_through():
    import inspect
    src = inspect.getsource(__import__("services.daily_picks", fromlist=["_write_score_snapshots"])._write_score_snapshots)
    assert "market=market," in src


@pytest.mark.regression
def test_score_history_endpoint_accepts_market_query_param():
    import inspect
    from api.routers import stocks
    src = inspect.getsource(stocks.get_score_history)
    assert 'market: Literal["IN", "US"] = Query("IN")' in src
    assert "_gsh, symbol.upper(), market, horizon, days" in src
