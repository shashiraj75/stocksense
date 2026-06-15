import time
import asyncio
import yfinance as yf
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


# ── Live index data ────────────────────────────────────────────────────────────
_index_cache: dict[str, tuple[float, dict]] = {}
_INDEX_TTL = 60  # 1 minute

INDICES = {
    "IN":     [("^NSEI", "NIFTY 50"), ("^BSESN", "SENSEX")],
    "US":     [("^GSPC", "S&P 500"), ("^IXIC", "NASDAQ"), ("^DJI", "DOW")],
    "CRYPTO": [("BTC-USD", "Bitcoin")],
}

def _fetch_index(ticker_sym: str, name: str) -> dict:
    try:
        fi = yf.Ticker(ticker_sym).fast_info
        price = float(fi.last_price) if fi.last_price else None
        prev  = float(fi.previous_close) if fi.previous_close else None
        change_pct = round((price - prev) / prev * 100, 2) if price and prev else None
        change_pts = round(price - prev, 2) if price and prev else None
        return {
            "symbol": ticker_sym,
            "name": name,
            "price": round(price, 2) if price else None,
            "change_pct": change_pct,
            "change_pts": change_pts,
        }
    except Exception:
        return {"symbol": ticker_sym, "name": name, "price": None, "change_pct": None, "change_pts": None}


@router.get("/indices")
async def get_indices(market: Literal["US", "IN", "CRYPTO"] = Query("IN")):
    cached = _index_cache.get(market)
    if cached and (time.time() - cached[0]) < _INDEX_TTL:
        return cached[1]

    loop = asyncio.get_running_loop()
    pairs = INDICES.get(market, [])
    results = await asyncio.gather(*[
        loop.run_in_executor(None, _fetch_index, sym, name)
        for sym, name in pairs
    ])
    data = {"indices": list(results)}
    _index_cache[market] = (time.time(), data)
    return data
