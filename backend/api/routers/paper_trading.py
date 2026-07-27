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
from services.postmortem.idempotency import (
    OPERATION_TYPE_PAPER_BUY,
    POLL_ATTEMPTS,
    POLL_INTERVAL_SECONDS,
    ExistingIdempotencyRow,
    IdempotencyAction,
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

    @field_validator("price")
    @classmethod
    def _validate_finite(cls, v):
        return _reject_non_finite(v)


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
    conn, user_id: str, idempotency_key: str, fingerprint: str
) -> _IdempotencyReservation:
    """Durable Buy-idempotency reservation — see
    services/postmortem/idempotency.py's module docstring for the full
    design rationale. Each iteration is its own read/decide/act step
    against fresh database state (never cached across iterations), so a
    concurrent request's progress is always correctly observed.

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
                (user_id, OPERATION_TYPE_PAPER_BUY, idempotency_key, fingerprint)
            ).fetchone()
            if inserted is not None:
                _metrics.increment(_metrics.COUNTER_FRESH_RESERVATION)
                return _IdempotencyReservation(IdempotencyAction.PROCEED_FRESH.value, inserted[0], None)

            row = conn.execute(
                """SELECT id, status, request_fingerprint, response_body, created_at
                   FROM paper_trade_idempotency_key
                   WHERE user_id = %s AND operation_type = %s AND idempotency_key = %s""",
                (user_id, OPERATION_TYPE_PAPER_BUY, idempotency_key)
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
    if req.price <= 0:
        raise HTTPException(status_code=400, detail="Price must be > 0")

    with _conn() as conn:
        trade = conn.execute(
            "SELECT user_id, symbol, quantity, entry_price, status, market FROM paper_trades WHERE id = %s",
            (trade_id,)
        ).fetchone()

        if trade is None:
            raise HTTPException(status_code=404, detail="Trade not found")

        owner, sym, qty, ep, status, trade_market = trade
        if owner != user_id:
            raise HTTPException(status_code=403, detail="Not your trade")
        if status != "OPEN":
            raise HTTPException(status_code=400, detail="Trade already closed")
        if not _is_market_open(trade_market):
            raise HTTPException(status_code=400, detail=f"{trade_market} market is closed — orders are paused until it reopens")

        cash_col = _CASH_COL[trade_market]
        proceeds = req.price * qty
        # Trade Postmortem Engine, Phase 1 — same shared function as
        # _trade_row_to_dict above; single authoritative P&L calculation
        # path. pnl_pct preserves this endpoint's pre-existing `0` fallback
        # for the (practically unreachable, since /buy and /sell both
        # reject price <= 0) ep <= 0 case, rather than switching this
        # response to `None`.
        pnl = compute_realized_pnl_abs(ep, req.price, qty)
        pnl_pct = compute_realized_pnl_pct(ep, req.price)
        pnl_pct = pnl_pct if pnl_pct is not None else 0

        # WHERE ... AND status = 'OPEN' makes this close idempotent at the
        # database level: a duplicate/racing close request (e.g. an auto-close
        # firing twice from two browser tabs) updates zero rows the second
        # time instead of double-crediting cash or overwriting the exit price.
        closed = conn.execute(
            "UPDATE paper_trades SET exit_price = %s, status = 'CLOSED', closed_at = now(), exit_reason = %s "
            "WHERE id = %s AND status = 'OPEN' RETURNING id",
            (req.price, req.exit_reason, trade_id)
        ).fetchone()
        if closed is None:
            raise HTTPException(status_code=400, detail="Trade already closed")
        conn.execute(
            f"UPDATE paper_portfolio SET {cash_col} = {cash_col} + %s, updated_at = now() WHERE user_id = %s",
            (proceeds, user_id)
        )

    return {
        "message": "Paper sell placed",
        "trade_id": trade_id,
        "symbol": sym,
        "market": trade_market,
        "quantity": qty,
        "entry_price": ep,
        "exit_price": req.price,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "proceeds": round(proceeds, 2),
    }


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
