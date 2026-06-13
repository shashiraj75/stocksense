from fastapi import APIRouter, Query
from services.screener_service import ScreenerService
from typing import Literal, Optional

router = APIRouter()
svc = ScreenerService()


@router.get("/top-movers")
async def top_movers(market: Literal["US", "IN"] = Query("US")):
    return await svc.get_top_movers(market)


@router.get("/filter")
async def filter_stocks(
    market: Literal["US", "IN"] = Query("US"),
    min_market_cap: Optional[float] = None,
    max_pe: Optional[float] = None,
    min_roe: Optional[float] = None,
    sector: Optional[str] = None,
    signal: Optional[Literal["BUY", "HOLD", "SELL"]] = None,
):
    return await svc.filter_stocks(
        market, min_market_cap, max_pe, min_roe, sector, signal
    )
