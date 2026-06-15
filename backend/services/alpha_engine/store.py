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


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _lock, _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS predictions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_at      TEXT    NOT NULL,
            symbol         TEXT    NOT NULL,
            horizon        TEXT    NOT NULL,
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
            pred_date    TEXT NOT NULL,
            return_1d    REAL,
            return_5d    REAL,
            return_20d   REAL
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


def log_prediction(symbol: str, horizon: str, factor_zscores: dict,
                   combined_alpha: float, meta_alpha: float | None,
                   signal: str, price: float, regime_label: str = ""):
    init_db()
    with _lock, _conn() as c:
        c.execute("""
            INSERT INTO predictions
              (logged_at, symbol, horizon, tech_z, fund_z, sentiment_z, quality_z,
               combined_alpha, meta_alpha, signal, price, regime_label)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            symbol, horizon,
            factor_zscores.get("tech"),
            factor_zscores.get("fund"),
            factor_zscores.get("sentiment"),
            factor_zscores.get("quality"),
            combined_alpha, meta_alpha, signal, price, regime_label,
        ))


def log_outcome(symbol: str, horizon: str, pred_date: str,
                return_1d: float | None, return_5d: float | None,
                return_20d: float | None):
    init_db()
    with _lock, _conn() as c:
        # Avoid duplicate outcomes for the same prediction date
        existing = c.execute(
            "SELECT id FROM outcomes WHERE symbol=? AND horizon=? AND pred_date=?",
            (symbol, horizon, pred_date)
        ).fetchone()
        if existing:
            return
        c.execute("""
            INSERT INTO outcomes (resolved_at, symbol, horizon, pred_date,
                                  return_1d, return_5d, return_20d)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            symbol, horizon, pred_date, return_1d, return_5d, return_20d,
        ))


def log_regime(regime_id: int, label: str, features: list[float]):
    import json
    init_db()
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO regime_log (logged_at, regime_id, label, features) VALUES (?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), regime_id, label, json.dumps(features)),
        )


def get_training_data(horizon: str) -> list[dict]:
    """
    Join predictions with outcomes to get labelled training rows.
    Forward return column selected by horizon.
    """
    init_db()
    fwd_col = {"short": "return_5d", "medium": "return_20d", "long": "return_20d"}[horizon]
    with _lock, _conn() as c:
        rows = c.execute(f"""
            SELECT p.tech_z, p.fund_z, p.sentiment_z, p.quality_z,
                   p.combined_alpha, p.meta_alpha, p.signal, p.regime_label,
                   o.{fwd_col} AS fwd_return
            FROM predictions p
            JOIN outcomes o
              ON p.symbol = o.symbol
             AND p.horizon = o.horizon
             AND date(p.logged_at) = date(o.pred_date)
            WHERE p.horizon = ?
              AND o.{fwd_col} IS NOT NULL
        """, (horizon,)).fetchall()
    return [dict(r) for r in rows]


def get_unresolved_predictions(horizon: str, min_days_old: int) -> list[dict]:
    """
    Fetch predictions logged ≥ min_days_old ago that have no outcome entry yet.
    Used by the outcome logger to know which prices to fetch.
    """
    init_db()
    with _lock, _conn() as c:
        rows = c.execute("""
            SELECT p.symbol, p.horizon, date(p.logged_at) AS pred_date, p.price
            FROM predictions p
            WHERE p.horizon = ?
              AND julianday('now') - julianday(p.logged_at) >= ?
              AND NOT EXISTS (
                  SELECT 1 FROM outcomes o
                  WHERE o.symbol = p.symbol
                    AND o.horizon = p.horizon
                    AND o.pred_date = date(p.logged_at)
              )
            GROUP BY p.symbol, date(p.logged_at)
        """, (horizon, min_days_old)).fetchall()
    return [dict(r) for r in rows]


def get_regime_history() -> list[list[float]]:
    """Return all stored regime feature vectors for KMeans retraining."""
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


def count_training_rows(horizon: str) -> int:
    return len(get_training_data(horizon))
