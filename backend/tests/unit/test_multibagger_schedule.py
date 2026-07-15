"""
Product Integrity #009 — pure-function tests for services.multibagger_schedule.
No DB, no network — these are deterministic timezone-arithmetic checks.
"""
from datetime import datetime, timezone

from services.multibagger_schedule import (
    in_scheduled_window, us_scheduled_window,
    in_scheduled_period_key, us_scheduled_period_key,
    scheduled_window_ok, scheduled_period_key,
)


def test_india_friday_2130_utc_maps_to_saturday_3am_ist():
    now_utc = datetime(2026, 7, 17, 21, 30, tzinfo=timezone.utc)  # Friday
    assert in_scheduled_window(now_utc) is True


def test_india_window_lower_boundary_230am_ist_accepted():
    # 2:30 AM IST = 21:00 UTC the prior day (IST = UTC+5:30)
    now_utc = datetime(2026, 7, 17, 21, 0, tzinfo=timezone.utc)
    assert in_scheduled_window(now_utc) is True


def test_india_window_upper_boundary_430am_ist_rejected():
    # 4:30 AM IST = 23:00 UTC the prior day
    now_utc = datetime(2026, 7, 17, 23, 0, tzinfo=timezone.utc)
    assert in_scheduled_window(now_utc) is False


def test_india_window_just_before_upper_boundary_accepted():
    now_utc = datetime(2026, 7, 17, 22, 59, tzinfo=timezone.utc)  # 4:29 AM IST
    assert in_scheduled_window(now_utc) is True


def test_india_wrong_weekday_rejected():
    # Same time-of-day but a Wednesday, not Saturday
    now_utc = datetime(2026, 7, 15, 21, 30, tzinfo=timezone.utc)
    assert in_scheduled_window(now_utc) is False


def test_us_sunday_3am_edt_accepted():
    # 07:00 UTC candidate, July = EDT (UTC-4) -> 3:00 AM ET
    now_utc = datetime(2026, 7, 19, 7, 0, tzinfo=timezone.utc)  # Sunday
    assert us_scheduled_window(now_utc) is True


def test_us_sunday_3am_est_accepted():
    # 08:00 UTC candidate, January = EST (UTC-5) -> 3:00 AM ET
    now_utc = datetime(2026, 1, 18, 8, 0, tzinfo=timezone.utc)  # Sunday
    assert us_scheduled_window(now_utc) is True


def test_us_inactive_edt_candidate_during_est_safely_rejected():
    # 07:00 UTC in January (EST) = 2:00 AM ET — before the window opens
    now_utc = datetime(2026, 1, 18, 7, 0, tzinfo=timezone.utc)
    assert us_scheduled_window(now_utc) is False


def test_both_candidates_can_land_inside_the_window_during_edt():
    """
    During EDT, 07:00 UTC = 3:00 AM ET and 08:00 UTC = 4:00 AM ET — both
    inside [2:30, 4:30) ET. The window's job is DST tolerance, not candidate
    exclusivity; weekly-period idempotency (scheduled_period_key) is what
    prevents this from starting two jobs, not the window itself. During
    EST the asymmetry flips the other way (see the EST test above) — only
    one candidate (08:00 UTC = 3:00 AM ET) lands inside the window.
    """
    now_utc = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)  # 4:00 AM EDT
    assert us_scheduled_window(now_utc) is True


def test_us_window_lower_boundary_230am_et_accepted():
    now_utc = datetime(2026, 7, 19, 6, 30, tzinfo=timezone.utc)  # 2:30 AM EDT
    assert us_scheduled_window(now_utc) is True


def test_us_window_upper_boundary_430am_et_rejected():
    now_utc = datetime(2026, 7, 19, 8, 30, tzinfo=timezone.utc)  # 4:30 AM EDT
    assert us_scheduled_window(now_utc) is False


def test_us_wrong_weekday_rejected():
    now_utc = datetime(2026, 7, 20, 7, 0, tzinfo=timezone.utc)  # Monday, same time-of-day
    assert us_scheduled_window(now_utc) is False


def test_weekday_scheduled_calls_rejected_for_both_markets():
    wednesday = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    assert in_scheduled_window(wednesday) is False
    assert us_scheduled_window(wednesday) is False


def test_scheduled_window_ok_dispatches_by_market():
    saturday_ist = datetime(2026, 7, 17, 21, 30, tzinfo=timezone.utc)
    sunday_edt = datetime(2026, 7, 19, 7, 0, tzinfo=timezone.utc)
    assert scheduled_window_ok("IN", saturday_ist) is True
    assert scheduled_window_ok("US", saturday_ist) is False
    assert scheduled_window_ok("US", sunday_edt) is True
    assert scheduled_window_ok("IN", sunday_edt) is False


def test_india_period_key_is_the_intended_saturday_ist_date():
    now_utc = datetime(2026, 7, 17, 21, 30, tzinfo=timezone.utc)  # Fri 21:30 UTC -> Sat 3:00 AM IST
    assert in_scheduled_period_key(now_utc) == "2026-07-18"


def test_us_period_key_is_the_intended_sunday_et_date():
    now_utc = datetime(2026, 7, 19, 7, 0, tzinfo=timezone.utc)  # Sun 07:00 UTC -> Sun 3:00 AM EDT
    assert us_scheduled_period_key(now_utc) == "2026-07-19"


def test_us_est_only_one_candidate_lands_in_window():
    """During EST: 07:00 UTC = 2:00 AM ET (before window), 08:00 UTC = 3:00 AM ET (inside)."""
    est_sunday_before_window = datetime(2026, 1, 18, 7, 0, tzinfo=timezone.utc)
    est_sunday_in_window = datetime(2026, 1, 18, 8, 0, tzinfo=timezone.utc)
    assert us_scheduled_window(est_sunday_before_window) is False
    assert us_scheduled_window(est_sunday_in_window) is True


def test_us_duplicate_same_period_key_when_both_edt_candidates_fire():
    """
    Even when both candidates land inside the window on the same Sunday
    (the EDT case above), they resolve to the identical scheduled_period_key
    — the actual duplicate-prevention mechanism is weekly-period
    idempotency in postgres_store, not window exclusivity. Asserted here as
    the reason the window doesn't need to be narrower than 2 hours.
    """
    candidate_a = datetime(2026, 7, 19, 7, 0, tzinfo=timezone.utc)
    candidate_b = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    assert us_scheduled_period_key(candidate_a) == us_scheduled_period_key(candidate_b) == "2026-07-19"


def test_scheduled_period_key_dispatches_by_market():
    saturday_ist = datetime(2026, 7, 17, 21, 30, tzinfo=timezone.utc)
    assert scheduled_period_key("IN", saturday_ist) == "2026-07-18"
    sunday_edt = datetime(2026, 7, 19, 7, 0, tzinfo=timezone.utc)
    assert scheduled_period_key("US", sunday_edt) == "2026-07-19"
