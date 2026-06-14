from fastapi import APIRouter, Query
from services.screener_service import ScreenerService
from typing import Literal, Optional
import yfinance as yf

router = APIRouter()
svc = ScreenerService()

CRYPTO_UNIVERSE = ["BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD",
                   "DOGE-USD","ADA-USD","AVAX-USD","LINK-USD","DOT-USD"]
CRYPTO_NAMES = {"BTC":"Bitcoin","ETH":"Ethereum","BNB":"BNB","SOL":"Solana",
                "XRP":"XRP","DOGE":"Dogecoin","ADA":"Cardano","AVAX":"Avalanche",
                "LINK":"Chainlink","DOT":"Polkadot"}


@router.get("/crypto-movers")
async def crypto_movers():
    results = []
    for yf_sym in CRYPTO_UNIVERSE:
        sym = yf_sym.replace("-USD", "")
        try:
            t = yf.Ticker(yf_sym)
            info = t.fast_info
            price = round(float(info.last_price), 4) if info.last_price else None
            prev  = round(float(info.previous_close), 4) if info.previous_close else None
            if price and prev:
                change_pct = round((price - prev) / prev * 100, 2)
            else:
                change_pct = 0
            results.append({
                "symbol": sym, "name": CRYPTO_NAMES.get(sym, sym),
                "price": price, "change_pct": change_pct,
            })
        except Exception:
            results.append({"symbol": sym, "name": CRYPTO_NAMES.get(sym, sym), "price": None, "change_pct": 0})
    return {"movers": results}


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
