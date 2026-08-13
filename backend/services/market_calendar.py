"""
V-SCHED1C2B — trusted, package-shipped exchange-calendar resolution for
automatic short-horizon scheduling.

Uses pandas_market_calendars (pinned pandas-market-calendars==5.4.0):
  - "XNSE" for nifty100 and midcap (NSE, India);
  - "NYSE" for us.

All calendar data is package-shipped at install time — no network calls,
no database access, no live fetch, at runtime. This module is a pure,
stateless, side-effect-free resolver: it never creates ledger state
itself (see api/main.py's short scheduler/catch-up for that).

FAIL-CLOSED CONTRACT — the single most important property of this
module: any exception, unsupported universe, non-timezone-aware input,
malformed schedule row, or NSE session date beyond
NSE_CALENDAR_SUPPORTED_THROUGH resolves to status="unknown", never
"eligible". Calendar uncertainty must never be silently treated as an
eligible trading session. Confirmed directly (not assumed) that
pandas_market_calendars' XNSE calendar does NOT raise or return an empty
schedule for dates beyond its own lunar/festival holiday data — it
silently continues generating "open" weekday rows indefinitely — so this
boundary is enforced by this module's own code, not delegated to the
library.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pandas_market_calendars as mcal

# NSE lunar/festival holiday data in the underlying calendar package is
# only reliably populated through this date (directly verified: querying
# XNSE for 2028 returns every weekday as "open" with zero holidays
# excluded — no exception, no empty result). Any NSE-exchange
# determination for a session date after this boundary MUST fail closed
# as unknown/ineligible. Bump this constant only as part of a reviewed
# pandas-market-calendars package upgrade that re-verifies NSE holiday
# coverage through the new boundary — never bump it standalone.
NSE_CALENDAR_SUPPORTED_THROUGH = date(2027, 12, 31)

# A regular NSE session is ~6h15m (09:15-15:30 IST = 375 minutes).
# Muhurat and any other shortened/special session run roughly one hour.
# This is a generic duration threshold, not a hardcoded Muhurat date, so
# any future shortened session is excluded without a per-date update.
# 300 minutes (5 hours) sits comfortably below every observed regular
# NSE session length and comfortably above a ~1-hour special session.
NSE_FULL_SESSION_MIN_MINUTES = 300

_NSE_EXCHANGES = ("XNSE", "BSE")

_UNIVERSE_EXCHANGE = {
    "nifty100": "XNSE",
    "midcap": "XNSE",
    "us": "NYSE",
}

# Bounded lookback — comfortably longer than any credible NSE/NYSE
# holiday run (the longest realistic closure streak, e.g. a long weekend
# plus an adjacent holiday, is well under a week) while still bounded —
# this module must never walk an unbounded historical backlog.
_LOOKBACK_DAYS = 10


@dataclass(frozen=True)
class ShortSessionResolution:
    """Immutable result of resolve_latest_completed_short_session()."""
    status: str  # "eligible" | "ineligible" | "unknown"
    universe: str
    exchange: str | None
    session_date: date | None
    open_utc: datetime | None
    close_utc: datetime | None
    reason: str


def _unknown(universe: str, reason: str, exchange: str | None = None) -> ShortSessionResolution:
    return ShortSessionResolution(
        status="unknown", universe=universe, exchange=exchange,
        session_date=None, open_utc=None, close_utc=None, reason=reason,
    )


def resolve_latest_completed_short_session(universe: str, *, now_utc: datetime) -> ShortSessionResolution:
    """The latest COMPLETED, applicable exchange session for `universe`
    as of `now_utc` — or an "unknown"/"ineligible" result if none can be
    safely determined. Never manufactures a session from weekday
    arithmetic alone; every result is derived from the package-shipped
    calendar's own schedule.

    NYSE early-close sessions ARE eligible (a shorter session is still a
    real, completed session for the US market). NSE shortened/special
    sessions (e.g. Muhurat) are EXCLUDED in this version — see
    NSE_FULL_SESSION_MIN_MINUTES — by skipping them and continuing to
    walk backward for the last regular-length session, since a regular
    session usually did complete nearby, just not on that date.
    """
    if universe not in _UNIVERSE_EXCHANGE:
        return _unknown(universe, "unsupported_universe")
    if now_utc.tzinfo is None:
        return _unknown(universe, "now_utc_not_timezone_aware")

    exchange = _UNIVERSE_EXCHANGE[universe]
    is_nse = exchange in _NSE_EXCHANGES

    if is_nse and now_utc.date() > NSE_CALENDAR_SUPPORTED_THROUGH:
        return _unknown(universe, "nse_beyond_supported_boundary", exchange)

    try:
        calendar = mcal.get_calendar(exchange)
    except Exception:
        return _unknown(universe, "calendar_unavailable", exchange)

    start = (now_utc - timedelta(days=_LOOKBACK_DAYS)).date()
    end = now_utc.date()
    try:
        schedule = calendar.schedule(start_date=start.isoformat(), end_date=end.isoformat())
    except Exception:
        return _unknown(universe, "calendar_schedule_error", exchange)

    if schedule is None or schedule.empty:
        return _unknown(universe, "no_sessions_in_lookback_window", exchange)

    try:
        closes = schedule["market_close"]
        opens = schedule["market_open"]
    except Exception:
        return _unknown(universe, "malformed_schedule", exchange)

    for session_ts in reversed(schedule.index):
        try:
            raw_close = closes.loc[session_ts]
            raw_open = opens.loc[session_ts]
            if raw_close.tzinfo is None or raw_open.tzinfo is None:
                return _unknown(universe, "naive_session_timestamp", exchange)
            close_utc = raw_close.to_pydatetime().astimezone(timezone.utc)
            open_utc = raw_open.to_pydatetime().astimezone(timezone.utc)
        except Exception:
            return _unknown(universe, "malformed_session_row", exchange)

        if close_utc > now_utc:
            continue  # not yet completed as of now_utc

        session_date = session_ts.date() if hasattr(session_ts, "date") else session_ts

        if is_nse and session_date > NSE_CALENDAR_SUPPORTED_THROUGH:
            return _unknown(universe, "nse_beyond_supported_boundary", exchange)

        if is_nse:
            duration_minutes = (close_utc - open_utc).total_seconds() / 60
            if duration_minutes < NSE_FULL_SESSION_MIN_MINUTES:
                continue  # shortened/special session — excluded in v1, keep looking

        return ShortSessionResolution(
            status="eligible", universe=universe, exchange=exchange,
            session_date=session_date, open_utc=open_utc, close_utc=close_utc,
            reason="ok",
        )

    return _unknown(universe, "no_completed_session_in_lookback_window", exchange)
