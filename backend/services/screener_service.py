import math
import time
import logging
import yfinance as yf
from typing import Optional

log = logging.getLogger(__name__)

from services.heatmap_service import INDIA_SECTORS, US_SECTORS
from services.stock_universe import IN_STOCKS, US_STOCKS
from services import finnhub_client
from services import nse_client
from services.market_hours import is_market_open as _is_market_open

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

# Persistent last-known-good cache — never expires, replaced only when fresh data arrives.
# Prevents empty dashboard after market close when all live sources fail.
_last_good_movers: dict[str, dict] = {}

# Compact universe for fallback — just Nifty 50 (.NS) for faster bulk download
_IN_FALLBACK_UNIVERSE = [s + ".NS" for s in NIFTY50_SYMBOLS]


def _bulk_quotes(tickers: list[str]) -> dict[str, dict]:
    """Bulk download last 5 days — ensures ≥2 settled trading rows even when today is all-NaN."""
    results = {}
    try:
        df = yf.download(tickers, period="5d", interval="1d", progress=False, auto_adjust=True)
        if df.empty:
            return results
        # Handle both multi-ticker (MultiIndex) and single-ticker DataFrames
        if hasattr(df.columns, "levels"):
            lvl0 = df.columns.get_level_values(0)
            close = df["Close"] if "Close" in lvl0 else None
        else:
            close = df[["Close"]] if "Close" in df.columns else None
        if close is None:
            return results
        # Drop rows where ALL tickers have NaN (e.g. weekends)
        close = close.dropna(how="all")
        if len(close) < 2:
            return results
        prev_row  = close.iloc[-2]
        today_row = close.iloc[-1]
        # If today's row is still mostly NaN (intra-day, not settled), use [-3] vs [-2]
        today_valid = today_row.dropna()
        if len(today_valid) < len(tickers) * 0.5 and len(close) >= 3:
            prev_row  = close.iloc[-3]
            today_row = close.iloc[-2]
        for ticker in tickers:
            prev  = prev_row.get(ticker)
            today = today_row.get(ticker)
            try:
                prev_f  = float(prev)  if prev  is not None else None
                today_f = float(today) if today is not None else None
            except (TypeError, ValueError):
                continue
            if prev_f and today_f and not math.isnan(prev_f) and not math.isnan(today_f) and prev_f > 0:
                sym = ticker.replace(".NS", "").replace(".BO", "")
                results[ticker] = {
                    "symbol": sym,
                    "price": round(today_f, 2),
                    "change_pct": round((today_f - prev_f) / prev_f * 100, 2),
                    "name": _NAME_MAP.get(sym.upper(), ""),
                }
    except Exception as e:
        err = str(e).lower()
        if "crumb" in err or "401" in err or "unauthorized" in err:
            try:
                if hasattr(yf.utils, "get_crumb"):
                    yf.utils.get_crumb(force=True)
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


def refresh_us_movers_cache() -> bool:
    """
    Full-universe US large-cap movers scan, meant to run on its own background
    schedule (see _us_movers_refresh_loop in main.py) — NOT on the live request
    path. The synchronous request-time fallback for US (Finnhub /quote calls)
    can only check ~50 symbols within an acceptable timeout because Finnhub's
    free tier caps at 60 req/min with no bulk-quote endpoint; the curated US
    large-cap universe (US_SECTORS) has 340+ unique symbols, so most of it was
    never actually being checked — that's why Top Gainers/Losers regularly
    showed far fewer than 10 names each. A single yf.download() bulk call
    covers the whole universe at once with no per-symbol rate limit, same
    mechanism already used as the last-resort fallback below — this just runs
    it proactively so the cache is warm before any user ever asks.
    Returns True if it found anything and updated the cache.
    """
    gainers, losers = _closed_gainers_losers(US_UNIVERSE)
    if not gainers and not losers:
        return False
    response = {
        "market": "US",
        "market_open": _is_market_open("US"),
        "gainers": gainers,
        "losers": losers,
        "movers": gainers + losers,
        "error": None,
    }
    _movers_cache["US"] = (time.time(), response)
    _last_good_movers["US"] = response
    try:
        import os
        if os.getenv("USE_POSTGRES") == "1":
            from services.postgres_store import save_market_cache
            save_market_cache("movers_US", response)
    except Exception:
        pass
    return True


class ScreenerService:
    async def get_top_movers(self, market: str) -> dict:
        cached = _movers_cache.get(market)
        if cached:
            stored_at, data = cached
            ttl = _TTL_OPEN if data.get("market_open") else _TTL_CLOSED
            if (time.time() - stored_at) < ttl:
                return data

        import asyncio
        loop = asyncio.get_running_loop()
        is_open = _is_market_open(market)
        gainers, losers = [], []

        # PRIMARY for India: NSE live-analysis-variations — no cookies needed,
        # works from Render cloud IPs, returns top movers in 2 HTTP calls
        if market == "IN":
            try:
                def _nse_movers():
                    g, l = nse_client.get_gainers_losers("NIFTY")
                    if not g and not l:
                        g, l = nse_client.get_gainers_losers("allSec")
                    def _norm(s):
                        sym = s.get("symbol", "")
                        price = s.get("price")
                        chg   = s.get("change_pct")
                        if not sym or price is None or chg is None:
                            return None
                        return {
                            "symbol":     sym,
                            "price":      price,
                            "change_pct": chg,
                            "name":       s.get("company_name") or _NAME_MAP.get(sym.upper(), ""),
                        }
                    def _safe_norm(items):
                        return [r for x in items if (r := _norm(x)) is not None]
                    return _safe_norm(g), _safe_norm(l)

                gainers, losers = await asyncio.wait_for(
                    loop.run_in_executor(None, _nse_movers),
                    timeout=15.0,
                )
                log.info("screener: NSE live-analysis returned %d gainers, %d losers", len(gainers), len(losers))
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

        # Last resort: yfinance bulk download (use compact Nifty50 list for speed)
        if not gainers and not losers:
            try:
                universe = _IN_FALLBACK_UNIVERSE if market == "IN" else US_UNIVERSE
                gainers, losers = await asyncio.wait_for(
                    loop.run_in_executor(None, _closed_gainers_losers, universe),
                    timeout=30.0,
                )
                log.info("screener: yf bulk returned %d gainers, %d losers for %s",
                         len(gainers), len(losers), market)
            except Exception as e:
                log.warning("yf bulk movers failed for %s: %s", market, e)

        # If still empty, serve last known good data — check memory first, then Postgres
        if not gainers and not losers:
            last_good = _last_good_movers.get(market)
            if not last_good:
                # Try Postgres persistent cache (survives server restarts)
                try:
                    USE_POSTGRES = __import__("os").getenv("USE_POSTGRES") == "1"
                    if USE_POSTGRES:
                        from services.postgres_store import load_market_cache
                        last_good = load_market_cache(f"movers_{market}")
                        if last_good:
                            _last_good_movers[market] = last_good
                except Exception:
                    pass
            if last_good:
                log.info("screener: serving last-known-good movers for %s", market)
                stale = dict(last_good)
                stale["market_open"] = is_open
                stale["stale"] = True
                return stale

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
            _last_good_movers[market] = response
            # Persist to Postgres so it survives server restarts
            try:
                USE_POSTGRES = __import__("os").getenv("USE_POSTGRES") == "1"
                if USE_POSTGRES:
                    from services.postgres_store import save_market_cache
                    save_market_cache(f"movers_{market}", response)
            except Exception:
                pass
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
        import asyncio
        universe = US_UNIVERSE if market == "US" else IN_UNIVERSE

        def _run_filter():
            results = []
            skipped = 0
            for sym in universe:
                try:
                    info = yf.Ticker(sym).info
                    pe   = info.get("trailingPE")
                    roe  = info.get("returnOnEquity")  # yfinance returns this as a fraction (0.15 = 15%)
                    mcap = info.get("marketCap") or 0
                    sec  = info.get("sector", "")
                    passes = True
                    if min_market_cap is not None and mcap < min_market_cap:
                        passes = False
                    if max_pe is not None and pe is not None and pe > max_pe:
                        passes = False
                    # min_roe is documented/expected as a percentage (e.g. 15 for "15%"),
                    # matching the *100-scaled `roe` field returned below — compare on
                    # the same scale instead of against the raw fraction.
                    if min_roe is not None and (roe is None or roe * 100 < min_roe):
                        passes = False
                    if sector and sector.lower() not in sec.lower():
                        passes = False
                    if passes:
                        results.append({
                            "symbol":     sym.replace(".NS", "").replace(".BO", ""),
                            "sector":     sec,
                            "pe":         round(pe, 2) if pe is not None else None,
                            "roe":        round(roe * 100, 2) if roe is not None else None,
                            "market_cap": mcap,
                        })
                except Exception as e:
                    skipped += 1
                    log.debug("screener filter: skipped %s (%s)", sym, e)
            if skipped:
                log.info("screener filter: %d/%d symbols failed to fetch and were skipped", skipped, len(universe))
            return results

        loop = asyncio.get_running_loop()
        results = await asyncio.wait_for(
            loop.run_in_executor(None, _run_filter), timeout=60.0
        )
        return {"market": market, "results": results}
