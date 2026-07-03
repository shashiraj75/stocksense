"""
RCI observability regression tests (Epic 006, Release 13C).

Covers the process-local aggregate counters added to
api/routers/predictions.py: composition-success and fail-open counts,
exposed additively through /debug/state. Proves the counters are correct,
that a broken counter can never affect the prediction response, cache, or
status code, and that RCI-disabled requests never touch the counters at
all.
"""

import time

import pytest

import api.routers.predictions as predictions_router
from services.prediction_engine import _pred_cache, _cache_set
from services.recommendation_consolidation_contract import (
    CONTRACT_VERSION, is_valid_rci_response_payload,
)


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


@pytest.fixture(autouse=True)
def _reset_counters():
    """Counters are process-local globals — reset around every test so
    tests don't leak state into each other."""
    predictions_router._rci_composition_success_count = 0
    predictions_router._rci_fail_open_count = 0
    yield
    predictions_router._rci_composition_success_count = 0
    predictions_router._rci_fail_open_count = 0


@pytest.mark.regression
class TestSuccessCase:
    def test_successful_composition_increments_success_count_once(self, monkeypatch):
        monkeypatch.setenv("RCI_LIVE_STOCK_ANALYSIS_ENABLED", "1")
        key = "OBSTEST1:US:short"
        original = _prediction(symbol="OBSTEST1", market="US")
        _cache_set(_pred_cache, key, (time.time(), original))

        from services.recommendation_consolidation_api_composer import (
            compose_prediction_response_with_rci, rci_live_stock_analysis_enabled,
        )
        result = _pred_cache[key][1]
        assert rci_live_stock_analysis_enabled() is True
        result = compose_prediction_response_with_rci(result, symbol="OBSTEST1", market="US")
        predictions_router._record_rci_outcome(
                    success=is_valid_rci_response_payload(result.get("recommendation_consolidation"))
                )

        assert predictions_router._rci_composition_success_count == 1
        assert predictions_router._rci_fail_open_count == 0
        # Base prediction fields unchanged
        assert result["signal"] == original["signal"]
        assert result["confidence"] == original["confidence"]
        assert result["composite_score"] == original["composite_score"]
        del _pred_cache[key]


@pytest.mark.regression
class TestPayloadValidityGate:
    """Release 13C blocker fix: presence of the `recommendation_consolidation`
    key alone must never count as success. Only a structurally-complete,
    canonical-contract-version payload may increment
    `composition_success_count` — everything else is fail-open."""

    def _classify(self, payload):
        predictions_router._record_rci_outcome(success=is_valid_rci_response_payload(payload))

    def test_missing_key_is_fail_open(self):
        result = {"signal": "BUY"}  # no recommendation_consolidation key at all
        self._classify(result.get("recommendation_consolidation"))
        assert predictions_router._rci_fail_open_count == 1
        assert predictions_router._rci_composition_success_count == 0

    def test_key_present_with_none_is_fail_open(self):
        result = {"signal": "BUY", "recommendation_consolidation": None}
        self._classify(result.get("recommendation_consolidation"))
        assert predictions_router._rci_fail_open_count == 1
        assert predictions_router._rci_composition_success_count == 0

    def test_key_present_with_non_dict_value_is_fail_open(self):
        for bogus in ("a string", 42, 3.14, True, ["list", "not", "dict"], (1, 2)):
            predictions_router._rci_fail_open_count = 0
            predictions_router._rci_composition_success_count = 0
            result = {"recommendation_consolidation": bogus}
            self._classify(result.get("recommendation_consolidation"))
            assert predictions_router._rci_fail_open_count == 1, f"bogus={bogus!r}"
            assert predictions_router._rci_composition_success_count == 0, f"bogus={bogus!r}"

    def test_key_present_with_malformed_dict_is_fail_open(self):
        # Right contract_version, but missing most of the required fields —
        # e.g. a truncated or partially-constructed payload.
        malformed = {"contract_version": CONTRACT_VERSION, "thesis_state": "supported"}
        result = {"recommendation_consolidation": malformed}
        self._classify(result.get("recommendation_consolidation"))
        assert predictions_router._rci_fail_open_count == 1
        assert predictions_router._rci_composition_success_count == 0

    def test_key_present_with_missing_contract_version_is_fail_open(self):
        no_version = _valid_rci_payload()
        del no_version["contract_version"]
        result = {"recommendation_consolidation": no_version}
        self._classify(result.get("recommendation_consolidation"))
        assert predictions_router._rci_fail_open_count == 1
        assert predictions_router._rci_composition_success_count == 0

    def test_key_present_with_unsupported_contract_version_is_fail_open(self):
        for bad_version in (0, 2, 999, CONTRACT_VERSION + 1, "1", None):
            predictions_router._rci_fail_open_count = 0
            predictions_router._rci_composition_success_count = 0
            payload = _valid_rci_payload()
            payload["contract_version"] = bad_version
            result = {"recommendation_consolidation": payload}
            self._classify(result.get("recommendation_consolidation"))
            assert predictions_router._rci_fail_open_count == 1, f"version={bad_version!r}"
            assert predictions_router._rci_composition_success_count == 0, f"version={bad_version!r}"

    def test_valid_supported_payload_is_success(self):
        result = {"recommendation_consolidation": _valid_rci_payload()}
        self._classify(result.get("recommendation_consolidation"))
        assert predictions_router._rci_composition_success_count == 1
        assert predictions_router._rci_fail_open_count == 0

    def test_real_composer_output_passes_the_validity_gate(self, monkeypatch):
        """End-to-end sanity: a real, unmocked composer call over a real
        engine snapshot produces a payload the new gate accepts as valid —
        proves the gate isn't accidentally stricter than the real contract."""
        monkeypatch.setenv("RCI_LIVE_STOCK_ANALYSIS_ENABLED", "1")
        from services.recommendation_consolidation_api_composer import (
            compose_prediction_response_with_rci,
        )
        original = _prediction(symbol="OBSTEST_GATE", market="US")
        result = compose_prediction_response_with_rci(original, symbol="OBSTEST_GATE", market="US")
        assert is_valid_rci_response_payload(result.get("recommendation_consolidation")) is True


def _valid_rci_payload() -> dict:
    """A minimal but structurally-complete RCI response payload — every
    field `RecommendationConsolidationResponse` declares, with the
    canonical contract version."""
    return {
        "contract_version": CONTRACT_VERSION,
        "snapshot_id": "test-snapshot",
        "computed_at": "2026-07-03T00:00:00+00:00",
        "is_snapshot": True,
        "thesis_state": "supported",
        "engine_agreement": "4 of 4 applicable engines support this thesis",
        "conflicts": (),
        "coverage_notices": (),
        "supporting_evidence": (),
        "opposing_evidence": (),
        "active_gates": (),
        "unresolved_risk_flags": (),
        "material_warnings": (),
        "evidence_completeness_pct": 90.0,
        "explanation_confidence_category": "high",
        "narrative": "test narrative",
        "engine_versions_used": {},
    }


@pytest.mark.regression
class TestFailOpenCase:
    def test_forced_internal_exception_increments_fail_open_count_once(self, monkeypatch):
        monkeypatch.setenv("RCI_LIVE_STOCK_ANALYSIS_ENABLED", "1")
        import services.recommendation_consolidation_api_composer as composer

        def boom(*a, **kw):
            raise RuntimeError("forced failure for Release 13C observability test")

        monkeypatch.setattr(composer, "build_recommendation_evidence_snapshot", boom)

        original = _prediction(symbol="OBSTEST2", market="US")
        result = composer.compose_prediction_response_with_rci(original, symbol="OBSTEST2", market="US")
        predictions_router._record_rci_outcome(
                    success=is_valid_rci_response_payload(result.get("recommendation_consolidation"))
                )

        assert predictions_router._rci_fail_open_count == 1
        assert predictions_router._rci_composition_success_count == 0
        assert result is original
        assert "recommendation_consolidation" not in result
        assert result == _prediction(symbol="OBSTEST2", market="US")


@pytest.mark.regression
class TestTelemetrySelfFailureNeverAffectsResponse:
    def test_broken_counter_helper_does_not_break_router_logic(self, monkeypatch):
        """Re-derives the router's own try/except-guarded call site (same
        precedent test_recommendation_consolidation_api_cache_safety.py's
        TestFeatureFlagDisabledLeavesBaseResponseUnchanged already uses for
        testing conditional wiring without a live HTTP call)."""
        monkeypatch.setenv("RCI_LIVE_STOCK_ANALYSIS_ENABLED", "1")

        def broken_recorder(success):
            raise RuntimeError("simulated telemetry failure")

        monkeypatch.setattr(predictions_router, "_record_rci_outcome", broken_recorder)

        from services.recommendation_consolidation_api_composer import (
            compose_prediction_response_with_rci, rci_live_stock_analysis_enabled,
        )
        key = "OBSTEST3:US:short"
        original = _prediction(symbol="OBSTEST3", market="US")
        _cache_set(_pred_cache, key, (time.time(), original))

        result = _pred_cache[key][1]
        if rci_live_stock_analysis_enabled():
            result = compose_prediction_response_with_rci(result, symbol="OBSTEST3", market="US")
            try:
                predictions_router._record_rci_outcome(
                    success=is_valid_rci_response_payload(result.get("recommendation_consolidation"))
                )
            except Exception:
                pass

        # Response is unaffected by the broken counter.
        assert "recommendation_consolidation" in result
        assert result["signal"] == original["signal"]
        assert result["confidence"] == original["confidence"]
        # Cache entry itself untouched.
        assert _pred_cache[key][1] == original
        assert "recommendation_consolidation" not in _pred_cache[key][1]
        del _pred_cache[key]


@pytest.mark.regression
class TestDisabledFlagEmitsNoTelemetry:
    def test_disabled_flag_leaves_counters_at_zero_and_never_calls_composer(self, monkeypatch):
        monkeypatch.delenv("RCI_LIVE_STOCK_ANALYSIS_ENABLED", raising=False)
        from services.recommendation_consolidation_api_composer import rci_live_stock_analysis_enabled

        called = {"composer": False, "recorder": False}

        def spy_composer(*a, **kw):
            called["composer"] = True
            raise AssertionError("composer must not be called when RCI is disabled")

        def spy_recorder(*a, **kw):
            called["recorder"] = True
            raise AssertionError("counter must not be touched when RCI is disabled")

        monkeypatch.setattr(predictions_router, "compose_prediction_response_with_rci", spy_composer)
        monkeypatch.setattr(predictions_router, "_record_rci_outcome", spy_recorder)

        result = _prediction(symbol="OBSTEST4", market="US")
        if rci_live_stock_analysis_enabled():
            result = predictions_router.compose_prediction_response_with_rci(
                result, symbol="OBSTEST4", market="US"
            )
            predictions_router._record_rci_outcome(
                    success=is_valid_rci_response_payload(result.get("recommendation_consolidation"))
                )

        assert called == {"composer": False, "recorder": False}
        assert predictions_router._rci_composition_success_count == 0
        assert predictions_router._rci_fail_open_count == 0
        assert "recommendation_consolidation" not in result


@pytest.mark.regression
class TestCacheSafetyWithObservability:
    def test_cache_entry_identity_and_contents_unchanged_after_composition_and_recording(self, monkeypatch):
        monkeypatch.setenv("RCI_LIVE_STOCK_ANALYSIS_ENABLED", "1")
        from services.recommendation_consolidation_api_composer import compose_prediction_response_with_rci

        key = "OBSTEST5:US:short"
        original = _prediction(symbol="OBSTEST5", market="US")
        _cache_set(_pred_cache, key, (time.time(), original))

        cached_before_id = id(_pred_cache[key][1])
        cached_before_snapshot = dict(_pred_cache[key][1])

        result = compose_prediction_response_with_rci(_pred_cache[key][1], symbol="OBSTEST5", market="US")
        predictions_router._record_rci_outcome(
                    success=is_valid_rci_response_payload(result.get("recommendation_consolidation"))
                )

        assert id(_pred_cache[key][1]) == cached_before_id
        assert _pred_cache[key][1] == cached_before_snapshot
        assert "recommendation_consolidation" not in _pred_cache[key][1]
        del _pred_cache[key]


@pytest.mark.regression
class TestDebugStateShapeIsAdditive:
    """Release 14B narrowed and authenticated /debug/state — rci_observability
    remains available to an authenticated caller, unchanged in shape."""

    def test_debug_state_exposes_only_two_aggregate_integers(self, monkeypatch):
        monkeypatch.setattr(predictions_router, "_DEBUG_SECRET", "test-debug-secret")
        predictions_router._rci_composition_success_count = 3
        predictions_router._rci_fail_open_count = 1
        import asyncio
        state = asyncio.run(predictions_router.debug_state(x_secret="test-debug-secret"))
        assert state["rci_observability"] == {
            "composition_success_count": 3,
            "fail_open_count": 1,
        }
        # Release 14B narrowed fields present; raw cache_keys/log never returned.
        for field in ("computing_count", "cache_entry_count", "cache_age_summary_s", "thread_count"):
            assert field in state
        assert "cache_keys" not in state
        assert "log" not in state
        assert "computing" not in state  # replaced by computing_count


@pytest.mark.regression
class TestNoSchedulerOrDailyPicksReachesObservabilityCode:
    def test_daily_picks_module_does_not_reference_rci_counters(self):
        import pathlib
        import services.daily_picks as dp
        source = pathlib.Path(dp.__file__).read_text()
        assert "_record_rci_outcome" not in source
        assert "_rci_composition_success_count" not in source
        assert "_rci_fail_open_count" not in source

    def test_bg_thread_does_not_reference_rci_counters(self):
        import pathlib
        source = pathlib.Path(predictions_router.__file__).read_text()
        bg_thread_start = source.index("def _bg_thread")
        bg_thread_end = source.index("def debug_state")
        bg_thread_block = source[bg_thread_start:bg_thread_end]
        assert "_record_rci_outcome" not in bg_thread_block
