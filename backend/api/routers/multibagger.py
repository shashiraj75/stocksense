import os
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from typing import Literal

router = APIRouter(prefix="/api/multibagger", tags=["multibagger"])

# Reuses the same secret as Daily Picks generation — both are GitHub
# Actions-triggered background jobs protected the same way.
MULTIBAGGER_SECRET = os.getenv("PICKS_SECRET", "")

_VALID_SCREENS = ("quality_compounder", "multibagger_discovery", "tenbagger_early")

_refresh_state = {"running": False, "last_summary": None}


@router.get("/screen")
def get_screen(screen: Literal["quality_compounder", "multibagger_discovery", "tenbagger_early"]):
    """
    Run a hard AND-filter screen against the cached fundamentals table.
    Instant — no live scraping happens here, only against the nightly cache.
    """
    from services import fundamentals_cache as cache
    from services.multibagger_scorecard import annotate_and_rank
    try:
        cache.ensure_table()
        rows = annotate_and_rank(cache.query_screen(screen))
        return {
            "screen": screen,
            "count": len(rows),
            "results": rows,
            "last_refreshed": cache.last_refreshed(),
        }
    except Exception as e:
        return {"screen": screen, "count": 0, "results": [], "last_refreshed": None, "error": str(e)}


@router.get("/status")
def refresh_status():
    from services import fundamentals_cache as cache
    try:
        cache.ensure_table()
        last_refreshed = cache.last_refreshed()
    except Exception:
        last_refreshed = None
    return {
        "running": _refresh_state["running"],
        "last_summary": _refresh_state["last_summary"],
        "last_refreshed": last_refreshed,
    }


def _run_refresh_job():
    _refresh_state["running"] = True
    try:
        from services.fundamentals_refresh import run_full_refresh
        _refresh_state["last_summary"] = run_full_refresh()
    finally:
        _refresh_state["running"] = False


@router.post("/refresh")
def trigger_refresh(background_tasks: BackgroundTasks, x_secret: str = Header(None)):
    """
    Trigger a full-universe fundamentals refresh in the background. Takes
    roughly 1-2 hours for ~2,300 NSE stocks at a polite scrape pace.
    Protected by X-Secret header — called by a GitHub Actions cron, same
    pattern as POST /api/picks/generate.
    """
    if x_secret != MULTIBAGGER_SECRET:
        raise HTTPException(status_code=401, detail="Invalid secret")
    if _refresh_state["running"]:
        return {"status": "already_running"}

    background_tasks.add_task(_run_refresh_job)
    return {"status": "started"}
