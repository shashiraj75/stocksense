import logging
import os
import uuid
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from typing import Literal

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/multibagger", tags=["multibagger"])

# User-safe message for any screen-computation failure — the real exception
# is logged server-side only (log.exception below), never sent to the client.
_SCREEN_UNAVAILABLE_MESSAGE = "Screen data is temporarily unavailable."

# Reuses the same secret as Daily Picks generation — both are GitHub
# Actions-triggered background jobs protected the same way.
MULTIBAGGER_SECRET = os.getenv("PICKS_SECRET", "")

# IN and US refresh independently — different jobs, different schedules,
# neither should block the other from being triggered.
_refresh_state = {
    "IN": {"running": False, "last_summary": None},
    "US": {"running": False, "last_summary": None},
}

# US workload overlap protection (2026-07-15): a cooperative stop signal US
# Daily Picks generation can raise when it needs the shared yfinance
# provider — see run_full_refresh()'s should_stop param. IN has no
# equivalent flag; it has never conflicted with anything in the scheduler
# matrix and is untouched by this release.
_stop_requested = {"US": False}

# Distinct per-process runner id, same pattern as services.daily_picks._RUNNER_ID
# — identifies which backend instance holds a given durable reservation.
_RUNNER_ID = str(uuid.uuid4())


def request_us_stop() -> None:
    """
    Called by POST /api/picks/generate (market=US) when it observes an
    active US Multibagger refresh — see picks.py's Step 4a. Best-effort and
    cooperative: the refresh loop checks this once per symbol and exits
    cleanly at the next boundary; it is never force-killed mid-fetch.
    """
    _stop_requested["US"] = True


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
        # status="ok" here covers BOTH a non-empty result AND a genuine,
        # successfully-evaluated zero-result outcome (count=0, no exception) —
        # the frontend must be able to tell that apart from a computation
        # failure below, which also sets count=0 but status="unavailable".
        return {
            "screen": screen,
            "market": market,
            "status": "ok",
            "count": len(rows),
            "results": rows,
            "last_refreshed": cache.last_refreshed(market),
        }
    except Exception as e:
        # Full exception detail stays server-side only — never expose raw
        # Python/library error text (e.g. Decimal/float TypeErrors) to the UI.
        log.exception(f"[multibagger] screen computation failed ({market}/{screen})")
        return {
            "screen": screen,
            "market": market,
            "status": "unavailable",
            "count": 0,
            "results": [],
            "last_refreshed": None,
            "error": _SCREEN_UNAVAILABLE_MESSAGE,
        }


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


def _run_refresh_job(market: str, job_id: str | None = None):
    _refresh_state[market]["running"] = True
    if market == "US":
        _stop_requested["US"] = False
    try:
        if market == "IN":
            from services.fundamentals_refresh import run_full_refresh
            summary = run_full_refresh()
        else:
            from services.us_fundamentals_refresh import run_full_refresh
            summary = run_full_refresh(should_stop=lambda: _stop_requested["US"])
        _refresh_state[market]["last_summary"] = summary
        if market == "US" and job_id:
            from services.postgres_store import mark_multibagger_job_completed
            try:
                mark_multibagger_job_completed(job_id, stopped_early=bool(summary.get("stopped_early")))
            except Exception:
                pass
    except Exception as e:
        if market == "US" and job_id:
            from services.postgres_store import mark_multibagger_job_failed
            try:
                mark_multibagger_job_failed(job_id, str(e))
            except Exception:
                pass
        raise
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

    US workload overlap protection (2026-07-15): US Daily Picks base
    generation has priority over this job (they share yfinance and the
    schedule was designed so this refresh normally finishes hours before
    the 06:00 UTC base run — see .github/workflows/multibagger_refresh_us.yml
    and Product-Integrity-008). If an active/queued US Daily Picks job is
    detected — or that check itself cannot be performed — this endpoint
    refuses to start rather than risk running both at once. IN is
    unaffected: no durable state, no conflict check, unchanged behavior.

    HTTP contract (US only; IN keeps its original in-memory-only contract):
      202 — accepted and queued
      200 — already_fresh — not applicable here (no freshness concept)
      409 — already_running (another Multibagger refresh already reserved
            the slot) or deferred_daily_picks_priority (US Daily Picks is
            active; Multibagger yields)
      503 — durable_job_state_unavailable (USE_POSTGRES != "1", the
            conflict check failed, or the durable insert failed)
    """
    if x_secret != MULTIBAGGER_SECRET:
        raise HTTPException(status_code=401, detail="Invalid secret")

    if market == "IN":
        if _refresh_state["IN"]["running"]:
            return {"status": "already_running", "market": "IN"}
        background_tasks.add_task(_run_refresh_job, "IN")
        return {"status": "started", "market": "IN"}

    # market == "US" — durable path with Daily Picks priority.
    if os.getenv("USE_POSTGRES") != "1":
        return JSONResponse(
            status_code=503,
            content={"status": "durable_job_state_unavailable", "market": "US",
                     "message": "USE_POSTGRES is not enabled; US Multibagger refresh requires durable job state."},
        )

    if _refresh_state["US"]["running"]:
        return JSONResponse(status_code=409,
                             content={"status": "already_running", "market": "US"})

    from services.postgres_store import has_active_daily_picks_job_or_unknown
    try:
        conflict = has_active_daily_picks_job_or_unknown("US")
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "durable_job_state_unavailable", "market": "US",
                     "message": f"Could not check for a conflicting US Daily Picks job: {e}"},
        )
    if conflict:
        return JSONResponse(
            status_code=409,
            content={"status": "deferred_daily_picks_priority", "market": "US",
                      "message": "US Daily Picks base generation is active or the conflict check "
                                 "could not be verified; Multibagger refresh yields and did not start."},
        )

    job_id = str(uuid.uuid4())
    try:
        from services.postgres_store import try_reserve_multibagger_job
        reserved = try_reserve_multibagger_job(job_id, "US", _RUNNER_ID)
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "durable_job_state_unavailable", "market": "US",
                     "message": f"Could not write durable job state: {e}"},
        )
    if not reserved:
        return JSONResponse(status_code=409,
                             content={"status": "already_running", "market": "US"})

    background_tasks.add_task(_run_refresh_job, "US", job_id)
    return JSONResponse(status_code=202,
                         content={"status": "started", "market": "US", "job_id": job_id})
