"""
Product Integrity #010 — weekly Multibagger scheduled-day and weekly-
period-identity logic, DST-aware, both markets. Pure functions, no
DB/network access, so they're directly unit-testable without mocking.

India: once weekly, Saturday, Asia/Kolkata (no DST).
US: once weekly, Sunday, America/New_York — 3:00 AM EST / 4:00 AM EDT for
the single fixed 08:00 UTC cron (Product Integrity #010; #009's original
two-DST-candidate design was replaced with one fixed cron — the one-hour
seasonal difference is accepted as materially safer than duplicate cron
delivery and DST-candidate ambiguity).

#010 change: acceptance is now day-only, not a narrow arrival-time window.
A narrow window (the #009 2:30-4:30 AM design) would reject a GitHub
Actions dispatch delayed past the window on the same correct local day —
GitHub's own scheduled delivery is best-effort and has been observed
arriving hours late in this repository (see Product Integrity #003).
Accepting the full scheduled local day and relying on weekly-period
idempotency (not window narrowness) to prevent duplicates is the safer
design.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ET = ZoneInfo("America/New_York")

_IN_WEEKDAY = 5   # Python Monday=0 .. Sunday=6; Saturday=5
_US_WEEKDAY = 6   # Sunday=6


def in_scheduled_day(now_utc: datetime) -> bool:
    """True if now_utc, converted to IST, falls on Saturday (any local time that day)."""
    return now_utc.astimezone(IST).weekday() == _IN_WEEKDAY


def us_scheduled_day(now_utc: datetime) -> bool:
    """True if now_utc, converted to America/New_York, falls on Sunday (any local time that day)."""
    return now_utc.astimezone(ET).weekday() == _US_WEEKDAY


def in_scheduled_period_key(now_utc: datetime) -> str:
    """
    The intended Saturday's IST date as YYYY-MM-DD, regardless of exactly
    when that Saturday now_utc falls — both a punctual and a delayed
    dispatch of the same Saturday's run resolve to the same key.
    """
    return now_utc.astimezone(IST).date().isoformat()


def us_scheduled_period_key(now_utc: datetime) -> str:
    """The intended Sunday's ET date as YYYY-MM-DD — see in_scheduled_period_key()."""
    return now_utc.astimezone(ET).date().isoformat()


def scheduled_day_ok(market: str, now_utc: datetime) -> bool:
    return in_scheduled_day(now_utc) if market == "IN" else us_scheduled_day(now_utc)


def scheduled_period_key(market: str, now_utc: datetime) -> str:
    return in_scheduled_period_key(now_utc) if market == "IN" else us_scheduled_period_key(now_utc)
