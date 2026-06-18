"""
Validation API — exposes walk-forward backtest results to the frontend.
"""
import numpy as np
from fastapi import APIRouter, Query, BackgroundTasks
from fastapi.responses import JSONResponse
from typing import Literal

router = APIRouter()


def _safe_json(obj):
    """Recursively convert numpy scalars / ndarrays to native Python types."""
    if isinstance(obj, dict):
        return {k: _safe_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_safe_json(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def _json_response(data: dict) -> JSONResponse:
    return JSONResponse(content=_safe_json(data))


@router.post("/run")
async def trigger_validation(
    background_tasks: BackgroundTasks,
    horizon: Literal["short", "medium", "long"] = Query("medium"),
):
    """
    Trigger a walk-forward validation run across all Nifty 100 stocks.
    Returns immediately — poll /status for progress, /results for output.
    """
    from services.validation_engine import run_validation, get_run_status

    status = get_run_status()
    if status.get("running"):
        return _json_response({"status": "already_running", "progress": status.get("progress"), "total": status.get("total")})

    def _run():
        run_validation(horizon=horizon)

    background_tasks.add_task(_run)
    return _json_response({
        "status": "started",
        "horizon": horizon,
        "message": f"Walk-forward validation started across all Nifty 100 stocks ({horizon} horizon). Poll /api/validation/status for progress.",
    })


@router.get("/status")
def get_status():
    """Poll this endpoint to track validation run progress."""
    from services.validation_engine import get_run_status
    return _json_response(get_run_status())


@router.get("/results")
def get_results(horizon: Literal["short", "medium", "long"] = Query("medium")):
    """Return aggregate validation metrics for the latest run of the given horizon."""
    from services.validation_engine import get_latest_results
    try:
        return _json_response(get_latest_results(horizon=horizon))
    except Exception as e:
        import traceback
        return _json_response({"available": False, "error": str(e), "trace": traceback.format_exc()})


@router.get("/results/stocks")
def get_stock_results(horizon: Literal["short", "medium", "long"] = Query("medium")):
    """Per-stock hit rate and average return breakdown for the latest run."""
    from services.validation_engine import get_per_stock_results
    try:
        return _json_response({"horizon": horizon, "stocks": get_per_stock_results(horizon=horizon)})
    except Exception as e:
        return _json_response({"horizon": horizon, "stocks": [], "error": str(e)})


@router.get("/results/stock/{symbol}")
def get_single_stock_accuracy(symbol: str, horizon: Literal["short", "medium", "long"] = Query("medium")):
    """Accuracy stats for a single stock symbol across all horizons."""
    from services.validation_engine import get_per_stock_results
    try:
        all_results = {}
        for h in ["short", "medium", "long"]:
            rows = get_per_stock_results(horizon=h)
            match = next((r for r in rows if r.get("symbol", "").upper() == symbol.upper()), None)
            if match:
                all_results[h] = match
        return _json_response({"symbol": symbol, "accuracy": all_results})
    except Exception as e:
        return _json_response({"symbol": symbol, "accuracy": {}, "error": str(e)})


@router.get("/results/history")
def get_history():
    """List of all past validation runs with key summary metrics."""
    from services.validation_engine import get_all_run_summaries
    try:
        return _json_response({"runs": get_all_run_summaries()})
    except Exception as e:
        return _json_response({"runs": [], "error": str(e)})
