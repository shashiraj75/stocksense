import asyncio
import logging
import time
import yfinance as yf
from typing import Optional
from services import finnhub_client

log = logging.getLogger(__name__)

MARKET_SUFFIX = {"US": "", "IN": ".NS"}

# ── In-process caches ──────────────────────────────────────────────────────────
_quote_cache: dict[str, tuple[float, dict]] = {}
_QUOTE_TTL = 60  # 1 min

_fundamentals_cache: dict[str, tuple[float, dict]] = {}
_FUND_TTL = 12 * 3600  # 12 hours — fundamentals change infrequently

_ohlcv_cache: dict[str, tuple[float, dict]] = {}
_OHLCV_TTL = 5 * 60  # 5 min — chart data doesn't need to be real-time

_name_cache: dict[str, tuple[float, str]] = {}
_NAME_TTL = 24 * 3600  # company names change rarely — cache for 24 hours


def _fetch_name_sync(sym_yf: str, sym_fh: str, market: str) -> Optional[str]:
    """
    Fetch company name. Tries Finnhub profile first (fast, <200ms),
    falls back to yfinance .info. Runs in a thread via run_in_executor
    so it never blocks the async event loop.
    """
    # 1. Finnhub /stock/profile2 — fast, no crumb issues
    if finnhub_client.FINNHUB_KEY:
        try:
            r = finnhub_client._SESSION.get(
                f"{finnhub_client._BASE}/stock/profile2",
                params={"symbol": sym_fh},
                timeout=3,
            )
            if r.status_code == 200:
                d = r.json()
                name = d.get("name")
                if name:
                    return name
        except Exception:
            pass

    # 2. yfinance .info fallback
    try:
        info = yf.Ticker(sym_yf).info
        if isinstance(info, dict):
            return info.get("longName") or info.get("shortName")
    except Exception:
        pass

    return None


class MarketDataService:
    def _sym(self, symbol: str, market: str) -> str:
        return symbol + MARKET_SUFFIX.get(market, "")

    def _fh_sym(self, symbol: str, market: str) -> str:
        """Finnhub symbol format: NSE:SHALBY, AAPL etc."""
        s = symbol.upper().replace(".NS", "").replace(".BO", "")
        if market == "IN":
            return f"NSE:{s}"
        elif market == "CRYPTO":
            return f"BINANCE:{s}USDT"
        return s

    async def get_quote(self, symbol: str, market: str) -> Optional[dict]:
        key = f"{symbol}:{market}"
        cached = _quote_cache.get(key)
        if cached and (time.time() - cached[0]) < _QUOTE_TTL:
            return cached[1]

        # 1. Try Finnhub first (fast, reliable from cloud IPs, 60s internal cache)
        result = finnhub_client.get_quote(symbol, market)

        # 2. Fallback to yfinance fast_info (one call only)
        if not result:
            try:
                fi = yf.Ticker(self._sym(symbol, market)).fast_info
                price = fi.last_price
                prev  = fi.previous_close
                if price and prev:
                    result = {
                        "symbol":     symbol,
                        "market":     market,
                        "price":      round(float(price), 2),
                        "prev_close": round(float(prev), 2),
                        "change":     round(float(price - prev), 2),
                        "change_pct": round(float((price - prev) / prev * 100), 2),
                        "high":       fi.day_high,
                        "low":        fi.day_low,
                        "open":       fi.open,
                        "volume":     int(fi.three_month_average_volume or 0),
                        "market_cap": fi.market_cap,
                        "fifty_two_week_high": fi.year_high,
                        "fifty_two_week_low":  fi.year_low,
                    }
            except Exception as e:
                log.warning("get_quote yfinance fallback failed %s/%s: %s", symbol, market, e)
                return None

        # 3. Enrich Finnhub result with fast_info extras if missing
        if result and not result.get("market_cap"):
            try:
                fi = yf.Ticker(self._sym(symbol, market)).fast_info
                result.setdefault("volume",              int(fi.three_month_average_volume or 0))
                result.setdefault("market_cap",          fi.market_cap)
                result.setdefault("fifty_two_week_high", fi.year_high)
                result.setdefault("fifty_two_week_low",  fi.year_low)
            except Exception:
                pass

        # 4. Company name — 24h cache. Price is NEVER blocked by the name fetch.
        #    Cache hit → attach instantly. Cache miss → fire background task and
        #    return the quote immediately; name appears on the next quote refresh.
        if result:
            cached_name = _name_cache.get(key)
            if cached_name and (time.time() - cached_name[0]) < _NAME_TTL:
                result["company_name"] = cached_name[1]
            else:
                sym_yf = self._sym(symbol, market)
                sym_fh = self._fh_sym(symbol, market)
                loop = asyncio.get_event_loop()
                async def _bg_fetch_name(k=key, yf=sym_yf, fh=sym_fh, mkt=market):
                    try:
                        name = await asyncio.wait_for(
                            loop.run_in_executor(None, _fetch_name_sync, yf, fh, mkt),
                            timeout=5.0,
                        )
                        if name:
                            _name_cache[k] = (time.time(), name)
                            # Patch the cached quote so the next cache hit includes the name
                            cached_q = _quote_cache.get(k)
                            if cached_q:
                                cached_q[1]["company_name"] = name
                    except Exception:
                        pass
                asyncio.ensure_future(_bg_fetch_name())

        if result:
            _quote_cache[key] = (time.time(), result)
        return result

    async def get_ohlcv(self, symbol: str, market: str, period: str, interval: str) -> dict:
        key = f"{symbol}:{market}:{period}:{interval}"
        cached = _ohlcv_cache.get(key)
        if cached and (time.time() - cached[0]) < _OHLCV_TTL:
            return cached[1]

        try:
            df = yf.Ticker(self._sym(symbol, market)).history(period=period, interval=interval)
            df.reset_index(inplace=True)
            result = {
                "symbol": symbol,
                "market": market,
                "data": [
                    {
                        "date":   str(row["Date"])[:10],
                        "open":   round(row["Open"],   2),
                        "high":   round(row["High"],   2),
                        "low":    round(row["Low"],    2),
                        "close":  round(row["Close"],  2),
                        "volume": int(row["Volume"]),
                    }
                    for _, row in df.iterrows()
                ],
            }
        except Exception as e:
            log.warning("get_ohlcv failed %s/%s: %s", symbol, market, e)
            result = {"symbol": symbol, "market": market, "data": []}

        _ohlcv_cache[key] = (time.time(), result)
        return result

    async def get_fundamentals(self, symbol: str, market: str) -> dict:
        key = f"{symbol}:{market}"
        cached = _fundamentals_cache.get(key)
        if cached and (time.time() - cached[0]) < _FUND_TTL:
            return cached[1]

        info: dict = {}
        try:
            loop = asyncio.get_event_loop()
            info = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: yf.Ticker(self._sym(symbol, market)).info),
                timeout=8.0,
            )
        except asyncio.TimeoutError:
            log.warning("get_fundamentals timeout for %s/%s — returning partial data", symbol, market)
        except Exception as e:
            log.warning("get_fundamentals failed for %s/%s: %s", symbol, market, e)

        result = {
            "symbol":          symbol,
            "pe_ratio":        info.get("trailingPE"),
            "pb_ratio":        info.get("priceToBook"),
            "roe":             info.get("returnOnEquity"),
            "debt_to_equity":  info.get("debtToEquity"),
            "revenue_growth":  info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
            "dividend_yield":  info.get("dividendYield"),
            "sector":          info.get("sector"),
            "industry":        info.get("industry"),
            "description":     info.get("longBusinessSummary", "")[:500],
        }

        has_data = any(v is not None for v in [result["pe_ratio"], result["roe"], result["sector"]])
        ttl = _FUND_TTL if has_data else 3600
        _fundamentals_cache[key] = (time.time() - (_FUND_TTL - ttl), result)

        return result

    async def search(self, query: str, market: str) -> list:
        from services.stock_universe import search_universe
        return search_universe(query, market, limit=8)
