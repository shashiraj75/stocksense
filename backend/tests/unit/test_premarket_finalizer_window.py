"""
3-phase US Daily Picks upgrade — premarket finalizer DST-aware window guard.

services.premarket_finalizer.in_premarket_window() must allow execution only
inside 7:30-9:00 AM America/New_York on a US market weekday/non-holiday,
regardless of which of the two fixed-UTC GitHub Actions cron candidates
(daily_picks_us_premarket.yml: 11:35 UTC for EDT, 12:35 UTC for EST) fired.

Widened from the original 8:00-8:30 (30 min) on 2026-07-13 after a real
missed run — GitHub Actions' scheduled cron didn't fire the workflow at all
before the old, narrower window closed. This width is now WIDER than the
1-hour EDT/EST gap between the two cron candidates, so — unlike the old
design — BOTH candidates now land inside the window on the same day; the
finalize_premarket() same-day idempotency guard (tested separately below)
is what prevents that from causing a duplicate daily run, not this window
function alone.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from unittest.mock import patch

import pytest

from services.premarket_finalizer import in_premarket_window, finalize_premarket

ET = ZoneInfo("America/New_York")


def _et(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=ET)


@pytest.mark.unit
class TestPremarketWindowGuard:
    def test_inside_window_start_boundary_is_allowed(self):
        # 2026-07-06 is a Monday, no US holiday.
        assert in_premarket_window(_et(2026, 7, 6, 7, 30)) is True

    def test_inside_window_mid_point_is_allowed(self):
        assert in_premarket_window(_et(2026, 7, 6, 8, 15)) is True

    def test_inside_window_end_boundary_is_allowed(self):
        assert in_premarket_window(_et(2026, 7, 6, 9, 0)) is True

    def test_before_window_is_rejected(self):
        assert in_premarket_window(_et(2026, 7, 6, 7, 29)) is False

    def test_after_window_is_rejected(self):
        assert in_premarket_window(_et(2026, 7, 6, 9, 1)) is False

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
        # 11:35 UTC on a July (EDT) weekday = 7:35 AM ET.
        utc_time = datetime(2026, 7, 6, 11, 35, tzinfo=timezone.utc)
        assert in_premarket_window(utc_time.astimezone(ET)) is True

    def test_est_cron_candidate_also_maps_inside_window_during_edt(self):
        # 12:35 UTC on the SAME July (EDT) weekday = 8:35 AM ET — this is the
        # key behavior change from the old 30-minute-window design: with a
        # 90-minute window, the "wrong" DST candidate now ALSO lands inside
        # the window (unlike before, where it safely fell outside a narrower
        # one). This is expected and relies on finalize_premarket()'s
        # same-day idempotency guard, not window exclusivity, to prevent a
        # duplicate daily run — see TestFinalizePremarketIdempotency below.
        utc_time = datetime(2026, 7, 6, 12, 35, tzinfo=timezone.utc)
        assert in_premarket_window(utc_time.astimezone(ET)) is True

    def test_est_cron_candidate_maps_inside_window_during_est(self):
        # 12:35 UTC on a January (EST) weekday = 7:35 AM ET.
        utc_time = datetime(2026, 1, 6, 12, 35, tzinfo=timezone.utc)
        assert in_premarket_window(utc_time.astimezone(ET)) is True

    def test_edt_cron_candidate_also_maps_inside_window_during_est(self):
        # 11:35 UTC on the SAME January (EST) weekday = 6:35 AM ET — outside
        # the window on this side (unlike the EDT case above, this one still
        # falls outside 7:30-9:00, since EST shifts it earlier, not later).
        utc_time = datetime(2026, 1, 6, 11, 35, tzinfo=timezone.utc)
        assert in_premarket_window(utc_time.astimezone(ET)) is False


@pytest.mark.unit
class TestFinalizePremarketIdempotency:
    """
    Guards the new (2026-07-13) same-day idempotency check: with the window
    now wider than the 1-hour EDT/EST cron gap, both daily cron candidates
    land inside it, so finalize_premarket() must treat a same-day repeat
    call as a safe no-op rather than re-doing the premarket price-gap work.
    """

    def _payload(self, premarket_finalized_at=None):
        return {
            "picks": {"short": [{"symbol": "AAPL"}]},
            "generated_at": "2026-07-06T07:30:00+00:00",
            "premarket_finalized_at": premarket_finalized_at,
        }

    @pytest.mark.asyncio
    async def test_second_call_same_day_is_skipped_not_reprocessed(self):
        now = _et(2026, 7, 6, 8, 35)  # inside window, second (EST-candidate) call
        already_finalized_today = datetime(2026, 7, 6, 11, 35, tzinfo=timezone.utc).isoformat()
        with patch("services.daily_picks.get_cached_picks", return_value=self._payload(already_finalized_today)):
            result = await finalize_premarket("US", now=now.astimezone(timezone.utc))
        assert result["status"] == "skipped"
        assert result["reason"] == "already_finalized_today"

    @pytest.mark.asyncio
    async def test_first_call_of_the_day_proceeds_normally(self):
        now = _et(2026, 7, 6, 7, 35)  # inside window, first (EDT-candidate) call
        with patch("services.daily_picks.get_cached_picks", return_value=self._payload(None)):
            with patch("services.premarket_finalizer._gather_index_proxy", return_value=None):
                with patch("services.premarket_finalizer._price_gap_for_pick", return_value=None):
                    result = await finalize_premarket("US", now=now.astimezone(timezone.utc))
        assert result.get("status") != "skipped"

    @pytest.mark.asyncio
    async def test_a_prior_days_finalization_does_not_block_todays_run(self):
        now = _et(2026, 7, 6, 7, 35)
        yesterday_finalized = datetime(2026, 7, 5, 11, 35, tzinfo=timezone.utc).isoformat()
        with patch("services.daily_picks.get_cached_picks", return_value=self._payload(yesterday_finalized)):
            with patch("services.premarket_finalizer._gather_index_proxy", return_value=None):
                with patch("services.premarket_finalizer._price_gap_for_pick", return_value=None):
                    result = await finalize_premarket("US", now=now.astimezone(timezone.utc))
        assert result.get("reason") != "already_finalized_today"

    @pytest.mark.asyncio
    async def test_malformed_premarket_finalized_at_does_not_block_a_real_attempt(self):
        now = _et(2026, 7, 6, 7, 35)
        with patch("services.daily_picks.get_cached_picks", return_value=self._payload("not-a-timestamp")):
            with patch("services.premarket_finalizer._gather_index_proxy", return_value=None):
                with patch("services.premarket_finalizer._price_gap_for_pick", return_value=None):
                    result = await finalize_premarket("US", now=now.astimezone(timezone.utc))
        assert result.get("reason") != "already_finalized_today"
