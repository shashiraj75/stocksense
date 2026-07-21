"""
Regression tests for the ranking-stage lifecycle fix (2026-07-21).

Incident: a US Daily Picks job (54eb7136-...) completed phase_1 (1188/1188),
entered the `ranking` phase, and its owning process was killed
(OOM-consistent signature) ~30-45s later. It sat `status='running'` with a
frozen heartbeat, `last_error=null`, and its heavy-workload lease still
held, because:
  (a) the peak-memory instant in ranking (writing score snapshots for all
      three horizons while `raw` still held all of them in memory) landed
      right where the process died, and
  (b) the only orphan-reconciliation pass runs once at startup with a 6h
      threshold — far too coarse to catch a same-session, minutes-old
      orphan on the very next boot.

This file proves the corrections made for both:
  1. A per-horizon score-snapshot write, reducing peak retained memory.
  2. A ranking-stage watchdog (services.daily_picks._run_with_ranking_watchdog)
     that marks a hung ranking stage failed + releases its lease, without
     ever masking an ordinary in-process exception's own handling.
  3. A periodic (not just startup-only) orphan-reconciliation sweep with a
     much shorter, heartbeat-cadence-aware threshold.

All tests are deterministic and fully mocked — no real DB, no external
providers, no live Daily Picks generation.
"""
import inspect
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


# ─── 1/2/6. _run_with_ranking_watchdog — timeout path ─────────────────────
#
# These tests fake out threading.Timer entirely rather than racing a real
# one against a real time.sleep() — a real-clock race (e.g. timeout=0.05s
# vs. a 0.3s sleep) is nondeterministic under system load (observed
# flaking when run alongside the rest of the suite) and this file's own
# module docstring requires "deterministic offline tests". Faking the
# timer lets the test control exactly when the watchdog fires, with zero
# wall-clock dependency.

class _FakeTimer:
    """Records the callback instead of scheduling it on a real clock;
    `fire()` invokes it synchronously, deterministically, in-thread."""
    instances = []

    def __init__(self, interval, function):
        self.interval = interval
        self.function = function
        self.cancelled = False
        _FakeTimer.instances.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.function()


@pytest.fixture(autouse=True)
def _reset_fake_timer_registry():
    _FakeTimer.instances.clear()
    yield
    _FakeTimer.instances.clear()


def test_watchdog_fires_marks_job_failed_and_releases_lease_on_timeout():
    """A ranking stage that outlives the watchdog timeout is marked failed
    with a bounded, non-generic diagnostic and its lease is released — the
    job never remains falsely 'running'."""
    from services.daily_picks import _run_with_ranking_watchdog, _RankingTimeoutError

    def _hangs():
        # Simulate the watchdog firing while the ranking stage is still
        # "running" — deterministically, via the fake timer, not a real
        # sleep/race.
        _FakeTimer.instances[0].fire()
        return "should never surface"

    with patch("services.daily_picks._threading.Timer", _FakeTimer), \
         patch("services.postgres_store.mark_daily_picks_job_failed") as mock_failed, \
         patch("services.postgres_store.release_heavy_workload_lease") as mock_release:
        with pytest.raises(_RankingTimeoutError):
            _run_with_ranking_watchdog(
                _hangs, job_id="job-timeout-1", market="US", timeout_seconds=600,
            )

    mock_failed.assert_called_once()
    args, _ = mock_failed.call_args
    assert args[0] == "job-timeout-1"
    assert "ranking_timeout" in args[2]
    assert "600" in args[2]
    mock_release.assert_called_once_with("job-timeout-1")


def test_watchdog_lease_release_failure_does_not_hide_original_timeout_failure():
    """A failed lease-release attempt during a watchdog timeout must be
    logged and swallowed, never allowed to erase or replace the already-
    persisted timeout failure."""
    from services.daily_picks import _run_with_ranking_watchdog, _RankingTimeoutError

    def _hangs():
        _FakeTimer.instances[0].fire()

    with patch("services.daily_picks._threading.Timer", _FakeTimer), \
         patch("services.postgres_store.mark_daily_picks_job_failed") as mock_failed, \
         patch("services.postgres_store.release_heavy_workload_lease",
               side_effect=Exception("lease table unavailable")):
        with pytest.raises(_RankingTimeoutError):
            _run_with_ranking_watchdog(
                _hangs, job_id="job-timeout-2", market="US", timeout_seconds=600,
            )

    # The failure was still persisted despite the lease-release error.
    mock_failed.assert_called_once()


# ─── 8. healthy ranking completes normally, watchdog does not interfere ───

def test_watchdog_success_path_returns_result_without_touching_job_state():
    """A ranking stage that finishes well within the timeout returns
    normally; the watchdog must not mark the job failed or release its
    lease in that case."""
    from services.daily_picks import _run_with_ranking_watchdog

    def _fast():
        return {"picks": {"short": [], "medium": [], "long": []}}

    with patch("services.daily_picks._threading.Timer", _FakeTimer), \
         patch("services.postgres_store.mark_daily_picks_job_failed") as mock_failed, \
         patch("services.postgres_store.release_heavy_workload_lease") as mock_release:
        result = _run_with_ranking_watchdog(
            _fast, job_id="job-ok-1", market="US", timeout_seconds=600,
        )

    # fn() returned before the (fake, never-fired) timer would have fired —
    # confirm it was cancelled rather than left dangling.
    assert _FakeTimer.instances[0].cancelled is True

    assert result == {"picks": {"short": [], "medium": [], "long": []}}
    mock_failed.assert_not_called()
    mock_release.assert_not_called()


# ─── ordinary exception (not a timeout) still flows through unmodified ────

def test_watchdog_ordinary_exception_propagates_without_double_marking():
    """An ordinary exception raised inside the ranking closure (not a
    watchdog timeout) must propagate as-is, without the watchdog itself
    calling mark_daily_picks_job_failed — that stays generate_picks()'s own
    top-level except-block's job, exactly as before this fix, so last_error
    keeps reflecting the real underlying exception rather than a generic
    watchdog message."""
    from services.daily_picks import _run_with_ranking_watchdog

    def _raises():
        raise ValueError("bad candidate record")

    with patch("services.daily_picks._threading.Timer", _FakeTimer), \
         patch("services.postgres_store.mark_daily_picks_job_failed") as mock_failed, \
         patch("services.postgres_store.release_heavy_workload_lease") as mock_release:
        with pytest.raises(ValueError, match="bad candidate record"):
            _run_with_ranking_watchdog(
                _raises, job_id="job-exc-1", market="US", timeout_seconds=600,
            )

    mock_failed.assert_not_called()
    mock_release.assert_not_called()


# ─── 9. partial/incomplete ranking results are never published as today's
#        base when the watchdog fires ──────────────────────────────────────

def test_generate_picks_never_marks_completed_when_ranking_watchdog_times_out():
    """End-to-end through generate_picks(): if _generate_picks_inner raises
    _RankingTimeoutError (the watchdog fired), generate_picks() must land in
    its failure path — mark_daily_picks_job_completed must never be called,
    so an incomplete ranking result can never be published as a completed
    base."""
    import services.daily_picks as _dp
    from services.daily_picks import _RankingTimeoutError

    with patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
         patch.object(_dp, "_generate_picks_inner",
                      side_effect=_RankingTimeoutError("ranking exceeded 600s watchdog")), \
         patch("services.postgres_store.mark_daily_picks_job_running"), \
         patch("services.postgres_store.mark_daily_picks_job_completed") as mock_completed, \
         patch("services.postgres_store.mark_daily_picks_job_failed") as mock_failed, \
         patch("services.postgres_store.record_daily_picks_job_heartbeat"), \
         patch("services.postgres_store.save_picks_to_db"), \
         patch("threading.Thread"):

        _dp.generate_picks("US", job_id="job-e2e-timeout")

    mock_completed.assert_not_called()
    mock_failed.assert_called_once()


# ─── 10. per-horizon memory fix ────────────────────────────────────────────

def test_score_snapshots_written_per_horizon_not_for_full_raw_dict_up_front():
    """The 2026-07-21 postmortem found the process killed while
    _write_score_snapshots ran over the FULL 3-horizon `raw` dict, held
    entirely in memory, before any horizon's slot was released. The fix
    writes snapshots per-horizon, immediately after that horizon's slot in
    `raw` is released — verified by source inspection: the call site must
    use a single-horizon dict, not the raw whole-`raw` call, and must appear
    inside the per-horizon loop body (after `raw[horizon] = None`)."""
    from services.daily_picks import _generate_picks_inner

    src = inspect.getsource(_generate_picks_inner)

    assert "_write_score_snapshots({horizon: items}, market)" in src
    assert "_write_score_snapshots(raw, market)" not in src

    raw_none_pos = src.index("raw[horizon] = None")
    snapshot_call_pos = src.index("_write_score_snapshots({horizon: items}, market)")
    assert raw_none_pos < snapshot_call_pos, (
        "score snapshots for a horizon must be written AFTER that horizon's "
        "slot in `raw` is released, not before"
    )


def test_ranking_watchdog_wraps_the_rank_select_and_persist_closure():
    """The ranking/selection/persistence closure must actually run through
    _run_with_ranking_watchdog when a durable job_id exists — otherwise the
    watchdog exists but is never wired in."""
    from services.daily_picks import _generate_picks_inner

    src = inspect.getsource(_generate_picks_inner)
    assert "_run_with_ranking_watchdog(" in src
    assert "_rank_select_and_persist" in src


# ─── 5/12. periodic reconciliation with a shorter threshold ───────────────

def _mock_pool_capture():
    mock_conn = MagicMock()
    stale_cursor = MagicMock()
    stale_cursor.fetchall.return_value = []
    mock_conn.execute.return_value = stale_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn
    return mock_pool, mock_conn


def test_reconcile_default_call_unchanged_uses_six_hour_threshold():
    """Calling reconcile_stale_daily_picks_jobs() with no argument (the
    existing startup call site) must keep embedding the original 6h
    threshold, unchanged from before this fix."""
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    mock_pool, mock_conn = _mock_pool_capture()
    with patch("services.postgres_store._get_pool", return_value=mock_pool):
        reconcile_stale_daily_picks_jobs()
    sql_text = mock_conn.execute.call_args_list[0][0][0]
    assert "6 hours" in sql_text


def test_reconcile_accepts_shorter_periodic_interval_override():
    """The periodic sweep call site passes a short INTERVAL literal; the
    function must use exactly that literal instead of the 6h default."""
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    mock_pool, mock_conn = _mock_pool_capture()
    with patch("services.postgres_store._get_pool", return_value=mock_pool):
        reconcile_stale_daily_picks_jobs(stale_interval="10 minutes")
    sql_text = mock_conn.execute.call_args_list[0][0][0]
    assert "10 minutes" in sql_text
    assert "6 hours" not in sql_text


def test_periodic_stale_interval_has_safety_margin_over_heartbeat_cadence():
    """The periodic sweep threshold must sit far above the heartbeat write
    cadence (30s, in services.daily_picks._heartbeat_loop) so a genuinely
    healthy job — which keeps writing a heartbeat every 30s regardless of
    total run length — can never be interrupted by this sweep."""
    from services.postgres_store import _DAILY_PICKS_PERIODIC_STALE_INTERVAL
    import re

    match = re.match(r"(\d+)\s+minutes?", _DAILY_PICKS_PERIODIC_STALE_INTERVAL)
    assert match, f"unexpected interval literal format: {_DAILY_PICKS_PERIODIC_STALE_INTERVAL}"
    minutes = int(match.group(1))
    heartbeat_cadence_seconds = 30
    assert minutes * 60 >= 10 * heartbeat_cadence_seconds, (
        "periodic sweep threshold must be at least 10x the heartbeat cadence "
        "to avoid ever touching a healthy job"
    )
    # Also stay well below the original 6h startup-only threshold — this is
    # a fast supplementary sweep, not a replacement for it.
    assert minutes < 6 * 60


def test_daily_picks_periodic_reconciliation_loop_wired_into_lifespan():
    """The periodic sweep must actually be started as a background task at
    app startup, same wiring pattern as the other periodic loops."""
    import api.main as main_mod
    src = inspect.getsource(main_mod)
    assert "_daily_picks_orphan_reconciliation_loop" in src
    assert "asyncio.create_task(_daily_picks_orphan_reconciliation_loop())" in src


def test_periodic_reconciliation_loop_uses_the_short_interval_constant():
    """The periodic loop must call reconcile_stale_daily_picks_jobs with
    _DAILY_PICKS_PERIODIC_STALE_INTERVAL — not the bare 6h default — or the
    whole point of adding it is silently lost."""
    import api.main as main_mod
    src = inspect.getsource(main_mod._daily_picks_orphan_reconciliation_loop)
    assert "_DAILY_PICKS_PERIODIC_STALE_INTERVAL" in src
    assert "reconcile_stale_daily_picks_jobs" in src


# ─── 11. no automatic duplicate US replacement is ever created ────────────

def test_reconciliation_never_starts_a_replacement_job():
    """Neither the reconciliation function nor the periodic loop that calls
    it may ever start a new generation run — orphan cleanup only frees the
    active-job slot; a human (or a separate, explicit trigger) must start
    any replacement."""
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    import api.main as main_mod

    reconcile_src = inspect.getsource(reconcile_stale_daily_picks_jobs)
    loop_src = inspect.getsource(main_mod._daily_picks_orphan_reconciliation_loop)

    for forbidden in ("generate_picks", "try_reserve_daily_picks_job", "/api/picks/generate"):
        assert forbidden not in reconcile_src
        assert forbidden not in loop_src


def test_reconcile_periodic_sweep_still_only_targets_queued_and_running():
    """The parameterized function must keep the exact same WHERE clause
    scope as before — only active rows, never a terminal one."""
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    src = inspect.getsource(reconcile_stale_daily_picks_jobs)
    assert "WHERE status IN ('queued', 'running')" in src
