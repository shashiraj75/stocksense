"""
Price alerts — persisted in Postgres.
Uses same "default" user_id pattern as watchlist and paper trading.
"""
import os
import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _conn():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None)


def _ensure_table():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_alerts (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                symbol      TEXT NOT NULL,
                market      TEXT NOT NULL,
                target_price NUMERIC NOT NULL,
                direction   TEXT NOT NULL CHECK (direction IN ('above', 'below')),
                triggered   BOOLEAN NOT NULL DEFAULT FALSE,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                triggered_at TIMESTAMPTZ
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_user ON price_alerts(user_id)")
        # Email backstop: lets the background checker (services/price_alert_notifier.py)
        # notify a user even if their tab is closed, instead of relying purely on the
        # client-side 5s poll in alerts/page.tsx.
        conn.execute("ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS email TEXT")


class AlertCreate(BaseModel):
    symbol: str
    market: Literal["IN", "US"]
    target_price: float
    direction: Literal["above", "below"]
    email: str | None = None


class AlertTrigger(BaseModel):
    triggered: bool


@router.on_event("startup")
def startup():
    try:
        _ensure_table()
    except Exception:
        pass  # DB may not be available in dev


@router.get("/{user_id}")
def get_alerts(user_id: str):
    try:
        _ensure_table()
        with _conn() as conn:
            rows = conn.execute(
                "SELECT id, symbol, market, target_price, direction, triggered, created_at FROM price_alerts WHERE user_id = %s ORDER BY created_at DESC",
                (user_id,)
            ).fetchall()
        return {"items": [
            {
                "id": r[0], "symbol": r[1], "market": r[2],
                "targetPrice": float(r[3]), "direction": r[4],
                "triggered": r[5], "createdAt": r[6].isoformat() if r[6] else "",
            }
            for r in rows
        ]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{user_id}")
def create_alert(user_id: str, body: AlertCreate):
    try:
        _ensure_table()
        alert_id = str(uuid.uuid4())
        with _conn() as conn:
            conn.execute(
                "INSERT INTO price_alerts (id, user_id, symbol, market, target_price, direction, email) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (alert_id, user_id, body.symbol.upper(), body.market, body.target_price, body.direction, body.email)
            )
        return {"id": alert_id, "symbol": body.symbol.upper(), "market": body.market,
                "targetPrice": body.target_price, "direction": body.direction,
                "triggered": False, "createdAt": ""}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{user_id}/{alert_id}")
def update_alert(user_id: str, alert_id: str, body: AlertTrigger):
    try:
        with _conn() as conn:
            if body.triggered:
                conn.execute(
                    "UPDATE price_alerts SET triggered = TRUE, triggered_at = now() WHERE id = %s AND user_id = %s",
                    (alert_id, user_id)
                )
            else:
                conn.execute(
                    "UPDATE price_alerts SET triggered = FALSE, triggered_at = NULL WHERE id = %s AND user_id = %s",
                    (alert_id, user_id)
                )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{user_id}/{alert_id}")
def delete_alert(user_id: str, alert_id: str):
    try:
        with _conn() as conn:
            conn.execute("DELETE FROM price_alerts WHERE id = %s AND user_id = %s", (alert_id, user_id))
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
