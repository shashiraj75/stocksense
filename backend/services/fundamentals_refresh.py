"""
Nightly batch job: scrapes screener.in for the full NSE universe and
caches the result in Postgres (stock_fundamentals_cache), so the
Multibagger Screen feature can run instant SQL filters instead of
scraping live on every request.

~2,300 stocks at a polite pace (rate-limited to avoid screener.in blocking
us — we also depend on it for predictions and the Fundamentals tab) takes
roughly 1-2 hours. Triggered via POST /api/multibagger/refresh from a
GitHub Actions cron, same pattern as daily picks generation — not run as
an in-process asyncio loop, since a job this long shouldn't compete with
live request handling on the same server process.
"""
import time
import logging

from services.stock_universe import IN_STOCKS
from services.screener_data import fetch_screener_data
from services.india_business_quality_adapter import compute_india_business_quality
from services import fundamentals_cache as cache

log = logging.getLogger(__name__)

REQUEST_DELAY_SECONDS = 1.0  # politeness delay between screener.in requests

# financial-sector keyword check — same pattern used in prediction_engine.py's
# is_financial detection, so screens can exempt the same set of companies.
_FINANCIAL_KEYWORDS = ("financial", "bank", "insurance", "nbfc")


def _is_financial(sector_name: str | None, industry_name: str | None) -> bool:
    text = f"{sector_name or ''} {industry_name or ''}".lower()
    return any(k in text for k in _FINANCIAL_KEYWORDS)


def run_full_refresh() -> dict:
    cache.ensure_table()

    total = len(IN_STOCKS)
    refreshed = 0
    skipped = 0
    failed = 0
    started = time.time()

    print(f"[fundamentals_refresh] Starting full refresh — {total} symbols")

    for i, (symbol, _name) in enumerate(IN_STOCKS, 1):
        try:
            data = fetch_screener_data(symbol)
            if not data.get("available"):
                skipped += 1
                continue

            is_fin = _is_financial(data.get("sector_name"), data.get("industry_name"))

            # StockSense360 India Business Quality Adapter (SSDS-003,
            # SSDS-004, Sprint #007). Reuses the screener data already
            # fetched above — no extra screener call. Computed here at
            # refresh time, exactly as the US side does in
            # us_fundamentals.py's _build(), so request-time latency is
            # unaffected (the Multibagger screen still serves from cache).
            # Failure is non-fatal: the four fields stay absent and the row
            # upserts with everything else, mirroring the US try/except.
            bq = compute_india_business_quality(symbol, data, market="IN")
            if bq is not None:
                data["business_quality_score"] = bq.get("score")
                data["business_quality_grade"] = bq.get("grade")
                data["business_quality_style"] = (bq.get("metadata") or {}).get("suitable_investment_style")
                data["business_quality_confidence"] = bq.get("confidence")

            cache.upsert(symbol, "IN", is_fin, data)
            refreshed += 1

        except Exception as e:
            failed += 1
            log.warning("[fundamentals_refresh] %s failed: %s", symbol, e)

        if i % 100 == 0:
            elapsed = time.time() - started
            print(f"[fundamentals_refresh] {i}/{total} processed "
                  f"({refreshed} ok, {skipped} skipped, {failed} failed) — {elapsed/60:.1f}m elapsed")

        time.sleep(REQUEST_DELAY_SECONDS)

    elapsed = time.time() - started
    summary = {
        "total": total, "refreshed": refreshed, "skipped": skipped,
        "failed": failed, "elapsed_minutes": round(elapsed / 60, 1),
    }
    print(f"[fundamentals_refresh] Done: {summary}")
    return summary
