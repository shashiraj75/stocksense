"""
Issue: India/US Daily Picks did not run on 2026-09-29. Root cause —
services.postgres_store._reserve_job_with_lease() (shared by
try_reserve_daily_picks_job_with_lease and try_reserve_multibagger_job_with_lease)
returns the literal "started" on a successful reservation (see its own
docstring: "A normal (non-exception) return of 'started' from inside the
block lets conn.transaction() commit as usual."). Both consumers of
try_reserve_daily_picks_job_with_lease — scripts/run_daily_picks_worker.py
and services.daily_picks's watchdog recovery — checked for a literal
"reserved" instead, a string the function never returns. Every reservation
therefore fell into the "unexpected outcome" branch: the dedicated Railway
cron workers exited 5 (Railway marks this CRASHED) before calling
generate_picks at all, on every single scheduled run since the dedicated
workers shipped (2026-09-25); the in-API watchdog recovery path silently
returned {"triggered": False, "reason": "started"} since 2026-07-22,
misleadingly labelling its own success as if it were a reason for not
running. No exception was ever raised — a successful reservation was
misread as a failure — so this was never caught by tests exercising the
already_running/resource_busy branches alone.
"""
from unittest.mock import patch

from services import daily_picks
from scripts import run_daily_picks_worker


def test_worker_proceeds_to_generate_picks_when_reservation_succeeds():
    """The one outcome try_reserve_daily_picks_job_with_lease actually
    returns on success ("started") must not be treated as unexpected."""
    with patch.dict("os.environ", {"USE_POSTGRES": "1"}, clear=False), \
         patch("services.daily_picks.picks_generated_today", return_value=False), \
         patch("services.postgres_store.reconcile_stale_daily_picks_jobs", return_value=0), \
         patch("services.postgres_store.try_reserve_daily_picks_job_with_lease", return_value="started") as reserve, \
         patch("services.daily_picks.generate_picks", return_value={"picks": []}) as generate, \
         patch("services.postgres_store.get_daily_picks_job_by_id", return_value={"status": "completed"}), \
         patch("services.postgres_store.release_heavy_workload_lease"):
        exit_code = run_daily_picks_worker.run("IN")

    reserve.assert_called_once()
    generate.assert_called_once()  # the actual bug: this was never reached
    assert exit_code == 0


def test_worker_still_rejects_a_genuinely_unexpected_outcome():
    with patch.dict("os.environ", {"USE_POSTGRES": "1"}, clear=False), \
         patch("services.daily_picks.picks_generated_today", return_value=False), \
         patch("services.postgres_store.reconcile_stale_daily_picks_jobs", return_value=0), \
         patch("services.postgres_store.try_reserve_daily_picks_job_with_lease", return_value="some_future_outcome"), \
         patch("services.daily_picks.generate_picks") as generate:
        exit_code = run_daily_picks_worker.run("IN")

    generate.assert_not_called()
    assert exit_code == 5


def test_watchdog_recovery_treats_started_as_triggered_not_a_skip_reason():
    with patch.dict("os.environ", {"DAILY_PICKS_EXTERNAL_WORKER_ONLY": "0", "USE_POSTGRES": "1"}, clear=False), \
         patch("services.daily_picks.picks_generated_today", return_value=False), \
         patch("services.postgres_store.get_active_daily_picks_job", return_value=None), \
         patch("services.postgres_store.count_daily_picks_job_attempts_since", return_value=0), \
         patch("services.postgres_store.try_reserve_daily_picks_job_with_lease", return_value="started"), \
         patch.object(daily_picks, "_generating", {}), \
         patch("services.daily_picks._threading.Thread") as thread_cls:
        result = daily_picks.attempt_governed_recovery("IN", reason="missed_scheduled_trigger")

    assert result["triggered"] is True
    thread_cls.assert_called_once()  # the actual bug: recovery never started a run
