"""
US fundamentals fetcher — yfinance is the source for US stocks (unlike India,
where yfinance is frequently stale/missing and screener.in fills the gap).
Mirrors the shape of services/screener_data.py's output where practical so
the frontend's Fundamentals tab can reuse the same card patterns.
"""
import threading
import time

import yfinance as yf

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
CACHE_TTL = 4 * 3600  # 4 hours — fundamentals don't change intraday


def _pct(v: float | None) -> float | None:
    return round(v * 100, 2) if v is not None else None


def _clean(v):
    """NaN is not None — yfinance/pandas readily returns it for missing cells,
    and it isn't valid JSON. Convert it to None everywhere."""
    return None if v is None or v != v else v


def fetch_us_fundamentals(symbol: str) -> dict:
    sym = symbol.upper().strip()

    with _cache_lock:
        cached = _cache.get(sym)
        if cached and (time.time() - cached[0]) < CACHE_TTL:
            return cached[1]

    result = _build(sym)

    with _cache_lock:
        _cache[sym] = (time.time(), result)

    return result


def _build(sym: str) -> dict:
    try:
        ticker = yf.Ticker(sym)
        info = ticker.info or {}
        if info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
            return {"available": False, "reason": f"No fundamental data available for {sym}", "symbol": sym}

        data: dict = {
            "available": True,
            "symbol": sym,
            "source": "yfinance",
            "company_name": info.get("longName") or info.get("shortName"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "price_to_book": info.get("priceToBook"),
            "book_value": info.get("bookValue"),
            "roe_pct": _pct(info.get("returnOnEquity")),
            "roa_pct": _pct(info.get("returnOnAssets")),
            "profit_margin_pct": _pct(info.get("profitMargins")),
            # yfinance's dividendYield is already a plain percentage number
            # (e.g. 0.36 means 0.36%), not a fraction — no *100 scaling needed.
            "dividend_yield_pct": info.get("dividendYield"),
            "market_cap": info.get("marketCap"),
            "debt_to_equity": info.get("debtToEquity"),
            "revenue_growth_pct": _pct(info.get("revenueGrowth")),
            "earnings_growth_pct": _pct(info.get("earningsGrowth")),
            "free_cashflow": info.get("freeCashflow"),
            "operating_cashflow": info.get("operatingCashflow"),
            "insider_holding_pct": _pct(info.get("heldPercentInsiders")),
            "institution_holding_pct": _pct(info.get("heldPercentInstitutions")),
            "analyst_recommendation": info.get("recommendationKey"),
            "analyst_target_price": info.get("targetMeanPrice"),
            "analyst_count": info.get("numberOfAnalystOpinions"),
        }

        # Multi-year balance sheet — Total Debt / Stockholders Equity / Total Assets,
        # oldest → newest to match the IN Balance Sheet card's display convention.
        try:
            bs = ticker.balance_sheet
            if bs is not None and not bs.empty:
                cols = list(bs.columns)
                data["balance_sheet_labels"] = [f"FY{str(c.year)[2:]}" for c in reversed(cols)]
                for row_label, key in [
                    ("Total Debt", "total_debt_annual_m"),
                    ("Stockholders Equity", "stockholders_equity_annual_m"),
                    ("Total Assets", "total_assets_annual_m"),
                ]:
                    if row_label in bs.index:
                        vals = bs.loc[row_label].tolist()
                        data[key] = [round(v / 1e6, 1) if _clean(v) is not None else None for v in reversed(vals)]
        except Exception:
            pass

        # Multi-year cash flow — Operating / Investing, oldest → newest
        try:
            cf = ticker.cashflow
            if cf is not None and not cf.empty:
                cols = list(cf.columns)
                data["cashflow_labels"] = [f"FY{str(c.year)[2:]}" for c in reversed(cols)]
                for row_label, key in [
                    ("Operating Cash Flow", "operating_cf_annual_m"),
                    ("Investing Cash Flow", "investing_cf_annual_m"),
                ]:
                    if row_label in cf.index:
                        vals = cf.loc[row_label].tolist()
                        data[key] = [round(v / 1e6, 1) if _clean(v) is not None else None for v in reversed(vals)]
        except Exception:
            pass

        # Revenue / net income 3Y CAGR computed from annual financials
        try:
            fin = ticker.financials
            if fin is not None and not fin.empty:
                if "Total Revenue" in fin.index:
                    rev = fin.loc["Total Revenue"].dropna().tolist()  # newest first
                    n = min(3, len(rev) - 1)
                    if n > 0 and rev[n] and rev[n] > 0:
                        data["revenue_3y_cagr_pct"] = round(((rev[0] / rev[n]) ** (1 / n) - 1) * 100, 1)
                if "Net Income" in fin.index:
                    ni = fin.loc["Net Income"].dropna().tolist()
                    n = min(3, len(ni) - 1)
                    if n > 0 and ni[n] and ni[n] > 0:
                        data["profit_3y_cagr_pct"] = round(((ni[0] / ni[n]) ** (1 / n) - 1) * 100, 1)
        except Exception:
            pass

        return data
    except Exception as e:
        return {"available": False, "reason": str(e), "symbol": sym}
