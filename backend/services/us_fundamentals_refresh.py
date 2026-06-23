"""
Nightly batch job: pulls yfinance fundamentals for the US common-stock
universe and caches the result in Postgres (stock_fundamentals_cache,
market='US'), so the Multibagger Screen feature can run instant SQL
filters instead of fetching live on every request.

Deliberately more conservative than the IN job's pace: yfinance is the
live pricing/quote backbone for the ENTIRE app (not just this feature), so
getting it rate-limited or blocked has a much bigger blast radius than the
IN job's screener.in dependency (which only affects IN fundamentals). Each
symbol here also makes 4 separate yfinance calls (info, balance_sheet,
cashflow, financials) versus screener.in's single page-scrape, so the
per-symbol cost is already higher before adding the larger delay.

~5,300 real common stocks (the ~12,000-entry US_STOCKS universe is more
than half ETFs/funds/preferred shares/warrants, which are excluded here —
they have no "ROE/ROCE/sales growth" in the equity-fundamentals sense a
stock screen needs) at this pace takes roughly 5-6 hours.
"""
import time
import logging

from services.stock_universe import US_STOCKS
from services.us_fundamentals import fetch_us_fundamentals
from services import fundamentals_cache as cache

log = logging.getLogger(__name__)

REQUEST_DELAY_SECONDS = 2.5  # more conservative than the IN job's 1.0s — see module docstring

_NON_COMMON_KEYWORDS = (
    "etf", "fund", "trust", "depositary shares", "warrant", "right",
    " unit ", "units", "preferred", "notes", "spac",
)

_FINANCIAL_KEYWORDS = ("financial", "bank", "insurance", "nbfc", "capital markets")


def _is_common_stock(name: str) -> bool:
    lname = name.lower()
    return not any(k in lname for k in _NON_COMMON_KEYWORDS)


def _is_financial(sector: str | None, industry: str | None) -> bool:
    text = f"{sector or ''} {industry or ''}".lower()
    return any(k in text for k in _FINANCIAL_KEYWORDS)


def run_full_refresh() -> dict:
    cache.ensure_table()

    universe = [(sym, name) for sym, name in US_STOCKS if _is_common_stock(name)]
    total = len(universe)
    refreshed = 0
    skipped = 0
    failed = 0
    started = time.time()

    print(f"[us_fundamentals_refresh] Starting full refresh — {total} common stocks "
          f"(filtered from {len(US_STOCKS)} total universe entries)")

    for i, (symbol, _name) in enumerate(universe, 1):
        try:
            data = fetch_us_fundamentals(symbol)
            if not data.get("available"):
                skipped += 1
                continue

            is_fin = _is_financial(data.get("sector"), data.get("industry"))
            fields = {
                "company_name": data.get("company_name"),
                "sector_name": data.get("sector"),
                "industry_name": data.get("industry"),
                "market_cap_usd_m": round(data["market_cap"] / 1e6, 1) if data.get("market_cap") else None,
                "pe_ratio": data.get("pe_ratio"),
                "roe_pct": data.get("roe_pct"),
                "roe_5y_pct": data.get("roe_4y_pct"),  # see FIELD_MAP comment — 4Y avg for US
                "roce_pct": data.get("roce_pct"),
                "debt_to_equity_pct": data.get("debt_to_equity"),
                "insider_holding_pct": data.get("insider_holding_pct"),
                "sales_growth_3y_pct": data.get("revenue_3y_cagr_pct"),
                "profit_growth_3y_pct": data.get("profit_3y_cagr_pct"),
                "opm_pct": data.get("opm_pct"),
                "interest_coverage_ratio": data.get("interest_coverage_ratio"),
                "ev_ebitda": data.get("ev_ebitda"),
                "price_to_sales": data.get("price_to_sales"),
                "operating_cf_latest_cr": data.get("operating_cashflow") / 1e6 if data.get("operating_cashflow") else None,
            }
            cache.upsert(symbol, "US", is_fin, fields)
            refreshed += 1

        except Exception as e:
            failed += 1
            log.warning("[us_fundamentals_refresh] %s failed: %s", symbol, e)

        if i % 100 == 0:
            elapsed = time.time() - started
            print(f"[us_fundamentals_refresh] {i}/{total} processed "
                  f"({refreshed} ok, {skipped} skipped, {failed} failed) — {elapsed/60:.1f}m elapsed")

        time.sleep(REQUEST_DELAY_SECONDS)

    elapsed = time.time() - started
    summary = {
        "total": total, "refreshed": refreshed, "skipped": skipped,
        "failed": failed, "elapsed_minutes": round(elapsed / 60, 1),
    }
    print(f"[us_fundamentals_refresh] Done: {summary}")
    return summary
