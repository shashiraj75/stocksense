from fastapi import APIRouter, Query, HTTPException
from services.market_data import MarketDataService
from typing import Literal

router = APIRouter()
svc = MarketDataService()


@router.get("/quote/{symbol}")
async def get_quote(
    symbol: str,
    market: Literal["US", "IN"] = Query("US", description="US or IN (India)"),
):
    data = await svc.get_quote(symbol.upper(), market)
    if not data:
        raise HTTPException(status_code=404, detail="Symbol not found")
    return data


@router.get("/ohlcv/{symbol}")
async def get_ohlcv(
    symbol: str,
    market: Literal["US", "IN"] = Query("US"),
    period: Literal["1mo", "3mo", "6mo", "1y", "2y", "5y"] = "1y",
    interval: Literal["1d", "1wk", "1mo"] = "1d",
):
    return await svc.get_ohlcv(symbol.upper(), market, period, interval)


@router.get("/search")
async def search_stocks(
    q: str = Query(..., min_length=1),
    market: Literal["US", "IN", "ALL"] = "ALL",
):
    return await svc.search(q, market)


@router.get("/fundamentals/{symbol}")
async def get_fundamentals(
    symbol: str,
    market: Literal["US", "IN"] = Query("US"),
):
    return await svc.get_fundamentals(symbol.upper(), market)
