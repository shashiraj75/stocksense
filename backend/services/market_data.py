import logging
import time
import random
import yfinance as yf
from typing import Optional
from services import finnhub_client

log = logging.getLogger(__name__)

MARKET_SUFFIX = {"US": "", "IN": ".NS"}

# Quote cache: { "SYMBOL:MARKET" -> (timestamp, result) }
_quote_cache: dict[str, tuple[float, dict]] = {}
_QUOTE_TTL = 60  # 1 min


class MarketDataService:
    def _sym(self, symbol: str, market: str) -> str:
        return symbol + MARKET_SUFFIX.get(market, "")

    async def get_quote(self, symbol: str, market: str) -> Optional[dict]:
        key = f"{symbol}:{market}"
        cached = _quote_cache.get(key)
        if cached and (time.time() - cached[0]) < _QUOTE_TTL:
            return cached[1]

        # Try Finnhub first (reliable from cloud IPs)
        result = finnhub_client.get_quote(symbol, market)

        # Fallback to yfinance if Finnhub unavailable or key not set
        if not result:
            try:
                t = yf.Ticker(self._sym(symbol, market))
                fi = t.fast_info
                price = fi.last_price
                prev = fi.previous_close
                result = {
                    "symbol": symbol,
                    "market": market,
                    "price": round(price, 2),
                    "prev_close": round(prev, 2),
                    "change": round(price - prev, 2),
                    "change_pct": round((price - prev) / prev * 100, 2),
                    "high": fi.day_high,
                    "low": fi.day_low,
                    "open": fi.open,
                }
            except Exception as e:
                log.warning("get_quote failed for %s/%s: %s", symbol, market, e)
                return None

        # Enrich with extra fields from yfinance fast_info (non-critical)
        try:
            fi = yf.Ticker(self._sym(symbol, market)).fast_info
            result["volume"] = int(fi.three_month_average_volume or 0)
            result["market_cap"] = fi.market_cap
            result["fifty_two_week_high"] = fi.year_high
            result["fifty_two_week_low"] = fi.year_low
        except Exception:
            pass

        _quote_cache[key] = (time.time(), result)
        return result

    async def get_ohlcv(self, symbol: str, market: str, period: str, interval: str) -> dict:
        t = yf.Ticker(self._sym(symbol, market))
        df = t.history(period=period, interval=interval)
        df.reset_index(inplace=True)
        return {
            "symbol": symbol,
            "market": market,
            "data": [
                {
                    "date": str(row["Date"])[:10],
                    "open": round(row["Open"], 2),
                    "high": round(row["High"], 2),
                    "low": round(row["Low"], 2),
                    "close": round(row["Close"], 2),
                    "volume": int(row["Volume"]),
                }
                for _, row in df.iterrows()
            ],
        }

    async def get_fundamentals(self, symbol: str, market: str) -> dict:
        for attempt in range(3):
            try:
                info = yf.Ticker(self._sym(symbol, market)).info
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(3 * (attempt + 1) + random.uniform(0, 1))
                else:
                    log.warning("get_fundamentals failed for %s/%s: %s", symbol, market, e)
                    info = {}
        return {
            "symbol": symbol,
            "pe_ratio": info.get("trailingPE"),
            "pb_ratio": info.get("priceToBook"),
            "roe": info.get("returnOnEquity"),
            "debt_to_equity": info.get("debtToEquity"),
            "revenue_growth": info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
            "dividend_yield": info.get("dividendYield"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "description": info.get("longBusinessSummary", "")[:500],
        }

    async def search(self, query: str, market: str) -> list:
        from services.stock_universe import search_universe
        return search_universe(query, market, limit=8)
