"""
NSE bhavcopy — official end-of-day closing prices, used as a last-resort
price correction for India Daily Picks (Product Integrity #012).

Root cause this addresses: yfinance's per-symbol price data can lag one
NSE trading session behind at generation time (Product Integrity #004,
#011). NSE publishes its own official daily closing-price archive
("bhavcopy") for every listed security, independent of Yahoo's pipeline:

  https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{DDMMYYYY}.csv

Usage contract:
  - Only ever supplies a single day's official CLOSE_PRICE for one symbol.
    It does NOT provide OHLC history — callers must keep using their
    existing history source (yfinance) for technical indicators; this
    module corrects only the reference price used for price_reference and
    downstream trade-level calculations.
  - Returns None on any failure (network, parse, 404, symbol not found) —
    callers must treat that identically to "no correction available" and
    fall back to their existing behavior. This module never raises.

Caching: the whole day's file is fetched and parsed once per session_date,
then reused for every symbol lookup against that date (a Daily Picks run
checks ~400 symbols — without this, each would trigger its own HTTP
request). Successful parses are cached for 24h (a published session's
bhavcopy is immutable). Failures are cached for a short 5 min window only
— long enough to avoid hammering NSE across one generation run's symbol
loop, short enough to let the next run retry if the failure was transient.
"""

import csv
import io
import logging
import threading
import time
from datetime import date

import requests

log = logging.getLogger(__name__)

_BHAVCOPY_URL_TEMPLATE = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"
_TIMEOUT = 12
_MAX_ATTEMPTS = 2
_TTL_SUCCESS = 24 * 3600
_TTL_FAILURE = 5 * 60

_lock = threading.Lock()
_cache: dict[date, tuple[float, dict[str, float] | None]] = {}

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.nseindia.com/",
})


def _fetch_and_parse(session_date: date) -> dict[str, float] | None:
    url = _BHAVCOPY_URL_TEMPLATE.format(ddmmyyyy=session_date.strftime("%d%m%Y"))
    for attempt in range(_MAX_ATTEMPTS):
        try:
            resp = _SESSION.get(url, timeout=_TIMEOUT)
            if resp.status_code == 404:
                # Not published for this date (holiday/weekend/too-early) — not retryable.
                log.info("[nse_bhavcopy] no bhavcopy published for %s (404)", session_date)
                return None
            resp.raise_for_status()
            return _parse_csv(resp.text)
        except Exception as e:
            if attempt < _MAX_ATTEMPTS - 1:
                log.info("[nse_bhavcopy] fetch attempt %d failed for %s: %s", attempt, session_date, e)
                time.sleep(1 + attempt)
            else:
                log.warning("[nse_bhavcopy] fetch failed for %s after %d attempts: %s",
                            session_date, _MAX_ATTEMPTS, e)
                return None
    return None


def _parse_csv(raw_text: str) -> dict[str, float]:
    closes: dict[str, float] = {}
    reader = csv.DictReader(io.StringIO(raw_text))
    for row in reader:
        row = {(k or "").strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
        symbol = row.get("SYMBOL")
        series = row.get("SERIES")
        close = row.get("CLOSE_PRICE")
        if not symbol or not close:
            continue
        if series and series != "EQ":
            # Only plain equity — excludes gilts, SME/ETF series, etc. that
            # share the same file but aren't relevant to Daily Picks symbols.
            continue
        try:
            closes[symbol.upper()] = float(close)
        except ValueError:
            continue
    return closes


def get_bhavcopy_close(symbol: str, session_date: date) -> float | None:
    """Official NSE closing price for `symbol` on `session_date`, or None
    if unavailable for any reason. Never raises."""
    with _lock:
        cached = _cache.get(session_date)
        ttl = _TTL_SUCCESS if (cached and cached[1] is not None) else _TTL_FAILURE
        if cached is not None and (time.time() - cached[0]) < ttl:
            closes = cached[1]
        else:
            closes = _fetch_and_parse(session_date)
            _cache[session_date] = (time.time(), closes)
    if closes is None:
        return None
    return closes.get(symbol.upper())
