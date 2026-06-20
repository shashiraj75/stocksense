import os
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Literal

log = logging.getLogger(__name__)
router = APIRouter()

STARTING_CASH = 1_000_000.0  # ₹10,00,000 virtual cash


def _conn():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None)


def _ensure_portfolio(session_id: str) -> dict:
    with _conn() as conn:
        row = conn.execute(
            "SELECT cash, created_at FROM paper_portfolio WHERE session_id = %s",
            (session_id,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO paper_portfolio (session_id, cash) VALUES (%s, %s)",
                (session_id, STARTING_CASH)
            )
            return {"cash": STARTING_CASH}
        return {"cash": row[0]}


# ── Models ────────────────────────────────────────────────────────────────────

class BuyRequest(BaseModel):
    session_id: str
    symbol: str
    market: Literal["IN", "US"]
    quantity: int
    price: float          # current live price passed from frontend
    signal: str = "HOLD"
    horizon: str = "medium"
    stop_loss: float | None = None
    target_price: float | None = None


class SellRequest(BaseModel):
    session_id: str
    price: float          # current live price passed from frontend

class EditRequest(BaseModel):
    session_id: str
    stop_loss: float | None = None
    target_price: float | None = None
    entry_price: float | None = None  # correction only — adjusts cash to reflect true entry


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/portfolio")
def get_portfolio(session_id: str = Query(...)):
    portfolio = _ensure_portfolio(session_id)
    with _conn() as conn:
        trades = conn.execute(
            """SELECT id, symbol, market, quantity, entry_price, exit_price,
                      status, signal, horizon, opened_at, closed_at, stop_loss, target_price
               FROM paper_trades WHERE session_id = %s ORDER BY opened_at DESC""",
            (session_id,)
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
        "session_id": session_id,
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

    cost = req.price * req.quantity
    portfolio = _ensure_portfolio(req.session_id)

    if portfolio["cash"] < cost:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient funds. Available: ₹{portfolio['cash']:,.2f}, Required: ₹{cost:,.2f}"
        )

    with _conn() as conn:
        conn.execute(
            "UPDATE paper_portfolio SET cash = cash - %s, updated_at = now() WHERE session_id = %s",
            (cost, req.session_id)
        )
        row = conn.execute(
            """INSERT INTO paper_trades (session_id, symbol, market, quantity, entry_price, signal, horizon, stop_loss, target_price)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (req.session_id, req.symbol.upper(), req.market, req.quantity,
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
            "SELECT session_id, symbol, quantity, entry_price, status FROM paper_trades WHERE id = %s",
            (trade_id,)
        ).fetchone()

        if trade is None:
            raise HTTPException(status_code=404, detail="Trade not found")

        sess, sym, qty, ep, status = trade
        if sess != req.session_id:
            raise HTTPException(status_code=403, detail="Not your trade")
        if status != "OPEN":
            raise HTTPException(status_code=400, detail="Trade already closed")

        proceeds = req.price * qty
        pnl = (req.price - ep) * qty

        conn.execute(
            """UPDATE paper_trades
               SET exit_price = %s, status = 'CLOSED', closed_at = now()
               WHERE id = %s""",
            (req.price, trade_id)
        )
        conn.execute(
            "UPDATE paper_portfolio SET cash = cash + %s, updated_at = now() WHERE session_id = %s",
            (proceeds, req.session_id)
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
            "SELECT session_id, status, entry_price, quantity FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()
        if trade is None:
            raise HTTPException(status_code=404, detail="Trade not found")
        if trade[0] != req.session_id:
            raise HTTPException(status_code=403, detail="Not your trade")
        if trade[1] != "OPEN":
            raise HTTPException(status_code=400, detail="Cannot edit a closed trade")

        old_entry, qty = trade[2], trade[3]

        conn.execute(
            "UPDATE paper_trades SET stop_loss = %s, target_price = %s WHERE id = %s",
            (req.stop_loss, req.target_price, trade_id)
        )

        # If entry price correction requested, adjust cash to reflect the difference
        if req.entry_price and req.entry_price > 0 and req.entry_price != old_entry:
            cash_delta = (old_entry - req.entry_price) * qty  # positive = refund, negative = charge
            conn.execute(
                "UPDATE paper_trades SET entry_price = %s WHERE id = %s",
                (req.entry_price, trade_id)
            )
            conn.execute(
                "UPDATE paper_portfolio SET cash = cash + %s, updated_at = now() WHERE session_id = %s",
                (cash_delta, req.session_id)
            )

    return {"message": "Trade updated", "trade_id": trade_id}


@router.post("/reset")
def reset_portfolio(session_id: str = Query(...)):
    with _conn() as conn:
        conn.execute("DELETE FROM paper_trades WHERE session_id = %s", (session_id,))
        conn.execute(
            """INSERT INTO paper_portfolio (session_id, cash) VALUES (%s, %s)
               ON CONFLICT (session_id) DO UPDATE SET cash = %s, updated_at = now()""",
            (session_id, STARTING_CASH, STARTING_CASH)
        )
    return {"message": "Portfolio reset", "cash": STARTING_CASH}
