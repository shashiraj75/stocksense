"""
Snapshot persistence for the Market Leadership and Trend Context Layer.

Dual Postgres (production) / SQLite (local dev) — mirrors the proven
pattern in validation_engine.py (idempotent CREATE TABLE IF NOT EXISTS,
DROP-then-ADD for constraint changes, per-process init guard). NOT wired
into production `postgres_store.init_db()` startup — this module creates
its own tables lazily, only when actually called, and only matters once
MARKET_LEADERSHIP_SHADOW_ENABLED is on.

Uniqueness: a (market, symbol|group, as_of_session, universe_version,
methodology_version) row can only ever be inserted once while COMPLETE.
PARTIAL snapshots are upserted freely but are NEVER returned by read paths
— a partial calculation cannot publish as complete.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading

_USE_POSTGRES = bool(os.getenv("DATABASE_URL") and os.getenv("USE_POSTGRES", "0") == "1")
_DB_PATH = os.path.join(os.path.dirname(__file__), "../../../market_leadership.db")
_db_lock = threading.Lock()
_db_initialised = False

_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS ml_rs_snapshots (
    id SERIAL PRIMARY KEY,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    as_of_session DATE NOT NULL,
    universe_id TEXT NOT NULL,
    universe_version TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PARTIAL',
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ml_rs_snapshots
    ON ml_rs_snapshots (market, symbol, as_of_session, universe_version, methodology_version);

CREATE TABLE IF NOT EXISTS ml_group_snapshots (
    id SERIAL PRIMARY KEY,
    market TEXT NOT NULL,
    group_name TEXT NOT NULL,
    group_kind TEXT NOT NULL,
    as_of_session DATE NOT NULL,
    universe_id TEXT NOT NULL,
    universe_version TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PARTIAL',
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ml_group_snapshots
    ON ml_group_snapshots (market, group_name, group_kind, as_of_session, universe_version, methodology_version);

CREATE TABLE IF NOT EXISTS ml_breadth_snapshots (
    id SERIAL PRIMARY KEY,
    market TEXT NOT NULL,
    scope TEXT NOT NULL,
    as_of_session DATE NOT NULL,
    universe_id TEXT NOT NULL,
    universe_version TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PARTIAL',
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ml_breadth_snapshots
    ON ml_breadth_snapshots (market, scope, as_of_session, universe_version, methodology_version);
"""

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS ml_rs_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    as_of_session TEXT NOT NULL,
    universe_id TEXT NOT NULL,
    universe_version TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PARTIAL',
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(market, symbol, as_of_session, universe_version, methodology_version)
);
CREATE TABLE IF NOT EXISTS ml_group_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    group_name TEXT NOT NULL,
    group_kind TEXT NOT NULL,
    as_of_session TEXT NOT NULL,
    universe_id TEXT NOT NULL,
    universe_version TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PARTIAL',
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(market, group_name, group_kind, as_of_session, universe_version, methodology_version)
);
CREATE TABLE IF NOT EXISTS ml_breadth_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    scope TEXT NOT NULL,
    as_of_session TEXT NOT NULL,
    universe_id TEXT NOT NULL,
    universe_version TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PARTIAL',
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(market, scope, as_of_session, universe_version, methodology_version)
);
"""


def _pg_conn():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None)


def _sqlite_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_leadership_db() -> None:
    """Idempotent, per-process-guarded. Never called from application
    startup in this release — invoked lazily by orchestration.py only when
    MARKET_LEADERSHIP_SHADOW_ENABLED is on, and directly by tests."""
    global _db_initialised
    if _db_initialised:
        return
    with _db_lock:
        if _db_initialised:
            return
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(_PG_SCHEMA)
            finally:
                conn.close()
        else:
            with _sqlite_conn() as conn:
                conn.executescript(_SQLITE_SCHEMA)
        _db_initialised = True


def _upsert(table: str, unique_cols: list[str], row: dict, status: str) -> None:
    """Insert-or-update while PARTIAL; a COMPLETE row is immutable (insert
    is skipped, not overwritten, if one already exists for the same key)."""
    init_leadership_db()
    cols = list(row.keys()) + ["status"]
    values = list(row.values()) + [status]
    with _db_lock:
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                placeholders = ", ".join(["%s"] * len(cols))
                col_list = ", ".join(cols)
                update_cols = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c != "status")
                sql = (
                    f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
                    f"ON CONFLICT ({', '.join(unique_cols)}) DO UPDATE SET {update_cols}, status=EXCLUDED.status "
                    f"WHERE {table}.status = 'PARTIAL'"
                )
                with conn.cursor() as cur:
                    cur.execute(sql, values)
            finally:
                conn.close()
        else:
            with _sqlite_conn() as conn:
                existing = conn.execute(
                    f"SELECT status FROM {table} WHERE " +
                    " AND ".join(f"{c}=?" for c in unique_cols),
                    [row[c] for c in unique_cols],
                ).fetchone()
                if existing is None:
                    placeholders = ", ".join(["?"] * len(cols))
                    conn.execute(
                        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
                        values,
                    )
                elif existing["status"] == "PARTIAL":
                    set_clause = ", ".join(f"{c}=?" for c in cols)
                    conn.execute(
                        f"UPDATE {table} SET {set_clause} WHERE " +
                        " AND ".join(f"{c}=?" for c in unique_cols),
                        values + [row[c] for c in unique_cols],
                    )
                # else: COMPLETE row exists — immutable, silently no-op.


def save_rs_snapshot(market: str, symbol: str, as_of_session: str, universe_id: str,
                     universe_version: str, methodology_version: str,
                     payload: dict, status: str = "COMPLETE") -> None:
    row = {
        "market": market, "symbol": symbol, "as_of_session": as_of_session,
        "universe_id": universe_id, "universe_version": universe_version,
        "methodology_version": methodology_version,
        "payload": json.dumps(payload) if not _USE_POSTGRES else payload,
    }
    if _USE_POSTGRES:
        import psycopg.types.json as pgjson
        row["payload"] = pgjson.Json(payload)
    _upsert(
        "ml_rs_snapshots",
        ["market", "symbol", "as_of_session", "universe_version", "methodology_version"],
        row, status,
    )


def save_group_snapshot(market: str, group_name: str, group_kind: str, as_of_session: str,
                        universe_id: str, universe_version: str, methodology_version: str,
                        payload: dict, status: str = "COMPLETE") -> None:
    row = {
        "market": market, "group_name": group_name, "group_kind": group_kind,
        "as_of_session": as_of_session, "universe_id": universe_id,
        "universe_version": universe_version, "methodology_version": methodology_version,
        "payload": json.dumps(payload) if not _USE_POSTGRES else payload,
    }
    if _USE_POSTGRES:
        import psycopg.types.json as pgjson
        row["payload"] = pgjson.Json(payload)
    _upsert(
        "ml_group_snapshots",
        ["market", "group_name", "group_kind", "as_of_session", "universe_version", "methodology_version"],
        row, status,
    )


def save_breadth_snapshot(market: str, scope: str, as_of_session: str, universe_id: str,
                          universe_version: str, methodology_version: str,
                          payload: dict, status: str = "COMPLETE") -> None:
    row = {
        "market": market, "scope": scope, "as_of_session": as_of_session,
        "universe_id": universe_id, "universe_version": universe_version,
        "methodology_version": methodology_version,
        "payload": json.dumps(payload) if not _USE_POSTGRES else payload,
    }
    if _USE_POSTGRES:
        import psycopg.types.json as pgjson
        row["payload"] = pgjson.Json(payload)
    _upsert(
        "ml_breadth_snapshots",
        ["market", "scope", "as_of_session", "universe_version", "methodology_version"],
        row, status,
    )


def get_latest_rs_snapshot(market: str, symbol: str, universe_id: str) -> dict | None:
    """Only ever returns a COMPLETE snapshot — a partial calculation must
    never be mistaken for a published result."""
    init_leadership_db()
    with _db_lock:
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT payload FROM ml_rs_snapshots WHERE market=%s AND symbol=%s "
                        "AND universe_id=%s AND status='COMPLETE' ORDER BY as_of_session DESC LIMIT 1",
                        (market, symbol, universe_id),
                    )
                    row = cur.fetchone()
                    return row[0] if row else None
            finally:
                conn.close()
        else:
            with _sqlite_conn() as conn:
                row = conn.execute(
                    "SELECT payload FROM ml_rs_snapshots WHERE market=? AND symbol=? "
                    "AND universe_id=? AND status='COMPLETE' ORDER BY as_of_session DESC LIMIT 1",
                    (market, symbol, universe_id),
                ).fetchone()
                return json.loads(row["payload"]) if row else None
