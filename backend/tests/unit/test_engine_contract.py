"""Unit tests for the shared EngineResponse contract (services/engine_contract.py)."""

import pytest

from services.engine_contract import EngineResponse, Grade


class TestEngineResponseValidation:
    @pytest.mark.unit
    def test_score_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            EngineResponse(score=101, grade=Grade.BUY, confidence=50)

    @pytest.mark.unit
    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            EngineResponse(score=50, grade=Grade.BUY, confidence=-1)

    @pytest.mark.unit
    def test_grade_accepts_raw_string(self):
        r = EngineResponse(score=50, grade="hold", confidence=50)
        assert r.grade == Grade.HOLD


class TestEngineResponseSerialization:
    @pytest.mark.unit
    def test_to_dict_round_trips_through_from_dict(self):
        original = EngineResponse(
            score=88.0,
            grade=Grade.STRONG_BUY,
            confidence=72.0,
            strengths=["a"],
            weaknesses=["b"],
            risks=["c"],
            explanation="why",
            metadata={"engine": "test"},
        )
        restored = EngineResponse.from_dict(original.to_dict())
        assert restored == original
