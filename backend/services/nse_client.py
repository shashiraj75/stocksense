"""
NSE India Official API Client
==============================
Uses NSE's own public JSON APIs — the same endpoints Moneycontrol, Zerodha,
and screener.in consume. One call returns 50 stocks with live prices, change%,
52W high/low, volume, and company names. No rate limits, no crumb issues,
no cloud-IP blocking.

Key endpoints:
  /api/equity-stockIndices?index=NIFTY%2050   — full Nifty 50 in one call
  /api/equity-stockIndices?index=NIFTY%20BANK — full Nifty Bank sector
  /api/quote-equity?symbol=RELIANCE           — single stock live quote

Session: NSE requires the homepage to be hit first to get cookies. The session
is kept alive and refreshed automatically.
"""

import logging
import threading
import time
from typing import Optional
from urllib.parse import quote as url_quote

import requests

log = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en-GB;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
})

_session_lock = threading.Lock()
_session_init_at: float = 0.0
_SESSION_TTL = 30 * 60  # refresh cookies every 30 min


def _ensure_session():
    """Hit NSE homepage to obtain session cookies. Refreshes every 30 min."""
    global _session_init_at
    with _session_lock:
        if (time.time() - _session_init_at) < _SESSION_TTL:
            return
        # Always update timestamp to avoid retry storms on cloud IPs where
        # the homepage may return 403 — callers must degrade gracefully.
        _session_init_at = time.time()
        try:
            r = _SESSION.get("https://www.nseindia.com/", timeout=12)
            if r.status_code == 200:
                log.info("NSE session initialised (cookies: %d)", len(_SESSION.cookies))
            else:
                log.warning("NSE homepage returned %s — API calls may be unauthenticated", r.status_code)
        except Exception as e:
            log.warning("NSE session init failed: %s", e)


# ── Index name map ────────────────────────────────────────────────────────────
# Maps our internal sector names to NSE index slugs
SECTOR_TO_NSE_INDEX: dict[str, str] = {
    "Banking":   "NIFTY BANK",
    "IT":        "NIFTY IT",
    "Pharma":    "NIFTY PHARMA",
    "Auto":      "NIFTY AUTO",
    "FMCG":      "NIFTY FMCG",
    "Metal":     "NIFTY METAL",
    "Realty":    "NIFTY REALTY",
    "Finance":   "NIFTY FINANCIAL SERVICES",
    "Energy":    "NIFTY OIL AND GAS",
    "Telecom":   "NIFTY MEDIA",          # closest available
    "Consumer":  "NIFTY CONSUMER DURABLES",
    "Infra":     "NIFTY INFRASTRUCTURE",
}

_index_cache: dict[str, tuple[float, list]] = {}
_INDEX_TTL = 120  # 2 min — live during market hours


def fetch_index(index_name: str) -> list[dict]:
    """
    Fetch all stocks in an NSE index with live prices.

    Returns list of dicts:
      symbol, company_name, price, prev_close, change, change_pct,
      year_high, year_low, volume
    """
    cached = _index_cache.get(index_name)
    if cached and (time.time() - cached[0]) < _INDEX_TTL:
        return cached[1]

    _ensure_session()
    encoded = url_quote(index_name)
    try:
        r = _SESSION.get(
            f"https://www.nseindia.com/api/equity-stockIndices?index={encoded}",
            timeout=10,
        )
        if r.status_code != 200:
            log.warning("NSE index %s → HTTP %s", index_name, r.status_code)
            return []

        payload = r.json()
        rows = payload.get("data", [])
        # First row is the index itself (e.g., "NIFTY 50") — skip it
        stocks = []
        for row in rows:
            sym = row.get("symbol", "")
            if not sym or row.get("series") == "":
                continue  # skip the index summary row
            price = row.get("lastPrice") or row.get("open")
            prev  = row.get("previousClose")
            if price is None or prev is None:
                continue
            price = float(price)
            prev  = float(prev)
            chg_pct = row.get("pChange")
            if chg_pct is None and prev > 0:
                chg_pct = round((price - prev) / prev * 100, 2)
            stocks.append({
                "symbol":       sym,
                "company_name": (row.get("meta") or {}).get("companyName", ""),
                "price":        round(price, 2),
                "prev_close":   round(prev, 2),
                "change":       round(float(row.get("change") or 0), 2),
                "change_pct":   round(float(chg_pct or 0), 2),
                "year_high":    row.get("yearHigh"),
                "year_low":     row.get("yearLow"),
                "volume":       int(row.get("totalTradedVolume") or 0),
            })

        _index_cache[index_name] = (time.time(), stocks)
        log.info("NSE index %s → %d stocks", index_name, len(stocks))
        return stocks

    except Exception as e:
        log.warning("NSE fetch_index(%s) failed: %s", index_name, e)
        return []


def get_nifty50_quotes() -> list[dict]:
    """Return all Nifty 50 constituents with live price data."""
    return fetch_index("NIFTY 50")


def get_nifty100_quotes() -> list[dict]:
    """Return all Nifty 100 constituents."""
    return fetch_index("NIFTY 100")


_quote_cache: dict[str, tuple[float, dict]] = {}
_QUOTE_TTL = 60  # 1 min


def get_quote(symbol: str) -> Optional[dict]:
    """
    Fetch a single stock's live quote from NSE.
    Uses /api/quote-equity which returns richer data than the index endpoint.
    """
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")
    cached = _quote_cache.get(sym)
    if cached and (time.time() - cached[0]) < _QUOTE_TTL:
        return cached[1]

    _ensure_session()
    try:
        r = _SESSION.get(
            f"https://www.nseindia.com/api/quote-equity?symbol={url_quote(sym)}",
            timeout=8,
        )
        if r.status_code != 200:
            return None

        data = r.json()
        info = data.get("priceInfo", {})
        meta = data.get("info", {})
        ltp  = info.get("lastPrice") or info.get("open")
        prev = info.get("previousClose")
        if ltp is None or prev is None:
            return None

        ltp  = float(ltp)
        prev = float(prev)
        result = {
            "symbol":              sym,
            "market":              "IN",
            "company_name":        meta.get("companyName", ""),
            "price":               round(ltp, 2),
            "prev_close":          round(prev, 2),
            "change":              round(ltp - prev, 2),
            "change_pct":          round((ltp - prev) / prev * 100, 2) if prev else 0,
            "open":                info.get("open"),
            "high":                info.get("intraDayHighLow", {}).get("max"),
            "low":                 info.get("intraDayHighLow", {}).get("min"),
            "fifty_two_week_high": info.get("weekHighLow", {}).get("max"),
            "fifty_two_week_low":  info.get("weekHighLow", {}).get("min"),
            "volume":              int(
                                       (data.get("marketDeptOrderBook", {}) or {})
                                           .get("tradeInfo", {}).get("totalTradedVolume")
                                       or 0
                                   ),
        }
        _quote_cache[sym] = (time.time(), result)
        return result

    except Exception as e:
        log.warning("NSE get_quote(%s) failed: %s", sym, e)
        return None


def get_gainers_losers(index: str = "NIFTY 50") -> tuple[list[dict], list[dict]]:
    """
    Fetch top gainers and losers by pulling the full Nifty 50 / Nifty 100
    index via equity-stockIndices (same endpoint that powers the working heatmap).
    Splits all 50/100 stocks by sign of change_pct to get gainers and losers.

    This is more reliable than live-analysis-variations which only returns
    gainers (not losers) and behaves differently on Render cloud IPs.
    """
    try:
        # Try Nifty 100 first for more coverage, fall back to Nifty 50
        stocks = fetch_index("NIFTY 100")
        if not stocks:
            stocks = fetch_index("NIFTY 50")
        if not stocks:
            return [], []

        gainers = sorted([s for s in stocks if s["change_pct"] > 0],
                         key=lambda x: x["change_pct"], reverse=True)[:10]
        losers  = sorted([s for s in stocks if s["change_pct"] < 0],
                         key=lambda x: x["change_pct"])[:10]

        log.info("NSE gainers_losers (via index): %d total → %d gainers, %d losers",
                 len(stocks), len(gainers), len(losers))
        return gainers, losers

    except Exception as e:
        log.warning("NSE get_gainers_losers failed: %s", e)
        return [], []


def get_sector_changes(sector: str) -> Optional[dict[str, float]]:
    """
    Return {symbol: change_pct} for all stocks in a sector index.
    Returns None if the sector has no mapped NSE index.
    """
    nse_index = SECTOR_TO_NSE_INDEX.get(sector)
    if not nse_index:
        return None
    stocks = fetch_index(nse_index)
    return {s["symbol"]: s["change_pct"] for s in stocks}
