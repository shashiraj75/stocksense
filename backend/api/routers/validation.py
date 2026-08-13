"""
Validation API — exposes walk-forward backtest results to the frontend.
"""
import hmac
import logging
import math
import os
import numpy as np
from fastapi import APIRouter, Query, BackgroundTasks, Header, HTTPException
from fastapi.responses import JSONResponse
from typing import Literal

from services.safe_errors import safe_error_message

log = logging.getLogger(__name__)

router = APIRouter()

# V-SEC1 — reuses the project's established shared admin-secret convention:
# same PICKS_SECRET env var, same X-Secret header, same fail-closed
# comparison already proven for /api/predictions/debug/state (Release 14B —
# both sides must be non-empty after stripping before any comparison is
# attempted; an unconfigured/blank secret must never "match" a blank
# header). Read independently here (not imported from picks.py/
# predictions.py) to keep this a single-file, isolated change with zero
# coupling to those routes' own protection — same rationale predictions.py's
# _DEBUG_SECRET already documents. Additionally uses hmac.compare_digest for
# constant-time comparison, which neither existing convention does.
_VALIDATION_RUN_SECRET = os.getenv("PICKS_SECRET", "")


def _require_validation_secret(x_secret: str | None) -> None:
    """Fail-closed: rejects unless both the configured secret and the
    supplied header are non-empty, non-whitespace, and exactly equal after
    stripping. Never logs or echoes the value either side."""
    configured = (_VALIDATION_RUN_SECRET or "").strip()
    provided = (x_secret or "").strip()
    if not configured or not provided or not hmac.compare_digest(provided, configured):
        raise HTTPException(status_code=401, detail="Invalid secret")


def _safe_json(obj):
    """Recursively convert numpy scalars / ndarrays to native Python types,
    and normalize any non-finite float (NaN, +/-Infinity — built-in or
    NumPy) to None.

    V-PS2 — Starlette's JSONResponse renders with allow_nan=False, so a
    single non-finite value anywhere in the payload previously raised
    `ValueError: Out of range float values are not JSON compliant: nan`
    and took down the ENTIRE response (see get_stock_results' router
    docstring / V-PS1's root-cause trace) — one bad historical row could
    make a whole universe/horizon's per-stock endpoint unavailable. This
    is defense in depth, not a substitute for excluding invalid values at
    the aggregation layer (see get_per_stock_results' `clean` CTE) — by
    the time a value reaches here, it should already be None, but this
    guarantees the API boundary itself can never crash on one regardless.
    """
    if isinstance(obj, dict):
        return {k: _safe_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_json(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        obj = float(obj)
    if isinstance(obj, np.ndarray):
        return _safe_json(obj.tolist())
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    return obj


def _json_response(data: dict) -> JSONResponse:
    return JSONResponse(content=_safe_json(data))


@router.post("/run")
async def trigger_validation(
    background_tasks: BackgroundTasks,
    horizon: Literal["short", "medium", "long"] = Query("medium"),
    universe: Literal["nifty100", "midcap", "us"] = Query("nifty100"),
    x_secret: str | None = Header(None),
):
    """
    Trigger a walk-forward validation run.
    universe: nifty100 (default) | midcap | us
    Returns immediately — poll /status for progress, /results for output.

    Protected by X-Secret header (V-SEC1) — checked before anything else,
    including the durable-ledger admission attempt below, so an
    unauthenticated caller can never learn whether a run is currently
    active.

    V-SCHED1C1 — this route now goes through the same shared admission
    path the scheduler and catch-up use
    (services.validation_engine.admit_validation_attempt /
    execute_and_complete_admitted_attempt), built on the V-SCHED1B durable
    ledger and global execution lease, rather than a separate in-process-
    only claim. Admission is attempted SYNCHRONOUSLY here, before any
    response is built — a background task is enqueued only once admission
    genuinely succeeds, never after a rejected/failed admission. A manual
    attempt is always unbound (slot_id=NULL) and can never satisfy a
    scheduled slot. The busy/conflict response is deliberately minimal —
    it never exposes the lease owner, fencing token, secret, or any raw
    internal/database detail.
    """
    _require_validation_secret(x_secret)

    import os
    from datetime import datetime, timezone
    import uuid
    from services.validation_engine import (
        admit_validation_attempt, execute_and_complete_admitted_attempt,
        NIFTY_100, NSE_MIDCAP, US_BASKET, UNIVERSE_MARKET, UNIVERSE_VERSION,
        VALIDATION_MODEL_VERSION, VALIDATION_METHODOLOGY_VERSION,
    )

    owner = f"manual-{uuid.uuid4()}"
    now_dt = datetime.now(timezone.utc)
    admitted = admit_validation_attempt(
        horizon=horizon, universe=universe, trigger_type="manual", owner=owner,
        now=now_dt,
    )
    if not admitted.get("ok"):
        # V-SCHED1C1-C1 — restore the pre-V-SCHED1C1 busy-response shape
        # (status + job) where it can be reconstructed WITHOUT exposing
        # anything the ledger holds privately (lease owner, fencing token,
        # secret, DB details). There is no "current job" concept to report
        # here anymore — a rejected admission simply means the single
        # global lease is held by someone else — so `job` is omitted
        # rather than fabricated; a caller that only branches on `status`
        # (the documented contract) is unaffected either way.
        return _json_response({"status": "already_running"})

    attempt_id = admitted["attempt_id"]
    fencing_token = admitted["fencing_token"]
    universe_labels = {"nifty100": "Nifty 100", "midcap": "NSE Midcap", "us": "US S&P 500 basket"}
    universe_map = {"nifty100": NIFTY_100, "midcap": NSE_MIDCAP, "us": US_BASKET}
    benchmark = "^GSPC" if universe == "us" else "^NSEI"

    def _run():
        execute_and_complete_admitted_attempt(
            attempt_id, owner, fencing_token, horizon, universe, "manual",
        )

    background_tasks.add_task(_run)

    # V-SCHED1C1-C2 — `job` restores the pre-V-SCHED1C1 accepted-response
    # shape exactly: every field below is either a static/public constant
    # (model/methodology/universe version, benchmark, universe size, source
    # commit — same env var already publicly exposed before this change)
    # or directly known from this admission call. `job_id` is a genuinely
    # fresh uuid.uuid4() — matching the exact pre-V-SCHED1C1 public format
    # — generated purely for public display; it is NOT derived from and
    # carries no relationship to the durable integer attempt_id, which
    # stays internal to this closure (used only for admission/completion)
    # and is never itself exposed. It intentionally does NOT expose the
    # ledger's lease owner or fencing token, which no pre-V-SCHED1C1
    # consumer ever received either. `data_cutoff`/`requested_by` were
    # always None/not-captured at this same point in the pre-V-SCHED1C1
    # code too, so this is not a loss of information a caller ever
    # actually had.
    job = {
        "job_id": str(uuid.uuid4()),
        "market": UNIVERSE_MARKET.get(universe),
        "universe_id": universe,
        "universe_version": UNIVERSE_VERSION.get(universe, "unknown"),
        "benchmark": benchmark,
        "horizon": horizon,
        "started_at": now_dt.isoformat(),
        "completed_at": None,
        "status": "running",
        "processed": 0,
        "total": len(universe_map.get(universe, [])),
        "current_symbol": None,
        "source_commit": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
        "model_version": VALIDATION_MODEL_VERSION,
        "methodology_version": VALIDATION_METHODOLOGY_VERSION,
        "data_cutoff": None,
        "data_cutoff_basis": "not_captured",
        "requested_by": None,
        "trigger_type": "manual",
        "created_at": now_dt.isoformat(),
        "updated_at": now_dt.isoformat(),
        "failure_code": None,
        "failure_message": None,
    }

    return _json_response({
        "status": "started",
        "horizon": horizon,
        "universe": universe,
        "job": job,
        "message": (
            f"Walk-forward validation started across all {universe_labels.get(universe, universe)} "
            f"stocks ({horizon} horizon). Poll /api/validation/status for progress."
        ),
    })


@router.get("/status")
def get_status():
    """Poll this endpoint to track validation run progress."""
    from services.validation_engine import get_run_status
    return _json_response(get_run_status())


@router.get("/results")
def get_results(
    horizon: Literal["short", "medium", "long"] = Query("medium"),
    universe: Literal["nifty100", "midcap", "us"] = Query("nifty100"),
):
    """Return aggregate validation metrics for the latest run of the given horizon + universe."""
    from services.validation_engine import get_latest_results
    try:
        return _json_response(get_latest_results(horizon=horizon, universe=universe))
    except Exception as e:
        return _json_response({"available": False, "error": safe_error_message(
            log, "validation.get_results", e, "Validation data is temporarily unavailable.")})


@router.get("/results/stocks")
def get_stock_results(
    horizon: Literal["short", "medium", "long"] = Query("medium"),
    universe: Literal["nifty100", "midcap", "us"] = Query("nifty100"),
    run_id: int | None = Query(None),
):
    """Per-stock hit rate and average return breakdown for the given run.

    V-SNAP1B — optional `run_id` pins the response to one exact,
    immutable validation run (matching the run_id the aggregate
    /results endpoint returns), closing the race where a newer run
    could complete between the aggregate and per-stock requests. Every
    response now also includes the resolved `run_id` so the frontend
    can verify identity before rendering. An explicit `run_id` that
    doesn't exist, or belongs to a different horizon/universe, or is
    not eligible, fails closed with the same generic sanitized
    unavailable response already used elsewhere on this router — never
    a silent fallback to latest, and never a distinguishable error that
    would let a caller probe which specific reason caused it.
    """
    from services.validation_engine import get_per_stock_results, resolve_eligible_run_id
    try:
        resolved_run_id = resolve_eligible_run_id(run_id, horizon, universe)
        if resolved_run_id is None:
            return _json_response({
                "available": False, "horizon": horizon, "run_id": None, "stocks": [],
                "error": "Validation data is temporarily unavailable.",
            })
        return _json_response({
            "available": True, "horizon": horizon, "run_id": resolved_run_id,
            "stocks": get_per_stock_results(run_id=resolved_run_id, horizon=horizon, universe=universe),
        })
    except Exception as e:
        return _json_response({"available": False, "horizon": horizon, "run_id": None, "stocks": [], "error": safe_error_message(
            log, "validation.get_stock_results", e, "Validation data is temporarily unavailable.")})


@router.get("/results/stock/{symbol}")
def get_single_stock_accuracy(
    symbol: str,
    horizon: Literal["short", "medium", "long"] = Query("medium"),
    universe: Literal["nifty100", "midcap", "us"] = Query("nifty100"),
):
    """Accuracy stats for a single stock symbol across all horizons."""
    from services.validation_engine import get_per_stock_results
    try:
        all_results = {}
        for h in ["short", "medium", "long"]:
            rows = get_per_stock_results(horizon=h, universe=universe)
            match = next((r for r in rows if r.get("symbol", "").upper() == symbol.upper()), None)
            if match:
                all_results[h] = match
        return _json_response({"available": True, "symbol": symbol, "accuracy": all_results})
    except Exception as e:
        return _json_response({"available": False, "symbol": symbol, "accuracy": {}, "error": safe_error_message(
            log, "validation.get_single_stock_accuracy", e, "Validation data is temporarily unavailable.")})


@router.get("/results/history")
def get_history():
    """List of all past validation runs with key summary metrics."""
    from services.validation_engine import get_all_run_summaries
    try:
        return _json_response({"available": True, "runs": get_all_run_summaries()})
    except Exception as e:
        return _json_response({"available": False, "runs": [], "error": safe_error_message(
            log, "validation.get_history", e, "Validation data is temporarily unavailable.")})
