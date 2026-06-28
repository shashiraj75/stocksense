"""
Golden explainability tests for Growth Intelligence's Prediction Engine
integration (Epic 003 Sprint #007). Confirms `_apply_growth_intelligence_
adjustment`'s reasoning output is deterministic, evidence-based, and does
not duplicate reasoning already produced by other engines (per this
sprint's explicit explainability requirement). These are fixed,
synthetic profiles -- not live-company snapshots -- locking in today's
exact wording/shape so a future change can't silently alter it.
"""

import pytest

from services.prediction_engine import PredictionEngine


@pytest.mark.golden
class TestGrowthIntelligenceExplainabilityGolden:
    def test_india_strong_buy_reasoning_is_deterministic(self):
        engine = PredictionEngine()
        gi = {"score": 90, "grade": "strong_buy", "metadata": {}}
        results = [
            engine._apply_growth_intelligence_adjustment("IN", gi, 50, [], [], [])
            for _ in range(5)
        ]
        assert len(set(results)) == 1  # identical input -> identical output, every time

    def test_india_strong_buy_reasoning_exact_shape(self, monkeypatch):
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        engine = PredictionEngine()
        gi = {"score": 90, "grade": "strong_buy", "metadata": {}}
        reasoning, bull, bear = [], [], []
        engine._apply_growth_intelligence_adjustment("IN", gi, 50, reasoning, bull, bear)

        assert reasoning == [{
            "indicator": "Growth Intelligence",
            "signal": "BULLISH",
            "reason": "Growth Intelligence Score 90/100 (strong_buy) — confidence boosted by 2 point(s).",
        }]
        assert bull == ["Growth Intelligence Score 90/100 (strong_buy) — confidence boosted by 2 point(s)."]
        assert bear == []

    def test_india_avoid_reasoning_exact_shape(self, monkeypatch):
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        engine = PredictionEngine()
        gi = {"score": 10, "grade": "avoid", "metadata": {}}
        reasoning, bull, bear = [], [], []
        engine._apply_growth_intelligence_adjustment("IN", gi, 50, reasoning, bull, bear)

        assert reasoning == [{
            "indicator": "Growth Intelligence",
            "signal": "BEARISH",
            "reason": "Growth Intelligence Score 10/100 (avoid) — confidence demoted by 2 point(s).",
        }]
        assert bear == ["Growth Intelligence Score 10/100 (avoid) — confidence demoted by 2 point(s)."]
        assert bull == []

    def test_reasoning_indicator_label_is_distinct_from_financial_strength(self, monkeypatch):
        """Confirms the two engines' explanations are never confusable --
        no shared/duplicated indicator label, even though both produce a
        structurally similar {indicator, signal, reason} entry."""
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        engine = PredictionEngine()
        gi_reasoning = []
        engine._apply_growth_intelligence_adjustment(
            "IN", {"score": 90, "grade": "strong_buy", "metadata": {}}, 50, gi_reasoning, [], [])

        fs_reasoning = []
        engine._apply_financial_strength_adjustment(
            "US", {"score": 90, "grade": "strong_buy", "metadata": {}}, 50, fs_reasoning, [], [])

        assert gi_reasoning[0]["indicator"] == "Growth Intelligence"
        assert fs_reasoning[0]["indicator"] == "Financial Strength"
        assert gi_reasoning[0]["indicator"] != fs_reasoning[0]["indicator"]
        assert gi_reasoning[0]["reason"] != fs_reasoning[0]["reason"]

    def test_zero_adjustment_produces_no_reasoning_entry(self, monkeypatch):
        """A score that rounds to a 0-point adjustment must not add a
        hollow/no-op reasoning entry -- explainability should only ever
        describe a real, non-zero effect."""
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        engine = PredictionEngine()
        gi = {"score": 50, "grade": "hold", "metadata": {}}  # (50-50)/50*3 = 0
        reasoning, bull, bear = [], [], []
        result = engine._apply_growth_intelligence_adjustment("IN", gi, 50, reasoning, bull, bear)
        assert result == 50
        assert reasoning == [] and bull == [] and bear == []

    def test_us_explainability_is_empty_not_a_placeholder_message(self, monkeypatch):
        """US must not get a fabricated 'not applicable' reasoning entry
        -- per Sprint #007, explainability for US comes from the
        growth_intelligence dict itself (always populated), not from
        this adjustment function, which should stay silent for US."""
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_US", raising=False)
        engine = PredictionEngine()
        gi = {"score": 90, "grade": "strong_buy", "metadata": {}}
        reasoning, bull, bear = [], [], []
        engine._apply_growth_intelligence_adjustment("US", gi, 50, reasoning, bull, bear)
        assert reasoning == []
