"""
Screener.in data fetcher for Indian stocks.

Screener.in (https://www.screener.in) is India's most reliable public source for
10-year financial history, Piotroski-style ratios, and compounded growth rates.
yfinance often has stale or missing Indian fundamental data — screener fills these gaps.

Authentication: logs in with SCREENER_EMAIL / SCREENER_PASSWORD env vars so that
requests from cloud IPs (Render) are not blocked. Session is refreshed every 6 hours.

Cache TTL: 4 hours — data updates once daily after market close.
"""

import os
import re
import threading
import time
import logging
from typing import Optional

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
CACHE_TTL = 4 * 3600  # 4 hours — screener data is updated daily

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.screener.in/",
})

_login_lock = threading.Lock()
_last_login_at: float = 0.0
_LOGIN_TTL = 6 * 3600  # re-login every 6 hours


def _login() -> bool:
    """
    Log in to screener.in using SCREENER_EMAIL / SCREENER_PASSWORD env vars.
    Sets session cookies so subsequent requests are authenticated.
    Returns True if login succeeded.
    """
    global _last_login_at
    email = os.getenv("SCREENER_EMAIL", "")
    password = os.getenv("SCREENER_PASSWORD", "")
    if not email or not password:
        log.warning("SCREENER_EMAIL / SCREENER_PASSWORD not set — screener.in requests will be unauthenticated")
        return False

    try:
        # 1. GET login page to obtain CSRF token
        resp = _SESSION.get("https://www.screener.in/login/", timeout=10)
        resp.raise_for_status()
        log.info("screener.in login page status=%s url=%s", resp.status_code, resp.url)

        soup = BeautifulSoup(resp.text, "html.parser")
        csrf_input = soup.find("input", {"name": "csrfmiddlewaretoken"})
        if not csrf_input:
            # Log the form fields we actually see to help debug
            form = soup.find("form")
            fields = [i.get("name") for i in form.find_all("input")] if form else []
            log.error("screener.in login: CSRF token not found. Form fields seen: %s", fields)
            return False
        csrf = csrf_input.get("value", "")
        log.info("screener.in CSRF token obtained (len=%d)", len(csrf))

        # 2. POST credentials — try both 'username' and 'email' field names
        # screener.in uses 'username' but accepts email address as the value
        for username_field in ("username", "email"):
            login_resp = _SESSION.post(
                "https://www.screener.in/login/",
                data={
                    username_field:          email,
                    "password":              password,
                    "csrfmiddlewaretoken":   csrf,
                    "next":                  "/",
                },
                headers={"Referer": "https://www.screener.in/login/"},
                timeout=10,
                allow_redirects=True,
            )
            log.info("screener.in POST (field=%s) → status=%s url=%s",
                     username_field, login_resp.status_code, login_resp.url)

            # Successful login redirects away from /login/
            if "/login/" not in login_resp.url and login_resp.status_code == 200:
                _last_login_at = time.time()
                log.info("screener.in login successful (field=%s)", username_field)
                return True

        log.error("screener.in login failed with both username fields — final URL: %s", login_resp.url)
        # Log a snippet of the response to see if there's an error message
        error_div = soup.find(class_=lambda c: c and "error" in c.lower()) if soup else None
        if error_div:
            log.error("screener.in error message: %s", error_div.get_text(strip=True)[:200])
        return False

    except Exception as e:
        log.error("screener.in login exception: %s", e, exc_info=True)
        return False


def _ensure_logged_in():
    """Login on first use and refresh every LOGIN_TTL seconds."""
    global _last_login_at
    with _login_lock:
        if (time.time() - _last_login_at) > _LOGIN_TTL:
            _login()


_url_cache: dict[str, str] = {}  # symbol -> resolved path

# NSE symbol → screener.in path overrides for known mismatches in Nifty 100
_SCREENER_OVERRIDES: dict[str, str] = {
    "TATAMOTORS":   "/company/TMCV/consolidated/",
    "M&M":          "/company/M&M/consolidated/",
    "BAJAJ-AUTO":   "/company/BAJAJ-AUTO/consolidated/",
    "HCLTECH":      "/company/HCL-Technologies/consolidated/",
    "LTIM":         "",  # LTIMindtree merger not yet indexed on screener.in
    "NIFTY50":      "",  # index, not a company
}


def _resolve_screener_url(symbol: str) -> str | None:
    """
    Return the screener.in path for a given NSE symbol.
    Priority: static override map → search API → None.
    """
    if symbol in _url_cache:
        return _url_cache[symbol]

    # 1. Static overrides for known Nifty mismatches
    if symbol in _SCREENER_OVERRIDES:
        path = _SCREENER_OVERRIDES[symbol]
        if path:
            _url_cache[symbol] = path
        return path or None

    # 2. Try screener's search API with company name query
    try:
        resp = _SESSION.get(
            f"https://www.screener.in/api/company/search/?q={symbol}&v=3&fts=1",
            timeout=6,
        )
        if resp.status_code == 200:
            for item in resp.json():
                url = item.get("url", "")
                if "/company/" in url and "/full-text-search/" not in url:
                    _url_cache[symbol] = url
                    return url
    except Exception:
        pass
    return None


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

    _ensure_logged_in()

    result: dict = {"symbol": sym, "source": "screener.in", "available": False}

    # Build URL candidates: direct match first, then search-resolved URL
    paths = [f"/company/{sym}/consolidated/", f"/company/{sym}/"]
    resolved = _resolve_screener_url(sym)
    if resolved and resolved not in paths:
        paths.insert(0, resolved)

    for path in paths:
        url = f"https://www.screener.in{path}"
        try:
            resp = _SESSION.get(url, timeout=10)
            if resp.status_code == 200 and "company" in resp.url:
                try:
                    soup = BeautifulSoup(resp.text, "lxml")
                except Exception:
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

    # ── Company name from page <h1> ────────────────────────────────────────────
    h1 = soup.find("h1")
    if h1:
        name = h1.get_text(strip=True)
        if name and name.upper() != symbol:
            data["company_name"] = name

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
            # ── Banking-specific KPIs (replace D/E and FCF for financial sector) ──
            elif "net npa" in name or "net non-performing" in name:
                data["net_npa_pct"] = val
            elif "gross npa" in name or "gnpa" in name:
                data["gross_npa_pct"] = val
            elif "nim" == name or "net interest margin" in name:
                data["nim_pct"] = val
            elif "casa" in name:
                data["casa_ratio_pct"] = val
            elif "capital adequacy" in name or name == "car":
                data["capital_adequacy_ratio_pct"] = val
            elif "net interest income" in name or "nii" == name:
                data["nii_cr"] = val

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
            if not rows:
                continue
            # Build column index map from header row.
            # Screener uses <th> or <td> for header cells depending on table type.
            # Columns are some subset of: 10 Years / 5 Years / 3 Years / TTM
            col_map: dict[str, int] = {}
            header_row = rows[0]
            header_cells = header_row.find_all("th") or header_row.find_all("td")
            for i, cell in enumerate(header_cells[1:], 0):  # skip label column
                h = cell.get_text(strip=True).lower()
                if "10" in h:
                    col_map["10y"] = i
                elif "5" in h:
                    col_map["5y"] = i
                elif "3" in h:
                    col_map["3y"] = i
                elif "ttm" in h or "trailing" in h:
                    col_map["ttm"] = i

            for row in rows[1:]:
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                label = cells[0].get_text(strip=True).lower()
                vals = [_parse_number(c.get_text(strip=True)) for c in cells[1:]]

                def _get(key: str):
                    idx = col_map.get(key)
                    return vals[idx] if idx is not None and idx < len(vals) else None

                if "sales growth" in label:
                    data["sales_growth_3y_pct"]  = _get("3y")
                    data["sales_growth_5y_pct"]  = _get("5y")
                    data["sales_growth_ttm_pct"] = _get("ttm")
                elif "profit growth" in label:
                    data["profit_growth_3y_pct"]  = _get("3y")
                    data["profit_growth_5y_pct"]  = _get("5y")
                    data["profit_growth_ttm_pct"] = _get("ttm")
                elif "stock price cagr" in label or "price cagr" in label:
                    data["price_cagr_5y_pct"] = _get("5y")

    # ── Fallback growth from P&L annual data (banks lack #growth section) ───────
    # Banks on screener.in don't have the compounded growth table — calculate
    # 3-year CAGR from the annual profit-loss revenue and net profit rows instead.
    if data.get("sales_growth_3y_pct") is None:
        pl_section = soup.find("section", id="profit-loss")
        if pl_section:
            for table in pl_section.find_all("table"):
                for row in table.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) < 4:
                        continue
                    label = cells[0].get_text(strip=True).lower()
                    vals = [_parse_number(c.get_text(strip=True)) for c in cells[1:]]
                    vals = [v for v in vals if v is not None]
                    if ("revenue" in label or "sales" in label) and len(vals) >= 4 and not data.get("sales_growth_3y_pct"):
                        oldest, latest = vals[0], vals[-1]
                        n = len(vals) - 1
                        if oldest and oldest > 0 and n > 0:
                            cagr = ((latest / oldest) ** (1 / n) - 1) * 100
                            data["sales_growth_3y_pct"] = round(cagr, 2)
                            data["sales_growth_ttm_pct"] = round(cagr, 2)
                    elif ("net profit" in label or ("profit" in label and "tax" not in label and "before" not in label)) and len(vals) >= 4 and not data.get("profit_growth_3y_pct"):
                        oldest, latest = vals[0], vals[-1]
                        n = len(vals) - 1
                        if oldest and oldest > 0 and latest and n > 0:
                            cagr = ((latest / oldest) ** (1 / n) - 1) * 100
                            data["profit_growth_3y_pct"] = round(cagr, 2)
                            data["profit_growth_ttm_pct"] = round(cagr, 2)

    # ── Shareholding pattern — latest + full quarterly history ───────────────
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
                # Parse all quarters (strip % sign), oldest first
                all_vals = [_parse_number(c.get_text(strip=True).replace("%", ""))
                            for c in cells[1:]]
                all_vals = [v for v in all_vals if v is not None]
                latest = all_vals[-1] if all_vals else None
                if latest is None:
                    continue
                if "promoters" in label:
                    data["promoter_holding_pct"] = latest
                    data["promoter_quarterly_pct"] = all_vals
                elif "fii" in label or "foreign" in label:
                    data["fii_holding_pct"] = latest
                    data["fii_quarterly_pct"] = all_vals
                elif "dii" in label or "domestic inst" in label:
                    data["dii_holding_pct"] = latest
                    data["dii_quarterly_pct"] = all_vals   # main MF signal
                elif "public" in label:
                    data["public_holding_pct"] = latest
                elif "pledge" in label:
                    data["promoter_pledge_pct"] = latest

    # ── Cash Flow statement — annual operating CF & capex ────────────────────
    cf_section = soup.find("section", id="cash-flow")
    if cf_section:
        table = cf_section.find("table")
        if table:
            rows = table.find_all("tr")
            # Extract fiscal year labels from header row
            header_row = table.find("tr")
            if header_row:
                ths = header_row.find_all("th")
                cf_labels = [th.get_text(strip=True) for th in ths[1:]]
                if cf_labels:
                    data["cashflow_labels"] = cf_labels
            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                label = cells[0].get_text(strip=True).lower()
                vals = [_parse_number(c.get_text(strip=True)) for c in cells[1:]]
                vals = [v for v in vals if v is not None]
                if not vals:
                    continue
                if "operating" in label or "cash from operations" in label:
                    data["operating_cf_annual_cr"] = vals  # oldest → newest
                    data["operating_cf_latest_cr"] = vals[-1]
                elif "investing" in label or "cash from investing" in label:
                    data["investing_cf_annual_cr"] = vals
                    data["investing_cf_latest_cr"] = vals[-1]

    # ── Quarterly results — last 8 quarters of revenue + PAT ─────────────────
    quarterly_section = soup.find("section", id="quarters")
    if quarterly_section:
        table = quarterly_section.find("table")
        if table:
            rows = table.find_all("tr")
            # Extract quarter labels from the header row (th elements)
            header_row = table.find("tr")
            if header_row:
                ths = header_row.find_all("th")
                # First th is the row label ("Quarter"), rest are period labels
                quarter_labels = [th.get_text(strip=True) for th in ths[1:]]
                if quarter_labels:
                    data["quarterly_labels"] = quarter_labels
            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                label = cells[0].get_text(strip=True).lower()
                # Collect all available quarterly values (newest = last column)
                vals = [_parse_number(c.get_text(strip=True)) for c in cells[1:]]
                vals = [v for v in vals if v is not None]
                if "sales" in label or "revenue" in label:
                    data["latest_quarter_revenue_cr"] = vals[-1] if vals else None
                    data["quarterly_revenue_cr"] = vals  # full history
                elif "net profit" in label or "pat" in label:
                    data["latest_quarter_pat_cr"] = vals[-1] if vals else None
                    data["quarterly_pat_cr"] = vals  # full history for earnings stability

    return data


def augment_info_with_screener(info: dict, symbol: str) -> dict:
    """
    Blend screener.in data into the yfinance `info` dict for Indian stocks.
    Screener.in is the authoritative source for Indian fundamentals — its values
    OVERRIDE yfinance for PE, ROE, revenue/profit growth (yfinance is often stale
    for NSE). Missing fields are filled from screener; present fields are replaced.
    """
    try:
        screener = fetch_screener_data(symbol)
        if not screener.get("available"):
            return info

        enriched = dict(info)

        # ── Override core ratios with screener values (more current for India) ──
        # P/E: screener updates daily from BSE/NSE; yfinance can lag weeks
        if screener.get("pe_ratio") is not None:
            enriched["trailingPE"] = screener["pe_ratio"]

        # ROE: screener uses latest annual report; yfinance often stale/missing
        if screener.get("roe_pct") is not None:
            enriched["returnOnEquity"] = screener["roe_pct"] / 100

        # ROCE — yfinance never provides this for Indian stocks
        if screener.get("roce_pct") is not None:
            enriched["returnOnCapitalEmployed"] = screener["roce_pct"] / 100

        # Revenue growth: prefer screener TTM CAGR; fall back to 3Y CAGR
        rev_growth = screener.get("sales_growth_ttm_pct") or screener.get("sales_growth_3y_pct")
        if rev_growth is not None:
            enriched["revenueGrowth"] = rev_growth / 100

        # Earnings growth: derive from quarterly PAT if available, else TTM CAGR
        quarterly_pat = screener.get("quarterly_pat_cr") or []
        if len(quarterly_pat) >= 4:
            # YoY growth: compare latest quarter vs same quarter last year
            latest_pat = quarterly_pat[-1]
            year_ago_pat = quarterly_pat[-5] if len(quarterly_pat) >= 5 else quarterly_pat[0]
            if year_ago_pat and year_ago_pat > 0 and latest_pat is not None:
                yoy_pat_growth = (latest_pat - year_ago_pat) / abs(year_ago_pat)
                enriched["earningsGrowth"] = round(yoy_pat_growth, 4)
            elif screener.get("profit_growth_ttm_pct") is not None:
                enriched["earningsGrowth"] = screener["profit_growth_ttm_pct"] / 100
        elif screener.get("profit_growth_ttm_pct") is not None:
            enriched["earningsGrowth"] = screener["profit_growth_ttm_pct"] / 100

        # Promoter holding — always use screener (yfinance is unreliable for India)
        if screener.get("promoter_holding_pct") is not None:
            enriched["promoterHolding"] = screener["promoter_holding_pct"] / 100
            enriched["heldPercentInsiders"] = screener["promoter_holding_pct"] / 100

        # FII + DII as institutional ownership
        fii = screener.get("fii_holding_pct") or 0
        dii = screener.get("dii_holding_pct") or 0
        if (fii + dii) > 0:
            enriched["heldPercentInstitutions"] = (fii + dii) / 100

        # ── Derive quarterly EPS trend for prediction engine ──────────────────
        # Convert quarterly PAT (Cr) → relative QoQ acceleration signal
        eps_trend = None
        if len(quarterly_pat) >= 4:
            recent = quarterly_pat[-4:]
            try:
                # Check if PAT is consistently growing over last 4 quarters
                diffs = [recent[i+1] - recent[i] for i in range(len(recent)-1)]
                positive_diffs = sum(1 for d in diffs if d > 0)
                if positive_diffs == 3:
                    eps_trend = "accelerating"
                elif positive_diffs == 0:
                    eps_trend = "decelerating"
                elif positive_diffs >= 2:
                    eps_trend = "mixed_positive"
                else:
                    eps_trend = "mixed_negative"
            except Exception:
                pass

        # Tag all enrichment metadata for use in scoring functions
        enriched["_screener_available"] = True
        enriched["_screener_data"] = {
            # Holding structure
            "promoter_holding_pct":      screener.get("promoter_holding_pct"),
            "promoter_pledge_pct":       screener.get("promoter_pledge_pct"),
            "fii_holding_pct":           screener.get("fii_holding_pct"),
            "dii_holding_pct":           screener.get("dii_holding_pct"),
            # Quarterly holding trends (oldest → newest)
            "promoter_quarterly_pct":    screener.get("promoter_quarterly_pct"),
            "fii_quarterly_pct":         screener.get("fii_quarterly_pct"),
            "dii_quarterly_pct":         screener.get("dii_quarterly_pct"),
            # Capital efficiency
            "roce_pct":                  screener.get("roce_pct"),
            # Growth CAGRs
            "sales_growth_3y_pct":       screener.get("sales_growth_3y_pct"),
            "sales_growth_5y_pct":       screener.get("sales_growth_5y_pct"),
            "profit_growth_3y_pct":      screener.get("profit_growth_3y_pct"),
            "profit_growth_5y_pct":      screener.get("profit_growth_5y_pct"),
            "price_cagr_5y_pct":         screener.get("price_cagr_5y_pct"),
            # Book value for P/B calculation
            "book_value":                screener.get("book_value"),
            # Quarterly financials
            "latest_quarter_revenue_cr": screener.get("latest_quarter_revenue_cr"),
            "latest_quarter_pat_cr":     screener.get("latest_quarter_pat_cr"),
            "quarterly_pat_cr":          screener.get("quarterly_pat_cr"),
            "quarterly_revenue_cr":      screener.get("quarterly_revenue_cr"),
            # Derived signals
            "eps_trend":                 eps_trend,
            # Cash flow
            "operating_cf_annual_cr":    screener.get("operating_cf_annual_cr"),
            "operating_cf_latest_cr":    screener.get("operating_cf_latest_cr"),
            "investing_cf_annual_cr":    screener.get("investing_cf_annual_cr"),
            "investing_cf_latest_cr":    screener.get("investing_cf_latest_cr"),
            # Banking-specific KPIs
            "nim_pct":                   screener.get("nim_pct"),
            "net_npa_pct":               screener.get("net_npa_pct"),
            "gross_npa_pct":             screener.get("gross_npa_pct"),
            "casa_ratio_pct":            screener.get("casa_ratio_pct"),
            "capital_adequacy_ratio_pct": screener.get("capital_adequacy_ratio_pct"),
            "nii_cr":                    screener.get("nii_cr"),
        }
        return enriched

    except Exception:
        return info  # always fall back to plain yfinance info on any error
