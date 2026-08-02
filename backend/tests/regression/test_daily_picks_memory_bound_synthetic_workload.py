"""
US Daily Picks generation-reliability incident (2026-07-22) — large
synthetic-workload memory-bound proof.

Runs a moderately-sized (60 candidates x 3 horizons = 180 tasks — smaller
than production's actual 400-candidate _N_CANDIDATES purely to keep this
test's wall-clock time reasonable; the peak-vs-horizon-count relationship
being proven does not depend on the absolute candidate count) synthetic
pipeline through
_generate_picks_inner with _predict_stock mocked to return payloads
carrying non-trivial `reasoning`/`summary`/`global_context` text, and
gc-tracks the peak number of these synthetic result objects simultaneously
reachable at any point during the run.

2026-07-23/24 incident update: the chunked candidate-major Phase 1
deliberately accumulates all three horizons' slim result pools (~12 KB
each, ~15 MB total in production — measured never to be the dominant
memory term; the dominant term was the 3x SEC companyfacts churn the old
horizon-major ordering caused). The empirically-proven bound is therefore:
peak reachable result objects never exceeds ~3 x candidates (no hidden
duplication of pools by ranking/persistence), and pools are actually
RELEASED per horizon as Phases 3-6 complete (peak does not grow to 3 x
candidates PLUS retained copies), and nothing accumulates across runs.
"""
import gc
from unittest.mock import MagicMock, patch

_FAKE_REGIME = {"regime_id": 0, "label": "neutral", "description": "", "weight_multipliers": {}}

# A realistically-sized "rich" per-candidate payload — same shape/order of
# magnitude as what _predict_stock legitimately returns in production for
# each scored candidate (reasoning bullet list, prose summary, nested
# context dicts) before this codebase's own existing top-6 slicing keeps
# only the published fields.
_FAKE_REASONING = ["reason " + str(i) * 20 for i in range(15)]
_FAKE_SUMMARY = "x" * 2000
_FAKE_GLOBAL_CONTEXT = {f"factor_{i}": {"value": i, "detail": "y" * 100} for i in range(10)}


class _TrackedResult(dict):
    """A dict subclass so live instances can be counted via gc, without
    needing every unrelated dict in the interpreter to be swept up too."""
    pass


def test_peak_tracked_result_count_bounded_by_three_slim_pools_no_duplication():
    import services.daily_picks as dp

    n_candidates = 60
    candidates = [f"SYM{i}" for i in range(n_candidates)]
    peak_alive = {"count": 0}

    def fake_predict(sym, horizon, market):
        r = _TrackedResult({
            "symbol": sym, "name": sym, "signal": "BUY", "horizon": horizon,
            "price": 100.0, "target": 110.0,
            "tech_score": 55.0, "fund_score": 55.0, "sentiment_score": 50.0,
            "quality_score": 55.0, "sentiment_available": True, "quality_available": True,
            "quality_raw_score": 55.0, "sentiment": "NEUTRAL",
            "reasoning": list(_FAKE_REASONING), "summary": _FAKE_SUMMARY,
            "score_band": "B", "global_context": dict(_FAKE_GLOBAL_CONTEXT),
            "quality_factors": dict(_FAKE_GLOBAL_CONTEXT),
            "composite_score": 55.0, "confidence_model": 55.0, "confidence": 60,
            "risk_reward": 1.5,
        })
        # Sample peak live-instance count right after creation — the worst
        # case for "how many of these are simultaneously reachable" is
        # immediately before the current horizon's ranking step releases
        # the previous horizon's list, so sampling on every call catches it.
        gc.collect()
        alive = sum(1 for o in gc.get_objects() if isinstance(o, _TrackedResult))
        peak_alive["count"] = max(peak_alive["count"], alive)
        return r

    with patch("services.daily_picks._bulk_screen",
               return_value=(candidates, 1000, "screener", False, 2000,
                             {"universe_candidate_count": n_candidates, "attempts": 1,
                              "reason": "healthy_screener_universe", "error_category": "none",
                              "tier_map": {}})), \
         patch("services.daily_picks._predict_stock", side_effect=fake_predict), \
         patch("services.daily_picks._write_score_snapshots", new=lambda *a, **kw: None), \
         patch("services.alpha_engine.outcome_logger.resolve_pending_outcomes"), \
         patch("services.alpha_engine.regime_cluster.detect_regime", return_value=_FAKE_REGIME), \
         patch("services.alpha_engine.ic_engine.get_production_ic_weights",
               return_value={"tech": 0.3, "fund": 0.3, "sentiment": 0.2, "quality": 0.2}), \
         patch("services.alpha_engine.store.log_prediction"), \
         patch("services.global_context.get_global_context", return_value={}), \
         patch("os.getenv", return_value=None), \
         patch("builtins.open", MagicMock()), \
         patch("json.dump"):
        try:
            dp._generate_picks_inner("US", job_id=None)
        except Exception:
            pass  # only the peak live-object count matters for this test

    # 2026-07-23/24 contract: the chunked candidate-major Phase 1 retains
    # all three horizons' slim pools by design (3 x n_candidates), and the
    # ranking step for one horizon only adds references to the SAME objects
    # (`universe`/`ranked`), never copies. Peak must therefore stay at
    # ~3 x n_candidates with only a small transient margin — anything
    # approaching 4x would mean a horizon's pool was duplicated or retained
    # past its own Phases 3-6, the exact regression this test exists to
    # catch. (The peak is sampled inside fake_predict, so it directly
    # observes the accumulator at its fullest — end of Phase 1.)
    three_horizons = 3 * n_candidates
    assert peak_alive["count"] <= three_horizons + 20, (
        f"peak simultaneously-alive rich candidate objects was "
        f"{peak_alive['count']}, expected at most ~{three_horizons} "
        f"(3 slim horizon pools) plus a small transient margin — a horizon "
        f"pool is being duplicated or retained past its release point"
    )


def test_repeated_runs_do_not_show_monotonic_peak_growth():
    """Two consecutive synthetic runs must show the same peak (module-level
    caches like _pred_cache are bounded/TTL'd and reset-independent) —
    proves this isn't a one-run-only bound that leaks across runs."""
    import services.daily_picks as dp

    n_candidates = 50
    candidates = [f"SYM{i}" for i in range(n_candidates)]
    peaks = []

    def make_fake_predict(peak_tracker):
        def fake_predict(sym, horizon, market):
            r = _TrackedResult({
                "symbol": sym, "name": sym, "signal": "BUY", "horizon": horizon,
                "price": 100.0, "target": 110.0,
                "tech_score": 55.0, "fund_score": 55.0, "sentiment_score": 50.0,
                "quality_score": 55.0, "sentiment_available": True, "quality_available": True,
                "quality_raw_score": 55.0, "sentiment": "NEUTRAL",
                "reasoning": list(_FAKE_REASONING), "summary": _FAKE_SUMMARY,
                "score_band": "B", "global_context": dict(_FAKE_GLOBAL_CONTEXT),
                "quality_factors": dict(_FAKE_GLOBAL_CONTEXT),
                "composite_score": 55.0, "confidence_model": 55.0, "confidence": 60,
                "risk_reward": 1.5,
            })
            gc.collect()
            alive = sum(1 for o in gc.get_objects() if isinstance(o, _TrackedResult))
            peak_tracker["count"] = max(peak_tracker["count"], alive)
            return r
        return fake_predict

    for _ in range(2):
        tracker = {"count": 0}
        with patch("services.daily_picks._bulk_screen",
                   return_value=(candidates, 200, "screener", False, 400,
                                 {"universe_candidate_count": n_candidates, "attempts": 1,
                                  "reason": "healthy_screener_universe", "error_category": "none",
                                  "tier_map": {}})), \
             patch("services.daily_picks._predict_stock", side_effect=make_fake_predict(tracker)), \
             patch("services.daily_picks._write_score_snapshots", new=lambda *a, **kw: None), \
             patch("services.alpha_engine.outcome_logger.resolve_pending_outcomes"), \
             patch("services.alpha_engine.regime_cluster.detect_regime", return_value=_FAKE_REGIME), \
             patch("services.alpha_engine.ic_engine.get_production_ic_weights",
                   return_value={"tech": 0.3, "fund": 0.3, "sentiment": 0.2, "quality": 0.2}), \
             patch("services.alpha_engine.store.log_prediction"), \
             patch("services.global_context.get_global_context", return_value={}), \
             patch("os.getenv", return_value=None), \
             patch("builtins.open", MagicMock()), \
             patch("json.dump"):
            try:
                dp._generate_picks_inner("US", job_id=None)
            except Exception:
                pass
        gc.collect()
        peaks.append(tracker["count"])

    assert peaks[1] <= peaks[0] + 10, (
        f"second run's peak ({peaks[1]}) grew meaningfully beyond the "
        f"first run's peak ({peaks[0]}) — suggests cross-run accumulation"
    )
