"""
Postgres (Supabase) persistence layer for the Learning Alpha Engine.

Mirrors services/alpha_engine/store.py's function names 1:1 so call sites
(daily_picks.py, ic_engine.py, outcome_logger.py, weight_adapter.py) need no
changes — alpha_engine/store.py delegates here when USE_POSTGRES=1.

Why this exists: Render's free tier wipes local disk (SQLite alpha_engine.db,
picks_cache.json) on every restart/sleep, so the SQLite-based "learning" never
accumulated state across restarts. Moving to Supabase Postgres (already
provisioned for auth) fixes that.

Connect via the Supabase connection *pooler* (port 6543, sslmode=require) —
Render's frequent cold-starts make pooled/short-lived connections safer than
the direct Postgres port.
"""

import os
import json
from datetime import datetime, timezone

import psycopg
from psycopg_pool import ConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL", "")

_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not set — cannot use postgres_store")
        # autocommit=True — without it, psycopg_pool rolls back any uncommitted
        # transaction when a connection is returned to the pool, silently
        # discarding every CREATE TABLE / INSERT we never explicitly committed.
        _pool = ConnectionPool(
            DATABASE_URL, min_size=1, max_size=5, open=True,
            kwargs={"autocommit": True},
        )
    return _pool


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS predictions (
    id               BIGSERIAL PRIMARY KEY,
    logged_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol           TEXT NOT NULL,
    horizon          TEXT NOT NULL,
    tech_z           DOUBLE PRECISION,
    fund_z           DOUBLE PRECISION,
    sentiment_z      DOUBLE PRECISION,
    quality_z        DOUBLE PRECISION,
    combined_alpha   DOUBLE PRECISION,
    meta_alpha       DOUBLE PRECISION,
    signal           TEXT,
    price            DOUBLE PRECISION,
    regime_label     TEXT,
    contrib_technical    DOUBLE PRECISION,
    contrib_fundamental  DOUBLE PRECISION,
    contrib_sentiment    DOUBLE PRECISION,
    contrib_quality      DOUBLE PRECISION,
    contrib_analyst      DOUBLE PRECISION,
    contrib_week52       DOUBLE PRECISION,
    contrib_regime       DOUBLE PRECISION,
    contrib_global_macro DOUBLE PRECISION,
    contrib_risk_penalty DOUBLE PRECISION,
    contrib_clamp_adj    DOUBLE PRECISION,
    composite_score      DOUBLE PRECISION,
    confidence_score      DOUBLE PRECISION,
    confidence_band        TEXT,
    confidence_components  JSONB,
    bull_case        JSONB,
    bear_case        JSONB,
    is_daily_pick     BOOLEAN NOT NULL DEFAULT FALSE,
    pick_rank         SMALLINT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_predictions_symbol_date ON predictions(symbol, logged_at);
CREATE INDEX IF NOT EXISTS idx_predictions_horizon     ON predictions(horizon, logged_at);
CREATE INDEX IF NOT EXISTS idx_predictions_daily_picks ON predictions(is_daily_pick, horizon, logged_at) WHERE is_daily_pick;

CREATE TABLE IF NOT EXISTS outcomes (
    id           BIGSERIAL PRIMARY KEY,
    resolved_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol       TEXT NOT NULL,
    horizon      TEXT NOT NULL,
    pred_date    DATE NOT NULL,
    return_1d    DOUBLE PRECISION,
    return_5d    DOUBLE PRECISION,
    return_20d   DOUBLE PRECISION,
    return_60d   DOUBLE PRECISION,
    benchmark_return_5d  DOUBLE PRECISION,
    benchmark_return_20d DOUBLE PRECISION,
    benchmark_return_60d DOUBLE PRECISION,
    UNIQUE (symbol, horizon, pred_date)
);
CREATE INDEX IF NOT EXISTS idx_outcomes_lookup ON outcomes(symbol, pred_date, horizon);

CREATE TABLE IF NOT EXISTS regime_log (
    id         BIGSERIAL PRIMARY KEY,
    logged_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    regime_id  SMALLINT,
    label      TEXT,
    features   JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS score_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    snapshot_date   DATE NOT NULL,
    symbol          TEXT NOT NULL,
    horizon         TEXT NOT NULL,
    composite_score DOUBLE PRECISION NOT NULL,
    signal          TEXT,
    quality_score   DOUBLE PRECISION,
    growth_score    DOUBLE PRECISION,
    valuation_score DOUBLE PRECISION,
    technical_score DOUBLE PRECISION,
    sentiment_score DOUBLE PRECISION,
    risk_score      DOUBLE PRECISION,
    confidence_score DOUBLE PRECISION,
    factor_breakdown JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (symbol, horizon, snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_score_snapshots_symbol ON score_snapshots(symbol, horizon, snapshot_date);

CREATE TABLE IF NOT EXISTS daily_picks_cache (
    id           BIGSERIAL PRIMARY KEY,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload      JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_daily_picks_cache_date ON daily_picks_cache(generated_at DESC);

CREATE TABLE IF NOT EXISTS factor_ic_history (
    id            BIGSERIAL PRIMARY KEY,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    horizon       TEXT NOT NULL,
    factor        TEXT NOT NULL,
    window_days   SMALLINT NOT NULL,
    ic_value      DOUBLE PRECISION,
    sample_size   INTEGER,
    is_live       BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_factor_ic_lookup ON factor_ic_history(horizon, factor, window_days, computed_at DESC);

CREATE TABLE IF NOT EXISTS paper_portfolio (
    session_id   TEXT PRIMARY KEY,
    cash         DOUBLE PRECISION NOT NULL DEFAULT 1000000.0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id           BIGSERIAL PRIMARY KEY,
    session_id   TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    market       TEXT NOT NULL,
    quantity     INTEGER NOT NULL,
    entry_price  DOUBLE PRECISION NOT NULL,
    exit_price   DOUBLE PRECISION,
    stop_loss    DOUBLE PRECISION,
    status       TEXT NOT NULL DEFAULT 'OPEN',
    signal       TEXT,
    horizon      TEXT,
    opened_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at    TIMESTAMPTZ
);
-- Add columns to existing tables (safe to run multiple times)
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS stop_loss DOUBLE PRECISION;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS target_price DOUBLE PRECISION;
CREATE INDEX IF NOT EXISTS idx_paper_trades_session ON paper_trades(session_id, status);
-- Migrate paper trading from session-based to user-based (Supabase user_id)
ALTER TABLE paper_portfolio ADD COLUMN IF NOT EXISTS user_id TEXT UNIQUE;
ALTER TABLE paper_trades    ADD COLUMN IF NOT EXISTS user_id TEXT;
CREATE INDEX IF NOT EXISTS idx_paper_trades_user ON paper_trades(user_id, status);
CREATE INDEX IF NOT EXISTS idx_paper_portfolio_user ON paper_portfolio(user_id);
-- Target/stop-loss proximity email notifications
ALTER TABLE paper_portfolio ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE paper_trades    ADD COLUMN IF NOT EXISTS target_notified_at TIMESTAMPTZ;
ALTER TABLE paper_trades    ADD COLUMN IF NOT EXISTS stop_notified_at   TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS watchlist (
    id         BIGSERIAL PRIMARY KEY,
    user_id    TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    market     TEXT NOT NULL,
    notes      TEXT NOT NULL DEFAULT '',
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, symbol, market)
);
CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_id);

CREATE TABLE IF NOT EXISTS terms_acceptance (
    id            BIGSERIAL PRIMARY KEY,
    user_id       TEXT NOT NULL,
    email         TEXT NOT NULL,
    first_name    TEXT,
    last_name     TEXT,
    mobile        TEXT,
    country       TEXT,
    accepted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip_address    TEXT,
    terms_version TEXT NOT NULL DEFAULT 'v1.0',
    UNIQUE (user_id, terms_version)
);
CREATE INDEX IF NOT EXISTS idx_terms_acceptance_user ON terms_acceptance(user_id);
-- Add columns if table already exists (safe on re-run)
ALTER TABLE terms_acceptance ADD COLUMN IF NOT EXISTS first_name TEXT;
ALTER TABLE terms_acceptance ADD COLUMN IF NOT EXISTS last_name  TEXT;
ALTER TABLE terms_acceptance ADD COLUMN IF NOT EXISTS mobile     TEXT;
ALTER TABLE terms_acceptance ADD COLUMN IF NOT EXISTS country    TEXT;

-- Persistent market data cache — survives server restarts on Render free tier.
-- Stores last-known-good movers and heatmap data so users never see blank pages.
CREATE TABLE IF NOT EXISTS market_cache (
    cache_key   TEXT PRIMARY KEY,
    data        JSONB NOT NULL,
    saved_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Signal feedback: thumbs up/down on BUY/HOLD/SELL signals
CREATE TABLE IF NOT EXISTS signal_feedback (
    id           BIGSERIAL PRIMARY KEY,
    user_id      TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    market       TEXT NOT NULL,
    horizon      TEXT NOT NULL,
    signal       TEXT NOT NULL,
    vote         SMALLINT NOT NULL CHECK (vote IN (1, -1)),
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, symbol, market, horizon)
);
CREATE INDEX IF NOT EXISTS idx_signal_feedback_user   ON signal_feedback(user_id);
CREATE INDEX IF NOT EXISTS idx_signal_feedback_symbol ON signal_feedback(symbol, market, horizon);

-- NPS survey responses: monthly 0-10 score + optional comment
CREATE TABLE IF NOT EXISTS nps_responses (
    id           BIGSERIAL PRIMARY KEY,
    user_id      TEXT NOT NULL,
    score        SMALLINT NOT NULL CHECK (score BETWEEN 0 AND 10),
    comment      TEXT,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_nps_user ON nps_responses(user_id, submitted_at);
"""


def save_market_cache(key: str, data: dict | list) -> None:
    """Persist last-known-good market data to Postgres so it survives server restarts."""
    try:
        with _get_pool().connection() as conn:
            conn.execute(
                """INSERT INTO market_cache (cache_key, data, saved_at)
                   VALUES (%s, %s::jsonb, now())
                   ON CONFLICT (cache_key) DO UPDATE
                       SET data = EXCLUDED.data, saved_at = EXCLUDED.saved_at""",
                (key, json.dumps(data))
            )
    except Exception:
        pass  # never block the main request path


def load_market_cache(key: str) -> dict | list | None:
    """Load last-known-good market data from Postgres."""
    try:
        with _get_pool().connection() as conn:
            row = conn.execute(
                "SELECT data FROM market_cache WHERE cache_key = %s", (key,)
            ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def init_db():
    with _get_pool().connection() as conn:
        conn.execute(SCHEMA_SQL)


def log_prediction(symbol: str, horizon: str, factor_zscores: dict,
                   combined_alpha: float, meta_alpha: float | None,
                   signal: str, price: float, regime_label: str = "",
                   contributions: dict | None = None,
                   composite_score: float | None = None,
                   confidence_score: float | None = None,
                   confidence_band: str | None = None,
                   confidence_components: dict | None = None,
                   bull_case: list | None = None,
                   bear_case: list | None = None,
                   is_daily_pick: bool = False,
                   pick_rank: int | None = None):
    contributions = contributions or {}
    with _get_pool().connection() as conn:
        conn.execute("""
            INSERT INTO predictions
              (logged_at, symbol, horizon, tech_z, fund_z, sentiment_z, quality_z,
               combined_alpha, meta_alpha, signal, price, regime_label,
               contrib_technical, contrib_fundamental, contrib_sentiment, contrib_quality,
               contrib_analyst, contrib_week52, contrib_regime, contrib_global_macro,
               contrib_risk_penalty, contrib_clamp_adj, composite_score,
               confidence_score, confidence_band, confidence_components,
               bull_case, bear_case, is_daily_pick, pick_rank)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s)
        """, (
            datetime.now(timezone.utc), symbol, horizon,
            factor_zscores.get("tech"), factor_zscores.get("fund"),
            factor_zscores.get("sentiment"), factor_zscores.get("quality"),
            combined_alpha, meta_alpha, signal, price, regime_label,
            contributions.get("technical"), contributions.get("fundamental"),
            contributions.get("sentiment"), contributions.get("quality"),
            contributions.get("analyst"), contributions.get("week52"),
            contributions.get("regime"), contributions.get("global_macro"),
            contributions.get("risk_penalty"), contributions.get("clamp_adjustment"),
            composite_score,
            confidence_score, confidence_band,
            json.dumps(confidence_components) if confidence_components else None,
            json.dumps(bull_case) if bull_case is not None else None,
            json.dumps(bear_case) if bear_case is not None else None,
            is_daily_pick, pick_rank,
        ))


def log_outcome(symbol: str, horizon: str, pred_date: str,
                return_1d: float | None, return_5d: float | None,
                return_20d: float | None, return_60d: float | None = None,
                benchmark_return_5d: float | None = None,
                benchmark_return_20d: float | None = None,
                benchmark_return_60d: float | None = None):
    with _get_pool().connection() as conn:
        conn.execute("""
            INSERT INTO outcomes (resolved_at, symbol, horizon, pred_date,
                                  return_1d, return_5d, return_20d, return_60d,
                                  benchmark_return_5d, benchmark_return_20d, benchmark_return_60d)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, horizon, pred_date) DO NOTHING
        """, (
            datetime.now(timezone.utc), symbol, horizon, pred_date,
            return_1d, return_5d, return_20d, return_60d,
            benchmark_return_5d, benchmark_return_20d, benchmark_return_60d,
        ))


def log_regime(regime_id: int, label: str, features: list[float]):
    with _get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO regime_log (logged_at, regime_id, label, features) VALUES (%s, %s, %s, %s)",
            (datetime.now(timezone.utc), regime_id, label, json.dumps(features)),
        )


def get_training_data(horizon: str, window_days: int | None = None) -> list[dict]:
    """
    Join predictions with outcomes to get labelled training rows.
    Forward return column selected by horizon.
    window_days: if set, only include predictions logged within the last N days
                 (used by the multi-window IC engine).
    """
    fwd_col = {"short": "return_5d", "medium": "return_20d", "long": "return_60d"}[horizon]
    window_clause = "AND p.logged_at >= now() - interval '%s days'" % window_days if window_days else ""
    with _get_pool().connection() as conn:
        rows = conn.execute(f"""
            SELECT p.tech_z, p.fund_z, p.sentiment_z, p.quality_z,
                   p.combined_alpha, p.meta_alpha, p.signal, p.regime_label,
                   o.{fwd_col} AS fwd_return
            FROM predictions p
            JOIN outcomes o
              ON p.symbol = o.symbol
             AND p.horizon = o.horizon
             AND date(p.logged_at) = o.pred_date
            WHERE p.horizon = %s
              AND o.{fwd_col} IS NOT NULL
              {window_clause}
        """, (horizon,)).fetchall()
    cols = ["tech_z", "fund_z", "sentiment_z", "quality_z", "combined_alpha",
            "meta_alpha", "signal", "regime_label", "fwd_return"]
    return [dict(zip(cols, r)) for r in rows]


def get_unresolved_predictions(horizon: str, min_days_old: int) -> list[dict]:
    with _get_pool().connection() as conn:
        rows = conn.execute("""
            SELECT p.symbol, p.horizon, date(p.logged_at) AS pred_date, p.price
            FROM predictions p
            WHERE p.horizon = %s
              AND now() - p.logged_at >= (%s || ' days')::interval
              AND NOT EXISTS (
                  SELECT 1 FROM outcomes o
                  WHERE o.symbol = p.symbol
                    AND o.horizon = p.horizon
                    AND o.pred_date = date(p.logged_at)
              )
            GROUP BY p.symbol, p.horizon, date(p.logged_at), p.price
        """, (horizon, str(min_days_old))).fetchall()
    cols = ["symbol", "horizon", "pred_date", "price"]
    return [dict(zip(cols, r)) for r in rows]


def get_regime_history() -> list[list[float]]:
    with _get_pool().connection() as conn:
        rows = conn.execute("SELECT features FROM regime_log").fetchall()
    result = []
    for (features,) in rows:
        try:
            result.append(features if isinstance(features, list) else json.loads(features))
        except Exception:
            pass
    return result


def count_training_rows(horizon: str) -> int:
    return len(get_training_data(horizon))


# ── New: Score history (section 4) ──────────────────────────────────────────

def log_score_snapshot(snapshot_date: str, symbol: str, horizon: str,
                        composite_score: float, signal: str | None = None,
                        quality_score: float | None = None,
                        growth_score: float | None = None,
                        valuation_score: float | None = None,
                        technical_score: float | None = None,
                        sentiment_score: float | None = None,
                        risk_score: float | None = None,
                        confidence_score: float | None = None,
                        factor_breakdown: dict | None = None):
    with _get_pool().connection() as conn:
        conn.execute("""
            INSERT INTO score_snapshots
              (snapshot_date, symbol, horizon, composite_score, signal,
               quality_score, growth_score, valuation_score, technical_score,
               sentiment_score, risk_score, confidence_score, factor_breakdown)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, horizon, snapshot_date) DO UPDATE SET
              composite_score = EXCLUDED.composite_score,
              signal = EXCLUDED.signal,
              quality_score = EXCLUDED.quality_score,
              growth_score = EXCLUDED.growth_score,
              valuation_score = EXCLUDED.valuation_score,
              technical_score = EXCLUDED.technical_score,
              sentiment_score = EXCLUDED.sentiment_score,
              risk_score = EXCLUDED.risk_score,
              confidence_score = EXCLUDED.confidence_score,
              factor_breakdown = EXCLUDED.factor_breakdown
        """, (
            snapshot_date, symbol, horizon, composite_score, signal,
            quality_score, growth_score, valuation_score, technical_score,
            sentiment_score, risk_score, confidence_score,
            json.dumps(factor_breakdown) if factor_breakdown else None,
        ))


def get_score_history(symbol: str, horizon: str, days: int = 90) -> list[dict]:
    with _get_pool().connection() as conn:
        rows = conn.execute("""
            SELECT snapshot_date, composite_score, signal, quality_score, growth_score,
                   valuation_score, technical_score, sentiment_score, risk_score, confidence_score
            FROM score_snapshots
            WHERE symbol = %s AND horizon = %s
              AND snapshot_date >= now() - (%s || ' days')::interval
            ORDER BY snapshot_date ASC
        """, (symbol, horizon, str(days))).fetchall()
    cols = ["date", "composite_score", "signal", "quality_score", "growth_score",
            "valuation_score", "technical_score", "sentiment_score", "risk_score", "confidence_score"]
    return [dict(zip(cols, r)) for r in rows]


# ── New: Factor IC history (section 6) ──────────────────────────────────────

def log_factor_ic(horizon: str, factor: str, window_days: int,
                   ic_value: float | None, sample_size: int, is_live: bool):
    with _get_pool().connection() as conn:
        conn.execute("""
            INSERT INTO factor_ic_history (horizon, factor, window_days, ic_value, sample_size, is_live)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (horizon, factor, window_days, ic_value, sample_size, is_live))


def get_factor_ic_history(horizon: str) -> list[dict]:
    """Latest IC value per (factor, window_days) for the given horizon."""
    with _get_pool().connection() as conn:
        rows = conn.execute("""
            SELECT DISTINCT ON (factor, window_days)
                   factor, window_days, ic_value, sample_size, is_live, computed_at
            FROM factor_ic_history
            WHERE horizon = %s
            ORDER BY factor, window_days, computed_at DESC
        """, (horizon,)).fetchall()
    cols = ["factor", "window_days", "ic_value", "sample_size", "is_live", "computed_at"]
    return [dict(zip(cols, r)) for r in rows]


# ── New: Daily picks performance (section 5) ────────────────────────────────

def save_picks_to_db(payload: dict) -> None:
    """Persist the full picks payload to Postgres so it survives Render redeploys."""
    with _get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO daily_picks_cache (generated_at, payload) VALUES (%s, %s)",
            (datetime.now(timezone.utc), json.dumps(payload)),
        )
        # Keep only last 10 rows to avoid bloat
        conn.execute("""
            DELETE FROM daily_picks_cache
            WHERE id NOT IN (
                SELECT id FROM daily_picks_cache ORDER BY generated_at DESC LIMIT 10
            )
        """)


def load_picks_from_db() -> dict | None:
    """Load the most recently generated picks from Postgres."""
    try:
        with _get_pool().connection() as conn:
            row = conn.execute(
                "SELECT payload FROM daily_picks_cache ORDER BY generated_at DESC LIMIT 1"
            ).fetchone()
        if row:
            payload = row[0]
            return payload if isinstance(payload, dict) else json.loads(payload)
    except Exception as e:
        print(f"[postgres_store] load_picks_from_db error: {e}")
    return None


def get_daily_picks_performance(horizon: str, window_days: int = 90) -> list[dict]:
    """All daily-pick predictions in the window, joined with outcomes if resolved."""
    with _get_pool().connection() as conn:
        rows = conn.execute("""
            SELECT p.symbol, date(p.logged_at) AS pred_date, p.price AS entry_price,
                   p.composite_score, p.confidence_score,
                   o.return_5d, o.return_20d, o.return_60d,
                   o.benchmark_return_5d, o.benchmark_return_20d, o.benchmark_return_60d
            FROM predictions p
            LEFT JOIN outcomes o
              ON p.symbol = o.symbol AND p.horizon = o.horizon AND o.pred_date = date(p.logged_at)
            WHERE p.horizon = %s AND p.is_daily_pick = TRUE
              AND p.logged_at >= now() - (%s || ' days')::interval
            ORDER BY p.logged_at DESC
        """, (horizon, str(window_days))).fetchall()
    cols = ["symbol", "date", "entry_price", "score", "confidence",
            "return_5d", "return_20d", "return_60d",
            "benchmark_return_5d", "benchmark_return_20d", "benchmark_return_60d"]
    return [dict(zip(cols, r)) for r in rows]
