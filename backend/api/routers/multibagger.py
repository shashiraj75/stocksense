import os
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query
from typing import Literal

router = APIRouter(prefix="/api/multibagger", tags=["multibagger"])

# Reuses the same secret as Daily Picks generation — both are GitHub
# Actions-triggered background jobs protected the same way.
MULTIBAGGER_SECRET = os.getenv("PICKS_SECRET", "")

# IN and US refresh independently — different jobs, different schedules,
# neither should block the other from being triggered.
_refresh_state = {
    "IN": {"running": False, "last_summary": None},
    "US": {"running": False, "last_summary": None},
}


@router.get("/screen")
def get_screen(
    screen: Literal["quality_compounder", "multibagger_discovery", "tenbagger_early"],
    market: Literal["IN", "US"] = Query("IN"),
):
    """
    Run a hard AND-filter screen against the cached fundamentals table.
    Instant — no live scraping happens here, only against the nightly cache.
    """
    from services import fundamentals_cache as cache
    from services.multibagger_scorecard import annotate_and_rank
    try:
        cache.ensure_table()
        rows = annotate_and_rank(cache.query_screen(screen, market), market)
        return {
            "screen": screen,
            "market": market,
            "count": len(rows),
            "results": rows,
            "last_refreshed": cache.last_refreshed(market),
        }
    except Exception as e:
        return {"screen": screen, "market": market, "count": 0, "results": [], "last_refreshed": None, "error": str(e)}


@router.get("/status")
def refresh_status(market: Literal["IN", "US"] = Query("IN")):
    from services import fundamentals_cache as cache
    try:
        cache.ensure_table()
        last_refreshed = cache.last_refreshed(market)
    except Exception:
        last_refreshed = None
    return {
        "market": market,
        "running": _refresh_state[market]["running"],
        "last_summary": _refresh_state[market]["last_summary"],
        "last_refreshed": last_refreshed,
    }


def _run_refresh_job(market: str):
    _refresh_state[market]["running"] = True
    try:
        if market == "IN":
            from services.fundamentals_refresh import run_full_refresh
        else:
            from services.us_fundamentals_refresh import run_full_refresh
        _refresh_state[market]["last_summary"] = run_full_refresh()
    finally:
        _refresh_state[market]["running"] = False


@router.post("/refresh")
def trigger_refresh(
    background_tasks: BackgroundTasks,
    x_secret: str = Header(None),
    market: Literal["IN", "US"] = Query("IN"),
):
    """
    Trigger a full-universe fundamentals refresh in the background.
    IN: ~1-2 hours for ~2,300 NSE stocks. US: ~5-6 hours for ~5,300 common
    stocks (more conservative pace — yfinance backs live pricing app-wide,
    so it carries more risk than the IN job's screener.in dependency).
    Protected by X-Secret header — called by a GitHub Actions cron, same
    pattern as POST /api/picks/generate.
    """
    if x_secret != MULTIBAGGER_SECRET:
        raise HTTPException(status_code=401, detail="Invalid secret")
    if _refresh_state[market]["running"]:
        return {"status": "already_running", "market": market}

    background_tasks.add_task(_run_refresh_job, market)
    return {"status": "started", "market": market}
