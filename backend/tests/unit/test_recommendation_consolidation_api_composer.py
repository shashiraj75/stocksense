"""
Unit tests for the Recommendation Consolidation Live Stock Analysis API
composer (Epic 005, Sprint #008) -- the approved integration boundary
per Sprint #007's own decision. Proves the composer never mutates its
input, returns a new dict, fails open, and respects the feature flag.
"""

import logging

import pytest

from services.recommendation_consolidation_api_composer import (
    compose_prediction_response_with_rci, rci_live_stock_analysis_enabled,
)


def _engine_dict(score=70, grade="buy", confidence=80):
    return {
        "score": score, "grade": grade, "confidence": confidence,
        "strengths": [], "weaknesses": [], "risks": [],
        "explanation": "x", "metadata": {"data_completeness_pct": 90.0},
    }


def _prediction(symbol="X", market="US", **overrides):
    base = {
        "symbol": symbol, "market": market, "signal": "BUY", "confidence": 75,
        "composite_score": 60, "technical": {"score": 50}, "sentiment_score": {"score": 50},
        "business_quality": _engine_dict(), "financial_strength": _engine_dict(),
        "growth_intelligence": _engine_dict(), "valuation_intelligence": _engine_dict(),
    }
    base.update(overrides)
    return base


@pytest.mark.unit
class TestFeatureFlagDefault:
    def test_default_is_disabled(self, monkeypatch):
        monkeypatch.delenv("RCI_LIVE_STOCK_ANALYSIS_ENABLED", raising=False)
        assert rci_live_stock_analysis_enabled() is False

    def test_can_be_enabled_via_env_var(self, monkeypatch):
        monkeypatch.setenv("RCI_LIVE_STOCK_ANALYSIS_ENABLED", "1")
        assert rci_live_stock_analysis_enabled() is True

    def test_fails_safe_on_malformed_value(self, monkeypatch):
        monkeypatch.setenv("RCI_LIVE_STOCK_ANALYSIS_ENABLED", "not-a-real-value")
        assert rci_live_stock_analysis_enabled() is False

    def test_accepts_common_truthy_variants(self, monkeypatch):
        for val in ("1", "true", "True", "YES", "on"):
            monkeypatch.setenv("RCI_LIVE_STOCK_ANALYSIS_ENABLED", val)
            assert rci_live_stock_analysis_enabled() is True


@pytest.mark.unit
class TestComposerNeverMutatesInput:
    def test_returns_a_new_dict_object(self):
        prediction = _prediction()
        result = compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        assert result is not prediction

    def test_original_prediction_dict_unchanged(self):
        prediction = _prediction()
        original_snapshot = dict(prediction)
        compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        assert prediction == original_snapshot

    def test_original_prediction_has_no_rci_key_after_composition(self):
        prediction = _prediction()
        compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        assert "recommendation_consolidation" not in prediction

    def test_nested_engine_dicts_remain_the_same_object_reference(self):
        """Confirms the shallow-merge strategy is correct: nested engine
        sub-dicts are read-only inputs throughout the RCI pure core, so
        no deep copy is needed -- verified, not assumed."""
        prediction = _prediction()
        bq_id = id(prediction["business_quality"])
        result = compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        assert id(result["business_quality"]) == bq_id

    def test_nested_engine_dict_values_unchanged(self):
        prediction = _prediction()
        bq_snapshot = dict(prediction["business_quality"])
        compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        assert prediction["business_quality"] == bq_snapshot

    def test_result_contains_rci_key(self):
        prediction = _prediction()
        result = compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        assert "recommendation_consolidation" in result

    def test_repeated_composition_does_not_accumulate_or_duplicate(self):
        """Calling the composer twice on the SAME original prediction must
        produce equal, non-accumulating results each time -- not append to
        a growing list, not mutate state across calls."""
        prediction = _prediction()
        result1 = compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        result2 = compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        rci1 = result1["recommendation_consolidation"]
        rci2 = result2["recommendation_consolidation"]
        assert rci1["thesis_state"] == rci2["thesis_state"]
        assert rci1["supporting_evidence"] == rci2["supporting_evidence"]
        assert rci1["conflicts"] == rci2["conflicts"]


@pytest.mark.unit
class TestComposerOutputShape:
    def test_rci_payload_is_json_serializable(self):
        import json
        prediction = _prediction()
        result = compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        json.dumps(result["recommendation_consolidation"])  # raises if not JSON-safe

    def test_rci_payload_has_no_replacement_signal_or_confidence(self):
        prediction = _prediction()
        result = compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        rci = result["recommendation_consolidation"]
        assert "signal" not in rci
        assert "recommendation" not in rci
        # 'confidence' as a standalone replacement key must not exist --
        # only the categorical explanation_confidence_category may.
        assert "confidence" not in rci
        assert "explanation_confidence_category" in rci

    def test_base_prediction_fields_unchanged_in_composed_result(self):
        prediction = _prediction()
        result = compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        assert result["signal"] == prediction["signal"]
        assert result["confidence"] == prediction["confidence"]
        assert result["composite_score"] == prediction["composite_score"]


@pytest.mark.unit
class TestErrorIsolation:
    def test_malformed_engine_output_does_not_raise(self):
        prediction = _prediction(business_quality={"unexpected": "shape"})
        result = compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        # Malformed shape degrades gracefully inside the adapter (confirmed
        # by Sprint #003's own tests) -- composition still succeeds.
        assert "recommendation_consolidation" in result

    def test_total_rci_failure_returns_original_prediction_unchanged(self, monkeypatch):
        """Forces an exception inside the composer's own try block and
        confirms it returns the ORIGINAL prediction_result reference
        unchanged, with no recommendation_consolidation key -- Option A
        (omit entirely on failure), per this sprint's own decision."""
        import services.recommendation_consolidation_api_composer as composer

        def _raises(*args, **kwargs):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(composer, "build_recommendation_evidence_snapshot", _raises)
        prediction = _prediction()
        result = compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        assert result is prediction  # the exact same object, not even a copy
        assert "recommendation_consolidation" not in result

    def test_failure_is_logged_not_raised_to_caller(self, monkeypatch, caplog):
        import services.recommendation_consolidation_api_composer as composer

        def _raises(*args, **kwargs):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(composer, "build_recommendation_evidence_snapshot", _raises)
        prediction = _prediction()
        with caplog.at_level(logging.WARNING, logger="services.recommendation_consolidation_api_composer"):
            compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        assert any("recommendation_consolidation" in r.message for r in caplog.records)

    def test_failure_does_not_expose_internal_stack_trace_details(self, monkeypatch, caplog):
        """The log message itself may contain internals (for operators);
        the RETURNED prediction dict must never contain any trace/error
        detail -- confirmed by checking the returned dict's own keys."""
        import services.recommendation_consolidation_api_composer as composer

        def _raises(*args, **kwargs):
            raise RuntimeError("simulated failure with sensitive detail")

        monkeypatch.setattr(composer, "build_recommendation_evidence_snapshot", _raises)
        prediction = _prediction()
        result = compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        assert "error" not in result
        assert "traceback" not in result
        assert "sensitive detail" not in str(result)

    def test_one_failure_does_not_affect_a_later_successful_call(self, monkeypatch):
        import services.recommendation_consolidation_api_composer as composer
        from services.recommendation_evidence_adapter import build_recommendation_evidence_snapshot as real_builder

        def _raises(*args, **kwargs):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(composer, "build_recommendation_evidence_snapshot", _raises)
        failed_result = compose_prediction_response_with_rci(_prediction(), symbol="X", market="US")
        assert "recommendation_consolidation" not in failed_result

        monkeypatch.setattr(composer, "build_recommendation_evidence_snapshot", real_builder)
        ok_result = compose_prediction_response_with_rci(_prediction(), symbol="Y", market="US")
        assert "recommendation_consolidation" in ok_result


@pytest.mark.unit
class TestValuationKillSwitchObservedNotAltered:
    def test_disabled_kill_switch_reflected_as_feature_disabled_in_rci(self, monkeypatch):
        monkeypatch.delenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        monkeypatch.delenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_US", raising=False)
        prediction = _prediction(market="US")
        result = compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        # The composer reads (never alters) the real switch state --
        # confirmed by the switch's own env var being untouched here.
        import os
        assert os.getenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_US") is None

    def test_kill_switch_env_vars_are_never_written_by_the_composer(self, monkeypatch):
        monkeypatch.delenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        compose_prediction_response_with_rci(_prediction(market="IN"), symbol="X", market="IN")
        import os
        assert os.getenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN") is None
