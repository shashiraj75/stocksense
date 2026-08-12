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
    including the concurrency/claim check below, so an unauthenticated
    caller can never learn whether a run is currently active. The internal
    scheduler (api/main.py's _validation_schedule_loop/_catchup_validation)
    calls run_validation() directly as a Python function and never goes
    through this HTTP route, so it is unaffected by this check.
    """
    _require_validation_secret(x_secret)

    from services.validation_engine import run_validation, get_run_status, claim_validation_job

    # Claim the run slot synchronously so the response can carry the real,
    # immutable job identity (market/universe/horizon) — the /status payload
    # is bound to this identity for the whole run. Never infer market or
    # universe from frontend tab state.
    job = claim_validation_job(horizon, universe, trigger_type="api")
    if job is None:
        status = get_run_status()
        return _json_response({
            "status": "already_running",
            "progress": status.get("progress"),
            "total": status.get("total"),
            "job": status.get("job"),
        })

    universe_labels = {"nifty100": "Nifty 100", "midcap": "NSE Midcap", "us": "US S&P 500 basket"}

    def _run():
        run_validation(horizon=horizon, universe=universe, _claimed_job=job)

    background_tasks.add_task(_run)
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
):
    """Per-stock hit rate and average return breakdown for the latest run."""
    from services.validation_engine import get_per_stock_results
    try:
        return _json_response({
            "available": True, "horizon": horizon,
            "stocks": get_per_stock_results(horizon=horizon, universe=universe),
        })
    except Exception as e:
        return _json_response({"available": False, "horizon": horizon, "stocks": [], "error": safe_error_message(
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
