import os
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Literal
from services.auth import get_current_user_id
from services.market_hours import is_market_open as _is_market_open

log = logging.getLogger(__name__)
router = APIRouter()

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
        trade["realized_pnl"] = round((xp - ep) * qty, 2) if xp else 0.0
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


def _conn():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None)


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

class BuyRequest(BaseModel):
    symbol: str
    market: Literal["IN", "US"]
    quantity: int
    price: float
    signal: str = "HOLD"
    horizon: str = "medium"
    stop_loss: float | None = None
    target_price: float | None = None
    # "ai_assisted" is accepted (not rejected) so a trade opened while that
    # option is visible-but-disabled in the UI can't ever reach the backend
    # today — validated here anyway since no client currently sends it.
    trade_management_mode: Literal["manual", "auto", "ai_assisted"] = "manual"


class SellRequest(BaseModel):
    price: float
    # Set by the client when a close was triggered by an auto-close rule
    # (stop-loss/target hit) rather than a manual "Close" click — omitted
    # (None) for an ordinary manual close.
    exit_reason: Literal["STOP_LOSS", "TARGET_HIT", "MANUAL"] | None = None

class EditRequest(BaseModel):
    stop_loss: float | None = None
    target_price: float | None = None
    entry_price: float | None = None


class ManagementModeRequest(BaseModel):
    # "ai_assisted" is intentionally excluded from this Literal — it isn't a
    # rejected-at-runtime value like BuyRequest's, it simply isn't an option
    # this endpoint accepts at all yet, since it has no functional behavior.
    trade_management_mode: Literal["manual", "auto"]


class NotificationPreferenceRequest(BaseModel):
    email_notifications_enabled: bool


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
def paper_buy(req: BuyRequest, user_id: str = Depends(get_current_user_id)):
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

    with _conn() as conn:
        # Atomic check-and-debit: the WHERE clause re-checks the balance at
        # write time, inside the same statement, instead of trusting a value
        # read moments earlier. Two concurrent buys can no longer both pass a
        # stale balance check and both decrement past zero.
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

        row = conn.execute(
            """INSERT INTO paper_trades
               (session_id, user_id, symbol, market, quantity, entry_price, signal, horizon, stop_loss, target_price, trade_management_mode)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (user_id, user_id, req.symbol.upper(), req.market, req.quantity,
             req.price, req.signal, req.horizon, req.stop_loss, req.target_price, req.trade_management_mode)
        ).fetchone()
        remaining_cash = debited[0]  # already post-debit — RETURNING reflects the new balance

    return {
        "message": "Paper buy placed",
        "trade_id": row[0],
        "symbol": req.symbol.upper(),
        "market": req.market,
        "quantity": req.quantity,
        "entry_price": req.price,
        "cost": round(cost, 2),
        "remaining_cash": round(remaining_cash, 2),
    }


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
        pnl = (req.price - ep) * qty

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
        "pnl": round(pnl, 2),
        "pnl_pct": round((req.price - ep) / ep * 100, 2) if ep and ep > 0 else 0,
        "proceeds": round(proceeds, 2),
    }


@router.patch("/trade/{trade_id}")
def edit_trade(trade_id: int, req: EditRequest, user_id: str = Depends(get_current_user_id)):
    with _conn() as conn:
        trade = conn.execute(
            "SELECT user_id, status, entry_price, quantity, market FROM paper_trades WHERE id = %s",
            (trade_id,)
        ).fetchone()
        if trade is None:
            raise HTTPException(status_code=404, detail="Trade not found")
        if trade[0] != user_id:
            raise HTTPException(status_code=403, detail="Not your trade")
        if trade[1] != "OPEN":
            raise HTTPException(status_code=400, detail="Cannot edit a closed trade")

        old_entry, qty, trade_market = trade[2], trade[3], trade[4]
        if trade_market not in _CASH_COL:
            raise HTTPException(status_code=500, detail=f"Trade has an unrecognized market '{trade_market}' — cannot determine which cash ledger to adjust")
        cash_col = _CASH_COL[trade_market]

        conn.execute(
            "UPDATE paper_trades SET stop_loss = %s, target_price = %s WHERE id = %s",
            (req.stop_loss, req.target_price, trade_id)
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
        if market == "ALL":
            conn.execute("DELETE FROM paper_trades WHERE user_id = %s", (user_id,))
            conn.execute(
                """INSERT INTO paper_portfolio (session_id, user_id, cash, cash_usd) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (user_id) DO UPDATE SET cash = %s, cash_usd = %s, updated_at = now()""",
                (user_id, user_id, STARTING_CASH_IN, STARTING_CASH_US, STARTING_CASH_IN, STARTING_CASH_US)
            )
            return {"message": "Portfolio reset", "cash": STARTING_CASH_IN, "cash_usd": STARTING_CASH_US}

        conn.execute("DELETE FROM paper_trades WHERE user_id = %s AND market = %s", (user_id, market))
        cash_col = _CASH_COL[market]
        starting = _STARTING[market]
        conn.execute(
            f"""INSERT INTO paper_portfolio (session_id, user_id, {cash_col}) VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET {cash_col} = %s, updated_at = now()""",
            (user_id, user_id, starting, starting)
        )
        return {"message": f"{market} portfolio reset", cash_col: starting}
