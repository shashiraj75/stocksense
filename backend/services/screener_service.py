import time
import datetime
import yfinance as yf
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from services.heatmap_service import INDIA_SECTORS, US_SECTORS


def _is_market_open(market: str) -> bool:
    if market == "IN":
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
        if now.weekday() >= 5:
            return False
        return now.replace(hour=9, minute=15, second=0, microsecond=0) <= now <= now.replace(hour=15, minute=30, second=0, microsecond=0)
    elif market == "US":
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-4)))
        if now.weekday() >= 5:
            return False
        return now.replace(hour=9, minute=30, second=0, microsecond=0) <= now <= now.replace(hour=16, minute=0, second=0, microsecond=0)
    return False


def _universe_from_sectors(sectors: dict, suffix: str = "") -> list[str]:
    seen: set[str] = set()
    result = []
    for stocks in sectors.values():
        for s in stocks:
            key = s + suffix
            if key not in seen:
                seen.add(key)
                result.append(key)
    return result


IN_UNIVERSE = _universe_from_sectors(INDIA_SECTORS, ".NS")
US_UNIVERSE = _universe_from_sectors(US_SECTORS)

_movers_cache: dict[str, tuple[float, dict]] = {}
_TTL_OPEN   = 120   # 2 min when live
_TTL_CLOSED = 300   # 5 min when closed


def _fetch_one(sym: str) -> dict | None:
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


def _quotes_to_movers(quotes: list) -> list:
    movers = []
    for q in quotes:
        sym = q.get("symbol", "")
        price = q.get("regularMarketPrice")
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


MIN_MCAP_IN = 1_000_000_000  # ~100 Cr INR


def _live_gainers_losers_in() -> tuple[list, list]:
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
    gainers = _quotes_to_movers(yf.screen(gainers_q, sortField="percentchange", sortAsc=False, count=25).get("quotes", []))
    losers  = _quotes_to_movers(yf.screen(losers_q,  sortField="percentchange", sortAsc=True,  count=25).get("quotes", []))
    return gainers[:10], losers[:10]


def _live_gainers_losers_us() -> tuple[list, list]:
    gainers = _quotes_to_movers(yf.screen("day_gainers", count=25).get("quotes", []))
    losers  = _quotes_to_movers(yf.screen("day_losers",  count=25).get("quotes", []))
    return gainers[:10], losers[:10]


def _closed_gainers_losers(universe: list[str]) -> tuple[list, list]:
    all_movers = []
    with ThreadPoolExecutor(max_workers=30) as pool:
        futures = {pool.submit(_fetch_one, sym): sym for sym in universe}
        for future in as_completed(futures):
            result = future.result()
            if result:
                all_movers.append(result)
    gainers = sorted([m for m in all_movers if m["change_pct"] > 0],  key=lambda x: x["change_pct"], reverse=True)[:10]
    losers  = sorted([m for m in all_movers if m["change_pct"] <= 0], key=lambda x: x["change_pct"])[:10]
    return gainers, losers


class ScreenerService:
    async def get_top_movers(self, market: str) -> dict:
        cached = _movers_cache.get(market)
        if cached:
            stored_at, data = cached
            ttl = _TTL_OPEN if data.get("market_open") else _TTL_CLOSED
            if (time.time() - stored_at) < ttl:
                return data

        is_open = _is_market_open(market)
        gainers, losers = [], []

        if is_open:
            try:
                if market == "IN":
                    gainers, losers = _live_gainers_losers_in()
                elif market == "US":
                    gainers, losers = _live_gainers_losers_us()
            except Exception:
                pass

        if not gainers and not losers:
            universe = IN_UNIVERSE if market == "IN" else US_UNIVERSE
            gainers, losers = _closed_gainers_losers(universe)

        response = {"market": market, "market_open": is_open, "gainers": gainers, "losers": losers, "movers": gainers + losers}
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
