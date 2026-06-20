import os
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

router = APIRouter()

PICKS_SECRET = os.getenv("PICKS_SECRET", "")  # must be set in Render environment variables


@router.get("/daily")
def daily_picks():
    """Return today's cached BUY picks. Instant — reads from disk/Postgres."""
    import services.daily_picks as _dp
    data = _dp.get_cached_picks()
    if not data:
        generating = _dp._generating
        return {
            "generated_at": None,
            "picks": {"short": [], "medium": [], "long": []},
            "generating": generating,
            "message": (
                "Picks are being generated now — check back in a few minutes."
                if generating else
                "Picks not yet generated. Generated at 2 AM IST daily — check back then."
            ),
        }
    return {**data, "generating": _dp._generating}


@router.get("/status")
def picks_status():
    """Quick check: are picks available and/or is generation running?"""
    import services.daily_picks as _dp
    return {
        "generating": _dp._generating,
        "has_today": _dp.picks_generated_today(),
        "last_error": _dp._last_error,
    }


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
    Called by GitHub Actions cron at 20:30 UTC (2 AM IST) daily.
    """
    if x_secret != PICKS_SECRET:
        raise HTTPException(status_code=401, detail="Invalid secret")

    import services.daily_picks as _dp
    with _dp._generating_lock:
        if _dp._generating:
            return {"status": "already running", "message": "Picks generation is already in progress."}
        _dp._generating = True

    def _run():
        try:
            _dp.generate_picks()
        finally:
            with _dp._generating_lock:
                _dp._generating = False

    background_tasks.add_task(_run)
    return {"status": "generation started", "message": "Picks will be ready in ~10 minutes."}
