"""
2026-08-24 sustained-high-baseline remediation.

Railway production evidence: service memory held steady at ~4.4-4.9GB of an
8GB limit and did not fall when CPU went idle between Daily Picks runs.
Traced to services/memory_guard.py's `release_memory()` (safe-cache clear +
gc.collect + malloc_trim — the same mechanism already proven, via its
run-start invocation, to recover >1GB) only ever being invoked at the START
of the NEXT run. A successful run's own end called `log_summary()`, which is
read-only observability, not a cleanup; a run that raised skipped past even
that. Whatever a run's peak was — safe-cache contents plus allocator-arena
fragmentation from Phase 1's DataFrames/models — stayed resident for the
entire idle gap until the next run's run-start cleanup picked it up.

Fix: generate_picks()'s `finally:` block (which already runs on every
terminal path — success or exception, matching the heartbeat-stop and
job-status-terminal contract already covered elsewhere) now also invokes a
fresh MemoryCircuitBreaker's `release_memory("generate_picks_end")` before
Phase 8/Telegram. These tests verify that call happens on every terminal
path, is a no-op with respect to generation's own success/failure contract,
and can never itself crash or block a run.
"""
from unittest.mock import MagicMock, patch

from services.memory_guard import MemoryCircuitBreaker as _RealBreaker


def _breaker_factory(*, released=None, released_stages=None):
    """A stand-in for the MemoryCircuitBreaker constructor that records
    every release_memory() stage invoked on any instance it creates,
    driven by a real breaker (fixed low, non-limit-crossing fake reader)
    so run-start/abort behavior is unaffected."""
    def factory(market, job_id=None, **kw):
        breaker = _RealBreaker(market, job_id=job_id, reader=lambda: (100, 1000))
        if released_stages is not None:
            _orig = breaker.release_memory

            def _tracked(stage, *a, **kw):
                released_stages.append((market, stage))
                return _orig(stage, *a, **kw)

            breaker.release_memory = _tracked
        return breaker
    return factory


def test_run_end_release_memory_invoked_on_successful_generation():
    import services.daily_picks as dp

    fake_payload = {"generated_at": "2026-08-24T00:00:00Z", "picks": {}}
    stages = []

    with patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
         patch.object(dp, "_generate_picks_inner",
                      return_value=(fake_payload, None)), \
         patch("services.memory_guard.MemoryCircuitBreaker",
               side_effect=_breaker_factory(released_stages=stages)), \
         patch("services.postgres_store.mark_daily_picks_job_running"), \
         patch("services.postgres_store.mark_daily_picks_job_completed"), \
         patch("services.postgres_store.mark_daily_picks_job_failed"), \
         patch("services.postgres_store.record_daily_picks_job_heartbeat"), \
         patch("threading.Thread"):
        dp.generate_picks("IN", job_id="job-end-1")

    assert ("IN", "generate_picks_end") in stages


def test_run_end_release_memory_invoked_when_inner_raises():
    """The failure path must also release — previously it skipped cleanup
    entirely, leaving a failed run's partial allocations resident until the
    next run's own run-start cleanup."""
    import services.daily_picks as dp

    stages = []

    with patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
         patch.object(dp, "_generate_picks_inner",
                      side_effect=RuntimeError("provider stall")), \
         patch("services.memory_guard.MemoryCircuitBreaker",
               side_effect=_breaker_factory(released_stages=stages)), \
         patch("services.postgres_store.mark_daily_picks_job_running"), \
         patch("services.postgres_store.mark_daily_picks_job_completed"), \
         patch("services.postgres_store.mark_daily_picks_job_failed") as mock_failed, \
         patch("services.postgres_store.record_daily_picks_job_heartbeat"), \
         patch("threading.Thread"):
        result = dp.generate_picks("US", job_id="job-end-2")

    assert ("US", "generate_picks_end") in stages
    # The pre-existing failure-path contract (job marked failed, error
    # payload returned) must be unaffected by adding this cleanup step.
    mock_failed.assert_called_once()
    assert result.get("error") is not None


def test_run_end_release_memory_invoked_with_no_job_id():
    import services.daily_picks as dp

    fake_payload = {"generated_at": "2026-08-24T00:00:00Z", "picks": {}}
    stages = []

    with patch.dict("os.environ", {}, clear=True), \
         patch.object(dp, "_generate_picks_inner",
                      return_value=(fake_payload, None)), \
         patch("services.memory_guard.MemoryCircuitBreaker",
               side_effect=_breaker_factory(released_stages=stages)), \
         patch("threading.Thread"):
        dp.generate_picks("IN", job_id=None)

    assert ("IN", "generate_picks_end") in stages


def test_run_end_release_memory_failure_does_not_crash_generation():
    """If constructing the end-of-run breaker itself raises (not just a
    failure inside release_memory, which memory_guard already swallows
    internally), generate_picks must still complete and return normally —
    this cleanup step must never become a new source of job failure."""
    import services.daily_picks as dp

    fake_payload = {"generated_at": "2026-08-24T00:00:00Z", "picks": {}}

    with patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
         patch.object(dp, "_generate_picks_inner",
                      return_value=(fake_payload, None)), \
         patch("services.memory_guard.MemoryCircuitBreaker",
               side_effect=RuntimeError("unexpected construction failure")), \
         patch("services.postgres_store.mark_daily_picks_job_running"), \
         patch("services.postgres_store.mark_daily_picks_job_completed") as mock_completed, \
         patch("services.postgres_store.mark_daily_picks_job_failed") as mock_failed, \
         patch("services.postgres_store.record_daily_picks_job_heartbeat"), \
         patch("threading.Thread"):
        result = dp.generate_picks("IN", job_id="job-end-4")

    # The job's own success/failure status must reflect _generate_picks_inner's
    # real outcome (success here), not the unrelated cleanup-step failure.
    mock_completed.assert_not_called()  # persisted_at is None -> marked failed
    mock_failed.assert_called_once()
    assert result == fake_payload
