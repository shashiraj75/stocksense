"""
SQLite persistence layer for the Learning Alpha Engine.

Tables:
  predictions — factor z-scores + signal logged at generation time
  outcomes    — actual forward returns resolved after the holding period
  regime_log  — historical regime snapshots for KMeans retraining
"""

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

from services.market_integrity import MISSING_MARKET, require_explicit_market

log = logging.getLogger(__name__)

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
            market         TEXT    NOT NULL,
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
            market       TEXT NOT NULL,
            pred_date    TEXT NOT NULL,
            return_1d    REAL,
            return_5d    REAL,
            return_20d   REAL,
            return_60d   REAL,
            benchmark_return_5d  REAL,
            benchmark_return_20d REAL,
            benchmark_return_60d REAL
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
        #
        # Phase 1A.6: this specific fallback intentionally KEEPS a default,
        # unlike the fresh CREATE TABLE above — it's a narrow backward-
        # compatibility path for a genuinely ancient, already-populated
        # SQLite file that predates the `market` column entirely. SQLite
        # requires a non-NULL default to ADD a NOT NULL column to a
        # non-empty table; this never fires against any DB created by the
        # CREATE TABLE statement above (the column already exists there, so
        # this becomes a no-op caught by the except below), and it never
        # rewrites any historical row — the default only governs what a
        # *future* insert would get if it omitted the column, which
        # log_prediction/log_outcome never do.
        for stmt in (
            "ALTER TABLE predictions ADD COLUMN market TEXT NOT NULL DEFAULT 'IN'",
            "ALTER TABLE outcomes ADD COLUMN market TEXT NOT NULL DEFAULT 'IN'",
            # GPI-0 condition D3 (2026-07-17) — see log_outcome's own comment
            # for why these were added; nullable (unlike market above), no
            # default needed since a resolved outcome without a computed
            # benchmark return should read NULL, never a fabricated 0.
            "ALTER TABLE outcomes ADD COLUMN benchmark_return_5d REAL",
            "ALTER TABLE outcomes ADD COLUMN benchmark_return_20d REAL",
            "ALTER TABLE outcomes ADD COLUMN benchmark_return_60d REAL",
        ):
            try:
                c.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists


def log_prediction(symbol: str, horizon: str, factor_zscores: dict,
                   combined_alpha: float, meta_alpha: float | None,
                   signal: str, price: float, regime_label: str = "",
                   *, market=MISSING_MARKET, _writer_source: str = "unknown", **kwargs):
    """
    market is required and keyword-only, defaulting to the MISSING_MARKET
    sentinel — see services/postgres_store.py's log_prediction for the
    full Phase 1A.6 rationale (this SQLite path mirrors it for dev/test
    parity). Raises MissingMarketContextError / InvalidMarketError /
    MarketSymbolConflictError; logs (never silently accepts or rejects)
    an UNKNOWN symbol. Every check runs before any SQLite connection is
    opened.
    """
    result = require_explicit_market(symbol, market, writer_context=_writer_source)
    market = result.market

    if USE_POSTGRES:
        return _pg.log_prediction(symbol, horizon, factor_zscores, combined_alpha,
                                   meta_alpha, signal, price, regime_label, market=market,
                                   _writer_source=_writer_source, **kwargs)
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


class OutcomeDriftError(Exception):
    """
    Raised by execute_outcome_writes_transactional when a manifest candidate's
    live outcome state no longer matches what the manifest recorded. This is
    the canonical exception type callers (manifest_backfill.py) should catch —
    when USE_POSTGRES=1, postgres_store's own OutcomeDriftError is caught and
    re-raised as this class so callers never need to know which backend is
    active.
    """


def log_outcome(symbol: str, horizon: str, pred_date: str,
                return_1d: float | None, return_5d: float | None,
                return_20d: float | None, return_60d: float | None = None,
                *, market=MISSING_MARKET, _writer_source: str = "unknown", **kwargs):
    """
    Upsert (SELECT-then-INSERT-or-UPDATE, since the SQLite outcomes table has
    no UNIQUE constraint to target with ON CONFLICT). A column that already
    holds a resolved value is preserved via COALESCE(existing, new) — this
    lets a later sweep backfill return_20d/return_60d without disturbing
    return_1d/return_5d a prior sweep already wrote, or losing a value if a
    later sweep can't compute one for a given horizon. Mirrors
    services/postgres_store.py's log_outcome exactly for behavioral parity
    between the SQLite (dev/test) and Postgres (production) backends.

    market carries the same explicit-market contract as log_prediction
    (Phase 1A.6 remediation) — required, keyword-only, MISSING_MARKET
    sentinel default. Validated before any SQLite connection is opened and
    before delegating to the Postgres path.
    """
    result = require_explicit_market(symbol, market, writer_context=_writer_source)
    market = result.market

    if USE_POSTGRES:
        return _pg.log_outcome(symbol, horizon, pred_date, return_1d, return_5d, return_20d,
                                return_60d=return_60d, market=market,
                                _writer_source=_writer_source, **kwargs)

    # GPI-0 condition D3 (2026-07-17): mirrors postgres_store.log_outcome's
    # own benchmark_return_5d/20d/60d params for SQLite parity — previously
    # silently absorbed into **kwargs and dropped on this backend only,
    # which would have made this docstring's "mirrors postgres_store.py's
    # log_outcome exactly for behavioral parity" claim false the moment the
    # live resolver started passing them.
    benchmark_return_5d = kwargs.get("benchmark_return_5d")
    benchmark_return_20d = kwargs.get("benchmark_return_20d")
    benchmark_return_60d = kwargs.get("benchmark_return_60d")

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
                    return_60d = COALESCE(return_60d, ?),
                    benchmark_return_5d  = COALESCE(benchmark_return_5d, ?),
                    benchmark_return_20d = COALESCE(benchmark_return_20d, ?),
                    benchmark_return_60d = COALESCE(benchmark_return_60d, ?)
                WHERE id = ?
            """, (now, return_1d, return_5d, return_20d, return_60d,
                  benchmark_return_5d, benchmark_return_20d, benchmark_return_60d,
                  existing["id"]))
            return
        c.execute("""
            INSERT INTO outcomes (resolved_at, symbol, horizon, pred_date, market,
                                  return_1d, return_5d, return_20d, return_60d,
                                  benchmark_return_5d, benchmark_return_20d, benchmark_return_60d)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now, symbol, horizon, pred_date, market, return_1d, return_5d, return_20d, return_60d,
            benchmark_return_5d, benchmark_return_20d, benchmark_return_60d,
        ))


def execute_outcome_writes_transactional(writes: list[dict]) -> list[dict]:
    """
    Apply a whole manifest-locked batch of outcome writes as ONE transaction
    (Phase 1A.3). See services/postgres_store.py's version for the full
    rationale — this SQLite path mirrors it for dev/test parity.

    SQLite has no per-row FOR UPDATE, but a single `with conn:` block already
    takes an exclusive write lock on the whole database for the duration of
    the transaction and commits on success / rolls back on any exception —
    which is more than sufficient to serialize against a concurrent writer
    for the purposes this batch needs (no other writer can interleave a
    change between this transaction's re-check and its write).
    """
    if USE_POSTGRES:
        try:
            return _pg.execute_outcome_writes_transactional(writes)
        except _pg.OutcomeDriftError as e:
            raise OutcomeDriftError(str(e)) from e

    init_db()
    results = []
    with _lock, _conn() as c:
        for w in writes:
            current = c.execute(
                "SELECT id, return_1d, return_5d, return_20d, return_60d FROM outcomes "
                "WHERE symbol=? AND horizon=? AND market=? AND pred_date=?",
                (w["symbol"], w["horizon"], w["market"], w["pred_date"]),
            ).fetchone()

            expected = w.get("expected_existing") or {}
            if current is None:
                if expected.get("id") is not None:
                    raise OutcomeDriftError(
                        f"{w['symbol']}/{w['pred_date']}: manifest expected an existing "
                        f"outcome row (id={expected.get('id')}) that no longer exists"
                    )
            else:
                if expected.get("id") is None:
                    raise OutcomeDriftError(
                        f"{w['symbol']}/{w['pred_date']}: an outcome row (id={current['id']}) now "
                        f"exists but the manifest recorded none — likely created by the normal "
                        f"resolver since manifest generation; refusing to claim it as this batch's write"
                    )
                for col in ("return_1d", "return_5d", "return_20d", "return_60d"):
                    exp_val = expected.get(col)
                    if exp_val is not None and current[col] != exp_val:
                        raise OutcomeDriftError(
                            f"{w['symbol']}/{w['pred_date']}: {col} changed from "
                            f"{exp_val!r} (manifest) to {current[col]!r} (live) — "
                            f"likely written by the normal resolver since manifest generation"
                        )
                    if exp_val is None and w.get(col) is not None and current[col] is not None:
                        raise OutcomeDriftError(
                            f"{w['symbol']}/{w['pred_date']}: {col} was NULL when the manifest "
                            f"was generated (this batch intended to populate it) but is now "
                            f"{current[col]!r} — likely written by the normal resolver since "
                            f"manifest generation; refusing to claim it as this batch's write"
                        )

            now = datetime.now(timezone.utc).isoformat()
            if current is not None:
                c.execute("""
                    UPDATE outcomes SET
                        resolved_at = ?,
                        return_1d  = COALESCE(return_1d, ?),
                        return_5d  = COALESCE(return_5d, ?),
                        return_20d = COALESCE(return_20d, ?),
                        return_60d = COALESCE(return_60d, ?)
                    WHERE id = ?
                """, (now, w.get("return_1d"), w.get("return_5d"), w.get("return_20d"),
                      w.get("return_60d"), current["id"]))
            else:
                c.execute("""
                    INSERT INTO outcomes (resolved_at, symbol, horizon, pred_date, market,
                                          return_1d, return_5d, return_20d, return_60d)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (now, w["symbol"], w["horizon"], w["pred_date"], w["market"],
                      w.get("return_1d"), w.get("return_5d"), w.get("return_20d"), w.get("return_60d")))

            results.append({
                "symbol": w["symbol"], "horizon": w["horizon"], "market": w["market"],
                "pred_date": w["pred_date"],
                "operation": "UPDATE" if current is not None else "INSERT",
            })
    return results


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


# TTL cache for the shared unbounded predictions/outcomes join. Added 2026-08
# (Supabase egress incident): besides ic_engine's diagnostic live-IC
# computation, weight_adapter's count_training_rows() also called this
# uncached on every adaptation cycle just to count rows — and did so
# immediately after ic_engine.invalidate_cache() wiped ic_engine's own
# cache, defeating that cache's purpose. Caching here, at the single shared
# entry point every non-training caller goes through, fixes both at once.
# force_fresh=True (used by meta_model.train()) bypasses this cache — an
# actual model retrain must always see the true current data, never a
# cached snapshot, so numerical training behavior is unchanged.
_TRAINING_DATA_CACHE_TTL = 3600  # matches ic_engine.CACHE_TTL
_training_data_cache: dict[tuple, list[dict]] = {}
_training_data_cache_expiry: dict[tuple, float] = {}
_training_data_cache_lock = threading.Lock()


def invalidate_training_data_cache() -> None:
    """Force a fresh join on next non-force_fresh call (called after new
    outcomes are logged, alongside ic_engine.invalidate_cache())."""
    with _training_data_cache_lock:
        _training_data_cache.clear()
        _training_data_cache_expiry.clear()


def get_training_data(
    horizon: str, market: str = "IN", window_days: int | None = None,
    force_fresh: bool = False,
) -> list[dict]:
    """
    Join predictions with outcomes to get labelled training rows.
    Forward return column selected by horizon. IN and US train separately —
    see services/postgres_store.py's get_training_data for why.

    Phase 1A.6: excludes any row with a definitive market/symbol conflict
    (services.market_integrity) — see postgres_store.get_training_data's
    docstring for why this matters even though it's rare in practice.

    Cached per (horizon, market, window_days) for _TRAINING_DATA_CACHE_TTL
    seconds unless force_fresh=True — see the module comment above.
    """
    cache_key = (horizon, market, window_days)
    if not force_fresh:
        now = time.time()
        with _training_data_cache_lock:
            if cache_key in _training_data_cache and now < _training_data_cache_expiry.get(cache_key, 0):
                return _training_data_cache[cache_key]

    result = _get_training_data_uncached(horizon, market=market, window_days=window_days)

    if not force_fresh:
        with _training_data_cache_lock:
            _training_data_cache[cache_key] = result
            _training_data_cache_expiry[cache_key] = time.time() + _TRAINING_DATA_CACHE_TTL

    return result


def _get_training_data_uncached(horizon: str, market: str = "IN", window_days: int | None = None) -> list[dict]:
    """Uncached implementation — see get_training_data, the cached entry
    point every non-training caller should use instead of calling this
    directly."""
    if USE_POSTGRES:
        return _pg.get_training_data(horizon, market=market, window_days=window_days)
    from services.market_integrity import (
        EVENT_PERFORMANCE_CONFLICTS_EXCLUDED, REASON_DEFINITIVE_CONFLICT,
        emit_market_event, is_definitive_conflict,
    )

    init_db()
    # long horizon = 60D forward return (≈3 months), matching the stated holding period
    fwd_col = {"short": "return_5d", "medium": "return_20d", "long": "return_60d"}[horizon]
    with _lock, _conn() as c:
        rows = c.execute(f"""
            SELECT p.symbol, p.tech_z, p.fund_z, p.sentiment_z, p.quality_z,
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
    kept = []
    excluded_count = 0
    for r in rows:
        d = dict(r)
        symbol = d.pop("symbol")
        if is_definitive_conflict(symbol, market):
            excluded_count += 1
        else:
            kept.append(d)
    if excluded_count:
        emit_market_event(
            EVENT_PERFORMANCE_CONFLICTS_EXCLUDED,
            f"[alpha_engine.store] get_training_data horizon={horizon!r} market={market!r}: "
            f"excluded {excluded_count} definitive market/symbol conflict(s)",
            level=logging.WARNING, reason_code=REASON_DEFINITIVE_CONFLICT, market=market,
            writer_context="get_training_data",
            fetched_count=len(rows), excluded_count=excluded_count, eligible_count=len(kept),
        )
    return kept


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
    the same symbol/horizon/market) are excluded — fail closed, before
    ORDER BY / any caller-side LIMIT.

    Ordering is explicit and stable (pred_date ASC, symbol ASC, prediction_id
    ASC — prediction_id is min(id) within a same-price duplicate group) so a
    caller-side batch_limit slice is reproducible across repeated calls
    against unchanged data (Phase 1A.3 fix — the prior version had no
    ORDER BY and didn't select predictions.id at all).
    """
    if USE_POSTGRES:
        return _pg.get_unresolved_predictions(horizon, min_days_old, market=market)
    if horizon not in _HORIZON_ELIGIBILITY_SQL:
        raise ValueError(f"unknown horizon: {horizon!r}")
    eligibility_clause = _HORIZON_ELIGIBILITY_SQL[horizon]
    init_db()
    with _lock, _conn() as c:
        rows = c.execute(f"""
            SELECT p.symbol, p.horizon, p.market, date(p.logged_at) AS pred_date,
                   max(p.price) AS price, min(p.id) AS prediction_id
            FROM predictions p
            LEFT JOIN outcomes o
              ON o.symbol = p.symbol AND o.horizon = p.horizon AND o.market = p.market
             AND o.pred_date = date(p.logged_at)
            WHERE p.horizon = ? AND p.market = ?
              AND julianday('now') - julianday(p.logged_at) >= ?
              AND {eligibility_clause}
            GROUP BY p.symbol, p.horizon, p.market, date(p.logged_at)
            HAVING count(DISTINCT p.price) <= 1
            ORDER BY date(p.logged_at) ASC, p.symbol ASC, min(p.id) ASC
        """, (horizon, market, min_days_old)).fetchall()
    return [dict(r) for r in rows]


def get_predictions_snapshot(prediction_ids: list[int]) -> dict[int, dict]:
    """SQLite mirror of postgres_store.get_predictions_snapshot — used only by
    manifest preflight re-validation (Phase 1A.3)."""
    if USE_POSTGRES:
        return _pg.get_predictions_snapshot(prediction_ids)
    if not prediction_ids:
        return {}
    init_db()
    placeholders = ",".join("?" * len(prediction_ids))
    with _lock, _conn() as c:
        rows = c.execute(f"""
            SELECT id, symbol, market, horizon, date(logged_at) AS pred_date, price
            FROM predictions WHERE id IN ({placeholders})
        """, list(prediction_ids)).fetchall()
    return {r["id"]: dict(r) for r in rows}


def get_outcome_snapshot(symbol: str, horizon: str, market: str, pred_date: str) -> dict | None:
    """SQLite mirror of postgres_store.get_outcome_snapshot."""
    if USE_POSTGRES:
        return _pg.get_outcome_snapshot(symbol, horizon, market, pred_date)
    init_db()
    with _lock, _conn() as c:
        row = c.execute("""
            SELECT id, return_1d, return_5d, return_20d, return_60d
            FROM outcomes
            WHERE symbol=? AND horizon=? AND market=? AND pred_date=?
        """, (symbol, horizon, market, pred_date)).fetchone()
    return dict(row) if row is not None else None


def check_ambiguous_for_key(symbol: str, horizon: str, market: str, pred_date: str) -> bool:
    """SQLite mirror of postgres_store.check_ambiguous_for_key."""
    if USE_POSTGRES:
        return _pg.check_ambiguous_for_key(symbol, horizon, market, pred_date)
    init_db()
    with _lock, _conn() as c:
        row = c.execute("""
            SELECT count(DISTINCT price) FROM predictions
            WHERE symbol=? AND horizon=? AND market=? AND date(logged_at)=?
        """, (symbol, horizon, market, pred_date)).fetchone()
    return (row[0] or 0) > 1


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
