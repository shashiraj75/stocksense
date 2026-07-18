"""
DP-025 foundation — parity proof for the three functions extracted from
_generate_picks_inner's per-horizon loop into module-level, pure helpers:

  - _passes_quality_gate()      (was a nested closure)
  - _select_short_term_top_six()  (was an inline high_conf/rest block)
  - _compute_portfolio_allocation() (was inline optimize()+cash-math)

The extraction's whole point is that services/validation/pipeline_replay.py
can call the SAME functions production uses, instead of a second,
potentially-diverging implementation. These tests prove the extraction
changed nothing observable: each function's behaviour here is identical to
what the pre-extraction inline code did (verified against the existing,
still-passing test_daily_picks_phase5_tier_and_confidence_selection.py,
test_daily_picks_output_integrity.py, test_daily_picks_portfolio_cash.py,
and test_optimizer_portfolio_cash_cap.py — all still pass unmodified,
which is itself part of this parity proof).
"""

import services.daily_picks as dp


def _candidate(symbol, confidence=70, reasoning=None):
    return {"symbol": symbol, "confidence": confidence, "reasoning": reasoning or []}


class TestPassesQualityGateExtractedFunction:
    def test_low_confidence_rejected(self):
        assert dp._passes_quality_gate(_candidate("AAA", confidence=10), "medium") is False

    def test_confidence_exactly_25_passes(self):
        assert dp._passes_quality_gate(_candidate("AAA", confidence=25), "medium") is True

    def test_risk_reward_indicator_rejected(self):
        c = _candidate("AAA", confidence=80, reasoning=[{"indicator": "Risk/Reward", "reason": "bad R:R"}])
        assert dp._passes_quality_gate(c, "medium") is False

    def test_governance_risk_indicator_rejected(self):
        c = _candidate("AAA", confidence=80, reasoning=[{"indicator": "Governance Risk", "reason": "pledge"}])
        assert dp._passes_quality_gate(c, "medium") is False

    def test_liquidity_distress_phrase_rejected(self):
        c = _candidate("AAA", confidence=80, reasoning=[
            {"indicator": "Financial Strength", "reason": "Confirmed liquidity distress: current ratio 0.4"}
        ])
        assert dp._passes_quality_gate(c, "medium") is False

    def test_positive_financial_strength_not_rejected(self):
        c = _candidate("AAA", confidence=80, reasoning=[
            {"indicator": "Financial Strength", "reason": "Fortress balance sheet, low debt"}
        ])
        assert dp._passes_quality_gate(c, "medium") is True

    def test_overbought_rejected_only_for_short_horizon(self):
        c = _candidate("AAA", confidence=80, reasoning=[{"indicator": "RSI", "reason": "Overbought at 82"}])
        assert dp._passes_quality_gate(c, "short") is False
        assert dp._passes_quality_gate(c, "medium") is True

    def test_clean_candidate_passes(self):
        c = _candidate("AAA", confidence=90, reasoning=[{"indicator": "Momentum", "reason": "strong trend"}])
        assert dp._passes_quality_gate(c, "medium") is True
        assert dp._passes_quality_gate(c, "short") is True


class TestSelectShortTermTopSixExtractedFunction:
    def test_high_confidence_bucket_prioritized_over_rest(self):
        candidates = [
            {"symbol": "LOW1", "confidence": 79},
            {"symbol": "HIGH1", "confidence": 85},
            {"symbol": "LOW2", "confidence": 60},
            {"symbol": "HIGH2", "confidence": 90},
        ]
        result = dp._select_short_term_top_six(candidates)
        symbols = [r["symbol"] for r in result]
        assert symbols == ["HIGH1", "HIGH2", "LOW1", "LOW2"]

    def test_caps_at_six(self):
        candidates = [{"symbol": f"S{i}", "confidence": 90} for i in range(10)]
        assert len(dp._select_short_term_top_six(candidates)) == 6

    def test_boundary_confidence_exactly_80_is_not_high_conf_bucket(self):
        candidates = [{"symbol": "EXACTLY80", "confidence": 80}, {"symbol": "ABOVE80", "confidence": 81}]
        result = dp._select_short_term_top_six(candidates)
        assert [r["symbol"] for r in result] == ["ABOVE80", "EXACTLY80"]

    def test_empty_input_returns_empty(self):
        assert dp._select_short_term_top_six([]) == []


class TestComputePortfolioAllocationExtractedFunction:
    def test_empty_alphas_returns_empty(self):
        weights, cash = dp._compute_portfolio_allocation([], None, "BULL_CALM")
        assert weights == []
        assert cash == 0.0

    def test_single_alpha_respects_default_forty_percent_cap(self):
        # DP-021: a single candidate is no longer special-cased at 50% —
        # it obeys the same max_weight cap as every other candidate count,
        # with the shortfall left as cash.
        weights, cash = dp._compute_portfolio_allocation([5.0], None, "BULL_CALM")
        assert weights == [0.40]
        assert cash == 0.60

    def test_single_alpha_respects_custom_max_weight(self):
        weights, cash = dp._compute_portfolio_allocation([5.0], None, "BULL_CALM", max_weight=0.25)
        assert weights == [0.25]
        assert cash == 0.75

    def test_two_alphas_at_default_cap_yields_forty_forty_twenty_cash(self):
        weights, cash = dp._compute_portfolio_allocation([1.0, 1.0], None, "BULL_CALM")
        assert weights == [0.4, 0.4]
        assert cash == 0.2

    def test_matches_direct_optimizer_call_for_three_or_more(self):
        from services.alpha_engine.optimizer import optimize
        alphas = [3.0, 2.0, 1.0]
        weights, cash = dp._compute_portfolio_allocation(alphas, None, "BULL_CALM")
        direct = optimize(alphas=alphas, returns_matrix=None, max_weight=0.40, risk_aversion=2.0, regime_label="BULL_CALM")
        assert weights == direct
        assert cash == round(max(0.0, 1.0 - sum(direct)), 4)
