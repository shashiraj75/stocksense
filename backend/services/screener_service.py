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


MIN_MCAP_IN = 1_000_000_000  # ~100 Cr INR — filters out illiquid micro-caps


def _live_gainers_losers_in() -> tuple[list, list]:
    """Fetch top 10 NSE gainers and top 10 NSE losers from the full exchange."""
    gainers_q = yf.EquityQuery("and", [
        yf.EquityQuery("eq", ["exchange", "NSI"]),
        yf.EquityQuery("gt", ["intradaymarketcap", MIN_MCAP_IN]),
        yf.EquityQuery("gt", ["percentchange", 0]),
    ])
    losers_q = yf.EquityQuery("and", [
        yf.EquityQuery("eq", ["exchange", "NSI"]),
        yf.EquityQuery("gt", ["intradaymarketcap", MIN_MCAP_IN]),
        yf.EquityQuery("lt", ["percentchange", 0]),
    ])
    gainers = _quotes_to_movers(yf.screen(gainers_q, sortField="percentchange", sortAsc=False, count=10).get("quotes", []))
    losers  = _quotes_to_movers(yf.screen(losers_q,  sortField="percentchange", sortAsc=True,  count=10).get("quotes", []))
    return gainers[:10], losers[:10]


def _live_gainers_losers_us() -> tuple[list, list]:
    """Fetch top 10 US gainers and top 10 US losers via predefined screeners."""
    gainers = _quotes_to_movers(yf.screen("day_gainers", count=10).get("quotes", []))
    losers  = _quotes_to_movers(yf.screen("day_losers",  count=10).get("quotes", []))
    return gainers[:10], losers[:10]


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

        gainers, losers = [], []
        try:
            if market == "IN":
                gainers, losers = _live_gainers_losers_in()
            elif market == "US":
                gainers, losers = _live_gainers_losers_us()
        except Exception:
            pass

        # Fallback to fixed universe if screener fails
        if not gainers and not losers:
            universe = US_UNIVERSE if market == "US" else IN_UNIVERSE
            all_movers = []
            with ThreadPoolExecutor(max_workers=20) as pool:
                futures = {pool.submit(_fetch_mover, sym): sym for sym in universe}
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        all_movers.append(result)
            gainers = sorted([m for m in all_movers if m["change_pct"] >= 0], key=lambda x: x["change_pct"], reverse=True)[:10]
            losers  = sorted([m for m in all_movers if m["change_pct"] < 0],  key=lambda x: x["change_pct"])[:10]

        response = {"market": market, "gainers": gainers, "losers": losers, "movers": gainers + losers}
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
