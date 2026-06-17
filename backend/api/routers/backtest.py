import asyncio
import logging
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from services.backtester import run_backtest
from typing import Literal

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/{symbol}")
async def backtest(
    symbol: str,
    market: Literal["US", "IN", "CRYPTO"] = Query("US"),
    horizon: Literal["short", "medium", "long"] = Query("short"),
):
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, run_backtest, symbol.upper(), market, horizon)
    except Exception as e:
        log.exception("Backtest failed for %s (%s/%s)", symbol, market, horizon)
        return JSONResponse(status_code=500, content={"error": "Backtest failed. Please try again."})
