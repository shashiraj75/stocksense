import os
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Literal
from services.market_hours import is_market_open as _is_market_open

log = logging.getLogger(__name__)
router = APIRouter()

STARTING_CASH = 1_000_000.0  # ₹10,00,000 virtual cash


def _conn():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None)


def _ensure_portfolio(user_id: str, email: str | None = None) -> dict:
    with _conn() as conn:
        row = conn.execute(
            "SELECT cash FROM paper_portfolio WHERE user_id = %s",
            (user_id,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO paper_portfolio (session_id, user_id, cash, email) VALUES (%s, %s, %s, %s)",
                (user_id, user_id, STARTING_CASH, email)
            )
            return {"cash": STARTING_CASH}
        if email:
            # Keep email fresh — cheap to update on every call, no extra round trip
            conn.execute(
                "UPDATE paper_portfolio SET email = %s WHERE user_id = %s AND (email IS DISTINCT FROM %s)",
                (email, user_id, email)
            )
        return {"cash": row[0]}


# ── Models ────────────────────────────────────────────────────────────────────

class BuyRequest(BaseModel):
    user_id: str
    symbol: str
    market: Literal["IN", "US"]
    quantity: int
    price: float
    signal: str = "HOLD"
    horizon: str = "medium"
    stop_loss: float | None = None
    target_price: float | None = None
    email: str | None = None


class SellRequest(BaseModel):
    user_id: str
    price: float

class EditRequest(BaseModel):
    user_id: str
    stop_loss: float | None = None
    target_price: float | None = None
    entry_price: float | None = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/portfolio")
def get_portfolio(user_id: str = Query(...), email: str | None = Query(None)):
    portfolio = _ensure_portfolio(user_id, email)
    with _conn() as conn:
        trades = conn.execute(
            """SELECT id, symbol, market, quantity, entry_price, exit_price,
                      status, signal, horizon, opened_at, closed_at, stop_loss, target_price
               FROM paper_trades WHERE user_id = %s ORDER BY opened_at DESC""",
            (user_id,)
        ).fetchall()

    open_trades = []
    closed_trades = []
    total_realized = 0.0

    for t in trades:
        tid, sym, mkt, qty, ep, xp, status, sig, hor, opened, closed, sl, tp = t
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
        }
        if status == "OPEN":
            open_trades.append(trade)
        else:
            realized = round((xp - ep) * qty, 2) if xp else 0.0
            trade["realized_pnl"] = realized
            total_realized += realized
            closed_trades.append(trade)

    return {
        "user_id": user_id,
        "cash": round(portfolio["cash"], 2),
        "starting_cash": STARTING_CASH,
        "open_trades": open_trades,
        "closed_trades": closed_trades,
        "total_realized_pnl": round(total_realized, 2),
    }


@router.post("/buy")
def paper_buy(req: BuyRequest):
    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be > 0")
    if req.price <= 0:
        raise HTTPException(status_code=400, detail="Price must be > 0")
    if not _is_market_open(req.market):
        raise HTTPException(status_code=400, detail=f"{req.market} market is closed — orders are paused until it reopens")

    cost = req.price * req.quantity
    portfolio = _ensure_portfolio(req.user_id, req.email)

    if portfolio["cash"] < cost:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient funds. Available: ₹{portfolio['cash']:,.2f}, Required: ₹{cost:,.2f}"
        )

    with _conn() as conn:
        conn.execute(
            "UPDATE paper_portfolio SET cash = cash - %s, updated_at = now() WHERE user_id = %s",
            (cost, req.user_id)
        )
        row = conn.execute(
            """INSERT INTO paper_trades
               (session_id, user_id, symbol, market, quantity, entry_price, signal, horizon, stop_loss, target_price)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (req.user_id, req.user_id, req.symbol.upper(), req.market, req.quantity,
             req.price, req.signal, req.horizon, req.stop_loss, req.target_price)
        ).fetchone()

    return {
        "message": "Paper buy placed",
        "trade_id": row[0],
        "symbol": req.symbol.upper(),
        "quantity": req.quantity,
        "entry_price": req.price,
        "cost": round(cost, 2),
        "remaining_cash": round(portfolio["cash"] - cost, 2),
    }


@router.post("/sell/{trade_id}")
def paper_sell(trade_id: int, req: SellRequest):
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
        if owner != req.user_id:
            raise HTTPException(status_code=403, detail="Not your trade")
        if status != "OPEN":
            raise HTTPException(status_code=400, detail="Trade already closed")
        if not _is_market_open(trade_market):
            raise HTTPException(status_code=400, detail=f"{trade_market} market is closed — orders are paused until it reopens")

        proceeds = req.price * qty
        pnl = (req.price - ep) * qty

        conn.execute(
            "UPDATE paper_trades SET exit_price = %s, status = 'CLOSED', closed_at = now() WHERE id = %s",
            (req.price, trade_id)
        )
        conn.execute(
            "UPDATE paper_portfolio SET cash = cash + %s, updated_at = now() WHERE user_id = %s",
            (proceeds, req.user_id)
        )

    return {
        "message": "Paper sell placed",
        "trade_id": trade_id,
        "symbol": sym,
        "quantity": qty,
        "entry_price": ep,
        "exit_price": req.price,
        "pnl": round(pnl, 2),
        "pnl_pct": round((req.price - ep) / ep * 100, 2) if ep and ep > 0 else 0,
        "proceeds": round(proceeds, 2),
    }


@router.patch("/trade/{trade_id}")
def edit_trade(trade_id: int, req: EditRequest):
    with _conn() as conn:
        trade = conn.execute(
            "SELECT user_id, status, entry_price, quantity FROM paper_trades WHERE id = %s",
            (trade_id,)
        ).fetchone()
        if trade is None:
            raise HTTPException(status_code=404, detail="Trade not found")
        if trade[0] != req.user_id:
            raise HTTPException(status_code=403, detail="Not your trade")
        if trade[1] != "OPEN":
            raise HTTPException(status_code=400, detail="Cannot edit a closed trade")

        old_entry, qty = trade[2], trade[3]

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
                "UPDATE paper_portfolio SET cash = cash + %s, updated_at = now() WHERE user_id = %s",
                (cash_delta, req.user_id)
            )

    return {"message": "Trade updated", "trade_id": trade_id}


@router.post("/reset")
def reset_portfolio(user_id: str = Query(...)):
    with _conn() as conn:
        conn.execute("DELETE FROM paper_trades WHERE user_id = %s", (user_id,))
        conn.execute(
            """INSERT INTO paper_portfolio (session_id, user_id, cash) VALUES (%s, %s, %s)
               ON CONFLICT (user_id) DO UPDATE SET cash = %s, updated_at = now()""",
            (user_id, user_id, STARTING_CASH, STARTING_CASH)
        )
    return {"message": "Portfolio reset", "cash": STARTING_CASH}
