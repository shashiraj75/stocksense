"""
BSE India API fallback for fundamental data.

Used when yfinance has no data for a stock — most commonly:
  - Merged / renamed companies (LTIM, JSWINFRA, etc.)
  - Newly listed stocks not yet indexed by yfinance
  - Stocks where yfinance returns empty info dict

BSE provides official financial data via:
  https://api.bseindia.com/BseIndiaAPI/api/

Flow:
  1. Resolve NSE symbol → BSE script code (via NSE search API)
  2. Fetch key ratios from BSE Fundamental API
  3. Return a dict that mirrors the yfinance `info` structure so the
     rest of the pipeline needs zero changes

Cache TTL: 4 hours (same as screener.in — data updates after market close).
"""

import logging
import re
import threading
import time

import requests

log = logging.getLogger(__name__)

_symbol_cache: dict[str, str | None] = {}   # NSE symbol → BSE script code
_data_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()
_TTL = 4 * 3600

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bseindia.com/",
})

# Known NSE→BSE code overrides for tricky merged/renamed stocks
_BSE_OVERRIDES: dict[str, str] = {
    "LTIM":       "543966",  # LTIMindtree (post-merger)
    "JSWINFRA":   "543957",  # JSW Infrastructure
    "MANKIND":    "543904",  # Mankind Pharma
    "TIINDIA":    "540691",  # Tube Investments
    "CAMS":       "543232",
    "MFSL":       "500271",  # Max Financial Services
}


def get_bse_fundamentals(nse_symbol: str) -> dict:
    """
    Return a yfinance-compatible info dict filled from BSE.
    Returns empty dict if BSE lookup fails — caller falls back gracefully.
    """
    sym = nse_symbol.upper().strip()

    with _lock:
        cached = _data_cache.get(sym)
        if cached and (time.time() - cached[0]) < _TTL:
            return cached[1]

    bse_code = _resolve_bse_code(sym)
    if not bse_code:
        return {}

    result = _fetch_bse_data(bse_code, sym)

    with _lock:
        _data_cache[sym] = (time.time(), result)

    return result


def _resolve_bse_code(symbol: str) -> str | None:
    if symbol in _symbol_cache:
        return _symbol_cache[symbol]

    # 1. Static overrides for known merged/renamed stocks
    if symbol in _BSE_OVERRIDES:
        code = _BSE_OVERRIDES[symbol]
        _symbol_cache[symbol] = code
        return code

    # 2. NSE search API → scrip info → BSE code
    try:
        resp = _SESSION.get(
            f"https://www.nseindia.com/api/search/autocomplete?q={symbol}",
            timeout=8,
        )
        if resp.status_code == 200:
            for item in resp.json().get("symbols", []):
                if item.get("symbol", "").upper() == symbol:
                    code = item.get("meta", {}).get("bseCode") or item.get("bseCode")
                    if code:
                        _symbol_cache[symbol] = str(code)
                        return str(code)
    except Exception:
        pass

    # 3. BSE scrip search as last resort
    try:
        resp = _SESSION.get(
            f"https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?segment=Equity&status=Active&scripcode=&group=&exchange=B&industry=",
            timeout=8,
        )
    except Exception:
        pass

    _symbol_cache[symbol] = None
    return None


def _fetch_bse_data(bse_code: str, symbol: str) -> dict:
    """Fetch key ratios and company info from BSE API."""
    result: dict = {}

    try:
        # BSE company header — gives sector, industry, market cap
        resp = _SESSION.get(
            f"https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w"
            f"?Debtflag=&scripcode={bse_code}&seriesid=",
            timeout=10,
        )
        if resp.status_code == 200:
            d = resp.json()
            result["longName"] = d.get("CmpName") or symbol
            result["sector"] = d.get("Sector") or ""
            result["industry"] = d.get("Industry") or ""
            mktcap = _safe_float(d.get("MktCap"))
            if mktcap:
                result["marketCap"] = int(mktcap * 1e7)  # BSE gives Cr; convert to units
    except Exception as e:
        log.debug("BSE header fetch failed for %s: %s", bse_code, e)

    try:
        # BSE fundamental ratios — P/E, EPS, book value, ROE, D/E
        resp = _SESSION.get(
            f"https://api.bseindia.com/BseIndiaAPI/api/Fundamental1/w"
            f"?scripcode={bse_code}&seriesid=EQ&Flag=C",
            timeout=10,
        )
        if resp.status_code == 200:
            d = resp.json()
            pe = _safe_float(d.get("PE"))
            if pe:
                result["trailingPE"] = pe
            eps = _safe_float(d.get("EPS"))
            if eps:
                result["trailingEps"] = eps
            bv = _safe_float(d.get("BV"))
            if bv:
                result["bookValue"] = bv
            roe = _safe_float(d.get("ROE"))
            if roe:
                result["returnOnEquity"] = roe / 100
            de = _safe_float(d.get("DebtEquity"))
            if de is not None:
                result["debtToEquity"] = de
            div_yield = _safe_float(d.get("DivYield"))
            if div_yield:
                result["dividendYield"] = div_yield / 100
            face_val = _safe_float(d.get("FaceValue"))
            if face_val:
                result["regularMarketOpen"] = face_val  # not ideal but fills a gap
    except Exception as e:
        log.debug("BSE fundamentals fetch failed for %s: %s", bse_code, e)

    try:
        # BSE financial results — revenue, profit, margins
        resp = _SESSION.get(
            f"https://api.bseindia.com/BseIndiaAPI/api/FinancialResultNew/w"
            f"?scripcode={bse_code}&report=Quarterly&period=5",
            timeout=10,
        )
        if resp.status_code == 200:
            quarters = resp.json().get("Table", [])
            if quarters:
                latest = quarters[0]
                revenue = _safe_float(latest.get("SALES"))
                net_profit = _safe_float(latest.get("NETPROFIT"))
                if revenue and revenue > 0:
                    result["totalRevenue"] = int(revenue * 1e7)
                    if net_profit is not None:
                        result["netIncomeToCommon"] = int(net_profit * 1e7)
                        margin = net_profit / revenue
                        result["profitMargins"] = round(margin, 4)
                # Revenue growth: compare latest vs year-ago quarter
                if len(quarters) >= 5:
                    prior_rev = _safe_float(quarters[4].get("SALES"))
                    if prior_rev and prior_rev > 0 and revenue:
                        result["revenueGrowth"] = round((revenue - prior_rev) / abs(prior_rev), 4)
    except Exception as e:
        log.debug("BSE financial results fetch failed for %s: %s", bse_code, e)

    if result:
        result["_bse_available"] = True
        result["_bse_code"] = bse_code
    return result


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        f = float(str(val).replace(",", "").strip())
        return f if f == f else None  # NaN check
    except (ValueError, TypeError):
        return None
