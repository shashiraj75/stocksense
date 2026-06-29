"""
Golden explainability tests for Valuation Intelligence's Prediction
Engine integration (Epic 004 Sprint #007). Confirms
`_apply_valuation_intelligence_adjustment`'s reasoning output is
deterministic, evidence-based, and does not duplicate reasoning
already produced by other engines (per this sprint's explicit
explainability requirement). These are fixed, synthetic profiles -- not
live-company snapshots -- locking in today's exact wording/shape so a
future change can't silently alter it.
"""

import pytest

from services.prediction_engine import PredictionEngine


@pytest.mark.golden
class TestValuationIntelligenceExplainabilityGolden:
    def test_undervaluation_boost_is_deterministic(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 90, "grade": "strong_buy", "metadata": {}}
        results = [
            engine._apply_valuation_intelligence_adjustment(
                "IN", vi, {"grade": "buy"}, None, {"grade": "buy"}, 50, [], [], []
            )
            for _ in range(5)
        ]
        assert len(set(results)) == 1  # identical input -> identical output, every time

    def test_undervaluation_boost_reasoning_exact_shape(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 90, "grade": "strong_buy", "metadata": {}}
        reasoning, bull, bear = [], [], []
        engine._apply_valuation_intelligence_adjustment(
            "IN", vi, {"grade": "buy"}, None, {"grade": "buy"}, 50, reasoning, bull, bear
        )

        assert reasoning == [{
            "indicator": "Valuation Intelligence",
            "signal": "BULLISH",
            "reason": "Valuation Intelligence Score 90/100 (strong_buy) — confidence boosted by 2 point(s).",
        }]
        assert bull == ["Valuation Intelligence Score 90/100 (strong_buy) — confidence boosted by 2 point(s)."]
        assert bear == []

    def test_overvaluation_demotion_reasoning_exact_shape(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 10, "grade": "avoid", "metadata": {}}
        reasoning, bull, bear = [], [], []
        engine._apply_valuation_intelligence_adjustment(
            "IN", vi, None, None, None, 50, reasoning, bull, bear
        )

        assert reasoning == [{
            "indicator": "Valuation Intelligence",
            "signal": "BEARISH",
            "reason": "Valuation Intelligence Score 10/100 (avoid) — confidence demoted by 3 point(s).",
        }]
        assert bear == ["Valuation Intelligence Score 10/100 (avoid) — confidence demoted by 3 point(s)."]
        assert bull == []

    def test_gate_blocked_reasoning_exact_shape(self, monkeypatch):
        """The Standalone Consumption Rule's own explainability: when a
        positive valuation signal is suppressed by the cross-engine
        gate, the reasoning must say so explicitly, not silently produce
        zero adjustment with no explanation."""
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 90, "grade": "strong_buy", "metadata": {}}
        reasoning, bull, bear = [], [], []
        engine._apply_valuation_intelligence_adjustment(
            "IN", vi, {"grade": "avoid"}, None, {"grade": "buy"}, 50, reasoning, bull, bear
        )

        assert len(reasoning) == 1
        assert reasoning[0]["indicator"] == "Valuation Intelligence"
        assert reasoning[0]["signal"] == "NEUTRAL"
        assert "Business Quality" in reasoning[0]["reason"]
        assert "Standalone Consumption Rule" in reasoning[0]["reason"]
        assert bull == [] and bear == []

    def test_disabled_kill_switch_produces_no_reasoning_at_all(self, monkeypatch):
        """No generic/placeholder text when the switch is off -- exactly
        nothing is added, mirroring Growth Intelligence's US-disabled
        behavior."""
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "0")
        engine = PredictionEngine()
        vi = {"score": 90, "grade": "strong_buy", "metadata": {}}
        reasoning, bull, bear = [], [], []
        engine._apply_valuation_intelligence_adjustment(
            "IN", vi, {"grade": "buy"}, None, {"grade": "buy"}, 50, reasoning, bull, bear
        )
        assert reasoning == [] and bull == [] and bear == []

    def test_no_duplicated_reasoning_text_across_engines(self, monkeypatch):
        """Confirms Valuation Intelligence's own reasoning text is
        distinct from Growth Intelligence's -- both engines can fire in
        the same predict() call without producing identical-looking
        explanation strings a user couldn't tell apart."""
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        engine = PredictionEngine()
        gi = {"score": 90, "grade": "strong_buy", "metadata": {}}
        vi = {"score": 90, "grade": "strong_buy", "metadata": {}}
        reasoning = []
        engine._apply_growth_intelligence_adjustment("IN", gi, 50, reasoning, [], [])
        engine._apply_valuation_intelligence_adjustment(
            "IN", vi, {"grade": "buy"}, None, gi, 50, reasoning, [], []
        )
        assert reasoning[0]["reason"] != reasoning[1]["reason"]
        assert reasoning[0]["indicator"] != reasoning[1]["indicator"]
