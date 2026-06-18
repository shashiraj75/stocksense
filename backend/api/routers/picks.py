import os
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

router = APIRouter()

PICKS_SECRET = os.getenv("PICKS_SECRET", "")  # must be set in Render environment variables


@router.get("/daily")
def daily_picks():
    """Return today's cached BUY picks. Instant — reads from disk."""
    from services.daily_picks import get_cached_picks
    data = get_cached_picks()
    if not data:
        return {
            "generated_at": None,
            "picks": {"short": [], "medium": [], "long": []},
            "message": "Picks not yet generated. Check back after 9 AM IST.",
        }
    return data


@router.get("/performance")
def picks_performance(horizon: str = "medium", window_days: int = 90):
    """Live performance of past daily picks — hit rate, P&L, vs benchmark."""
    try:
        from services.postgres_store import get_daily_picks_performance
        rows = get_daily_picks_performance(horizon=horizon, window_days=window_days)
        return {"horizon": horizon, "window_days": window_days, "picks": rows}
    except Exception as e:
        return {"horizon": horizon, "window_days": window_days, "picks": [], "error": str(e)}


@router.post("/generate")
def trigger_generation(background_tasks: BackgroundTasks, x_secret: str = Header(None)):
    """
    Trigger a fresh pick generation run in the background.
    Protected by X-Secret header to prevent abuse.
    Called by GitHub Actions cron at 3:30 UTC (9 AM IST) daily.
    """
    if x_secret != PICKS_SECRET:
        raise HTTPException(status_code=401, detail="Invalid secret")

    from services.daily_picks import generate_picks
    background_tasks.add_task(generate_picks)
    return {"status": "generation started", "message": "Picks will be ready in ~10 minutes."}
