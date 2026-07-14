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

import logging
import os
import json
from datetime import datetime, timezone

import psycopg
from psycopg_pool import ConnectionPool

from services.market_integrity import MISSING_MARKET, require_explicit_market

log = logging.getLogger(__name__)

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
        #
        # prepare_threshold=None — every other direct psycopg.connect() call
        # site in this codebase (paper_trading.py, alerts.py, portfolio.py,
        # fundamentals_cache.py, validation_engine.py) already sets this; this
        # pool was the one exception. Without it, psycopg auto-prepares a
        # query as a named server-side statement after a few uses — but
        # Supabase's transaction-mode pooler (port 6543) can hand the same
        # underlying server connection to a different logical query between
        # transactions, so a statement name minted for a 1-parameter query can
        # later collide with a 31-parameter one reusing the same pooled
        # connection. Confirmed live in Railway logs: "Failed to log
        # prediction for X: bind message supplies 31 parameters, but prepared
        # statement requires 1" — repeating for nearly every symbol, silently
        # breaking log_prediction (and therefore the IC engine / meta-model
        # training data) regardless of the Render/Railway duplicate-deployment
        # issue resolved earlier — this pool alone reproduces it standalone.
        _pool = ConnectionPool(
            DATABASE_URL, min_size=1, max_size=5, open=True,
            kwargs={"autocommit": True, "prepare_threshold": None},
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
-- The learning store had no market column at all — IN and US predictions
-- were indistinguishable, and outcome_logger hardcoded the NSE ".NS" ticker
-- suffix unconditionally, so US outcomes could never resolve. That meant the
-- IC engine and meta-model were trained only on (the subset of) IN outcomes
-- that resolved, yet that single learned weighting was applied to ranking
-- both markets. Added market so IN and US each learn from their own outcomes.
-- Phase 1A.6: no DEFAULT — this ADD COLUMN only ever fires on a genuinely
-- fresh database (IF NOT EXISTS guards it; an already-migrated production
-- table already has the column and this line is a no-op there). A bare
-- NOT NULL with no default is valid DDL against an empty table. The write
-- boundary (log_prediction) requires an explicit market on every insert —
-- see services/market_integrity.py — so no code path ever needs a
-- database-level default. The live production column still carries its
-- original DEFAULT 'IN' until the separate, manually-run migration in
-- scripts/migrations/phase_1a6_drop_predictions_market_default.sql is
-- applied — this idempotent bootstrap script deliberately never touches
-- an already-existing column's default itself.
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS market TEXT NOT NULL;
CREATE INDEX IF NOT EXISTS idx_predictions_symbol_date ON predictions(symbol, logged_at);
CREATE INDEX IF NOT EXISTS idx_predictions_horizon     ON predictions(horizon, logged_at);
CREATE INDEX IF NOT EXISTS idx_predictions_daily_picks ON predictions(is_daily_pick, horizon, logged_at) WHERE is_daily_pick;
CREATE INDEX IF NOT EXISTS idx_predictions_market       ON predictions(market, horizon, logged_at);

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
-- Phase 1A.6: no DEFAULT — see the identical note on the predictions.market
-- column above. Live production still carries its original default until
-- scripts/migrations/phase_1a6_drop_predictions_market_default.sql is
-- manually applied.
ALTER TABLE outcomes ADD COLUMN IF NOT EXISTS market TEXT NOT NULL;
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
ALTER TABLE daily_picks_cache ADD COLUMN IF NOT EXISTS market TEXT NOT NULL DEFAULT 'IN';
CREATE INDEX IF NOT EXISTS idx_daily_picks_cache_date ON daily_picks_cache(generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_daily_picks_cache_market ON daily_picks_cache(market, generated_at DESC);

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

-- (cash_usd column added below via ALTER for existing deployments)
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
-- Separate USD ledger so IN (₹) and US ($) paper trading never share a cash pool
ALTER TABLE paper_portfolio ADD COLUMN IF NOT EXISTS cash_usd DOUBLE PRECISION NOT NULL DEFAULT 100000.0;
-- One-time backfill: bump untouched $10,000 balances (the brief initial default)
-- up to $100,000. Only touches rows that never bought/sold a US paper trade yet.
UPDATE paper_portfolio SET cash_usd = 100000.0 WHERE cash_usd = 10000.0;
-- Manual vs Auto Close trade management (AI Assisted is UI-only "coming soon"
-- for now — the column accepts the value but no backend logic acts on it yet).
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS trade_management_mode TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS exit_reason TEXT;
-- Learning Alpha Engine remediation, Phase 1 — paper-trade provenance
-- foundation. Purely additive and nullable: every existing row reads back
-- unchanged (all NULL — genuinely unknown provenance, never guessed or
-- backfilled). New trades MAY supply these when opened from a Daily Pick;
-- when they don't, these stay NULL exactly like a legacy row. Nothing in
-- this remediation phase reads or reports on these columns yet — that is
-- deliberately left for a later phase once real data starts accumulating.
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS recommendation_source TEXT;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS daily_pick_run_id TEXT;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS daily_pick_rank SMALLINT;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS recommendation_generated_at TIMESTAMPTZ;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS recommendation_reference_price DOUBLE PRECISION;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS recommendation_entry_low DOUBLE PRECISION;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS recommendation_entry_high DOUBLE PRECISION;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS recommendation_original_stop_loss DOUBLE PRECISION;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS recommendation_original_target DOUBLE PRECISION;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS model_version TEXT;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS execution_slippage_pct DOUBLE PRECISION;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS signal_override BOOLEAN;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS levels_modified_after_entry BOOLEAN;
-- Per-user Paper Trading notification preference — gates both the trade
-- notifier's proximity/auto-close emails and (client-side) whether the
-- Notifications toggle asks for browser permission. Paper Trading only;
-- unrelated to Daily Picks alerts, which have their own separate mechanism.
ALTER TABLE paper_portfolio ADD COLUMN IF NOT EXISTS email_notifications_enabled BOOLEAN NOT NULL DEFAULT true;
-- Epic 007 Phase 3A — Intelligence Engine V1 shadow-run telemetry only.
-- Additive, standalone table: nothing else reads from or writes to this
-- table, and it is never joined against paper_trades/daily-picks tables.
-- Populated only when INTELLIGENCE_ENGINE_SHADOW_ENABLED=1 (default off);
-- has zero rows and zero effect on production until explicitly enabled.
CREATE TABLE IF NOT EXISTS intelligence_engine_shadow_runs (
    id                          BIGSERIAL PRIMARY KEY,
    market                      TEXT NOT NULL,
    run_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    universe_version            TEXT NOT NULL,
    raw_count                   INTEGER NOT NULL,
    evaluated_count             INTEGER NOT NULL,
    passed_count                INTEGER NOT NULL,
    excluded_count              INTEGER NOT NULL,
    excluded_counts_by_reason   JSONB,
    instrument_type_counts      JSONB,
    sample_exclusions           JSONB,
    source_commit               TEXT,
    generation_job_id           TEXT
);
CREATE INDEX IF NOT EXISTS idx_intelligence_engine_shadow_runs_market_time
    ON intelligence_engine_shadow_runs(market, run_at DESC);
-- Epic 007 Phase 3B — Tradability/Liquidity/Confidence telemetry. Purely
-- additive to the Phase 3A table; all nullable, so historical Phase 3A
-- rows (which predate these gates) simply read back as NULL/"not
-- evaluated" rather than needing any backfill.
ALTER TABLE intelligence_engine_shadow_runs ADD COLUMN IF NOT EXISTS tradability_passed INTEGER;
ALTER TABLE intelligence_engine_shadow_runs ADD COLUMN IF NOT EXISTS tradability_failed INTEGER;
ALTER TABLE intelligence_engine_shadow_runs ADD COLUMN IF NOT EXISTS liquidity_passed INTEGER;
ALTER TABLE intelligence_engine_shadow_runs ADD COLUMN IF NOT EXISTS liquidity_failed INTEGER;
ALTER TABLE intelligence_engine_shadow_runs ADD COLUMN IF NOT EXISTS data_confidence_average DOUBLE PRECISION;
ALTER TABLE intelligence_engine_shadow_runs ADD COLUMN IF NOT EXISTS top_failure_reasons JSONB;
ALTER TABLE intelligence_engine_shadow_runs ADD COLUMN IF NOT EXISTS sample_liquidity_rejections JSONB;

-- Learning Alpha Engine remediation, Phase 2A — shadow-only canonical
-- cross-sectional alpha observation store. Additive, standalone table:
-- nothing reads from it in production yet (get_training_data/ic_engine/
-- meta_model are untouched and keep reading `predictions`/`outcomes`
-- exactly as before). One row per successfully scored, non-rejected Daily
-- Picks candidate per run, written once after _zscore_and_rank(), final
-- Top-6 selection, and portfolio optimisation have all completed — so a
-- row's selection-metadata columns are correct at insert time and never
-- need a follow-up UPDATE. See
-- Documentation/Engineering-Handbook (Phase 2A report) for full rationale.
CREATE TABLE IF NOT EXISTS alpha_observations (
    observation_id            UUID PRIMARY KEY,
    run_id                    TEXT NOT NULL,
    market                    TEXT NOT NULL,
    horizon                   TEXT NOT NULL,
    symbol                    TEXT NOT NULL,
    run_generated_at          TIMESTAMPTZ NOT NULL,
    run_session_date          DATE NOT NULL,
    reference_session_date    DATE NOT NULL,
    reference_price           DOUBLE PRECISION NOT NULL,
    reference_price_source    TEXT NOT NULL,
    reference_price_basis     TEXT NOT NULL,
    -- Raw, single-stock factor scores — NEVER a cross-sectional value.
    technical_raw_score       DOUBLE PRECISION,
    fundamental_raw_score     DOUBLE PRECISION,
    sentiment_raw_score       DOUBLE PRECISION,
    quality_raw_score         DOUBLE PRECISION,
    -- Evidence-availability flags, captured at source (Phase 1), never
    -- inferred after the fact from a fallback numeric value.
    sentiment_available       BOOLEAN NOT NULL,
    quality_available         BOOLEAN NOT NULL,
    -- True cross-sectional z-scores from _zscore_and_rank() — never the
    -- raw score divided by 100.
    technical_zscore          DOUBLE PRECISION NOT NULL,
    fundamental_zscore        DOUBLE PRECISION NOT NULL,
    sentiment_zscore          DOUBLE PRECISION NOT NULL,
    quality_zscore            DOUBLE PRECISION NOT NULL,
    composite_score           DOUBLE PRECISION,
    ic_combined_alpha         DOUBLE PRECISION NOT NULL,
    meta_alpha                DOUBLE PRECISION,
    ranking_alpha             DOUBLE PRECISION NOT NULL,
    signal                    TEXT,
    signal_confidence         DOUBLE PRECISION,
    canonical_regime_id       SMALLINT,
    canonical_regime_label    TEXT,
    feature_schema_version    SMALLINT NOT NULL,
    regime_schema_version     SMALLINT NOT NULL,
    is_daily_pick             BOOLEAN NOT NULL DEFAULT FALSE,
    pick_rank                 SMALLINT,
    portfolio_weight          DOUBLE PRECISION,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, market, horizon, symbol)
);
CREATE INDEX IF NOT EXISTS idx_alpha_observations_market_horizon_session
    ON alpha_observations(market, horizon, reference_session_date);
CREATE INDEX IF NOT EXISTS idx_alpha_observations_run
    ON alpha_observations(run_id);
CREATE INDEX IF NOT EXISTS idx_alpha_observations_symbol_market_horizon
    ON alpha_observations(symbol, market, horizon);

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

-- Durable Daily Picks job state — survives Railway restarts and cross-process
-- duplicate-run prevention. The partial unique index on (market) WHERE
-- status IN ('queued','running') is the only cross-process mutual-exclusion
-- gate: an INSERT with ON CONFLICT DO NOTHING atomically returns rowcount=0
-- if another process already holds the active slot for that market.
-- 'interrupted' is a manual-only operator recovery status; no code path
-- writes it automatically. 'slow' and 'unresponsive' are computed
-- presentation states derived from timestamp gaps, never stored here.
CREATE TABLE IF NOT EXISTS daily_picks_jobs (
    id                        BIGSERIAL PRIMARY KEY,
    job_id                    TEXT NOT NULL UNIQUE,
    market                    TEXT NOT NULL CHECK (market IN ('IN', 'US')),
    status                    TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'completed', 'failed', 'interrupted')
    ),
    runner_instance_id        TEXT NOT NULL,
    started_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_runner_heartbeat_at  TIMESTAMPTZ,
    last_progress_at          TIMESTAMPTZ,
    phase                     TEXT,
    processed                 INTEGER,
    total                     INTEGER,
    universe_used             TEXT,
    universe_degraded         BOOLEAN,
    last_error                TEXT,
    completed_at              TIMESTAMPTZ,
    persisted_picks_timestamp TIMESTAMPTZ
);
-- Cross-process exclusion: at most one queued/running row per market.
CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_picks_jobs_one_active_per_market
    ON daily_picks_jobs (market)
    WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS idx_daily_picks_jobs_market_status
    ON daily_picks_jobs (market, status, started_at DESC);

-- Release 12C: additive universe-selection observability columns. Written
-- only at Phase-0b (same as universe_used/universe_degraded above) — never
-- overwritten by later phases, unlike processed/total which are genuinely
-- phase-local and reused across every phase transition.
ALTER TABLE daily_picks_jobs ADD COLUMN IF NOT EXISTS screener_raw_count INTEGER;
ALTER TABLE daily_picks_jobs ADD COLUMN IF NOT EXISTS universe_candidate_count INTEGER;
ALTER TABLE daily_picks_jobs ADD COLUMN IF NOT EXISTS universe_selection_attempts INTEGER;
ALTER TABLE daily_picks_jobs ADD COLUMN IF NOT EXISTS universe_selection_reason TEXT;
ALTER TABLE daily_picks_jobs ADD COLUMN IF NOT EXISTS universe_selection_error_category TEXT;
-- Explicit, unambiguous aliases of processed/total for the CURRENT phase's
-- work units — written on every progress update, same values as
-- processed/total. processed/total are kept unchanged for compatibility;
-- these new columns exist so a consumer never has to guess whether a given
-- "total" describes universe breadth or a phase task count.
ALTER TABLE daily_picks_jobs ADD COLUMN IF NOT EXISTS phase_task_processed INTEGER;
ALTER TABLE daily_picks_jobs ADD COLUMN IF NOT EXISTS phase_task_total INTEGER;

-- Learning Alpha Engine remediation, Phase 1 — Production Containment and
-- Evidence Preservation. Additive, nullable observability columns written
-- once per run (containment state is fixed for the whole run, not
-- per-horizon) via record_daily_picks_job_containment — same best-effort,
-- never-affects-job-lifecycle isolation as the Release 12C columns above.
-- Absent/null on any job row recorded before this release, or on a run
-- where job_id was never provided (e.g. direct/local calls) — never
-- fabricated. shadow_ic_available / shadow_meta_model_available are
-- per-horizon booleans (JSONB) since containment.py's flag is global but
-- shadow-data availability legitimately varies by horizon.
ALTER TABLE daily_picks_jobs ADD COLUMN IF NOT EXISTS production_alpha_source TEXT;
ALTER TABLE daily_picks_jobs ADD COLUMN IF NOT EXISTS shadow_ic_available JSONB;
ALTER TABLE daily_picks_jobs ADD COLUMN IF NOT EXISTS shadow_meta_model_available JSONB;
ALTER TABLE daily_picks_jobs ADD COLUMN IF NOT EXISTS containment_reason TEXT;
ALTER TABLE daily_picks_jobs ADD COLUMN IF NOT EXISTS learning_dataset_version TEXT;

-- Every table above had Row-Level Security disabled, meaning anyone with
-- this project's URL + anon key (normally embedded in frontend JS by
-- design, for Supabase Auth) could read/write/delete all of them directly
-- via the auto-generated PostgREST API, completely bypassing our FastAPI
-- backend's own access control. This connection authenticates as the
-- `postgres` role, which has BYPASSRLS by default in every Supabase
-- project, so enabling RLS here with no policies blocks the public REST
-- API (which connects as `anon`/`authenticated`) while leaving this
-- backend's own direct access completely unaffected. ENABLE is idempotent
-- — safe to run on every startup.
ALTER TABLE predictions          ENABLE ROW LEVEL SECURITY;
ALTER TABLE outcomes             ENABLE ROW LEVEL SECURITY;
ALTER TABLE regime_log           ENABLE ROW LEVEL SECURITY;
ALTER TABLE score_snapshots      ENABLE ROW LEVEL SECURITY;
ALTER TABLE alpha_observations   ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_picks_cache    ENABLE ROW LEVEL SECURITY;
ALTER TABLE factor_ic_history    ENABLE ROW LEVEL SECURITY;
ALTER TABLE paper_portfolio      ENABLE ROW LEVEL SECURITY;
ALTER TABLE paper_trades         ENABLE ROW LEVEL SECURITY;
ALTER TABLE watchlist            ENABLE ROW LEVEL SECURITY;
ALTER TABLE terms_acceptance     ENABLE ROW LEVEL SECURITY;
ALTER TABLE market_cache         ENABLE ROW LEVEL SECURITY;
ALTER TABLE signal_feedback      ENABLE ROW LEVEL SECURITY;
ALTER TABLE nps_responses        ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_picks_jobs     ENABLE ROW LEVEL SECURITY;
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
        _migrate_outcomes_market_constraint(conn)


def _migrate_outcomes_market_constraint(conn) -> None:
    """
    One-time migration, guarded so it only runs once even though init_db()
    runs on every startup (same pattern as other schema changes in this
    file). The original UNIQUE (symbol, horizon, pred_date) constraint
    predates the market column — without widening it, IN and US outcomes
    for the same symbol/horizon/date would collide on ON CONFLICT.
    Postgres doesn't support ADD CONSTRAINT IF NOT EXISTS directly, so this
    checks information_schema first.
    """
    exists = conn.execute("""
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'outcomes' AND constraint_type = 'UNIQUE'
          AND constraint_name = 'outcomes_symbol_horizon_pred_date_market_key'
    """).fetchone()
    if exists:
        return
    conn.execute("ALTER TABLE outcomes DROP CONSTRAINT IF EXISTS outcomes_symbol_horizon_pred_date_key")
    conn.execute(
        "ALTER TABLE outcomes ADD CONSTRAINT outcomes_symbol_horizon_pred_date_market_key "
        "UNIQUE (symbol, horizon, pred_date, market)"
    )


def log_prediction(symbol: str, horizon: str, factor_zscores: dict,
                   combined_alpha: float, meta_alpha: float | None,
                   signal: str, price: float, regime_label: str = "",
                   *, market=MISSING_MARKET,
                   contributions: dict | None = None,
                   composite_score: float | None = None,
                   confidence_score: float | None = None,
                   confidence_band: str | None = None,
                   confidence_components: dict | None = None,
                   bull_case: list | None = None,
                   bear_case: list | None = None,
                   is_daily_pick: bool = False,
                   pick_rank: int | None = None,
                   _writer_source: str = "unknown"):
    """
    market is required and keyword-only, defaulting to the MISSING_MARKET
    sentinel rather than a bare `market: str` with no default (Phase
    1A.6; see market_integrity.py). A required-keyword-only argument with
    no default raises Python's own TypeError for an omitted call *before
    this function body runs at all* — no structured event could ever be
    emitted for that case. The sentinel default makes omission a value
    require_explicit_market can see, log, and reject on its own terms.

    Before commit 536fd3d, one call site silently dropped an already-
    resolved `market` variable and fell through to this function's old
    `market="IN"` default, mislabeling 13,605 US predictions as IN over
    several weeks before the gap was noticed. That call site was fixed, but
    the API itself still allowed the same class of mistake — this closes it
    at the single shared choke point every writer goes through, rather than
    trusting each caller to remember.

    Raises (see services.market_integrity for the full hierarchy):
      MissingMarketContextError — market was never supplied
      InvalidMarketError        — market was supplied but blank/unsupported
      MarketSymbolConflictError — the symbol's canonical classification
                                  definitively contradicts the claimed market
    A BOTH (dual-listed) symbol is valid under either explicit market. An
    UNKNOWN symbol (absent from both canonical universes) is never
    rejected on that basis alone — logged as an observable event and
    persisted as claimed, since many legitimate symbols simply aren't in
    the curated static universe yet. Every check runs, and every
    structured event is emitted, before this function ever touches
    _get_pool().
    """
    result = require_explicit_market(symbol, market, writer_context=_writer_source)
    market = result.market
    contributions = contributions or {}
    with _get_pool().connection() as conn:
        conn.execute("""
            INSERT INTO predictions
              (logged_at, symbol, horizon, market, tech_z, fund_z, sentiment_z, quality_z,
               combined_alpha, meta_alpha, signal, price, regime_label,
               contrib_technical, contrib_fundamental, contrib_sentiment, contrib_quality,
               contrib_analyst, contrib_week52, contrib_regime, contrib_global_macro,
               contrib_risk_penalty, contrib_clamp_adj, composite_score,
               confidence_score, confidence_band, confidence_components,
               bull_case, bear_case, is_daily_pick, pick_rank)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s)
        """, (
            datetime.now(timezone.utc), symbol, horizon, market,
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



# Shared upsert SQL — used by both the single-row autocommit path
# (log_outcome) and the transactional manifest batch writer
# (execute_outcome_writes_transactional), so the write semantics can never
# drift apart between the two call sites.
_OUTCOME_UPSERT_SQL = """
    INSERT INTO outcomes (resolved_at, symbol, horizon, pred_date, market,
                          return_1d, return_5d, return_20d, return_60d,
                          benchmark_return_5d, benchmark_return_20d, benchmark_return_60d)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (symbol, horizon, pred_date, market) DO UPDATE SET
        resolved_at          = EXCLUDED.resolved_at,
        return_1d            = COALESCE(outcomes.return_1d, EXCLUDED.return_1d),
        return_5d            = COALESCE(outcomes.return_5d, EXCLUDED.return_5d),
        return_20d           = COALESCE(outcomes.return_20d, EXCLUDED.return_20d),
        return_60d           = COALESCE(outcomes.return_60d, EXCLUDED.return_60d),
        benchmark_return_5d  = COALESCE(outcomes.benchmark_return_5d, EXCLUDED.benchmark_return_5d),
        benchmark_return_20d = COALESCE(outcomes.benchmark_return_20d, EXCLUDED.benchmark_return_20d),
        benchmark_return_60d = COALESCE(outcomes.benchmark_return_60d, EXCLUDED.benchmark_return_60d)
"""


class OutcomeDriftError(Exception):
    """
    Raised by execute_outcome_writes_transactional when a manifest candidate's
    live outcome state no longer matches what the manifest recorded, detected
    by in-transaction re-validation immediately before that row's write.
    Raising this inside the conn.transaction() block aborts and rolls back
    every write already staged earlier in the same batch — never a partial
    commit.
    """


def log_outcome(symbol: str, horizon: str, pred_date: str,
                return_1d: float | None, return_5d: float | None,
                return_20d: float | None, return_60d: float | None = None,
                benchmark_return_5d: float | None = None,
                benchmark_return_20d: float | None = None,
                benchmark_return_60d: float | None = None,
                *, market=MISSING_MARKET, _writer_source: str = "unknown"):
    """
    Upsert an outcome row. On conflict, each forward-return / benchmark column
    is filled in via COALESCE(existing, new) — a column that already holds a
    resolved value is never overwritten, and a column this call didn't compute
    (passed as None) never clobbers a value a *later* sweep already wrote.
    This makes repeated resolver runs safe to backfill later horizons (e.g.
    return_20d arriving weeks after return_5d) without disturbing what's
    already been resolved. resolved_at is bumped on every touch so it reflects
    the most recent write, whether that's the first partial resolution or a
    later backfill.

    This is the single-row, individually-autocommitted path used by the live
    periodic resolver. For a manifest-locked batch that must commit or roll
    back as one unit, see execute_outcome_writes_transactional.

    market carries the exact same explicit-market contract as log_prediction
    (Phase 1A.6 remediation): required, keyword-only, defaulting to the
    MISSING_MARKET sentinel rather than a bare `market: str = "IN"`. Before
    this remediation, market silently defaulted to "IN" here — the same
    defect class 536fd3d fixed for log_prediction, just on the sibling
    outcomes table. scripts/migrate_sqlite_to_postgres.py's log_outcome call
    was the one caller that actually omitted it; it has been fixed to pass
    the source row's own market explicitly. See services/market_integrity.py
    for the full exception hierarchy. Every check runs, and every structured
    event is emitted, before this function ever touches _get_pool().
    """
    result = require_explicit_market(symbol, market, writer_context=_writer_source)
    market = result.market

    with _get_pool().connection() as conn:
        conn.execute(_OUTCOME_UPSERT_SQL, (
            datetime.now(timezone.utc), symbol, horizon, pred_date, market,
            return_1d, return_5d, return_20d, return_60d,
            benchmark_return_5d, benchmark_return_20d, benchmark_return_60d,
        ))


def execute_outcome_writes_transactional(writes: list[dict]) -> list[dict]:
    """
    Apply a whole manifest-locked batch of outcome writes as ONE database
    transaction (Phase 1A.3). Each write dict must have keys: symbol, horizon,
    market, pred_date, return_1d, return_5d, return_20d, return_60d,
    benchmark_return_5d, benchmark_return_20d, benchmark_return_60d (all
    return_*/benchmark_* optional, None if not computed), and an optional
    expected_existing dict ({"id":..., "return_1d":..., ...}) — the outcome
    state the manifest recorded at generation time, used to detect drift.

    For each write, in order, inside the single transaction:
      1. SELECT ... FOR UPDATE the current outcomes row (if any) — this both
         locks it against a concurrent writer (e.g. the live 6-hourly
         resolver) and gives us its current value to compare.
      2. If expected_existing was recorded and the live row's non-NULL values
         no longer match it (or a row the manifest expected to exist is now
         missing, or a row now exists that the manifest recorded as absent
         while a truly conflicting value is present), raise
         OutcomeDriftError — this is never caught here, so it propagates out
         of the `with conn.transaction():` block, which rolls back every
         write already applied earlier in this same call. No partial batch
         is ever left committed.
      3. Otherwise perform the same COALESCE upsert log_outcome uses.

    Returns the list of {symbol, horizon, market, pred_date, operation} for
    every row written, but only if the entire batch committed successfully —
    if this function raises, nothing in `writes` was persisted.
    """
    results = []
    with _get_pool().connection() as conn:
        with conn.transaction():
            for w in writes:
                current = conn.execute("""
                    SELECT id, return_1d, return_5d, return_20d, return_60d
                    FROM outcomes
                    WHERE symbol=%s AND horizon=%s AND market=%s AND pred_date=%s
                    FOR UPDATE
                """, (w["symbol"], w["horizon"], w["market"], w["pred_date"])).fetchone()

                expected = w.get("expected_existing") or {}
                if current is None:
                    if expected.get("id") is not None:
                        raise OutcomeDriftError(
                            f"{w['symbol']}/{w['pred_date']}: manifest expected an existing "
                            f"outcome row (id={expected.get('id')}) that no longer exists"
                        )
                else:
                    if expected.get("id") is None:
                        # Manifest was generated when no outcome row existed for this key,
                        # but one exists now — most likely the normal 6-hourly resolver
                        # created it in the meantime. Fail closed rather than silently
                        # treating this row as the canary's own INSERT.
                        raise OutcomeDriftError(
                            f"{w['symbol']}/{w['pred_date']}: an outcome row (id={current[0]}) now "
                            f"exists but the manifest recorded none — likely created by the normal "
                            f"resolver since manifest generation; refusing to claim it as this batch's write"
                        )
                    current_map = {"id": current[0], "return_1d": current[1], "return_5d": current[2],
                                    "return_20d": current[3], "return_60d": current[4]}
                    for col in ("return_1d", "return_5d", "return_20d", "return_60d"):
                        exp_val = expected.get(col)
                        if exp_val is not None and current_map[col] != exp_val:
                            raise OutcomeDriftError(
                                f"{w['symbol']}/{w['pred_date']}: {col} changed from "
                                f"{exp_val!r} (manifest) to {current_map[col]!r} (live) — "
                                f"likely written by the normal resolver since manifest generation"
                            )
                        # A column this write intends to NEWLY populate (manifest recorded
                        # it as NULL, and we computed a real value to fill it) but which is
                        # now already non-NULL live: a COALESCE upsert would silently
                        # preserve that value and no-op on this column, yet still report
                        # the row as a successful write of this batch — falsely claiming a
                        # value the normal resolver actually produced. Fail closed instead.
                        if exp_val is None and w.get(col) is not None and current_map[col] is not None:
                            raise OutcomeDriftError(
                                f"{w['symbol']}/{w['pred_date']}: {col} was NULL when the manifest "
                                f"was generated (this batch intended to populate it) but is now "
                                f"{current_map[col]!r} — likely written by the normal resolver since "
                                f"manifest generation; refusing to claim it as this batch's write"
                            )

                conn.execute(_OUTCOME_UPSERT_SQL, (
                    datetime.now(timezone.utc), w["symbol"], w["horizon"], w["pred_date"], w["market"],
                    w.get("return_1d"), w.get("return_5d"), w.get("return_20d"), w.get("return_60d"),
                    w.get("benchmark_return_5d"), w.get("benchmark_return_20d"), w.get("benchmark_return_60d"),
                ))
                results.append({
                    "symbol": w["symbol"], "horizon": w["horizon"], "market": w["market"],
                    "pred_date": w["pred_date"],
                    "operation": "UPDATE" if current is not None else "INSERT",
                })
    return results


def log_regime(regime_id: int, label: str, features: list[float]):
    with _get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO regime_log (logged_at, regime_id, label, features) VALUES (%s, %s, %s, %s)",
            (datetime.now(timezone.utc), regime_id, label, json.dumps(features)),
        )


def get_training_data(horizon: str, market: str = "IN", window_days: int | None = None) -> list[dict]:
    """
    Join predictions with outcomes to get labelled training rows.
    Forward return column selected by horizon.
    market: IN and US train completely separately — different fundamentals
            distributions, different outcome dynamics, no reason to assume
            the same factor weights transfer between them.
    window_days: if set, only include predictions logged within the last N days
                 (used by the multi-window IC engine).

    Phase 1A.6: a prediction with a definitive market/symbol conflict (see
    services.market_integrity) can only reach this join when it also has a
    matching same-market outcome row — expected to be rare, since the
    write boundary now rejects new conflicting rows outright, but legacy
    rows predating that enforcement could still have one. Excluded here so
    the IC engine and meta-model never train on a mislabeled row, never
    silently included. BOTH and UNKNOWN symbols remain included. Emits a
    structured summary event with fetched/excluded/eligible counts
    whenever at least one row is excluded.
    """
    from services.market_integrity import (
        EVENT_PERFORMANCE_CONFLICTS_EXCLUDED, REASON_DEFINITIVE_CONFLICT,
        emit_market_event, is_definitive_conflict,
    )

    fwd_col = {"short": "return_5d", "medium": "return_20d", "long": "return_60d"}[horizon]
    window_clause = "AND p.logged_at >= now() - interval '%s days'" % window_days if window_days else ""
    with _get_pool().connection() as conn:
        rows = conn.execute(f"""
            SELECT p.symbol, p.tech_z, p.fund_z, p.sentiment_z, p.quality_z,
                   p.combined_alpha, p.meta_alpha, p.signal, p.regime_label,
                   o.{fwd_col} AS fwd_return
            FROM predictions p
            JOIN outcomes o
              ON p.symbol = o.symbol
             AND p.horizon = o.horizon
             AND p.market = o.market
             AND date(p.logged_at) = o.pred_date
            WHERE p.horizon = %s AND p.market = %s
              AND o.{fwd_col} IS NOT NULL
              {window_clause}
        """, (horizon, market)).fetchall()
    cols = ["symbol", "tech_z", "fund_z", "sentiment_z", "quality_z", "combined_alpha",
            "meta_alpha", "signal", "regime_label", "fwd_return"]
    all_rows = [dict(zip(cols, r)) for r in rows]
    kept = [r for r in all_rows if not is_definitive_conflict(r["symbol"], market)]
    excluded_count = len(all_rows) - len(kept)
    if excluded_count:
        emit_market_event(
            EVENT_PERFORMANCE_CONFLICTS_EXCLUDED,
            f"[postgres_store] get_training_data horizon={horizon!r} market={market!r}: "
            f"excluded {excluded_count} definitive market/symbol conflict(s)",
            level=logging.WARNING, reason_code=REASON_DEFINITIVE_CONFLICT, market=market,
            writer_context="get_training_data",
            fetched_count=len(all_rows), excluded_count=excluded_count, eligible_count=len(kept),
        )
    for r in kept:
        del r["symbol"]
    return kept


# Which forward-return columns a horizon's resolver sweep is ever responsible
# for (mirrors outcome_logger.py's horizon_config d1/d5/d20/d60 flags). Keyed
# off a fixed, hardcoded allow-list — never built from caller-supplied text —
# so a prediction stays eligible for re-resolution until *every* column its
# own horizon cares about is filled, instead of being excluded the instant
# any outcome row exists at all (the bug that permanently orphaned return_5d/
# return_20d/return_60d as NULL — see the 2026-07-13 forensic audit).
_HORIZON_ELIGIBILITY_SQL = {
    "short":  "(o.id IS NULL OR o.return_1d IS NULL OR o.return_5d IS NULL)",
    "medium": "(o.id IS NULL OR o.return_5d IS NULL OR o.return_20d IS NULL)",
    "long":   "(o.id IS NULL OR o.return_60d IS NULL)",
}


def get_unresolved_predictions(horizon: str, min_days_old: int, market: str = "IN") -> list[dict]:
    """
    Predictions whose horizon-relevant outcome columns are still incomplete —
    either no outcome row exists yet, or one does but at least one column this
    horizon is responsible for (see _HORIZON_ELIGIBILITY_SQL) is still NULL.

    A prediction is only ever re-offered for resolution while genuinely
    incomplete; once every relevant column is populated it stops appearing
    here (safe no-op for the resolver — see log_outcome's COALESCE upsert).

    Ambiguous groups — multiple predictions logged the same day for the same
    (symbol, horizon, market) at *different* prices, which cannot be told
    apart once joined to the single-row-per-day outcomes key — are excluded
    entirely (fail closed) rather than guessing which one an outcome belongs
    to (the HAVING clause runs before ORDER BY / any caller-side LIMIT, so
    ambiguous groups never reach either). Same-price duplicates (e.g. a batch
    that ran twice) collapse safely to one candidate since they'd resolve to
    the same value either way — `prediction_id` for such a group is the
    lowest (earliest-inserted) `predictions.id` among the duplicates, used
    only as a stable representative for manifest/ordering purposes; the
    outcome relationship itself is keyed by (symbol, horizon, market,
    pred_date), not by any single prediction row (there is no FK).

    Ordering is explicit and stable — oldest prediction_date first, then
    symbol, then prediction_id — so that a caller-side batch_limit slice
    (e.g. the operator backfill tool) is reproducible across repeated calls
    against unchanged data, rather than relying on Postgres's unspecified
    result order for an unordered query (the 2026-07 Phase 1A.3 finding:
    the previous version of this query had no ORDER BY at all and didn't
    even select predictions.id, so a batch-size slice could silently select
    a different set of predictions from one run to the next).
    """
    if horizon not in _HORIZON_ELIGIBILITY_SQL:
        raise ValueError(f"unknown horizon: {horizon!r}")
    eligibility_clause = _HORIZON_ELIGIBILITY_SQL[horizon]
    with _get_pool().connection() as conn:
        rows = conn.execute(f"""
            SELECT p.symbol, p.horizon, p.market, date(p.logged_at) AS pred_date,
                   max(p.price) AS price, min(p.id) AS prediction_id
            FROM predictions p
            LEFT JOIN outcomes o
              ON o.symbol = p.symbol AND o.horizon = p.horizon AND o.market = p.market
             AND o.pred_date = date(p.logged_at)
            WHERE p.horizon = %s AND p.market = %s
              AND now() - p.logged_at >= (%s || ' days')::interval
              AND {eligibility_clause}
            GROUP BY p.symbol, p.horizon, p.market, date(p.logged_at)
            HAVING count(DISTINCT p.price) <= 1
            ORDER BY date(p.logged_at) ASC, p.symbol ASC, min(p.id) ASC
        """, (horizon, market, str(min_days_old))).fetchall()
    cols = ["symbol", "horizon", "market", "pred_date", "price", "prediction_id"]
    return [dict(zip(cols, r)) for r in rows]


def get_predictions_snapshot(prediction_ids: list[int]) -> dict[int, dict]:
    """
    Read-only current-state lookup for a list of predictions.id values, keyed
    by id — used only by manifest preflight re-validation (Phase 1A.3) to
    confirm a manifest's recorded prediction rows still exist and still match
    what was recorded (symbol/market/horizon/date/price), before any write.
    """
    if not prediction_ids:
        return {}
    with _get_pool().connection() as conn:
        rows = conn.execute("""
            SELECT id, symbol, market, horizon, date(logged_at) AS pred_date, price
            FROM predictions WHERE id = ANY(%s)
        """, (list(prediction_ids),)).fetchall()
    cols = ["id", "symbol", "market", "horizon", "pred_date", "price"]
    return {r[0]: dict(zip(cols, r)) for r in rows}


def get_outcome_snapshot(symbol: str, horizon: str, market: str, pred_date: str) -> dict | None:
    """
    Read-only current-state lookup of the (at most one) outcomes row for a
    given (symbol, horizon, market, pred_date) key — used by manifest
    preflight and by the transactional batch writer's in-transaction
    re-validation (Phase 1A.3). Returns None if no row exists yet.
    """
    with _get_pool().connection() as conn:
        row = conn.execute("""
            SELECT id, return_1d, return_5d, return_20d, return_60d
            FROM outcomes
            WHERE symbol = %s AND horizon = %s AND market = %s AND pred_date = %s
        """, (symbol, horizon, market, pred_date)).fetchone()
    if row is None:
        return None
    cols = ["id", "return_1d", "return_5d", "return_20d", "return_60d"]
    return dict(zip(cols, row))


def check_ambiguous_for_key(symbol: str, horizon: str, market: str, pred_date: str) -> bool:
    """
    Live re-check: does the (symbol, horizon, market, pred_date) key currently
    have more than one distinct predictions.price value? Used by manifest
    preflight to catch a new conflicting-price prediction logged after the
    manifest was generated, which would make a previously-clean group
    ambiguous by the time of execution.
    """
    with _get_pool().connection() as conn:
        row = conn.execute("""
            SELECT count(DISTINCT price) FROM predictions
            WHERE symbol = %s AND horizon = %s AND market = %s AND date(logged_at) = %s
        """, (symbol, horizon, market, pred_date)).fetchone()
    return (row[0] or 0) > 1


def count_existing_outcomes(market: str, horizon: str,
                             start_date: str | None = None, end_date: str | None = None) -> dict:
    """
    Read-only summary of existing outcomes rows for a (market, horizon),
    optionally bounded to a pred_date range — used only by the operator
    backfill tool's dry-run report (requirement D "outcome rows found" /
    "missing Nd fields").
    """
    clauses = ["market = %s", "horizon = %s"]
    params: list = [market, horizon]
    if start_date:
        clauses.append("pred_date >= %s")
        params.append(start_date)
    if end_date:
        clauses.append("pred_date <= %s")
        params.append(end_date)
    where = " AND ".join(clauses)
    with _get_pool().connection() as conn:
        row = conn.execute(f"""
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE return_5d IS NULL)  AS missing_5d,
                   count(*) FILTER (WHERE return_20d IS NULL) AS missing_20d,
                   count(*) FILTER (WHERE return_60d IS NULL) AS missing_60d
            FROM outcomes WHERE {where}
        """, params).fetchone()
    return {"total": row[0], "missing_5d": row[1], "missing_20d": row[2], "missing_60d": row[3]}


def get_ambiguous_pending_predictions(horizon: str, min_days_old: int, market: str = "IN") -> list[dict]:
    """
    Companion to get_unresolved_predictions: groups excluded from normal
    resolution because they had >1 distinct price for the same
    (symbol, horizon, market, pred_date) key — used only by the operator
    backfill tool's dry-run report, never by the live resolver.
    """
    if horizon not in _HORIZON_ELIGIBILITY_SQL:
        raise ValueError(f"unknown horizon: {horizon!r}")
    eligibility_clause = _HORIZON_ELIGIBILITY_SQL[horizon]
    with _get_pool().connection() as conn:
        rows = conn.execute(f"""
            SELECT p.symbol, p.horizon, date(p.logged_at) AS pred_date,
                   count(*) AS n_predictions, count(DISTINCT p.price) AS n_distinct_prices
            FROM predictions p
            LEFT JOIN outcomes o
              ON o.symbol = p.symbol AND o.horizon = p.horizon AND o.market = p.market
             AND o.pred_date = date(p.logged_at)
            WHERE p.horizon = %s AND p.market = %s
              AND now() - p.logged_at >= (%s || ' days')::interval
              AND {eligibility_clause}
            GROUP BY p.symbol, p.horizon, date(p.logged_at)
            HAVING count(DISTINCT p.price) > 1
        """, (horizon, market, str(min_days_old))).fetchall()
    cols = ["symbol", "horizon", "pred_date", "n_predictions", "n_distinct_prices"]
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


def count_training_rows(horizon: str, market: str = "IN") -> int:
    return len(get_training_data(horizon, market=market))


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


def get_latest_signals_batch(symbols: list[str], horizon: str) -> dict[str, dict]:
    """
    Portfolio Signal-column hotfix (Session 13) — the root cause of "Not
    cached" showing for almost every holding: the in-memory `_pred_cache`
    (services/prediction_engine.py) that /signal and cached-batch both read
    has only a 15-MINUTE freshness window. Daily Picks deep-scores ~400
    stocks once a night and writes into that same cache, so a holding it
    covered showed a real signal for 15 minutes after that run and then
    "Not cached" for the rest of the day, regardless of coverage — the
    ephemeral cache was never meant to answer "what's the latest signal we
    know," only "is it safe to skip recomputing right now."

    `score_snapshots` is the actual persistent record of that same nightly
    scoring (already written by daily_picks.py's _write_score_snapshots for
    every deep-scored candidate, no expiry) — this reads the single most
    recent snapshot per requested symbol for the given horizon, regardless
    of how long ago it was written. Still a pure read: no prediction is
    computed here, only the last one Daily Picks already computed and
    persisted is looked up.

    NOT market-scoped — score_snapshots has no market column today (the
    same pre-existing limitation get_score_history above already has for
    the History tab chart); a symbol string colliding across IN and US is
    structurally possible but hasn't been observed in practice. Returns
    {symbol: {signal, confidence, snapshot_date}} only for symbols that
    have at least one snapshot; callers should treat a missing key as "no
    persisted signal either," not an error.
    """
    symbols = [s.upper() for s in symbols if s]
    if not symbols:
        return {}
    with _get_pool().connection() as conn:
        rows = conn.execute("""
            SELECT DISTINCT ON (symbol) symbol, signal, confidence_score, snapshot_date
            FROM score_snapshots
            WHERE symbol = ANY(%s) AND horizon = %s
            ORDER BY symbol, snapshot_date DESC
        """, (symbols, horizon)).fetchall()
    return {
        row[0]: {"signal": row[1], "confidence": row[2], "snapshot_date": str(row[3])}
        for row in rows
    }


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


# ── Learning Alpha Engine remediation, Phase 2A: shadow-only canonical ─────
# alpha observation store. save_alpha_observations() is the ONLY write path
# for the alpha_observations table — nothing in this module reads from it,
# and no other service imports a read function for it yet (shadow-only, by
# design, for this phase).

def init_alpha_observations_schema() -> None:
    """Idempotent — CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS are
    already part of the shared SCHEMA_SQL executed by init_db(); this is a
    thin, explicitly-named alias so call sites can express intent without
    re-running the entire schema string themselves."""
    init_db()


def save_alpha_observations(rows: list[dict]) -> bool:
    """
    Bulk-insert one canonical alpha_observations row per dict in `rows` for a
    single (run_id, market, horizon) snapshot.

    Idempotent for retries of the same run: ON CONFLICT (run_id, market,
    horizon, symbol) DO NOTHING — a retried Daily Picks run reusing the same
    job_id/run_id simply skips rows it already wrote, while a genuinely new
    run_id (manual rerun) always inserts fresh rows.

    Returns True on success, False on any failure (exception is logged, not
    raised) — this is shadow telemetry for this phase; a failure here must
    never interrupt or corrupt the Daily Picks job lifecycle or payload.
    """
    if not rows:
        return True
    try:
        with _get_pool().connection() as conn:
            conn.executemany("""
                INSERT INTO alpha_observations (
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
                    %(observation_id)s, %(run_id)s, %(market)s, %(horizon)s, %(symbol)s,
                    %(run_generated_at)s, %(run_session_date)s, %(reference_session_date)s,
                    %(reference_price)s, %(reference_price_source)s, %(reference_price_basis)s,
                    %(technical_raw_score)s, %(fundamental_raw_score)s,
                    %(sentiment_raw_score)s, %(quality_raw_score)s,
                    %(sentiment_available)s, %(quality_available)s,
                    %(technical_zscore)s, %(fundamental_zscore)s,
                    %(sentiment_zscore)s, %(quality_zscore)s,
                    %(composite_score)s, %(ic_combined_alpha)s, %(meta_alpha)s, %(ranking_alpha)s,
                    %(signal)s, %(signal_confidence)s,
                    %(canonical_regime_id)s, %(canonical_regime_label)s,
                    %(feature_schema_version)s, %(regime_schema_version)s,
                    %(is_daily_pick)s, %(pick_rank)s, %(portfolio_weight)s
                )
                ON CONFLICT (run_id, market, horizon, symbol) DO NOTHING
            """, rows)
        return True
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            f"[postgres_store] save_alpha_observations failed ({len(rows)} rows): {e}"
        )
        return False


# ── New: Daily picks performance (section 5) ────────────────────────────────

def save_picks_to_db(payload: dict, market: str = "IN") -> bool:
    """
    Persist the full picks payload to Postgres so it survives Railway redeploys.

    Returns True on success, False on any failure (exception is logged, not raised).
    Callers must check the return value to determine whether durable persistence
    succeeded before marking a Daily Picks job as completed.
    """
    try:
        with _get_pool().connection() as conn:
            conn.execute(
                "INSERT INTO daily_picks_cache (generated_at, payload, market) VALUES (%s, %s, %s)",
                (datetime.now(timezone.utc), json.dumps(payload), market),
            )
            # Keep only last 10 rows per market to avoid bloat
            conn.execute("""
                DELETE FROM daily_picks_cache
                WHERE market = %s AND id NOT IN (
                    SELECT id FROM daily_picks_cache WHERE market = %s
                    ORDER BY generated_at DESC LIMIT 10
                )
            """, (market, market))
        return True
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            f"[postgres_store] save_picks_to_db failed for {market}: {e}"
        )
        return False


def load_picks_from_db(market: str = "IN") -> dict | None:
    """Load the most recently generated picks for a market from Postgres."""
    try:
        with _get_pool().connection() as conn:
            row = conn.execute(
                "SELECT payload FROM daily_picks_cache WHERE market = %s "
                "ORDER BY generated_at DESC LIMIT 1",
                (market,),
            ).fetchone()
        if row:
            payload = row[0]
            return payload if isinstance(payload, dict) else json.loads(payload)
    except Exception as e:
        print(f"[postgres_store] load_picks_from_db error: {e}")
    return None


def get_daily_picks_performance(horizon: str, window_days: int = 90) -> list[dict]:
    """All daily-pick predictions in the window, joined with outcomes if resolved.

    Phase 1A.6: this query has no market filter — it reports every
    is_daily_pick row across both markets in one combined window, so a
    legacy market/symbol conflict (a US-only symbol stored under
    market='IN', the exact shape of the pre-536fd3d contamination) would
    otherwise be silently counted into these hit-rate/P&L numbers. Rows
    with a definitive conflict (services.market_integrity.
    is_definitive_conflict) are excluded after fetch, never included with
    a null/zeroed return — this is the smallest safe fail-closed
    exclusion; it does not change ranking, scoring, or which rows are
    is_daily_pick in the first place. BOTH (dual-listed) and UNKNOWN
    symbols, and valid IN_ONLY/US_ONLY rows, all remain included —
    numerator and denominator both derive from the same filtered list.
    Emits a structured summary event with fetched/excluded/eligible
    counts whenever at least one row is excluded.
    """
    from services.market_integrity import (
        EVENT_PERFORMANCE_CONFLICTS_EXCLUDED, REASON_DEFINITIVE_CONFLICT,
        emit_market_event, is_definitive_conflict,
    )

    with _get_pool().connection() as conn:
        rows = conn.execute("""
            SELECT p.symbol, p.market, date(p.logged_at) AS pred_date, p.price AS entry_price,
                   p.composite_score, p.confidence_score,
                   o.return_5d, o.return_20d, o.return_60d,
                   o.benchmark_return_5d, o.benchmark_return_20d, o.benchmark_return_60d
            FROM predictions p
            LEFT JOIN outcomes o
              ON p.symbol = o.symbol AND p.horizon = o.horizon AND p.market = o.market
             AND o.pred_date = date(p.logged_at)
            WHERE p.horizon = %s AND p.is_daily_pick = TRUE
              AND p.logged_at >= now() - (%s || ' days')::interval
            ORDER BY p.logged_at DESC
        """, (horizon, str(window_days))).fetchall()
    cols = ["symbol", "market", "date", "entry_price", "score", "confidence",
            "return_5d", "return_20d", "return_60d",
            "benchmark_return_5d", "benchmark_return_20d", "benchmark_return_60d"]
    all_rows = [dict(zip(cols, r)) for r in rows]
    filtered = [r for r in all_rows if not is_definitive_conflict(r["symbol"], r["market"])]
    excluded_count = len(all_rows) - len(filtered)
    if excluded_count:
        emit_market_event(
            EVENT_PERFORMANCE_CONFLICTS_EXCLUDED,
            f"[postgres_store] get_daily_picks_performance horizon={horizon!r}: "
            f"excluded {excluded_count} definitive market/symbol conflict(s)",
            level=logging.WARNING, reason_code=REASON_DEFINITIVE_CONFLICT,
            writer_context="get_daily_picks_performance",
            fetched_count=len(all_rows), excluded_count=excluded_count, eligible_count=len(filtered),
        )
    return filtered


# ── Daily Picks job-state helpers (#002D-E) ──────────────────────────────────
# These functions use only explicit, hardcoded column names — no dynamic SQL
# construction from caller-provided input. All column names in UPDATE/INSERT
# statements are literals in this file, not derived from arguments.

def try_reserve_daily_picks_job(
    job_id: str, market: str, runner_instance_id: str
) -> bool:
    """
    Atomically insert a new 'queued' job row for the given market.

    Uses ON CONFLICT DO NOTHING so the partial unique index
    (market) WHERE status IN ('queued','running') acts as the gate:
      - Returns True  → insertion succeeded; caller owns the reservation.
      - Returns False → a conflicting active row exists; caller must not start.
      - Raises        → genuine DB error; caller should treat as 503.
    """
    with _get_pool().connection() as conn:
        result = conn.execute(
            """INSERT INTO daily_picks_jobs
                   (job_id, market, status, runner_instance_id, started_at)
               VALUES (%s, %s, 'queued', %s, now())
               ON CONFLICT DO NOTHING""",
            (job_id, market, runner_instance_id),
        )
        return result.rowcount == 1


def mark_daily_picks_job_running(job_id: str) -> None:
    """Transition a queued job to running. Called at the start of the background task."""
    with _get_pool().connection() as conn:
        conn.execute(
            "UPDATE daily_picks_jobs SET status = 'running' WHERE job_id = %s",
            (job_id,),
        )


def record_daily_picks_job_progress(
    job_id: str,
    phase: str,
    processed: int | None,
    total: int | None,
    universe_used: str | None = None,
    universe_degraded: bool | None = None,
    last_progress_at=None,
    screener_raw_count: int | None = None,
    universe_candidate_count: int | None = None,
    universe_selection_attempts: int | None = None,
    universe_selection_reason: str | None = None,
    universe_selection_error_category: str | None = None,
) -> None:
    """
    Record genuine forward progress: a batch or candidate completed.
    last_progress_at defaults to now() if not supplied.
    universe_used / universe_degraded — and, as of Release 12C, the
    universe-selection observability fields (screener_raw_count,
    universe_candidate_count, universe_selection_attempts,
    universe_selection_reason, universe_selection_error_category) — are
    written only when provided (Phase 0b only), same gating as before.

    phase_task_processed / phase_task_total (Release 12C) are always written
    as explicit aliases of processed/total for the CURRENT call's phase —
    processed/total themselves are left untouched for backward compatibility,
    but a consumer that wants an unambiguous "this phase's work units, never
    universe size" value can read the new columns instead.
    """
    if last_progress_at is None:
        last_progress_at = datetime.now(timezone.utc)
    with _get_pool().connection() as conn:
        if universe_used is not None and universe_degraded is not None:
            conn.execute(
                """UPDATE daily_picks_jobs
                   SET phase = %s, processed = %s, total = %s,
                       phase_task_processed = %s, phase_task_total = %s,
                       universe_used = %s, universe_degraded = %s,
                       screener_raw_count = %s, universe_candidate_count = %s,
                       universe_selection_attempts = %s,
                       universe_selection_reason = %s,
                       universe_selection_error_category = %s,
                       last_progress_at = %s
                   WHERE job_id = %s""",
                (phase, processed, total, processed, total,
                 universe_used, universe_degraded,
                 screener_raw_count, universe_candidate_count,
                 universe_selection_attempts, universe_selection_reason,
                 universe_selection_error_category,
                 last_progress_at, job_id),
            )
        else:
            conn.execute(
                """UPDATE daily_picks_jobs
                   SET phase = %s, processed = %s, total = %s,
                       phase_task_processed = %s, phase_task_total = %s,
                       last_progress_at = %s
                   WHERE job_id = %s""",
                (phase, processed, total, processed, total, last_progress_at, job_id),
            )


def record_daily_picks_job_heartbeat(job_id: str, timestamp) -> None:
    """Write last_runner_heartbeat_at. Never changes status or releases locks."""
    with _get_pool().connection() as conn:
        conn.execute(
            "UPDATE daily_picks_jobs SET last_runner_heartbeat_at = %s WHERE job_id = %s",
            (timestamp, job_id),
        )


def mark_daily_picks_job_completed(
    job_id: str,
    completed_at,
    persisted_picks_timestamp,
) -> None:
    """
    Mark a job completed. Only called after save_picks_to_db() returned True.
    Both completed_at and persisted_picks_timestamp must be non-None.
    """
    with _get_pool().connection() as conn:
        conn.execute(
            """UPDATE daily_picks_jobs
               SET status = 'completed',
                   completed_at = %s,
                   persisted_picks_timestamp = %s
               WHERE job_id = %s""",
            (completed_at, persisted_picks_timestamp, job_id),
        )


def record_daily_picks_job_containment(
    job_id: str,
    production_alpha_source: str,
    shadow_ic_available: dict,
    shadow_meta_model_available: dict,
    containment_reason: str | None,
    learning_dataset_version: str,
) -> None:
    """
    Learning Alpha Engine remediation, Phase 1 — persist containment
    observability once per run onto the durable daily_picks_jobs row.
    shadow_ic_available / shadow_meta_model_available are per-horizon
    ({"short": bool, "medium": bool, "long": bool}) dicts, stored as JSONB.
    Best-effort — callers (daily_picks._try_job_containment) already swallow
    any exception raised here.
    """
    with _get_pool().connection() as conn:
        conn.execute(
            """UPDATE daily_picks_jobs
               SET production_alpha_source = %s,
                   shadow_ic_available = %s::jsonb,
                   shadow_meta_model_available = %s::jsonb,
                   containment_reason = %s,
                   learning_dataset_version = %s
               WHERE job_id = %s""",
            (
                production_alpha_source,
                json.dumps(shadow_ic_available),
                json.dumps(shadow_meta_model_available),
                containment_reason,
                learning_dataset_version,
                job_id,
            ),
        )


def mark_daily_picks_job_failed(
    job_id: str,
    completed_at,
    last_error: str,
) -> None:
    """
    Mark a job failed. Called on any exception inside generate_picks() or
    when save_picks_to_db() returns False (durable persistence failed).
    persisted_picks_timestamp remains NULL.
    """
    with _get_pool().connection() as conn:
        conn.execute(
            """UPDATE daily_picks_jobs
               SET status = 'failed',
                   completed_at = %s,
                   last_error = %s
               WHERE job_id = %s""",
            (completed_at, last_error, job_id),
        )


def get_active_daily_picks_job(market: str) -> dict | None:
    """
    Return the most recent queued/running job row for a market, or None.
    Used for 409 conflict detail and the 'generating' flag in /status.
    Swallows DB errors — callers must not treat None as "no DB".
    """
    try:
        with _get_pool().connection() as conn:
            row = conn.execute(
                """SELECT job_id, status, phase, processed, total,
                          last_runner_heartbeat_at, last_progress_at,
                          started_at, runner_instance_id,
                          universe_used, universe_degraded,
                          screener_raw_count, universe_candidate_count,
                          universe_selection_attempts, universe_selection_reason,
                          universe_selection_error_category,
                          phase_task_processed, phase_task_total
                   FROM daily_picks_jobs
                   WHERE market = %s AND status IN ('queued', 'running')
                   ORDER BY started_at DESC LIMIT 1""",
                (market,),
            ).fetchone()
        if not row:
            return None
        cols = ["job_id", "status", "phase", "processed", "total",
                "last_runner_heartbeat_at", "last_progress_at",
                "started_at", "runner_instance_id",
                "universe_used", "universe_degraded",
                "screener_raw_count", "universe_candidate_count",
                "universe_selection_attempts", "universe_selection_reason",
                "universe_selection_error_category",
                "phase_task_processed", "phase_task_total"]
        return dict(zip(cols, row))
    except Exception:
        return None


def get_latest_daily_picks_job(market: str) -> dict | None:
    """
    Return the most recent job row for a market regardless of status, or None.
    Used by GET /api/picks/status so completed/failed job details remain visible.
    Swallows DB errors.
    """
    try:
        with _get_pool().connection() as conn:
            row = conn.execute(
                """SELECT job_id, status, phase, processed, total,
                          last_runner_heartbeat_at, last_progress_at,
                          started_at, completed_at, persisted_picks_timestamp,
                          runner_instance_id, universe_used, universe_degraded,
                          last_error,
                          screener_raw_count, universe_candidate_count,
                          universe_selection_attempts, universe_selection_reason,
                          universe_selection_error_category,
                          phase_task_processed, phase_task_total,
                          production_alpha_source, shadow_ic_available,
                          shadow_meta_model_available, containment_reason,
                          learning_dataset_version
                   FROM daily_picks_jobs
                   WHERE market = %s
                   ORDER BY started_at DESC LIMIT 1""",
                (market,),
            ).fetchone()
        if not row:
            return None
        cols = ["job_id", "status", "phase", "processed", "total",
                "last_runner_heartbeat_at", "last_progress_at",
                "started_at", "completed_at", "persisted_picks_timestamp",
                "runner_instance_id", "universe_used", "universe_degraded",
                "last_error",
                "screener_raw_count", "universe_candidate_count",
                "universe_selection_attempts", "universe_selection_reason",
                "universe_selection_error_category",
                "phase_task_processed", "phase_task_total",
                "production_alpha_source", "shadow_ic_available",
                "shadow_meta_model_available", "containment_reason",
                "learning_dataset_version"]
        return dict(zip(cols, row))
    except Exception:
        return None


def get_daily_picks_job_by_id(job_id: str) -> dict | None:
    """
    Exact, parameterized lookup of a single daily_picks_jobs row by job_id —
    Daily Picks Scheduler Remediation Phase 1A base-job provenance.

    Returns only the fields needed to verify base-job provenance for
    premarket finalization: job_id, market, status, started_at, completed_at,
    persisted_picks_timestamp, universe_degraded, last_error.

    Returns None ONLY when no row matches job_id. A genuine database error
    (bad connection, query failure) is deliberately NOT caught here and
    propagates to the caller — unlike get_active_daily_picks_job/
    get_latest_daily_picks_job above, which swallow errors for their own
    presentation-only use cases. Provenance verification must be able to
    distinguish "this job_id does not exist" from "the lookup itself could
    not be performed"; collapsing both into None would let a database outage
    silently masquerade as a malformed-provenance rejection. Read-only —
    issues no writes.
    """
    with _get_pool().connection() as conn:
        row = conn.execute(
            """SELECT job_id, market, status, started_at, completed_at,
                      persisted_picks_timestamp, universe_degraded, last_error
               FROM daily_picks_jobs
               WHERE job_id = %s""",
            (job_id,),
        ).fetchone()
    if not row:
        return None
    cols = ["job_id", "market", "status", "started_at", "completed_at",
            "persisted_picks_timestamp", "universe_degraded", "last_error"]
    return dict(zip(cols, row))
