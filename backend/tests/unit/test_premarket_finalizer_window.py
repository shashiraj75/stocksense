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
from unittest.mock import MagicMock, patch

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
    Guards the exact-base idempotency check (Daily Picks Scheduler
    Remediation Phase 1A, superseding the old same-calendar-date-only
    guard): with the window wider than the 1-hour EDT/EST cron gap, both
    daily cron candidates land inside it, so finalize_premarket() must
    treat a repeat call for the SAME (source_job_id, base_generated_at)
    as a safe no-op rather than re-doing the premarket price-gap work —
    but a genuinely new base (different source_job_id or generated_at)
    must never be blocked merely because it shares today's calendar date.
    """

    def _payload(self, *, premarket_finalized_for_job_id=None,
                 premarket_finalized_for_base=None, premarket_finalized_at=None,
                 generated_at="2026-07-06T07:30:00+00:00", source_job_id="job-us-1"):
        payload = {
            "market": "US",
            "picks": {"short": [{"symbol": "AAPL"}], "medium": [], "long": []},
            "generated_at": generated_at,
            "source_job_id": source_job_id,
        }
        if premarket_finalized_for_job_id is not None:
            payload["premarket_finalized_for_job_id"] = premarket_finalized_for_job_id
        if premarket_finalized_for_base is not None:
            payload["premarket_finalized_for_base"] = premarket_finalized_for_base
        if premarket_finalized_at is not None:
            payload["premarket_finalized_at"] = premarket_finalized_at
        return payload

    def _job_row(self, job_id="job-us-1"):
        return {
            "job_id": job_id, "market": "US", "status": "completed",
            "started_at": "2026-07-06T03:00:00+00:00",
            "completed_at": "2026-07-06T07:00:00+00:00",
            "persisted_picks_timestamp": "2026-07-06T07:00:01+00:00",
            "universe_degraded": False, "last_error": None,
        }

    @pytest.mark.asyncio
    async def test_second_call_same_exact_base_is_skipped_not_reprocessed(self):
        now = _et(2026, 7, 6, 8, 35)  # inside window, second (EST-candidate) call
        already_finalized_today = datetime(2026, 7, 6, 11, 35, tzinfo=timezone.utc).isoformat()
        payload = self._payload(
            premarket_finalized_for_job_id="job-us-1",
            premarket_finalized_for_base="2026-07-06T07:30:00+00:00",
            premarket_finalized_at=already_finalized_today,
        )
        with patch("services.daily_picks.get_cached_picks", return_value=payload):
            result = await finalize_premarket("US", now=now.astimezone(timezone.utc))
        assert result["status"] == "skipped"
        assert result["reason"] == "already_finalized"

    @pytest.mark.asyncio
    async def test_first_call_of_the_day_proceeds_to_completion(self):
        now = _et(2026, 7, 6, 7, 35)  # inside window, first (EDT-candidate) call
        payload = self._payload()
        with patch("services.daily_picks.get_cached_picks", return_value=payload), \
             patch("services.daily_picks._cache_file", return_value="/dev/null"), \
             patch("services.postgres_store.get_daily_picks_job_by_id", return_value=self._job_row()), \
             patch("services.postgres_store.save_picks_to_db", return_value=True), \
             patch("services.premarket_finalizer._gather_index_proxy", return_value=None), \
             patch("services.premarket_finalizer._price_gap_for_pick", return_value=None), \
             patch.dict("os.environ", {"USE_POSTGRES": "1"}):
            result = await finalize_premarket("US", now=now.astimezone(timezone.utc))
        assert result["status"] == "completed"
        assert result["reason"] == "finalized"

    @pytest.mark.asyncio
    async def test_a_prior_days_finalization_does_not_block_todays_run(self):
        """A different base_generated_at (yesterday's) must never match
        today's idempotency key, regardless of calendar-date proximity.

        Scheduler Remediation Phase 1A post-review fix: leftover markers
        that are present but describe a DIFFERENT job/base than the
        current, structurally-fresh payload are contradictory provenance,
        not a harmless "no markers" fresh base — they fail closed
        (rejected_malformed_base) rather than being silently treated as
        eligible for a brand-new finalization. This is a strictly
        stronger guarantee than the old assertion here (which only
        checked the result wasn't wrongly reported already_finalized):
        it also confirms mismatched markers never cause the base to be
        finalized over contradictory metadata. See
        TestFinalizePremarketAdversarialFinalizationMarkers in
        test_premarket_finalizer_base_integrity.py for the dedicated
        coverage, and test_missing_idempotency_fields_do_not_block_a_real_attempt
        below for what a genuine same-day rerun (no markers at all)
        actually looks like."""
        now = _et(2026, 7, 6, 7, 35)
        payload = self._payload(
            premarket_finalized_for_job_id="job-us-yesterday",
            premarket_finalized_for_base="2026-07-05T07:30:00+00:00",
            premarket_finalized_at=datetime(2026, 7, 5, 11, 35, tzinfo=timezone.utc).isoformat(),
        )
        save_mock = MagicMock()
        with patch("services.daily_picks.get_cached_picks", return_value=payload), \
             patch("services.daily_picks._cache_file", return_value="/dev/null"), \
             patch("services.postgres_store.get_daily_picks_job_by_id") as job_lookup_mock, \
             patch("services.postgres_store.save_picks_to_db", save_mock), \
             patch("services.premarket_finalizer._gather_index_proxy", return_value=None), \
             patch("services.premarket_finalizer._price_gap_for_pick", return_value=None), \
             patch.dict("os.environ", {"USE_POSTGRES": "1"}):
            result = await finalize_premarket("US", now=now.astimezone(timezone.utc))
        assert result.get("reason") != "already_finalized"
        assert result["status"] == "failed"
        assert result["reason"] == "rejected_malformed_base"
        job_lookup_mock.assert_not_called()
        save_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_idempotency_fields_do_not_block_a_real_attempt(self):
        """A payload with no prior finalization fields at all (never
        finalized before) must never be mistaken for already-finalized."""
        now = _et(2026, 7, 6, 7, 35)
        payload = self._payload()
        assert "premarket_finalized_for_job_id" not in payload
        with patch("services.daily_picks.get_cached_picks", return_value=payload), \
             patch("services.daily_picks._cache_file", return_value="/dev/null"), \
             patch("services.postgres_store.get_daily_picks_job_by_id", return_value=self._job_row()), \
             patch("services.postgres_store.save_picks_to_db", return_value=True), \
             patch("services.premarket_finalizer._gather_index_proxy", return_value=None), \
             patch("services.premarket_finalizer._price_gap_for_pick", return_value=None), \
             patch.dict("os.environ", {"USE_POSTGRES": "1"}):
            result = await finalize_premarket("US", now=now.astimezone(timezone.utc))
        assert result.get("reason") != "already_finalized"
