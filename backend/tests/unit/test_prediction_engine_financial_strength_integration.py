"""
Unit tests for PredictionEngine._apply_financial_strength_adjustment
(Epic 002 Sprint #010) — the Financial Strength Intelligence Engine's
only point of influence on the Prediction Engine's confidence. Pure
logic only — constructed financial_strength dicts, no network.
"""

import pytest

from services.prediction_engine import PredictionEngine


@pytest.fixture
def engine():
    return PredictionEngine()


def _fs(score=None, grade="hold", rejection_reason=None, current_ratio=None):
    metadata = {}
    if rejection_reason:
        metadata["rejection_reason"] = rejection_reason
    if current_ratio is not None:
        metadata["current_ratio"] = current_ratio
    return {"score": score, "grade": grade, "metadata": metadata}


# ── Graceful degradation ─────────────────────────────────────────────────────

@pytest.mark.unit
def test_none_financial_strength_leaves_confidence_unchanged(engine):
    result = engine._apply_financial_strength_adjustment("US", None, 65, [], [], [])
    assert result == 65


@pytest.mark.unit
def test_non_us_market_leaves_confidence_unchanged(engine):
    fs = _fs(score=90, grade="strong_buy")
    result = engine._apply_financial_strength_adjustment("IN", fs, 65, [], [], [])
    assert result == 65


@pytest.mark.unit
def test_sector_not_supported_rejection_does_not_penalize(engine):
    """A FINANCIAL/REAL_ESTATE company has no real Financial Strength
    signal at all — must never be penalized for data this engine
    doesn't have."""
    fs = _fs(score=0, grade="rejected", rejection_reason="sector_not_yet_supported")
    result = engine._apply_financial_strength_adjustment("US", fs, 65, [], [], [])
    assert result == 65


@pytest.mark.unit
def test_insufficient_data_rejection_does_not_penalize(engine):
    fs = _fs(score=0, grade="rejected", rejection_reason="insufficient_data")
    result = engine._apply_financial_strength_adjustment("US", fs, 65, [], [], [])
    assert result == 65


@pytest.mark.unit
def test_missing_score_leaves_confidence_unchanged(engine):
    fs = {"grade": "hold", "metadata": {}}  # no "score" key at all
    result = engine._apply_financial_strength_adjustment("US", fs, 65, [], [], [])
    assert result == 65


# ── Hard-gate influence ──────────────────────────────────────────────────────

@pytest.mark.unit
def test_liquidity_distress_demotes_confidence_to_severe_cap(engine):
    fs = _fs(score=0, grade="rejected", rejection_reason="liquidity_distress", current_ratio=0.45)
    reasoning, bull_case, bear_case = [], [], []
    result = engine._apply_financial_strength_adjustment("US", fs, 80, reasoning, bull_case, bear_case)
    assert result == 30  # min(80, 30)
    assert any("liquidity distress" in r["reason"].lower() for r in reasoning)
    assert any("liquidity distress" in m.lower() for m in bear_case)


@pytest.mark.unit
def test_liquidity_distress_does_not_raise_an_already_low_confidence(engine):
    fs = _fs(score=0, grade="rejected", rejection_reason="liquidity_distress", current_ratio=0.45)
    result = engine._apply_financial_strength_adjustment("US", fs, 15, [], [], [])
    assert result == 15  # min(15, 30) == 15, never raised


# ── Soft, score-scaled influence ─────────────────────────────────────────────

@pytest.mark.unit
def test_strong_financial_strength_score_boosts_confidence(engine):
    fs = _fs(score=100, grade="strong_buy")
    reasoning, bull_case, bear_case = [], [], []
    result = engine._apply_financial_strength_adjustment("US", fs, 60, reasoning, bull_case, bear_case)
    assert result == 66  # (100-50)/50 * 6 = +6
    assert bull_case
    assert not bear_case


@pytest.mark.unit
def test_weak_financial_strength_score_demotes_confidence(engine):
    fs = _fs(score=0, grade="avoid")
    reasoning, bull_case, bear_case = [], [], []
    result = engine._apply_financial_strength_adjustment("US", fs, 60, reasoning, bull_case, bear_case)
    assert result == 54  # (0-50)/50 * 6 = -6
    assert bear_case
    assert not bull_case


@pytest.mark.unit
def test_neutral_financial_strength_score_does_not_change_confidence(engine):
    fs = _fs(score=50, grade="hold")
    result = engine._apply_financial_strength_adjustment("US", fs, 60, [], [], [])
    assert result == 60


@pytest.mark.unit
def test_adjustment_is_bounded_and_cannot_exceed_the_cap(engine):
    """Confirms the adjustment can never exceed
    PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP regardless of score —
    the additive-not-dominant guarantee this sprint requires."""
    from services.thresholds import FINANCIAL_STRENGTH as FS
    fs = _fs(score=100, grade="strong_buy")
    result = engine._apply_financial_strength_adjustment("US", fs, 50, [], [], [])
    assert abs(result - 50) <= FS.PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP


@pytest.mark.unit
def test_confidence_never_exceeds_100_or_drops_below_0(engine):
    fs_strong = _fs(score=100, grade="strong_buy")
    assert engine._apply_financial_strength_adjustment("US", fs_strong, 98, [], [], []) <= 100

    fs_weak = _fs(score=0, grade="avoid")
    assert engine._apply_financial_strength_adjustment("US", fs_weak, 2, [], [], []) >= 0


@pytest.mark.unit
def test_signal_label_is_never_part_of_this_functions_contract(engine):
    """Structural confirmation that this function's signature has no
    `signal` parameter at all -- per this sprint's explicit 'do not
    redesign the Prediction Engine' rule, Financial Strength can only
    ever influence confidence, never the BUY/HOLD/SELL label itself."""
    import inspect
    sig = inspect.signature(engine._apply_financial_strength_adjustment)
    assert "signal" not in sig.parameters
