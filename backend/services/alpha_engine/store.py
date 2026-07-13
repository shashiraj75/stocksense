"""
SQLite persistence layer for the Learning Alpha Engine.

Tables:
  predictions — factor z-scores + signal logged at generation time
  outcomes    — actual forward returns resolved after the holding period
  regime_log  — historical regime snapshots for KMeans retraining
"""

import os
import sqlite3
import threading
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "../../../alpha_engine.db")
_lock = threading.Lock()

USE_POSTGRES = os.getenv("USE_POSTGRES") == "1"
if USE_POSTGRES:
    from services import postgres_store as _pg


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    if USE_POSTGRES:
        return _pg.init_db()
    with _lock, _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS predictions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_at      TEXT    NOT NULL,
            symbol         TEXT    NOT NULL,
            horizon        TEXT    NOT NULL,
            market         TEXT    NOT NULL DEFAULT 'IN',
            tech_z         REAL,
            fund_z         REAL,
            sentiment_z    REAL,
            quality_z      REAL,
            combined_alpha REAL,
            meta_alpha     REAL,
            signal         TEXT,
            price          REAL,
            regime_label   TEXT
        );

        CREATE TABLE IF NOT EXISTS outcomes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            resolved_at  TEXT NOT NULL,
            symbol       TEXT NOT NULL,
            horizon      TEXT NOT NULL,
            market       TEXT NOT NULL DEFAULT 'IN',
            pred_date    TEXT NOT NULL,
            return_1d    REAL,
            return_5d    REAL,
            return_20d   REAL,
            return_60d   REAL
        );

        CREATE TABLE IF NOT EXISTS regime_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_at  TEXT NOT NULL,
            regime_id  INTEGER,
            label      TEXT,
            features   TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_pred_sym_date
            ON predictions(symbol, logged_at);
        CREATE INDEX IF NOT EXISTS idx_pred_horizon
            ON predictions(horizon, logged_at);
        CREATE INDEX IF NOT EXISTS idx_outcome_lookup
            ON outcomes(symbol, pred_date, horizon);
        """)
        # CREATE TABLE IF NOT EXISTS doesn't add columns to a table that
        # already existed before `market` was introduced — guard each ALTER
        # since SQLite errors (not no-ops) on a duplicate column.
        for stmt in (
            "ALTER TABLE predictions ADD COLUMN market TEXT NOT NULL DEFAULT 'IN'",
            "ALTER TABLE outcomes ADD COLUMN market TEXT NOT NULL DEFAULT 'IN'",
        ):
            try:
                c.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists


def log_prediction(symbol: str, horizon: str, factor_zscores: dict,
                   combined_alpha: float, meta_alpha: float | None,
                   signal: str, price: float, regime_label: str = "",
                   market: str = "IN", **kwargs):
    if USE_POSTGRES:
        return _pg.log_prediction(symbol, horizon, factor_zscores, combined_alpha,
                                   meta_alpha, signal, price, regime_label, market=market, **kwargs)
    init_db()
    with _lock, _conn() as c:
        c.execute("""
            INSERT INTO predictions
              (logged_at, symbol, horizon, market, tech_z, fund_z, sentiment_z, quality_z,
               combined_alpha, meta_alpha, signal, price, regime_label)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            symbol, horizon, market,
            factor_zscores.get("tech"),
            factor_zscores.get("fund"),
            factor_zscores.get("sentiment"),
            factor_zscores.get("quality"),
            combined_alpha, meta_alpha, signal, price, regime_label,
        ))


def log_outcome(symbol: str, horizon: str, pred_date: str,
                return_1d: float | None, return_5d: float | None,
                return_20d: float | None, return_60d: float | None = None,
                market: str = "IN", **kwargs):
    """
    Upsert (SELECT-then-INSERT-or-UPDATE, since the SQLite outcomes table has
    no UNIQUE constraint to target with ON CONFLICT). A column that already
    holds a resolved value is preserved via COALESCE(existing, new) — this
    lets a later sweep backfill return_20d/return_60d without disturbing
    return_1d/return_5d a prior sweep already wrote, or losing a value if a
    later sweep can't compute one for a given horizon. Mirrors
    services/postgres_store.py's log_outcome exactly for behavioral parity
    between the SQLite (dev/test) and Postgres (production) backends.
    """
    if USE_POSTGRES:
        return _pg.log_outcome(symbol, horizon, pred_date, return_1d, return_5d, return_20d,
                                return_60d=return_60d, market=market, **kwargs)
    init_db()
    with _lock, _conn() as c:
        existing = c.execute(
            "SELECT id FROM outcomes WHERE symbol=? AND horizon=? AND pred_date=? AND market=?",
            (symbol, horizon, pred_date, market)
        ).fetchone()
        now = datetime.now(timezone.utc).isoformat()
        if existing:
            c.execute("""
                UPDATE outcomes SET
                    resolved_at = ?,
                    return_1d  = COALESCE(return_1d, ?),
                    return_5d  = COALESCE(return_5d, ?),
                    return_20d = COALESCE(return_20d, ?),
                    return_60d = COALESCE(return_60d, ?)
                WHERE id = ?
            """, (now, return_1d, return_5d, return_20d, return_60d, existing["id"]))
            return
        c.execute("""
            INSERT INTO outcomes (resolved_at, symbol, horizon, pred_date, market,
                                  return_1d, return_5d, return_20d, return_60d)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now, symbol, horizon, pred_date, market, return_1d, return_5d, return_20d, return_60d,
        ))


def log_regime(regime_id: int, label: str, features: list[float]):
    if USE_POSTGRES:
        return _pg.log_regime(regime_id, label, features)
    import json
    init_db()
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO regime_log (logged_at, regime_id, label, features) VALUES (?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), regime_id, label, json.dumps(features)),
        )


def get_training_data(horizon: str, market: str = "IN", window_days: int | None = None) -> list[dict]:
    """
    Join predictions with outcomes to get labelled training rows.
    Forward return column selected by horizon. IN and US train separately —
    see services/postgres_store.py's get_training_data for why.
    """
    if USE_POSTGRES:
        return _pg.get_training_data(horizon, market=market, window_days=window_days)
    init_db()
    # long horizon = 60D forward return (≈3 months), matching the stated holding period
    fwd_col = {"short": "return_5d", "medium": "return_20d", "long": "return_60d"}[horizon]
    with _lock, _conn() as c:
        rows = c.execute(f"""
            SELECT p.tech_z, p.fund_z, p.sentiment_z, p.quality_z,
                   p.combined_alpha, p.meta_alpha, p.signal, p.regime_label,
                   o.{fwd_col} AS fwd_return
            FROM predictions p
            JOIN outcomes o
              ON p.symbol = o.symbol
             AND p.horizon = o.horizon
             AND p.market = o.market
             AND date(p.logged_at) = date(o.pred_date)
            WHERE p.horizon = ? AND p.market = ?
              AND o.{fwd_col} IS NOT NULL
        """, (horizon, market)).fetchall()
    return [dict(r) for r in rows]


# Mirrors services/postgres_store.py's _HORIZON_ELIGIBILITY_SQL — a fixed,
# hardcoded allow-list (never built from caller-supplied text) of which
# forward-return columns each horizon's resolver sweep is responsible for.
_HORIZON_ELIGIBILITY_SQL = {
    "short":  "(o.id IS NULL OR o.return_1d IS NULL OR o.return_5d IS NULL)",
    "medium": "(o.id IS NULL OR o.return_5d IS NULL OR o.return_20d IS NULL)",
    "long":   "(o.id IS NULL OR o.return_60d IS NULL)",
}


def get_unresolved_predictions(horizon: str, min_days_old: int, market: str = "IN") -> list[dict]:
    """
    Predictions whose horizon-relevant outcome columns are still incomplete —
    see services/postgres_store.py's get_unresolved_predictions for the full
    rationale (this SQLite path mirrors it for dev/test parity). A prediction
    stays eligible until every column its own horizon cares about is filled;
    it does not stop being offered the instant *any* outcome row exists.
    Ambiguous groups (multiple same-day predictions at different prices for
    the same symbol/horizon/market) are excluded — fail closed.
    """
    if USE_POSTGRES:
        return _pg.get_unresolved_predictions(horizon, min_days_old, market=market)
    if horizon not in _HORIZON_ELIGIBILITY_SQL:
        raise ValueError(f"unknown horizon: {horizon!r}")
    eligibility_clause = _HORIZON_ELIGIBILITY_SQL[horizon]
    init_db()
    with _lock, _conn() as c:
        rows = c.execute(f"""
            SELECT p.symbol, p.horizon, date(p.logged_at) AS pred_date,
                   max(p.price) AS price
            FROM predictions p
            LEFT JOIN outcomes o
              ON o.symbol = p.symbol AND o.horizon = p.horizon AND o.market = p.market
             AND o.pred_date = date(p.logged_at)
            WHERE p.horizon = ? AND p.market = ?
              AND julianday('now') - julianday(p.logged_at) >= ?
              AND {eligibility_clause}
            GROUP BY p.symbol, p.horizon, date(p.logged_at)
            HAVING count(DISTINCT p.price) <= 1
        """, (horizon, market, min_days_old)).fetchall()
    return [dict(r) for r in rows]


def count_existing_outcomes(market: str, horizon: str,
                            start_date: str | None = None, end_date: str | None = None) -> dict:
    """SQLite mirror of postgres_store.count_existing_outcomes — used only by
    the operator backfill tool's dry-run report."""
    if USE_POSTGRES:
        return _pg.count_existing_outcomes(market, horizon, start_date=start_date, end_date=end_date)
    init_db()
    clauses = ["market = ?", "horizon = ?"]
    params: list = [market, horizon]
    if start_date:
        clauses.append("pred_date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("pred_date <= ?")
        params.append(end_date)
    where = " AND ".join(clauses)
    with _lock, _conn() as c:
        row = c.execute(f"""
            SELECT count(*) AS total,
                   sum(CASE WHEN return_5d  IS NULL THEN 1 ELSE 0 END) AS missing_5d,
                   sum(CASE WHEN return_20d IS NULL THEN 1 ELSE 0 END) AS missing_20d,
                   sum(CASE WHEN return_60d IS NULL THEN 1 ELSE 0 END) AS missing_60d
            FROM outcomes WHERE {where}
        """, params).fetchone()
    return {"total": row[0] or 0, "missing_5d": row[1] or 0,
            "missing_20d": row[2] or 0, "missing_60d": row[3] or 0}


def get_ambiguous_pending_predictions(horizon: str, min_days_old: int, market: str = "IN") -> list[dict]:
    """SQLite mirror of postgres_store.get_ambiguous_pending_predictions — used
    only by the operator backfill tool's dry-run report."""
    if USE_POSTGRES:
        return _pg.get_ambiguous_pending_predictions(horizon, min_days_old, market=market)
    if horizon not in _HORIZON_ELIGIBILITY_SQL:
        raise ValueError(f"unknown horizon: {horizon!r}")
    eligibility_clause = _HORIZON_ELIGIBILITY_SQL[horizon]
    init_db()
    with _lock, _conn() as c:
        rows = c.execute(f"""
            SELECT p.symbol, p.horizon, date(p.logged_at) AS pred_date,
                   count(*) AS n_predictions, count(DISTINCT p.price) AS n_distinct_prices
            FROM predictions p
            LEFT JOIN outcomes o
              ON o.symbol = p.symbol AND o.horizon = p.horizon AND o.market = p.market
             AND o.pred_date = date(p.logged_at)
            WHERE p.horizon = ? AND p.market = ?
              AND julianday('now') - julianday(p.logged_at) >= ?
              AND {eligibility_clause}
            GROUP BY p.symbol, p.horizon, date(p.logged_at)
            HAVING count(DISTINCT p.price) > 1
        """, (horizon, market, min_days_old)).fetchall()
    return [dict(r) for r in rows]


def get_regime_history() -> list[list[float]]:
    """Return all stored regime feature vectors for KMeans retraining."""
    if USE_POSTGRES:
        return _pg.get_regime_history()
    import json
    init_db()
    with _lock, _conn() as c:
        rows = c.execute("SELECT features FROM regime_log").fetchall()
    result = []
    for r in rows:
        try:
            result.append(json.loads(r["features"]))
        except Exception:
            pass
    return result


def count_training_rows(horizon: str, market: str = "IN") -> int:
    return len(get_training_data(horizon, market=market))
