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
            # yfinance gives this directly — no need to derive from market cap/sales
            "price_to_sales": info.get("priceToSalesTrailing12Months"),
        }

        # EV/EBITDA — yfinance gives real totalDebt/totalCash for US stocks
        # (unlike screener.in for India, where we had to approximate without
        # cash netting), so this is a genuine EV calc, not an approximation.
        try:
            total_debt = info.get("totalDebt")
            total_cash = info.get("totalCash")
            market_cap = info.get("marketCap")
            fin = ticker.financials
            if fin is not None and not fin.empty and "EBITDA" in fin.index and market_cap is not None:
                ebitda = _clean(fin.loc["EBITDA"].dropna().iloc[0]) if not fin.loc["EBITDA"].dropna().empty else None
                if ebitda and ebitda > 0:
                    ev = market_cap + (total_debt or 0) - (total_cash or 0)
                    data["ev_ebitda"] = round(ev / ebitda, 2)
        except Exception:
            pass

        # OPM% and Interest Coverage Ratio from the income statement —
        # yfinance exposes EBIT/Operating Income/Interest Expense directly.
        try:
            fin = ticker.financials
            if fin is not None and not fin.empty:
                op_income = _clean(fin.loc["Operating Income"].dropna().iloc[0]) if "Operating Income" in fin.index and not fin.loc["Operating Income"].dropna().empty else None
                revenue = _clean(fin.loc["Total Revenue"].dropna().iloc[0]) if "Total Revenue" in fin.index and not fin.loc["Total Revenue"].dropna().empty else None
                if op_income is not None and revenue and revenue > 0:
                    data["opm_pct"] = round(op_income / revenue * 100, 1)

                ebit = _clean(fin.loc["EBIT"].dropna().iloc[0]) if "EBIT" in fin.index and not fin.loc["EBIT"].dropna().empty else None
                interest = _clean(fin.loc["Interest Expense"].dropna().iloc[0]) if "Interest Expense" in fin.index and not fin.loc["Interest Expense"].dropna().empty else None
                if ebit is not None and interest and abs(interest) > 1e4:  # skip ~0 interest — meaningless coverage
                    data["interest_coverage_ratio"] = round(ebit / abs(interest), 2)
        except Exception:
            pass

        # ROCE = EBIT / (Total Assets - Current Liabilities), latest year.
        # yfinance's own returnOnCapitalEmployed field is unreliable/sparse
        # (same issue noted for India) — derived directly instead.
        try:
            fin = ticker.financials
            bs = ticker.balance_sheet
            if fin is not None and not fin.empty and bs is not None and not bs.empty and "EBIT" in fin.index:
                ebit = _clean(fin.loc["EBIT"].dropna().iloc[0]) if not fin.loc["EBIT"].dropna().empty else None
                total_assets = _clean(bs.loc["Total Assets"].dropna().iloc[0]) if "Total Assets" in bs.index and not bs.loc["Total Assets"].dropna().empty else None
                current_liab = _clean(bs.loc["Current Liabilities"].dropna().iloc[0]) if "Current Liabilities" in bs.index and not bs.loc["Current Liabilities"].dropna().empty else None
                if ebit is not None and total_assets is not None and current_liab is not None:
                    capital_employed = total_assets - current_liab
                    if capital_employed > 0:
                        data["roce_pct"] = round(ebit / capital_employed * 100, 1)
        except Exception:
            pass

        # ROE 4Y average (yfinance's free tier caps annual financials at 4
        # years — labelled "4y" rather than "5y" so it doesn't overclaim data
        # we don't have, same honesty constraint as the IN scorecard).
        try:
            fin = ticker.financials
            bs = ticker.balance_sheet
            if fin is not None and not fin.empty and bs is not None and not bs.empty and "Net Income" in fin.index and "Stockholders Equity" in bs.index:
                ni_series = fin.loc["Net Income"].dropna()
                eq_series = bs.loc["Stockholders Equity"].dropna()
                common_years = [c for c in ni_series.index if c in eq_series.index]
                yearly_roe = []
                for y in common_years:
                    ni, eq = _clean(ni_series[y]), _clean(eq_series[y])
                    if ni is not None and eq and eq > 0:
                        yearly_roe.append(ni / eq * 100)
                if yearly_roe:
                    data["roe_4y_pct"] = round(sum(yearly_roe) / len(yearly_roe), 1)
        except Exception:
            pass

        # Multi-year balance sheet — Total Debt / Stockholders Equity / Total Assets.
        # yfinance's columns already come newest-first, so no reversal needed.
        try:
            bs = ticker.balance_sheet
            if bs is not None and not bs.empty:
                cols = list(bs.columns)
                data["balance_sheet_labels"] = [f"FY{str(c.year)[2:]}" for c in cols]
                for row_label, key in [
                    ("Total Debt", "total_debt_annual_m"),
                    ("Stockholders Equity", "stockholders_equity_annual_m"),
                    ("Total Assets", "total_assets_annual_m"),
                ]:
                    if row_label in bs.index:
                        vals = bs.loc[row_label].tolist()
                        data[key] = [round(v / 1e6, 1) if _clean(v) is not None else None for v in vals]
        except Exception:
            pass

        # Multi-year cash flow — Operating / Investing, newest-first
        try:
            cf = ticker.cashflow
            if cf is not None and not cf.empty:
                cols = list(cf.columns)
                data["cashflow_labels"] = [f"FY{str(c.year)[2:]}" for c in cols]
                for row_label, key in [
                    ("Operating Cash Flow", "operating_cf_annual_m"),
                    ("Investing Cash Flow", "investing_cf_annual_m"),
                ]:
                    if row_label in cf.index:
                        vals = cf.loc[row_label].tolist()
                        data[key] = [round(v / 1e6, 1) if _clean(v) is not None else None for v in vals]
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

        # StockSense360 Business Quality Engine (SSDS-003) — additive only,
        # Sprint #005. Sprint #005 deliberately scoped this to US: this
        # function already has `ticker`/`info` in scope from the yfinance
        # call above, so adding the engine here costs zero new network
        # calls. The IN refresh path (fundamentals_refresh.py) sources
        # fundamentals from screener.in and never constructs a yfinance
        # Ticker at all — adding one there would be a new data-fetching
        # pattern in an already rate-limited nightly job, which is exactly
        # the "broad refactor" this sprint was told not to do. Tracked as
        # an explicit, named follow-up, not silently done or silently
        # skipped. `df` is passed as an empty DataFrame deliberately —
        # confirmed by reading both buffett_munger_score's and
        # quality_metrics_score's source: neither one actually reads the
        # `df` parameter anywhere in its body, so fetching price history
        # here would cost a network call for zero signal.
        try:
            import pandas as pd
            from services.business_quality_engine import compute_business_quality
            bq = compute_business_quality(sym, ticker, pd.DataFrame(), info, market="US")
            data["business_quality_score"] = bq.get("score")
            data["business_quality_grade"] = bq.get("grade")
            data["business_quality_style"] = (bq.get("metadata") or {}).get("suitable_investment_style")
        except Exception as e:
            # Never let a Business Quality Engine failure break the
            # existing fundamentals fetch this function has always
            # provided - additive signal, not a new hard dependency.
            data["business_quality_score"] = None
            data["business_quality_grade"] = None
            data["business_quality_style"] = None

        return data
    except Exception as e:
        return {"available": False, "reason": str(e), "symbol": sym}
