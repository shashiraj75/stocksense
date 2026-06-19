import time
import logging
import datetime
import yfinance as yf
from typing import Optional

log = logging.getLogger(__name__)

from services.heatmap_service import INDIA_SECTORS, US_SECTORS
from services.stock_universe import IN_STOCKS, US_STOCKS
from services import finnhub_client
from services import nse_client

# Build symbol→name lookup from universe
_NAME_MAP: dict[str, str] = {}
for _sym, _name in (IN_STOCKS + US_STOCKS):
    _NAME_MAP[_sym.upper()] = _name

# Curated Nifty 50 list for Finnhub-based screener (reliable from cloud IPs)
NIFTY50_SYMBOLS = [
    "RELIANCE","TCS","HDFCBANK","BHARTIARTL","ICICIBANK","INFOSYS","SBIN",
    "HINDUNILVR","ITC","LT","KOTAKBANK","AXISBANK","MARUTI","TITAN","BAJFINANCE",
    "ASIANPAINT","NESTLEIND","HCLTECH","SUNPHARMA","WIPRO","ULTRACEMCO","BAJAJFINSV",
    "ONGC","NTPC","POWERGRID","COALINDIA","TECHM","TATAMOTORS","ADANIENT","JSWSTEEL",
    "TATASTEEL","INDUSINDBK","CIPLA","BPCL","GRASIM","EICHERMOT","DRREDDY",
    "BRITANNIA","APOLLOHOSP","DIVISLAB","HDFCLIFE","SBILIFE","BAJAJ-AUTO","ADANIPORTS",
    "TRENT","SHRIRAMFIN","BEL","ETERNAL","HEROMOTOCO","M&M",
]


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


def _bulk_quotes(tickers: list[str]) -> dict[str, dict]:
    """Bulk download last 5 days — ensures ≥2 settled trading rows even when today is all-NaN."""
    results = {}
    try:
        df = yf.download(tickers, period="5d", interval="1d", progress=False)
        if df.empty:
            return results
        if hasattr(df.columns, "levels"):
            close = df["Close"] if "Close" in df.columns.get_level_values(0) else None
        else:
            close = df[["Close"]] if "Close" in df.columns else None
        if close is None:
            return results
        close = close.dropna(how="all")
        if len(close) < 2:
            return results
        prev_row  = close.iloc[-2]
        today_row = close.iloc[-1]
        for ticker in tickers:
            prev  = prev_row.get(ticker)
            today = today_row.get(ticker)
            if prev is not None and today is not None and float(prev) > 0:
                sym = ticker.replace(".NS", "").replace(".BO", "")
                results[ticker] = {
                    "symbol": sym,
                    "price": round(float(today), 2),
                    "change_pct": round((float(today) - float(prev)) / float(prev) * 100, 2),
                    "name": _NAME_MAP.get(sym.upper(), ""),
                }
    except Exception as e:
        err = str(e).lower()
        if "crumb" in err or "401" in err or "unauthorized" in err:
            try:
                import yfinance as yf
                yf.utils.get_crumb(force=True) if hasattr(yf.utils, "get_crumb") else None
                log.info("bulk_quotes: crumb refreshed after 401")
            except Exception:
                pass
        log.warning("bulk_quotes failed: %s", e)
    return results


def _quotes_to_movers(quotes: list) -> list:
    movers = []
    for q in quotes:
        sym = q.get("symbol", "")
        price = q.get("regularMarketPrice")
        change_pct = q.get("regularMarketChangePercent")
        clean_sym = sym.replace(".NS", "").replace(".BO", "")
        name = q.get("shortName") or q.get("longName") or _NAME_MAP.get(clean_sym.upper(), "")
        if sym and price is not None and change_pct is not None:
            movers.append({
                "symbol": clean_sym,
                "price": round(float(price), 2),
                "change_pct": round(float(change_pct), 2),
                "name": name,
            })
    return movers


def _finnhub_gainers_losers(symbols: list[str], market: str) -> tuple[list, list]:
    """
    Fetch quotes via Finnhub (reliable from cloud IPs, no crumb issues).
    Returns top 10 gainers and top 10 losers sorted by change_pct.
    """
    movers = []
    # Finnhub free tier: 60 req/min — batch with small delay every 10
    for i, sym in enumerate(symbols):
        q = finnhub_client.get_quote(sym, market)
        if q and q.get("price") and q.get("change_pct") is not None:
            movers.append({
                "symbol": sym,
                "price": q["price"],
                "change_pct": q["change_pct"],
                "name": _NAME_MAP.get(sym.upper(), ""),
            })
        if (i + 1) % 10 == 0:
            time.sleep(1)  # respect 60 req/min rate limit

    gainers = sorted([m for m in movers if m["change_pct"] > 0],
                     key=lambda x: x["change_pct"], reverse=True)[:10]
    losers  = sorted([m for m in movers if m["change_pct"] <= 0],
                     key=lambda x: x["change_pct"])[:10]
    return gainers, losers


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
    """Rank universe by last session % change using a single bulk download."""
    quotes = _bulk_quotes(universe)
    all_movers = list(quotes.values())
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

        import asyncio
        loop = asyncio.get_event_loop()
        is_open = _is_market_open(market)
        gainers, losers = [], []

        # PRIMARY for India: NSE official API — one call, all 50 stocks, no rate limits
        if market == "IN":
            try:
                def _nse_movers():
                    stocks = nse_client.get_nifty100_quotes()
                    if not stocks:
                        stocks = nse_client.get_nifty50_quotes()
                    g = sorted([s for s in stocks if s["change_pct"] > 0],
                               key=lambda x: x["change_pct"], reverse=True)[:10]
                    l = sorted([s for s in stocks if s["change_pct"] <= 0],
                               key=lambda x: x["change_pct"])[:10]
                    # normalise to expected screener shape
                    def _norm(s):
                        return {
                            "symbol":     s["symbol"],
                            "price":      s["price"],
                            "change_pct": s["change_pct"],
                            "name":       s.get("company_name") or _NAME_MAP.get(s["symbol"].upper(), ""),
                        }
                    return [_norm(x) for x in g], [_norm(x) for x in l]

                gainers, losers = await asyncio.wait_for(
                    loop.run_in_executor(None, _nse_movers),
                    timeout=15.0,
                )
                log.info("screener: NSE API returned %d gainers, %d losers", len(gainers), len(losers))
            except Exception as e:
                log.warning("NSE movers failed: %s", e)

        # PRIMARY for US / fallback for India: Finnhub
        if not gainers and not losers and finnhub_client.FINNHUB_KEY:
            try:
                symbols = NIFTY50_SYMBOLS if market == "IN" else list({
                    s for stocks in US_SECTORS.values() for s in stocks
                })[:50]
                gainers, losers = await asyncio.wait_for(
                    loop.run_in_executor(None, _finnhub_gainers_losers, symbols, market),
                    timeout=30.0,
                )
                log.info("screener: Finnhub returned %d gainers, %d losers for %s",
                         len(gainers), len(losers), market)
            except Exception as e:
                log.warning("Finnhub movers failed for %s: %s", market, e)

        # Fallback: yfinance live screen (market open only)
        if not gainers and not losers and is_open:
            try:
                if market == "IN":
                    gainers, losers = await asyncio.wait_for(
                        loop.run_in_executor(None, _live_gainers_losers_in),
                        timeout=15.0,
                    )
                elif market == "US":
                    gainers, losers = await asyncio.wait_for(
                        loop.run_in_executor(None, _live_gainers_losers_us),
                        timeout=15.0,
                    )
            except Exception as e:
                log.warning("yf live movers failed for %s: %s", market, e)

        # Last resort: yfinance bulk download
        if not gainers and not losers:
            try:
                universe = IN_UNIVERSE if market == "IN" else US_UNIVERSE
                gainers, losers = await asyncio.wait_for(
                    loop.run_in_executor(None, _closed_gainers_losers, universe),
                    timeout=25.0,
                )
            except Exception as e:
                log.warning("yf bulk movers failed for %s: %s", market, e)

        response = {
            "market": market,
            "market_open": is_open,
            "gainers": gainers,
            "losers": losers,
            "movers": gainers + losers,
            "error": "Data temporarily unavailable" if not gainers and not losers else None,
        }
        if gainers or losers:
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
