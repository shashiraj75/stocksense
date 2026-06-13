from fastapi import APIRouter, Query
from services.backtester import run_backtest
from typing import Literal

router = APIRouter()


@router.get("/{symbol}")
async def backtest(
    symbol: str,
    market: Literal["US", "IN"] = Query("US"),
    horizon: Literal["short", "medium", "long"] = Query("short"),
):
    """
    Runs a walk-forward backtest on the prediction engine.
    Tests signals at regular intervals in history and measures accuracy.
    """
    return run_backtest(symbol.upper(), market, horizon)
