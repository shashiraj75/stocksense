"""
US Daily Picks generation-reliability incident (2026-07-22) — bounded,
non-overlapping governed recovery ("watchdog") tests.

`attempt_governed_recovery()` is called from the US premarket finalizer
(services/premarket_finalizer.py) when it finds today's base payload
missing or stale — the finalizer already runs on a real GitHub Actions
cron well after the base's expected completion time, so it doubles as
this watchdog's check point instead of a brand-new scheduling mechanism.
It reuses the EXACT SAME durable reservation path as POST
/api/picks/generate (try_reserve_daily_picks_job_with_lease), so duplicate-
job protection is automatic and identical to the HTTP endpoint's.
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


def test_recovery_not_triggered_when_already_fresh():
    import services.daily_picks as dp
    with patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
         patch.object(dp, "picks_generated_today", return_value=True):
        result = dp.attempt_governed_recovery("US", reason="skipped_stale_base")
    assert result == {"triggered": False, "reason": "already_fresh"}


def test_recovery_not_triggered_when_in_memory_generating_flag_set():
    import services.daily_picks as dp
    with patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
         patch.object(dp, "picks_generated_today", return_value=False), \
         patch.dict(dp._generating, {"US": True}):
        result = dp.attempt_governed_recovery("US", reason="skipped_no_base")
    assert result == {"triggered": False, "reason": "already_running"}


def test_recovery_not_triggered_when_a_durable_active_job_exists():
    """Even if the in-memory flag is clear (e.g. a different process
    instance holds it, or this process just restarted), the durable
    daily_picks_jobs check must independently prevent overlap."""
    import services.daily_picks as dp
    with patch.object(dp, "picks_generated_today", return_value=False), \
         patch.dict(dp._generating, {"US": False}), \
         patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
         patch("services.postgres_store.get_active_daily_picks_job",
               return_value={"job_id": "already-running-job"}), \
         patch("services.postgres_store.count_daily_picks_job_attempts_since"):
        result = dp.attempt_governed_recovery("US", reason="skipped_stale_base")
    assert result == {"triggered": False, "reason": "already_running"}


def test_recovery_not_triggered_when_max_daily_attempts_reached():
    import services.daily_picks as dp
    with patch.object(dp, "picks_generated_today", return_value=False), \
         patch.dict(dp._generating, {"US": False}), \
         patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
         patch("services.postgres_store.get_active_daily_picks_job", return_value=None), \
         patch("services.postgres_store.count_daily_picks_job_attempts_since", return_value=3):
        result = dp.attempt_governed_recovery("US", reason="skipped_stale_base")
    assert result["triggered"] is False
    assert result["reason"] == "max_attempts_reached"
    assert result["attempts_today"] == 3


def test_recovery_triggers_exactly_one_bounded_job_when_genuinely_missing():
    """The success path: no active job, under the attempt cap — a new job
    is reserved via the SAME lease-based reservation the HTTP endpoint
    uses, and generation is launched in a background thread (never
    synchronously, so the caller — the finalizer's async handler — returns
    promptly)."""
    import services.daily_picks as dp
    with patch.object(dp, "picks_generated_today", return_value=False), \
         patch.dict(dp._generating, {"US": False}), \
         patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
         patch("services.postgres_store.get_active_daily_picks_job", return_value=None), \
         patch("services.postgres_store.count_daily_picks_job_attempts_since", return_value=1), \
         patch("services.postgres_store.try_reserve_daily_picks_job_with_lease",
               return_value="reserved") as mock_reserve, \
         patch("threading.Thread") as mock_thread:
        result = dp.attempt_governed_recovery("US", reason="skipped_stale_base")

    assert result["triggered"] is True
    assert "job_id" in result
    mock_reserve.assert_called_once()
    mock_thread.assert_called_once()
    mock_thread.return_value.start.assert_called_once()
    # Must never call generate_picks() synchronously on the calling thread —
    # only ever via the Thread it constructs.


def test_recovery_reports_resource_busy_without_starting_a_thread():
    import services.daily_picks as dp
    with patch.object(dp, "picks_generated_today", return_value=False), \
         patch.dict(dp._generating, {"US": False}), \
         patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
         patch("services.postgres_store.get_active_daily_picks_job", return_value=None), \
         patch("services.postgres_store.count_daily_picks_job_attempts_since", return_value=0), \
         patch("services.postgres_store.try_reserve_daily_picks_job_with_lease",
               return_value="resource_busy"), \
         patch("threading.Thread") as mock_thread:
        result = dp.attempt_governed_recovery("US", reason="skipped_no_base")

    assert result == {"triggered": False, "reason": "resource_busy"}
    mock_thread.assert_not_called()


def test_recovery_bounded_by_max_constant_not_unbounded():
    import services.daily_picks as dp
    assert dp._MAX_DAILY_RECOVERY_ATTEMPTS == 3
    assert dp._MAX_DAILY_RECOVERY_ATTEMPTS < 10, "must be a small, genuinely bounded number"


# ─────────────────────────────────────────────────────────────────────────
# premarket_finalizer wiring — recovery hooked on missing/stale base only
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_finalizer_triggers_recovery_on_missing_base():
    import services.premarket_finalizer as pf
    with patch("services.daily_picks.get_cached_picks", return_value=None), \
         patch("services.daily_picks.attempt_governed_recovery",
               return_value={"triggered": True, "job_id": "recovered"}) as mock_recovery, \
         patch("services.premarket_finalizer.in_premarket_window", return_value=True):
        result = await pf.finalize_premarket("US", now=datetime.now(timezone.utc))

    assert result["reason"] == "skipped_no_base"
    mock_recovery.assert_called_once_with("US", reason="skipped_no_base")
    assert result["recovery"] == {"triggered": True, "job_id": "recovered"}


@pytest.mark.asyncio
async def test_finalizer_triggers_recovery_on_stale_base():
    import services.premarket_finalizer as pf
    stale_payload = {
        "market": "US",
        "picks": {"short": [{"symbol": "AAPL"}], "medium": [], "long": []},
        "generated_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        "source_job_id": "old-job",
    }
    with patch("services.daily_picks.get_cached_picks", return_value=stale_payload), \
         patch("services.daily_picks.attempt_governed_recovery",
               return_value={"triggered": True, "job_id": "recovered"}) as mock_recovery, \
         patch("services.premarket_finalizer.in_premarket_window", return_value=True):
        result = await pf.finalize_premarket("US", now=datetime.now(timezone.utc))

    assert result["reason"] == "skipped_stale_base"
    mock_recovery.assert_called_once_with("US", reason="skipped_stale_base")


@pytest.mark.asyncio
async def test_finalizer_does_not_trigger_recovery_for_malformed_base():
    """A malformed base is a data-integrity problem a blind retry could
    mask — recovery must NOT be attempted for this outcome."""
    import services.premarket_finalizer as pf
    malformed_payload = {
        "market": "NOT_US",  # triggers rejected_malformed_base
        "picks": {"short": [{"symbol": "AAPL"}], "medium": [], "long": []},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_job_id": "job-x",
    }
    with patch("services.daily_picks.get_cached_picks", return_value=malformed_payload), \
         patch("services.daily_picks.attempt_governed_recovery") as mock_recovery, \
         patch("services.premarket_finalizer.in_premarket_window", return_value=True):
        result = await pf.finalize_premarket("US", now=datetime.now(timezone.utc))

    assert result["reason"] == "rejected_malformed_base"
    mock_recovery.assert_not_called()


@pytest.mark.asyncio
async def test_finalizer_does_not_trigger_recovery_when_base_is_healthy():
    import services.premarket_finalizer as pf
    fresh_payload = {
        "market": "US",
        "picks": {"short": [{"symbol": "AAPL"}], "medium": [], "long": []},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_job_id": "job-fresh",
        "premarket_finalized_at": None,
    }
    with patch("services.daily_picks.get_cached_picks", return_value=fresh_payload), \
         patch("services.daily_picks.attempt_governed_recovery") as mock_recovery, \
         patch("services.premarket_finalizer.in_premarket_window", return_value=True), \
         patch("services.premarket_finalizer._validate_source_job_state",
               return_value=(None, {})), \
         patch("services.premarket_finalizer._gather_index_proxy", return_value=None), \
         patch("services.postgres_store.save_picks_to_db", return_value=True), \
         patch("os.getenv", return_value="1"):
        await pf.finalize_premarket("US", now=datetime.now(timezone.utc))

    mock_recovery.assert_not_called()
