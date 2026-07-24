"""
2026-07-23/24 US Daily Picks memory incident — chunk-boundary coverage and
progress truthfulness for the chunked candidate-major Phase 1.

Candidate counts straddling every interesting boundary of the 25-entry
chunk size (empty, single, one-below, exact, one-above, two chunks, and
the production-scale 400) must all score every candidate exactly once per
horizon, and the durable phase_1 progress reported along the way must be
monotonically increasing from 0 to candidates x 3 with no chunk-boundary
reset or overcount.
"""
from unittest.mock import MagicMock, patch

import pytest

_FAKE_REGIME = {"regime_id": 0, "label": "neutral", "description": "", "weight_multipliers": {}}


def _run_and_collect(candidates):
    """Run _generate_picks_inner with a call-logging _predict_stock stub;
    returns (calls, progress_events) where progress_events is every
    (processed, total) reported for phase name 'phase_1'."""
    import services.daily_picks as dp

    calls: list[tuple[str, str]] = []
    progress: list[tuple[int, int]] = []

    def fake_predict(sym, horizon, market):
        calls.append((sym, horizon))
        return None

    def fake_progress(job_id, phase, processed, total, **kw):
        if phase == "phase_1" and processed is not None:
            progress.append((processed, total))

    with patch("services.daily_picks._bulk_screen",
               return_value=(list(candidates), 10, "screener", False, 20,
                             {"universe_candidate_count": len(candidates), "attempts": 1,
                              "reason": "healthy_screener_universe", "error_category": "none",
                              "tier_map": {}})), \
         patch("services.daily_picks._predict_stock", side_effect=fake_predict), \
         patch("services.daily_picks._try_job_progress", side_effect=fake_progress), \
         patch("services.daily_picks._write_score_snapshots"), \
         patch("services.daily_picks.yf", MagicMock()), \
         patch("services.alpha_engine.regime_cluster.detect_regime", return_value=_FAKE_REGIME), \
         patch("services.alpha_engine.ic_engine.get_production_ic_weights", return_value={}), \
         patch("services.alpha_engine.store.log_prediction"), \
         patch("services.global_context.get_global_context", return_value={}), \
         patch("os.getenv", return_value=None), \
         patch("builtins.open", MagicMock()), \
         patch("json.dump"):
        dp._generate_picks_inner("US", job_id="job-chunk-test")

    return calls, progress


@pytest.mark.parametrize("n", [0, 1, 24, 25, 26, 50, 400])
def test_no_candidate_skipped_or_duplicated_at_any_chunk_boundary(n):
    candidates = [f"SYM{i:03d}" for i in range(n)]
    calls, progress = _run_and_collect(candidates)

    expected = {(sym, h) for sym in candidates for h in ("short", "medium", "long")}
    assert set(calls) == expected, f"n={n}: call set mismatch"
    assert len(calls) == 3 * n, f"n={n}: expected {3*n} calls, got {len(calls)} (duplicates?)"

    # Progress truthfulness: total is always candidates x 3 (after momentum
    # filtering — the stubbed _bulk_screen already returns the filtered
    # list, exactly like production), processed is monotonically
    # non-decreasing, ends at total, and never exceeds it (no overcount at
    # chunk boundaries, no reset).
    total = 3 * n
    assert progress[0] == (0, total), f"n={n}: phase_1 must start at 0/{total}"
    processed_seq = [p for p, _ in progress]
    assert all(t == total for _, t in progress), f"n={n}: total must never change mid-phase"
    assert processed_seq == sorted(processed_seq), f"n={n}: processed must be monotonic"
    assert processed_seq[-1] == total, f"n={n}: final processed must equal total"
    assert max(processed_seq) <= total, f"n={n}: processed overcounted past total"
    # Every task increments progress exactly once: 0 plus one event per task.
    assert len(progress) == total + 1, f"n={n}: expected {total + 1} phase_1 events"


def test_chunk_size_is_bounded_by_sec_facts_cache_capacity():
    """The chunk size must track the SEC facts cache's own capacity so the
    two can never silently drift apart (a chunk larger than the cache
    would reintroduce silent refetch churn)."""
    from services.daily_picks import _phase1_chunk_size
    from services.sec_edgar_adapter import _FACTS_CACHE_MAX

    assert _phase1_chunk_size() == _FACTS_CACHE_MAX
    assert _phase1_chunk_size() <= 25


def test_chunk_size_falls_back_safely_if_adapter_import_fails():
    import services.daily_picks as dp

    with patch.dict("sys.modules", {"services.sec_edgar_adapter": None}):
        assert dp._phase1_chunk_size() == 25
