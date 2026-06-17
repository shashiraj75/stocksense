"""
NSE pledge data fetcher.

NSE publishes promoter pledge disclosures quarterly via:
  /api/corporate-pledgedata?symbol={NSE_SYMBOL}

Returns the latest % of promoter shares pledged — the key risk signal.
High pledge = forced selling risk if stock price drops.
Cache TTL: 24 hours (data is quarterly disclosure).
"""

import logging
import time
import threading

import requests

log = logging.getLogger(__name__)

_cache: dict[str, tuple[float, float | None]] = {}
_cache_lock = threading.Lock()
_TTL = 24 * 3600  # quarterly data; 24h cache is more than fresh enough

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nseindia.com/",
    "Accept-Language": "en-IN,en;q=0.9",
})
_session_init = False
_session_lock = threading.Lock()


def _ensure_session():
    """Warm up the NSE session cookie (required for API calls)."""
    global _session_init
    with _session_lock:
        if not _session_init:
            try:
                _SESSION.get("https://www.nseindia.com/", timeout=10)
                _session_init = True
            except Exception as e:
                log.warning("NSE session init failed: %s", e)


def get_promoter_pledge_pct(symbol: str) -> float | None:
    """
    Return % of promoter shares pledged for an NSE-listed stock.
    Returns None if data unavailable or fetch fails.
    Cached for 24 hours.
    """
    sym = symbol.upper().strip()

    with _cache_lock:
        cached = _cache.get(sym)
        if cached and (time.time() - cached[0]) < _TTL:
            return cached[1]

    result = _fetch_pledge(sym)

    with _cache_lock:
        _cache[sym] = (time.time(), result)

    return result


def _fetch_pledge(symbol: str) -> float | None:
    try:
        _ensure_session()
        url = f"https://www.nseindia.com/api/corporate-pledgedata?symbol={symbol}"
        resp = _SESSION.get(url, timeout=12)
        if resp.status_code != 200:
            return None

        data = resp.json()
        rows = data.get("data") or []
        if not rows:
            return None

        # Take most recent row (first entry = latest quarter)
        latest = rows[0]

        # percPromoterShares = % of promoter's own holding that is pledged
        # This is the key risk metric (not % of total shares)
        raw = latest.get("percPromoterShares", "").strip()
        if raw:
            try:
                return round(float(raw), 2)
            except (ValueError, TypeError):
                pass

        # Fallback: compute from share counts if percPromoterShares missing
        pledged = float(latest.get("numSharesPledged") or 0)
        promoter_total = float(latest.get("totPromoterHolding") or 0)
        if promoter_total > 0:
            return round(pledged / promoter_total * 100, 2)

        return None

    except Exception as e:
        log.warning("NSE pledge fetch failed for %s: %s", symbol, e)
        return None
