"""
Regression tests for bounded, horizon-scoped Phase 1 processing.

History:
- 2026-07-21 memory-exhaustion postmortem: `{pool.submit(_predict_stock, sym,
  h, market): (sym, h) for sym, h in tasks}` submitted all ~1188 (candidate x
  horizon) tasks to a ThreadPoolExecutor(max_workers=1) up front, retaining
  every Future object simultaneously for the whole Phase 1 run. Fixed to a
  plain sequential loop over a pre-built `tasks` list covering all 3
  horizons at once — bounded WITHIN Phase 1, but Phase 1 itself still built
  and held all 3 horizons' full candidate pools (raw, ~1,191 rich dicts)
  before Phases 3-6 released one horizon at a time.
- 2026-07-22 US Daily Picks generation-reliability incident: Phase 1 was
  made horizon-major so only one horizon's result pool was resident at a
  time.
- 2026-07-23/24 incident: the horizon-major ordering was found to have
  tripled SEC companyfacts download/parse churn (each symbol re-visited
  ~400 tasks after its facts were evicted from the 25-entry cache),
  feeding the allocator-fragmentation RSS ratchet that aborted both runs.
  Phase 1 is now CHUNKED CANDIDATE-MAJOR: candidates are processed in
  chunks no larger than the SEC facts-cache capacity, each symbol's three
  horizons run consecutively (facts fetched once per symbol, warm for
  horizons 2/3), and the three horizons' slim result pools (~12 KB each,
  ~15 MB total — measured never to be the dominant memory term) accumulate
  across chunks before Phases 3-6 run per horizon exactly as before,
  releasing each pool after its horizon's ranking/persistence completes.
  Still strictly sequential — no pools, no gather, no futures.

Deterministic — source-inspection tests are used for the parts of
_generate_picks_inner that (like its ranking body) aren't practically
mountable end-to-end in a unit test, consistent with this codebase's
existing convention (see test_daily_picks_raw_memory_release.py's history).
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
    must not be used for Phase 1 — a bounded sequential loop replaces it,
    so at most one task's Future/result can ever be alive. 2026-07-23/24:
    the loop is chunked candidate-major — an outer chunk loop, a symbol
    loop over the chunk's slice, and an innermost consecutive-horizons
    loop."""
    from services.daily_picks import _generate_picks_inner
    src = inspect.getsource(_generate_picks_inner)
    assert "ThreadPoolExecutor(" not in src
    assert "as_completed(" not in src
    assert "asyncio.gather(" not in src
    assert "for sym in candidates[_chunk_start:_chunk_start + _chunk_size]:" in src
    assert "r = _predict_stock(sym, horizon, market)" in src


def test_threadpoolexecutor_import_removed_module_wide():
    """Confirms the unused import was actually cleaned up, not just
    unreferenced in this one function."""
    import services.daily_picks as _dp
    src = inspect.getsource(_dp)
    assert "from concurrent.futures import" not in src
    assert "ThreadPoolExecutor(" not in src
    assert "as_completed(" not in src


def test_phase1_no_flat_all_horizons_task_list_or_raw_dict():
    """The flat `tasks = [(sym, h) for sym in candidates for h in (...)]`
    list and the `raw: dict[str, list] = {"short": [], ...}` dict must not
    return. 2026-07-23/24: the chunked candidate-major loop accumulates
    into `_horizon_items` (slim ~12 KB result dicts only — an explicit,
    measured, accepted ~15 MB trade documented at the loop), and each
    horizon's pool is popped out and released after that horizon's own
    Phases 3-6."""
    from services.daily_picks import _generate_picks_inner
    src = inspect.getsource(_generate_picks_inner)
    assert 'tasks = [(sym, h) for sym in candidates for h in' not in src
    assert 'raw: dict[str, list] = {"short": [], "medium": [], "long": []}' not in src
    assert "_horizon_items: dict[str, list]" in src
    assert "items: list = _horizon_items.pop(horizon)" in src


def test_phase1_result_reference_is_dropped_immediately_each_iteration():
    """Each iteration's local `r` must be explicitly released before moving
    to the next task — nothing should accumulate a growing set of retained
    per-task result objects across the loop (beyond the slim accepted
    _horizon_items accumulator itself)."""
    from services.daily_picks import _generate_picks_inner
    src = inspect.getsource(_generate_picks_inner)
    loop_idx = src.index("for sym in candidates[_chunk_start:")
    del_idx = src.index("del r", loop_idx)
    next_stage_idx = src.index('_try_job_progress(job_id, "ranking"', loop_idx)
    assert loop_idx < del_idx < next_stage_idx


def test_phase1_call_order_and_arguments_to_predict_stock_unchanged():
    """The fix must not change WHAT gets predicted — only the order results
    are computed and how long provider payloads stay cached.
    _predict_stock is still called with the same three positional args;
    the chunked candidate-major nesting cannot change the SET of
    (symbol, horizon) calls or their individual arguments — see
    test_daily_picks_horizon_bounded_memory.py for the full output-
    equivalence proof."""
    from services.daily_picks import _generate_picks_inner
    src = inspect.getsource(_generate_picks_inner)
    assert 'for horizon in ("short", "medium", "long"):' in src
    assert "for sym in candidates[_chunk_start:_chunk_start + _chunk_size]:" in src
    assert "_predict_stock(sym, horizon, market)" in src


def test_memory_guard_checked_during_phase1_and_at_ranking_entry():
    """The circuit breaker must actually be wired into the loop and the
    per-horizon ranking-entry phase boundary — the exact instant the
    2026-07-21/07-22 incidents' processes were killed/aborted — not merely
    imported and unused. 2026-07-22: ranking_entry is now checked once per
    horizon (inside the loop), not once globally after all Phase 1 work."""
    from services.daily_picks import _generate_picks_inner
    src = inspect.getsource(_generate_picks_inner)
    assert "_mem_guard.check(\"phase_1\"" in src
    assert "_mem_guard.check(\"ranking_entry\"" in src
    assert "_mem_guard.check(\"persisting\")" in src
    loop_idx = src.index('for horizon in ("short", "medium", "long"):')
    ranking_entry_idx = src.index('_mem_guard.check("ranking_entry"')
    assert loop_idx < ranking_entry_idx, "ranking_entry check must be inside the per-horizon loop"


# ─── per-horizon score-snapshot placement (mirrors the ranking-stall fix) ──

def test_score_snapshots_written_per_horizon_not_for_full_raw_dict_up_front():
    from services.daily_picks import _generate_picks_inner
    src = inspect.getsource(_generate_picks_inner)
    assert "_write_score_snapshots({horizon: items}, market)" in src
    assert "_write_score_snapshots(raw, market)" not in src


def test_score_snapshots_written_after_this_horizons_ranking_entry_check():
    """2026-07-22: score snapshots for a horizon are written after that
    horizon's own Phase-1 scoring AND ranking_entry memory check complete —
    never before, and never using another horizon's items."""
    from services.daily_picks import _generate_picks_inner
    src = inspect.getsource(_generate_picks_inner)
    ranking_entry_pos = src.index('_mem_guard.check("ranking_entry"')
    snapshot_call_pos = src.index("_write_score_snapshots({horizon: items}, market)")
    assert ranking_entry_pos < snapshot_call_pos


# ─── memory-failure lifecycle: same guarantees as any other exception ────

def test_generate_picks_marks_failed_when_memory_limit_error_raised():
    """A DailyPicksMemoryLimitError raised from the pipeline must flow
    through generate_picks()'s existing exception handling exactly like
    any other exception: job marked failed, last_error set, completed
    never called, has_today never flipped.

    2026-07-22 US Daily Picks generation-reliability incident: the
    error-path payload is NO LONGER saved to disk or to
    daily_picks_cache — that was the exact defect that let a failed run's
    empty payload silently shadow the last genuinely successful one. The
    failure is durably recorded exclusively via mark_daily_picks_job_failed
    (daily_picks_jobs); save_picks_to_db must not be called at all on this
    path."""
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
    # 2026-07-22: unlike before, the error-path payload is NEVER saved —
    # neither to disk nor to Postgres. mark_completed is the only thing
    # that would make has_today true, and it was never called; save_picks_to_db
    # must also never be called on this path (it used to be, "best-effort",
    # which is exactly what silently overwrote the last successful payload).
    mock_save.assert_not_called()
