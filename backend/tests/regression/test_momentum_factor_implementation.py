"""
2026-08-24 momentum factor implementation (item 1 of the stock-picking
methodology rebuild). Follows a definitive negative finding for the
existing technical composite (Fama-MacBeth cross-sectional IC ~0 in
every segment tested — see fama_macbeth_ic_test.py) and a positive,
replicated one for 12-1 month momentum: significant and correctly
signed at MEDIUM horizon in the US (t=2.34 and t=6.48 across two
independent 150-stock universe samples) and India (t=2.72, 229-stock
sample); flat at long horizon (t=-0.13) and weak at short (t=0.85).

services.technical_indicators.compute_momentum_score is the new factor.
It is wired into the composite at exactly ONE horizon — MEDIUM — in both
the live path (prediction_engine.py::_composite_signal) and the backtest
mirror (validation_engine.py::_score_at), matching where the evidence
actually supports it. These tests verify: the raw scoring function's
bucket behavior, and that both callers apply it only at medium horizon.
"""
import numpy as np
import pandas as pd
import pytest

from services.technical_indicators import (
    compute_momentum_score, MOMENTUM_LOOKBACK_DAYS, MOMENTUM_SKIP_DAYS,
)
from services.prediction_engine import PredictionEngine
from services.validation_engine import _score_at


def _close_series(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"Close": prices})


class TestComputeMomentumScore:
    def test_insufficient_history_returns_neutral(self):
        df = _close_series([100.0] * (MOMENTUM_LOOKBACK_DAYS - 1))
        assert compute_momentum_score(df) == 50.0

    def test_strong_positive_momentum_scores_above_neutral(self):
        # Block1: exactly LOOKBACK_DAYS rows at the OLD price, so
        # iloc[-LOOKBACK_DAYS] (the first row of block2) lands on the
        # boundary; block2 needs > SKIP_DAYS extra rows at the NEW price
        # so iloc[-SKIP_DAYS] is safely within it, never spilling back
        # into block1. Old=100, new=150 -> +50% (above the >30% bucket).
        buffer = MOMENTUM_SKIP_DAYS + 5
        prices = [100.0] * MOMENTUM_LOOKBACK_DAYS + [150.0] * buffer
        df = _close_series(prices)
        assert compute_momentum_score(df) == 65.0  # 50 + 15

    def test_strong_negative_momentum_scores_below_neutral(self):
        buffer = MOMENTUM_SKIP_DAYS + 5
        prices = [100.0] * MOMENTUM_LOOKBACK_DAYS + [50.0] * buffer
        df = _close_series(prices)
        assert compute_momentum_score(df) == 35.0  # 50 - 15

    def test_flat_price_history_scores_neutral(self):
        df = _close_series([100.0] * (MOMENTUM_LOOKBACK_DAYS + 10))
        assert compute_momentum_score(df) == 50.0

    def test_never_returns_outside_0_100(self):
        n = MOMENTUM_LOOKBACK_DAYS + 5
        prices = [1.0] * (n - MOMENTUM_LOOKBACK_DAYS) + [10_000.0] * MOMENTUM_LOOKBACK_DAYS
        df = _close_series(prices)
        score = compute_momentum_score(df)
        assert 0.0 <= score <= 100.0


class TestPredictionEngineMomentumGate:
    def _base_kwargs(self, df=None):
        return dict(
            tech={"score": 50, "breakdown": []},
            fund={"score": 50, "reasons": []},
            sentiment={"score": 50, "data_available": True, "label": "NEUTRAL"},
            weights={"tech": 0.5, "fund": 0.3, "sentiment": 0.2},
            regime={"trend": "NEUTRAL", "reason": "test", "score_adj": 0},
            df=df,
        )

    def test_momentum_contributes_at_medium_horizon(self):
        prices = [100.0] * MOMENTUM_LOOKBACK_DAYS + [150.0] * (MOMENTUM_SKIP_DAYS + 5)
        df = _close_series(prices)
        engine = PredictionEngine()
        (_signal, _confidence, _reasoning, _score_band, contributions, _composite_r,
         _confidence_score, _confidence_band, _confidence_components) = engine._composite_signal(
            horizon="medium", **self._base_kwargs(df=df),
        )
        assert contributions["momentum"] != 0.0
        assert contributions["momentum"] > 0  # strong positive momentum

    @pytest.mark.parametrize("horizon", ["short", "long"])
    def test_momentum_does_not_contribute_outside_medium_horizon(self, horizon):
        prices = [100.0] * MOMENTUM_LOOKBACK_DAYS + [150.0] * (MOMENTUM_SKIP_DAYS + 5)
        df = _close_series(prices)
        engine = PredictionEngine()
        (_signal, _confidence, _reasoning, _score_band, contributions, _composite_r,
         _confidence_score, _confidence_band, _confidence_components) = engine._composite_signal(
            horizon=horizon, **self._base_kwargs(df=df),
        )
        assert contributions["momentum"] == 0.0

    def test_momentum_contribution_present_and_zero_with_no_df(self):
        engine = PredictionEngine()
        (_signal, _confidence, _reasoning, _score_band, contributions, _composite_r,
         _confidence_score, _confidence_band, _confidence_components) = engine._composite_signal(
            horizon="medium", **self._base_kwargs(df=None),
        )
        assert contributions["momentum"] == 0.0


class TestValidationEngineMomentumGate:
    def _df_with_momentum(self, close_val: float) -> pd.DataFrame:
        rows = [{"Open": 100.0, "High": 100.0, "Low": 100.0, "Close": 100.0, "Volume": 1_000_000}
                for _ in range(MOMENTUM_LOOKBACK_DAYS)]
        rows += [{"Open": close_val, "High": close_val, "Low": close_val, "Close": close_val, "Volume": 1_000_000}
                 for _ in range(MOMENTUM_SKIP_DAYS + 5)]
        df = pd.DataFrame(rows)
        for col, val in {
            "rsi_14": 50.0, "macd_diff": 1.0, "ema_200": 90.0, "ema_20": 100.0, "ema_50": 90.0,
            "adx": 10.0, "adx_pos": 20.0, "adx_neg": 20.0,
            "bb_pct": 0.5, "stoch_rsi": 0.5, "williams_r": -50.0, "cci": 0.0,
        }.items():
            df[col] = val
        return df

    def test_momentum_moves_composite_at_medium_horizon(self):
        strong_momentum_df = self._df_with_momentum(150.0)
        flat_df = self._df_with_momentum(100.0)
        i = len(strong_momentum_df) - 1
        with_momentum = _score_at(strong_momentum_df, i, None, fund_score=50.0, regime_adj=0.0, horizon="medium")
        without_extra_momentum = _score_at(flat_df, i, None, fund_score=50.0, regime_adj=0.0, horizon="medium")
        assert with_momentum["composite"] > without_extra_momentum["composite"]

    @pytest.mark.parametrize("horizon", ["short", "long", None])
    def test_momentum_does_not_move_composite_outside_medium_horizon(self, horizon):
        strong_momentum_df = self._df_with_momentum(150.0)
        flat_df = self._df_with_momentum(100.0)
        i = len(strong_momentum_df) - 1
        with_momentum = _score_at(strong_momentum_df, i, None, fund_score=50.0, regime_adj=0.0, horizon=horizon)
        without_extra_momentum = _score_at(flat_df, i, None, fund_score=50.0, regime_adj=0.0, horizon=horizon)
        assert with_momentum["composite"] == without_extra_momentum["composite"]
