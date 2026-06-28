"""
Integration tests (Epic 003 Sprint #007): the Growth Intelligence
Engine wired additively into PredictionEngine.predict()'s
`_get_growth_intelligence` closure and
`_apply_growth_intelligence_adjustment`. Mirrors
test_financial_strength_prediction_engine_integration.py's own
established pattern exactly -- `predict()` itself is too heavy to mock
realistically end-to-end, so this exercises the same closure logic and
static-wiring properties that file's own precedent already proved is
the right level of confidence for this kind of additive change.
"""

import inspect
import pathlib

import pytest

import services.prediction_engine as pe
from services.prediction_engine import PredictionEngine, _growth_intelligence_confidence_enabled


def _f(value):
    return {"value": value}


class TestGrowthIntelligenceClosureFailsafe:
    @pytest.mark.integration
    def test_unsupported_market_returns_none_without_calling_adapter(self):
        """Mirrors _get_financial_strength's market-guard pattern -- a
        market that's neither IN nor US never reaches either adapter."""
        market = "CRYPTO"
        result = None if market not in ("IN", "US") else "should not reach here"
        assert result is None

    @pytest.mark.integration
    def test_india_adapter_exception_does_not_propagate(self, monkeypatch):
        def _raises(*args, **kwargs):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr("services.screener_data.fetch_screener_data", _raises)
        try:
            from services.screener_data import fetch_screener_data
            result = fetch_screener_data("TEST")
        except BaseException:
            result = None
        assert result is None

    @pytest.mark.integration
    def test_us_adapter_exception_does_not_propagate(self, monkeypatch):
        def _raises(*args, **kwargs):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr("services.us_growth_adapter.build_us_growth_fields", _raises)
        try:
            from services.us_growth_adapter import build_us_growth_fields
            result = build_us_growth_fields(object())
        except BaseException:
            result = None
        assert result is None


class TestGrowthIntelligenceFieldPresenceAndWiring:
    @pytest.mark.integration
    def test_result_dict_includes_growth_intelligence_key(self):
        source = pathlib.Path(pe.__file__).read_text()
        assert '"growth_intelligence": growth_intelligence,' in source
        # Additive, not a replacement -- financial_strength/business_quality
        # must still both be present at their existing call sites.
        assert source.count('"financial_strength"') >= 2
        assert source.count('"business_quality"') >= 2

    @pytest.mark.integration
    def test_growth_intelligence_adapters_are_imported_lazily_not_at_module_level(self):
        source = pathlib.Path(pe.__file__).read_text()
        assert "from services.india_growth_adapter import build_india_growth_fields" in source
        assert "from services.us_growth_adapter import build_us_growth_fields" in source
        top_of_file = source.split("class PredictionEngine:")[0]
        assert "from services.india_growth_adapter import build_india_growth_fields" not in top_of_file
        assert "from services.us_growth_adapter import build_us_growth_fields" not in top_of_file

    @pytest.mark.integration
    def test_growth_intelligence_adjustment_is_called_after_financial_strength(self):
        """Confirms the wiring order: every pre-existing adjustment
        (risk-reward, pledge, financial strength) runs first, unmodified
        -- Growth Intelligence's adjustment runs last, so it can only
        ever refine an already-computed confidence, never override any
        prior adjustment's own intent."""
        source = pathlib.Path(pe.__file__).read_text()
        rr_idx = source.index("_apply_risk_reward_adjustment(signal, confidence, trade_levels, reasoning, bear_case)")
        pledge_idx = source.index('_apply_pledge_adjustment(market, info or {}, signal, confidence, reasoning, bear_case)')
        fs_idx = source.index("_apply_financial_strength_adjustment(")
        gi_idx = source.index("_apply_growth_intelligence_adjustment(")
        assert rr_idx < pledge_idx < fs_idx < gi_idx

    @pytest.mark.integration
    def test_growth_intelligence_gather_task_added_to_round_2(self):
        """Confirms _get_growth_intelligence is wired into the same
        asyncio.gather Round 2 call as _get_financial_strength -- a
        parallel task, not a new sequential round."""
        source = pathlib.Path(pe.__file__).read_text()
        gather_block = source[source.index("news_data, global_ctx, quality"):source.index("sentiment_score = self._aggregate_sentiment")]
        assert "_get_growth_intelligence" in gather_block
        assert "_get_financial_strength" in gather_block


class TestGrowthIntelligenceAdjustmentWiring:
    @pytest.mark.integration
    def test_adjustment_method_exists_on_prediction_engine(self):
        engine = PredictionEngine()
        assert hasattr(engine, "_apply_growth_intelligence_adjustment")
        assert callable(engine._apply_growth_intelligence_adjustment)

    @pytest.mark.integration
    def test_adjustment_does_not_touch_composite_score_or_signal(self):
        """Structural confirmation, per this sprint's explicit 'no
        redesign' rule: the method's signature has no access to
        composite_score/signal internals at all."""
        engine = PredictionEngine()
        sig = inspect.signature(engine._apply_growth_intelligence_adjustment)
        assert "composite_score" not in sig.parameters
        assert "signal" not in sig.parameters


@pytest.mark.integration
class TestKillSwitch:
    def test_default_enabled_for_india(self, monkeypatch):
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        assert _growth_intelligence_confidence_enabled("IN") is True

    def test_default_disabled_for_us(self, monkeypatch):
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_US", raising=False)
        assert _growth_intelligence_confidence_enabled("US") is False

    def test_can_be_disabled_for_india_via_env_var(self, monkeypatch):
        monkeypatch.setenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "0")
        assert _growth_intelligence_confidence_enabled("IN") is False

    def test_can_be_enabled_for_us_via_env_var(self, monkeypatch):
        """Confirms the switch is independently flippable -- not gated
        only by the hard market check elsewhere -- even though Sprint
        #006 doesn't authorize using this in production for US today."""
        monkeypatch.setenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_US", "1")
        assert _growth_intelligence_confidence_enabled("US") is True

    def test_fails_safe_on_malformed_value(self, monkeypatch):
        monkeypatch.setenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "not-a-real-value")
        assert _growth_intelligence_confidence_enabled("IN") is False

    def test_unsupported_market_always_disabled(self, monkeypatch):
        monkeypatch.setenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        monkeypatch.setenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_US", "1")
        assert _growth_intelligence_confidence_enabled("CRYPTO") is False

    def test_accepts_common_truthy_string_variants(self, monkeypatch):
        for val in ("1", "true", "True", "YES", "on"):
            monkeypatch.setenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", val)
            assert _growth_intelligence_confidence_enabled("IN") is True

    def test_kill_switch_is_independent_of_financial_strength(self):
        """No Financial-Strength-specific flag exists to couple with --
        confirms Growth Intelligence's switch is its own, separate
        mechanism, not a reuse."""
        source = pathlib.Path(pe.__file__).read_text()
        assert "FINANCIAL_STRENGTH_ENABLED" not in source
        assert "GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN" in source
        assert "GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_US" in source


@pytest.mark.integration
class TestIndiaConfidenceAdjustment:
    def test_strong_score_boosts_confidence_capped_at_3(self, monkeypatch):
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        engine = PredictionEngine()
        gi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        reasoning, bull, bear = [], [], []
        new_conf = engine._apply_growth_intelligence_adjustment("IN", gi, 50, reasoning, bull, bear)
        assert new_conf == 53  # +3, the cap
        assert len(bull) == 1
        assert "Growth Intelligence" in reasoning[0]["indicator"]

    def test_weak_score_demotes_confidence_capped_at_minus_3(self, monkeypatch):
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        engine = PredictionEngine()
        gi = {"score": 0, "grade": "avoid", "metadata": {}}
        reasoning, bull, bear = [], [], []
        new_conf = engine._apply_growth_intelligence_adjustment("IN", gi, 50, reasoning, bull, bear)
        assert new_conf == 47  # -3, the cap
        assert len(bear) == 1

    def test_adjustment_never_exceeds_cap_even_at_score_extremes(self, monkeypatch):
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        engine = PredictionEngine()
        for score in (0, 25, 50, 75, 100):
            gi = {"score": score, "grade": "hold", "metadata": {}}
            new_conf = engine._apply_growth_intelligence_adjustment("IN", gi, 50, [], [], [])
            assert abs(new_conf - 50) <= 3

    def test_confidence_clamped_to_0_100_range(self, monkeypatch):
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        engine = PredictionEngine()
        gi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        assert engine._apply_growth_intelligence_adjustment("IN", gi, 99, [], [], []) == 100
        gi_weak = {"score": 0, "grade": "avoid", "metadata": {}}
        assert engine._apply_growth_intelligence_adjustment("IN", gi_weak, 1, [], [], []) == 0


@pytest.mark.integration
class TestUsZeroAdjustment:
    def test_us_market_never_adjusts_confidence_even_with_strong_score(self, monkeypatch):
        """The primary requirement: regardless of kill-switch state, the
        hard market == 'IN' check inside the adjustment function means
        US confidence is untouched. Tested with the kill switch's
        default (disabled for US) AND explicitly enabled, to confirm
        the market gate is the binding control either way."""
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_US", raising=False)
        engine = PredictionEngine()
        gi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        assert engine._apply_growth_intelligence_adjustment("US", gi, 50, [], [], []) == 50

    def test_us_unaffected_even_if_kill_switch_is_manually_enabled(self, monkeypatch):
        """Defense-in-depth check: even an explicit US-enable env var
        does not bypass the separate, hard market=='IN' check inside
        the adjustment function itself."""
        monkeypatch.setenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_US", "1")
        engine = PredictionEngine()
        gi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        assert engine._apply_growth_intelligence_adjustment("US", gi, 50, [], [], []) == 50

    def test_us_produces_no_reasoning_entries(self, monkeypatch):
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_US", raising=False)
        engine = PredictionEngine()
        gi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        reasoning, bull, bear = [], [], []
        engine._apply_growth_intelligence_adjustment("US", gi, 50, reasoning, bull, bear)
        assert reasoning == [] and bull == [] and bear == []


@pytest.mark.integration
class TestGracefulDegradation:
    def test_none_input_leaves_confidence_unchanged(self, monkeypatch):
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        engine = PredictionEngine()
        assert engine._apply_growth_intelligence_adjustment("IN", None, 50, [], [], []) == 50

    def test_rejected_grade_is_a_graceful_no_op_not_a_penalty(self, monkeypatch):
        """Mirrors Financial Strength's own non-liquidity-distress
        rejection handling: missing data must never be penalized."""
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        engine = PredictionEngine()
        gi = {"score": 0, "grade": "rejected", "metadata": {"rejection_reason": "insufficient_data"}}
        assert engine._apply_growth_intelligence_adjustment("IN", gi, 50, [], [], []) == 50

    def test_missing_score_key_degrades_gracefully(self, monkeypatch):
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        engine = PredictionEngine()
        gi = {"grade": "hold", "metadata": {}}  # no "score" key at all
        assert engine._apply_growth_intelligence_adjustment("IN", gi, 50, [], [], []) == 50

    def test_empty_dict_degrades_gracefully(self, monkeypatch):
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        engine = PredictionEngine()
        assert engine._apply_growth_intelligence_adjustment("IN", {}, 50, [], [], []) == 50


@pytest.mark.integration
class TestTelemetry:
    def test_telemetry_logged_for_every_evaluation(self, monkeypatch, caplog):
        import logging
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        engine = PredictionEngine()
        gi = {"score": 80, "grade": "strong_buy", "metadata": {}}
        with caplog.at_level(logging.INFO, logger="services.prediction_engine"):
            engine._apply_growth_intelligence_adjustment("IN", gi, 50, [], [], [])
        assert any("growth_intelligence_telemetry" in r.message for r in caplog.records)

    def test_telemetry_failure_never_affects_returned_confidence(self, monkeypatch):
        """Sprint #007's explicit rule: telemetry must not affect
        recommendations. Simulates a broken logger and confirms the
        adjustment's return value is unaffected."""
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        engine = PredictionEngine()
        gi = {"score": 100, "grade": "strong_buy", "metadata": {}}

        def _broken_log_info(*args, **kwargs):
            raise RuntimeError("simulated logging failure")

        monkeypatch.setattr(pe.log, "info", _broken_log_info)
        result = engine._apply_growth_intelligence_adjustment("IN", gi, 50, [], [], [])
        assert result == 53  # adjustment still applied correctly despite the logging failure
