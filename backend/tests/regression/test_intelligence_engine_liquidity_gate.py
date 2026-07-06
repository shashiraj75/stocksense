"""Epic 007 Phase 3B — Liquidity Gate unit tests. Pure-function tests
only; uses explicit LiquidityThresholds objects so the tests don't depend
on (or need to mutate) environment variables."""
import pytest

from services.intelligence_engine.liquidity_gate import (
    check_liquidity,
    default_thresholds,
    LiquidityThresholds,
    LIQUIDITY_OK,
    LIQUIDITY_LOW_AVG_VALUE,
    LIQUIDITY_LOW_AVG_VOLUME,
    LIQUIDITY_TOO_MANY_ZERO_VOLUME_DAYS,
    LIQUIDITY_PRICE_TOO_LOW,
    LIQUIDITY_FREE_FLOAT_TOO_LOW,
)

_T = LiquidityThresholds(
    min_avg_daily_value=1_000_000,
    min_avg_daily_volume=10_000,
    max_zero_volume_days=2,
    min_price=5.0,
    min_free_float_pct=10.0,
)


@pytest.mark.regression
class TestLiquidityGate:
    def test_liquid_stock_passes(self):
        result = check_liquidity(
            "AAPL", "US",
            avg_daily_value=50_000_000, avg_daily_volume=1_000_000,
            zero_volume_days=0, price=150.0, free_float_pct=90.0,
            thresholds=_T,
        )
        assert result.passed is True
        assert result.reason == LIQUIDITY_OK

    def test_low_avg_daily_value_fails(self):
        result = check_liquidity(
            "XYZ", "US",
            avg_daily_value=500_000, avg_daily_volume=1_000_000,
            zero_volume_days=0, price=150.0,
            thresholds=_T,
        )
        assert result.passed is False
        assert result.reason == LIQUIDITY_LOW_AVG_VALUE

    def test_low_avg_daily_volume_fails(self):
        result = check_liquidity(
            "XYZ", "US",
            avg_daily_value=50_000_000, avg_daily_volume=1_000,
            zero_volume_days=0, price=150.0,
            thresholds=_T,
        )
        assert result.passed is False
        assert result.reason == LIQUIDITY_LOW_AVG_VOLUME

    def test_too_many_zero_volume_days_fails(self):
        result = check_liquidity(
            "XYZ", "US",
            avg_daily_value=50_000_000, avg_daily_volume=1_000_000,
            zero_volume_days=5, price=150.0,
            thresholds=_T,
        )
        assert result.passed is False
        assert result.reason == LIQUIDITY_TOO_MANY_ZERO_VOLUME_DAYS

    def test_price_too_low_fails(self):
        result = check_liquidity(
            "XYZ", "US",
            avg_daily_value=50_000_000, avg_daily_volume=1_000_000,
            zero_volume_days=0, price=0.50,
            thresholds=_T,
        )
        assert result.passed is False
        assert result.reason == LIQUIDITY_PRICE_TOO_LOW

    def test_low_free_float_fails(self):
        result = check_liquidity(
            "XYZ", "US",
            avg_daily_value=50_000_000, avg_daily_volume=1_000_000,
            zero_volume_days=0, price=150.0, free_float_pct=2.0,
            thresholds=_T,
        )
        assert result.passed is False
        assert result.reason == LIQUIDITY_FREE_FLOAT_TOO_LOW

    def test_missing_inputs_do_not_gate(self):
        # Every input None except price — nothing to reject on; the gate
        # must not fail a symbol just because data is incomplete (that's
        # Data Confidence's job, not Liquidity's).
        result = check_liquidity(
            "XYZ", "US",
            avg_daily_value=None, avg_daily_volume=None,
            zero_volume_days=None, price=150.0,
            thresholds=_T,
        )
        assert result.passed is True

    def test_free_float_not_gated_when_threshold_unset(self):
        t = LiquidityThresholds(
            min_avg_daily_value=1_000_000, min_avg_daily_volume=10_000,
            max_zero_volume_days=2, min_price=5.0, min_free_float_pct=None,
        )
        result = check_liquidity(
            "XYZ", "US",
            avg_daily_value=50_000_000, avg_daily_volume=1_000_000,
            zero_volume_days=0, price=150.0, free_float_pct=1.0,
            thresholds=t,
        )
        assert result.passed is True

    def test_default_thresholds_differ_by_market(self):
        in_t = default_thresholds("IN")
        us_t = default_thresholds("US")
        assert in_t.min_price != us_t.min_price or in_t.min_avg_daily_value != us_t.min_avg_daily_value
