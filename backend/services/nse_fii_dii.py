"""
NSE FII/DII daily flow fetcher.

NSE publishes daily FII and DII buy/sell values (in Crores) via:
  /api/fiidiiTradeReact

This replaces the MFI proxy in quality_factors.py with actual institutional
flow data. Tracks:
  - Today's net flow (positive = net buying, negative = net selling)
  - 5-day cumulative flow to identify sustained trends
  - Alignment signal (both FII + DII buying = strong conviction)

Cache TTL: 30 minutes (published once per day after market close,
but we want to catch intraday updates if NSE publishes them).
"""

import logging
import time
import threading
from datetime import datetime

import requests

log = logging.getLogger(__name__)

_cache: tuple[float, dict] | None = None
_cache_lock = threading.Lock()
_TTL = 30 * 60  # 30 min

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
    global _session_init
    with _session_lock:
        if not _session_init:
            try:
                _SESSION.get("https://www.nseindia.com/", timeout=10)
                _session_init = True
            except Exception as e:
                log.warning("NSE FII/DII session init failed: %s", e)


def get_fii_dii_flow() -> dict:
    """
    Return today's FII and DII net buy/sell in Crores.
    Also returns a composite signal and 5-day totals when available.

    Returns:
        {
          "fii_net_cr": float,      # positive = net buying, negative = net selling
          "dii_net_cr": float,
          "fii_5d_net_cr": float,   # rolling 5-day sum
          "dii_5d_net_cr": float,
          "signal": "BULLISH" | "BEARISH" | "NEUTRAL",
          "score": 0-100,           # 50 = neutral
          "reasons": [...],
          "date": "DD-Mon-YYYY",
          "available": bool,
        }
    """
    with _cache_lock:
        if _cache and (time.time() - _cache[0]) < _TTL:
            return _cache[1]

    result = _fetch_flow()

    with _cache_lock:
        global _cache
        _cache = (time.time(), result)

    return result


def _fetch_flow() -> dict:
    base = {"available": False, "signal": "NEUTRAL", "score": 50, "reasons": []}
    try:
        _ensure_session()
        resp = _SESSION.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=12)
        if resp.status_code != 200:
            return base

        rows = resp.json()
        if not rows:
            return base

        fii_today = dii_today = None
        fii_5d = dii_5d = 0.0
        date_str = ""
        count = 0

        for row in rows:
            cat = row.get("category", "").upper()
            try:
                net = float(row.get("netValue", 0) or 0)
            except (ValueError, TypeError):
                net = 0.0

            if "FII" in cat or "FPI" in cat:
                if fii_today is None:
                    fii_today = net
                    date_str = row.get("date", "")
                fii_5d += net
            elif "DII" in cat:
                if dii_today is None:
                    dii_today = net
                dii_5d += net
            count += 1
            if count >= 10:  # roughly 5 trading days × 2 categories
                break

        if fii_today is None or dii_today is None:
            return base

        # Score: each side contributes up to ±25 pts from neutral (50)
        # FII flow: ±2000 Cr/day is "significant"; scale accordingly
        fii_score = max(-25, min(25, fii_today / 2000 * 25))
        dii_score = max(-25, min(25, dii_today / 2000 * 25))
        raw_score = 50 + fii_score + dii_score
        score = round(max(0, min(100, raw_score)))

        reasons = []
        _add_flow_reason(reasons, "FII", fii_today, fii_5d)
        _add_flow_reason(reasons, "DII", dii_today, dii_5d)

        # Alignment bonus/warning
        if fii_today > 500 and dii_today > 500:
            reasons.append("Both FII and DII net buyers today — strong institutional conviction")
        elif fii_today < -500 and dii_today < -500:
            reasons.append("Both FII and DII net sellers today — institutional de-risking")

        if score >= 60:
            signal = "BULLISH"
        elif score <= 40:
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"

        return {
            "available": True,
            "fii_net_cr": round(fii_today, 2),
            "dii_net_cr": round(dii_today, 2),
            "fii_5d_net_cr": round(fii_5d, 2),
            "dii_5d_net_cr": round(dii_5d, 2),
            "signal": signal,
            "score": score,
            "reasons": reasons,
            "date": date_str,
        }

    except Exception as e:
        log.warning("NSE FII/DII fetch failed: %s", e)
        return base


def _add_flow_reason(reasons: list, label: str, today: float, five_day: float):
    if abs(today) < 100:
        return  # noise — don't clutter reasoning
    direction = "bought" if today > 0 else "sold"
    amt = abs(today)
    reasons.append(f"{label} net {direction} ₹{amt:.0f} Cr today (5D net: ₹{five_day:+.0f} Cr)")
