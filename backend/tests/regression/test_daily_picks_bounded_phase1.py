"""
Regression tests for the bounded Phase 1 processing fix (2026-07-21 memory-
exhaustion postmortem).

Prior structure: `{pool.submit(_predict_stock, sym, h, market): (sym, h)
for sym, h in tasks}` submitted all ~1188 (candidate x horizon) tasks to a
ThreadPoolExecutor(max_workers=1) up front, retaining every Future object
— plus its args and, once resolved, its full result dict — simultaneously
for the whole Phase 1 run, even though a single worker only ever processes
one at a time. Fixed to a plain sequential loop: at most one task's
result is ever alive at a time, with the identical call order/arguments to
_predict_stock and the identical per-task progress-reporting contract.

Deterministic — source-inspection tests are used for the parts of
_generate_picks_inner that (like its ranking body) aren't practically
mountable end-to-end in a unit test, consistent with this codebase's
existing convention (see test_daily_picks_raw_memory_release.py).
"""
import inspect


def test_phase1_does_not_submit_all_tasks_upfront():
    """The old `{pool.submit(...): (sym, h) for sym, h in tasks}` pattern
    (building a full dict of every task's Future before consuming any) must
    not exist any more."""
    from services.daily_picks import _generate_picks_inner
    src = inspect.getsource(_generate_picks_inner)
    assert "pool.submit(_predict_stock" not in src
    assert "futures = {" not in src


def test_phase1_uses_a_plain_sequential_loop_not_threadpoolexecutor():
    """max_workers=1 never provided real concurrency; ThreadPoolExecutor
    must no longer be used for Phase 1 at all — a bounded sequential loop
    replaces it, so at most one task's Future/result can ever be alive."""
    from services.daily_picks import _generate_picks_inner
    src = inspect.getsource(_generate_picks_inner)
    # Checks actual usage (call syntax), not the bare word — this file's
    # own explanatory comments legitimately mention "ThreadPoolExecutor" in
    # prose describing what was removed.
    assert "ThreadPoolExecutor(" not in src
    assert "as_completed(" not in src
    assert "for sym, h in tasks:" in src
    assert "r = _predict_stock(sym, h, market)" in src


def test_threadpoolexecutor_import_removed_module_wide():
    """Confirms the unused import was actually cleaned up, not just
    unreferenced in this one function."""
    import services.daily_picks as _dp
    src = inspect.getsource(_dp)
    assert "from concurrent.futures import" not in src
    assert "ThreadPoolExecutor(" not in src
    assert "as_completed(" not in src


def test_phase1_result_reference_is_dropped_immediately_each_iteration():
    """Each iteration's local `r` must be explicitly released before moving
    to the next task — nothing should accumulate a growing set of retained
    per-task result objects across the loop."""
    from services.daily_picks import _generate_picks_inner
    src = inspect.getsource(_generate_picks_inner)
    loop_idx = src.index("for sym, h in tasks:")
    del_idx = src.index("del r", loop_idx)
    next_stage_idx = src.index('_try_job_progress(job_id, "ranking"', loop_idx)
    assert loop_idx < del_idx < next_stage_idx


def test_phase1_call_order_and_arguments_to_predict_stock_unchanged():
    """The fix must not change WHAT gets predicted or in what order — only
    how results are retained. tasks is still built the same way, and
    _predict_stock is still called with the same three positional args."""
    from services.daily_picks import _generate_picks_inner
    src = inspect.getsource(_generate_picks_inner)
    assert 'tasks = [(sym, h) for sym in candidates for h in ("short", "medium", "long")]' in src
    assert "_predict_stock(sym, h, market)" in src


def test_memory_guard_checked_during_phase1_and_at_ranking_entry():
    """The circuit breaker must actually be wired into the loop and the
    ranking-entry phase boundary — the exact instant the 2026-07-21
    incident's process was killed — not merely imported and unused."""
    from services.daily_picks import _generate_picks_inner
    src = inspect.getsource(_generate_picks_inner)
    assert "_mem_guard.check(\"phase_1\"" in src
    assert "_mem_guard.check(\"ranking_entry\"" in src
    assert "_mem_guard.check(\"persisting\")" in src


# ─── per-horizon score-snapshot placement (mirrors the ranking-stall fix) ──

def test_score_snapshots_written_per_horizon_not_for_full_raw_dict_up_front():
    from services.daily_picks import _generate_picks_inner
    src = inspect.getsource(_generate_picks_inner)
    assert "_write_score_snapshots({horizon: items}, market)" in src
    assert "_write_score_snapshots(raw, market)" not in src
    raw_none_pos = src.index("raw[horizon] = None")
    snapshot_call_pos = src.index("_write_score_snapshots({horizon: items}, market)")
    assert raw_none_pos < snapshot_call_pos


# ─── memory-failure lifecycle: same guarantees as any other exception ────

def test_generate_picks_marks_failed_when_memory_limit_error_raised():
    """A DailyPicksMemoryLimitError raised from the pipeline must flow
    through generate_picks()'s existing exception handling exactly like
    any other exception: job marked failed, last_error set, completed
    never called, has_today never flipped (no save_picks_to_db success
    path is reached)."""
    import services.daily_picks as _dp
    from services.memory_guard import DailyPicksMemoryLimitError

    from unittest.mock import patch

    with patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
         patch.object(_dp, "_generate_picks_inner",
                      side_effect=DailyPicksMemoryLimitError(
                          "memory usage 81.0% >= abort threshold 80% at phase=ranking_entry"
                      )), \
         patch("services.postgres_store.mark_daily_picks_job_running"), \
         patch("services.postgres_store.mark_daily_picks_job_completed") as mock_completed, \
         patch("services.postgres_store.mark_daily_picks_job_failed") as mock_failed, \
         patch("services.postgres_store.record_daily_picks_job_heartbeat"), \
         patch("services.postgres_store.save_picks_to_db") as mock_save, \
         patch("threading.Thread"):

        _dp.generate_picks("US", job_id="job-mem-limit")

    mock_completed.assert_not_called()
    mock_failed.assert_called_once()
    args, _ = mock_failed.call_args
    assert "memory usage 81.0%" in args[2]
    # The error-path payload save is allowed (writes an empty "no signals"
    # payload with generated_at + error, same as any other crash) but must
    # never be treated as a successful base — mark_completed is the only
    # thing that would make has_today true, and it was never called.
