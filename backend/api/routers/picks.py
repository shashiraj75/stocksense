import os
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter()

PICKS_SECRET = os.getenv("PICKS_SECRET", "")  # must be set in production environment
_VALID_MARKETS = ("IN", "US")

# Heartbeat considered slow after 90 s, unresponsive after 180 s
_HEARTBEAT_SLOW_SECS = 90
_HEARTBEAT_UNRESPONSIVE_SECS = 180


def _norm_market(market: str) -> str:
    m = (market or "IN").upper()
    if m not in _VALID_MARKETS:
        raise HTTPException(status_code=400, detail=f"Unsupported market '{market}' — use IN or US")
    return m


def _derive_job_health(job: dict | None) -> str | None:
    """Return 'ok' | 'slow' | 'unresponsive' | None based on last heartbeat."""
    if not job or job.get("status") != "running":
        return None
    hb = job.get("last_runner_heartbeat_at")
    if not hb:
        return "slow"
    if isinstance(hb, str):
        hb = datetime.fromisoformat(hb.replace("Z", "+00:00"))
    age = (datetime.now(timezone.utc) - hb).total_seconds()
    if age >= _HEARTBEAT_UNRESPONSIVE_SECS:
        return "unresponsive"
    if age >= _HEARTBEAT_SLOW_SECS:
        return "slow"
    return "ok"


@router.get("/daily")
def daily_picks(market: str = "IN"):
    """Return today's cached BUY picks for a market. Instant — reads from disk/Postgres."""
    market = _norm_market(market)
    import services.daily_picks as _dp

    in_memory = _dp._generating.get(market, False)
    db_active = False
    if os.getenv("USE_POSTGRES") == "1":
        try:
            from services.postgres_store import get_active_daily_picks_job
            active = get_active_daily_picks_job(market)
            db_active = active is not None
        except Exception:
            pass
    generating = in_memory or db_active

    data = _dp.get_cached_picks(market)
    if not data:
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
    return {**data, "generating": generating}


@router.get("/status")
def picks_status(market: str = "IN"):
    """Quick check: are picks available and/or is generation running?"""
    market = _norm_market(market)
    import services.daily_picks as _dp

    in_memory = _dp._generating.get(market, False)
    job = None
    if os.getenv("USE_POSTGRES") == "1":
        try:
            from services.postgres_store import get_latest_daily_picks_job
            job = get_latest_daily_picks_job(market)
        except Exception:
            pass

    db_active = job is not None and job.get("status") in ("queued", "running")
    generating = in_memory or db_active

    resp = {
        "market": market,
        "generating": generating,
        "has_today": _dp.picks_generated_today(market),
        "last_error": _dp._last_error.get(market),
        "last_trigger_received_at": _dp._last_trigger_received_at.get(market),
    }
    if job:
        resp.update({
            "job_id": job.get("job_id"),
            "job_status": job.get("status"),
            "phase": job.get("phase"),          # key matches postgres_store dict output
            "processed": job.get("processed"),
            "total": job.get("total"),
            "last_runner_heartbeat_at": job.get("last_runner_heartbeat_at"),
            "last_progress_at": job.get("last_progress_at"),
            "universe_used": job.get("universe_used"),
            "universe_degraded": job.get("universe_degraded"),
            "derived_job_health": _derive_job_health(job),
        })
    return resp


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
    Called by GitHub Actions cron: IN at 20:30 UTC (2 AM IST), US at 12:30 UTC (~8:30 AM ET).

    HTTP contract:
      202 — accepted and queued
      200 — already_fresh (picks exist for today; idempotent success)
      409 — already_running (a job is already queued/running)
      503 — durable_job_state_unavailable (USE_POSTGRES != "1" OR DB insert failed)
    """
    if x_secret != PICKS_SECRET:
        raise HTTPException(status_code=401, detail="Invalid secret")
    market = _norm_market(market)

    # Step 1: Require durable Postgres state — no legacy in-memory fallback in production
    if os.getenv("USE_POSTGRES") != "1":
        return JSONResponse(
            status_code=503,
            content={"status": "durable_job_state_unavailable", "market": market,
                     "message": "USE_POSTGRES is not enabled; Daily Picks requires durable job state."},
        )

    import services.daily_picks as _dp

    # Step 2: Record trigger receipt timestamp
    _dp._last_trigger_received_at[market] = datetime.now(timezone.utc).isoformat()

    # Step 3: Check picks_generated_today — return 200 if already fresh
    if _dp.picks_generated_today(market):
        return JSONResponse(
            status_code=200,
            content={"status": "already_fresh", "market": market,
                     "message": f"{market} picks already generated for today."},
        )

    # Step 4: Fast-path in-memory check (avoids DB round-trip if local flag is already set)
    with _dp._generating_lock:
        if _dp._generating.get(market, False):
            return JSONResponse(
                status_code=409,
                content={"status": "already_running", "market": market,
                         "message": f"{market} picks generation is already in progress."},
            )

    # Step 5: Atomic durable reservation via partial unique index
    job_id = str(uuid.uuid4())
    try:
        from services.postgres_store import (
            try_reserve_daily_picks_job,
            get_active_daily_picks_job,
            mark_daily_picks_job_failed,
        )
        reserved = try_reserve_daily_picks_job(job_id, market, _dp._RUNNER_ID)
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "durable_job_state_unavailable", "market": market,
                     "message": f"Could not write durable job state: {e}"},
        )

    # Step 6: Conflict — another process holds the active slot
    if not reserved:
        active = get_active_daily_picks_job(market)
        return JSONResponse(
            status_code=409,
            content={
                "status": "already_running",
                "market": market,
                "job_id": active.get("job_id") if active else None,
                "message": f"{market} picks generation is already in progress (reserved by another process).",
            },
        )

    # Step 7: Set in-memory flag AFTER successful DB reservation
    with _dp._generating_lock:
        _dp._generating[market] = True

    # Step 8: Launch background task; clean up durable reservation if dispatch itself fails
    def _run():
        try:
            _dp.generate_picks(market, job_id=job_id)
        finally:
            with _dp._generating_lock:
                _dp._generating[market] = False

    try:
        background_tasks.add_task(_run)
    except Exception as dispatch_err:
        # Dispatch failure: mark the reserved row failed so it does not block future runs
        with _dp._generating_lock:
            _dp._generating[market] = False
        try:
            mark_daily_picks_job_failed(
                job_id, datetime.now(timezone.utc),
                f"failed_to_start: {dispatch_err}",
            )
        except Exception:
            pass
        return JSONResponse(
            status_code=503,
            content={"status": "durable_job_state_unavailable", "market": market,
                     "message": f"Background task dispatch failed: {dispatch_err}"},
        )

    return JSONResponse(
        status_code=202,
        content={"status": "accepted", "market": market, "job_id": job_id,
                 "message": f"{market} picks will be ready in ~10-20 minutes."},
    )
