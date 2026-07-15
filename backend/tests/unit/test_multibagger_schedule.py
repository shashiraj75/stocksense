"""
Product Integrity #010 — pure-function tests for services.multibagger_schedule.
No DB, no network — these are deterministic timezone-arithmetic checks.

#010 replaced the #009 narrow arrival-time window (2:30-4:30 AM local) with
day-only acceptance — a delayed same-day GitHub Actions dispatch must not
be rejected, since GitHub's own scheduled delivery is best-effort and has
been observed arriving hours late in this repository.
"""
from datetime import datetime, timezone

from services.multibagger_schedule import (
    in_scheduled_day, us_scheduled_day,
    in_scheduled_period_key, us_scheduled_period_key,
    scheduled_day_ok, scheduled_period_key,
)


def test_india_friday_2130_utc_maps_to_saturday_accepted():
    now_utc = datetime(2026, 7, 17, 21, 30, tzinfo=timezone.utc)  # Friday -> Saturday IST
    assert in_scheduled_day(now_utc) is True


def test_india_early_saturday_ist_accepted():
    now_utc = datetime(2026, 7, 17, 21, 0, tzinfo=timezone.utc)  # 2:30 AM IST Saturday
    assert in_scheduled_day(now_utc) is True


def test_india_late_saturday_afternoon_ist_still_accepted():
    """Day-only acceptance (#010): a dispatch delayed to Saturday afternoon IST is still accepted."""
    now_utc = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)  # 3:30 PM IST Saturday
    assert in_scheduled_day(now_utc) is True


def test_india_saturday_almost_midnight_ist_still_accepted():
    now_utc = datetime(2026, 7, 18, 18, 25, tzinfo=timezone.utc)  # 11:55 PM IST Saturday
    assert in_scheduled_day(now_utc) is True


def test_india_sunday_ist_rejected():
    now_utc = datetime(2026, 7, 18, 18, 35, tzinfo=timezone.utc)  # 12:05 AM IST Sunday
    assert in_scheduled_day(now_utc) is False


def test_india_wrong_weekday_rejected():
    now_utc = datetime(2026, 7, 15, 21, 30, tzinfo=timezone.utc)  # Wednesday
    assert in_scheduled_day(now_utc) is False


def test_us_sunday_3am_est_accepted():
    now_utc = datetime(2026, 1, 18, 8, 0, tzinfo=timezone.utc)  # Sunday, 3:00 AM EST
    assert us_scheduled_day(now_utc) is True


def test_us_sunday_4am_edt_accepted():
    now_utc = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)  # Sunday, 4:00 AM EDT
    assert us_scheduled_day(now_utc) is True


def test_us_late_sunday_afternoon_et_still_accepted():
    """Day-only acceptance (#010): a dispatch delayed to Sunday afternoon ET is still accepted."""
    now_utc = datetime(2026, 7, 19, 20, 0, tzinfo=timezone.utc)  # 4:00 PM EDT Sunday
    assert us_scheduled_day(now_utc) is True


def test_us_monday_et_rejected():
    now_utc = datetime(2026, 7, 20, 5, 0, tzinfo=timezone.utc)  # 1:00 AM EDT Monday
    assert us_scheduled_day(now_utc) is False


def test_us_wrong_weekday_rejected():
    now_utc = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)  # Monday, same time-of-day as the real cron
    assert us_scheduled_day(now_utc) is False


def test_weekday_scheduled_calls_rejected_for_both_markets():
    wednesday = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    assert in_scheduled_day(wednesday) is False
    assert us_scheduled_day(wednesday) is False


def test_scheduled_day_ok_dispatches_by_market():
    saturday_ist = datetime(2026, 7, 17, 21, 30, tzinfo=timezone.utc)
    sunday_est = datetime(2026, 1, 18, 8, 0, tzinfo=timezone.utc)
    assert scheduled_day_ok("IN", saturday_ist) is True
    assert scheduled_day_ok("US", saturday_ist) is False
    assert scheduled_day_ok("US", sunday_est) is True
    assert scheduled_day_ok("IN", sunday_est) is False


def test_india_period_key_is_the_intended_saturday_ist_date():
    now_utc = datetime(2026, 7, 17, 21, 30, tzinfo=timezone.utc)  # Fri 21:30 UTC -> Sat 3:00 AM IST
    assert in_scheduled_period_key(now_utc) == "2026-07-18"


def test_india_delayed_dispatch_same_saturday_resolves_to_same_period_key():
    punctual = datetime(2026, 7, 17, 21, 30, tzinfo=timezone.utc)
    delayed = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)  # same Saturday, hours later
    assert in_scheduled_period_key(punctual) == in_scheduled_period_key(delayed) == "2026-07-18"


def test_us_period_key_is_the_intended_sunday_et_date():
    now_utc = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)  # Sun 08:00 UTC -> Sun 4:00 AM EDT
    assert us_scheduled_period_key(now_utc) == "2026-07-19"


def test_us_delayed_dispatch_same_sunday_resolves_to_same_period_key():
    punctual = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    delayed = datetime(2026, 7, 19, 20, 0, tzinfo=timezone.utc)  # same Sunday ET, hours later
    assert us_scheduled_period_key(punctual) == us_scheduled_period_key(delayed) == "2026-07-19"


def test_scheduled_period_key_dispatches_by_market():
    saturday_ist = datetime(2026, 7, 17, 21, 30, tzinfo=timezone.utc)
    assert scheduled_period_key("IN", saturday_ist) == "2026-07-18"
    sunday_est = datetime(2026, 1, 18, 8, 0, tzinfo=timezone.utc)
    assert scheduled_period_key("US", sunday_est) == "2026-01-18"
