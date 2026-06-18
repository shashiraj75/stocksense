import time
import yfinance as yf
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# Fallback universes used only if live screener fails
US_UNIVERSE = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "JPM", "BAC", "XOM",
               "WMT", "JNJ", "V", "PG", "MA", "HD", "CVX", "MRK", "ABBV", "PEP"]
IN_UNIVERSE = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS",
               "WIPRO.NS", "SBIN.NS", "LT.NS", "BAJFINANCE.NS", "HINDUNILVR.NS",
               "ADANIENT.NS", "TATAMOTORS.NS", "SUNPHARMA.NS", "HCLTECH.NS", "AXISBANK.NS"]

# TTL cache: { market -> (timestamp, result) }
_movers_cache: dict[str, tuple[float, dict]] = {}
_MOVERS_TTL = 120  # seconds


def _quotes_to_movers(quotes: list, suffix_strip: str = "") -> list:
    movers = []
    for q in quotes:
        sym = q.get("symbol", "")
        price = q.get("regularMarketPrice") or q.get("regularMarketPrice")
        change_pct = q.get("regularMarketChangePercent")
        name = q.get("shortName") or q.get("longName") or ""
        if sym and price is not None and change_pct is not None:
            movers.append({
                "symbol": sym.replace(".NS", "").replace(".BO", ""),
                "price": round(float(price), 2),
                "change_pct": round(float(change_pct), 2),
                "name": name,
            })
    return movers


def _live_movers_in() -> list:
    """Fetch actual NSE top movers via yfinance EquityQuery screener."""
    # min market cap ~100 Cr (1B INR) to filter out illiquid micro-caps
    min_mcap = 1_000_000_000
    gainers_q = yf.EquityQuery("and", [
        yf.EquityQuery("eq", ["exchange", "NSI"]),
        yf.EquityQuery("gt", ["intradaymarketcap", min_mcap]),
        yf.EquityQuery("gt", ["percentchange", 0]),
    ])
    losers_q = yf.EquityQuery("and", [
        yf.EquityQuery("eq", ["exchange", "NSI"]),
        yf.EquityQuery("gt", ["intradaymarketcap", min_mcap]),
        yf.EquityQuery("lt", ["percentchange", 0]),
    ])
    gainers = yf.screen(gainers_q, sortField="percentchange", sortAsc=False, count=10).get("quotes", [])
    losers  = yf.screen(losers_q,  sortField="percentchange", sortAsc=True,  count=10).get("quotes", [])
    all_movers = _quotes_to_movers(gainers) + _quotes_to_movers(losers)
    all_movers.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    return all_movers[:10]


def _live_movers_us() -> list:
    """Fetch actual US top movers via yfinance predefined screeners."""
    gainers = yf.screen("day_gainers", count=10).get("quotes", [])
    losers  = yf.screen("day_losers",  count=10).get("quotes", [])
    all_movers = _quotes_to_movers(gainers) + _quotes_to_movers(losers)
    all_movers.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    return all_movers[:10]


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
                "name": "",
            }
    except Exception:
        pass
    return None


class ScreenerService:
    async def get_top_movers(self, market: str) -> dict:
        cached = _movers_cache.get(market)
        if cached and (time.time() - cached[0]) < _MOVERS_TTL:
            return cached[1]

        movers = []
        try:
            if market == "IN":
                movers = _live_movers_in()
            elif market == "US":
                movers = _live_movers_us()
        except Exception:
            pass

        # Fallback to fixed universe if screener fails
        if not movers:
            universe = US_UNIVERSE if market == "US" else IN_UNIVERSE
            with ThreadPoolExecutor(max_workers=20) as pool:
                futures = {pool.submit(_fetch_mover, sym): sym for sym in universe}
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        movers.append(result)
            movers.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
            movers = movers[:10]

        response = {"market": market, "movers": movers}
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
