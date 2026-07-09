"""
US Daily Picks premarket finalizer — Phase 2 of the 3-phase US Daily Picks
upgrade (see .github/workflows/daily_picks_us_premarket.yml).

Problem this solves: the heavy US base Daily Picks run
(.github/workflows/daily_picks_us.yml, 04:00 UTC = 8:00 AM Dubai = 9:30 AM
IST) now completes many hours before the 9:30 AM ET US market open. This
module provides a lightweight, separate finalizer that re-checks the
already-generated base picks shortly before market open using whatever
premarket-relevant signals this codebase already has a safe, existing
provider for. It never recomputes the US universe, never re-runs
PredictionEngine, and never invents data for a signal this codebase has no
provider for.

What is explicitly NOT done here, and why:
  - No new stock-universe scan, no re-scoring, no re-ranking — Phase 0-7 of
    generate_picks() (daily_picks.py) is the sole owner of that pipeline.
  - No confidence/scoring/ranking/stop-loss/target/regime mutation — this
    feature's safety rules and SES-001 risk-matching forbid weakening or
    silently changing the base pipeline's decision logic. This module only
    ever ADDS metadata fields to the already-persisted payload/pick dicts.
  - No backup-candidate substitution ("replace_from_backup" is defined as
    an allowed per-pick action label but never exercised) — generate_picks()
    does not persist runner-up candidates beyond the top-6-per-horizon
    selection, so there is nothing to replace from. Inventing a backup list
    here would violate "do not invent missing data."
  - No fresh scrape of a data source this codebase doesn't already have a
    provider for (premarket volume, incremental news-since-timestamp,
    sector-index movement, an earnings calendar, bid/ask spread) — each is
    reported as an explicit missing input, never fabricated.

Data inputs actually available (existing providers only):
  - price_gap_pct: the latest available quote from
    services.market_data.MarketDataService (already used by
    GET /api/stocks/quote) compared against the pick's recorded
    generation-time price. Labelled honestly as "latest available quote",
    not verified true premarket-session tick data — yfinance's fast_info
    does not reliably distinguish a premarket print from the prior close.
  - index_proxy_direction: S&P 500 / NASDAQ % change from the existing
    services.global_context.get_global_context() macro snapshot.

Known limitation: holiday detection reuses services.market_hours's US
fixed-holiday calendar (Good Friday + the standard NYSE closures), which is
exchange-calendar-aware, not merely Mon-Fri — but it does not cover ad hoc
one-off market closures (e.g. a national day of mourning) since those have
no closed-form date and are not in that calendar either.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

PREMARKET_FINALIZER_VERSION = "1.0.0"
PREMARKET_TIMEZONE = "America/New_York"
_ET = ZoneInfo(PREMARKET_TIMEZONE)

# Allowed per-pick finalizer actions. See module docstring for which are
# actually exercised today ("upgrade"/"downgrade"/"replace_from_backup" are
# reserved, not yet exercised — see the docstring for why).
ALLOWED_PICK_ACTIONS = frozenset({
    "keep", "upgrade", "downgrade", "replace_from_backup",
    "mark_premarket_risk", "skip_due_to_data_unavailable",
})

# Premarket price-gap magnitude, in percent, above which a pick is flagged
# mark_premarket_risk rather than silently kept. Additive-only signal —
# never changes the pick's own signal/confidence/target/stop-loss fields.
PRICE_GAP_RISK_THRESHOLD_PCT = 3.0

# Inputs this codebase has no existing safe provider for today. Always
# reported as missing — never fabricated. See module docstring.
_ALWAYS_MISSING_INPUTS = (
    "premarket_volume", "fresh_news_since_generation", "sector_movement",
    "earnings_event_risk", "abnormal_volatility_spread_risk",
)


def in_premarket_window(now_et: datetime) -> bool:
    """8:00-8:30 AM America/New_York, Mon-Fri, excluding US market holidays.
    Reuses the existing exchange-calendar holiday utility
    (services.market_hours._us_fixed_holidays) rather than a Mon-Fri-only
    guard — see the module docstring's Known Limitation note for its scope."""
    from services.market_hours import _us_fixed_holidays
    if now_et.weekday() >= 5:
        return False
    if now_et.date() in _us_fixed_holidays(now_et.year):
        return False
    start = now_et.replace(hour=8, minute=0, second=0, microsecond=0)
    end = now_et.replace(hour=8, minute=30, second=0, microsecond=0)
    return start <= now_et <= end


async def _price_gap_for_pick(pick: dict) -> None:
    """Mutates `pick` in place with premarket_* fields. Never raises —
    any failure degrades to skip_due_to_data_unavailable, isolated per pick
    so one symbol's failure can't affect the others (SES-002 §6)."""
    symbol = pick.get("symbol")
    base_price = pick.get("generation_reference_price") or pick.get("price")
    checked_at = datetime.now(timezone.utc).isoformat()
    pick["premarket_checked_at"] = checked_at

    if not symbol or not base_price:
        pick["premarket_action"] = "skip_due_to_data_unavailable"
        pick["premarket_reason"] = "no generation-time price on record to compare against"
        pick["premarket_data_available"] = False
        pick["premarket_warning"] = "missing_base_price"
        return

    try:
        from services.market_data import MarketDataService
        quote = await MarketDataService().get_quote(symbol, "US")
        current = quote.get("price") if quote else None
    except Exception as e:
        log.warning(f"[premarket_finalizer] quote fetch failed for {symbol}: {e}")
        current = None

    if not current:
        pick["premarket_action"] = "skip_due_to_data_unavailable"
        pick["premarket_reason"] = "premarket quote unavailable from existing quote provider"
        pick["premarket_data_available"] = False
        pick["premarket_warning"] = "quote_unavailable"
        return

    gap_pct = round((float(current) - float(base_price)) / float(base_price) * 100, 2)
    pick["premarket_data_available"] = True
    if abs(gap_pct) >= PRICE_GAP_RISK_THRESHOLD_PCT:
        pick["premarket_action"] = "mark_premarket_risk"
        pick["premarket_reason"] = (
            f"latest available quote is {gap_pct:+.2f}% vs generation-time price "
            f"(>= {PRICE_GAP_RISK_THRESHOLD_PCT}% threshold)"
        )
        pick["premarket_warning"] = "premarket_gap_risk"
    else:
        pick["premarket_action"] = "keep"
        pick["premarket_reason"] = (
            f"latest available quote is {gap_pct:+.2f}% vs generation-time price "
            f"(within {PRICE_GAP_RISK_THRESHOLD_PCT}% threshold)"
        )


async def _gather_index_proxy() -> dict | None:
    """Returns {"sp500_change_pct", "nasdaq_change_pct"} or None on failure.
    Never invents a value — None means genuinely unavailable this run."""
    try:
        from services.global_context import get_global_context
        loop = asyncio.get_running_loop()
        ctx = await loop.run_in_executor(None, get_global_context)
        changes = ctx.get("changes", {}) if isinstance(ctx, dict) else {}
        sp500, nasdaq = changes.get("sp500"), changes.get("nasdaq")
        if sp500 is None and nasdaq is None:
            return None
        return {"sp500_change_pct": sp500, "nasdaq_change_pct": nasdaq}
    except Exception as e:
        log.warning(f"[premarket_finalizer] index proxy fetch failed: {e}")
        return None


async def finalize_premarket(market: str, now: datetime | None = None) -> dict:
    """
    Re-check the existing US base Daily Picks with whatever premarket
    signals this codebase already has a safe provider for. Never
    recomputes the universe, never mutates scoring/ranking/confidence/
    target/stop-loss, never invents missing data. See module docstring.

    `now` is injectable (defaults to real UTC now) purely so tests can
    exercise the DST window guard deterministically.
    """
    if market != "US":
        return {"market": market, "status": "skipped", "reason": "unsupported_market"}

    now_et = (now or datetime.now(timezone.utc)).astimezone(_ET)
    if not in_premarket_window(now_et):
        return {"market": "US", "status": "skipped", "reason": "outside_premarket_finalizer_window"}

    import services.daily_picks as _dp
    payload = _dp.get_cached_picks("US")
    if not payload or not payload.get("picks"):
        return {
            "market": "US",
            "status": "failed",
            "reason": "no_base_picks_available",
            "premarket_finalizer_version": PREMARKET_FINALIZER_VERSION,
        }

    checked_at = datetime.now(timezone.utc).isoformat()
    all_picks = [
        pick for items in payload.get("picks", {}).values() for pick in items
    ]

    index_proxy_task = asyncio.ensure_future(_gather_index_proxy())
    if all_picks:
        await asyncio.gather(*(_price_gap_for_pick(p) for p in all_picks))
    index_proxy = await index_proxy_task

    any_price_gap_succeeded = any(p.get("premarket_data_available") for p in all_picks)
    missing_inputs = list(_ALWAYS_MISSING_INPUTS)
    if index_proxy is None:
        missing_inputs.append("index_proxy_direction")
    if all_picks and not any_price_gap_succeeded:
        missing_inputs.append("price_gap_pct")

    data_available = any_price_gap_succeeded or index_proxy is not None

    # Additive metadata only — every existing field on `payload` (generated_at,
    # picks, alpha_engine, regime, screener_raw_count, etc.) is preserved
    # untouched; per-pick base fields (symbol, price, target, stop_loss,
    # confidence, signal, reasoning, factor_zscores, ranking, ...) are
    # likewise preserved, only premarket_* keys are added.
    payload["base_generated_at"] = payload.get("generated_at")
    payload["premarket_finalized_at"] = datetime.now(timezone.utc).isoformat()
    payload["premarket_status"] = "completed_with_limited_premarket_data"
    payload["premarket_finalizer_version"] = PREMARKET_FINALIZER_VERSION
    payload["premarket_data_available"] = data_available
    payload["premarket_missing_inputs"] = sorted(set(missing_inputs))
    payload["premarket_window_checked"] = checked_at
    payload["premarket_timezone"] = PREMARKET_TIMEZONE
    if index_proxy is not None:
        payload["premarket_index_proxy"] = index_proxy

    # Persist the SAME payload with additive fields only.
    try:
        import json as _json
        with open(_dp._cache_file("US"), "w") as f:
            _json.dump(payload, f)
    except Exception as e:
        log.warning(f"[premarket_finalizer] disk cache write failed: {e}")

    if os.getenv("USE_POSTGRES") == "1":
        try:
            from services.postgres_store import save_picks_to_db
            save_picks_to_db(payload, market="US")
        except Exception as e:
            log.warning(f"[premarket_finalizer] Postgres save failed: {e}")

    return {
        "market": "US",
        "status": "completed_with_limited_premarket_data",
        "premarket_finalizer_version": PREMARKET_FINALIZER_VERSION,
        "premarket_data_available": data_available,
        "premarket_missing_inputs": payload["premarket_missing_inputs"],
    }
