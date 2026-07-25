"""Unit tests for trend_lifecycle.py (pure, deterministic classification)."""
import pandas as pd
import pytest

from services.market_leadership.contracts import TrendLifecycleState, ExtensionRisk
from services.market_leadership.trend_lifecycle import (
    compute_trend_inputs, classify_trend_lifecycle, build_trend_lifecycle,
)
from tests.unit.market_leadership.conftest import make_price_df, make_uptrend_df, make_downtrend_df, make_flat_df


@pytest.mark.unit
class TestComputeTrendInputs:
    def test_insufficient_history_below_min_sessions(self):
        df = make_flat_df(50)
        inputs = compute_trend_inputs(df, df.index[-1].date())
        assert inputs["insufficient_history"] is True

    def test_sufficient_history_computes_all_fields(self):
        df = make_uptrend_df(300)
        inputs = compute_trend_inputs(df, df.index[-1].date())
        assert inputs["insufficient_history"] is False
        assert inputs["ma200"] is not None
        assert inputs["price_vs_long_ma_pct"] is not None

    def test_as_of_slicing_excludes_future_bars(self):
        """Leakage guard: computing at an earlier as_of must not see later
        bars — moving the as_of date forward with unchanged history up to
        that point must not change the earlier result (no look-ahead)."""
        df = make_uptrend_df(400)
        early_as_of = df.index[300].date()
        inputs_early = compute_trend_inputs(df, early_as_of)
        # Recompute with a truncated frame that physically ends at the same date.
        truncated = df.iloc[:301]
        inputs_truncated = compute_trend_inputs(truncated, early_as_of)
        assert inputs_early["last_close"] == inputs_truncated["last_close"]
        assert inputs_early["ma200"] == inputs_truncated["ma200"]

    def test_extra_future_bars_do_not_change_past_result(self):
        """Direct property-test form of the leakage guarantee required by
        Section 10/16: adding future data must not alter a historical
        result computed at a fixed as_of."""
        df = make_uptrend_df(400)
        as_of = df.index[250].date()
        result_without_future = compute_trend_inputs(df.iloc[:251], as_of)
        result_with_future = compute_trend_inputs(df, as_of)
        assert result_without_future["last_close"] == result_with_future["last_close"]
        assert result_without_future["price_vs_long_ma_pct"] == result_with_future["price_vs_long_ma_pct"]


@pytest.mark.unit
class TestClassifyTrendLifecycle:
    def test_insufficient_history_state(self):
        state, risk, reasons, completeness = classify_trend_lifecycle({"insufficient_history": True})
        assert state == TrendLifecycleState.INSUFFICIENT_DATA
        assert risk == ExtensionRisk.UNKNOWN
        assert completeness == 0.0

    def test_confirmed_advance_on_clean_bullish_structure(self):
        df = make_uptrend_df(400)
        inputs = compute_trend_inputs(df, df.index[-1].date())
        state, risk, reasons, completeness = classify_trend_lifecycle(inputs)
        assert state in (
            TrendLifecycleState.CONFIRMED_ADVANCE, TrendLifecycleState.EXTENDED_ADVANCE,
            TrendLifecycleState.EARLY_ADVANCE,
        )
        assert completeness > 0.8

    def test_declining_on_clean_downtrend(self):
        df = make_downtrend_df(400)
        inputs = compute_trend_inputs(df, df.index[-1].date())
        state, risk, reasons, completeness = classify_trend_lifecycle(inputs)
        assert state == TrendLifecycleState.DECLINING

    def test_extended_advance_flags_high_extension_risk_even_with_bullish_structure(self):
        """A strong trend must not be presented as an attractive entry — an
        extended stock is marked HIGH extension risk regardless of trend
        strength (Section 7 requirement #4/#5)."""
        df = make_price_df(400, daily_drift_pct=0.5, volatility_pct=0.2, seed=9)  # steep, low-noise
        inputs = compute_trend_inputs(df, df.index[-1].date())
        state, risk, reasons, completeness = classify_trend_lifecycle(inputs)
        assert state == TrendLifecycleState.EXTENDED_ADVANCE
        assert risk == ExtensionRisk.HIGH
        assert "EXCESSIVE_EXTENSION" in reasons

    def test_base_forming_on_flat_recovered_price(self):
        df = make_flat_df(400)
        inputs = compute_trend_inputs(df, df.index[-1].date())
        state, risk, reasons, completeness = classify_trend_lifecycle(inputs)
        assert state in (TrendLifecycleState.BASE_FORMING, TrendLifecycleState.CONFIRMED_ADVANCE, TrendLifecycleState.EARLY_ADVANCE)

    def test_reason_codes_never_empty_for_a_determinate_state(self):
        df = make_uptrend_df(400)
        inputs = compute_trend_inputs(df, df.index[-1].date())
        state, risk, reasons, completeness = classify_trend_lifecycle(inputs)
        assert len(reasons) > 0

    def test_identical_inputs_produce_identical_classification(self):
        df = make_uptrend_df(400)
        inputs = compute_trend_inputs(df, df.index[-1].date())
        r1 = classify_trend_lifecycle(inputs)
        r2 = classify_trend_lifecycle(dict(inputs))
        assert r1[0] == r2[0] and r1[1] == r2[1]


@pytest.mark.unit
class TestBuildTrendLifecycle:
    def test_contract_fields_populated(self):
        df = make_uptrend_df(400)
        as_of = df.index[-1].date()
        contract = build_trend_lifecycle("ABC", "US", df, as_of, rs_trend_improving=True)
        assert contract.symbol == "ABC"
        assert contract.market == "US"
        assert contract.methodology_version
        assert contract.calculation_as_of == as_of.isoformat()

    def test_insufficient_history_never_claims_a_confident_state(self):
        """Property: insufficient data never produces a confident
        classification (Section 16.B property-test requirement)."""
        df = make_flat_df(60)
        contract = build_trend_lifecycle("ABC", "IN", df, df.index[-1].date())
        assert contract.state == TrendLifecycleState.INSUFFICIENT_DATA
        assert contract.evidence_completeness == 0.0
