"""
Pre-merge hardening for the 2026-07-23/24 memory remediation (PR #20):
run-start abort enforcement wired into _generate_picks_inner.

If the US run-start cleanup (safe-cache clear + gc + malloc_trim) leaves
the container at/above the 80% abort threshold, the run must fail fast —
before universe screening, Phase 1 construction, or a single
_predict_stock call — instead of executing ~30 expensive prediction tasks
first (check()'s next evaluation point). And successful runs must still
log their normal run_end summary exactly once.
"""
from unittest.mock import MagicMock, patch

import pytest

import services.memory_guard as mg

_FAKE_REGIME = {"regime_id": 0, "label": "neutral", "description": "", "weight_multipliers": {}}

_RealBreaker = mg.MemoryCircuitBreaker


def _breaker_factory(used, limit):
    """A stand-in for the MemoryCircuitBreaker constructor producing a real
    breaker driven by a fixed fake reader."""
    def factory(market, job_id=None, **kw):
        return _RealBreaker(market, job_id=job_id, reader=lambda: (used, limit))
    return factory


def _run_us_generation(breaker_factory):
    """Run _generate_picks_inner('US') with the standard deterministic stub
    set; returns (bulk_screen_mock, predict_mock, json_dump_mock)."""
    import services.daily_picks as dp

    with patch("services.memory_guard.MemoryCircuitBreaker", side_effect=breaker_factory), \
         patch("services.memory_guard.clear_safe_caches", return_value=[]), \
         patch("services.memory_guard.malloc_trim", return_value=(False, False)), \
         patch("services.daily_picks._bulk_screen",
               return_value=(["AAA", "BBB"], 10, "screener", False, 20,
                             {"universe_candidate_count": 2, "attempts": 1,
                              "reason": "healthy_screener_universe", "error_category": "none",
                              "tier_map": {}})) as mock_screen, \
         patch("services.daily_picks._predict_stock", return_value=None) as mock_predict, \
         patch("services.daily_picks._write_score_snapshots"), \
         patch("services.daily_picks.yf", MagicMock()), \
         patch("services.alpha_engine.regime_cluster.detect_regime", return_value=_FAKE_REGIME), \
         patch("services.alpha_engine.ic_engine.get_production_ic_weights", return_value={}), \
         patch("services.alpha_engine.store.log_prediction"), \
         patch("services.global_context.get_global_context", return_value={}), \
         patch("os.getenv", return_value=None), \
         patch("builtins.open", MagicMock()), \
         patch("json.dump") as mock_dump:
        dp._generate_picks_inner("US", job_id="job-run-start")
    return mock_screen, mock_predict, mock_dump


def test_us_run_aborts_immediately_when_cleanup_leaves_memory_at_81_pct():
    from services.memory_guard import DailyPicksMemoryLimitError

    with pytest.raises(DailyPicksMemoryLimitError) as exc_info:
        _run_us_generation(_breaker_factory(81, 100))

    assert "run_start_post_cleanup" in str(exc_info.value)


def test_us_run_start_abort_happens_before_any_expensive_work():
    from services.memory_guard import DailyPicksMemoryLimitError
    import services.daily_picks as dp

    with patch("services.memory_guard.MemoryCircuitBreaker",
               side_effect=_breaker_factory(81, 100)), \
         patch("services.memory_guard.clear_safe_caches", return_value=[]), \
         patch("services.memory_guard.malloc_trim", return_value=(False, False)), \
         patch("services.daily_picks._bulk_screen") as mock_screen, \
         patch("services.daily_picks._predict_stock") as mock_predict, \
         patch("services.daily_picks.yf", MagicMock()), \
         patch("services.alpha_engine.regime_cluster.detect_regime") as mock_regime, \
         patch("os.getenv", return_value=None), \
         patch("builtins.open", MagicMock()), \
         patch("json.dump") as mock_dump:
        with pytest.raises(DailyPicksMemoryLimitError):
            dp._generate_picks_inner("US", job_id="job-run-start")

    mock_screen.assert_not_called()      # no universe screening
    mock_predict.assert_not_called()     # no Phase 1 task ever ran
    mock_regime.assert_not_called()      # no regime detection either
    mock_dump.assert_not_called()        # nothing published or persisted


def test_us_run_proceeds_normally_when_cleanup_leaves_memory_at_79_pct():
    """79% is above the cleanup tier but below abort: the run-start
    abort-only check must not raise and must not re-run cleanup — the
    run-start release_memory remains the only cleanup at that boundary —
    and generation proceeds into Phase 1."""
    import services.daily_picks as dp

    events = []

    def screen_stub(*a, **kw):
        events.append("screen")
        return (["AAA", "BBB"], 10, "screener", False, 20,
                {"universe_candidate_count": 2, "attempts": 1,
                 "reason": "healthy_screener_universe", "error_category": "none",
                 "tier_map": {}})

    with patch("services.memory_guard.MemoryCircuitBreaker",
               side_effect=_breaker_factory(79, 100)), \
         patch("services.memory_guard.clear_safe_caches",
               side_effect=lambda: events.append("clear") or []), \
         patch("services.memory_guard.malloc_trim", return_value=(False, False)), \
         patch("services.daily_picks._bulk_screen", side_effect=screen_stub) as mock_screen, \
         patch("services.daily_picks._predict_stock", return_value=None) as mock_predict, \
         patch("services.daily_picks._write_score_snapshots"), \
         patch("services.daily_picks.yf", MagicMock()), \
         patch("services.alpha_engine.regime_cluster.detect_regime", return_value=_FAKE_REGIME), \
         patch("services.alpha_engine.ic_engine.get_production_ic_weights", return_value={}), \
         patch("services.alpha_engine.store.log_prediction"), \
         patch("services.global_context.get_global_context", return_value={}), \
         patch("os.getenv", return_value=None), \
         patch("builtins.open", MagicMock()), \
         patch("json.dump"):
        dp._generate_picks_inner("US", job_id="job-run-start")

    mock_screen.assert_called_once()
    assert mock_predict.call_count == 6  # 2 candidates x 3 horizons — Phase 1 ran
    # Exactly ONE cleanup at the run-start boundary (release_memory);
    # the abort-only enforcement added none. Cleanups AFTER screening are
    # the pre-existing 72%-tier behavior at ranking_entry/persisting
    # checks, deliberately unchanged and out of scope here.
    pre_screen = events[:events.index("screen")]
    assert pre_screen == ["clear"], (
        f"expected exactly one run-start cleanup before universe screening, "
        f"got {pre_screen}"
    )


def test_us_run_with_no_visible_limit_never_false_aborts_at_start():
    _run_us_generation(_breaker_factory(6_000_000_000, None))  # must not raise


def test_successful_run_logs_run_end_summary_exactly_once():
    with patch.object(_RealBreaker, "log_summary", autospec=True) as mock_summary:
        _run_us_generation(_breaker_factory(10, 100))
    assert mock_summary.call_count == 1
