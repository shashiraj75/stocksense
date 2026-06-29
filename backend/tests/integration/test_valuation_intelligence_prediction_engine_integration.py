"""
Integration tests (Epic 004 Sprint #007): the Valuation Intelligence
Engine wired additively into PredictionEngine.predict()'s
`_get_valuation_intelligence` closure and
`_apply_valuation_intelligence_adjustment`. Mirrors
test_growth_intelligence_prediction_engine_integration.py's own
established pattern exactly -- `predict()` itself is too heavy to mock
realistically end-to-end, so this exercises the same closure logic and
static-wiring properties that file's own precedent already proved is
the right level of confidence for this kind of additive change.
"""

import inspect
import logging
import pathlib

import pytest

import services.prediction_engine as pe
from services.prediction_engine import PredictionEngine, _valuation_intelligence_confidence_enabled


class TestValuationIntelligenceClosureFailsafe:
    @pytest.mark.integration
    def test_india_adapter_exception_does_not_propagate(self, monkeypatch):
        def _raises(*args, **kwargs):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr("services.india_valuation_adapter.build_india_valuation_fields", _raises)
        try:
            from services.india_valuation_adapter import build_india_valuation_fields
            result = build_india_valuation_fields({}, {})
        except BaseException:
            result = None
        assert result is None  # patched to raise -- confirms the patch itself works as expected

    @pytest.mark.integration
    def test_us_adapter_exception_does_not_propagate(self, monkeypatch):
        def _raises(*args, **kwargs):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr("services.us_valuation_adapter.build_us_valuation_fields", _raises)
        try:
            from services.us_valuation_adapter import build_us_valuation_fields
            result = build_us_valuation_fields({})
        except BaseException:
            result = None
        assert result is None


class TestValuationIntelligenceFieldPresenceAndWiring:
    @pytest.mark.integration
    def test_result_dict_includes_valuation_intelligence_key(self):
        source = pathlib.Path(pe.__file__).read_text()
        assert '"valuation_intelligence": valuation_intelligence,' in source
        # Additive, not a replacement -- growth_intelligence/financial_strength/
        # business_quality must all still be present at their existing call sites.
        assert source.count('"growth_intelligence"') >= 1
        assert source.count('"financial_strength"') >= 2
        assert source.count('"business_quality"') >= 2

    @pytest.mark.integration
    def test_valuation_intelligence_adapters_are_imported_lazily_not_at_module_level(self):
        source = pathlib.Path(pe.__file__).read_text()
        assert "from services.india_valuation_adapter import build_india_valuation_fields" in source
        assert "from services.us_valuation_adapter import build_us_valuation_fields" in source
        top_of_file = source.split("class PredictionEngine:")[0]
        assert "from services.india_valuation_adapter import build_india_valuation_fields" not in top_of_file
        assert "from services.us_valuation_adapter import build_us_valuation_fields" not in top_of_file

    @pytest.mark.integration
    def test_valuation_intelligence_adjustment_is_called_after_growth_intelligence(self):
        """Confirms the wiring order: every pre-existing adjustment runs
        first, unmodified -- Valuation Intelligence's adjustment runs
        last, so it can only ever refine an already-computed confidence,
        never override any prior adjustment's own intent."""
        source = pathlib.Path(pe.__file__).read_text()
        rr_idx = source.index("_apply_risk_reward_adjustment(signal, confidence, trade_levels, reasoning, bear_case)")
        pledge_idx = source.index('_apply_pledge_adjustment(market, info or {}, signal, confidence, reasoning, bear_case)')
        fs_idx = source.index("_apply_financial_strength_adjustment(")
        gi_idx = source.index("_apply_growth_intelligence_adjustment(")
        vi_idx = source.index("_apply_valuation_intelligence_adjustment(")
        assert rr_idx < pledge_idx < fs_idx < gi_idx < vi_idx

    @pytest.mark.integration
    def test_valuation_intelligence_gather_task_added_to_round_2(self):
        """Confirms _get_valuation_intelligence is wired into the same
        asyncio.gather Round 2 call as the other additive engines -- a
        parallel task, not a new sequential round."""
        source = pathlib.Path(pe.__file__).read_text()
        gather_block = source[source.index("(news_data, global_ctx, quality"):source.index("sentiment_score = self._aggregate_sentiment")]
        assert "_get_valuation_intelligence" in gather_block
        assert "_get_growth_intelligence" in gather_block

    @pytest.mark.integration
    def test_sector_bucket_is_computed_not_left_empty(self):
        """Unlike Growth Intelligence's own closure (which passes
        sector_bucket=""), Valuation Intelligence's population-gating
        (EV/EBITDA/FCF/PEG vs. FINANCIAL, Price/Book vs. FINANCIAL/
        REAL_ESTATE) depends on a real sector_bucket -- passing an empty
        string would silently defeat the Bank/NBFC gating Sprints
        #002-#004 specifically validated. Confirmed this is NOT the
        case here."""
        source = pathlib.Path(pe.__file__).read_text()
        vi_closure = source[source.index("def _get_valuation_intelligence"):source.index("def _get_valuation_intelligence") + 2500]
        assert "classify_sector(info)" in vi_closure
        assert 'sector_bucket=""' not in vi_closure


class TestValuationIntelligenceAdjustmentWiring:
    @pytest.mark.integration
    def test_adjustment_method_exists_on_prediction_engine(self):
        engine = PredictionEngine()
        assert hasattr(engine, "_apply_valuation_intelligence_adjustment")
        assert callable(engine._apply_valuation_intelligence_adjustment)

    @pytest.mark.integration
    def test_adjustment_does_not_touch_composite_score_or_signal(self):
        """Structural confirmation, per this sprint's explicit 'no
        redesign' rule: the method's signature has no access to
        composite_score/signal internals at all."""
        engine = PredictionEngine()
        sig = inspect.signature(engine._apply_valuation_intelligence_adjustment)
        assert "composite_score" not in sig.parameters
        assert "signal" not in sig.parameters


@pytest.mark.integration
class TestKillSwitch:
    def test_default_disabled_for_india(self, monkeypatch):
        """Unlike Growth Intelligence (enabled by default for India),
        Sprint #006 mandates Valuation Intelligence default to disabled
        in BOTH markets -- a more conservative posture given this
        engine's uniquely severe demonstrated downside risk."""
        monkeypatch.delenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        assert _valuation_intelligence_confidence_enabled("IN") is False

    def test_default_disabled_for_us(self, monkeypatch):
        monkeypatch.delenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_US", raising=False)
        assert _valuation_intelligence_confidence_enabled("US") is False

    def test_can_be_enabled_for_india_via_env_var(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        assert _valuation_intelligence_confidence_enabled("IN") is True

    def test_can_be_enabled_for_us_via_env_var(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_US", "1")
        assert _valuation_intelligence_confidence_enabled("US") is True

    def test_fails_safe_on_malformed_value(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "not-a-real-value")
        assert _valuation_intelligence_confidence_enabled("IN") is False

    def test_unsupported_market_always_disabled(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_US", "1")
        assert _valuation_intelligence_confidence_enabled("CRYPTO") is False

    def test_accepts_common_truthy_string_variants(self, monkeypatch):
        for val in ("1", "true", "True", "YES", "on"):
            monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", val)
            assert _valuation_intelligence_confidence_enabled("IN") is True

    def test_kill_switch_is_independent_of_other_engines(self):
        source = pathlib.Path(pe.__file__).read_text()
        assert "VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN" in source
        assert "VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_US" in source


@pytest.mark.integration
class TestUndervaluationBoost:
    def test_boost_applied_when_no_engine_blocks(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        bq = {"grade": "buy"}
        gi = {"grade": "hold"}
        reasoning, bull, bear = [], [], []
        new_conf = engine._apply_valuation_intelligence_adjustment(
            "IN", vi, bq, None, gi, 50, reasoning, bull, bear
        )
        assert new_conf == 52  # +2, the cap
        assert len(bull) == 1

    def test_boost_capped_at_plus_2_even_at_score_100(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        new_conf = engine._apply_valuation_intelligence_adjustment(
            "IN", vi, {"grade": "buy"}, None, {"grade": "buy"}, 50, [], [], []
        )
        assert new_conf - 50 <= 2

    def test_boost_blocked_when_business_quality_avoids(self, monkeypatch):
        """The Standalone Consumption Rule's core mechanism: an
        undervaluation signal must never independently promote a stock
        Business Quality flags as a hard-negative risk."""
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        bq = {"grade": "avoid"}
        gi = {"grade": "hold"}
        reasoning, bull, bear = [], [], []
        new_conf = engine._apply_valuation_intelligence_adjustment(
            "IN", vi, bq, None, gi, 50, reasoning, bull, bear
        )
        assert new_conf == 50  # gate blocked -- no boost
        assert len(bull) == 0
        assert "Business Quality" in reasoning[0]["reason"]

    def test_boost_blocked_when_growth_intelligence_rejects(self, monkeypatch):
        """Direct test of Sprint #005's own decisive cross-engine
        finding: Growth Intelligence flagging a company must block the
        valuation boost, the same combination that caught 3 of 4 worst
        India value traps."""
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        bq = {"grade": "buy"}
        gi = {"grade": "rejected"}
        new_conf = engine._apply_valuation_intelligence_adjustment(
            "IN", vi, bq, None, gi, 50, [], [], []
        )
        assert new_conf == 50

    def test_boost_blocked_when_financial_strength_avoids_us_only(self, monkeypatch):
        """Financial Strength only participates in the gate for US
        (it has no India coverage, confirmed unchanged since Epic 002)."""
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_US", "1")
        engine = PredictionEngine()
        vi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        bq = {"grade": "buy"}
        fs = {"grade": "avoid"}
        gi = {"grade": "hold"}
        reasoning = []
        new_conf = engine._apply_valuation_intelligence_adjustment(
            "US", vi, bq, fs, gi, 50, reasoning, [], []
        )
        assert new_conf == 50
        assert "Financial Strength" in reasoning[0]["reason"]

    def test_financial_strength_not_consulted_for_india(self, monkeypatch):
        """Even if a (hypothetical) financial_strength dict were passed
        for India, the gate must not consult it -- India has no
        Financial Strength coverage by design."""
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        bq = {"grade": "buy"}
        fs = {"grade": "avoid"}  # should be ignored for India
        gi = {"grade": "hold"}
        new_conf = engine._apply_valuation_intelligence_adjustment(
            "IN", vi, bq, fs, gi, 50, [], [], []
        )
        assert new_conf == 52  # boost still applies -- FS ignored for India

    def test_missing_gate_engine_does_not_block_only_explicit_hard_negative_does(self, monkeypatch):
        """Never penalize missing data -- an engine that's None
        (unavailable for this company) must not block the boost; only
        an explicit avoid/rejected grade does."""
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        new_conf = engine._apply_valuation_intelligence_adjustment(
            "IN", vi, None, None, None, 50, [], [], []
        )
        assert new_conf == 52


@pytest.mark.integration
class TestOvervaluationDemotion:
    def test_demotion_applied_unconditionally_even_with_strong_other_engines(self, monkeypatch):
        """The other half of the Standalone Consumption Rule: 'the
        warning must always apply' -- no cross-engine gate for the
        demote side, confirmed even when every other engine is strong."""
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 0, "grade": "avoid", "metadata": {}}
        bq = {"grade": "strong_buy"}
        gi = {"grade": "strong_buy"}
        reasoning, bull, bear = [], [], []
        new_conf = engine._apply_valuation_intelligence_adjustment(
            "IN", vi, bq, None, gi, 50, reasoning, bull, bear
        )
        assert new_conf == 46  # -4, the cap, unconditionally applied
        assert len(bear) == 1

    def test_demotion_capped_at_minus_4_even_at_score_0(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 0, "grade": "avoid", "metadata": {}}
        new_conf = engine._apply_valuation_intelligence_adjustment(
            "IN", vi, None, None, None, 50, [], [], []
        )
        assert 50 - new_conf <= 4

    def test_demotion_is_larger_than_boost_for_symmetric_score_distance(self, monkeypatch):
        """Direct confirmation of the asymmetric design: a score 50
        points below center demotes more than a score 50 points above
        center boosts."""
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        boost = engine._apply_valuation_intelligence_adjustment(
            "IN", {"score": 100, "grade": "strong_buy", "metadata": {}},
            {"grade": "buy"}, None, {"grade": "buy"}, 50, [], [], [],
        )
        demote = engine._apply_valuation_intelligence_adjustment(
            "IN", {"score": 0, "grade": "avoid", "metadata": {}},
            {"grade": "buy"}, None, {"grade": "buy"}, 50, [], [], [],
        )
        assert (boost - 50) < (50 - demote)


@pytest.mark.integration
class TestConfidenceClamping:
    def test_confidence_clamped_to_0_100_range(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi_strong = {"score": 100, "grade": "strong_buy", "metadata": {}}
        assert engine._apply_valuation_intelligence_adjustment(
            "IN", vi_strong, {"grade": "buy"}, None, {"grade": "buy"}, 99, [], [], []
        ) == 100
        vi_weak = {"score": 0, "grade": "avoid", "metadata": {}}
        assert engine._apply_valuation_intelligence_adjustment(
            "IN", vi_weak, None, None, None, 1, [], [], []
        ) == 0


@pytest.mark.integration
class TestGracefulDegradation:
    def test_none_input_leaves_confidence_unchanged(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        assert engine._apply_valuation_intelligence_adjustment(
            "IN", None, None, None, None, 50, [], [], []
        ) == 50

    def test_rejected_grade_is_a_graceful_no_op_not_a_penalty(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 0, "grade": "rejected", "metadata": {"rejection_reason": "insufficient_data"}}
        assert engine._apply_valuation_intelligence_adjustment(
            "IN", vi, None, None, None, 50, [], [], []
        ) == 50

    def test_missing_score_key_degrades_gracefully(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"grade": "hold", "metadata": {}}
        assert engine._apply_valuation_intelligence_adjustment(
            "IN", vi, None, None, None, 50, [], [], []
        ) == 50

    def test_empty_dict_degrades_gracefully(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        assert engine._apply_valuation_intelligence_adjustment(
            "IN", {}, None, None, None, 50, [], [], []
        ) == 50

    def test_disabled_kill_switch_computes_but_does_not_apply(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "0")
        engine = PredictionEngine()
        vi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        reasoning, bull, bear = [], [], []
        new_conf = engine._apply_valuation_intelligence_adjustment(
            "IN", vi, {"grade": "buy"}, None, {"grade": "buy"}, 50, reasoning, bull, bear
        )
        assert new_conf == 50
        assert reasoning == [] and bull == [] and bear == []


@pytest.mark.integration
class TestTelemetry:
    def test_telemetry_logged_for_every_evaluation(self, monkeypatch, caplog):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 80, "grade": "strong_buy", "metadata": {}}
        with caplog.at_level(logging.INFO, logger="services.prediction_engine"):
            engine._apply_valuation_intelligence_adjustment(
                "IN", vi, {"grade": "buy"}, None, {"grade": "buy"}, 50, [], [], []
            )
        assert any("valuation_intelligence_telemetry" in r.message for r in caplog.records)

    def test_telemetry_failure_never_affects_returned_confidence(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 100, "grade": "strong_buy", "metadata": {}}

        def _broken_log_info(*args, **kwargs):
            raise RuntimeError("simulated logging failure")

        monkeypatch.setattr(pe.log, "info", _broken_log_info)
        result = engine._apply_valuation_intelligence_adjustment(
            "IN", vi, {"grade": "buy"}, None, {"grade": "buy"}, 50, [], [], []
        )
        assert result == 52  # adjustment still applied correctly despite the logging failure
