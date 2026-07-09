"""
3-phase US Daily Picks upgrade — premarket finalizer DST-aware window guard.

services.premarket_finalizer.in_premarket_window() must allow execution only
inside 8:00-8:30 AM America/New_York on a US market weekday/non-holiday,
regardless of which of the two fixed-UTC GitHub Actions cron candidates
(daily_picks_us_premarket.yml: 12:05 UTC for EDT, 13:05 UTC for EST) fired —
since GitHub Actions cron is UTC-only and does not observe US DST, exactly
one of the two candidates is inside the window on any given day and the
other must safely no-op via this guard.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services.premarket_finalizer import in_premarket_window

ET = ZoneInfo("America/New_York")


def _et(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=ET)


@pytest.mark.unit
class TestPremarketWindowGuard:
    def test_inside_window_start_boundary_is_allowed(self):
        # 2026-07-06 is a Monday, no US holiday.
        assert in_premarket_window(_et(2026, 7, 6, 8, 0)) is True

    def test_inside_window_mid_point_is_allowed(self):
        assert in_premarket_window(_et(2026, 7, 6, 8, 15)) is True

    def test_inside_window_end_boundary_is_allowed(self):
        assert in_premarket_window(_et(2026, 7, 6, 8, 30)) is True

    def test_before_window_is_rejected(self):
        assert in_premarket_window(_et(2026, 7, 6, 7, 59)) is False

    def test_after_window_is_rejected(self):
        assert in_premarket_window(_et(2026, 7, 6, 8, 31)) is False

    def test_weekend_saturday_is_rejected(self):
        # 2026-07-04 is a Saturday (also July 4th — belt and suspenders).
        assert in_premarket_window(_et(2026, 7, 4, 8, 15)) is False

    def test_weekend_sunday_is_rejected(self):
        assert in_premarket_window(_et(2026, 7, 5, 8, 15)) is False

    def test_us_market_holiday_is_rejected(self):
        # Independence Day observed 2026-07-03 (July 4 falls on a Saturday
        # in 2026 -> observed Friday per services.market_hours._us_fixed_holidays).
        assert in_premarket_window(_et(2026, 7, 3, 8, 15)) is False

    def test_edt_cron_candidate_maps_inside_window_during_edt(self):
        # 12:05 UTC on a July (EDT) weekday = 8:05 AM ET.
        from datetime import timezone, timedelta
        utc_time = datetime(2026, 7, 6, 12, 5, tzinfo=timezone.utc)
        assert in_premarket_window(utc_time.astimezone(ET)) is True

    def test_est_cron_candidate_maps_outside_window_during_edt(self):
        # 13:05 UTC on the SAME July (EDT) weekday = 9:05 AM ET — outside window.
        from datetime import timezone
        utc_time = datetime(2026, 7, 6, 13, 5, tzinfo=timezone.utc)
        assert in_premarket_window(utc_time.astimezone(ET)) is False

    def test_est_cron_candidate_maps_inside_window_during_est(self):
        # 13:05 UTC on a January (EST) weekday = 8:05 AM ET.
        from datetime import timezone
        utc_time = datetime(2026, 1, 6, 13, 5, tzinfo=timezone.utc)
        assert in_premarket_window(utc_time.astimezone(ET)) is True

    def test_edt_cron_candidate_maps_outside_window_during_est(self):
        # 12:05 UTC on the SAME January (EST) weekday = 7:05 AM ET — outside window.
        from datetime import timezone
        utc_time = datetime(2026, 1, 6, 12, 5, tzinfo=timezone.utc)
        assert in_premarket_window(utc_time.astimezone(ET)) is False
