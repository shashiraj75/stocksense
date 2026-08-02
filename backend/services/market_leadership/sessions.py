"""
Point-in-time slicing and completed-session rules.

Every calculation in this module family takes an explicit `as_of` date and
must only see bars from sessions completed on or before that date. This file
is the single place that enforces it — calculation code never slices ad hoc.

Conservative completed-session rules (no live clock reads inside calculation
functions — `now` is always an explicit argument to the session helpers):
- India: reuses the NSE session algorithm already ported to the backend by
  Product Integrity #011 (`market_hours.get_expected_latest_completed_nse_session`).
- US: a session counts as completed only from 00:00 UTC of the following
  calendar day — deliberately conservative (no early-close special cases;
  a not-yet-countable completed session is safe, the reverse is not).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd


def slice_as_of(df: pd.DataFrame | pd.Series, as_of: date):
    """Return only rows whose session date is <= as_of.

    Handles tz-aware and tz-naive DatetimeIndex (yfinance returns tz-aware).
    The comparison is on the bar's *calendar session date* in its own
    exchange timezone as labeled by the index — no timezone conversion is
    performed, matching how the providers label sessions.
    """
    if df is None or len(df) == 0:
        return df
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        raise TypeError("slice_as_of requires a DatetimeIndex")
    mask = idx.date <= as_of
    return df.loc[mask]


def expected_latest_completed_us_session(now_utc: datetime) -> date:
    """Latest US session that is certainly complete at `now_utc`.

    Conservative rule: a weekday session D is countable only once the UTC
    calendar day is past D (i.e. from 00:00 UTC on D+1 — 7-8 pm ET on D).
    Weekends roll back to Friday. US holidays are NOT modeled — a holiday
    yields a one-session-stale expectation, which downstream code reports
    as staleness rather than failure (safe direction).
    """
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    d = (now_utc.astimezone(timezone.utc).date()) - timedelta(days=1)
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d -= timedelta(days=1)
    return d


def expected_latest_completed_session(market: str, now_utc: datetime) -> date | None:
    """Market-appropriate latest completed session (None only if the India
    holiday-calendar walk fails — callers must treat None as stale/unknown,
    never as 'no expectation, accept anything')."""
    if market == "IN":
        # PI-011's NSE rule, reused not duplicated (weekends + NSE holidays).
        from services.market_hours import get_expected_latest_completed_nse_session
        return get_expected_latest_completed_nse_session(now_utc)
    if market == "US":
        return expected_latest_completed_us_session(now_utc)
    raise ValueError(f"unsupported market {market!r} — must be 'IN' or 'US'")


def drop_incomplete_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Drop any row with a NaN Close — a provider-incomplete bar.

    Confirmed via live data (2026-07-25, real yfinance AAPL history):
    the technically most-recent row in a `period="2y"` fetch can carry a
    genuine NaN Close even though its session date has already closed —
    Yahoo had not finished backfilling that day's close yet. Such a row
    must never be treated as an available session: it would otherwise
    propagate NaN into every downstream return/moving-average/drawdown
    calculation and — confirmed live — break JSON serialization of the
    API response entirely. Dropping it is equivalent to treating that
    session as "not yet available," the same conservative posture used
    everywhere else in this module for missing data.
    """
    if df is None or len(df) == 0:
        return df
    if "Close" not in df.columns:
        return df
    return df[df["Close"].notna()]


def window_coverage(bars: pd.Series | pd.DataFrame, window_sessions: int) -> float:
    """Fraction of the expected trailing window actually covered by bars.

    `bars` must already be as-of sliced. Expected sessions are approximated
    as `window_sessions` trading days; coverage = available/expected capped
    at 1.0. (Exchange holiday calendars are not modeled per-market here —
    the 90% coverage gate absorbs normal holiday density.)
    """
    if bars is None or len(bars) == 0:
        return 0.0
    return min(1.0, len(bars.tail(window_sessions)) / float(window_sessions))
