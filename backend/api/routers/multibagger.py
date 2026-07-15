import logging
import os
import uuid
from datetime import datetime, timezone, timedelta
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

# Product Integrity #009/#010: the full-universe fundamentals refresh is
# weekly for both markets (previously nightly for IN, daily for US), and
# both markets are durable. This in-memory dict remains for the fast-path
# "already running" check and the GET /status fallback when durable state
# is unavailable — it is no longer the sole source of truth.
_refresh_state = {
    "IN": {"running": False, "last_summary": None},
    "US": {"running": False, "last_summary": None},
}

_RESOURCE_FOR_MARKET = {"IN": "IN_SCREENER_HEAVY", "US": "US_YFINANCE_HEAVY"}
_STALENESS_DAYS = 9  # wider than the 7-day weekly cadence, absorbing one missed/delayed run

# Distinct per-process runner id, same pattern as services.daily_picks._RUNNER_ID
# — identifies which backend instance holds a given durable reservation.
_RUNNER_ID = str(uuid.uuid4())


@router.get("/screen")
def get_screen(
    screen: Literal["quality_compounder", "multibagger_discovery", "tenbagger_early"],
    market: Literal["IN", "US"] = Query("IN"),
):
    """
    Run a hard AND-filter screen against the cached fundamentals table.
    Instant — no live scraping happens here, only against the weekly cache.
    """
    from services import fundamentals_cache as cache
    from services.multibagger_scorecard import annotate_and_rank
    try:
        cache.ensure_table()
        rows = annotate_and_rank(cache.query_screen(screen, market), market)
        return {
            "screen": screen,
            "market": market,
            "status": "ok",
            "count": len(rows),
            "results": rows,
            "last_refreshed": cache.last_refreshed(market),
        }
    except Exception as e:
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
    """
    Durable-first status. When Postgres is enabled, job_status/timestamps
    are sourced from multibagger_refresh_jobs — a restart never fabricates
    a healthy idle state, since the durable row (not the in-memory dict a
    restart resets) is authoritative. GET never mutates lifecycle state —
    see reconcile_stale_multibagger_jobs() for where orphan reconciliation
    actually happens (startup only).
    """
    from services import fundamentals_cache as cache
    try:
        cache.ensure_table()
        last_refreshed = cache.last_refreshed(market)
    except Exception:
        last_refreshed = None

    resp = {
        "market": market,
        "running": _refresh_state[market]["running"],
        "last_summary": _refresh_state[market]["last_summary"],
        "last_refreshed": last_refreshed,
        "schedule_frequency": "weekly",
        "next_scheduled_refresh_hint": (
            "Saturday 3:00 AM IST (Friday 21:30 UTC)" if market == "IN"
            else "Sunday 08:00 UTC (3:00 AM EST / 4:00 AM EDT)"
        ),
        "stale_after_days": _STALENESS_DAYS,
        "durable_state_available": False,
    }

    if os.getenv("USE_POSTGRES") != "1":
        return resp

    from services.postgres_store import get_latest_multibagger_job, get_last_successful_multibagger_refresh
    try:
        latest = get_latest_multibagger_job(market)
        last_success = get_last_successful_multibagger_refresh(market)
    except Exception:
        return resp

    resp["durable_state_available"] = True
    if latest:
        resp.update({
            "job_id": latest.get("job_id"),
            "job_status": latest.get("status"),
            "trigger_source": latest.get("trigger_source"),
            "scheduled_period_key": latest.get("scheduled_period_key"),
            "processed": latest.get("processed"),
            "total": latest.get("total"),
            "last_error": latest.get("last_error"),
            "last_runner_heartbeat_at": latest.get("last_runner_heartbeat_at"),
            "last_progress_at": latest.get("last_progress_at"),
        })
        resp["running"] = latest.get("status") in ("queued", "running")
    last_successful_at = last_success.get("completed_at") if last_success else None
    resp["last_successful_refresh_at"] = last_successful_at
    if last_successful_at is not None:
        # Product Integrity #010: precise timedelta comparison, not integer
        # .days truncation (which would treat 9.9 days as "9 days", i.e.
        # not-yet-stale, a full near-day later than intended).
        resp["is_stale"] = (datetime.now(timezone.utc) - last_successful_at) > timedelta(days=_STALENESS_DAYS)
    else:
        resp["is_stale"] = True
    return resp


def _run_refresh_job(market: str, job_id: str):
    """
    Background task body (Product Integrity #010 §11-12). Atomically claims
    the queued job (SELECT ... FOR UPDATE SKIP LOCKED — a no-op safety net
    against a hypothetical second worker/instance racing this one), loads
    any symbols already staged by a prior attempt at this exact job_id so a
    resumed run skips re-fetching them, runs the refresh with per-symbol
    staging instead of writing the active cache directly, then — ONLY if
    the full universe was traversed — atomically promotes staged results
    into stock_fundamentals_cache and reconciles the promoted count against
    the run's own summary before marking the job completed. The
    heavy-workload lease acquired at reservation time is always released in
    `finally`, regardless of outcome.
    """
    from services.postgres_store import (
        try_claim_queued_multibagger_job, record_multibagger_job_progress,
        mark_multibagger_job_completed, mark_multibagger_job_failed,
        release_heavy_workload_lease, get_staged_symbols, stage_multibagger_symbol,
        promote_staged_symbols, count_staged_outcomes,
    )

    if not try_claim_queued_multibagger_job(job_id):
        log.warning("[multibagger] job_id=%s could not be claimed (already claimed or not queued) — skipping", job_id)
        return

    _refresh_state[market]["running"] = True

    def _on_progress(processed: int, total: int):
        try:
            record_multibagger_job_progress(job_id, processed, total)
        except Exception:
            log.warning("[multibagger] failed to persist progress for job_id=%s", job_id)

    def _stage(symbol: str, outcome: str, fields, is_financial):
        stage_multibagger_symbol(job_id, market, symbol, outcome, fields, is_financial)

    try:
        already_staged = get_staged_symbols(job_id)
    except Exception:
        log.warning("[multibagger] failed to load resume checkpoint for job_id=%s — starting from scratch", job_id)
        already_staged = set()

    try:
        if market == "IN":
            from services.fundamentals_refresh import run_full_refresh
        else:
            from services.us_fundamentals_refresh import run_full_refresh
        summary = run_full_refresh(stage_fn=_stage, on_progress=_on_progress, already_staged=already_staged)
        _refresh_state[market]["last_summary"] = summary

        # STEP 13: a run "completes" only once the full intended universe
        # was attempted (traversal itself, not "every provider call
        # succeeded" — per-symbol failures are isolated and expected).
        staged_counts = count_staged_outcomes(job_id)
        staged_total = sum(staged_counts.values())
        if staged_total < summary["total"]:
            raise RuntimeError(
                f"incomplete universe traversal: staged {staged_total}/{summary['total']} symbols"
            )

        promoted = promote_staged_symbols(job_id, market)
        expected_promotable = staged_counts.get("refreshed", 0)
        if promoted != expected_promotable:
            log.warning(
                "[multibagger] job_id=%s promoted count (%d) does not exactly match staged "
                "'refreshed' count (%d) — likely a pre-existing row updated in place, not a data-loss signal, "
                "but recorded for visibility", job_id, promoted, expected_promotable,
            )

        if not mark_multibagger_job_completed(job_id):
            log.error("[multibagger] durable completion write failed for job_id=%s market=%s "
                      "— job remains in a prior lifecycle state until reconciliation", job_id, market)
    except Exception as e:
        log.exception("[multibagger] refresh raised for job_id=%s market=%s", job_id, market)
        if not mark_multibagger_job_failed(job_id, str(e)):
            log.error("[multibagger] durable failure write ALSO failed for job_id=%s market=%s "
                      "— job remains in a prior lifecycle state until reconciliation", job_id, market)
    finally:
        _refresh_state[market]["running"] = False
        try:
            release_heavy_workload_lease(job_id)
        except Exception:
            log.error("[multibagger] failed to release heavy-workload lease for job_id=%s", job_id)


@router.post("/refresh")
def trigger_refresh(
    background_tasks: BackgroundTasks,
    x_secret: str = Header(None),
    market: Literal["IN", "US"] = Query("IN"),
    trigger_source: Literal["scheduled", "manual"] = Query(...),
):
    """
    Trigger a full-universe fundamentals refresh in the background. Weekly
    for both markets (Product Integrity #009/#010) — see the release docs
    for full rationale.

    trigger_source is REQUIRED — there is no default, so a caller can never
    accidentally impersonate (or accidentally fail to identify as) a
    scheduled run:
      - scheduled: the calling GitHub Actions cron identifies itself this
        way. Accepted for any time on the correct scheduled local day
        (Saturday IST for India, Sunday ET for US) — not a narrow arrival
        window, since GitHub's own scheduled delivery is best-effort and
        has been observed arriving hours late in this repository. Subject
        to weekly-period idempotency — a call on the wrong local day is
        rejected outright; a duplicate for a period already queued/
        running/completed is a safe no-op.
      - manual: an exceptional, explicitly authorized run. May run on any
        day, still respects the heavy-workload lease and active-job
        uniqueness, but never occupies or satisfies a scheduled period's
        idempotency slot.

    Job reservation and heavy-workload lease acquisition happen atomically
    in one database transaction (Product Integrity #010 §9) — a lease
    conflict rolls back the job reservation too, so a caller never sees
    "started" state with no lease behind it.

    HTTP contract:
      202 — started
      200 — already_completed_for_period (scheduled only, that period already queued/running/completed)
      409 — already_running | resource_busy
      422 — outside_scheduled_day (scheduled only, wrong local weekday)
      503 — durable_state_unavailable
    """
    if x_secret != MULTIBAGGER_SECRET:
        raise HTTPException(status_code=401, detail="Invalid secret")

    if os.getenv("USE_POSTGRES") != "1":
        return JSONResponse(
            status_code=503,
            content={"status": "durable_state_unavailable", "market": market,
                     "message": "USE_POSTGRES is not enabled; Multibagger refresh requires durable job state."},
        )

    from services import multibagger_schedule as sched
    from services.postgres_store import try_reserve_multibagger_job_with_lease

    now_utc = datetime.now(timezone.utc)

    if trigger_source == "scheduled" and not sched.scheduled_day_ok(market, now_utc):
        return JSONResponse(
            status_code=422,
            content={"status": "outside_scheduled_day", "market": market,
                     "message": f"{market} scheduled refresh must arrive on its weekly scheduled local day; "
                                 "this call did not — no job started. Use trigger_source=manual for an "
                                 "explicitly authorized run on another day."},
        )

    if _refresh_state[market]["running"]:
        return JSONResponse(status_code=409, content={"status": "already_running", "market": market})

    period_key = sched.scheduled_period_key(market, now_utc) if trigger_source == "scheduled" else None
    job_id = str(uuid.uuid4())
    resource = _RESOURCE_FOR_MARKET[market]

    try:
        outcome = try_reserve_multibagger_job_with_lease(
            job_id, market, _RUNNER_ID, trigger_source, period_key, resource,
        )
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "durable_state_unavailable", "market": market,
                     "message": f"Could not write durable job/lease state: {e}"},
        )

    if outcome == "already_completed_for_period":
        return JSONResponse(
            status_code=200,
            content={"status": "already_completed_for_period", "market": market,
                      "scheduled_period_key": period_key,
                      "message": f"{market}'s scheduled refresh for period {period_key} is already "
                                  "queued, running, or completed — this duplicate call is a safe no-op."},
        )
    if outcome == "already_running":
        return JSONResponse(status_code=409, content={"status": "already_running", "market": market})
    if outcome == "resource_busy":
        return JSONResponse(
            status_code=409,
            content={"status": "resource_busy", "market": market, "resource": resource,
                      "message": f"{resource} is currently held by {market} Daily Picks (or another "
                                  "Multibagger run) — this request did not start and may be retried."},
        )

    background_tasks.add_task(_run_refresh_job, market, job_id)
    return JSONResponse(
        status_code=202,
        content={"status": "started", "market": market, "job_id": job_id,
                  "trigger_source": trigger_source, "scheduled_period_key": period_key},
    )
