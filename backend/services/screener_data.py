"""
Screener.in data fetcher for Indian stocks.

Screener.in (https://www.screener.in) is India's most reliable public source for
10-year financial history, Piotroski-style ratios, and compounded growth rates.
yfinance often has stale or missing Indian fundamental data — screener fills these gaps.

Cache TTL: 4 hours — data updates once daily after market close.
"""

import re
import threading
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
CACHE_TTL = 4 * 3600  # 4 hours — screener data is updated daily

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-IN,en;q=0.9",
})


def _parse_number(text: str) -> Optional[float]:
    """Convert screener's formatted numbers ('1,234.56', '12.3%', '-45.6 Cr') to float."""
    if not text:
        return None
    cleaned = re.sub(r"[₹,%\sCr]+", "", text.replace(",", "")).strip()
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def fetch_screener_data(symbol: str) -> dict:
    """
    Fetch key fundamentals for an NSE stock from screener.in.
    Returns a dict with available fields; missing fields are omitted.
    Cached for 4 hours per symbol.
    """
    sym = symbol.upper().strip()
    cache_key = sym

    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and (time.time() - cached[0]) < CACHE_TTL:
            return cached[1]

    result: dict = {"symbol": sym, "source": "screener.in", "available": False}

    # Try consolidated view first, fall back to standalone
    for path in (f"/company/{sym}/consolidated/", f"/company/{sym}/"):
        url = f"https://www.screener.in{path}"
        try:
            resp = _SESSION.get(url, timeout=10)
            if resp.status_code == 200 and "company" in resp.url:
                soup = BeautifulSoup(resp.text, "html.parser")
                result = _parse_screener_page(soup, sym)
                result["source_url"] = resp.url
                break
            elif resp.status_code == 404:
                continue
        except Exception as e:
            result["error"] = str(e)
            break

    with _cache_lock:
        _cache[cache_key] = (time.time(), result)

    return result


def _parse_screener_page(soup: BeautifulSoup, symbol: str) -> dict:
    """Extract key ratios and growth metrics from a screener.in company page."""
    data: dict = {"symbol": symbol, "source": "screener.in", "available": True}

    # ── Top ratios (the pill-box numbers at the top of every screener page) ──
    ratios_section = soup.find("ul", id="top-ratios")
    if ratios_section:
        for li in ratios_section.find_all("li"):
            name_tag = li.find("span", class_="name")
            value_tag = li.find("span", class_="nowrap")
            if not name_tag or not value_tag:
                continue
            name = name_tag.get_text(strip=True).lower()
            raw = value_tag.get_text(strip=True)
            val = _parse_number(raw)
            if val is None:
                continue
            if "market cap" in name:
                data["market_cap_cr"] = val
            elif "current price" in name:
                data["current_price"] = val
            elif "high / low" in name or "52 week" in name:
                pass  # skip, already in yfinance
            elif "stock p/e" in name or "p/e" == name:
                data["pe_ratio"] = val
            elif "book value" in name:
                data["book_value"] = val
            elif "dividend yield" in name:
                data["dividend_yield_pct"] = val
            elif "roce" in name:
                data["roce_pct"] = val
            elif "roe" in name:
                data["roe_pct"] = val
            elif "face value" in name:
                data["face_value"] = val

    # ── Compounded growth rates table ─────────────────────────────────────────
    # Screener shows: Sales/Profit/Stock price growth for 10Y/5Y/3Y/TTM
    growth_section = soup.find("section", id="growth")
    if not growth_section:
        # Try by heading text
        for section in soup.find_all("section"):
            h2 = section.find("h2")
            if h2 and "compounded" in h2.get_text(strip=True).lower():
                growth_section = section
                break

    if growth_section:
        for table in growth_section.find_all("table"):
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                label = cells[0].get_text(strip=True).lower()
                if "sales growth" in label:
                    # columns: 10Y / 5Y / 3Y / TTM
                    vals = [_parse_number(c.get_text(strip=True)) for c in cells[1:]]
                    data["sales_growth_3y_pct"]  = vals[2] if len(vals) > 2 else None
                    data["sales_growth_5y_pct"]  = vals[1] if len(vals) > 1 else None
                    data["sales_growth_ttm_pct"] = vals[-1] if vals else None
                elif "profit growth" in label:
                    vals = [_parse_number(c.get_text(strip=True)) for c in cells[1:]]
                    data["profit_growth_3y_pct"]  = vals[2] if len(vals) > 2 else None
                    data["profit_growth_5y_pct"]  = vals[1] if len(vals) > 1 else None
                    data["profit_growth_ttm_pct"] = vals[-1] if vals else None
                elif "stock price cagr" in label or "price cagr" in label:
                    vals = [_parse_number(c.get_text(strip=True)) for c in cells[1:]]
                    data["price_cagr_5y_pct"] = vals[1] if len(vals) > 1 else None

    # ── Shareholding pattern (promoter, FII, DII) ────────────────────────────
    shareholding_section = soup.find("section", id="shareholding")
    if shareholding_section:
        table = shareholding_section.find("table")
        if table:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                label = cells[0].get_text(strip=True).lower()
                latest = _parse_number(cells[-1].get_text(strip=True))
                if latest is None:
                    continue
                if "promoters" in label:
                    data["promoter_holding_pct"] = latest
                elif "fii" in label or "foreign" in label:
                    data["fii_holding_pct"] = latest
                elif "dii" in label or "domestic inst" in label:
                    data["dii_holding_pct"] = latest
                elif "public" in label:
                    data["public_holding_pct"] = latest

    # ── Quarterly results — extract latest quarter revenue + PAT ─────────────
    quarterly_section = soup.find("section", id="quarters")
    if quarterly_section:
        table = quarterly_section.find("table")
        if table:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                label = cells[0].get_text(strip=True).lower()
                latest = _parse_number(cells[-1].get_text(strip=True))
                if "sales" in label or "revenue" in label:
                    data["latest_quarter_revenue_cr"] = latest
                elif "net profit" in label or "pat" in label:
                    data["latest_quarter_pat_cr"] = latest

    return data


def augment_info_with_screener(info: dict, symbol: str) -> dict:
    """
    Blend screener.in data into the yfinance `info` dict for missing fields.
    Returns enriched info — original fields preserved, gaps filled from screener.
    """
    try:
        screener = fetch_screener_data(symbol)
        if not screener.get("available"):
            return info

        enriched = dict(info)

        # Fill ROE if missing from yfinance
        if not enriched.get("returnOnEquity") and screener.get("roe_pct") is not None:
            enriched["returnOnEquity"] = screener["roe_pct"] / 100

        # Fill ROCE — yfinance doesn't provide this; add as custom field
        if screener.get("roce_pct") is not None:
            enriched["returnOnCapitalEmployed"] = screener["roce_pct"] / 100

        # Fill revenue growth from 3Y CAGR if yfinance is missing
        if not enriched.get("revenueGrowth") and screener.get("sales_growth_ttm_pct") is not None:
            enriched["revenueGrowth"] = screener["sales_growth_ttm_pct"] / 100

        # Fill earnings growth
        if not enriched.get("earningsGrowth") and screener.get("profit_growth_ttm_pct") is not None:
            enriched["earningsGrowth"] = screener["profit_growth_ttm_pct"] / 100

        # P/E from screener (more current for Indian stocks)
        if not enriched.get("trailingPE") and screener.get("pe_ratio") is not None:
            enriched["trailingPE"] = screener["pe_ratio"]

        # Promoter holding — yfinance calls this heldPercentInsiders but is unreliable for India
        if screener.get("promoter_holding_pct") is not None:
            enriched["promoterHolding"] = screener["promoter_holding_pct"] / 100
            # Override insiders if screener has a more reliable figure
            if not enriched.get("heldPercentInsiders"):
                enriched["heldPercentInsiders"] = screener["promoter_holding_pct"] / 100

        # FII / DII as institutional ownership proxy
        fii = screener.get("fii_holding_pct", 0) or 0
        dii = screener.get("dii_holding_pct", 0) or 0
        if (fii + dii) > 0 and not enriched.get("heldPercentInstitutions"):
            enriched["heldPercentInstitutions"] = (fii + dii) / 100

        # Tag enrichment metadata
        enriched["_screener_available"] = True
        enriched["_screener_data"] = {
            "promoter_holding_pct": screener.get("promoter_holding_pct"),
            "fii_holding_pct":      screener.get("fii_holding_pct"),
            "dii_holding_pct":      screener.get("dii_holding_pct"),
            "roce_pct":             screener.get("roce_pct"),
            "sales_growth_3y_pct":  screener.get("sales_growth_3y_pct"),
            "profit_growth_3y_pct": screener.get("profit_growth_3y_pct"),
            "price_cagr_5y_pct":    screener.get("price_cagr_5y_pct"),
        }
        return enriched

    except Exception:
        return info  # always fall back to plain yfinance info on any error
