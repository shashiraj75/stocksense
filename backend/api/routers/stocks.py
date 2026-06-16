import time
import asyncio
import yfinance as yf
from fastapi import APIRouter, Query, HTTPException
from services.market_data import MarketDataService
from services.prediction_engine import PredictionEngine
from typing import Literal

router = APIRouter()
svc = MarketDataService()
_engine = PredictionEngine()

FACTOR_LABELS = {
    "technical":        "Technical",
    "fundamental":      "Fundamentals",
    "sentiment":        "News Sentiment",
    "regime":           "Market Regime",
    "global_macro":     "Global Macro",
    "analyst":          "Analyst Consensus",
    "week52":           "52-Week Position",
    "quality":          "Quality Factors",
    "clamp_adjustment":   "Score Bounds Adjustment",
    "risk_penalty":       "Risk Penalty",
    "rounding_adjustment": "Rounding Adjustment",
}


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
_INDEX_TTL = 15  # 15 seconds — fast_info is lightweight enough for near-live updates

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


@router.get("/{symbol}/factor-attribution")
async def get_factor_attribution(
    symbol: str,
    market: Literal["US", "IN"] = Query("US"),
    horizon: Literal["short", "medium", "long"] = Query("medium"),
):
    result = await _engine.predict(symbol.upper(), market, horizon)
    if not result or result.get("signal") == "REJECTED":
        raise HTTPException(status_code=404, detail="No attribution available — prediction rejected or unavailable")

    contributions_raw = result.get("factor_contributions") or {}
    contributions = []
    positive_total = 0.0
    negative_total = 0.0
    for factor, value in contributions_raw.items():
        value = round(value, 2)
        direction = "positive" if value >= 0 else "negative"
        if value >= 0:
            positive_total += value
        else:
            negative_total += value
        contributions.append({
            "factor": factor,
            "label": FACTOR_LABELS.get(factor, factor.replace("_", " ").title()),
            "contribution": value,
            "direction": direction,
        })

    return {
        "symbol": symbol.upper(),
        "horizon": horizon,
        "composite_score": result.get("composite_score"),
        "contributions": contributions,
        "positive_total": round(positive_total, 2),
        "negative_total": round(negative_total, 2),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


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
