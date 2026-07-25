"""
Regression: a provider-incomplete last bar (NaN Close) must never produce
NaN outputs or break JSON serialization.

Found via live manual verification (2026-07-25) against real yfinance AAPL
data: a `period="2y"` history fetch's technically most-recent row can carry
a genuine NaN Close even though its session date has already closed —
Yahoo had not finished backfilling that day's close yet. Before the fix,
this NaN propagated into trend_lifecycle's drawdown/price-vs-MA fields and
crashed `GET /api/leadership/context` with
`ValueError: Out of range float values are not JSON compliant: nan` —
confirmed live, not hypothetical, via a real running instance of the
backend with MARKET_LEADERSHIP_ENGINE_ENABLED=1.

Fix: services.market_leadership.sessions.drop_incomplete_bars() strips any
row with a NaN Close, applied in both relative_strength.py and
trend_lifecycle.py right after as-of slicing; api/routers/leadership.py
also gained a `_safe_json` boundary guard (defense in depth, mirroring
validation.py's existing pattern) so a future calculation path that
forgets this discipline still cannot break the response.
"""
import json
import math

import numpy as np
import pandas as pd
import pytest

from services.market_leadership.sessions import drop_incomplete_bars
from services.market_leadership.relative_strength import compute_relative_return_windows
from services.market_leadership.trend_lifecycle import compute_trend_inputs, build_trend_lifecycle
from tests.unit.market_leadership.conftest import make_price_df, make_flat_df


def _with_nan_last_close(df: pd.DataFrame) -> pd.DataFrame:
    """Reproduces the exact real-world shape: the last row's Close is NaN,
    every other field on that row is still present (Volume in particular —
    matches the live AAPL row that triggered this)."""
    df = df.copy()
    df.iloc[-1, df.columns.get_loc("Close")] = np.nan
    return df


@pytest.mark.regression
class TestDropIncompleteBars:
    def test_drops_only_the_nan_close_row(self):
        df = make_price_df(10)
        df_with_gap = _with_nan_last_close(df)
        cleaned = drop_incomplete_bars(df_with_gap)
        assert len(cleaned) == 9
        assert cleaned["Close"].isna().sum() == 0

    def test_no_nan_rows_is_a_no_op(self):
        df = make_price_df(10)
        cleaned = drop_incomplete_bars(df)
        assert len(cleaned) == len(df)

    def test_none_and_empty_pass_through_safely(self):
        assert drop_incomplete_bars(None) is None
        empty = pd.DataFrame(columns=["Close"])
        assert len(drop_incomplete_bars(empty)) == 0

    def test_gap_in_the_middle_is_also_dropped_not_just_trailing(self):
        df = make_price_df(20)
        df.iloc[10, df.columns.get_loc("Close")] = np.nan
        cleaned = drop_incomplete_bars(df)
        assert len(cleaned) == 19


@pytest.mark.regression
class TestRelativeStrengthNeverEmitsNan:
    def test_nan_last_bar_does_not_produce_nan_windows(self):
        stock = _with_nan_last_close(make_price_df(300, daily_drift_pct=0.1, volatility_pct=0.3, seed=1))
        bench = make_flat_df(300, seed=2)
        as_of = stock.index[-1].date()  # the as-of date IS the NaN row's date
        calc = compute_relative_return_windows(stock, bench, as_of)
        for key, value in calc["windows"].items():
            assert value is None or math.isfinite(value), f"{key} is non-finite: {value}"

    def test_nan_last_bar_on_benchmark_side_also_handled(self):
        stock = make_price_df(300, daily_drift_pct=0.1, volatility_pct=0.3, seed=3)
        bench = _with_nan_last_close(make_flat_df(300, seed=4))
        as_of = stock.index[-1].date()
        calc = compute_relative_return_windows(stock, bench, as_of)
        for key, value in calc["windows"].items():
            assert value is None or math.isfinite(value), f"{key} is non-finite: {value}"

    def test_composite_relative_return_json_serializable(self):
        stock = _with_nan_last_close(make_price_df(300, daily_drift_pct=0.1, volatility_pct=0.3, seed=5))
        bench = make_flat_df(300, seed=6)
        as_of = stock.index[-1].date()
        calc = compute_relative_return_windows(stock, bench, as_of)
        json.dumps(calc["windows"])  # must not raise


@pytest.mark.regression
class TestTrendLifecycleNeverEmitsNan:
    def test_nan_last_bar_does_not_produce_nan_drawdown(self):
        """The exact defect reproduced: drawdown_from_252d_high_pct going
        NaN on real AAPL data, which then broke JSON serialization."""
        df = _with_nan_last_close(make_price_df(400, daily_drift_pct=0.05, volatility_pct=0.3, seed=7))
        as_of = df.index[-1].date()
        inputs = compute_trend_inputs(df, as_of)
        assert inputs["insufficient_history"] is False
        for key in (
            "last_close", "price_vs_long_ma_pct", "drawdown_from_252d_high_pct",
            "above_63d_low_pct", "long_ma_slope_pct_per_day",
        ):
            value = inputs.get(key)
            assert value is None or math.isfinite(value), f"{key} is non-finite: {value}"

    def test_full_trend_lifecycle_contract_is_json_serializable(self):
        df = _with_nan_last_close(make_price_df(400, daily_drift_pct=0.05, volatility_pct=0.3, seed=8))
        as_of = df.index[-1].date()
        contract = build_trend_lifecycle("AAPL", "US", df, as_of)
        json.dumps(contract.to_dict())  # must not raise ValueError

    def test_classification_state_is_still_determinate_not_forced_to_insufficient(self):
        """Dropping one bad row out of 400 must not push a genuinely
        sufficient-history symbol into INSUFFICIENT_DATA."""
        from services.market_leadership.contracts import TrendLifecycleState
        df = _with_nan_last_close(make_price_df(400, daily_drift_pct=0.05, volatility_pct=0.3, seed=9))
        as_of = df.index[-1].date()
        contract = build_trend_lifecycle("AAPL", "US", df, as_of)
        assert contract.state != TrendLifecycleState.INSUFFICIENT_DATA


@pytest.mark.regression
class TestLeadershipApiSafeJson:
    def test_safe_json_replaces_nan_and_inf_with_none(self):
        from api.routers.leadership import _safe_json
        payload = {"a": float("nan"), "b": float("inf"), "c": float("-inf"), "d": 1.5, "e": [float("nan"), 2]}
        cleaned = _safe_json(payload)
        assert cleaned == {"a": None, "b": None, "c": None, "d": 1.5, "e": [None, 2]}
        json.dumps(cleaned)  # must not raise

    def test_safe_json_leaves_finite_values_untouched(self):
        from api.routers.leadership import _safe_json
        payload = {"a": 1, "b": "text", "c": None, "d": [1, 2, 3], "e": {"nested": 4.2}}
        assert _safe_json(payload) == payload
