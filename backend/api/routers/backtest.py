import traceback
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from services.backtester import run_backtest
from typing import Literal

router = APIRouter()


@router.get("/{symbol}")
async def backtest(
    symbol: str,
    market: Literal["US", "IN"] = Query("US"),
    horizon: Literal["short", "medium", "long"] = Query("short"),
):
    try:
        return run_backtest(symbol.upper(), market, horizon)
    except Exception as e:
        tb = traceback.format_exc()
        return JSONResponse(status_code=500, content={"error": str(e), "trace": tb})
