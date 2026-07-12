"""
Learning Alpha Engine remediation, Phase 2A — shadow-only canonical
cross-sectional alpha observation store.

This is a NEW, additive, standalone table (`alpha_observations`), separate
from the existing `predictions`/`outcomes` tables in store.py /
postgres_store.py. Nothing in production reads from it yet:
get_training_data(), ic_engine, and meta_model are all untouched and keep
reading `predictions`/`outcomes` exactly as before. This module is a
write-only shadow-telemetry path for this phase.

Mirrors alpha_engine/store.py's USE_POSTGRES delegation pattern: Postgres in
production (services.postgres_store), SQLite locally/in tests, so both
storage contracts are supported without either resembling the existing
predictions table's schema.
"""

import os
import sqlite3
import threading

DB_PATH = os.path.join(os.path.dirname(__file__), "../../../alpha_engine.db")
_lock = threading.Lock()

# Bumped only when the row shape or the meaning of an existing column
# changes — lets a future training query pin a known-consistent schema
# version rather than assuming every row means the same thing.
FEATURE_SCHEMA_VERSION = 1
REGIME_SCHEMA_VERSION = 1

USE_POSTGRES = os.getenv("USE_POSTGRES") == "1"
if USE_POSTGRES:
    from services import postgres_store as _pg


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init_schema() -> None:
    """Idempotent — safe to call on every process start."""
    if USE_POSTGRES:
        _pg.init_alpha_observations_schema()
        return
    with _lock, _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS alpha_observations (
            observation_id         TEXT PRIMARY KEY,
            run_id                 TEXT NOT NULL,
            market                 TEXT NOT NULL,
            horizon                TEXT NOT NULL,
            symbol                 TEXT NOT NULL,
            run_generated_at       TEXT NOT NULL,
            run_session_date       TEXT NOT NULL,
            reference_session_date TEXT NOT NULL,
            reference_price        REAL NOT NULL,
            reference_price_source TEXT NOT NULL,
            reference_price_basis  TEXT NOT NULL,
            technical_raw_score    REAL,
            fundamental_raw_score  REAL,
            sentiment_raw_score    REAL,
            quality_raw_score      REAL,
            sentiment_available    INTEGER NOT NULL,
            quality_available      INTEGER NOT NULL,
            technical_zscore       REAL NOT NULL,
            fundamental_zscore     REAL NOT NULL,
            sentiment_zscore       REAL NOT NULL,
            quality_zscore         REAL NOT NULL,
            composite_score        REAL,
            ic_combined_alpha      REAL NOT NULL,
            meta_alpha             REAL,
            ranking_alpha          REAL NOT NULL,
            signal                 TEXT,
            signal_confidence      REAL,
            canonical_regime_id    INTEGER,
            canonical_regime_label TEXT,
            feature_schema_version INTEGER NOT NULL,
            regime_schema_version  INTEGER NOT NULL,
            is_daily_pick          INTEGER NOT NULL DEFAULT 0,
            pick_rank              INTEGER,
            portfolio_weight       REAL,
            created_at             TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (run_id, market, horizon, symbol)
        );
        CREATE INDEX IF NOT EXISTS idx_alpha_observations_market_horizon_session
            ON alpha_observations(market, horizon, reference_session_date);
        CREATE INDEX IF NOT EXISTS idx_alpha_observations_run
            ON alpha_observations(run_id);
        CREATE INDEX IF NOT EXISTS idx_alpha_observations_symbol_market_horizon
            ON alpha_observations(symbol, market, horizon);
        """)


def save_observations(rows: list[dict]) -> bool:
    """
    Bulk-persist one canonical alpha_observations row per dict in `rows`.

    Idempotent for retries of the same run_id (INSERT OR IGNORE / ON
    CONFLICT DO NOTHING on (run_id, market, horizon, symbol)) — a genuinely
    new run_id always inserts fresh rows.

    Returns True on success, False on any failure. Never raises — this is
    shadow telemetry only in this phase; a failure here must not corrupt or
    interrupt the Daily Picks job lifecycle or its already-computed payload.
    """
    if not rows:
        return True
    if USE_POSTGRES:
        return _pg.save_alpha_observations(rows)
    try:
        init_schema()
        with _lock, _conn() as c:
            c.executemany("""
                INSERT OR IGNORE INTO alpha_observations (
                    observation_id, run_id, market, horizon, symbol,
                    run_generated_at, run_session_date, reference_session_date,
                    reference_price, reference_price_source, reference_price_basis,
                    technical_raw_score, fundamental_raw_score,
                    sentiment_raw_score, quality_raw_score,
                    sentiment_available, quality_available,
                    technical_zscore, fundamental_zscore,
                    sentiment_zscore, quality_zscore,
                    composite_score, ic_combined_alpha, meta_alpha, ranking_alpha,
                    signal, signal_confidence,
                    canonical_regime_id, canonical_regime_label,
                    feature_schema_version, regime_schema_version,
                    is_daily_pick, pick_rank, portfolio_weight
                ) VALUES (
                    :observation_id, :run_id, :market, :horizon, :symbol,
                    :run_generated_at, :run_session_date, :reference_session_date,
                    :reference_price, :reference_price_source, :reference_price_basis,
                    :technical_raw_score, :fundamental_raw_score,
                    :sentiment_raw_score, :quality_raw_score,
                    :sentiment_available, :quality_available,
                    :technical_zscore, :fundamental_zscore,
                    :sentiment_zscore, :quality_zscore,
                    :composite_score, :ic_combined_alpha, :meta_alpha, :ranking_alpha,
                    :signal, :signal_confidence,
                    :canonical_regime_id, :canonical_regime_label,
                    :feature_schema_version, :regime_schema_version,
                    :is_daily_pick, :pick_rank, :portfolio_weight
                )
            """, [
                {**r,
                 "sentiment_available": int(bool(r["sentiment_available"])),
                 "quality_available": int(bool(r["quality_available"])),
                 "is_daily_pick": int(bool(r["is_daily_pick"]))}
                for r in rows
            ])
        return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            f"[alpha_observations] save_observations failed ({len(rows)} rows): {e}"
        )
        return False
