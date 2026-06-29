"""
Regression tests for Valuation Intelligence's effect on the Daily Picks
pipeline (Epic 004 Sprint #008). Locks in the findings this sprint's
real-data validation produced (361 real companies, 206 India + 155 US):
ranking_alpha is bit-for-bit invariant to Valuation Intelligence's
confidence adjustment, the eligibility-floor rescue/sink effect is real
and bounded to the 25%-boundary region in BOTH markets (unlike Growth
Intelligence, Valuation Intelligence is not hard-gated to India only —
Sprint #006's own decision), the cross-engine gate blocks RELINFRA/VEDL
live, and no reasoning string-collision exists with the pre-existing
quality-gate checks, including the new gate-blocked NEUTRAL message
this engine's adjustment can produce.
"""

import pytest

from services.daily_picks import _zscore_and_rank
from services.prediction_engine import PredictionEngine


def _passes_quality_gate(r: dict, hz: str) -> bool:
    """Reconstructed exactly per the established convention
    (test_growth_intelligence_daily_picks_regression.py)."""
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
class TestRankingAlphaInvariantToValuationIntelligence:
    """The decisive finding from this sprint's real-data validation
    (after correcting two methodology bugs in the validation script
    itself — a shared-random-state mismatch and a stale kill-switch
    env var — both fixed before drawing this conclusion): ranking_alpha
    is bit-for-bit identical whether or not Valuation Intelligence's
    confidence adjustment was applied — confirmed empirically against
    206 real India + 155 real US companies."""

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

    def test_ranking_alpha_unaffected_for_us_market_too(self):
        """Unlike Growth Intelligence (hard-gated to India only at the
        adjustment level), Valuation Intelligence's confidence-only
        design applies to BOTH markets per Sprint #006 — confirming
        ranking invariance must hold for US too, not just India."""
        ic_weights = {"tech": 0.30, "fund": 0.30, "sentiment": 0.20, "quality": 0.20}
        regime = {"label": "BULL_CALM", "weight_multipliers": {}}

        rows_a = _rows()
        rows_b = [dict(r, confidence=5) for r in rows_a]

        alpha_a = {r["symbol"]: r["ranking_alpha"] for r in _zscore_and_rank(rows_a, ic_weights, regime, 0, market="US")}
        alpha_b = {r["symbol"]: r["ranking_alpha"] for r in _zscore_and_rank(rows_b, ic_weights, regime, 0, market="US")}
        assert alpha_a == alpha_b


@pytest.mark.regression
class TestGateBoundaryEffect:
    """The one real, intended effect: a strong, gate-cleared Valuation
    Intelligence score can rescue an already-borderline confidence
    value across the 25% noise floor; a weak score can sink one —
    never affecting a stock whose confidence isn't already near that
    boundary, and never affecting ranking. Confirmed in both markets,
    since this engine's confidence influence is not India-only."""

    def test_strong_score_can_rescue_a_borderline_confidence_when_gate_clears(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        gi = {"grade": "buy"}
        reasoning = []
        new_conf = engine._apply_valuation_intelligence_adjustment("IN", vi, {"grade": "buy"}, None, gi, 24, reasoning, [], [])
        row_before = {"confidence": 24, "reasoning": []}
        row_after = {"confidence": new_conf, "reasoning": reasoning}
        assert _passes_quality_gate(row_before, "medium") is False
        assert _passes_quality_gate(row_after, "medium") is True

    def test_weak_score_can_push_a_borderline_confidence_below_the_gate(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 0, "grade": "avoid", "metadata": {}}
        reasoning = []
        new_conf = engine._apply_valuation_intelligence_adjustment("IN", vi, None, None, None, 27, reasoning, [], [])
        row_before = {"confidence": 27, "reasoning": []}
        row_after = {"confidence": new_conf, "reasoning": reasoning}
        assert _passes_quality_gate(row_before, "medium") is True
        assert _passes_quality_gate(row_after, "medium") is False

    def test_far_from_boundary_confidence_never_flips(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        for vi_score in (0, 50, 100):
            vi = {"score": vi_score, "grade": "x", "metadata": {}}
            for base_conf in (50, 70, 90):
                reasoning = []
                new_conf = engine._apply_valuation_intelligence_adjustment("IN", vi, {"grade": "buy"}, None, {"grade": "buy"}, base_conf, reasoning, [], [])
                row_before = {"confidence": base_conf, "reasoning": []}
                row_after = {"confidence": new_conf, "reasoning": reasoning}
                assert _passes_quality_gate(row_before, "medium") == _passes_quality_gate(row_after, "medium")

    def test_gate_blocked_boost_never_flips_eligibility(self, monkeypatch):
        """A genuinely cheap score that the cross-engine gate suppresses
        must behave identically to having no Valuation Intelligence
        signal at all -- it must never rescue a borderline stock."""
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        gi_avoid = {"grade": "avoid"}
        reasoning = []
        new_conf = engine._apply_valuation_intelligence_adjustment("IN", vi, {"grade": "buy"}, None, gi_avoid, 24, reasoning, [], [])
        assert new_conf == 24  # gate blocked -- no rescue
        row_before = {"confidence": 24, "reasoning": []}
        row_after = {"confidence": new_conf, "reasoning": reasoning}
        assert _passes_quality_gate(row_before, "medium") == _passes_quality_gate(row_after, "medium") is False


@pytest.mark.regression
class TestCrossEngineGateOnNamedValueTraps:
    """Direct regression lock on this sprint's own live re-validation
    against the exact companies Sprint #005 identified as the worst
    known false positives — confirmed blocked TODAY, not just
    historically, using the real grades fetched during this sprint."""

    def test_relinfra_shaped_profile_is_blocked(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        gi = {"grade": "avoid"}  # Growth Intelligence's real, live grade for RELINFRA this sprint
        reasoning = []
        new_conf = engine._apply_valuation_intelligence_adjustment("IN", vi, None, None, gi, 50, reasoning, [], [])
        assert new_conf == 50
        assert "Growth Intelligence" in reasoning[0]["reason"]

    def test_vedl_shaped_profile_is_blocked(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 92, "grade": "strong_buy", "metadata": {}}
        gi = {"grade": "avoid"}  # Growth Intelligence's real, live grade for VEDL this sprint
        new_conf = engine._apply_valuation_intelligence_adjustment("IN", vi, None, None, gi, 50, [], [], [])
        assert new_conf == 50

    def test_relcapital_shaped_profile_is_the_disclosed_exception(self, monkeypatch):
        """RELCAPITAL's real, live Growth Intelligence grade this
        sprint was "hold," not avoid/rejected — the gate does NOT block
        it, exactly the honest, disclosed limitation Sprint #007 already
        named (Growth Intelligence catches 3 of 4 known traps, not 4 of
        4). Locked in here so a future change can't silently alter this
        known, accepted behavior without it being noticed."""
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 83, "grade": "strong_buy", "metadata": {}}
        gi = {"grade": "hold"}
        new_conf = engine._apply_valuation_intelligence_adjustment("IN", vi, None, None, gi, 50, [], [], [])
        assert new_conf > 50  # boost goes through -- the known, accepted gap


@pytest.mark.regression
class TestNoStringCollisionWithExistingGateChecks:
    """Confirms Valuation Intelligence's reasoning entries -- including
    the NEW gate-blocked NEUTRAL message this engine can produce, which
    Growth Intelligence's own equivalent test file never had to check --
    never trigger any of the gate's existing string-matching exclusions."""

    def test_boost_indicator_name_does_not_match_risk_reward_or_governance(self):
        row = {"confidence": 52, "reasoning": [{
            "indicator": "Valuation Intelligence", "signal": "BULLISH",
            "reason": "Valuation Intelligence Score 90/100 (strong_buy) — confidence boosted by 2 point(s).",
        }]}
        assert _passes_quality_gate(row, "medium") is True

    def test_demotion_indicator_name_does_not_match_risk_reward_or_governance(self):
        row = {"confidence": 46, "reasoning": [{
            "indicator": "Valuation Intelligence", "signal": "BEARISH",
            "reason": "Valuation Intelligence Score 10/100 (avoid) — confidence demoted by 3 point(s).",
        }]}
        assert _passes_quality_gate(row, "medium") is True

    def test_gate_blocked_neutral_message_does_not_match_risk_reward_or_governance(self):
        """The Standalone Consumption Rule's own explainability message
        uses the word 'risk' in its text ('...flagged this company as a
        hard-negative risk...') -- confirms this does NOT collide with
        the gate's exact-indicator-name-based 'Risk/Reward'/'Governance
        Risk' exclusion set, which matches on indicator name, not
        free-text content."""
        row = {"confidence": 50, "reasoning": [{
            "indicator": "Valuation Intelligence", "signal": "NEUTRAL",
            "reason": ("Valuation Intelligence Score 90/100 (strong_buy) suggests undervaluation, but no "
                       "confidence boost was applied — Growth Intelligence flagged this company as a "
                       "hard-negative risk (Standalone Consumption Rule)."),
        }]}
        assert _passes_quality_gate(row, "medium") is True

    def test_valuation_intelligence_reasoning_never_scanned_as_financial_strength(self):
        row = {"confidence": 50, "reasoning": [{
            "indicator": "Valuation Intelligence", "signal": "BEARISH",
            "reason": "Valuation Intelligence Score 0/100 (avoid) — confidence demoted by 4 point(s).",
        }]}
        assert _passes_quality_gate(row, "medium") is True

    def test_valuation_intelligence_reasoning_does_not_trigger_overbought_exclusion(self):
        row = {"confidence": 52, "reasoning": [{
            "indicator": "Valuation Intelligence", "signal": "BULLISH",
            "reason": "Valuation Intelligence Score 100/100 (strong_buy) — confidence boosted by 2 point(s).",
        }]}
        assert _passes_quality_gate(row, "short") is True

    def test_valuation_growth_and_financial_strength_reasoning_coexist_without_interference(self):
        """A stock with positive Financial Strength, Growth
        Intelligence, AND Valuation Intelligence entries simultaneously
        must still pass -- no engine's reasoning is mistaken for
        another's, confirmed with all three additive engines present at
        once for the first time."""
        row = {"confidence": 80, "reasoning": [
            {"indicator": "Financial Strength", "signal": "BULLISH",
             "reason": "Financial Strength Score 90/100 (strong_buy) — confidence boosted by 5 point(s)."},
            {"indicator": "Growth Intelligence", "signal": "BULLISH",
             "reason": "Growth Intelligence Score 85/100 (strong_buy) — confidence boosted by 2 point(s)."},
            {"indicator": "Valuation Intelligence", "signal": "BULLISH",
             "reason": "Valuation Intelligence Score 90/100 (strong_buy) — confidence boosted by 2 point(s)."},
        ]}
        assert _passes_quality_gate(row, "medium") is True
