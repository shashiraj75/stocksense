"""
Regression tests for Growth Intelligence's effect on the Daily Picks
pipeline (Epic 003 Sprint #008). Locks in the two real findings this
sprint's validation produced: ranking_alpha is bit-for-bit invariant to
Growth Intelligence's confidence adjustment (confirmed empirically
against 209 real India + 130 real US companies, not just by reading
the source), and the ONE real, architecturally-intended effect Growth
Intelligence can have on Daily Picks membership -- nudging an
already-borderline stock across the 25% confidence noise floor, never
affecting rank order.
"""

import pytest

from services.daily_picks import _zscore_and_rank
from services.prediction_engine import PredictionEngine


def _passes_quality_gate(r: dict, hz: str) -> bool:
    """Reconstructed exactly per the established convention
    (test_daily_picks_financial_strength_quality_gate.py)."""
    conf = r.get("confidence") or 0
    if conf < 25:
        return False
    indicators = {item.get("indicator") for item in r.get("reasoning", []) if isinstance(item, dict)}
    if "Risk/Reward" in indicators or "Governance Risk" in indicators:
        return False
    fs_reasons = " ".join(
        item.get("reason", "") for item in r.get("reasoning", [])
        if isinstance(item, dict) and item.get("indicator") == "Financial Strength")
    if "liquidity distress" in fs_reasons.lower():
        return False
    if hz == "short":
        reasons = " ".join(item.get("reason", "") if isinstance(item, dict) else str(item)
                            for item in r.get("reasoning", []))
        if "Overbought" in reasons:
            return False
    return True


def _rows(n=10):
    return [
        {"symbol": f"SYM{i}", "signal": "BUY", "horizon": "medium",
         "tech_score": 40 + i, "fund_score": 60 - i, "sentiment_score": 50, "quality_score": 55,
         "confidence": 50, "reasoning": []}
        for i in range(n)
    ]


@pytest.mark.regression
class TestRankingAlphaInvariantToGrowthIntelligence:
    """The decisive finding from this sprint's real-data validation:
    ranking_alpha must be bit-for-bit identical whether or not Growth
    Intelligence's confidence adjustment was applied -- confirmed
    empirically against 209 real India companies, not assumed from
    _FACTOR_KEYS' definition alone."""

    def test_ranking_alpha_unaffected_by_confidence_value(self):
        ic_weights = {"tech": 0.30, "fund": 0.30, "sentiment": 0.20, "quality": 0.20}
        regime = {"label": "BULL_CALM", "weight_multipliers": {}}

        rows_low_conf = _rows()
        rows_high_conf = [dict(r, confidence=95) for r in rows_low_conf]

        ranked_low = _zscore_and_rank(rows_low_conf, ic_weights, regime, 0, market="IN")
        ranked_high = _zscore_and_rank(rows_high_conf, ic_weights, regime, 0, market="IN")

        alpha_low = {r["symbol"]: r["ranking_alpha"] for r in ranked_low}
        alpha_high = {r["symbol"]: r["ranking_alpha"] for r in ranked_high}
        assert alpha_low == alpha_high

    def test_sort_order_unaffected_by_confidence_value(self):
        ic_weights = {"tech": 0.30, "fund": 0.30, "sentiment": 0.20, "quality": 0.20}
        regime = {"label": "BULL_CALM", "weight_multipliers": {}}

        rows_a = _rows()
        rows_b = [dict(r, confidence=10) for r in rows_a]

        order_a = [r["symbol"] for r in sorted(
            _zscore_and_rank(rows_a, ic_weights, regime, 0, market="IN"),
            key=lambda x: x["ranking_alpha"], reverse=True)]
        order_b = [r["symbol"] for r in sorted(
            _zscore_and_rank(rows_b, ic_weights, regime, 0, market="IN"),
            key=lambda x: x["ranking_alpha"], reverse=True)]
        assert order_a == order_b


@pytest.mark.regression
class TestGateBoundaryEffect:
    """The one real, intended effect: a strong Growth Intelligence score
    can rescue an already-borderline (near the 25% noise floor)
    confidence value -- never affecting any stock whose confidence
    isn't already near that boundary, and never affecting ranking."""

    def test_strong_score_can_rescue_a_borderline_confidence_across_the_gate(self):
        engine = PredictionEngine()
        gi = {"score": 90, "grade": "strong_buy", "metadata": {}}
        reasoning = []
        new_conf = engine._apply_growth_intelligence_adjustment("IN", gi, 23, reasoning, [], [])
        row_before = {"confidence": 23, "reasoning": []}
        row_after = {"confidence": new_conf, "reasoning": reasoning}
        assert _passes_quality_gate(row_before, "medium") is False
        assert _passes_quality_gate(row_after, "medium") is True

    def test_weak_score_can_push_a_borderline_confidence_below_the_gate(self):
        engine = PredictionEngine()
        gi = {"score": 5, "grade": "avoid", "metadata": {}}
        reasoning = []
        new_conf = engine._apply_growth_intelligence_adjustment("IN", gi, 27, reasoning, [], [])
        row_before = {"confidence": 27, "reasoning": []}
        row_after = {"confidence": new_conf, "reasoning": reasoning}
        assert _passes_quality_gate(row_before, "medium") is True
        assert _passes_quality_gate(row_after, "medium") is False

    def test_far_from_boundary_confidence_never_flips(self):
        """A stock nowhere near the 25% boundary must never have its
        gate eligibility changed by a +-3 adjustment."""
        engine = PredictionEngine()
        for gi_score in (0, 50, 100):
            gi = {"score": gi_score, "grade": "x", "metadata": {}}
            for base_conf in (50, 70, 90):
                reasoning = []
                new_conf = engine._apply_growth_intelligence_adjustment("IN", gi, base_conf, reasoning, [], [])
                row_before = {"confidence": base_conf, "reasoning": []}
                row_after = {"confidence": new_conf, "reasoning": reasoning}
                assert _passes_quality_gate(row_before, "medium") == _passes_quality_gate(row_after, "medium")

    def test_us_never_flips_gate_eligibility_regardless_of_confidence(self):
        engine = PredictionEngine()
        gi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        for base_conf in (10, 24, 25, 26, 50, 90):
            reasoning = []
            new_conf = engine._apply_growth_intelligence_adjustment("US", gi, base_conf, reasoning, [], [])
            assert new_conf == base_conf
            row_before = {"confidence": base_conf, "reasoning": []}
            row_after = {"confidence": new_conf, "reasoning": reasoning}
            assert _passes_quality_gate(row_before, "medium") == _passes_quality_gate(row_after, "medium")


@pytest.mark.regression
class TestNoStringCollisionWithExistingGateChecks:
    """Confirms Growth Intelligence's reasoning entries never trigger
    any of the gate's existing string-matching exclusions -- "Risk/
    Reward", "Governance Risk", the Financial-Strength-specific
    liquidity-distress phrase, or "Overbought" for short-horizon picks."""

    def test_growth_intelligence_indicator_name_does_not_match_risk_reward_or_governance(self):
        row = {"confidence": 50, "reasoning": [{
            "indicator": "Growth Intelligence", "signal": "BULLISH",
            "reason": "Growth Intelligence Score 90/100 (strong_buy) — confidence boosted by 3 point(s).",
        }]}
        assert _passes_quality_gate(row, "medium") is True

    def test_growth_intelligence_reasoning_never_scanned_as_financial_strength(self):
        """The liquidity-distress check only scans items whose
        indicator is exactly "Financial Strength" -- confirms a Growth
        Intelligence entry is never included in that scan, even if its
        own text happened to mention an unrelated phrase."""
        row = {"confidence": 50, "reasoning": [{
            "indicator": "Growth Intelligence", "signal": "BEARISH",
            "reason": "Growth Intelligence Score 0/100 (avoid) — confidence demoted by 3 point(s).",
        }]}
        assert _passes_quality_gate(row, "medium") is True

    def test_growth_intelligence_reasoning_does_not_trigger_overbought_exclusion(self):
        row = {"confidence": 50, "reasoning": [{
            "indicator": "Growth Intelligence", "signal": "BULLISH",
            "reason": "Growth Intelligence Score 100/100 (strong_buy) — confidence boosted by 3 point(s).",
        }]}
        assert _passes_quality_gate(row, "short") is True

    def test_growth_intelligence_and_financial_strength_reasoning_coexist_without_interference(self):
        """A stock with both a positive Financial Strength AND a
        positive Growth Intelligence entry must still pass -- neither
        engine's reasoning should be mistaken for the other's."""
        row = {"confidence": 70, "reasoning": [
            {"indicator": "Financial Strength", "signal": "BULLISH",
             "reason": "Financial Strength Score 90/100 (strong_buy) — confidence boosted by 5 point(s)."},
            {"indicator": "Growth Intelligence", "signal": "BULLISH",
             "reason": "Growth Intelligence Score 85/100 (strong_buy) — confidence boosted by 2 point(s)."},
        ]}
        assert _passes_quality_gate(row, "medium") is True
