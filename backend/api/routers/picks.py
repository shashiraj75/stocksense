import os
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

router = APIRouter()

PICKS_SECRET = os.getenv("PICKS_SECRET", "")  # must be set in Render environment variables
_VALID_MARKETS = ("IN", "US")


def _norm_market(market: str) -> str:
    m = (market or "IN").upper()
    if m not in _VALID_MARKETS:
        raise HTTPException(status_code=400, detail=f"Unsupported market '{market}' — use IN or US")
    return m


@router.get("/daily")
def daily_picks(market: str = "IN"):
    """Return today's cached BUY picks for a market. Instant — reads from disk/Postgres."""
    market = _norm_market(market)
    import services.daily_picks as _dp
    data = _dp.get_cached_picks(market)
    if not data:
        generating = _dp._generating.get(market, False)
        next_run = "2 AM IST" if market == "IN" else "9 AM ET (pre-market)"
        return {
            "generated_at": None,
            "market": market,
            "picks": {"short": [], "medium": [], "long": []},
            "generating": generating,
            "message": (
                "Picks are being generated now — check back in a few minutes."
                if generating else
                f"Picks not yet generated. Generated at {next_run} daily — check back then."
            ),
        }
    return {**data, "generating": _dp._generating.get(market, False)}


@router.get("/status")
def picks_status(market: str = "IN"):
    """Quick check: are picks available and/or is generation running?"""
    market = _norm_market(market)
    import services.daily_picks as _dp
    return {
        "market": market,
        "generating": _dp._generating.get(market, False),
        "has_today": _dp.picks_generated_today(market),
        "last_error": _dp._last_error.get(market),
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
def trigger_generation(background_tasks: BackgroundTasks, market: str = "IN", x_secret: str = Header(None)):
    """
    Trigger a fresh pick generation run in the background, for one market at a time.
    Protected by X-Secret header to prevent abuse.
    Called by GitHub Actions cron: IN at 20:30 UTC (2 AM IST), US at 13:00 UTC (9 AM ET).
    """
    if x_secret != PICKS_SECRET:
        raise HTTPException(status_code=401, detail="Invalid secret")
    market = _norm_market(market)

    import services.daily_picks as _dp
    with _dp._generating_lock:
        if _dp._generating.get(market, False):
            return {"status": "already running", "message": f"{market} picks generation is already in progress."}
        _dp._generating[market] = True

    def _run():
        try:
            _dp.generate_picks(market)
        finally:
            with _dp._generating_lock:
                _dp._generating[market] = False

    background_tasks.add_task(_run)
    return {"status": "generation started", "market": market, "message": f"{market} picks will be ready in ~10-20 minutes."}
