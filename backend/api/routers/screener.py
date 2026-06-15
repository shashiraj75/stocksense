import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import APIRouter, Query
from services.screener_service import ScreenerService
from services.heatmap_service import get_heatmap
from typing import Literal, Optional
import yfinance as yf

router = APIRouter()
svc = ScreenerService()

CRYPTO_UNIVERSE = ["BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD",
                   "DOGE-USD","ADA-USD","AVAX-USD","LINK-USD","DOT-USD"]
CRYPTO_NAMES = {"BTC":"Bitcoin","ETH":"Ethereum","BNB":"BNB","SOL":"Solana",
                "XRP":"XRP","DOGE":"Dogecoin","ADA":"Cardano","AVAX":"Avalanche",
                "LINK":"Chainlink","DOT":"Polkadot"}

_crypto_cache: tuple[float, dict] | None = None
_CRYPTO_TTL = 60  # seconds


def _fetch_crypto(yf_sym: str) -> dict:
    sym = yf_sym.replace("-USD", "")
    try:
        fi = yf.Ticker(yf_sym).fast_info
        price = round(float(fi.last_price), 4) if fi.last_price else None
        prev  = round(float(fi.previous_close), 4) if fi.previous_close else None
        change_pct = round((price - prev) / prev * 100, 2) if price and prev else 0
        return {"symbol": sym, "name": CRYPTO_NAMES.get(sym, sym), "price": price, "change_pct": change_pct}
    except Exception:
        return {"symbol": sym, "name": CRYPTO_NAMES.get(sym, sym), "price": None, "change_pct": 0}


@router.get("/crypto-movers")
async def crypto_movers():
    global _crypto_cache
    if _crypto_cache and (time.time() - _crypto_cache[0]) < _CRYPTO_TTL:
        return _crypto_cache[1]

    with ThreadPoolExecutor(max_workers=len(CRYPTO_UNIVERSE)) as pool:
        results = list(pool.map(_fetch_crypto, CRYPTO_UNIVERSE))

    response = {"movers": results}
    _crypto_cache = (time.time(), response)
    return response


@router.get("/top-movers")
async def top_movers(market: Literal["US", "IN"] = Query("US")):
    return await svc.get_top_movers(market)


@router.get("/heatmap")
async def heatmap(market: Literal["US", "IN"] = Query("IN")):
    import asyncio, traceback
    try:
        loop = asyncio.get_running_loop()
        sectors = await loop.run_in_executor(None, get_heatmap, market)
        return {"sectors": sectors}
    except Exception as e:
        traceback.print_exc()
        return {"sectors": [], "error": str(e)}


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
