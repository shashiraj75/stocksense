import time
import yfinance as yf
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

US_UNIVERSE = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "JPM", "BAC", "XOM",
               "WMT", "JNJ", "V", "PG", "MA", "HD", "CVX", "MRK", "ABBV", "PEP"]
IN_UNIVERSE = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS",
               "WIPRO.NS", "SBIN.NS", "LT.NS", "BAJFINANCE.NS", "HINDUNILVR.NS",
               "ADANIENT.NS", "TATAMOTORS.NS", "SUNPHARMA.NS", "HCLTECH.NS", "AXISBANK.NS"]

# TTL cache: { market -> (timestamp, result) }
_movers_cache: dict[str, tuple[float, dict]] = {}
_MOVERS_TTL = 60  # seconds


def _fetch_mover(sym: str) -> dict | None:
    try:
        fi = yf.Ticker(sym).fast_info
        price = float(fi.last_price)
        prev  = float(fi.previous_close)
        if price and prev and prev > 0:
            return {
                "symbol": sym.replace(".NS", "").replace(".BO", ""),
                "price": round(price, 2),
                "change_pct": round((price - prev) / prev * 100, 2),
            }
    except Exception:
        pass
    return None


class ScreenerService:
    async def get_top_movers(self, market: str) -> dict:
        cached = _movers_cache.get(market)
        if cached and (time.time() - cached[0]) < _MOVERS_TTL:
            return cached[1]

        universe = US_UNIVERSE if market == "US" else IN_UNIVERSE
        movers = []
        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = {pool.submit(_fetch_mover, sym): sym for sym in universe}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    movers.append(result)

        movers.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
        response = {"market": market, "movers": movers[:10]}
        _movers_cache[market] = (time.time(), response)
        return response

    async def filter_stocks(
        self,
        market: str,
        min_market_cap: Optional[float],
        max_pe: Optional[float],
        min_roe: Optional[float],
        sector: Optional[str],
        signal: Optional[str],
    ) -> dict:
        universe = US_UNIVERSE if market == "US" else IN_UNIVERSE
        results = []
        for sym in universe:
            try:
                info = yf.Ticker(sym).info
                pe = info.get("trailingPE") or 0
                roe = info.get("returnOnEquity") or 0
                mcap = info.get("marketCap") or 0
                sec = info.get("sector", "")
                passes = True
                if min_market_cap and mcap < min_market_cap:
                    passes = False
                if max_pe and pe and pe > max_pe:
                    passes = False
                if min_roe and roe < min_roe:
                    passes = False
                if sector and sector.lower() not in sec.lower():
                    passes = False
                if passes:
                    results.append({
                        "symbol": sym.replace(".NS", "").replace(".BO", ""),
                        "sector": sec,
                        "pe": round(pe, 2) if pe else None,
                        "roe": round(roe * 100, 2) if roe else None,
                        "market_cap": mcap,
                    })
            except Exception:
                pass
        return {"market": market, "results": results}
