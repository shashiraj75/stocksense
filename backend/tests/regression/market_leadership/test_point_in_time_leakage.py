"""
Section 10 — explicit point-in-time leakage tests.

Each test constructs a synthetic fixture where a future event (a large
price spike, a crash, a burst of volume) WOULD change the answer if the
calculation leaked it — then proves the historical, as-of result is
unaffected by appending that future data. A test that merely checks
"nothing crashes" would not satisfy this; each assertion below fails loudly
if leakage is (re)introduced.
"""
import datetime

import numpy as np
import pandas as pd
import pytest

from services.market_leadership.relative_strength import compute_relative_return_windows
from services.market_leadership.trend_lifecycle import compute_trend_inputs
from services.market_leadership.sessions import slice_as_of
from tests.unit.market_leadership.conftest import make_price_df, make_flat_df


def _append_future_spike(df: pd.DataFrame, n_future_days: int, spike_pct: float) -> pd.DataFrame:
    """Appends `n_future_days` sessions after df's last date, ending at a
    price that is `spike_pct`% above the last known close — a future event
    that would obviously change any return/trend calculation if leaked."""
    last_date = df.index[-1]
    last_close = float(df["Close"].iloc[-1])
    future_idx = pd.bdate_range(start=last_date + pd.Timedelta(days=1), periods=n_future_days, tz="UTC")
    future_close = np.linspace(last_close, last_close * (1 + spike_pct / 100.0), n_future_days)
    future = pd.DataFrame({
        "Open": future_close, "High": future_close * 1.01, "Low": future_close * 0.99,
        "Close": future_close, "Volume": np.full(n_future_days, 5_000_000.0),
    }, index=future_idx)
    return pd.concat([df, future])


@pytest.mark.regression
class TestRelativeStrengthLeakage:
    def test_future_100pct_spike_does_not_change_historical_composite(self):
        stock = make_price_df(300, daily_drift_pct=0.05, volatility_pct=0.3, seed=11)
        bench = make_flat_df(300, seed=22)
        as_of = stock.index[-1].date()

        result_before = compute_relative_return_windows(stock, bench, as_of)

        # A future spike so large it would swamp every window if leaked.
        stock_with_future_spike = _append_future_spike(stock, 60, spike_pct=200.0)
        result_after = compute_relative_return_windows(stock_with_future_spike, bench, as_of)

        assert result_before["windows"] == result_after["windows"], (
            "a future 200% price spike changed the historical as-of result — "
            "point-in-time slicing is leaking future data"
        )

    def test_future_crash_does_not_change_historical_composite(self):
        stock = make_price_df(300, daily_drift_pct=0.05, volatility_pct=0.3, seed=33)
        bench = make_flat_df(300, seed=44)
        as_of = stock.index[-1].date()

        result_before = compute_relative_return_windows(stock, bench, as_of)
        stock_with_future_crash = _append_future_spike(stock, 60, spike_pct=-80.0)
        result_after = compute_relative_return_windows(stock_with_future_crash, bench, as_of)

        assert result_before["windows"] == result_after["windows"]

    def test_future_data_on_the_benchmark_side_also_does_not_leak(self):
        stock = make_price_df(300, daily_drift_pct=0.05, volatility_pct=0.3, seed=55)
        bench = make_flat_df(300, seed=66)
        as_of = stock.index[-1].date()

        result_before = compute_relative_return_windows(stock, bench, as_of)
        bench_with_future = _append_future_spike(bench, 60, spike_pct=150.0)
        result_after = compute_relative_return_windows(stock, bench_with_future, as_of)

        assert result_before["windows"] == result_after["windows"]


@pytest.mark.regression
class TestTrendLifecycleLeakage:
    def test_future_spike_does_not_change_historical_trend_inputs(self):
        df = make_price_df(400, daily_drift_pct=0.05, volatility_pct=0.3, seed=77)
        as_of = df.index[-1].date()

        inputs_before = compute_trend_inputs(df, as_of)
        df_with_future_spike = _append_future_spike(df, 80, spike_pct=300.0)
        inputs_after = compute_trend_inputs(df_with_future_spike, as_of)

        assert inputs_before["last_close"] == inputs_after["last_close"]
        assert inputs_before["ma200"] == inputs_after["ma200"]
        assert inputs_before["price_vs_long_ma_pct"] == inputs_after["price_vs_long_ma_pct"]
        assert inputs_before["drawdown_from_252d_high_pct"] == inputs_after["drawdown_from_252d_high_pct"]

    def test_future_crash_does_not_retroactively_flag_a_historical_distribution_event(self):
        """A future high-volume decline must not be counted in a past
        as-of window's high_volume_decline_count_21d."""
        df = make_price_df(400, daily_drift_pct=0.05, volatility_pct=0.3, seed=88)
        as_of = df.index[-1].date()

        inputs_before = compute_trend_inputs(df, as_of)
        # Future crash with a huge volume spike — exactly the pattern that
        # would trip the distribution-event counter if it leaked backward.
        last_date = df.index[-1]
        future_idx = pd.bdate_range(start=last_date + pd.Timedelta(days=1), periods=5, tz="UTC")
        last_close = float(df["Close"].iloc[-1])
        crash_close = np.linspace(last_close, last_close * 0.5, 5)
        future = pd.DataFrame({
            "Open": crash_close, "High": crash_close * 1.01, "Low": crash_close * 0.9,
            "Close": crash_close, "Volume": np.full(5, 50_000_000.0),
        }, index=future_idx)
        df_with_future_crash = pd.concat([df, future])

        inputs_after = compute_trend_inputs(df_with_future_crash, as_of)
        assert (
            inputs_before["high_volume_decline_count_21d"]
            == inputs_after["high_volume_decline_count_21d"]
        )


@pytest.mark.regression
class TestSliceAsOfIsTheOnlyLeakagePreventionMechanism:
    def test_slicing_removes_exactly_the_future_rows_added(self):
        """A structural property test: for any n_future rows appended,
        slice_as_of(df_with_future, original_as_of) has exactly the same
        length as the original df — nothing more, nothing less."""
        df = make_price_df(200, seed=1)
        as_of = df.index[-1].date()
        for n_future in (1, 10, 100):
            extended = _append_future_spike(df, n_future, spike_pct=50.0)
            sliced = slice_as_of(extended, as_of)
            assert len(sliced) == len(df)
