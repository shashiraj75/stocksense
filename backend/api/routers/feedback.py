"""
User Feedback API
==================
Two feedback mechanisms:
  1. Signal feedback — thumbs up/down on BUY/HOLD/SELL signals
  2. NPS survey     — monthly 0-10 Net Promoter Score

All data is user-scoped via Supabase user_id.
"""

from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/feedback", tags=["Feedback"])


def _conn():
    from services.postgres_store import _get_pool
    return _get_pool().connection()


# ── Signal feedback ───────────────────────────────────────────────────────────

class SignalFeedbackIn(BaseModel):
    user_id: str
    symbol:  str
    market:  str
    horizon: str
    signal:  str
    vote:    int = Field(..., description="1 = thumbs up, -1 = thumbs down")


@router.post("/signal")
def submit_signal_feedback(body: SignalFeedbackIn):
    if body.vote not in (1, -1):
        raise HTTPException(status_code=400, detail="vote must be 1 or -1")
    with _conn() as conn:
        conn.execute(
            """INSERT INTO signal_feedback
               (user_id, symbol, market, horizon, signal, vote)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (user_id, symbol, market, horizon)
               DO UPDATE SET vote = EXCLUDED.vote, submitted_at = now()""",
            (body.user_id, body.symbol.upper(), body.market,
             body.horizon, body.signal, body.vote)
        )
    return {"status": "ok"}


@router.get("/signal/{symbol}")
def get_signal_feedback(symbol: str, user_id: str, market: str = "IN", horizon: str = "medium"):
    """Return this user's existing vote for a symbol/horizon (if any)."""
    with _conn() as conn:
        row = conn.execute(
            """SELECT vote, signal, submitted_at FROM signal_feedback
               WHERE user_id=%s AND symbol=%s AND market=%s AND horizon=%s""",
            (user_id, symbol.upper(), market, horizon)
        ).fetchone()
    if not row:
        return {"vote": None}
    return {"vote": row[0], "signal": row[1], "submitted_at": str(row[2])}


@router.get("/signal/summary/{symbol}")
def get_signal_summary(symbol: str, market: str = "IN", horizon: str = "medium"):
    """Aggregate thumbs up/down for a symbol across all users."""
    with _conn() as conn:
        row = conn.execute(
            """SELECT
                 COUNT(*) FILTER (WHERE vote = 1)  AS up,
                 COUNT(*) FILTER (WHERE vote = -1) AS down
               FROM signal_feedback
               WHERE symbol=%s AND market=%s AND horizon=%s""",
            (symbol.upper(), market, horizon)
        ).fetchone()
    up   = row[0] if row else 0
    down = row[1] if row else 0
    total = up + down
    return {
        "symbol":    symbol.upper(),
        "market":    market,
        "horizon":   horizon,
        "thumbs_up": up,
        "thumbs_down": down,
        "approval_pct": round(up / total * 100) if total > 0 else None,
    }


# ── NPS survey ────────────────────────────────────────────────────────────────

class NpsIn(BaseModel):
    user_id: str
    score:   int = Field(..., ge=0, le=10)
    comment: str | None = None


@router.post("/nps")
def submit_nps(body: NpsIn):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO nps_responses (user_id, score, comment) VALUES (%s, %s, %s)",
            (body.user_id, body.score, body.comment)
        )
    return {"status": "ok"}


@router.get("/nps/due")
def nps_due(user_id: str):
    """
    Returns whether the NPS survey should be shown to this user.
    Rules:
      - Never submitted → show after 7 days of account existence (we approximate
        by checking if any signal_feedback exists, meaning they've used the tool)
      - Submitted before → show again after 30 days
    """
    with _conn() as conn:
        row = conn.execute(
            "SELECT submitted_at FROM nps_responses WHERE user_id=%s ORDER BY submitted_at DESC LIMIT 1",
            (user_id,)
        ).fetchone()

    if row:
        last = row[0]
        if isinstance(last, str):
            last = datetime.fromisoformat(last)
        due = (datetime.now(timezone.utc) - last.replace(tzinfo=timezone.utc)) > timedelta(days=30)
        return {"due": due, "last_submitted_at": str(row[0])}

    # Never submitted — show if they've interacted with at least one signal
    with _conn() as conn:
        has_interaction = conn.execute(
            "SELECT 1 FROM signal_feedback WHERE user_id=%s LIMIT 1", (user_id,)
        ).fetchone()

    return {"due": bool(has_interaction), "last_submitted_at": None}
