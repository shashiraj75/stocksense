import os
import math
import json
import logging
import threading
import time as _time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Literal
from services.auth import get_current_user_id
from services.market_hours import is_market_open as _is_market_open
from services.paper_trade_math import compute_realized_pnl_abs, compute_realized_pnl_pct
from services.rate_limit import limiter, USER_DATA_RATE_LIMIT
from services.postmortem.deterministic import (
    CALCULATION_VERSION,
    ClosedTradeRecord,
    compute_postmortem,
)
from services.postmortem.entry_snapshot import (
    EntrySnapshot,
    RecommendationContext,
    SNAPSHOT_SCHEMA_VERSION,
    build_entry_snapshot,
    classify_evidence_completeness,
)
from services.postmortem.evidence_attribution import ATTRIBUTION_RULES_VERSION, build_evidence_attribution
from services.postmortem.daily_report import DailyTradePostmortem, build_daily_report
from services.market_hours import IST, ET
from services.postmortem.close_service import (
    CloseServiceError,
    CloseValidationError,
    TradeAlreadyClosedError,
    TradeNotFoundError,
    TradeNotOwnedError,
    UnsupportedMarketError,
    close_paper_trade,
    sell_exit_reason_to_mechanism,
)
from services.postmortem.close_service import _EXIT_SNAPSHOT_COLUMNS as _EXIT_SNAPSHOT_DB_COLUMNS
from services.postmortem.close_service import _insert_outbox_record
from services.postmortem.exit_snapshot import CloseExitMechanism, ExitSnapshot
from services.postmortem import generation_service
from services.postmortem import outbox as outbox_ops
from services.postmortem import report_store
from services.postmortem import price_path_generation
from services.postmortem.idempotency import (
    OPERATION_TYPE_PAPER_BUY,
    OPERATION_TYPE_PAPER_SELL,
    POLL_ATTEMPTS,
    POLL_INTERVAL_SECONDS,
    ExistingIdempotencyRow,
    IdempotencyAction,
    compute_close_request_fingerprint,
    compute_request_fingerprint,
    decide_action,
    validate_idempotency_key_format,
)
from services.postmortem import idempotency_metrics as _metrics

log = logging.getLogger(__name__)
router = APIRouter()

# Buy idempotency response-shape version — independent of
# entry_snapshot.SNAPSHOT_SCHEMA_VERSION (that's the snapshot row's own
# shape) and POSTMORTEM_SCHEMA_VERSION below (the postmortem endpoint's
# shape). A replayed idempotent response is stored verbatim tagged with
# this version so a future shape change can tell old stored responses
# apart from new ones.
BUY_RESPONSE_SCHEMA_VERSION = "1.0.0"
# Trade Postmortem Engine, Sprint 2 correction phase (Stage 6) — same
# convention, for the /sell (close) idempotent-response contract.
SELL_RESPONSE_SCHEMA_VERSION = "1.0.0"

STARTING_CASH_IN = 1_000_000.0  # ₹10,00,000 virtual cash
STARTING_CASH_US = 100_000.0    # $100,000 virtual cash — separate ledger, not a currency conversion of the above

_CASH_COL = {"IN": "cash", "US": "cash_usd"}
_STARTING = {"IN": STARTING_CASH_IN, "US": STARTING_CASH_US}
_SYMBOL = {"IN": "₹", "US": "$"}

# The three horizons a paper trade can be immutably tagged with at creation
# (see /buy's INSERT — `horizon` is never UPDATEd anywhere after that single
# INSERT, so it always reflects the recommendation horizon recorded when the
# trade was opened, never today's live horizon/signal/price). Any closed
# trade whose stored horizon isn't one of these three (e.g. a pre-existing
# legacy row with a NULL or otherwise-shaped value) is reported separately
# under "unclassified" rather than guessed into one of the three buckets.
_CLOSED_HORIZONS = ("short", "medium", "long")


_CLOSED_HISTORY_INITIAL_LIMIT = 5
# Bounds for the lazy "older trades" endpoint below — never unbounded.
_OLDER_HISTORY_DEFAULT_LIMIT = 10
_OLDER_HISTORY_MAX_LIMIT = 50

_TRADE_ROW_COLUMNS = (
    "id, symbol, market, quantity, entry_price, exit_price, status, signal, horizon, "
    "opened_at, closed_at, stop_loss, target_price, trade_management_mode, exit_reason"
)


def _trade_row_to_dict(row: tuple) -> dict:
    """Shared row->dict mapping for both GET /portfolio and the older-history
    endpoint, so the two never drift into two slightly different trade shapes.
    Column order must match _TRADE_ROW_COLUMNS exactly."""
    (tid, sym, mkt, qty, ep, xp, status, sig, hor, opened, closed, sl, tp, mgmt_mode, exit_reason) = row
    trade = {
        "id": tid,
        "symbol": sym,
        "market": mkt,
        "quantity": qty,
        "entry_price": ep,
        "exit_price": xp,
        "stop_loss": sl,
        "target_price": tp,
        "status": status,
        "signal": sig,
        "horizon": hor,
        "opened_at": opened.isoformat() if opened else None,
        "closed_at": closed.isoformat() if closed else None,
        "invested": round(ep * qty, 2),
        "trade_management_mode": mgmt_mode,
        "exit_reason": exit_reason,
    }
    if status != "OPEN":
        # Trade Postmortem Engine, Phase 1 — routed through the single
        # authoritative P&L function (services/paper_trade_math.py) instead
        # of an inline formula; behavior is byte-for-byte unchanged (see
        # tests/regression/test_paper_trading_postmortem_pnl_parity.py).
        trade["realized_pnl"] = compute_realized_pnl_abs(ep, xp, qty)
    return trade


def _bucket_closed_trades_by_horizon(trades: list[dict]) -> dict[str, list[dict]]:
    """Groups by the immutable persisted `horizon` field only — never by
    holding period, exit date, current recommendation, or any live state.
    Always returns all three official keys (possibly empty) plus
    "unclassified" (possibly empty) so callers can uniformly check length
    rather than handling a missing key."""
    buckets: dict[str, list[dict]] = {h: [] for h in _CLOSED_HORIZONS}
    buckets["unclassified"] = []
    for t in trades:
        key = t["horizon"] if t["horizon"] in _CLOSED_HORIZONS else "unclassified"
        buckets[key].append(t)
    return buckets


def _sort_closed_desc(trades: list[dict]) -> list[dict]:
    """Newest-closed-first. `closed_at` is an ISO-8601 string with a
    consistent offset (from datetime.isoformat() on a TIMESTAMPTZ column),
    so lexicographic sort is equivalent to chronological sort; `id` is the
    tiebreaker for same-timestamp rows, matching the keyset-pagination
    cursor used by the older-history endpoint below."""
    return sorted(trades, key=lambda t: (t["closed_at"] or "", t["id"]), reverse=True)


def _closed_history_bucket(trades: list[dict], limit: int = _CLOSED_HISTORY_INITIAL_LIMIT) -> dict:
    ordered = _sort_closed_desc(trades)
    return {
        "summary": _summarize_closed_bucket(trades),
        "latest_trades": ordered[:limit],
        "earlier_trade_count": max(0, len(ordered) - limit),
    }


def _closed_trade_history_by_horizon_for_market(closed_trades_for_market: list[dict]) -> dict:
    buckets = _bucket_closed_trades_by_horizon(closed_trades_for_market)
    result = {h: _closed_history_bucket(buckets[h]) for h in _CLOSED_HORIZONS}
    if buckets["unclassified"]:
        result["unclassified"] = _closed_history_bucket(buckets["unclassified"])
    return result


def _summarize_closed_bucket(trades: list[dict]) -> dict:
    """
    Two distinct, never-merged outcome metrics:

    Win Rate — the same canonical definition as the top-level Win Rate stat
    card (see _overview_from_closed_trades / _fetch_closed_trade_aggregates'
    win_trades_count, which use the identical ">0" predicate against
    realized P&L): a strictly positive realized_pnl is a win; exactly zero
    is break-even (never counted as a win); negative is a loss. Denominator
    is every closed trade in this bucket — never gated by exit_reason.

    Target Hit Rate — trades closed because their defined target was hit /
    trades with a conclusive target-or-stop-loss outcome — never against
    total closed trades or P&L sign. `exit_reason` (set only by POST /sell,
    either explicitly by the client on an auto-close trigger or a manual
    close) is the sole authoritative outcome field: "TARGET_HIT"/"STOP_LOSS"
    are conclusive; "MANUAL", None (legacy trades closed before this column
    existed, or a manual close that recorded no reason), or any other value
    are non-conclusive and excluded from the hit-rate denominator — but
    still counted in P&L/average-return/Win-Rate above, since those are
    realized-outcome metrics, not target-accuracy metrics.
    """
    count = len(trades)

    win_trades = sum(1 for t in trades if t["realized_pnl"] > 0)
    break_even_trades = sum(1 for t in trades if t["realized_pnl"] == 0)

    target_hit = sum(1 for t in trades if t["exit_reason"] == "TARGET_HIT")
    stop_loss = sum(1 for t in trades if t["exit_reason"] == "STOP_LOSS")
    conclusive = target_hit + stop_loss
    other = count - conclusive

    net_realized_pnl = round(sum(t["realized_pnl"] for t in trades), 2)

    returns = [
        (t["exit_price"] - t["entry_price"]) / t["entry_price"] * 100
        for t in trades
        if t["entry_price"] and t["entry_price"] > 0 and t["exit_price"] is not None
    ]
    avg_realized_return_pct = round(sum(returns) / len(returns), 2) if returns else None

    return {
        "closed_trade_count": count,
        "win_trades_count": win_trades,
        "win_rate_pct": round(win_trades / count * 100, 1) if count > 0 else None,
        "break_even_count": break_even_trades,
        "target_hit_count": target_hit,
        "stop_loss_count": stop_loss,
        "conclusive_count": conclusive,
        "other_count": other,
        "target_hit_rate_pct": round(target_hit / conclusive * 100, 1) if conclusive > 0 else None,
        "conclusive_rate_pct": round(conclusive / count * 100, 1) if count > 0 else None,
        "net_realized_pnl": net_realized_pnl,
        "avg_realized_return_pct": avg_realized_return_pct,
    }


def _closed_trade_summary_for_market(history_by_horizon: dict) -> dict:
    """Derives the (still-served, backward-compatible) flat summary shape
    from the same per-bucket `summary` already computed inside
    `_closed_trade_history_by_horizon_for_market` — never recomputed
    independently, so the two response fields can never silently disagree."""
    return {key: bucket["summary"] for key, bucket in history_by_horizon.items()}


# ── Modern (include_full_closed_trades=false) path — SQL aggregation ──────────
#
# The single source of truth for "which bucket does this horizon value
# belong to" — reused to build the SQL CASE expression below, so the SQL
# classification can never drift from _bucket_closed_trades_by_horizon's
# Python classification (both are derived from _CLOSED_HORIZONS).
_HORIZON_BUCKET_SQL_CASE = (
    "CASE WHEN horizon IN (" + ", ".join(f"'{h}'" for h in _CLOSED_HORIZONS) + ") "
    "THEN horizon ELSE 'unclassified' END"
)


def _fetch_closed_trade_aggregates(conn, user_id: str) -> list[dict]:
    """One aggregate row per (market, horizon-bucket) that has at least one
    closed trade — at most 2 markets x 4 buckets = 8 rows, regardless of how
    many closed trades actually exist. This is the modern path's replacement
    for materializing every closed trade row just to summarize them: no
    closed-trade row list is ever fetched or held in memory here."""
    sql = f"""
        SELECT market, {_HORIZON_BUCKET_SQL_CASE} AS bucket,
               COUNT(*) AS closed_trade_count,
               COUNT(*) FILTER (WHERE exit_reason = 'TARGET_HIT') AS target_hit_count,
               COUNT(*) FILTER (WHERE exit_reason = 'STOP_LOSS') AS stop_loss_count,
               COUNT(*) FILTER (WHERE (exit_price - entry_price) * quantity > 0) AS win_trades_count,
               COUNT(*) FILTER (WHERE (exit_price - entry_price) * quantity = 0) AS break_even_count,
               COALESCE(SUM((exit_price - entry_price) * quantity), 0) AS net_realized_pnl,
               COALESCE(SUM(entry_price * quantity), 0) AS invested,
               AVG(CASE WHEN entry_price > 0 AND exit_price IS NOT NULL
                        THEN (exit_price - entry_price) / entry_price * 100 END) AS avg_realized_return_pct
        FROM paper_trades
        WHERE user_id = %s AND status = 'CLOSED'
        GROUP BY market, bucket
    """
    rows = conn.execute(sql, (user_id,)).fetchall()
    return [
        {
            "market": r[0], "bucket": r[1],
            "closed_trade_count": r[2], "target_hit_count": r[3], "stop_loss_count": r[4],
            "win_trades_count": r[5], "break_even_count": r[6],
            "net_realized_pnl": round(float(r[7]), 2) if r[7] is not None else 0.0,
            "invested": round(float(r[8]), 2) if r[8] is not None else 0.0,
            "avg_realized_return_pct": round(float(r[9]), 2) if r[9] is not None else None,
        }
        for r in rows
    ]


def _summary_from_aggregate_row(agg: dict) -> dict:
    """Produces the exact same shape/values _summarize_closed_bucket would
    for the same underlying rows, but computed from a pre-aggregated SQL
    result row instead of a materialized trade list. Win Rate here is the
    same ">0" predicate the SQL aggregate query already used to compute
    win_trades_count — never recomputed with a different rule."""
    count = agg["closed_trade_count"]
    win_trades = agg["win_trades_count"]
    target_hit = agg["target_hit_count"]
    stop_loss = agg["stop_loss_count"]
    conclusive = target_hit + stop_loss
    other = count - conclusive
    return {
        "closed_trade_count": count,
        "win_trades_count": win_trades,
        "win_rate_pct": round(win_trades / count * 100, 1) if count > 0 else None,
        "break_even_count": agg["break_even_count"],
        "target_hit_count": target_hit,
        "stop_loss_count": stop_loss,
        "conclusive_count": conclusive,
        "other_count": other,
        "target_hit_rate_pct": round(target_hit / conclusive * 100, 1) if conclusive > 0 else None,
        "conclusive_rate_pct": round(conclusive / count * 100, 1) if count > 0 else None,
        "net_realized_pnl": agg["net_realized_pnl"],
        "avg_realized_return_pct": agg["avg_realized_return_pct"],
    }


def _fetch_latest_closed_trades_per_bucket(conn, user_id: str, limit: int = _CLOSED_HISTORY_INITIAL_LIMIT) -> dict:
    """Latest `limit` closed trades per (market, horizon-bucket), via one
    windowed query — bounded to at most 8 buckets x limit rows regardless of
    total closed-trade count, never a full closed-trade row list."""
    sql = f"""
        SELECT {_TRADE_ROW_COLUMNS} FROM (
            SELECT {_TRADE_ROW_COLUMNS},
                   ROW_NUMBER() OVER (
                       PARTITION BY market, {_HORIZON_BUCKET_SQL_CASE}
                       ORDER BY closed_at DESC, id DESC
                   ) AS rn
            FROM paper_trades
            WHERE user_id = %s AND status = 'CLOSED'
        ) sub
        WHERE rn <= %s
        ORDER BY market, closed_at DESC, id DESC
    """
    rows = conn.execute(sql, (user_id, limit)).fetchall()
    result: dict[str, dict[str, list[dict]]] = {
        m: {h: [] for h in (*_CLOSED_HORIZONS, "unclassified")} for m in ("IN", "US")
    }
    for row in rows:
        trade = _trade_row_to_dict(row)
        bucket = trade["horizon"] if trade["horizon"] in _CLOSED_HORIZONS else "unclassified"
        result[trade["market"]][bucket].append(trade)
    return result


def _build_modern_closed_trade_data(conn, user_id: str) -> tuple[dict, dict, dict, dict]:
    """Returns (closed_trade_history_by_horizon, closed_trade_summary,
    closed_trade_overview_by_market, total_realized_by_market) for the
    include_full_closed_trades=false path — built entirely from bounded
    aggregate/windowed queries (see helpers above), never a materialized
    list of every closed trade."""
    aggregates = _fetch_closed_trade_aggregates(conn, user_id)
    latest_by_market_bucket = _fetch_latest_closed_trades_per_bucket(conn, user_id)
    agg_lookup = {(a["market"], a["bucket"]): a for a in aggregates}

    history_by_horizon: dict = {"IN": {}, "US": {}}
    overview_by_market: dict = {}
    total_realized_by_market = {"IN": 0.0, "US": 0.0}

    for mkt in ("IN", "US"):
        market_count = 0
        market_win_trades = 0
        market_net_pnl = 0.0
        market_invested = 0.0
        for bucket_key in (*_CLOSED_HORIZONS, "unclassified"):
            agg = agg_lookup.get((mkt, bucket_key))
            latest_trades = latest_by_market_bucket[mkt][bucket_key]
            if agg is None:
                bucket_result = {"summary": _summarize_closed_bucket([]), "latest_trades": [], "earlier_trade_count": 0}
            else:
                summary = _summary_from_aggregate_row(agg)
                bucket_result = {
                    "summary": summary,
                    "latest_trades": latest_trades,
                    "earlier_trade_count": max(0, agg["closed_trade_count"] - len(latest_trades)),
                }
                market_count += agg["closed_trade_count"]
                market_win_trades += agg["win_trades_count"]
                market_net_pnl += agg["net_realized_pnl"]
                market_invested += agg["invested"]
            # Official horizons always present (possibly zero-count);
            # "unclassified" only when at least one such trade actually exists.
            if bucket_key != "unclassified" or bucket_result["summary"]["closed_trade_count"] > 0:
                history_by_horizon[mkt][bucket_key] = bucket_result

        total_realized_by_market[mkt] = round(market_net_pnl, 2)
        overview_by_market[mkt] = {
            "closed_trade_count": market_count,
            "win_trades_count": market_win_trades,
            "win_rate_pct": round(market_win_trades / market_count * 100, 1) if market_count > 0 else None,
            "total_invested": round(market_invested, 2),
        }

    closed_trade_summary = {
        mkt: _closed_trade_summary_for_market(history_by_horizon[mkt]) for mkt in ("IN", "US")
    }
    return history_by_horizon, closed_trade_summary, overview_by_market, total_realized_by_market


def _overview_from_closed_trades(closed_trades: list[dict]) -> dict:
    """Same compact overview shape as _build_modern_closed_trade_data's, but
    derived from an already-materialized closed_trades list (the legacy
    include_full_closed_trades=true path already has one in memory, so this
    is a cheap in-memory aggregation, not a second query)."""
    overview_by_market = {}
    for mkt in ("IN", "US"):
        trades = [t for t in closed_trades if t["market"] == mkt]
        count = len(trades)
        win_trades = sum(1 for t in trades if (t.get("realized_pnl") or 0) > 0)
        overview_by_market[mkt] = {
            "closed_trade_count": count,
            "win_trades_count": win_trades,
            "win_rate_pct": round(win_trades / count * 100, 1) if count > 0 else None,
            "total_invested": round(sum(t["invested"] for t in trades), 2),
        }
    return overview_by_market


# Every request handler below used to call psycopg.connect() directly —
# a fresh TCP+TLS handshake to Postgres per call, and most handlers (e.g.
# get_portfolio) call it twice (_ensure_portfolio, then the handler body),
# adding real, measurable latency to every Paper Trading page load. A
# process-wide pool (psycopg[pool] is already a declared dependency) hands
# out an already-established connection instead, opening new ones only
# when every pooled connection is busy.
_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                from psycopg_pool import ConnectionPool
                _pool = ConnectionPool(
                    os.environ["DATABASE_URL"],
                    min_size=1,
                    max_size=10,
                    kwargs={"autocommit": True, "prepare_threshold": None},
                )
    return _pool


def _conn():
    return _get_pool().connection()


def _ensure_portfolio(user_id: str, email: str | None = None) -> dict:
    with _conn() as conn:
        row = conn.execute(
            "SELECT cash, cash_usd, email_notifications_enabled FROM paper_portfolio WHERE user_id = %s",
            (user_id,)
        ).fetchone()
        if row is None:
            # ON CONFLICT DO NOTHING — two concurrent first-time requests (e.g.
            # /portfolio and /buy both firing on first login) could otherwise
            # both see no row, both INSERT, and the second hit the user_id
            # UNIQUE constraint as an unhandled 500.
            conn.execute(
                """INSERT INTO paper_portfolio (session_id, user_id, cash, cash_usd, email)
                   VALUES (%s, %s, %s, %s, %s) ON CONFLICT (user_id) DO NOTHING""",
                (user_id, user_id, STARTING_CASH_IN, STARTING_CASH_US, email)
            )
            row = conn.execute(
                "SELECT cash, cash_usd, email_notifications_enabled FROM paper_portfolio WHERE user_id = %s",
                (user_id,)
            ).fetchone()
        if email:
            # Keep email fresh — cheap to update on every call, no extra round trip
            conn.execute(
                "UPDATE paper_portfolio SET email = %s WHERE user_id = %s AND (email IS DISTINCT FROM %s)",
                (email, user_id, email)
            )
        return {"cash": row[0], "cash_usd": row[1], "email_notifications_enabled": row[2]}


# ── Models ────────────────────────────────────────────────────────────────────

# Migration-verification hardening gate — a plain `price: float` (or
# `stop_loss`/`target_price`/`entry_price`) Pydantic field accepts NaN and
# ±Infinity by default (Pydantic does not reject non-finite floats unless
# told to). `if req.price <= 0` does NOT catch this either — `float('nan')
# <= 0` is `False` in Python — so a NaN price previously passed every
# existing guard and reached the debit UPDATE / INSERT with a literal NaN
# bound parameter. Reused as a `field_validator` on every money-relevant
# float field across BuyRequest/SellRequest/EditRequest, the same
# finite-number check `EntryEvidenceRequest` already applies to its own
# fields (see its `_validate_evidence` validator above).
def _reject_non_finite(value: float | None) -> float | None:
    if value is not None and not math.isfinite(value):
        raise ValueError("must be a finite number")
    return value


# Trade Postmortem Engine, Stage 2 — freshness bounds for
# `EntryEvidenceRequest.recommendation_generated_at`. Not a financial
# scoring threshold (SES-002 §1's thresholds.py registry is for
# accept/reject gates on a stock's fundamentals/technicals) — this is an
# input-validation freshness bound on a client-reported timestamp, a
# different category, so it stays local to this module rather than being
# migrated into that registry.
_MAX_FUTURE_SKEW = timedelta(minutes=5)
_MAX_EVIDENCE_STALENESS = timedelta(hours=48)


class EntryEvidenceRequest(BaseModel):
    """Trade Postmortem Engine, Stage 2 — optional, richer entry-time
    evidence reported alongside a Buy order. Every field here is
    CLIENT_REPORTED, never SERVER_VERIFIED (see
    services/postmortem/entry_snapshot.py's module docstring — no
    server-side lookup path exists yet to independently corroborate any of
    these values). The backend range/finiteness/freshness-validates but
    does not cross-check them against a stored recommendation.

    Omitting this object entirely (old clients, or a genuinely manual trade
    with no recommendation behind it) is fully supported — see
    `BuyRequest.evidence_source` — and produces an immutable
    limited-evidence snapshot, not a rejected request.
    """

    # Migration-verification hardening gate, Part 10 — every free-text field
    # is length-bounded. None of these are meant to hold more than a short
    # label/enum value or a brief reasoning sentence; an unbounded string
    # (or the loosely-typed `recommendation_reasoning` list below) could
    # otherwise let a buggy or malicious client store an arbitrarily large
    # JSONB payload per trade with no schema-level ceiling.
    recommendation_signal: str | None = Field(default=None, max_length=64)
    recommendation_generated_at: str | None = None
    recommendation_reference_price: float | None = None
    recommendation_entry_low: float | None = None
    recommendation_entry_high: float | None = None
    recommended_stop_loss: float | None = None
    recommended_target_price: float | None = None
    confidence_score: float | None = None
    technical_signal: str | None = Field(default=None, max_length=64)
    technical_rsi: float | None = None
    technical_macd_diff: float | None = None
    fundamental_score: float | None = None
    sentiment_score: float | None = None
    sentiment_label: str | None = Field(default=None, max_length=64)
    market_regime_trend: str | None = Field(default=None, max_length=64)
    market_regime_score_adj: float | None = None
    market_regime_reason: str | None = Field(default=None, max_length=2000)
    recommendation_reasoning: list[dict] | None = Field(default=None, max_length=50)
    daily_pick_run_id: str | None = Field(default=None, max_length=128)
    daily_pick_rank: int | None = None
    model_version: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _validate_evidence(self):
        finite_fields = (
            "recommendation_reference_price", "recommendation_entry_low", "recommendation_entry_high",
            "recommended_stop_loss", "recommended_target_price", "confidence_score",
            "technical_rsi", "technical_macd_diff", "fundamental_score", "sentiment_score",
            "market_regime_score_adj",
        )
        for name in finite_fields:
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"entry_evidence.{name} must be a finite number")

        if (
            self.recommendation_entry_low is not None
            and self.recommendation_entry_high is not None
            and self.recommendation_entry_low > self.recommendation_entry_high
        ):
            raise ValueError("entry_evidence.recommendation_entry_low must not exceed recommendation_entry_high")

        if self.confidence_score is not None and not (0.0 <= self.confidence_score <= 100.0):
            raise ValueError("entry_evidence.confidence_score must be between 0 and 100")

        if self.recommendation_generated_at is not None:
            try:
                ts = datetime.fromisoformat(self.recommendation_generated_at.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError("entry_evidence.recommendation_generated_at must be a valid ISO-8601 timestamp")
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if ts > now + _MAX_FUTURE_SKEW:
                raise ValueError("entry_evidence.recommendation_generated_at is impossibly far in the future")
            if ts < now - _MAX_EVIDENCE_STALENESS:
                raise ValueError("entry_evidence.recommendation_generated_at is stale beyond the accepted freshness window")

        if self.recommendation_reasoning is not None:
            # `Field(max_length=50)` above already bounds the list length;
            # this bounds each item's own size, since the field is a
            # loosely-typed passthrough (list[dict]) with no per-key schema.
            serialized = json.dumps(self.recommendation_reasoning)
            if len(serialized) > 20_000:
                raise ValueError("entry_evidence.recommendation_reasoning is too large")

        return self


class BuyRequest(BaseModel):
    symbol: str
    market: Literal["IN", "US"]
    quantity: int
    price: float
    signal: str = "HOLD"
    horizon: str = "medium"
    stop_loss: float | None = None
    target_price: float | None = None

    @field_validator("price", "stop_loss", "target_price")
    @classmethod
    def _validate_finite(cls, v):
        return _reject_non_finite(v)

    # "ai_assisted" is accepted (not rejected) so a trade opened while that
    # option is visible-but-disabled in the UI can't ever reach the backend
    # today — validated here anyway since no client currently sends it.
    trade_management_mode: Literal["manual", "auto", "ai_assisted"] = "manual"

    # Learning Alpha Engine remediation, Phase 1 — paper-trade provenance
    # foundation. All optional/nullable: no client sends these yet, so every
    # trade opened today still stores NULL for all of them, same as a
    # legacy row — genuinely unknown provenance, never guessed. A future
    # "Buy from Daily Pick" UI flow can start supplying them without any
    # further backend change.
    recommendation_source: str | None = None
    daily_pick_run_id: str | None = None
    daily_pick_rank: int | None = None
    recommendation_generated_at: str | None = None
    recommendation_reference_price: float | None = None
    recommendation_entry_low: float | None = None
    recommendation_entry_high: float | None = None
    recommendation_original_stop_loss: float | None = None
    recommendation_original_target: float | None = None
    model_version: str | None = None

    # Trade Postmortem Engine, Stage 2 — immutable entry-evidence capture.
    # Defaults preserve full backward compatibility: a request that omits
    # both fields (every existing caller/test) is treated as evidence_source
    # "MANUAL" with no entry_evidence — a real, honest classification (not a
    # placeholder), matching Part 8's requirement that a trade with no
    # recommendation behind it must still succeed.
    evidence_source: Literal["MANUAL", "SCREENER", "DAILY_PICK", "RESEARCH"] = "MANUAL"
    entry_evidence: EntryEvidenceRequest | None = None

    # Migration-verification hardening gate, Part 7 — Buy idempotency.
    # Optional for backward compatibility: an old client that omits this
    # entirely gets exactly today's behavior (no dedup guarantee at all,
    # same as before this field existed) — see paper_buy's
    # `idempotency_enforced` flag in its own response. A client that DOES
    # supply one gets the full durable, exactly-once guarantee described in
    # services/postmortem/idempotency.py.
    idempotency_key: str | None = None

    @field_validator("idempotency_key")
    @classmethod
    def _validate_idempotency_key(cls, v):
        if v is not None:
            try:
                validate_idempotency_key_format(v)
            except ValueError as e:
                raise ValueError(str(e))
        return v


class SellRequest(BaseModel):
    price: float
    # Set by the client when a close was triggered by an auto-close rule
    # (stop-loss/target hit) rather than a manual "Close" click — omitted
    # (None) for an ordinary manual close.
    exit_reason: Literal["STOP_LOSS", "TARGET_HIT", "MANUAL"] | None = None

    # Trade Postmortem Engine, Sprint 2 correction phase (Stage 6) — close
    # idempotency, additive and backward compatible: an old client that
    # omits this gets exactly today's behavior (no response-loss dedup
    # guarantee, same as before this field existed — see paper_sell's own
    # `idempotency_enforced` flag in its response). Mirrors BuyRequest's
    # own idempotency_key field/validator exactly (same format rules, same
    # (user_id, operation_type, idempotency_key) uniqueness contract, a
    # DISTINCT operation_type so a Buy key and a Sell key never collide).
    idempotency_key: str | None = None

    @field_validator("price")
    @classmethod
    def _validate_finite(cls, v):
        return _reject_non_finite(v)

    @field_validator("idempotency_key")
    @classmethod
    def _validate_idempotency_key(cls, v):
        if v is not None:
            try:
                validate_idempotency_key_format(v)
            except ValueError as e:
                raise ValueError(str(e))
        return v


class EditRequest(BaseModel):
    stop_loss: float | None = None
    target_price: float | None = None
    entry_price: float | None = None

    @field_validator("stop_loss", "target_price", "entry_price")
    @classmethod
    def _validate_finite(cls, v):
        return _reject_non_finite(v)


class ManagementModeRequest(BaseModel):
    # "ai_assisted" is intentionally excluded from this Literal — it isn't a
    # rejected-at-runtime value like BuyRequest's, it simply isn't an option
    # this endpoint accepts at all yet, since it has no functional behavior.
    trade_management_mode: Literal["manual", "auto"]


class NotificationPreferenceRequest(BaseModel):
    email_notifications_enabled: bool


# Trade Postmortem Engine, Phase 1 — response schema for
# GET /postmortem/{trade_id}. `schema_version` tracks this JSON *shape*
# (bump when a field is added/removed/renamed); `calculation_version`
# (nested, from services.postmortem.deterministic.CALCULATION_VERSION)
# tracks the *rules* used to compute the values — the two can change
# independently.
POSTMORTEM_SCHEMA_VERSION = "1.0.0"


class PostmortemResponse(BaseModel):
    schema_version: str
    trade_id: int
    status: str
    outcome: str
    realized_pnl_abs: float | None
    realized_pnl_pct: float | None
    holding_duration_seconds: float | None
    exit_mechanism: str
    exit_mechanism_raw: str | None
    trade_management_mode: str
    auto_close_timing_evidence: str
    evidence_completeness: str
    available_evidence_fields: list[str]
    missing_evidence_fields: list[str]
    target_distance_at_exit_pct: float | None
    stop_distance_at_exit_pct: float | None
    calculation_version: str
    warnings: list[str]
    # Trade Postmortem Engine, Stage 2 — `None` for a trade with no entry
    # snapshot (every historical pre-Stage-2 trade, or a trade opened via
    # the old flat-field-only API contract with no entry_evidence) — not a
    # missing/failed field, an honest "no snapshot exists" signal.
    snapshot_schema_version: str | None = None
    evidence_source: str | None = None
    verification_levels: dict[str, str] | None = None


# Column order must match ClosedTradeRecord's field order exactly (excluding
# `user_id`, fetched alongside for the ownership check but not part of the
# typed record itself).
_POSTMORTEM_ROW_COLUMNS = (
    "id, user_id, status, symbol, market, quantity, entry_price, exit_price, "
    "stop_loss, target_price, opened_at, closed_at, trade_management_mode, exit_reason, "
    "recommendation_source, daily_pick_run_id, daily_pick_rank, "
    "recommendation_generated_at, recommendation_reference_price, "
    "recommendation_entry_low, recommendation_entry_high, "
    "recommendation_original_stop_loss, recommendation_original_target, "
    "model_version, execution_slippage_pct"
)


# Column order must match EntrySnapshot's field order exactly (see
# services/postmortem/entry_snapshot.py) — kept as one named tuple so the
# INSERT below and any future SELECT of this table share one canonical
# column list, the same pattern _TRADE_ROW_COLUMNS/_POSTMORTEM_ROW_COLUMNS
# already use for paper_trades.
_ENTRY_SNAPSHOT_COLUMNS = (
    "paper_trade_id, user_id, symbol, market, snapshot_schema_version, "
    "evidence_source, daily_pick_run_id, daily_pick_rank, recommendation_signal, "
    "recommendation_generated_at, recommendation_reference_price, "
    "recommendation_entry_low, recommendation_entry_high, "
    "simulated_execution_price, execution_slippage_pct, execution_range_position, "
    "recommended_stop_loss, recommended_target_price, "
    "user_selected_stop_loss, user_selected_target_price, "
    "user_overrode_recommendation, reward_to_risk_ratio, "
    "confidence_score, technical_signal, technical_rsi, technical_macd_diff, "
    "fundamental_score, sentiment_score, sentiment_label, "
    "market_regime_trend, market_regime_score_adj, market_regime_reason, "
    "recommendation_reasoning, model_version, verification_levels"
)


def _parse_entry_evidence_timestamp(value: str | None):
    """Same parsing `EntryEvidenceRequest`'s validator already accepted —
    re-parsed here (rather than threading the parsed value through Pydantic)
    since Pydantic request models store the original string. Never raises:
    the validator already rejected anything this can't parse before the
    request reached this handler."""
    if value is None:
        return None
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _build_snapshot_for_buy(*, trade_id: int, user_id: str, symbol: str, market: str, req: "BuyRequest") -> EntrySnapshot:
    """Builds the immutable entry snapshot for a newly created trade from
    the Buy request — pure construction, no I/O (see
    services/postmortem/entry_snapshot.py for the calculation logic this
    delegates to). `req.entry_evidence` omitted (old clients, or a
    genuinely manual trade) produces a real "no recommendation evidence was
    reported" snapshot, not a missing one — see RecommendationContext's
    defaults, all `None`."""
    evidence = req.entry_evidence
    ctx = RecommendationContext(
        evidence_source=req.evidence_source,
        simulated_execution_price=req.price,
        user_selected_stop_loss=req.stop_loss,
        user_selected_target_price=req.target_price,
        recommendation_signal=evidence.recommendation_signal if evidence else None,
        recommendation_generated_at=_parse_entry_evidence_timestamp(evidence.recommendation_generated_at) if evidence else None,
        recommendation_reference_price=evidence.recommendation_reference_price if evidence else None,
        recommendation_entry_low=evidence.recommendation_entry_low if evidence else None,
        recommendation_entry_high=evidence.recommendation_entry_high if evidence else None,
        recommended_stop_loss=evidence.recommended_stop_loss if evidence else None,
        recommended_target_price=evidence.recommended_target_price if evidence else None,
        confidence_score=evidence.confidence_score if evidence else None,
        technical_signal=evidence.technical_signal if evidence else None,
        technical_rsi=evidence.technical_rsi if evidence else None,
        technical_macd_diff=evidence.technical_macd_diff if evidence else None,
        fundamental_score=evidence.fundamental_score if evidence else None,
        sentiment_score=evidence.sentiment_score if evidence else None,
        sentiment_label=evidence.sentiment_label if evidence else None,
        market_regime_trend=evidence.market_regime_trend if evidence else None,
        market_regime_score_adj=evidence.market_regime_score_adj if evidence else None,
        market_regime_reason=evidence.market_regime_reason if evidence else None,
        recommendation_reasoning=evidence.recommendation_reasoning if evidence else None,
        daily_pick_run_id=evidence.daily_pick_run_id if evidence else None,
        daily_pick_rank=evidence.daily_pick_rank if evidence else None,
        model_version=evidence.model_version if evidence else None,
    )

    # Defensive invariant (Part 3/Part 9): a snapshot must describe the same
    # trade it's linked to — trivially true here since symbol/market/price
    # all come from the same request that just created this exact trade_id,
    # but asserted rather than assumed, so a future refactor that ever
    # threads a snapshot from a different call site trips this immediately
    # instead of silently persisting mismatched evidence.
    if not isinstance(trade_id, int) or trade_id <= 0:
        raise ValueError(f"invalid trade_id for entry snapshot: {trade_id!r}")

    return build_entry_snapshot(paper_trade_id=trade_id, user_id=user_id, symbol=symbol, market=market, ctx=ctx)


def _json_field(value):
    """Defensive read-side JSONB handling matching postgres_store.py's
    load_picks_from_db precedent: psycopg3 auto-deserializes JSONB to a
    Python object in the common case, but this is defensive against a raw
    string ever coming back instead."""
    if value is None or isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _fetch_entry_snapshot(conn, trade_id: int) -> EntrySnapshot | None:
    row = conn.execute(
        f"SELECT {_ENTRY_SNAPSHOT_COLUMNS} FROM paper_trade_entry_snapshot WHERE paper_trade_id = %s",
        (trade_id,)
    ).fetchone()
    if row is None:
        return None
    (ptid, uid, sym, mkt, schema_version, evidence_source, pick_run_id, pick_rank, rec_signal,
     rec_gen_at, rec_ref_price, rec_entry_low, rec_entry_high,
     sim_price, slippage_pct, range_position,
     rec_stop, rec_target, user_stop, user_target,
     overrode, rr_ratio,
     confidence, tech_signal, tech_rsi, tech_macd,
     fund_score, sent_score, sent_label,
     regime_trend, regime_adj, regime_reason,
     reasoning, model_version, verification_levels) = row
    return EntrySnapshot(
        paper_trade_id=ptid, user_id=uid, symbol=sym, market=mkt,
        snapshot_schema_version=schema_version, evidence_source=evidence_source,
        daily_pick_run_id=pick_run_id, daily_pick_rank=pick_rank,
        recommendation_signal=rec_signal, recommendation_generated_at=rec_gen_at,
        recommendation_reference_price=rec_ref_price,
        recommendation_entry_low=rec_entry_low, recommendation_entry_high=rec_entry_high,
        simulated_execution_price=sim_price, execution_slippage_pct=slippage_pct,
        execution_range_position=range_position,
        recommended_stop_loss=rec_stop, recommended_target_price=rec_target,
        user_selected_stop_loss=user_stop, user_selected_target_price=user_target,
        user_overrode_recommendation=overrode, reward_to_risk_ratio=rr_ratio,
        confidence_score=confidence, technical_signal=tech_signal,
        technical_rsi=tech_rsi, technical_macd_diff=tech_macd,
        fundamental_score=fund_score, sentiment_score=sent_score, sentiment_label=sent_label,
        market_regime_trend=regime_trend, market_regime_score_adj=regime_adj, market_regime_reason=regime_reason,
        recommendation_reasoning=_json_field(reasoning), model_version=model_version,
        verification_levels=_json_field(verification_levels) or {},
    )


def _insert_entry_snapshot(conn, snapshot: EntrySnapshot) -> None:
    conn.execute(
        f"""INSERT INTO paper_trade_entry_snapshot ({_ENTRY_SNAPSHOT_COLUMNS})
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            snapshot.paper_trade_id, snapshot.user_id, snapshot.symbol, snapshot.market,
            snapshot.snapshot_schema_version, snapshot.evidence_source,
            snapshot.daily_pick_run_id, snapshot.daily_pick_rank, snapshot.recommendation_signal,
            snapshot.recommendation_generated_at, snapshot.recommendation_reference_price,
            snapshot.recommendation_entry_low, snapshot.recommendation_entry_high,
            snapshot.simulated_execution_price, snapshot.execution_slippage_pct, snapshot.execution_range_position,
            snapshot.recommended_stop_loss, snapshot.recommended_target_price,
            snapshot.user_selected_stop_loss, snapshot.user_selected_target_price,
            snapshot.user_overrode_recommendation, snapshot.reward_to_risk_ratio,
            snapshot.confidence_score, snapshot.technical_signal, snapshot.technical_rsi, snapshot.technical_macd_diff,
            snapshot.fundamental_score, snapshot.sentiment_score, snapshot.sentiment_label,
            snapshot.market_regime_trend, snapshot.market_regime_score_adj, snapshot.market_regime_reason,
            json.dumps(snapshot.recommendation_reasoning) if snapshot.recommendation_reasoning is not None else None,
            snapshot.model_version,
            json.dumps(snapshot.verification_levels),
        )
    )


def _exit_snapshot_exists(conn, trade_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM paper_trade_exit_snapshot WHERE paper_trade_id = %s", (trade_id,)
    ).fetchone()
    return row is not None


def _fetch_exit_snapshot(conn, trade_id: int) -> ExitSnapshot | None:
    """Trade Postmortem Engine, Sprint 2 correction phase, Stage 4 — the
    typed counterpart to _exit_snapshot_exists (kept, still used where
    only presence matters). Column order reuses close_service's own
    _EXIT_SNAPSHOT_COLUMNS as the single source of truth (the same list
    the INSERT is built from — see that module's Stage 1 correction
    comment), so this SELECT and that INSERT can never drift apart."""
    row = conn.execute(
        f"SELECT {', '.join(_EXIT_SNAPSHOT_DB_COLUMNS)} FROM paper_trade_exit_snapshot WHERE paper_trade_id = %s",
        (trade_id,)
    ).fetchone()
    if row is None:
        return None
    values = list(row)
    values[_EXIT_SNAPSHOT_DB_COLUMNS.index("source_metadata")] = _json_field(
        values[_EXIT_SNAPSHOT_DB_COLUMNS.index("source_metadata")]
    )
    return ExitSnapshot(*values)


# Trade Postmortem Engine, Sprint 2 correction phase — Stage 8/3
# close-to-report behavior, rewritten around outbox.py's lease-based
# claim/mark contract (see that module's docstring for the full design).
_GENERATION_ERROR_CODE = "GENERATION_ERROR"

# Stable, machine-readable status returned by POST /generate when another
# valid (non-expired) lease currently owns the outbox row — never runs a
# concurrent duplicate generation attempt.
REPORT_GENERATION_IN_PROGRESS = "GENERATION_IN_PROGRESS"


def _run_claimed_generation(conn, *, user_id: str, trade_id: int, outbox_id: int, claimed_by: str):
    """Runs generation for an outbox row THIS caller has already claimed
    (status = GENERATING, claimed_by = `claimed_by`). Returns the
    PersistedReport on success. Any exception raised here (including
    generation_service.StaleLeaseError) is caught by the caller and
    turned into a FAILED_RETRYABLE mark using a FRESH connection and the
    SAME claimed_by token — this function itself never swallows an
    exception, so its caller always knows whether the attempt truly
    reached a terminal/retryable state before returning."""
    log.info("[postmortem_generation_started] trade_id=%s outbox_id=%s", trade_id, outbox_id)
    row = conn.execute(
        f"SELECT {_POSTMORTEM_ROW_COLUMNS} FROM paper_trades WHERE id = %s", (trade_id,)
    ).fetchone()
    if row is None:
        log.warning("[postmortem_generation_terminal_failure] trade_id=%s outbox_id=%s error_code=%s",
                    trade_id, outbox_id, "TRADE_MISSING")
        outbox_ops.mark_terminal_failure(
            conn, outbox_id=outbox_id, error_code="TRADE_MISSING",
            error_summary="trade row not found at generation time", claimed_by=claimed_by,
        )
        return None
    record, _owner = _row_to_closed_trade_record(row)
    snapshot = _fetch_entry_snapshot(conn, trade_id)
    exit_snapshot = _fetch_exit_snapshot(conn, trade_id)
    postmortem = compute_postmortem(record, snapshot=snapshot)
    tz = _REPORT_TIMEZONE.get(record.market)
    trading_date = record.closed_at.astimezone(tz).date() if (tz and record.closed_at) else None
    if trading_date is None:
        log.warning("[postmortem_generation_retryable_failure] trade_id=%s outbox_id=%s error_code=%s",
                    trade_id, outbox_id, "NO_TRADING_DATE")
        outbox_ops.mark_retryable_failure(
            conn, outbox_id=outbox_id, error_code="NO_TRADING_DATE",
            error_summary="could not resolve a market-local trading date", claimed_by=claimed_by,
        )
        return None
    market_timezone = "Asia/Kolkata" if record.market == "IN" else "America/New_York"
    with conn.transaction():
        report, _created = generation_service.generate_and_persist(
            conn, trade_id=trade_id, user_id=user_id, market=record.market,
            report_trading_date=trading_date, market_timezone=market_timezone,
            postmortem=postmortem, entry_snapshot=snapshot, exit_snapshot=exit_snapshot,
            outbox_id=outbox_id, claimed_by=claimed_by,
        )
    event = "[postmortem_generation_limited]" if report.status == "LIMITED_EVIDENCE" else "[postmortem_generation_completed]"
    log.info("%s trade_id=%s outbox_id=%s status=%s", event, trade_id, outbox_id, report.status)
    return report


def _claim_and_run_generation(conn, *, user_id: str, trade_id: int, outbox_id: int):
    """Claims `outbox_id` (lease-safe — reclaims a PENDING, eligible
    FAILED_RETRYABLE, or expired-lease GENERATING row) and, if the claim
    actually put THIS caller in GENERATING (as opposed to settling the
    row FAILED_TERMINAL for exceeding the attempt limit, or matching
    nothing because another valid lease already owns it), runs
    generation. Returns one of:

    - (PersistedReport, "GENERATED") — a report was produced this call.
    - (None, "IN_PROGRESS") — another valid, non-expired lease currently
      owns the row; the caller must not attempt concurrent generation.
    - (None, "TERMINAL_NO_REPORT") — the claim itself settled the row
      FAILED_TERMINAL (attempt limit exceeded) without ever generating.
    - (None, "NOTHING_TO_DO") — the row is already COMPLETE/LIMITED_
      EVIDENCE (a caller should have already checked for and returned
      the persisted report before calling this — reaching here is a
      benign race, not an error).

    On any exception during generation, marks the row FAILED_RETRYABLE
    (using the SAME claimed_by token, so a lease already lost to a
    reclaimer safely no-ops rather than overwriting the new claimant) and
    re-raises nothing — the caller only ever sees the tuple contract
    above; a generation failure surfaces as (None, "IN_PROGRESS"-shaped
    retry state), never an unhandled exception."""
    claimant = outbox_ops.new_claimant_token()
    claimed = outbox_ops.claim_next_attempt(conn, outbox_id=outbox_id, user_id=user_id, claimant=claimant)
    if claimed is None:
        return None, "IN_PROGRESS"
    if claimed.status == "FAILED_TERMINAL":
        return None, "TERMINAL_NO_REPORT"
    if claimed.status != "GENERATING":
        return None, "NOTHING_TO_DO"

    try:
        report = _run_claimed_generation(
            conn, user_id=user_id, trade_id=trade_id, outbox_id=outbox_id, claimed_by=claimant
        )
    except Exception as exc:
        # Privacy-safe: logs only the exception's class name, never its
        # message, user data, evidence values, or report narrative.
        log.warning("[postmortem_generation_retryable_failure] trade_id=%s outbox_id=%s error_code=%s",
                    trade_id, outbox_id, type(exc).__name__)
        outbox_ops.mark_retryable_failure(
            conn, outbox_id=outbox_id, error_code=_GENERATION_ERROR_CODE,
            error_summary=type(exc).__name__, claimed_by=claimant,
        )
        return None, "IN_PROGRESS"
    if report is None:
        return None, "IN_PROGRESS"
    return report, "GENERATED"


def _attempt_best_effort_generation(*, user_id: str, trade_id: int, outbox_id: int) -> None:
    """Best-effort immediate post-commit generation, per Stage 8's own
    explicit rule: 'a generation failure must never undo a valid trade
    close' and 'do not block trade close on external network services'.
    Called strictly AFTER the close transaction has already committed —
    never allowed to turn this endpoint's response into an error, never a
    500 for what is otherwise a successful sell.

    Crash-safety: the lease-based claim in `_claim_and_run_generation`
    means even a hard process kill between claim and mark no longer
    leaves this row stuck forever — it becomes reclaimable again once its
    lease (outbox.LEASE_DURATION_SECONDS) expires, via a later best-effort
    attempt on a subsequent close, or via POST /postmortem/{trade_id}/
    generate. The claim UPDATE itself is a single autocommitted statement
    (this pool's connections default to autocommit — never wrapped in the
    same `with conn.transaction():` block as generation/persistence), so
    it is durably visible to any other connection the instant it commits,
    independent of whether generation afterward ever completes."""
    try:
        with _conn() as conn:
            _claim_and_run_generation(conn, user_id=user_id, trade_id=trade_id, outbox_id=outbox_id)
    except Exception:
        log.warning("[postmortem_generation] best-effort generation attempt failed: %s", "unexpected_error")


# Trade Postmortem Sprint 3A, Stage H2 durable-lifecycle correction —
# the price-path attempt is now a genuine leased outbox lifecycle, not
# an unleased best-effort helper. Stable status vocabulary this
# function returns as the second element of its (report, status) tuple:
PRICE_PATH_GENERATED = "PRICE_PATH_GENERATED"
PRICE_PATH_ALREADY_COMPLETE = "PRICE_PATH_ALREADY_COMPLETE"
PRICE_PATH_GENERATION_IN_PROGRESS = "PRICE_PATH_GENERATION_IN_PROGRESS"
PRICE_PATH_FAILED_RETRYABLE = "PRICE_PATH_FAILED_RETRYABLE"
PRICE_PATH_FAILED_TERMINAL = "PRICE_PATH_FAILED_TERMINAL"
PRICE_PATH_NOT_YET_AVAILABLE = "PRICE_PATH_NOT_YET_AVAILABLE"  # no prior Sprint 2 report to enhance yet


def _price_path_target_identity() -> tuple[str, str, str]:
    """The Sprint 3A target report identity, computable BEFORE
    acquisition (never after) — price_path_acquisition.SOURCE_VERSION is
    a static module-level constant for the lifetime of one deployment,
    so the calculation_version suffix it produces is exactly the one any
    successful acquisition under this deployment will also embed into
    its evidence's own source_version field. This is what lets the
    outbox row's requested_calculation_version be fixed at claim time,
    matching Sprint 2's own static-identity convention exactly."""
    from services.postmortem.price_path_acquisition import SOURCE_VERSION as PP_SOURCE_VERSION
    return (
        price_path_generation.PRICE_PATH_REPORT_SCHEMA_VERSION,
        price_path_generation.price_path_calculation_suffix(PP_SOURCE_VERSION),
        ATTRIBUTION_RULES_VERSION,
    )


def _insert_price_path_outbox_record(conn, *, trade_id: int, user_id: str, source_request_id: str | None = None) -> tuple[int, bool]:
    """Same idempotent-insert pattern as close_service._insert_outbox_
    record, against the SAME paper_trade_postmortem_outbox table and the
    SAME (paper_trade_id, requested_report_schema_version,
    requested_calculation_version, requested_rules_version) unique
    index — that index already supports more than one row per trade
    whenever the version triple differs, so the Sprint 3A price-path
    identity gets its own row without any schema change. The Sprint 2
    outbox row (and report) are never touched by this function."""
    schema_v, calc_v, rules_v = _price_path_target_identity()
    row = conn.execute(
        """INSERT INTO paper_trade_postmortem_outbox
               (paper_trade_id, user_id, requested_report_schema_version,
                requested_calculation_version, requested_rules_version, source_request_id)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (paper_trade_id, requested_report_schema_version,
                        requested_calculation_version, requested_rules_version)
           DO NOTHING
           RETURNING id""",
        (trade_id, user_id, schema_v, calc_v, rules_v, source_request_id),
    ).fetchone()
    if row is not None:
        return row[0], True
    existing = conn.execute(
        """SELECT id FROM paper_trade_postmortem_outbox
           WHERE paper_trade_id = %s AND requested_report_schema_version = %s
             AND requested_calculation_version = %s AND requested_rules_version = %s""",
        (trade_id, schema_v, calc_v, rules_v),
    ).fetchone()
    return existing[0], False


_PRICE_PATH_ERROR_CODE = "PRICE_PATH_GENERATION_ERROR"


def _attempt_price_path_enhancement(
    *, user_id: str, trade_id: int,
    fetch_bars_fn=None, fetch_splits_fn=None, fetch_dividends_fn=None,
) -> tuple[object | None, str]:
    """Trade Postmortem Sprint 3A, Stage H2 durable-lifecycle correction.

    Returns (report_or_None, status) where status is one of the
    PRICE_PATH_* constants above. Uses its OWN versioned outbox row
    (see _insert_price_path_outbox_record) claimed through the exact
    same lease-safe outbox_ops.claim_next_attempt/mark_terminal/
    mark_retryable_failure machinery Sprint 2's own generation already
    relies on — atomic claim, claimant-token stale-worker protection,
    retry backoff, and an attempt limit all come from that shared,
    already-tested module, not reimplemented here.

    Phase boundaries (Stage H1's five-phase design) are preserved
    exactly: Phase 1/2 (read + claim) and Phase 5/6 (short writes) each
    get their OWN `with _conn():` block; provider acquisition runs
    entirely between them, with no database connection in scope at all.
    fetch_bars_fn/fetch_splits_fn/fetch_dividends_fn default to the real
    network-calling functions when not supplied (production behavior) —
    tests inject fakes instead of monkeypatching module internals.

    A genuinely unexpected exception is still never allowed to reach the
    caller (matching _attempt_best_effort_generation's own contract) —
    it is classified PRICE_PATH_FAILED_RETRYABLE where a claim was held,
    logged with only its class name (never raw exception text), and the
    lease is marked retryable so a later attempt can reclaim it rather
    than the row sitting stuck."""
    from services.postmortem.price_path_acquisition import (
        PriceProviderAcquisitionError, fetch_dividend_events, fetch_raw_daily_bars, fetch_split_events,
    )
    fetch_bars_fn = fetch_bars_fn or fetch_raw_daily_bars
    fetch_splits_fn = fetch_splits_fn or fetch_split_events
    fetch_dividends_fn = fetch_dividends_fn or fetch_dividend_events

    outbox_id = None
    claimant = None
    try:
        with _conn() as conn:
            row = conn.execute(
                f"SELECT {_POSTMORTEM_ROW_COLUMNS} FROM paper_trades WHERE id = %s", (trade_id,)
            ).fetchone()
            if row is None:
                return None, PRICE_PATH_NOT_YET_AVAILABLE
            record, owner = _row_to_closed_trade_record(row)
            if owner != user_id or record.status != "CLOSED":
                return None, PRICE_PATH_NOT_YET_AVAILABLE
            if record.entry_price is None or record.opened_at is None or record.closed_at is None:
                return None, PRICE_PATH_NOT_YET_AVAILABLE

            prior_report = report_store.get_current_report(
                conn, paper_trade_id=trade_id, user_id=user_id,
                report_schema_version=generation_service.REPORT_SCHEMA_VERSION,
                calculation_version=CALCULATION_VERSION, attribution_rules_version=ATTRIBUTION_RULES_VERSION,
            )
            if prior_report is None:
                return None, PRICE_PATH_NOT_YET_AVAILABLE  # Sprint 2 generation hasn't settled yet

            tz = _REPORT_TIMEZONE.get(record.market)
            if tz is None:
                return None, PRICE_PATH_NOT_YET_AVAILABLE
            market_timezone_name = "Asia/Kolkata" if record.market == "IN" else "America/New_York"

            schema_v, calc_v, rules_v = _price_path_target_identity()
            existing_pp_report = report_store.get_current_report(
                conn, paper_trade_id=trade_id, user_id=user_id,
                report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
            )
            if existing_pp_report is not None:
                # Idempotent: identical target identity already settled —
                # never re-claim, never call the provider again.
                return existing_pp_report, PRICE_PATH_ALREADY_COMPLETE

            outbox_id, _created = _insert_price_path_outbox_record(conn, trade_id=trade_id, user_id=user_id)
            claimant = outbox_ops.new_claimant_token()
            claimed = outbox_ops.claim_next_attempt(conn, outbox_id=outbox_id, user_id=user_id, claimant=claimant)
            if claimed is None:
                # Another valid, non-expired lease currently owns this
                # row — never run a concurrent duplicate attempt.
                return None, PRICE_PATH_GENERATION_IN_PROGRESS
            if claimed.status == "FAILED_TERMINAL":
                return None, PRICE_PATH_FAILED_TERMINAL
            if claimed.status != "GENERATING":
                return None, PRICE_PATH_NOT_YET_AVAILABLE

            ctx = price_path_generation.load_generation_context(
                conn, trade_id=trade_id, user_id=user_id, symbol=record.symbol, market=record.market,
                market_timezone_name=market_timezone_name, entry_timestamp=record.opened_at,
                entry_price=record.entry_price, exit_price=record.exit_price,
                applicable_stop=record.stop_loss, applicable_target=record.target_price,
                exit_timestamp=record.closed_at,
                # Sprint 3A's own documented finding (price_path_calculator.
                # CURRENT_LEVEL_HISTORY_FINDING) is LEVEL_HISTORY_ENDPOINTS_ONLY
                # — this codebase cannot honestly claim full level history for
                # any real trade yet.
                level_history_complete=False, prior_report=prior_report,
            )

        evidence = ctx.compatible_evidence
        if evidence is None:
            # Provider acquisition — deliberately outside any
            # `with _conn():` block; a provider timeout or transient
            # failure here is a classified, durable retryable failure,
            # never a swallowed None.
            try:
                bundle = price_path_generation.acquire_evidence_outside_transaction(
                    ctx, market_tzinfo=tz, fetch_bars_fn=fetch_bars_fn,
                    fetch_splits_fn=fetch_splits_fn, fetch_dividends_fn=fetch_dividends_fn,
                )
            except PriceProviderAcquisitionError as exc:
                with _conn() as conn:
                    outbox_ops.mark_retryable_failure(
                        conn, outbox_id=outbox_id, error_code=exc.code,
                        error_summary=type(exc).__name__, claimed_by=claimant,
                    )
                return None, PRICE_PATH_FAILED_RETRYABLE
            with _conn() as conn:
                evidence, _created = price_path_generation.persist_price_path_evidence(conn, bundle)

        payload = price_path_generation.build_price_path_report_payload(
            evidence, entry_price=ctx.entry_price, exit_price=ctx.exit_price,
            applicable_stop=ctx.applicable_stop, applicable_target=ctx.applicable_target,
            level_history_complete=ctx.level_history_complete, trade_id=trade_id,
        )

        with _conn() as conn:
            with conn.transaction():
                enhanced, _created = price_path_generation.persist_price_path_report(
                    conn, prior_report=prior_report, payload=payload, trade_id=trade_id, user_id=user_id,
                    market=record.market, report_trading_date=prior_report.report_trading_date,
                    market_timezone=market_timezone_name, source_version=evidence.source_version,
                    outbox_id=outbox_id, claimed_by=claimant,
                )
        return enhanced, PRICE_PATH_GENERATED
    except generation_service.StaleLeaseError:
        # A fresher claimant already reclaimed this row while this
        # attempt was computing — this attempt's own report INSERT (if
        # any occurred inside the `with conn.transaction():` above)
        # rolled back with it. Never treat this as success.
        return None, PRICE_PATH_GENERATION_IN_PROGRESS
    except Exception as exc:
        log.warning("[postmortem_price_path_generation] enhancement attempt failed: %s", type(exc).__name__)
        if outbox_id is not None and claimant is not None:
            try:
                with _conn() as conn:
                    outbox_ops.mark_retryable_failure(
                        conn, outbox_id=outbox_id, error_code=_PRICE_PATH_ERROR_CODE,
                        error_summary=type(exc).__name__, claimed_by=claimant,
                    )
            except Exception:
                pass  # best-effort — the claim will still expire and become reclaimable
        return None, PRICE_PATH_FAILED_RETRYABLE


@dataclass
class _IdempotencyReservation:
    """Outcome of `_resolve_idempotency_reservation` — what the caller
    should do next. `action` is one of `IdempotencyAction`'s values.
    `row_id` is set for PROCEED_FRESH/PROCEED_RECLAIMED (the id to mark
    COMPLETED/FAILED later); `response_body` is set only for
    REPLAY_COMPLETED (the stored response to return verbatim)."""

    action: str
    row_id: int | None
    response_body: dict | None


def _resolve_idempotency_reservation(
    conn, user_id: str, idempotency_key: str, fingerprint: str, operation_type: str = OPERATION_TYPE_PAPER_BUY
) -> _IdempotencyReservation:
    """Durable idempotency reservation — see
    services/postmortem/idempotency.py's module docstring for the full
    design rationale. Each iteration is its own read/decide/act step
    against fresh database state (never cached across iterations), so a
    concurrent request's progress is always correctly observed.

    Trade Postmortem Engine, Sprint 2 correction phase (Stage 6):
    `operation_type` is now a parameter (defaulting to
    OPERATION_TYPE_PAPER_BUY, so every existing Buy call site is
    unaffected) — paper_sell passes OPERATION_TYPE_PAPER_SELL, reusing
    this exact same engine and the same `paper_trade_idempotency_key`
    table. The (user_id, operation_type, idempotency_key) unique index
    means a Buy key and a Sell key sharing the same string value for the
    same user never collide.

    The reservation INSERT is a standalone statement (this pool's
    connections are autocommit=True outside an explicit
    `conn.transaction()` block — see paper_buy) so it becomes visible to
    OTHER concurrent requests immediately, before the caller's own
    financial transaction even begins. That visibility is exactly what
    lets a second concurrent request detect the first one's in-flight
    reservation instead of racing past it.
    """
    key_ref = _metrics.hash_key_prefix(idempotency_key)  # never log the raw key
    with _metrics.timed(_metrics.DURATION_RESERVATION):
        for attempt in range(POLL_ATTEMPTS):
            inserted = conn.execute(
                """INSERT INTO paper_trade_idempotency_key
                   (user_id, operation_type, idempotency_key, request_fingerprint, status)
                   VALUES (%s, %s, %s, %s, 'PENDING')
                   ON CONFLICT (user_id, operation_type, idempotency_key) DO NOTHING
                   RETURNING id""",
                (user_id, operation_type, idempotency_key, fingerprint)
            ).fetchone()
            if inserted is not None:
                _metrics.increment(_metrics.COUNTER_FRESH_RESERVATION)
                return _IdempotencyReservation(IdempotencyAction.PROCEED_FRESH.value, inserted[0], None)

            row = conn.execute(
                """SELECT id, status, request_fingerprint, response_body, created_at
                   FROM paper_trade_idempotency_key
                   WHERE user_id = %s AND operation_type = %s AND idempotency_key = %s""",
                (user_id, operation_type, idempotency_key)
            ).fetchone()
            if row is None:
                # Extremely unlikely race (e.g. a reset deleted it between our
                # failed INSERT and this SELECT) — safe to just retry fresh.
                continue

            row_id, status, existing_fp, response_body, created_at = row
            existing = ExistingIdempotencyRow(
                status=status, request_fingerprint=existing_fp,
                response_body=_json_field(response_body), created_at=created_at,
            )
            action = decide_action(existing, fingerprint)

            if action == IdempotencyAction.CONFLICT_FINGERPRINT_MISMATCH:
                _metrics.increment(_metrics.COUNTER_FINGERPRINT_CONFLICT)
                log.warning("[idempotency] fingerprint mismatch for key_ref=%s user=%s", key_ref, user_id)
                return _IdempotencyReservation(action.value, row_id, None)
            if action == IdempotencyAction.REPLAY_COMPLETED:
                _metrics.increment(_metrics.COUNTER_REPLAYED)
                _metrics.increment(_metrics.COUNTER_DUPLICATE_TRADE_PREVENTED)
                return _IdempotencyReservation(action.value, row_id, existing.response_body)
            if action == IdempotencyAction.PROCEED_RECLAIMED:
                # Compare-and-swap on the exact row version we just read
                # (status AND created_at): a stale-PENDING reclaim writes
                # status='PENDING' — the SAME value it started from — so a
                # WHERE clause matching on status alone is not a real CAS
                # for that case: after one thread wins and refreshes
                # created_at, the row's status is still 'PENDING', so a
                # second thread whose local `status` variable was also
                # read as 'PENDING' would match the same WHERE clause and
                # incorrectly win too, letting two requests both proceed to
                # the financial transaction. created_at always changes on a
                # successful reclaim (SET created_at = now()), so pinning
                # the WHERE clause to the created_at value this thread
                # actually read makes the row version comparison exact —
                # a second thread's now-stale created_at can never match
                # after the first thread's UPDATE has already run.
                reclaimed = conn.execute(
                    """UPDATE paper_trade_idempotency_key
                       SET status = 'PENDING', created_at = now(), failure_reason = NULL
                       WHERE id = %s AND status = %s AND created_at = %s
                       RETURNING id""",
                    (row_id, status, created_at)
                ).fetchone()
                if reclaimed is not None:
                    if status == "FAILED":
                        _metrics.increment(_metrics.COUNTER_FAILED_RECLAIMED)
                    else:
                        _metrics.increment(_metrics.COUNTER_STALE_RECLAIMED)
                        # How old the abandoned PENDING reservation was at
                        # the moment it got reclaimed — a rising trend here
                        # signals requests crashing before completion.
                        pending_age = created_at
                        if pending_age.tzinfo is None:
                            pending_age = pending_age.replace(tzinfo=timezone.utc)
                        age_seconds = (datetime.now(timezone.utc) - pending_age).total_seconds()
                        _metrics.record_duration(_metrics.DURATION_PENDING_ROW_AGE, age_seconds)
                    return _IdempotencyReservation(IdempotencyAction.PROCEED_RECLAIMED.value, row_id, None)
                # Lost the reclaim race — fall through to poll again below.
            if attempt < POLL_ATTEMPTS - 1:
                _time.sleep(POLL_INTERVAL_SECONDS)

        _metrics.increment(_metrics.COUNTER_ALREADY_IN_PROGRESS)
        log.info("[idempotency] poll exhausted, still in progress for key_ref=%s user=%s", key_ref, user_id)
        return _IdempotencyReservation(IdempotencyAction.STILL_IN_PROGRESS.value, None, None)


def _mark_close_idempotency_failed(conn, enforced: bool, reservation, reason: str) -> None:
    """Trade Postmortem Engine, Sprint 2 correction phase (Stage 6) —
    marks a Sell idempotency reservation FAILED so it becomes immediately
    reclaimable on the next attempt with the same key, mirroring
    paper_buy's own identical failure-marking pattern. A business-rule
    failure (trade not found, not owned, already closed, validation) or a
    genuine crash both leave zero financial effect here (the transaction
    rolled back), so there is nothing unsafe about allowing an immediate
    retry. `reason` is a short, fixed, non-sensitive label — never a raw
    exception message or request payload (Part 10's same no-sensitive-
    data-in-this-column discipline Buy's own failure path already
    follows)."""
    if not enforced or reservation is None or reservation.row_id is None:
        return
    try:
        conn.execute(
            "UPDATE paper_trade_idempotency_key SET status = 'FAILED', failure_reason = %s "
            "WHERE id = %s AND status = 'PENDING'",
            (reason[:500], reservation.row_id)
        )
    except Exception:
        pass


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/portfolio")
def get_portfolio(
    include_full_closed_trades: bool = True,
    user_id: str = Depends(get_current_user_id),
):
    """
    include_full_closed_trades defaults to True, preserving exact legacy
    behavior (the full flat `closed_trades` array, plus every field this
    endpoint has ever returned) for any consumer that omits the parameter.

    The modern Paper Trading frontend passes include_full_closed_trades=false
    explicitly: `closed_trades` is omitted entirely from the response (never
    a misleading empty array), and closed_trade_history_by_horizon /
    closed_trade_summary / closed_trade_overview_by_market are built from
    bounded SQL aggregation and a windowed "latest N per bucket" query —
    never from a materialized list of every closed trade.
    """
    portfolio = _ensure_portfolio(user_id)

    if not include_full_closed_trades:
        with _conn() as conn:
            open_rows = conn.execute(
                f"SELECT {_TRADE_ROW_COLUMNS} FROM paper_trades WHERE user_id = %s AND status = 'OPEN' ORDER BY opened_at DESC",
                (user_id,)
            ).fetchall()
            (closed_trade_history_by_horizon, closed_trade_summary,
             closed_trade_overview_by_market, total_realized_by_market) = _build_modern_closed_trade_data(conn, user_id)

        return {
            "user_id": user_id,
            "cash": round(portfolio["cash"], 2),
            "cash_usd": round(portfolio["cash_usd"], 2),
            "starting_cash": STARTING_CASH_IN,
            "starting_cash_usd": STARTING_CASH_US,
            "open_trades": [_trade_row_to_dict(r) for r in open_rows],
            # `closed_trades` intentionally omitted — the modern page must
            # not receive or depend on the full closed-trade list.
            "total_realized_pnl": total_realized_by_market["IN"],
            "total_realized_pnl_usd": total_realized_by_market["US"],
            "closed_trade_summary": closed_trade_summary,
            "closed_trade_history_by_horizon": closed_trade_history_by_horizon,
            "closed_trade_overview_by_market": closed_trade_overview_by_market,
            "email_notifications_enabled": portfolio["email_notifications_enabled"],
        }

    # ── Legacy path (default) — byte-for-byte unchanged from before this
    # hardening pass, plus the new (additive, harmless-to-ignore) overview
    # field computed cheaply from the already-materialized closed_trades list. ──
    with _conn() as conn:
        trades = conn.execute(
            f"SELECT {_TRADE_ROW_COLUMNS} FROM paper_trades WHERE user_id = %s ORDER BY opened_at DESC",
            (user_id,)
        ).fetchall()

    open_trades = []
    closed_trades = []
    total_realized_in = 0.0
    total_realized_us = 0.0

    for row in trades:
        trade = _trade_row_to_dict(row)
        if trade["status"] == "OPEN":
            open_trades.append(trade)
        else:
            if trade["market"] == "US":
                total_realized_us += trade["realized_pnl"]
            else:
                total_realized_in += trade["realized_pnl"]
            closed_trades.append(trade)

    # Market-scoped, per-horizon closed-trade history — the authoritative
    # source for Trade History rendering. Computed once here, server-side,
    # from the same `closed_trades` list already built above (no second
    # query, no additional provider/DB calls). IN and US are built
    # independently and never combined, matching every other paper-trading
    # metric's existing per-ledger scoping (see _CASH_COL/STARTING). The
    # frontend renders `latest_trades`/`earlier_trade_count`/`summary`
    # verbatim; it must not group, sort, classify, or slice the flat
    # `closed_trades` list below to reconstruct this itself.
    closed_trade_history_by_horizon = {
        "IN": _closed_trade_history_by_horizon_for_market([t for t in closed_trades if t["market"] == "IN"]),
        "US": _closed_trade_history_by_horizon_for_market([t for t in closed_trades if t["market"] == "US"]),
    }
    # Kept for backward compatibility only — derived from the same bucket
    # summaries above, never recomputed independently. New code should read
    # closed_trade_history_by_horizon[market][horizon]["summary"] instead.
    closed_trade_summary = {
        market: _closed_trade_summary_for_market(closed_trade_history_by_horizon[market])
        for market in ("IN", "US")
    }

    return {
        "user_id": user_id,
        "cash": round(portfolio["cash"], 2),
        "cash_usd": round(portfolio["cash_usd"], 2),
        "starting_cash": STARTING_CASH_IN,
        "starting_cash_usd": STARTING_CASH_US,
        "open_trades": open_trades,
        # Retained for backward compatibility — the modern Paper Trading
        # frontend requests include_full_closed_trades=false instead and
        # never reads this field (see closed_trade_history_by_horizon above).
        "closed_trades": closed_trades,
        "total_realized_pnl": round(total_realized_in, 2),
        "total_realized_pnl_usd": round(total_realized_us, 2),
        "closed_trade_summary": closed_trade_summary,
        "closed_trade_history_by_horizon": closed_trade_history_by_horizon,
        "closed_trade_overview_by_market": _overview_from_closed_trades(closed_trades),
        "email_notifications_enabled": portfolio["email_notifications_enabled"],
    }


@router.get("/closed-trades/older")
def get_older_closed_trades(
    market: Literal["IN", "US"],
    horizon: str,
    limit: int = Query(_OLDER_HISTORY_DEFAULT_LIMIT, ge=1, le=_OLDER_HISTORY_MAX_LIMIT),
    before_closed_at: str | None = None,
    before_id: int | None = None,
    user_id: str = Depends(get_current_user_id),
):
    """
    Lazy, bounded retrieval of closed trades older than the initial 5 shown
    per horizon in GET /portfolio's closed_trade_history_by_horizon — called
    only when a user expands a horizon's "Show N earlier closed trades"
    control, never prefetched. Market- and horizon-scoped (never all
    horizons, never all markets, never open trades); no provider calls and
    no interaction with the shared prediction cache.

    Keyset pagination on (closed_at, id) DESC — the same ordering and
    tiebreaker _sort_closed_desc uses for the initial page — rather than
    offset-based paging, so a page requested after another trade closes in
    the meantime cannot skip or duplicate a row relative to what the client
    has already rendered. Pass back the previous page's last trade's
    `closed_at`/`id` as `before_closed_at`/`before_id` to fetch the next one;
    omit both for the very first "older" page (immediately after the initial
    5 shown by GET /portfolio).
    """
    where = ["user_id = %s", "market = %s", "status = 'CLOSED'"]
    params: list = [user_id, market]

    if horizon in _CLOSED_HORIZONS:
        where.append("horizon = %s")
        params.append(horizon)
    else:
        # "unclassified" (or any other value) — every closed trade whose
        # stored horizon isn't one of the three official values, mirroring
        # _bucket_closed_trades_by_horizon's exact classification rule.
        where.append("(horizon IS NULL OR horizon NOT IN ('short', 'medium', 'long'))")

    if before_closed_at is not None and before_id is not None:
        where.append("(closed_at, id) < (%s::timestamptz, %s)")
        params.append(before_closed_at)
        params.append(before_id)

    # Fetch one extra row to detect whether more remain, without a second
    # COUNT query — trimmed back to `limit` before returning.
    sql = (
        f"SELECT {_TRADE_ROW_COLUMNS} FROM paper_trades "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY closed_at DESC, id DESC LIMIT %s"
    )
    params.append(limit + 1)

    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    has_more = len(rows) > limit
    page = [_trade_row_to_dict(row) for row in rows[:limit]]
    next_cursor = (
        {"before_closed_at": page[-1]["closed_at"], "before_id": page[-1]["id"]}
        if page and has_more else None
    )

    return {
        "market": market,
        "horizon": horizon,
        "trades": page,
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


@router.post("/buy")
@limiter.limit(USER_DATA_RATE_LIMIT)
def paper_buy(request: Request, req: BuyRequest, user_id: str = Depends(get_current_user_id)):
    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be > 0")
    if req.price <= 0:
        raise HTTPException(status_code=400, detail="Price must be > 0")
    if not _is_market_open(req.market):
        raise HTTPException(status_code=400, detail=f"{req.market} market is closed — orders are paused until it reopens")

    cash_col = _CASH_COL[req.market]
    sym = _SYMBOL[req.market]
    cost = req.price * req.quantity
    _ensure_portfolio(user_id)  # make sure the row exists before the conditional debit below

    # Migration-verification hardening gate, Part 7 — Buy idempotency.
    # `idempotency_enforced` is False for any request that omits the key
    # (full backward compatibility: identical behavior to before this
    # field existed). The fingerprint covers only the financially material
    # fields (see idempotency.compute_request_fingerprint) — never
    # symbol/quantity/timestamp matching alone, so two genuine purchases of
    # the same stock and quantity with two different keys remain
    # independently valid.
    idempotency_enforced = req.idempotency_key is not None
    fingerprint = None
    if idempotency_enforced:
        fingerprint = compute_request_fingerprint(
            market=req.market, symbol=req.symbol, quantity=req.quantity, price=req.price,
            stop_loss=req.stop_loss, target_price=req.target_price,
            trade_management_mode=req.trade_management_mode, evidence_source=req.evidence_source,
            entry_evidence_schema_version=SNAPSHOT_SCHEMA_VERSION,
        )

    with _conn() as conn:
        reservation = None
        if idempotency_enforced:
            reservation = _resolve_idempotency_reservation(conn, user_id, req.idempotency_key, fingerprint)
            if reservation.action == IdempotencyAction.REPLAY_COMPLETED.value:
                return reservation.response_body
            if reservation.action == IdempotencyAction.CONFLICT_FINGERPRINT_MISMATCH.value:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_code": "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST",
                        "message": "This idempotency_key was already used for a Buy request with different terms. Use a new key for a genuinely new Buy decision.",
                    },
                )
            if reservation.action == IdempotencyAction.STILL_IN_PROGRESS.value:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_code": "BUY_ALREADY_IN_PROGRESS",
                        "message": "A Buy request with this idempotency_key is still being processed. Retry shortly with the same key.",
                    },
                )
            # else PROCEED_FRESH or PROCEED_RECLAIMED — reservation.row_id is set, continue below.

        _buy_transaction_started_at = _time.monotonic()
        try:
            # Migration-verification hardening gate, Part 7 — the cash
            # debit now participates in the SAME explicit transaction as
            # the trade INSERT, snapshot INSERT, and (when enforced) the
            # idempotency-completion UPDATE, per this gate's explicit
            # "atomic financial behavior" requirement. This also closes a
            # pre-existing latent gap flagged in the Stage 2 report: before
            # this change, the debit committed independently (autocommit)
            # a moment before the trade INSERT — a failure in between would
            # have left cash debited with no trade created. Now either all
            # four effects commit together or none do.
            with conn.transaction():
                # Atomic check-and-debit: the WHERE clause re-checks the
                # balance at write time, inside the same statement, instead
                # of trusting a value read moments earlier. Two concurrent
                # buys can no longer both pass a stale balance check and
                # both decrement past zero.
                debited = conn.execute(
                    f"UPDATE paper_portfolio SET {cash_col} = {cash_col} - %s, updated_at = now() "
                    f"WHERE user_id = %s AND {cash_col} >= %s RETURNING {cash_col}",
                    (cost, user_id, cost)
                ).fetchone()

                if debited is None:
                    current = conn.execute(
                        f"SELECT {cash_col} FROM paper_portfolio WHERE user_id = %s", (user_id,)
                    ).fetchone()
                    available = current[0] if current else 0.0
                    raise HTTPException(
                        status_code=400,
                        detail=f"Insufficient {req.market} funds. Available: {sym}{available:,.2f}, Required: {sym}{cost:,.2f}"
                    )

                # Learning Alpha Engine remediation, Phase 1 (corrected per review):
                #
                # execution_slippage_pct is computed only when BOTH a
                # recommendation_source is known AND recommendation_reference_price
                # is a finite, strictly-positive number — otherwise NULL. Guards
                # against NaN/inf/negative/zero reference prices (a malformed or
                # absent reference price makes the slippage figure meaningless, not
                # just risky to divide by).
                #
                # signal_override is NOT derived from the client-supplied `signal`
                # field — that field is not an authoritative recommendation record,
                # a client could send anything. This phase has no backend
                # validation of a trade against an actual stored Daily Pick
                # recommendation yet, so signal_override always stays NULL here
                # rather than fabricate true/false for provenance we cannot verify.
                # A later phase that adds real validation against a stored
                # recommendation can populate this without any schema change.
                execution_slippage_pct = None
                ref_price = req.recommendation_reference_price
                if (
                    req.recommendation_source is not None
                    and ref_price is not None
                    and math.isfinite(ref_price)
                    and ref_price > 0
                ):
                    execution_slippage_pct = round((req.price - ref_price) / ref_price * 100, 4)
                signal_override = None

                row = conn.execute(
                    """INSERT INTO paper_trades
                       (session_id, user_id, symbol, market, quantity, entry_price, signal, horizon,
                        stop_loss, target_price, trade_management_mode,
                        recommendation_source, daily_pick_run_id, daily_pick_rank,
                        recommendation_generated_at, recommendation_reference_price,
                        recommendation_entry_low, recommendation_entry_high,
                        recommendation_original_stop_loss, recommendation_original_target,
                        model_version, execution_slippage_pct, signal_override)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                               %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       RETURNING id""",
                    (user_id, user_id, req.symbol.upper(), req.market, req.quantity,
                     req.price, req.signal, req.horizon, req.stop_loss, req.target_price, req.trade_management_mode,
                     req.recommendation_source, req.daily_pick_run_id, req.daily_pick_rank,
                     req.recommendation_generated_at, req.recommendation_reference_price,
                     req.recommendation_entry_low, req.recommendation_entry_high,
                     req.recommendation_original_stop_loss, req.recommendation_original_target,
                     req.model_version, execution_slippage_pct, signal_override)
                ).fetchone()
                trade_id = row[0]

                snapshot = _build_snapshot_for_buy(
                    trade_id=trade_id, user_id=user_id, symbol=req.symbol.upper(),
                    market=req.market, req=req,
                )
                try:
                    _insert_entry_snapshot(conn, snapshot)
                except Exception:
                    _metrics.increment(_metrics.COUNTER_SNAPSHOT_INSERT_FAILURE)
                    raise

                completeness, available_fields, missing_fields = classify_evidence_completeness(snapshot)

                response = {
                    "message": "Paper buy placed",
                    "trade_id": trade_id,
                    "symbol": req.symbol.upper(),
                    "market": req.market,
                    "quantity": req.quantity,
                    "entry_price": req.price,
                    "cost": round(cost, 2),
                    "remaining_cash": round(debited[0], 2),
                    # Trade Postmortem Engine, Stage 2 — lets a caller verify the
                    # snapshot decision without a second request.
                    "entry_evidence_captured": True,
                    "snapshot_schema_version": snapshot.snapshot_schema_version,
                    "evidence_source": snapshot.evidence_source,
                    "evidence_completeness": completeness,
                    "available_evidence_fields": available_fields,
                    "missing_evidence_fields": missing_fields,
                    # Migration-verification hardening gate, Part 7 — lets a
                    # caller/monitoring tell whether the exactly-once
                    # guarantee actually applied to this specific request.
                    "idempotency_enforced": idempotency_enforced,
                }

                if idempotency_enforced:
                    conn.execute(
                        """UPDATE paper_trade_idempotency_key
                           SET status = 'COMPLETED', paper_trade_id = %s, response_body = %s,
                               response_schema_version = %s, completed_at = now()
                           WHERE id = %s""",
                        (trade_id, json.dumps(response), BUY_RESPONSE_SCHEMA_VERSION, reservation.row_id)
                    )
            _metrics.increment(_metrics.COUNTER_COMPLETED)
        except Exception as exc:
            _metrics.increment(_metrics.COUNTER_BUY_FAILED)
            if idempotency_enforced and reservation is not None and reservation.row_id is not None:
                # Marks the reservation FAILED so it becomes immediately
                # reclaimable on the next attempt with the same key — a
                # business-rule failure (insufficient funds, market closed)
                # or a genuine crash both leave zero financial effect here
                # (the transaction rolled back), so there is nothing unsafe
                # about allowing an immediate retry. Truncated, generic
                # message only — never the full request payload or a stack
                # trace (Part 10: no sensitive data in this column).
                try:
                    conn.execute(
                        "UPDATE paper_trade_idempotency_key SET status = 'FAILED', failure_reason = %s "
                        "WHERE id = %s AND status = 'PENDING'",
                        (str(exc)[:500], reservation.row_id)
                    )
                except Exception:
                    _metrics.increment(_metrics.COUNTER_DB_ERROR)
                    raise
            raise
        finally:
            _metrics.record_duration(_metrics.DURATION_BUY_TRANSACTION, _time.monotonic() - _buy_transaction_started_at)

    return response


@router.post("/sell/{trade_id}")
def paper_sell(trade_id: int, req: SellRequest, user_id: str = Depends(get_current_user_id)):
    """
    Trade Postmortem Engine, Sprint 2 — the sole manual/stop-loss/target
    close path now delegates to services.postmortem.close_service.
    close_paper_trade for the trade UPDATE, immutable exit snapshot, and
    outbox record, all inside one transaction; this endpoint's own
    responsibilities are limited to the market-open business rule (not
    the close service's concern) and crediting cash to the correct
    market's portfolio column inside that SAME transaction.
    """
    if req.price <= 0:
        raise HTTPException(status_code=400, detail="Price must be > 0")

    exit_mechanism = sell_exit_reason_to_mechanism(req.exit_reason)

    # Trade Postmortem Engine, Sprint 2 correction phase (Stage 6) —
    # additive, backward-compatible close idempotency. A client that
    # omits idempotency_key gets exactly today's behavior (no
    # response-loss dedup guarantee — the honest, documented limitation
    # until the frontend's own auto-close callers begin supplying stable
    # keys). Mirrors paper_buy's own idempotency wiring exactly, reusing
    # the SAME engine (services.postmortem.idempotency) with a DISTINCT
    # operation_type so a Buy key and a Sell key never collide.
    close_idempotency_enforced = req.idempotency_key is not None
    close_fingerprint = None
    if close_idempotency_enforced:
        close_fingerprint = compute_close_request_fingerprint(
            trade_id=trade_id, exit_price=req.price, exit_reason=req.exit_reason,
        )

    with _conn() as conn:
        close_reservation = None
        if close_idempotency_enforced:
            close_reservation = _resolve_idempotency_reservation(
                conn, user_id, req.idempotency_key, close_fingerprint, operation_type=OPERATION_TYPE_PAPER_SELL,
            )
            if close_reservation.action == IdempotencyAction.REPLAY_COMPLETED.value:
                return close_reservation.response_body
            if close_reservation.action == IdempotencyAction.CONFLICT_FINGERPRINT_MISMATCH.value:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_code": "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST",
                        "message": "This idempotency_key was already used for a Sell request with different terms. Use a new key for a genuinely new Sell decision.",
                    },
                )
            if close_reservation.action == IdempotencyAction.STILL_IN_PROGRESS.value:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_code": "SELL_ALREADY_IN_PROGRESS",
                        "message": "A Sell request with this idempotency_key is still being processed. Retry shortly with the same key.",
                    },
                )
            # else PROCEED_FRESH or PROCEED_RECLAIMED — close_reservation.row_id is set, continue below.

        # Trade Postmortem Engine, Sprint 2 correction phase — this probe
        # previously queried by trade_id ALONE ("SELECT market FROM
        # paper_trades WHERE id = %s"), so any authenticated user could
        # learn whether an arbitrary trade_id exists, which market it's
        # in, and whether that market is currently open — all before
        # close_paper_trade's own ownership check ever ran. Now
        # user-scoped (WHERE id = %s AND user_id = %s): a nonexistent
        # trade and a trade owned by another user are indistinguishable
        # here, both simply return no row, and both get the identical 404
        # below — never leaking existence, market, or open/closed state
        # for a trade the caller doesn't own. close_paper_trade's own
        # ownership check (TradeNotOwnedError -> 403 further down) is
        # retained as defense in depth for a same-request race (this
        # probe and the close read the database at two different points
        # in time), not as the primary authorization boundary.
        trade_market_probe = conn.execute(
            "SELECT market FROM paper_trades WHERE id = %s AND user_id = %s", (trade_id, user_id)
        ).fetchone()
        if trade_market_probe is None:
            raise HTTPException(status_code=404, detail="Trade not found")
        if not _is_market_open(trade_market_probe[0]):
            raise HTTPException(
                status_code=400,
                detail=f"{trade_market_probe[0]} market is closed — orders are paused until it reopens",
            )

        try:
            with conn.transaction():
                result = close_paper_trade(
                    conn,
                    user_id=user_id,
                    trade_id=trade_id,
                    exit_price=req.price,
                    exit_mechanism=exit_mechanism,
                    exit_mechanism_raw=req.exit_reason,
                    # source_request_id is a caller-supplied, non-secret
                    # correlation token stored on the exit snapshot's own
                    # dedicated column — it is NOT the raw idempotency key
                    # (never store that anywhere outside
                    # paper_trade_idempotency_key itself, and never log it
                    # — _metrics.hash_key_prefix below is the only
                    # logged representation).
                    source_request_id=(
                        _metrics.hash_key_prefix(req.idempotency_key) if close_idempotency_enforced else None
                    ),
                )
                cash_col = _CASH_COL[result.market]
                conn.execute(
                    f"UPDATE paper_portfolio SET {cash_col} = {cash_col} + %s, updated_at = now() WHERE user_id = %s",
                    (result.proceeds, user_id),
                )

                response = {
                    "message": "Paper sell placed",
                    "trade_id": trade_id,
                    "symbol": result.symbol,
                    "market": result.market,
                    "quantity": result.quantity,
                    "entry_price": result.entry_price,
                    "exit_price": result.exit_price,
                    "pnl": result.realized_pnl_abs,
                    # Trade Postmortem Engine, Sprint 2 correction phase —
                    # CloseResult.realized_pnl_pct now preserves a genuine
                    # None (an indeterminate percentage, e.g. a legacy/
                    # corrupt non-positive entry_price) rather than
                    # close_service coercing it to 0. The "0" fallback
                    # below reproduces this endpoint's own pre-refactor
                    # response contract exactly (see
                    # test_paper_trading_postmortem_pnl_parity.py's
                    # locked-in old formula: `... if ep and ep > 0 else
                    # 0`) — applied ONLY here, at the JSON boundary, never
                    # inside the authoritative close service, so an
                    # indeterminate percentage is never misrepresented as
                    # a computed zero anywhere else that consumes
                    # CloseResult (e.g. the report/evidence integration).
                    "pnl_pct": result.realized_pnl_pct if result.realized_pnl_pct is not None else 0,
                    "proceeds": result.proceeds,
                    # Trade Postmortem Engine, Sprint 2 correction phase —
                    # mirrors paper_buy's own idempotency_enforced flag:
                    # lets a caller/monitoring tell whether the
                    # response-loss-safe replay guarantee actually applied
                    # to this specific request.
                    "idempotency_enforced": close_idempotency_enforced,
                }

                if close_idempotency_enforced:
                    conn.execute(
                        """UPDATE paper_trade_idempotency_key
                           SET status = 'COMPLETED', paper_trade_id = %s, response_body = %s,
                               response_schema_version = %s, completed_at = now()
                           WHERE id = %s""",
                        (trade_id, json.dumps(response), SELL_RESPONSE_SCHEMA_VERSION, close_reservation.row_id)
                    )
        except TradeNotFoundError:
            _mark_close_idempotency_failed(conn, close_idempotency_enforced, close_reservation, "trade not found")
            raise HTTPException(status_code=404, detail="Trade not found")
        except TradeNotOwnedError:
            _mark_close_idempotency_failed(conn, close_idempotency_enforced, close_reservation, "not owned")
            raise HTTPException(status_code=403, detail="Not your trade")
        except TradeAlreadyClosedError:
            _mark_close_idempotency_failed(conn, close_idempotency_enforced, close_reservation, "already closed")
            raise HTTPException(status_code=400, detail="Trade already closed")
        except (CloseValidationError, UnsupportedMarketError) as exc:
            _mark_close_idempotency_failed(conn, close_idempotency_enforced, close_reservation, "validation error")
            raise HTTPException(status_code=400, detail=str(exc))

    # Stage 8 — best-effort immediate post-commit report generation. The
    # close transaction has ALREADY committed successfully by this point
    # (we're past the `with _conn()` block) — a failure here can only
    # ever leave the outbox row in its already-durable PENDING state for
    # later on-request recovery; it can never undo the trade close, and
    # is never allowed to turn this endpoint's response into an error.
    _attempt_best_effort_generation(user_id=user_id, trade_id=trade_id, outbox_id=result.outbox_id)

    # Trade Postmortem Sprint 3A, Stage H2A — additive, best-effort
    # price-path enhancement on top of whatever the Sprint 2 attempt
    # above just settled. Off by default (see
    # _trade_postmortem_price_path_enabled) since this is the first
    # point in this codepath capable of a real external network call;
    # like the Sprint 2 attempt above, failure here can never turn a
    # successful sell into an HTTP error.
    if _trade_postmortem_price_path_enabled():
        _attempt_price_path_enhancement(user_id=user_id, trade_id=trade_id)

    return response


@router.patch("/trade/{trade_id}")
def edit_trade(trade_id: int, req: EditRequest, user_id: str = Depends(get_current_user_id)):
    with _conn() as conn:
        trade = conn.execute(
            "SELECT user_id, status, entry_price, quantity, market, stop_loss, target_price "
            "FROM paper_trades WHERE id = %s",
            (trade_id,)
        ).fetchone()
        if trade is None:
            raise HTTPException(status_code=404, detail="Trade not found")
        if trade[0] != user_id:
            raise HTTPException(status_code=403, detail="Not your trade")
        if trade[1] != "OPEN":
            raise HTTPException(status_code=400, detail="Cannot edit a closed trade")

        old_entry, qty, trade_market, old_stop_loss, old_target_price = trade[2], trade[3], trade[4], trade[5], trade[6]
        if trade_market not in _CASH_COL:
            raise HTTPException(status_code=500, detail=f"Trade has an unrecognized market '{trade_market}' — cannot determine which cash ledger to adjust")
        cash_col = _CASH_COL[trade_market]

        # Learning Alpha Engine remediation, Phase 1 (corrected per review):
        #
        # Partial-PATCH semantics: EditRequest.stop_loss/target_price both
        # default to None, so a request body that OMITS a field is
        # indistinguishable from one that explicitly submits null — UNLESS
        # we check model_fields_set (which JSON keys the client actually
        # sent), not just the resolved attribute value. An entry_price-only
        # request (stop_loss/target_price omitted) must preserve the
        # currently stored levels, not wipe them to NULL; an explicit
        # `{"stop_loss": null, ...}` still clears that level, matching this
        # endpoint's pre-existing behavior for a client that does that on
        # purpose.
        new_stop_loss = req.stop_loss if "stop_loss" in req.model_fields_set else old_stop_loss
        new_target_price = req.target_price if "target_price" in req.model_fields_set else old_target_price

        # levels_modified_after_entry is set TRUE only when stop_loss or
        # target_price GENUINELY changes from its previously stored value —
        # an entry_price-only correction (omitted or resubmitted-identical)
        # or a no-op edit must not set it. Once TRUE it must never revert:
        # the "unchanged" branch below simply never touches the column
        # again (rather than writing FALSE), so a prior TRUE always
        # survives a later no-op edit.
        levels_changed = (new_stop_loss != old_stop_loss) or (new_target_price != old_target_price)
        if levels_changed:
            conn.execute(
                "UPDATE paper_trades SET stop_loss = %s, target_price = %s, "
                "levels_modified_after_entry = TRUE WHERE id = %s",
                (new_stop_loss, new_target_price, trade_id)
            )
        else:
            conn.execute(
                "UPDATE paper_trades SET stop_loss = %s, target_price = %s WHERE id = %s",
                (new_stop_loss, new_target_price, trade_id)
            )

        if req.entry_price and req.entry_price > 0 and req.entry_price != old_entry:
            cash_delta = (old_entry - req.entry_price) * qty
            conn.execute(
                "UPDATE paper_trades SET entry_price = %s WHERE id = %s",
                (req.entry_price, trade_id)
            )
            conn.execute(
                f"UPDATE paper_portfolio SET {cash_col} = {cash_col} + %s, updated_at = now() WHERE user_id = %s",
                (cash_delta, user_id)
            )

    return {"message": "Trade updated", "trade_id": trade_id}


@router.patch("/trades/{trade_id}/management-mode")
def update_management_mode(trade_id: int, req: ManagementModeRequest, user_id: str = Depends(get_current_user_id)):
    with _conn() as conn:
        trade = conn.execute(
            """SELECT user_id, symbol, market, quantity, entry_price, exit_price, status, signal,
                      horizon, opened_at, closed_at, stop_loss, target_price, exit_reason
               FROM paper_trades WHERE id = %s""",
            (trade_id,)
        ).fetchone()
        if trade is None:
            raise HTTPException(status_code=404, detail="Trade not found")
        if trade[0] != user_id:
            raise HTTPException(status_code=403, detail="Not your trade")
        if trade[6] != "OPEN":
            raise HTTPException(status_code=400, detail="Cannot change trade management mode on a closed trade")

        updated = conn.execute(
            "UPDATE paper_trades SET trade_management_mode = %s WHERE id = %s AND status = 'OPEN' RETURNING id",
            (req.trade_management_mode, trade_id)
        ).fetchone()
        if updated is None:
            # Lost a race with a close that happened between the SELECT above
            # and this UPDATE — same "closed trade" rule, just caught atomically.
            raise HTTPException(status_code=400, detail="Cannot change trade management mode on a closed trade")

    (_owner, sym, mkt, qty, ep, xp, status, sig, hor, opened, closed, sl, tp, exit_reason) = trade
    return {
        "message": "Trade management mode updated",
        "trade": {
            "id": trade_id,
            "symbol": sym,
            "market": mkt,
            "quantity": qty,
            "entry_price": ep,
            "exit_price": xp,
            "stop_loss": sl,
            "target_price": tp,
            "status": status,
            "signal": sig,
            "horizon": hor,
            "opened_at": opened.isoformat() if opened else None,
            "closed_at": closed.isoformat() if closed else None,
            "invested": round(ep * qty, 2),
            "trade_management_mode": req.trade_management_mode,
            "exit_reason": exit_reason,
        },
    }


@router.patch("/notifications")
def update_notification_preference(req: NotificationPreferenceRequest, user_id: str = Depends(get_current_user_id)):
    """Toggles the single Paper Trading notification preference — gates both
    the trade notifier's proximity/auto-close emails (checked directly
    against this column) and, client-side, whether the Notifications button
    asks for browser permission. Scoped to Paper Trading only; does not
    touch Daily Picks alerts, which have their own separate mechanism."""
    _ensure_portfolio(user_id)  # make sure the row exists before updating it
    with _conn() as conn:
        conn.execute(
            "UPDATE paper_portfolio SET email_notifications_enabled = %s, updated_at = now() WHERE user_id = %s",
            (req.email_notifications_enabled, user_id)
        )
    return {"message": "Notification preference updated", "email_notifications_enabled": req.email_notifications_enabled}


@router.post("/reset")
def reset_portfolio(user_id: str = Depends(get_current_user_id), market: Literal["IN", "US", "ALL"] = "ALL"):
    """Reset paper trading. Defaults to wiping both ledgers; pass market=IN or
    market=US to reset just one side and leave the other market's trades/cash intact."""
    with _conn() as conn:
        # Migration-verification hardening gate — Stage 2 turned this
        # endpoint's single DELETE into a two-statement sequence (snapshot
        # rows, then trade rows) plus the cash-reset INSERT. Under the
        # pool's autocommit=True connections, each statement previously
        # committed independently; a crash between the snapshot DELETE and
        # the trade DELETE wouldn't corrupt data (a trade left with no
        # snapshot just reads as limited-evidence, same as any pre-Stage-2
        # trade) but WOULD leave "reset" only partially applied — the
        # user's trades not actually cleared despite a 200 response never
        # being returned (the crash prevents that too, but a *retry* could
        # then see a confusing partial state). Wrapped in one explicit
        # transaction so reset is now all-or-nothing, the same pattern
        # paper_buy already uses for its own trade+snapshot atomicity.
        with conn.transaction():
            if market == "ALL":
                # paper_trade_entry_snapshot has no FOREIGN KEY back to
                # paper_trades (see the migration's comment in
                # postgres_store.py for why), so deleting the trades
                # without also deleting their snapshots would leave
                # orphaned evidence rows pointing at a trade_id that no
                # longer exists. Deleted first (snapshot rows referencing a
                # still-existing trade_id are harmless to delete slightly
                # ahead of the trade itself; the reverse order would risk a
                # moment where a snapshot row is gone but its trade isn't).
                #
                # Trade Postmortem Engine, Sprint 2, Stage 11 — reset's
                # EXISTING contract (established above, unchanged by this
                # sprint) is DELETION of a user's trades and their
                # evidence, not administrative closure. The three Sprint 2
                # durability tables (exit snapshot, generation outbox,
                # persisted report) each carry their own user_id column
                # and are deleted the same way, before paper_trades, so a
                # reset never leaves an orphaned outbox/report row behind.
                # This means a persisted report or exit snapshot is
                # immutable-by-UPDATE during normal lifecycle, but not
                # absolutely immutable — an authorized reset may delete
                # it, exactly like the entry snapshot already does.
                conn.execute("DELETE FROM paper_trade_postmortem_report WHERE user_id = %s", (user_id,))
                conn.execute("DELETE FROM paper_trade_postmortem_outbox WHERE user_id = %s", (user_id,))
                conn.execute("DELETE FROM paper_trade_exit_snapshot WHERE user_id = %s", (user_id,))
                conn.execute("DELETE FROM paper_trade_entry_snapshot WHERE user_id = %s", (user_id,))
                conn.execute("DELETE FROM paper_trades WHERE user_id = %s", (user_id,))
                # Migration-verification hardening gate, Part 7 — a full
                # reset clears ALL of this user's Buy idempotency records
                # (every status), since every trade they could reference is
                # being wiped too. A market-specific reset (below) instead
                # only clears COMPLETED rows for that specific market —
                # see the ADR comment there for why a full wipe isn't used
                # in that branch.
                conn.execute(
                    "DELETE FROM paper_trade_idempotency_key WHERE user_id = %s AND operation_type = %s",
                    (user_id, OPERATION_TYPE_PAPER_BUY)
                )
                conn.execute(
                    """INSERT INTO paper_portfolio (session_id, user_id, cash, cash_usd) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (user_id) DO UPDATE SET cash = %s, cash_usd = %s, updated_at = now()""",
                    (user_id, user_id, STARTING_CASH_IN, STARTING_CASH_US, STARTING_CASH_IN, STARTING_CASH_US)
                )
                return {"message": "Portfolio reset", "cash": STARTING_CASH_IN, "cash_usd": STARTING_CASH_US}

            # Trade Postmortem Engine, Sprint 2, Stage 11 — same orphan
            # prevention as the ALL-markets branch above, scoped to this
            # one market. paper_trade_postmortem_report and
            # paper_trade_exit_snapshot both carry their own `market`
            # column and can be filtered directly. paper_trade_postmortem_
            # outbox does NOT carry a market column (it is scoped only by
            # paper_trade_id/user_id — see postgres_store.py), so its rows
            # are identified via a subquery against paper_trades, and MUST
            # run before the paper_trades DELETE below removes the rows
            # that subquery depends on.
            conn.execute(
                "DELETE FROM paper_trade_postmortem_report WHERE user_id = %s AND market = %s",
                (user_id, market)
            )
            conn.execute(
                """DELETE FROM paper_trade_postmortem_outbox
                   WHERE user_id = %s AND paper_trade_id IN (
                       SELECT id FROM paper_trades WHERE user_id = %s AND market = %s
                   )""",
                (user_id, user_id, market)
            )
            conn.execute(
                "DELETE FROM paper_trade_exit_snapshot WHERE user_id = %s AND market = %s",
                (user_id, market)
            )
            conn.execute(
                "DELETE FROM paper_trade_entry_snapshot WHERE user_id = %s AND market = %s",
                (user_id, market)
            )
            conn.execute("DELETE FROM paper_trades WHERE user_id = %s AND market = %s", (user_id, market))
            # Real PostgreSQL Verification / Retention phase — Stage 12 ADR
            # finding: a market-specific reset previously left EVERY
            # idempotency row untouched, including COMPLETED rows for
            # trades in the market just being wiped. Since
            # `paper_trade_idempotency_key` has no `market` column
            # (idempotency records aren't market-scoped by design — see
            # the ADR in this file's module-level notes), the only
            # reliable signal for "which market did this COMPLETED row's
            # trade belong to" is the market field already embedded in its
            # own stored `response_body`. Deletes only COMPLETED rows
            # matching this market: a stale replay of such a key would
            # otherwise return a response pointing at a paper_trade_id
            # that no longer exists. PENDING/FAILED rows have no
            # response_body yet and are left alone — harmless either way
            # (FAILED is immediately reclaimable regardless; a genuinely
            # in-flight PENDING row must never be touched by a reset).
            conn.execute(
                """DELETE FROM paper_trade_idempotency_key
                   WHERE user_id = %s AND operation_type = %s AND status = 'COMPLETED'
                     AND response_body ->> 'market' = %s""",
                (user_id, OPERATION_TYPE_PAPER_BUY, market)
            )
            cash_col = _CASH_COL[market]
            starting = _STARTING[market]
            conn.execute(
                f"""INSERT INTO paper_portfolio (session_id, user_id, {cash_col}) VALUES (%s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET {cash_col} = %s, updated_at = now()""",
                (user_id, user_id, starting, starting)
            )
        return {"message": f"{market} portfolio reset", cash_col: starting}


# Trade Postmortem Engine, Stage 3 (Sprint 1 evidence-provenance rewrite)
# — response schema for GET /postmortem/daily. `schema_version` tracks
# this JSON *shape*; `calculation_version` fields nested per-trade (from
# evidence_attribution.ATTRIBUTION_RULES_VERSION / daily_report.
# DAILY_REPORT_CALCULATION_VERSION) track the *rules*, independently.
# Bumped from 1.0.0 -> 2.0.0: the prototype's narrative/root_cause fields
# are replaced outright (not additively) by the claim/evidence/
# contributor-assessment model — a 1.0.0 client reading a 2.0.0 response
# would find `narrative`/`root_cause` gone, so this is a genuine breaking
# shape change, not an additive one. No production client exists yet
# (this feature has never shipped), so no deprecation shim is needed.
DAILY_POSTMORTEM_SCHEMA_VERSION = "2.0.0"

# Market-local calendar-day timezone per market — the trading day a trade
# "belongs to" is always the market's own local day, never UTC. Reuses
# services/market_hours.py's IST/ET constants (the same timezones the
# market-open/close logic itself uses) rather than defining a second,
# potentially-drifting copy.
_REPORT_TIMEZONE = {"IN": IST, "US": ET}

# Feature gate — PR #32 pre-merge correction. Sprint 1's daily Postmortem
# surface is an evidence-governance foundation, not the complete
# investor-facing product (no close-to-report orchestration, no exit
# snapshots, no persisted reports, no price-path/market/sector/news
# evidence, no export yet — see services/postmortem/evidence_attribution.py's
# module docstring). Most trades today correctly resolve to NOT_TESTABLE/
# INSUFFICIENT_EVIDENCE, which is honest but not yet what a public surface
# should default to showing. Deployed dormant: this endpoint 404s until
# explicitly enabled. Mirrors the exact fail-safe kill-switch pattern
# already proven in this codebase (services/recommendation_consolidation_
# api_composer.py's RCI_LIVE_STOCK_ANALYSIS_ENABLED, services/
# finnhub_client.py's ENABLE_FINNHUB_FOR_IN) — missing, empty, or any
# unrecognized value resolves to disabled; only an explicit accepted true
# value enables it. This function reads os.getenv ONLY for its own flag —
# no other environment access, no logging of user data or evidence.
_TRADE_POSTMORTEM_DAILY_ENV_VAR = "TRADE_POSTMORTEM_DAILY_ENABLED"


def _trade_postmortem_daily_enabled() -> bool:
    raw = os.getenv(_TRADE_POSTMORTEM_DAILY_ENV_VAR, "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


# Trade Postmortem Sprint 3A — separate, dedicated, off-by-default flag
# for price-path enhancement specifically. Unlike the existing Sprint
# 1/2 report generation above (pure computation from already-stored
# data, no external calls), _attempt_price_path_enhancement makes a
# REAL yfinance network call by default — a materially different risk
# profile that must not silently start firing on production sell/
# generate traffic just because this branch merged. Same accepted-value
# convention as _trade_postmortem_daily_enabled.
_TRADE_POSTMORTEM_PRICE_PATH_ENV_VAR = "TRADE_POSTMORTEM_PRICE_PATH_ENABLED"


def _trade_postmortem_price_path_enabled() -> bool:
    raw = os.getenv(_TRADE_POSTMORTEM_PRICE_PATH_ENV_VAR, "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


class EvidenceItemResponse(BaseModel):
    evidence_id: str
    category: str
    name: str
    value: object
    units: str | None
    observation_timestamp: datetime | None
    source: str
    source_type: str
    verification_level: str
    freshness_status: str
    limitations: list[str]


class PostmortemClaimResponse(BaseModel):
    claim_id: str
    report_section: str
    factor: str
    claim_text: str
    evidence_class: str
    confidence_band: str
    supporting_evidence_ids: list[str]
    opposing_evidence_ids: list[str]
    missing_evidence: list[str]
    contradiction_flags: list[str]
    rule_id: str
    rule_version: str
    limitations: list[str]


class SignalEvaluationResponse(BaseModel):
    signal_id: str
    signal_name: str
    expected_interpretation: str | None
    entry_evidence_id: str | None
    comparison_evidence_ids: list[str]
    status: str
    evidence_class: str
    confidence_band: str
    explanation_claim_id: str
    limitations: list[str]


class ContributorAssessmentResponse(BaseModel):
    category: str
    support_level: str
    evidence_class: str
    confidence_band: str
    supporting_evidence_ids: list[str]
    opposing_evidence_ids: list[str]
    claim_id: str
    limitations: list[str]


class EvidenceAttributionResponse(BaseModel):
    evidence_items: list[EvidenceItemResponse]
    claims: list[PostmortemClaimResponse]
    signal_scorecard: list[SignalEvaluationResponse]
    contributor_assessments: list[ContributorAssessmentResponse]
    # Null whenever no single category clears the STRONGLY_SUPPORTED,
    # no-competing-category bar — see evidence_attribution.py's
    # _select_primary_contributor. A null value is an expected, honest
    # result, not a missing-data bug; primary_contributor_claim_id always
    # points at a claim explaining why (either the winning claim, or an
    # INSUFFICIENT_EVIDENCE claim when null).
    primary_contributor: str | None
    primary_contributor_claim_id: str | None
    thesis_verdict: str
    thesis_verdict_claim_id: str
    rule_registry_version: str
    calculation_version: str
    warnings: list[str]


class DailyTradePostmortemResponse(BaseModel):
    trade_id: int
    symbol: str
    market: str
    postmortem: PostmortemResponse
    attribution: EvidenceAttributionResponse


class DailyPostmortemSummaryResponse(BaseModel):
    date: str
    market: str
    trade_count: int
    win_count: int
    loss_count: int
    breakeven_count: int
    indeterminate_count: int
    total_realized_pnl_abs: float | None
    pnl_excluded_trade_count: int
    recurring_supported_contributors: dict[str, int]
    recurring_conflicting_contributors: dict[str, int]
    recurring_not_assessable_count: dict[str, int]
    trades_with_no_supported_contributor: int


class DailyPostmortemReportResponse(BaseModel):
    schema_version: str
    summary: DailyPostmortemSummaryResponse
    trades: list[DailyTradePostmortemResponse]
    calculation_version: str
    warnings: list[str]


def _evidence_item_to_response(item) -> EvidenceItemResponse:
    return EvidenceItemResponse(
        evidence_id=item.evidence_id, category=item.category, name=item.name, value=item.value,
        units=item.units, observation_timestamp=item.observation_timestamp, source=item.source,
        source_type=item.source_type, verification_level=item.verification_level,
        freshness_status=item.freshness_status, limitations=item.limitations,
    )


def _claim_to_response(claim) -> PostmortemClaimResponse:
    return PostmortemClaimResponse(
        claim_id=claim.claim_id, report_section=claim.report_section, factor=claim.factor,
        claim_text=claim.claim_text, evidence_class=claim.evidence_class, confidence_band=claim.confidence_band,
        supporting_evidence_ids=claim.supporting_evidence_ids, opposing_evidence_ids=claim.opposing_evidence_ids,
        missing_evidence=claim.missing_evidence, contradiction_flags=claim.contradiction_flags,
        rule_id=claim.rule_id, rule_version=claim.rule_version, limitations=claim.limitations,
    )


def _signal_evaluation_to_response(sig) -> SignalEvaluationResponse:
    return SignalEvaluationResponse(
        signal_id=sig.signal_id, signal_name=sig.signal_name, expected_interpretation=sig.expected_interpretation,
        entry_evidence_id=sig.entry_evidence_id, comparison_evidence_ids=sig.comparison_evidence_ids,
        status=sig.status, evidence_class=sig.evidence_class, confidence_band=sig.confidence_band,
        explanation_claim_id=sig.explanation_claim_id, limitations=sig.limitations,
    )


def _contributor_to_response(c) -> ContributorAssessmentResponse:
    return ContributorAssessmentResponse(
        category=c.category, support_level=c.support_level, evidence_class=c.evidence_class,
        confidence_band=c.confidence_band, supporting_evidence_ids=c.supporting_evidence_ids,
        opposing_evidence_ids=c.opposing_evidence_ids, claim_id=c.claim_id, limitations=c.limitations,
    )


def _attribution_to_response(attribution) -> EvidenceAttributionResponse:
    return EvidenceAttributionResponse(
        evidence_items=[_evidence_item_to_response(e) for e in attribution.evidence_items],
        claims=[_claim_to_response(c) for c in attribution.claims],
        signal_scorecard=[_signal_evaluation_to_response(s) for s in attribution.signal_scorecard],
        contributor_assessments=[_contributor_to_response(c) for c in attribution.contributor_assessments],
        primary_contributor=attribution.primary_contributor,
        primary_contributor_claim_id=attribution.primary_contributor_claim_id,
        thesis_verdict=attribution.thesis_verdict,
        thesis_verdict_claim_id=attribution.thesis_verdict_claim_id,
        rule_registry_version=attribution.rule_registry_version,
        calculation_version=attribution.calculation_version,
        warnings=attribution.warnings,
    )


def _postmortem_to_response(result, snapshot: EntrySnapshot | None) -> PostmortemResponse:
    return PostmortemResponse(
        schema_version=POSTMORTEM_SCHEMA_VERSION,
        trade_id=result.trade_id,
        status=result.status,
        outcome=result.outcome.value,
        realized_pnl_abs=result.realized_pnl_abs,
        realized_pnl_pct=result.realized_pnl_pct,
        holding_duration_seconds=result.holding_duration_seconds,
        exit_mechanism=result.exit_mechanism.value,
        exit_mechanism_raw=result.exit_mechanism_raw,
        trade_management_mode=result.trade_management_mode,
        auto_close_timing_evidence=result.auto_close_timing_evidence.value,
        evidence_completeness=result.evidence_completeness.value,
        available_evidence_fields=result.available_evidence_fields,
        missing_evidence_fields=result.missing_evidence_fields,
        target_distance_at_exit_pct=result.target_distance_at_exit_pct,
        stop_distance_at_exit_pct=result.stop_distance_at_exit_pct,
        calculation_version=result.calculation_version,
        warnings=result.warnings,
        snapshot_schema_version=snapshot.snapshot_schema_version if snapshot else None,
        evidence_source=snapshot.evidence_source if snapshot else None,
        verification_levels=snapshot.verification_levels if snapshot else None,
    )


def _row_to_closed_trade_record(row) -> ClosedTradeRecord:
    (tid, owner, status, sym, mkt, qty, ep, xp, sl, tp, opened, closed, mgmt_mode, exit_reason,
     rec_source, pick_run_id, pick_rank, rec_gen_at, rec_ref_price, rec_entry_low, rec_entry_high,
     rec_orig_stop, rec_orig_target, model_version, exec_slippage_pct) = row
    return ClosedTradeRecord(
        trade_id=tid,
        status=status,
        symbol=sym,
        market=mkt,
        quantity=qty,
        entry_price=ep,
        exit_price=xp,
        stop_loss=sl,
        target_price=tp,
        opened_at=opened,
        closed_at=closed,
        trade_management_mode=mgmt_mode,
        exit_reason=exit_reason,
        recommendation_source=rec_source,
        daily_pick_run_id=pick_run_id,
        daily_pick_rank=pick_rank,
        recommendation_generated_at=rec_gen_at,
        recommendation_reference_price=rec_ref_price,
        recommendation_entry_low=rec_entry_low,
        recommendation_entry_high=rec_entry_high,
        recommendation_original_stop_loss=rec_orig_stop,
        recommendation_original_target=rec_orig_target,
        model_version=model_version,
        execution_slippage_pct=exec_slippage_pct,
    ), owner


@router.get("/postmortem/daily", response_model=DailyPostmortemReportResponse)
def get_daily_postmortem(
    date: str = Query(..., description="Market-local calendar day, YYYY-MM-DD"),
    market: Literal["IN", "US", "ALL"] = Query("ALL"),
    user_id: str = Depends(get_current_user_id),
):
    """
    Trade Postmortem Engine, Stage 3 (Sprint 1 evidence-provenance rewrite)
    — the Daily Trade Postmortem Report. For every trade this user closed
    on `date` (each trade's own MARKET-LOCAL calendar day — IST for IN,
    ET for US — never UTC), returns the deterministic Phase 1 postmortem
    plus the Sprint 1 evidence attribution (services/postmortem/
    evidence_attribution.py — claim-level provenance, non-circular signal
    scorecard, multi-contributor model), aggregated into one day-level
    report (services/postmortem/daily_report.py).

    A day with zero matching trades returns 200 with an empty `trades` list
    — a quiet trading day is a valid result, not an error.

    Feature-gated (see _trade_postmortem_daily_enabled above) — disabled
    by default. When disabled, returns 404 immediately: no date parsing,
    no database query, no attribution computed, no internal configuration
    detail exposed.
    """
    if not _trade_postmortem_daily_enabled():
        raise HTTPException(status_code=404, detail={"error_code": "FEATURE_NOT_ENABLED"})

    try:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be in YYYY-MM-DD format")

    # Bounded UTC fetch window wide enough to contain `target_date`'s local
    # midnight-to-midnight span in EITHER timezone this endpoint ever
    # converts to (IST is UTC+5:30, ET is UTC-4/-5) — the precise per-row
    # market-local-day match happens in Python below, never as a single
    # ambiguous SQL date cast (see the plan's rationale: IN and US need
    # different tz conversions within the same query).
    window_start = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc) - timedelta(days=1)
    window_end = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=2)

    market_filter_sql = "" if market == "ALL" else " AND market = %s"
    params: tuple = (user_id, window_start, window_end)
    if market != "ALL":
        params = params + (market,)

    with _conn() as conn:
        rows = conn.execute(
            f"""SELECT {_POSTMORTEM_ROW_COLUMNS} FROM paper_trades
                WHERE user_id = %s AND status = 'CLOSED' AND closed_at >= %s AND closed_at < %s{market_filter_sql}
                ORDER BY closed_at ASC""",
            params,
        ).fetchall()

    daily_trades: list[DailyTradePostmortem] = []
    response_trades: list[DailyTradePostmortemResponse] = []
    for row in rows:
        record, owner = _row_to_closed_trade_record(row)
        if owner != user_id:
            continue
        tz = _REPORT_TIMEZONE.get(record.market)
        if tz is None or record.closed_at is None:
            continue
        local_date = record.closed_at.astimezone(tz).date()
        if local_date != target_date:
            continue

        with _conn() as snapshot_conn:
            snapshot = _fetch_entry_snapshot(snapshot_conn, record.trade_id)
        postmortem = compute_postmortem(record, snapshot=snapshot)
        attribution = build_evidence_attribution(postmortem, snapshot)

        daily_trades.append(
            DailyTradePostmortem(
                trade_id=record.trade_id,
                symbol=record.symbol,
                market=record.market,
                postmortem=postmortem,
                attribution=attribution,
            )
        )
        response_trades.append(
            DailyTradePostmortemResponse(
                trade_id=record.trade_id,
                symbol=record.symbol,
                market=record.market,
                postmortem=_postmortem_to_response(postmortem, snapshot),
                attribution=_attribution_to_response(attribution),
            )
        )

    report = build_daily_report(date, market, daily_trades)

    return DailyPostmortemReportResponse(
        schema_version=DAILY_POSTMORTEM_SCHEMA_VERSION,
        summary=DailyPostmortemSummaryResponse(
            date=report.summary.date,
            market=report.summary.market,
            trade_count=report.summary.trade_count,
            win_count=report.summary.win_count,
            loss_count=report.summary.loss_count,
            breakeven_count=report.summary.breakeven_count,
            indeterminate_count=report.summary.indeterminate_count,
            total_realized_pnl_abs=report.summary.total_realized_pnl_abs,
            pnl_excluded_trade_count=report.summary.pnl_excluded_trade_count,
            recurring_supported_contributors=report.summary.recurring_supported_contributors,
            recurring_conflicting_contributors=report.summary.recurring_conflicting_contributors,
            recurring_not_assessable_count=report.summary.recurring_not_assessable_count,
            trades_with_no_supported_contributor=report.summary.trades_with_no_supported_contributor,
        ),
        trades=response_trades,
        calculation_version=report.calculation_version,
        warnings=report.warnings,
    )


@router.get("/postmortem/{trade_id}", response_model=PostmortemResponse)
def get_trade_postmortem(trade_id: int, user_id: str = Depends(get_current_user_id)):
    """
    Trade Postmortem Engine, Phase 1 — deterministic closed-trade analytics
    only. No AI, no causal narrative, no external calls, no writes: this
    endpoint reads one `paper_trades` row and runs a pure local calculation
    (services/postmortem/deterministic.py) over it.

    Ownership check is intentionally STRICTER than this router's other
    trade-scoped endpoints (POST /sell, PATCH /trade, PATCH .../management-mode
    all return 403 "Not your trade" for a mismatched owner, which confirms
    the trade_id exists even when it isn't the caller's). This endpoint
    returns the identical 404 for "trade doesn't exist" and "trade exists
    but belongs to someone else" — a caller can never learn that a given
    trade_id belongs to another user.
    """
    with _conn() as conn:
        row = conn.execute(
            f"SELECT {_POSTMORTEM_ROW_COLUMNS} FROM paper_trades WHERE id = %s",
            (trade_id,)
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Trade not found")

    (tid, owner, status, sym, mkt, qty, ep, xp, sl, tp, opened, closed, mgmt_mode, exit_reason,
     rec_source, pick_run_id, pick_rank, rec_gen_at, rec_ref_price, rec_entry_low, rec_entry_high,
     rec_orig_stop, rec_orig_target, model_version, exec_slippage_pct) = row

    if owner != user_id:
        # Same response as "trade not found" — never leaks that trade_id
        # exists for another user. See docstring above.
        raise HTTPException(status_code=404, detail="Trade not found")

    if status != "CLOSED":
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "TRADE_NOT_CLOSED",
                "message": "Postmortem reports are only available for closed trades",
            },
        )

    record = ClosedTradeRecord(
        trade_id=tid,
        status=status,
        symbol=sym,
        market=mkt,
        quantity=qty,
        entry_price=ep,
        exit_price=xp,
        stop_loss=sl,
        target_price=tp,
        opened_at=opened,
        closed_at=closed,
        trade_management_mode=mgmt_mode,
        exit_reason=exit_reason,
        recommendation_source=rec_source,
        daily_pick_run_id=pick_run_id,
        daily_pick_rank=pick_rank,
        recommendation_generated_at=rec_gen_at,
        recommendation_reference_price=rec_ref_price,
        recommendation_entry_low=rec_entry_low,
        recommendation_entry_high=rec_entry_high,
        recommendation_original_stop_loss=rec_orig_stop,
        recommendation_original_target=rec_orig_target,
        model_version=model_version,
        execution_slippage_pct=exec_slippage_pct,
    )
    with _conn() as snapshot_conn:
        snapshot = _fetch_entry_snapshot(snapshot_conn, tid)
    result = compute_postmortem(record, snapshot=snapshot)

    return PostmortemResponse(
        schema_version=POSTMORTEM_SCHEMA_VERSION,
        trade_id=result.trade_id,
        status=result.status,
        outcome=result.outcome.value,
        realized_pnl_abs=result.realized_pnl_abs,
        realized_pnl_pct=result.realized_pnl_pct,
        holding_duration_seconds=result.holding_duration_seconds,
        exit_mechanism=result.exit_mechanism.value,
        exit_mechanism_raw=result.exit_mechanism_raw,
        trade_management_mode=result.trade_management_mode,
        auto_close_timing_evidence=result.auto_close_timing_evidence.value,
        evidence_completeness=result.evidence_completeness.value,
        available_evidence_fields=result.available_evidence_fields,
        missing_evidence_fields=result.missing_evidence_fields,
        target_distance_at_exit_pct=result.target_distance_at_exit_pct,
        stop_distance_at_exit_pct=result.stop_distance_at_exit_pct,
        calculation_version=result.calculation_version,
        warnings=result.warnings,
        snapshot_schema_version=snapshot.snapshot_schema_version if snapshot else None,
        evidence_source=snapshot.evidence_source if snapshot else None,
        verification_levels=snapshot.verification_levels if snapshot else None,
    )




class GenerateReportResponse(BaseModel):
    trade_id: int
    # GENERATED (the EFFECTIVE report — the most authoritative one
    # available after this call, which may be the newly-created
    # price-path report even when the underlying Sprint 2 report already
    # existed — see Stage H2 durable-lifecycle correction) |
    # ALREADY_COMPLETE (the effective report already existed — returned
    # idempotently, nothing generated this call) |
    # IN_PROGRESS (another valid, non-expired lease currently owns the
    # relevant outbox row — no concurrent duplicate generation was
    # attempted; retry later).
    generation_status: str
    status: str | None = None
    report_schema_version: str | None = None
    calculation_version: str | None = None
    attribution_rules_version: str | None = None
    generated: bool
    evidence_gap_count: int | None = None
    # Additive Sprint 3A fields — existing clients reading only the
    # fields above remain fully compatible; these expose the granular
    # base-vs-price-path breakdown for clients that want it.
    base_generation_status: str | None = None
    price_path_generation_status: str | None = None
    effective_report_version: str | None = None


def _build_generate_response(*, trade_id: int, user_id: str, generation_status: str, report, generated: bool) -> "GenerateReportResponse":
    """Trade Postmortem Sprint 3A, Stage H2 durable-lifecycle correction
    — shared response builder for every /generate return site.

    When the price-path flag is enabled, attempts enhancement via its
    OWN leased outbox lifecycle (see _attempt_price_path_enhancement)
    and, when it PRODUCES a report this call (PRICE_PATH_GENERATED or
    PRICE_PATH_ALREADY_COMPLETE), that report becomes the EFFECTIVE
    report — `generation_status`/`generated`/`report_schema_version`/etc
    describe IT, never silently describing the Sprint 2 report while
    returning Sprint 3A fields (Programme Director finding #9), and a
    freshly-generated price-path report is never reported with
    generated=False merely because the underlying Sprint 2 report
    already existed (finding #1/requirement 1). `base_generation_status`
    preserves the original Sprint 2-level classification unconditionally."""
    effective = report
    effective_generation_status = generation_status
    effective_generated = generated
    price_path_status = None

    if _trade_postmortem_price_path_enabled():
        enhanced, price_path_status = _attempt_price_path_enhancement(user_id=user_id, trade_id=trade_id)
        if enhanced is not None:
            effective = enhanced
            effective_generated = price_path_status == PRICE_PATH_GENERATED
            effective_generation_status = (
                "GENERATED" if price_path_status == PRICE_PATH_GENERATED else "ALREADY_COMPLETE"
            )
        # A None result (IN_PROGRESS / FAILED_RETRYABLE / FAILED_TERMINAL /
        # NOT_YET_AVAILABLE) never overwrites `effective` — the caller's
        # own Sprint 2 report/status stands, with price_path_status
        # exposed separately so a failed attempt is never silently
        # represented as the Sprint 2 report being ALREADY_COMPLETE for
        # the price-path dimension too (finding #10).

    return GenerateReportResponse(
        trade_id=trade_id, generation_status=effective_generation_status, status=effective.status,
        report_schema_version=effective.report_schema_version,
        calculation_version=effective.calculation_version,
        attribution_rules_version=effective.attribution_rules_version,
        generated=effective_generated, evidence_gap_count=len(effective.evidence_gaps),
        base_generation_status=generation_status, price_path_generation_status=price_path_status,
        effective_report_version=effective.report_schema_version,
    )


@router.post("/postmortem/{trade_id}/generate", response_model=GenerateReportResponse)
def generate_trade_postmortem_endpoint(trade_id: int, user_id: str = Depends(get_current_user_id)):
    """
    Trade Postmortem Engine, Sprint 2 — safe, idempotent, user-scoped
    generation trigger, and (correction phase, Stage 3) THE explicit
    recovery mechanism for a PENDING/FAILED_RETRYABLE/stale-GENERATING
    outbox row — GET /postmortem/{trade_id} above remains completely
    read-only and unchanged by this sprint; it never claims, generates,
    or writes anything. Callers that want a trade's report durably
    persisted (as opposed to Phase 1's own always-fresh computation) call
    this endpoint, not GET.

    This endpoint:
    - derives the user strictly from the authenticated session, never a
      request parameter;
    - never alters the trade itself (no UPDATE to paper_trades anywhere
      in this call path);
    - never accepts or injects evidence from the request body — the only
      input is `trade_id`, evidence comes exclusively from already-
      stored trade/entry-snapshot/exit-snapshot rows;
    - is idempotent for a genuinely already-COMPLETE/LIMITED_EVIDENCE
      report (returns it verbatim, `generation_status="ALREADY_COMPLETE"`,
      `generated=false`) and never runs a concurrent duplicate generation
      attempt against a row another valid lease currently owns
      (`generation_status="IN_PROGRESS"`);
    - for a historical trade with no outbox row yet (closed before this
      sprint's durability layer existed), creates one for the CURRENT
      version triple (idempotent — `close_service._insert_outbox_record`'s
      own ON CONFLICT DO NOTHING) before claiming/generating.

    Not gated by TRADE_POSTMORTEM_DAILY_ENABLED — that flag governs only
    the daily, multi-trade aggregate surface (GET /postmortem/daily and
    the /postmortem frontend page), which remains dormant. This
    single-trade generation endpoint is part of the same durability
    layer Phase 1's own single-trade GET endpoint already belongs to.
    """
    with _conn() as conn:
        row = conn.execute(
            f"SELECT {_POSTMORTEM_ROW_COLUMNS} FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Trade not found")

        record, owner = _row_to_closed_trade_record(row)
        if owner != user_id:
            # Same "identical 404" convention as GET /postmortem/{trade_id}
            # — never lets a caller distinguish "doesn't exist" from
            # "exists but isn't yours".
            raise HTTPException(status_code=404, detail="Trade not found")
        if record.status != "CLOSED":
            raise HTTPException(
                status_code=409,
                detail={"error_code": "TRADE_NOT_CLOSED", "message": "Postmortem reports are only available for closed trades"},
            )

        # Idempotent, current-version-triple outbox row — creates one for
        # a historical trade that predates this sprint's durability layer,
        # or returns the existing id if this trade already has one for
        # these exact versions (a prior close, or a prior /generate call).
        outbox_id, _outbox_created = _insert_outbox_record(
            conn, trade_id=trade_id, user_id=user_id, source_request_id=None
        )

        # Already-settled report for the CURRENT version triple: return it
        # idempotently, never re-generate. This is the primary idempotency
        # path (Sprint 1/2's own "same versions -> same report" contract),
        # checked BEFORE ever attempting a claim.
        existing_report = report_store.get_current_report(
            conn, paper_trade_id=trade_id, user_id=user_id,
            report_schema_version=generation_service.REPORT_SCHEMA_VERSION,
            calculation_version=CALCULATION_VERSION, attribution_rules_version=ATTRIBUTION_RULES_VERSION,
        )
        if existing_report is not None:
            # settled = (generation_status, report, generated) — the
            # price-path enhancement call happens AFTER this `with
            # _conn():` block exits below; nothing in this branch
            # returns directly.
            settled = ("ALREADY_COMPLETE", existing_report, False)
        else:
            report, status_tag = _claim_and_run_generation(
                conn, user_id=user_id, trade_id=trade_id, outbox_id=outbox_id
            )

            if status_tag == "GENERATED":
                settled = ("GENERATED", report, True)

            elif status_tag == "TERMINAL_NO_REPORT":
                # Attempt limit exceeded (MAX_ATTEMPTS_BEFORE_TERMINAL) —
                # a stable, honest terminal failure, never an infinite
                # retry loop and never a fabricated report.
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error_code": "GENERATION_ATTEMPTS_EXHAUSTED",
                        "message": "report generation failed repeatedly for this trade and will not be retried automatically",
                    },
                )

            elif status_tag == "NOTHING_TO_DO":
                # Benign race: the row settled COMPLETE/LIMITED_EVIDENCE
                # between our existing_report check above and the claim
                # attempt (e.g. a concurrent best-effort generation from
                # a /sell call finished in between). Re-check once — the
                # report/outbox consistency invariant (Stage 3/8)
                # guarantees it must exist now if the outbox truly
                # settled terminal.
                settled_report = report_store.get_current_report(
                    conn, paper_trade_id=trade_id, user_id=user_id,
                    report_schema_version=generation_service.REPORT_SCHEMA_VERSION,
                    calculation_version=CALCULATION_VERSION, attribution_rules_version=ATTRIBUTION_RULES_VERSION,
                )
                if settled_report is None:
                    # Genuinely unexpected: outbox says terminal-complete
                    # but no report row exists for these versions — the
                    # exact report/outbox contradiction Stage 3/8
                    # requires never silently accepting. Surfaced as a
                    # 500 with a stable error code rather than papered
                    # over with an IN_PROGRESS response.
                    log.warning("[postmortem_generation_terminal_failure] trade_id=%s outbox_id=%s error_code=%s",
                                trade_id, outbox_id, "REPORT_OUTBOX_INCONSISTENCY")
                    raise HTTPException(
                        status_code=500,
                        detail={
                            "error_code": "REPORT_OUTBOX_INCONSISTENCY",
                            "message": "outbox settled without a corresponding persisted report",
                        },
                    )
                settled = ("ALREADY_COMPLETE", settled_report, False)

            else:
                # status_tag == "IN_PROGRESS" — another valid, non-
                # expired lease currently owns this row; never run
                # concurrent duplicate work, and never attempt price-
                # path enhancement against a report that doesn't exist
                # yet.
                return GenerateReportResponse(
                    trade_id=trade_id, generation_status=REPORT_GENERATION_IN_PROGRESS, generated=False,
                )

    # Trade Postmortem Sprint 3A, Stage H2B — price-path enhancement, if
    # enabled, runs here: AFTER the `with _conn():` block above has
    # released its connection, exactly matching the sell path's own
    # post-commit discipline. `settled` is always bound by this point
    # (every path that reaches here assigned it above; the only path
    # that doesn't is the IN_PROGRESS early return).
    generation_status, report, generated = settled
    return _build_generate_response(
        trade_id=trade_id, user_id=user_id, generation_status=generation_status,
        report=report, generated=generated,
    )
