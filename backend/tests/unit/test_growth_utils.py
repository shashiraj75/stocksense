"""
Unit tests for services/growth_utils.py (Epic 003 Sprint #003) — pure-math
functions shared by both Growth Intelligence market adapters. No
provider knowledge, no live data — synthetic series only.
"""

import pytest

from services.growth_utils import (
    compute_cagr_from_series,
    compute_categorical_trend,
    compute_coefficient_of_variation,
)


@pytest.mark.unit
class TestComputeCagrFromSeries:
    def test_simple_3y_cagr(self):
        # 100 -> 133.1 over 3 years is exactly 10%/yr
        series = [100, 110, 121, 133.1]
        assert compute_cagr_from_series(series, 3) == pytest.approx(10.0, abs=0.1)

    def test_uses_only_last_n_plus_1_values(self):
        series = [1, 2, 3, 100, 110, 121, 133.1]
        assert compute_cagr_from_series(series, 3) == pytest.approx(10.0, abs=0.1)

    def test_none_if_series_too_short(self):
        assert compute_cagr_from_series([100, 110], 3) is None

    def test_none_if_series_is_none(self):
        assert compute_cagr_from_series(None, 3) is None

    def test_none_if_base_is_zero(self):
        assert compute_cagr_from_series([0, 10, 20, 30], 3) is None

    def test_none_if_base_is_negative(self):
        """The DISHTV-style edge case the India Feasibility Study found in
        real data — a negative base makes CAGR mathematically undefined,
        not just 'low'."""
        assert compute_cagr_from_series([-10, 5, 10, 20], 3) is None

    def test_negative_cagr_for_declining_series(self):
        series = [100, 90, 81, 72.9]  # -10%/yr
        assert compute_cagr_from_series(series, 3) == pytest.approx(-10.0, abs=0.1)

    def test_none_if_latest_is_negative(self):
        """Regression: a positive base with a negative final value raises
        a negative ratio to a fractional power, producing a complex
        number that crashed round() with TypeError — found via an
        integration-test fixture during this sprint, not a hypothetical."""
        assert compute_cagr_from_series([10, 5, -2, -8], 3) is None

    def test_none_if_latest_is_zero(self):
        assert compute_cagr_from_series([10, 5, 2, 0], 3) is None


@pytest.mark.unit
class TestComputeCoefficientOfVariation:
    def test_perfectly_consistent_growth_has_zero_cv(self):
        series = [100, 110, 121, 133.1, 146.41]  # exactly 10%/yr every year
        cv = compute_coefficient_of_variation(series)
        assert cv == pytest.approx(0.0, abs=0.01)

    def test_volatile_growth_has_high_cv(self):
        series = [100, 150, 80, 200, 60]
        cv = compute_coefficient_of_variation(series)
        assert cv is not None and cv > 0.5

    def test_none_if_too_short(self):
        assert compute_coefficient_of_variation([100, 110]) is None

    def test_none_if_none(self):
        assert compute_coefficient_of_variation(None) is None

    def test_none_if_fewer_than_3_valid_growth_rates(self):
        # 4 values but one zero-base breaks one of the 3 YoY ratios
        series = [0, 10, 20, 30]
        assert compute_coefficient_of_variation(series) is None


@pytest.mark.unit
class TestComputeCategoricalTrend:
    def test_accelerating(self):
        assert compute_categorical_trend([10, 20, 30, 40]) == "accelerating"

    def test_decelerating(self):
        assert compute_categorical_trend([40, 30, 20, 10]) == "decelerating"

    def test_mixed_positive(self):
        assert compute_categorical_trend([10, 20, 15, 25]) == "mixed_positive"

    def test_mixed_negative(self):
        assert compute_categorical_trend([25, 15, 20, 10]) == "mixed_negative"

    def test_none_if_too_short(self):
        assert compute_categorical_trend([10, 20]) is None

    def test_none_if_none(self):
        assert compute_categorical_trend(None) is None

    def test_uses_only_last_4_values(self):
        # First 3 values would suggest decelerating; last 4 are accelerating
        assert compute_categorical_trend([100, 90, 80, 10, 20, 30, 40]) == "accelerating"
