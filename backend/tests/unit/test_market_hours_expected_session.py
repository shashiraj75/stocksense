"""
Product Integrity #011 — tests for get_expected_latest_completed_nse_session(),
the backend mirror of frontend marketHours.ts's getExpectedLatestCompletedSession()
(Product Integrity #004). Pure function, no DB/network.
"""
import datetime

from services.market_hours import get_expected_latest_completed_nse_session, IST


def _ist(y, m, d, h, mi):
    return datetime.datetime(y, m, d, h, mi, tzinfo=IST)


def test_after_close_same_day_returns_today():
    # Thursday 2026-07-16, 4:00 PM IST — after the 3:30 PM close.
    now = _ist(2026, 7, 16, 16, 0)
    assert get_expected_latest_completed_nse_session(now) == datetime.date(2026, 7, 16)


def test_before_close_same_day_returns_previous_trading_day():
    # Thursday 2026-07-16, 5:21 AM IST (a realistic Daily Picks generation time) —
    # before today's close, so the expected session is Wednesday 2026-07-15.
    now = _ist(2026, 7, 16, 5, 21)
    assert get_expected_latest_completed_nse_session(now) == datetime.date(2026, 7, 15)


def test_exactly_at_close_counts_as_after_close():
    now = _ist(2026, 7, 16, 15, 30)
    assert get_expected_latest_completed_nse_session(now) == datetime.date(2026, 7, 16)


def test_one_minute_before_close_counts_as_before_close():
    now = _ist(2026, 7, 16, 15, 29)
    assert get_expected_latest_completed_nse_session(now) == datetime.date(2026, 7, 15)


def test_monday_before_close_skips_weekend_to_friday():
    # Monday 2026-07-20, 5:00 AM IST — before close, so walk back past
    # Sunday 19th and Saturday 18th to Friday 2026-07-17.
    now = _ist(2026, 7, 20, 5, 0)
    assert get_expected_latest_completed_nse_session(now) == datetime.date(2026, 7, 17)


def test_saturday_returns_friday():
    now = _ist(2026, 7, 18, 12, 0)  # Saturday
    assert get_expected_latest_completed_nse_session(now) == datetime.date(2026, 7, 17)


def test_skips_a_known_2026_holiday():
    # Independence Day 2026-08-15 is a Saturday — already covered by the
    # weekend test above, so use a fixed weekday holiday instead: Republic
    # Day 2026-01-26 is a Monday. After close on the 26th, expected session
    # must skip back to Friday 2026-01-23 (weekend + this holiday).
    now = _ist(2026, 1, 26, 16, 0)
    assert get_expected_latest_completed_nse_session(now) == datetime.date(2026, 1, 23)


def test_skips_a_known_2026_extra_holiday():
    # Holi 2026-03-03 is a Tuesday (NSE_EXTRA_HOLIDAYS).
    now = _ist(2026, 3, 3, 16, 0)
    assert get_expected_latest_completed_nse_session(now) == datetime.date(2026, 3, 2)


def test_naive_datetime_is_treated_as_ist():
    now = datetime.datetime(2026, 7, 16, 16, 0)  # no tzinfo
    assert get_expected_latest_completed_nse_session(now) == datetime.date(2026, 7, 16)


def test_none_defaults_to_current_time_and_returns_a_date():
    result = get_expected_latest_completed_nse_session(None)
    assert isinstance(result, datetime.date)


def test_utc_datetime_is_converted_to_ist_before_computing():
    # 2026-07-16 05:00 UTC = 10:30 AM IST — after close would need 3:30 PM
    # IST i.e. 10:00 UTC, so this UTC time (converted) is before close.
    now_utc = datetime.datetime(2026, 7, 16, 5, 0, tzinfo=datetime.timezone.utc)
    assert get_expected_latest_completed_nse_session(now_utc) == datetime.date(2026, 7, 15)
