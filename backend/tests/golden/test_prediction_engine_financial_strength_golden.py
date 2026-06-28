"""
Golden tests for PredictionEngine._apply_financial_strength_adjustment's
full explainability output (Epic 002 Sprint #010) — asserts the
complete reasoning/bull_case/bear_case shape, not just the returned
confidence number, per SES-003 §6's "assert the full output structure"
rule. Fixture financial_strength dicts are loosely modeled on real
Sprint #008/#009 company profiles (a fortress-balance-sheet profile,
a leveraged/weak profile, and the real AEP liquidity_distress shape
post-Sprint-#009 calibration).
"""

import pytest

from services.prediction_engine import PredictionEngine


@pytest.fixture
def engine():
    return PredictionEngine()


@pytest.mark.golden
def test_golden_fortress_company_boosts_confidence_with_full_explainability(engine):
    """Loosely modeled on real GOOGL-shaped Sprint #008 output (score 100)."""
    financial_strength = {
        "score": 100, "grade": "strong_buy",
        "strengths": ["Liquidity Adequacy: Current ratio 2.01x — comfortably covers current liabilities"],
        "weaknesses": [],
        "metadata": {"sector_bucket": "TELECOM", "data_completeness_pct": 100.0},
    }
    reasoning, bull_case, bear_case = [], [], []
    result = engine._apply_financial_strength_adjustment("US", financial_strength, 70, reasoning, bull_case, bear_case)

    assert result == 76
    assert len(reasoning) == 1
    assert reasoning[0]["indicator"] == "Financial Strength"
    assert reasoning[0]["signal"] == "BULLISH"
    assert "100/100" in reasoning[0]["reason"]
    assert "boosted by 6" in reasoning[0]["reason"]
    assert len(bull_case) == 1
    assert bear_case == []


@pytest.mark.golden
def test_golden_weak_leveraged_company_demotes_confidence_with_full_explainability(engine):
    """Loosely modeled on a real BA-shaped Sprint #008 output (score 12,
    severe leverage, D/E 910%)."""
    financial_strength = {
        "score": 12, "grade": "avoid",
        "strengths": [],
        "weaknesses": ["Leverage & Capital Structure: Debt-to-equity 910% — severe leverage"],
        "risks": ["Severe leverage: debt-to-equity 910%"],
        "metadata": {"sector_bucket": "MANUFACTURING", "data_completeness_pct": 100.0},
    }
    reasoning, bull_case, bear_case = [], [], []
    result = engine._apply_financial_strength_adjustment("US", financial_strength, 70, reasoning, bull_case, bear_case)

    assert result == 65  # round((12-50)/50*6) = round(-4.56) = -5 -> 70-5=65
    assert reasoning[0]["signal"] == "BEARISH"
    assert "12/100" in reasoning[0]["reason"]
    assert "demoted by 5" in reasoning[0]["reason"]
    assert len(bear_case) == 1
    assert bull_case == []


@pytest.mark.golden
def test_golden_aep_post_sprint_009_calibration_shape_no_longer_hard_gated(engine):
    """Real, live-confirmed AEP shape AFTER Sprint #009's calibration fix
    (the UTILITIES_ENERGY hard-gate exemption) — AEP is no longer
    `rejected`/`liquidity_distress`; it scores 18/avoid via soft scoring.
    Confirms the Prediction Engine's adjustment correctly applies the
    soft, score-scaled path for this profile, not the hard-gate path."""
    financial_strength = {
        "score": 18, "grade": "avoid",
        "strengths": [],
        "weaknesses": ["Liquidity Adequacy: Current ratio 0.45x — current liabilities exceed current assets"],
        "metadata": {
            "sector_bucket": "UTILITIES_ENERGY", "data_completeness_pct": 100.0,
            "rejection_reason": None,
        },
    }
    reasoning, bull_case, bear_case = [], [], []
    result = engine._apply_financial_strength_adjustment("US", financial_strength, 60, reasoning, bull_case, bear_case)

    assert result < 60  # demoted, but via the soft path
    assert "liquidity distress" not in reasoning[0]["reason"].lower()
    assert "18/100" in reasoning[0]["reason"]


@pytest.mark.golden
def test_golden_real_aal_liquidity_distress_shape_hard_gates_through_prediction_engine(engine):
    """Real, live-confirmed AAL shape -- still hard-gated post-Sprint-#009
    (the exemption was deliberately NOT extended to airlines). Confirms
    the Prediction Engine's hard-gate path produces the full, specific
    explainability text, not a generic message."""
    financial_strength = {
        "score": 0, "grade": "rejected",
        "metadata": {
            "rejection_reason": "liquidity_distress",
            "current_ratio": 0.4983259839947738,
            "sector_bucket": "MANUFACTURING",
        },
    }
    reasoning, bull_case, bear_case = [], [], []
    result = engine._apply_financial_strength_adjustment("US", financial_strength, 85, reasoning, bull_case, bear_case)

    assert result == 30
    assert "0.50x" in reasoning[0]["reason"]
    assert "liquidity distress" in reasoning[0]["reason"].lower()
    assert bull_case == []
    assert len(bear_case) == 1
