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

import logging
import time
from datetime import datetime, timezone

import yfinance as yf

log = logging.getLogger(__name__)

_TICKER_SUFFIX = {"IN": ".NS", "US": ""}


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


def resolve_pending_outcomes():
    """
    Main entry point — called every 6 hours via outcome_resolver_loop in main.py.

    For each market and horizon, finds predictions old enough that the full
    forward return window has elapsed, fetches actual returns, and logs them.
    Never logs partial returns — a None from _fetch_return means "not ready yet".
    """
    sweep_start = time.time()
    total_examined = 0
    total_resolved = 0
    total_skipped = 0
    try:
        from services.alpha_engine.store import get_unresolved_predictions, log_outcome

        # (horizon, min_calendar_days_old, compute_1d, compute_5d, compute_20d, compute_60d)
        # min_days is calendar days to wait before attempting resolution.
        # _fetch_return enforces the trading-day window independently.
        horizon_config = [
            ("short",   3,  True,  True,  False, False),  # 1D+5D; wait 3 cal days
            ("medium", 30,  False, True,  True,  False),  # 5D+20D; wait 30 cal days
            ("long",   90,  False, False, False, True),   # 60D only; wait 90 cal days
        ]

        for market in ("IN", "US"):
            for horizon, min_days, d1, d5, d20, d60 in horizon_config:
                pending = get_unresolved_predictions(horizon, min_days_old=min_days, market=market)
                log.info(
                    f"[outcome_logger] [{market}/{horizon}] {len(pending)} unresolved "
                    f"prediction(s) found"
                )
                for row in pending:
                    symbol    = row["symbol"]
                    pred_date = row["pred_date"]
                    total_examined += 1

                    r1  = _fetch_return(symbol, pred_date, 1,  market) if d1  else None
                    r5  = _fetch_return(symbol, pred_date, 5,  market) if d5  else None
                    r20 = _fetch_return(symbol, pred_date, 20, market) if d20 else None
                    r60 = _fetch_return(symbol, pred_date, 60, market) if d60 else None

                    if any(x is not None for x in (r1, r5, r20, r60)):
                        log_outcome(symbol, horizon, pred_date, r1, r5, r20,
                                    return_60d=r60, market=market)
                        total_resolved += 1
                    else:
                        total_skipped += 1

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
