"""Unit tests for _compute_risk_penalty (module-level function, services/prediction_engine.py)."""

import pandas as pd
import pytest

from services.prediction_engine import _compute_risk_penalty


@pytest.fixture
def empty_df():
    return pd.DataFrame()


class TestRiskPenaltyHealthyStock:
    @pytest.mark.unit
    def test_no_penalty_for_clean_fundamentals(self, base_info, empty_df):
        penalty, reasons = _compute_risk_penalty(base_info, empty_df)
        assert penalty == 0
        assert reasons == []


class TestRiskPenaltyDebtTiers:
    @pytest.mark.unit
    def test_elevated_debt_tier_adds_4_points(self, base_info, empty_df):
        base_info["debtToEquity"] = 250.0  # between 200 and 300
        penalty, reasons = _compute_risk_penalty(base_info, empty_df)
        assert penalty == 4
        assert any("Elevated debt" in r for r in reasons)

    @pytest.mark.unit
    def test_high_debt_tier_adds_8_points(self, base_info, empty_df):
        base_info["debtToEquity"] = 350.0  # > 300
        penalty, reasons = _compute_risk_penalty(base_info, empty_df)
        assert penalty == 8
        assert any("High debt" in r for r in reasons)


class TestRiskPenaltyBetaTiers:
    @pytest.mark.unit
    def test_high_beta_adds_6_points(self, base_info, empty_df):
        base_info["beta"] = 2.5
        penalty, _ = _compute_risk_penalty(base_info, empty_df)
        assert penalty == 6

    @pytest.mark.unit
    def test_above_average_beta_adds_3_points(self, base_info, empty_df):
        base_info["beta"] = 1.8
        penalty, _ = _compute_risk_penalty(base_info, empty_df)
        assert penalty == 3


class TestRiskPenaltyCumulative:
    @pytest.mark.unit
    def test_multiple_risk_factors_stack(self, base_info, empty_df):
        base_info["debtToEquity"] = 350.0   # +8
        base_info["beta"] = 2.5             # +6
        base_info["freeCashflow"] = -1.0    # +5
        penalty, reasons = _compute_risk_penalty(base_info, empty_df)
        assert penalty == 19
        assert len(reasons) == 3
