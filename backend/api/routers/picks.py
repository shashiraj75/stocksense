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
        next_run = "2 AM IST" if market == "IN" else "06:00 UTC (10:00 AM Dubai / 11:30 AM IST)"
        return {
            "generated_at": None,
            "market": market,
            "picks": {"short": [], "medium": [], "long": []},
            "generating": generating,
            "message": (
                "Picks are being generated now — check back in a few minutes."
                if generating else
                f"Picks not yet generated. Generated at {next_run} daily"
                + ("" if market == "IN" else "; US Premarket Review targets ~6:00 AM ET")
                + " — check back then."
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

    # Learning Alpha Engine remediation, Phase 1 — current containment
    # config, computed live (no DB read, no generation triggered). The
    # per-run persisted fields on `job` below (when present) are the
    # historical record of what a specific run actually used; this is
    # "what would a run started right now use."
    from services.alpha_engine.containment import (
        is_production_learning_enabled, containment_reason,
        production_alpha_source, LEARNING_DATASET_VERSION,
    )

    resp = {
        "market": market,
        "generating": generating,
        "has_today": _dp.picks_generated_today(market),
        "last_error": _dp._last_error.get(market),
        "last_trigger_received_at": _dp._last_trigger_received_at.get(market),
        "containment": {
            "production_learning_enabled": is_production_learning_enabled(),
            "production_alpha_source": production_alpha_source(),
            "containment_reason": containment_reason(),
            "learning_dataset_version": LEARNING_DATASET_VERSION,
        },
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
            # ── Release 12C: additive universe-selection observability ──────
            # Absent/null for historical rows recorded before this release —
            # never fabricated, never inferred from processed/total.
            "screener_raw_count": job.get("screener_raw_count"),
            "universe_candidate_count": job.get("universe_candidate_count"),
            "universe_selection_attempts": job.get("universe_selection_attempts"),
            "universe_selection_reason": job.get("universe_selection_reason"),
            "universe_selection_error_category": job.get("universe_selection_error_category"),
            # phase_task_processed/phase_task_total: explicit, unambiguous
            # aliases of processed/total for THIS phase's work units — never
            # universe size. processed/total above are kept for compatibility.
            "phase_task_processed": job.get("phase_task_processed"),
            "phase_task_total": job.get("phase_task_total"),
            # Learning Alpha Engine remediation, Phase 1 — Production
            # Containment observability. Null on any job recorded before
            # this release — never fabricated. See services/alpha_engine/
            # containment.py for what these mean.
            "production_alpha_source": job.get("production_alpha_source"),
            "shadow_ic_available": job.get("shadow_ic_available"),
            "shadow_meta_model_available": job.get("shadow_meta_model_available"),
            "containment_reason": job.get("containment_reason"),
            "learning_dataset_version": job.get("learning_dataset_version"),
        })

    # ── 3-phase US Daily Picks upgrade: premarket finalizer status ──────────
    # US-only — scoped here rather than for both markets so IN's frequent
    # frontend polling (India has no premarket phase) gets no extra read.
    # Additive only: sourced straight from the cached payload's own
    # premarket_* keys (written by services/premarket_finalizer.py), never
    # fabricated. All None on a payload finalized before this feature
    # existed, or one no premarket run has touched yet — never an error.
    if market == "US":
        cached = _dp.get_cached_picks("US") or {}
        resp.update({
            "base_generated_at":          cached.get("base_generated_at") or cached.get("generated_at"),
            "premarket_finalized_at":     cached.get("premarket_finalized_at"),
            "premarket_status":           cached.get("premarket_status"),
            "premarket_finalizer_version": cached.get("premarket_finalizer_version"),
            "next_base_run_hint":         "06:00 UTC / 10:00 AM Dubai / 11:30 AM IST, Mon-Fri",
            "next_premarket_run_hint":    "~6:00 AM America/New_York, Mon-Fri (EDT candidate: 10:00 UTC, EST candidate: 11:00 UTC; backend acceptance window 6:00-7:30 AM ET)",
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


@router.get("/intelligence-shadow")
def intelligence_shadow(market: str = "IN"):
    """Epic 007 Phase 3A/3B — read-only inspection of the Intelligence
    Engine V1 shadow slice's most recent run for a market. Returns
    {"available": False} (not an error) when no shadow run has ever
    completed — e.g. the INTELLIGENCE_ENGINE_SHADOW_ENABLED flag has never
    been turned on, or Postgres isn't configured. Response includes the
    Phase 3A instrument-type fields plus Phase 3B's tradability/liquidity/
    data_confidence/top_failure_reasons summaries (each with its own
    "available" flag — see telemetry.get_latest_shadow_run and
    shadow_run.py's module docstring for why those three may legitimately
    report unavailable even on a completed run). This endpoint never
    triggers a run itself and never mutates anything — read-only only."""
    market = _norm_market(market)
    try:
        from services.intelligence_engine.telemetry import get_latest_shadow_run
        result = get_latest_shadow_run(market)
        if result is None:
            return {"available": False, "market": market}
        return {"available": True, **result}
    except Exception as e:
        return {"available": False, "market": market, "error": str(e)}


@router.post("/generate")
def trigger_generation(background_tasks: BackgroundTasks, market: str = "IN", x_secret: str = Header(None)):
    """
    Trigger a fresh, full/heavy Daily Picks base generation run in the background,
    for one market at a time. This is the US Pre-Open base generation stage,
    distinct from and always prior to the separate, lightweight US Premarket
    Review (see /premarket-finalize below).
    Protected by X-Secret header to prevent abuse.
    Called by GitHub Actions cron: IN at 20:30 UTC (2 AM IST), US at 06:00 UTC
    (10:00 AM Dubai / 11:30 AM IST).

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

    # Step 5-6a: Atomic durable job reservation + heavy-workload lease
    # (Product Integrity #010 §10, replacing #009's two-separate-calls
    # design — a lease-acquisition failure now rolls back the job
    # reservation too, in the same transaction, instead of leaving a
    # reserved-but-lease-less row behind). Daily Picks and Multibagger for
    # the same market arbitrate the shared provider (yfinance for US,
    # screener.in for IN) through this lease — exactly one wins; the other
    # gets a clean, retryable resource_busy response. Neither cancels a job
    # the other already started. Since Multibagger is now weekly and runs
    # at a very different local time, this should almost never actually
    # conflict in normal operation — it exists for the exceptional
    # manual-overlap case.
    job_id = str(uuid.uuid4())
    _HEAVY_RESOURCE = {"IN": "IN_SCREENER_HEAVY", "US": "US_YFINANCE_HEAVY"}[market]
    try:
        from services.postgres_store import (
            try_reserve_daily_picks_job_with_lease,
            get_active_daily_picks_job,
            mark_daily_picks_job_failed,
        )
        outcome = try_reserve_daily_picks_job_with_lease(job_id, market, _dp._RUNNER_ID, _HEAVY_RESOURCE)
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "durable_job_state_unavailable", "market": market,
                     "message": f"Could not write durable job/lease state: {e}"},
        )

    if outcome == "already_running":
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
    if outcome == "resource_busy":
        return JSONResponse(
            status_code=409,
            content={"status": "resource_busy", "market": market, "resource": _HEAVY_RESOURCE,
                      "message": f"{_HEAVY_RESOURCE} is currently held by a Multibagger refresh — "
                                  "this request did not start and may be retried."},
        )

    # Step 7: Set in-memory flag AFTER successful DB reservation
    with _dp._generating_lock:
        _dp._generating[market] = True

    # Step 8: Launch background task; clean up durable reservation and the
    # heavy-workload lease if dispatch itself fails; release the lease when
    # generation finishes, regardless of outcome.
    def _run():
        try:
            _dp.generate_picks(market, job_id=job_id)
        finally:
            with _dp._generating_lock:
                _dp._generating[market] = False
            try:
                from services.postgres_store import release_heavy_workload_lease
                release_heavy_workload_lease(job_id)
            except Exception:
                pass

    try:
        background_tasks.add_task(_run)
    except Exception as dispatch_err:
        # Dispatch failure: mark the reserved row failed and release the
        # lease so neither blocks future runs.
        with _dp._generating_lock:
            _dp._generating[market] = False
        try:
            mark_daily_picks_job_failed(
                job_id, datetime.now(timezone.utc),
                f"failed_to_start: {dispatch_err}",
            )
        except Exception:
            pass
        try:
            from services.postgres_store import release_heavy_workload_lease
            release_heavy_workload_lease(job_id)
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


@router.post("/premarket-finalize")
async def premarket_finalize(market: str = "US", x_secret: str = Header(None)):
    """
    Lightweight US premarket finalizer (3-phase US Daily Picks upgrade,
    Phase 2). Re-checks the already-generated US base Daily Picks with
    whatever premarket signals this codebase already has a safe provider
    for — never recomputes the universe, never re-runs PredictionEngine,
    never mutates scoring/ranking/confidence/target/stop-loss. See
    services/premarket_finalizer.py's module docstring for full scope and
    known limitations.

    Protected by the same X-Secret header as /generate. Guards its own
    execution window (6:00-7:30 AM America/New_York, Mon-Fri, excluding US
    market holidays — retargeted from the prior 7:30-9:00 AM window on
    2026-07-15 so 6:00 AM ET becomes the authoritative finalization target;
    see in_premarket_window()'s own docstring) internally and safely no-ops
    outside it — the calling workflow (daily_picks_us_premarket.yml) fires
    two fixed-UTC candidate times per day (one for EDT at 10:00 UTC, one for
    EST at 11:00 UTC) since GitHub Actions cron is UTC-only and does not
    observe US DST. Because the window is wider than the 1-hour EDT/EST gap
    between the two candidates, BOTH may land inside it on the same day — a
    same-day idempotency guard in finalize_premarket() (not this endpoint)
    makes the second call a safe no-op rather than a duplicate run. The
    finalizer only reviews an already-persisted US Pre-Open base run
    (validate_base_for_finalization()) — it never generates a base itself.

    Only market=US is supported — India Daily Picks timing/behavior is
    explicitly out of scope for this feature and untouched.

    HTTP contract:
      200 — completed, skipped (outside window / unsupported market), or
            failed (no base picks to finalize) — status is in the body,
            never inferred from the HTTP code alone, matching how callers
            already distinguish outcomes for /generate.
    """
    if x_secret != PICKS_SECRET:
        raise HTTPException(status_code=401, detail="Invalid secret")

    from services.premarket_finalizer import finalize_premarket
    result = await finalize_premarket(market)
    return JSONResponse(status_code=200, content=result)
