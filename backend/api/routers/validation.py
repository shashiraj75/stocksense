"""
Validation API — exposes walk-forward backtest results to the frontend.
"""
import asyncio
from fastapi import APIRouter, Query, BackgroundTasks
from typing import Literal

router = APIRouter()


@router.post("/run")
async def trigger_validation(
    background_tasks: BackgroundTasks,
    horizon: Literal["short", "medium", "long"] = Query("medium"),
    n_stocks: int = Query(50, ge=10, le=100),
):
    """
    Trigger a walk-forward validation run in the background.
    Returns immediately — poll /status for progress, /results for output.
    """
    from services.validation_engine import run_validation, get_run_status

    status = get_run_status()
    if status.get("running"):
        return {"status": "already_running", "progress": status.get("progress"), "total": status.get("total")}

    def _run():
        run_validation(horizon=horizon, n_stocks=n_stocks)

    background_tasks.add_task(_run)
    return {
        "status": "started",
        "horizon": horizon,
        "n_stocks": n_stocks,
        "message": f"Walk-forward validation started across {n_stocks} Nifty 100 stocks. Poll /api/validation/status for progress.",
    }


@router.get("/status")
def get_status():
    """Poll this endpoint to track validation run progress."""
    from services.validation_engine import get_run_status
    return get_run_status()


@router.get("/results")
def get_results(horizon: Literal["short", "medium", "long"] = Query("medium")):
    """Return aggregate validation metrics for the latest run of the given horizon."""
    from services.validation_engine import get_latest_results
    return get_latest_results(horizon=horizon)


@router.get("/results/stocks")
def get_stock_results(horizon: Literal["short", "medium", "long"] = Query("medium")):
    """Per-stock hit rate and average return breakdown for the latest run."""
    from services.validation_engine import get_per_stock_results
    return {"horizon": horizon, "stocks": get_per_stock_results(horizon=horizon)}


@router.get("/results/history")
def get_history():
    """List of all past validation runs with key summary metrics."""
    from services.validation_engine import get_all_run_summaries
    return {"runs": get_all_run_summaries()}
