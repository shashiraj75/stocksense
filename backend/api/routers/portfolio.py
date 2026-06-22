"""
Portfolio holdings — persisted in Postgres so they sync across devices for
the same logged-in user. Previously stored entirely in the browser's
localStorage, which meant a holding added on one device was invisible on any
other device or browser for that same account.
"""
import os
import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


def _conn():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None)


def _ensure_table():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_holdings (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                symbol      TEXT NOT NULL,
                market      TEXT NOT NULL,
                qty         NUMERIC NOT NULL,
                avg_price   NUMERIC NOT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_holdings_user ON portfolio_holdings(user_id)")


class HoldingCreate(BaseModel):
    symbol: str
    market: Literal["IN", "US"]
    qty: float
    avg_price: float


class HoldingUpdate(BaseModel):
    qty: float
    avg_price: float


@router.on_event("startup")
def startup():
    try:
        _ensure_table()
    except Exception:
        pass  # DB may not be available in dev


@router.get("/{user_id}")
def get_holdings(user_id: str):
    try:
        _ensure_table()
        with _conn() as conn:
            rows = conn.execute(
                "SELECT id, symbol, market, qty, avg_price FROM portfolio_holdings WHERE user_id = %s ORDER BY created_at",
                (user_id,)
            ).fetchall()
        return {"items": [
            {"id": r[0], "symbol": r[1], "market": r[2], "qty": float(r[3]), "avgPrice": float(r[4])}
            for r in rows
        ]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{user_id}")
def add_holding(user_id: str, body: HoldingCreate):
    try:
        _ensure_table()
        holding_id = str(uuid.uuid4())
        with _conn() as conn:
            conn.execute(
                "INSERT INTO portfolio_holdings (id, user_id, symbol, market, qty, avg_price) VALUES (%s, %s, %s, %s, %s, %s)",
                (holding_id, user_id, body.symbol.upper(), body.market, body.qty, body.avg_price)
            )
        return {"id": holding_id, "symbol": body.symbol.upper(), "market": body.market,
                "qty": body.qty, "avgPrice": body.avg_price}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{user_id}/{holding_id}")
def update_holding(user_id: str, holding_id: str, body: HoldingUpdate):
    try:
        with _conn() as conn:
            conn.execute(
                "UPDATE portfolio_holdings SET qty = %s, avg_price = %s WHERE id = %s AND user_id = %s",
                (body.qty, body.avg_price, holding_id, user_id)
            )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{user_id}/{holding_id}")
def delete_holding(user_id: str, holding_id: str):
    try:
        with _conn() as conn:
            conn.execute(
                "DELETE FROM portfolio_holdings WHERE id = %s AND user_id = %s",
                (holding_id, user_id)
            )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
