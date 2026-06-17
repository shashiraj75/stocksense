"""
Outcome Logger — resolves pending predictions against actual price returns.

Called at the start of each daily picks run.
For each unresolved prediction, fetches current price from yfinance and
computes 1D / 5D / 20D actual returns.

This is the data pipeline that feeds the IC engine and meta-model.
Without outcomes, neither IC nor the meta-model can learn.
"""

from datetime import datetime, timezone

import yfinance as yf


def _fetch_return(symbol: str, pred_date_str: str, days: int) -> float | None:
    """
    Compute the actual return from pred_date to pred_date + days.
    pred_date_str is ISO date string (YYYY-MM-DD).
    """
    try:
        from pandas import Timestamp, tseries
        pred_date = Timestamp(pred_date_str)
        ticker = yf.Ticker(symbol + ".NS")
        hist = ticker.history(start=pred_date_str, period="2mo")
        if hist.empty or len(hist) < 2:
            return None
        # Price on prediction date (or closest trading day after)
        hist.index = hist.index.tz_localize(None)
        avail = hist.index[hist.index >= pred_date]
        if len(avail) == 0:
            return None
        entry_price = float(hist.loc[avail[0], "Close"])
        # Forward return: find price at entry_date + days trading days
        future_rows = hist.index[hist.index >= avail[0]]
        if len(future_rows) <= days:
            # Use last available if not enough rows yet
            if len(future_rows) < 2:
                return None
            exit_price = float(hist.loc[future_rows[-1], "Close"])
        else:
            exit_price = float(hist.loc[future_rows[days], "Close"])
        return round((exit_price - entry_price) / entry_price * 100, 4)
    except Exception:
        return None


def resolve_pending_outcomes():
    """
    Main entry point — called at the start of each generate_picks() run.

    For each horizon, finds predictions old enough that the forward return
    period has elapsed, fetches actual returns, and logs them.
    """
    try:
        from services.alpha_engine.store import get_unresolved_predictions, log_outcome

        # Days to wait before resolving, per horizon
        horizon_config = [
            ("short",  2,  1,  5,  None),   # resolve after 2 days; compute 1D+5D
            ("medium", 22, None, 5, 20),     # resolve after 22 days; compute 5D+20D
            ("long",   22, None, None, 20),  # resolve after 22 days; compute 20D
        ]

        total_resolved = 0
        for horizon, min_days, d1, d5, d20 in horizon_config:
            pending = get_unresolved_predictions(horizon, min_days_old=min_days)
            for row in pending:
                symbol   = row["symbol"]
                pred_date = row["pred_date"]

                r1  = _fetch_return(symbol, pred_date, 1)  if d1  else None
                r5  = _fetch_return(symbol, pred_date, 5)  if d5  else None
                r20 = _fetch_return(symbol, pred_date, 20) if d20 else None

                if any(x is not None for x in (r1, r5, r20)):
                    log_outcome(symbol, horizon, pred_date, r1, r5, r20)
                    total_resolved += 1

        if total_resolved:
            print(f"[outcome_logger] Resolved {total_resolved} pending predictions")

    except Exception as e:
        print(f"[outcome_logger] Error: {e}")
