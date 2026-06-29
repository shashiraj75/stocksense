"""
Cache-safety and non-interference regression tests for the Recommendation
Consolidation Live Stock Analysis API integration (Epic 005, Sprint #008).

Proves the decisive finding from Sprint #007 cannot be reintroduced:
PredictionEngine.predict()'s cache-hit path returns its cached dict BY
REFERENCE, and Daily Picks shares the exact same `_pred_cache` object.
These tests directly exercise the shared cache to prove the composer
never contaminates it, and that Daily Picks-shaped direct `predict()`
consumers never observe an RCI field.
"""

import pathlib
import time

import pytest

import services.prediction_engine as pe
import api.routers.predictions as predictions_router
from services.prediction_engine import _pred_cache, _cache_set
from services.recommendation_consolidation_api_composer import compose_prediction_response_with_rci


def _engine_dict():
    return {
        "score": 70, "grade": "buy", "confidence": 80,
        "strengths": [], "weaknesses": [], "risks": [],
        "explanation": "x", "metadata": {"data_completeness_pct": 90.0},
    }


def _prediction(symbol="X", market="US"):
    return {
        "symbol": symbol, "market": market, "signal": "BUY", "confidence": 75,
        "composite_score": 60,
        "business_quality": _engine_dict(), "financial_strength": _engine_dict(),
        "growth_intelligence": _engine_dict(), "valuation_intelligence": _engine_dict(),
    }


@pytest.mark.regression
class TestSharedCacheNeverContaminated:
    def test_composing_an_rci_response_does_not_alter_the_cache_entry(self):
        """The decisive proof: populate the SAME _pred_cache used by both
        the router and PredictionEngine.predict() itself, compose an RCI
        response from the cached value, and confirm the cache entry's own
        dict is unchanged afterward."""
        key = "RCITEST:US:short"
        original = _prediction(symbol="RCITEST", market="US")
        _cache_set(_pred_cache, key, (time.time(), original))

        cached_before = dict(_pred_cache[key][1])
        compose_prediction_response_with_rci(_pred_cache[key][1], symbol="RCITEST", market="US")
        cached_after = _pred_cache[key][1]

        assert cached_after == cached_before
        assert "recommendation_consolidation" not in cached_after
        del _pred_cache[key]

    def test_a_later_direct_predict_style_cache_read_has_no_rci_field(self):
        """Simulates Daily Picks' own access pattern: reading directly
        from `_pred_cache` (exactly what `engine.predict()`'s own cache-hit
        branch does) after an API request has already composed an RCI
        response for the SAME cache key. The direct read must show no
        trace of RCI."""
        key = "RCITEST2:US:short"
        original = _prediction(symbol="RCITEST2", market="US")
        _cache_set(_pred_cache, key, (time.time(), original))

        # Simulate the API's own composition (as the router would do).
        composed = compose_prediction_response_with_rci(_pred_cache[key][1], symbol="RCITEST2", market="US")
        assert "recommendation_consolidation" in composed  # composition itself worked

        # A "Daily Picks"-shaped direct cache/engine read afterward:
        daily_picks_style_read = _pred_cache[key][1]
        assert "recommendation_consolidation" not in daily_picks_style_read
        del _pred_cache[key]

    def test_repeated_api_style_requests_do_not_accumulate_state_in_cache(self):
        key = "RCITEST3:US:short"
        original = _prediction(symbol="RCITEST3", market="US")
        _cache_set(_pred_cache, key, (time.time(), original))

        for _ in range(5):
            compose_prediction_response_with_rci(_pred_cache[key][1], symbol="RCITEST3", market="US")

        assert _pred_cache[key][1] == original
        assert "recommendation_consolidation" not in _pred_cache[key][1]
        del _pred_cache[key]


@pytest.mark.regression
class TestDailyPicksRemainsUnaffected:
    def test_daily_picks_module_does_not_import_the_composer(self):
        import services.daily_picks as dp
        source = pathlib.Path(dp.__file__).read_text()
        assert "recommendation_consolidation_api_composer" not in source
        assert "compose_prediction_response_with_rci" not in source

    def test_prediction_engine_does_not_import_the_composer(self):
        source = pathlib.Path(pe.__file__).read_text()
        assert "recommendation_consolidation_api_composer" not in source

    def test_individual_engines_do_not_import_the_composer(self):
        for module_name in (
            "business_quality_engine", "financial_strength_engine",
            "growth_intelligence_engine", "valuation_intelligence_engine",
        ):
            path = pathlib.Path(f"services/{module_name}.py")
            source = path.read_text()
            assert "recommendation_consolidation_api_composer" not in source


@pytest.mark.regression
class TestRouterIntegrationScope:
    def test_composer_is_only_invoked_in_predictions_router_cache_hit_branch(self):
        """Confirms the composer call sites are exactly where Sprint #007
        approved -- inside the /predict route's cache-hit branch, not
        anywhere inside PredictionEngine.predict() itself, not in any
        background-thread function."""
        source = pathlib.Path(predictions_router.__file__).read_text()
        assert source.count("compose_prediction_response_with_rci(") == 1

    def test_composer_call_is_inside_the_cache_hit_branch_not_bg_thread(self):
        source = pathlib.Path(predictions_router.__file__).read_text()
        bg_thread_start = source.index("def _bg_thread")
        bg_thread_end = source.index("def debug_state")
        bg_thread_block = source[bg_thread_start:bg_thread_end]
        assert "compose_prediction_response_with_rci" not in bg_thread_block

    def test_flag_check_precedes_composition_in_router_source(self):
        source = pathlib.Path(predictions_router.__file__).read_text()
        flag_idx = source.index("rci_live_stock_analysis_enabled()")
        compose_idx = source.index("compose_prediction_response_with_rci(result")
        assert flag_idx < compose_idx


@pytest.mark.regression
class TestFeatureFlagDisabledLeavesBaseResponseUnchanged:
    def test_disabled_flag_means_no_rci_key_via_router_logic(self, monkeypatch):
        """Re-derives the router's own conditional logic directly (the
        same pattern test_valuation_intelligence_prediction_engine_
        integration.py's own precedent already established for testing
        conditional wiring without a live HTTP call)."""
        monkeypatch.delenv("RCI_LIVE_STOCK_ANALYSIS_ENABLED", raising=False)
        from services.recommendation_consolidation_api_composer import rci_live_stock_analysis_enabled
        result = dict(_prediction())
        if rci_live_stock_analysis_enabled():
            result = compose_prediction_response_with_rci(result, symbol="X", market="US")
        assert "recommendation_consolidation" not in result
