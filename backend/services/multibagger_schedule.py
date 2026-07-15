"""
Product Integrity #009 — weekly Multibagger schedule-window and
weekly-period-identity logic, DST-aware, both markets. Pure functions, no
DB/network access, so they're directly unit-testable without mocking.

India: once weekly, Saturday, local window 2:30 AM (inclusive) through
4:30 AM (exclusive) Asia/Kolkata (IST has no DST).

US: once weekly, Sunday, local window 2:30 AM (inclusive) through 4:30 AM
(exclusive) America/New_York — DST-aware via zoneinfo, so the same window
correctly accepts either GitHub Actions UTC cron candidate (07:00 UTC EDT,
08:00 UTC EST) without the backend needing to know which one fired.
"""
from datetime import datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ET = ZoneInfo("America/New_York")

_WINDOW_START = time(2, 30)
_WINDOW_END = time(4, 30)

_IN_WEEKDAY = 5   # Python Monday=0 .. Sunday=6; Saturday=5
_US_WEEKDAY = 6   # Sunday=6


def in_scheduled_window(now_utc: datetime) -> bool:
    """True if now_utc, converted to IST, falls on Saturday within [2:30, 4:30)."""
    local = now_utc.astimezone(IST)
    return local.weekday() == _IN_WEEKDAY and _WINDOW_START <= local.time() < _WINDOW_END


def us_scheduled_window(now_utc: datetime) -> bool:
    """True if now_utc, converted to America/New_York, falls on Sunday within [2:30, 4:30)."""
    local = now_utc.astimezone(ET)
    return local.weekday() == _US_WEEKDAY and _WINDOW_START <= local.time() < _WINDOW_END


def in_scheduled_period_key(now_utc: datetime) -> str:
    """
    The intended Saturday's IST date as YYYY-MM-DD, regardless of exactly
    when within the window now_utc falls — both a punctual and a delayed
    dispatch of the same Saturday's run resolve to the same key.
    """
    return now_utc.astimezone(IST).date().isoformat()


def us_scheduled_period_key(now_utc: datetime) -> str:
    """The intended Sunday's ET date as YYYY-MM-DD — see in_scheduled_period_key()."""
    return now_utc.astimezone(ET).date().isoformat()


def scheduled_window_ok(market: str, now_utc: datetime) -> bool:
    return in_scheduled_window(now_utc) if market == "IN" else us_scheduled_window(now_utc)


def scheduled_period_key(market: str, now_utc: datetime) -> str:
    return in_scheduled_period_key(now_utc) if market == "IN" else us_scheduled_period_key(now_utc)
