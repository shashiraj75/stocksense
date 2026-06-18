"""
Finnhub API client for real-time stock quotes.
Used for Indian (NSE/BSE) and US stocks where yfinance is blocked from cloud IPs.
"""
import os
import time
import logging
import requests
from typing import Optional

log = logging.getLogger(__name__)

FINNHUB_KEY = os.getenv("FINNHUB_API_KEY", "")
_BASE = "https://finnhub.io/api/v1"
_SESSION = requests.Session()
_SESSION.headers.update({"X-Finnhub-Token": FINNHUB_KEY})

# Simple in-process cache to respect the 60 req/min free tier limit
_cache: dict[str, tuple[float, dict]] = {}
_QUOTE_TTL = 60  # seconds


def _finnhub_symbol(symbol: str, market: str) -> str:
    """Convert internal symbol to Finnhub format: NSE:RELIANCE, NYSE:AAPL etc."""
    symbol = symbol.upper().replace(".NS", "").replace(".BO", "")
    if market == "IN":
        return f"NSE:{symbol}"
    elif market == "CRYPTO":
        return f"BINANCE:{symbol}USDT"
    return symbol  # US symbols are plain (AAPL, MSFT etc.)


def get_quote(symbol: str, market: str) -> Optional[dict]:
    """
    Fetch real-time quote from Finnhub.
    Returns dict with price, prev_close, change, change_pct or None on failure.
    """
    if not FINNHUB_KEY:
        return None

    cache_key = f"{symbol}:{market}"
    cached = _cache.get(cache_key)
    if cached and (time.time() - cached[0]) < _QUOTE_TTL:
        return cached[1]

    fh_sym = _finnhub_symbol(symbol, market)
    try:
        r = _SESSION.get(f"{_BASE}/quote", params={"symbol": fh_sym}, timeout=5)
        r.raise_for_status()
        data = r.json()
        price = data.get("c")      # current price
        prev  = data.get("pc")     # previous close
        if not price or not prev or price == 0:
            return None
        result = {
            "symbol": symbol,
            "market": market,
            "price": round(float(price), 2),
            "prev_close": round(float(prev), 2),
            "change": round(float(price) - float(prev), 2),
            "change_pct": round((float(price) - float(prev)) / float(prev) * 100, 2),
            "high": data.get("h"),
            "low": data.get("l"),
            "open": data.get("o"),
        }
        _cache[cache_key] = (time.time(), result)
        return result
    except Exception as e:
        log.warning("Finnhub quote failed for %s (%s): %s", fh_sym, symbol, e)
        return None


def get_quotes_bulk(symbols: list[str], market: str) -> dict[str, dict]:
    """
    Fetch quotes for a list of symbols. Returns {symbol: quote_dict}.
    Respects rate limit with small delays between batches.
    """
    results = {}
    for i, sym in enumerate(symbols):
        q = get_quote(sym, market)
        if q:
            results[sym] = q
        # Small delay every 10 requests to stay under 60 req/min
        if (i + 1) % 10 == 0:
            time.sleep(1)
    return results
