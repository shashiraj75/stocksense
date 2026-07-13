"""
Outcome Logger — resolves pending predictions against actual price returns.

Called periodically (every 6 hours via main.py).
For each unresolved prediction, fetches current price from yfinance and
computes 1D / 5D / 20D / 60D actual returns.

Only logs outcomes when the full forward window has elapsed — partial returns
were previously logged silently and corrupted IC training data.

Runs once per market (IN, US) — this used to hardcode the NSE ".NS" ticker
suffix unconditionally, so US predictions (added later) could never resolve:
"AAPL.NS" doesn't exist on Yahoo Finance, every fetch silently returned None,
and US outcomes never accumulated at all despite US predictions being logged
fine. The IC engine and meta-model were effectively IN-only despite ranking
both markets' Daily Picks with the same learned weights.
"""

import datetime as _dt
import logging
import time
from datetime import datetime, timezone

import yfinance as yf

log = logging.getLogger(__name__)

_TICKER_SUFFIX = {"IN": ".NS", "US": ""}

# (horizon, min_calendar_days_old, compute_1d, compute_5d, compute_20d, compute_60d)
# min_days is calendar days to wait before attempting resolution.
# _fetch_return enforces the trading-day window independently.
HORIZON_CONFIG = [
    ("short",   3,  True,  True,  False, False),  # 1D+5D; wait 3 cal days
    ("medium", 30,  False, True,  True,  False),  # 5D+20D; wait 30 cal days
    ("long",   90,  False, False, False, True),   # 60D only; wait 90 cal days
]

MARKETS = ("IN", "US")


def _to_date(value) -> _dt.date | None:
    """Normalize a pred_date value (str 'YYYY-MM-DD' from SQLite, or a
    date/datetime from psycopg) to a plain date for range comparison."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    return _dt.date.fromisoformat(str(value))


def _in_date_range(pred_date, start_date: str | None, end_date: str | None) -> bool:
    """Inclusive pred_date range check; None bounds are open-ended."""
    d = _to_date(pred_date)
    if start_date and d < _dt.date.fromisoformat(start_date):
        return False
    if end_date and d > _dt.date.fromisoformat(end_date):
        return False
    return True


def _fetch_return(symbol: str, pred_date_str: str, days: int, market: str = "IN") -> float | None:
    """
    Compute the actual return from pred_date to pred_date + `days` trading days.
    Returns None if fewer than `days` trading days have elapsed since pred_date
    (avoids logging partial returns that corrupt IC calculations).
    """
    try:
        from pandas import Timestamp
        pred_date = Timestamp(pred_date_str)
        ticker = yf.Ticker(symbol + _TICKER_SUFFIX.get(market, ""))
        # Fetch enough history: 60 trading days ≈ 90 calendar days
        hist = ticker.history(start=pred_date_str, period="4mo")
        if hist.empty or len(hist) < 2:
            return None
        hist.index = hist.index.tz_localize(None)
        avail = hist.index[hist.index >= pred_date]
        if len(avail) == 0:
            return None
        entry_price = float(hist.loc[avail[0], "Close"])
        future_rows = hist.index[hist.index >= avail[0]]
        # Require the full window to have elapsed — never use partial returns
        if len(future_rows) <= days:
            return None
        exit_price = float(hist.loc[future_rows[days], "Close"])
        return round((exit_price - entry_price) / entry_price * 100, 4)
    except Exception:
        return None


def resolve_pair(market: str, horizon: str, min_days: int,
                  d1: bool, d5: bool, d20: bool, d60: bool, *,
                  start_date: str | None = None, end_date: str | None = None,
                  dry_run: bool = False, batch_limit: int | None = None) -> dict:
    """
    Resolve one (market, horizon) sweep. Shared by the periodic resolver
    (resolve_pending_outcomes, called with no extra kwargs — full backlog,
    writes enabled) and the operator backfill tool
    (scripts/backfill_outcomes.py) — the same eligibility check and
    COALESCE-preserving log_outcome back both, so a manual backfill run can
    never diverge in behavior from what the live resolver would eventually do
    on its own schedule.

    start_date/end_date: inclusive 'YYYY-MM-DD' pred_date bounds; None means
    open-ended. Only ever passed by the backfill tool.

    dry_run: when True, still fetches real prices (so the preview reflects
    genuine eligibility) but never calls log_outcome — zero writes.

    batch_limit: caps how many pending predictions this call processes.

    Returns a stats dict: examined, resolved (or would-resolve under
    dry_run), skipped (no return could be computed — see _fetch_return; this
    phase does not further distinguish "not enough trading days yet" from
    "missing/suspended/delisted price data", both surface as a skip), and
    pending (count of predictions offered this call, after start_date/
    end_date filtering and batch_limit truncation).
    """
    from services.alpha_engine.store import get_unresolved_predictions, log_outcome

    pending = get_unresolved_predictions(horizon, min_days_old=min_days, market=market)
    if start_date or end_date:
        pending = [p for p in pending if _in_date_range(p["pred_date"], start_date, end_date)]
    if batch_limit is not None:
        pending = pending[:batch_limit]

    log.info(
        f"[outcome_logger] [{market}/{horizon}] {len(pending)} unresolved "
        f"prediction(s) found"
    )

    stats = {"market": market, "horizon": horizon, "examined": 0,
             "resolved": 0, "skipped": 0, "pending": len(pending), "dry_run": dry_run}

    for row in pending:
        symbol    = row["symbol"]
        pred_date = row["pred_date"]
        stats["examined"] += 1

        r1  = _fetch_return(symbol, pred_date, 1,  market) if d1  else None
        r5  = _fetch_return(symbol, pred_date, 5,  market) if d5  else None
        r20 = _fetch_return(symbol, pred_date, 20, market) if d20 else None
        r60 = _fetch_return(symbol, pred_date, 60, market) if d60 else None

        if any(x is not None for x in (r1, r5, r20, r60)):
            if not dry_run:
                log_outcome(symbol, horizon, pred_date, r1, r5, r20,
                            return_60d=r60, market=market)
            stats["resolved"] += 1
        else:
            stats["skipped"] += 1

    return stats


def resolve_pending_outcomes():
    """
    Main entry point — called every 6 hours via outcome_resolver_loop in main.py.

    For each market and horizon, finds predictions whose horizon-relevant
    outcome columns are still incomplete, fetches actual returns, and logs
    them. Never logs a column as resolved before its forward window has
    elapsed — a None from _fetch_return means "not ready yet" for that
    column specifically; other already-resolved columns on the same
    prediction are left untouched (see postgres_store.log_outcome).
    """
    sweep_start = time.time()
    total_examined = 0
    total_resolved = 0
    total_skipped = 0
    try:
        for market in MARKETS:
            for horizon, min_days, d1, d5, d20, d60 in HORIZON_CONFIG:
                stats = resolve_pair(market, horizon, min_days, d1, d5, d20, d60)
                total_examined += stats["examined"]
                total_resolved += stats["resolved"]
                total_skipped  += stats["skipped"]

        elapsed = round(time.time() - sweep_start, 1)
        log.info(
            f"[outcome_logger] sweep complete: examined={total_examined} "
            f"resolved={total_resolved} skipped={total_skipped} elapsed={elapsed}s"
        )

    except Exception as e:
        elapsed = round(time.time() - sweep_start, 1)
        log.warning(
            f"[outcome_logger] sweep error after {elapsed}s "
            f"(examined={total_examined} resolved={total_resolved}): {e}"
        )
