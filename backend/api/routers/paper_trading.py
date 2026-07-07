import os
import logging
from fastapi import APIRouter, Depends, HTTPException
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


def _summarize_closed_bucket(trades: list[dict]) -> dict:
    """
    Target Hit Rate is defined as:
        trades closed because their defined target was hit
        / trades with a conclusive target-or-stop-loss outcome
    — never against total closed trades or P&L sign. `exit_reason` (set only
    by POST /sell, either explicitly by the client on an auto-close trigger
    or a manual close) is the sole authoritative outcome field:
    "TARGET_HIT"/"STOP_LOSS" are conclusive; "MANUAL", None (legacy trades
    closed before this column existed, or a manual close that recorded no
    reason), or any other value are non-conclusive and excluded from the
    hit-rate denominator — but still counted in P&L/average-return below,
    since those are realized-outcome metrics, not target-accuracy metrics.
    """
    count = len(trades)
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
        "target_hit_count": target_hit,
        "stop_loss_count": stop_loss,
        "conclusive_count": conclusive,
        "other_count": other,
        "target_hit_rate_pct": round(target_hit / conclusive * 100, 1) if conclusive > 0 else None,
        "net_realized_pnl": net_realized_pnl,
        "avg_realized_return_pct": avg_realized_return_pct,
    }


def _closed_trade_summary_for_market(closed_trades_for_market: list[dict]) -> dict:
    buckets: dict[str, list[dict]] = {h: [] for h in _CLOSED_HORIZONS}
    unclassified: list[dict] = []
    for t in closed_trades_for_market:
        bucket = buckets.get(t["horizon"])
        (bucket if bucket is not None else unclassified).append(t)

    summary = {h: _summarize_closed_bucket(buckets[h]) for h in _CLOSED_HORIZONS}
    if unclassified:
        summary["unclassified"] = _summarize_closed_bucket(unclassified)
    return summary


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
def get_portfolio(user_id: str = Depends(get_current_user_id)):
    portfolio = _ensure_portfolio(user_id)
    with _conn() as conn:
        trades = conn.execute(
            """SELECT id, symbol, market, quantity, entry_price, exit_price,
                      status, signal, horizon, opened_at, closed_at, stop_loss, target_price,
                      trade_management_mode, exit_reason
               FROM paper_trades WHERE user_id = %s ORDER BY opened_at DESC""",
            (user_id,)
        ).fetchall()

    open_trades = []
    closed_trades = []
    total_realized_in = 0.0
    total_realized_us = 0.0

    for t in trades:
        tid, sym, mkt, qty, ep, xp, status, sig, hor, opened, closed, sl, tp, mgmt_mode, exit_reason = t
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
        if status == "OPEN":
            open_trades.append(trade)
        else:
            realized = round((xp - ep) * qty, 2) if xp else 0.0
            trade["realized_pnl"] = realized
            if mkt == "US":
                total_realized_us += realized
            else:
                total_realized_in += realized
            closed_trades.append(trade)

    # Market-scoped, per-horizon closed-trade summary — computed once here,
    # server-side, from the same `closed_trades` list already built above
    # (no second query). IN and US are summarized independently and never
    # combined, matching every other paper-trading metric's existing
    # per-ledger scoping (see _CASH_COL/STARTING). The frontend renders this
    # verbatim; it must not recompute these figures from the trade rows.
    closed_trade_summary = {
        "IN": _closed_trade_summary_for_market([t for t in closed_trades if t["market"] == "IN"]),
        "US": _closed_trade_summary_for_market([t for t in closed_trades if t["market"] == "US"]),
    }

    return {
        "user_id": user_id,
        "cash": round(portfolio["cash"], 2),
        "cash_usd": round(portfolio["cash_usd"], 2),
        "starting_cash": STARTING_CASH_IN,
        "starting_cash_usd": STARTING_CASH_US,
        "open_trades": open_trades,
        "closed_trades": closed_trades,
        "total_realized_pnl": round(total_realized_in, 2),
        "total_realized_pnl_usd": round(total_realized_us, 2),
        "closed_trade_summary": closed_trade_summary,
        "email_notifications_enabled": portfolio["email_notifications_enabled"],
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
