"""Epic 007 Phase 3B — Data Confidence unit tests."""
import pytest

from services.intelligence_engine.data_confidence import compute_data_confidence, DataConfidenceInputs


@pytest.mark.regression
class TestDataConfidence:
    def test_fully_complete_data_scores_at_or_near_100(self):
        score = compute_data_confidence(DataConfidenceInputs(
            price_available=True,
            volume_available=True,
            fundamentals_completeness_pct=100.0,
            financials_completeness_pct=100.0,
            quote_age_days=0,
            history_depth_days=300,
        ))
        assert score == 100

    def test_completely_missing_data_scores_zero(self):
        score = compute_data_confidence(DataConfidenceInputs(
            price_available=False,
            volume_available=False,
            fundamentals_completeness_pct=None,
            financials_completeness_pct=None,
            quote_age_days=None,
            history_depth_days=None,
        ))
        assert score == 0

    def test_score_is_always_in_valid_range(self):
        score = compute_data_confidence(DataConfidenceInputs(
            price_available=True,
            volume_available=False,
            fundamentals_completeness_pct=50.0,
            financials_completeness_pct=None,
            quote_age_days=3,
            history_depth_days=100,
        ))
        assert 0 <= score <= 100

    def test_missing_price_reduces_score_vs_full_data(self):
        full = compute_data_confidence(DataConfidenceInputs(
            price_available=True, volume_available=True,
            fundamentals_completeness_pct=100.0, financials_completeness_pct=100.0,
            quote_age_days=0, history_depth_days=300,
        ))
        no_price = compute_data_confidence(DataConfidenceInputs(
            price_available=False, volume_available=True,
            fundamentals_completeness_pct=100.0, financials_completeness_pct=100.0,
            quote_age_days=0, history_depth_days=300,
        ))
        assert no_price < full

    def test_partial_fundamentals_scores_between_zero_and_full(self):
        partial = compute_data_confidence(DataConfidenceInputs(
            price_available=True, volume_available=True,
            fundamentals_completeness_pct=50.0, financials_completeness_pct=100.0,
            quote_age_days=0, history_depth_days=300,
        ))
        full = compute_data_confidence(DataConfidenceInputs(
            price_available=True, volume_available=True,
            fundamentals_completeness_pct=100.0, financials_completeness_pct=100.0,
            quote_age_days=0, history_depth_days=300,
        ))
        assert partial < full

    def test_short_history_reduces_score(self):
        short_hist = compute_data_confidence(DataConfidenceInputs(
            price_available=True, volume_available=True,
            fundamentals_completeness_pct=100.0, financials_completeness_pct=100.0,
            quote_age_days=0, history_depth_days=5,
        ))
        long_hist = compute_data_confidence(DataConfidenceInputs(
            price_available=True, volume_available=True,
            fundamentals_completeness_pct=100.0, financials_completeness_pct=100.0,
            quote_age_days=0, history_depth_days=300,
        ))
        assert short_hist < long_hist

    def test_returns_integer(self):
        score = compute_data_confidence(DataConfidenceInputs(
            price_available=True, volume_available=True,
            fundamentals_completeness_pct=73.0, financials_completeness_pct=61.0,
            quote_age_days=2, history_depth_days=150,
        ))
        assert isinstance(score, int)
