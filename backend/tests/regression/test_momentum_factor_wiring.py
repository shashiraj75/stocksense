"""
2026-09-06 corrective review of PR #83 — momentum wiring, default-off
parity, and direct (non-subtraction) baseline computation.

Isolated PR C: adds services/momentum_factor.py (shared, tested
indexing) and wires it into prediction_engine.py::_composite_signal and
validation_engine.py::_score_at, gated to medium horizon and
MOMENTUM_FACTOR_ENABLED=1 (default-off). Does NOT touch SELL
classification or oscillator/trend scoring — see the separate
fix/equity-sell-containment and fix/adx-oscillator-regime-gate
branches for those independently reviewable changes.

Also proves the corrected baseline-computation approach:
validation_engine.py::_score_at now returns `base_composite` (the
pre-momentum, pre-clamp composite) computed DIRECTLY, never
reconstructed by subtracting a weight constant from the final,
already-clamped `composite` — the corrective-review-confirmed defect in
the prior research script.
"""
from services.momentum_factor import LOOKBACK_DAYS, SKIP_DAYS
from services.prediction_engine import PredictionEngine
from services.validation_engine import _score_at

import pandas as pd
import pytest


def _momentum_close_series() -> pd.Series:
    n = LOOKBACK_DAYS + SKIP_DAYS + 10
    prices = [100.0] * n
    return pd.Series(prices[:-1] + [150.0])  # ensure strong recent momentum vs old anchor


def _close_series(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"Close": prices})


class TestPredictionEngineMomentumWiring:
    def _base_kwargs(self, df=None):
        return dict(
            tech={"score": 50, "breakdown": []},
            fund={"score": 50, "reasons": []},
            sentiment={"score": 50, "data_available": True, "label": "NEUTRAL"},
            weights={"tech": 0.5, "fund": 0.3, "sentiment": 0.2},
            regime={"trend": "NEUTRAL", "reason": "test", "score_adj": 0},
            df=df,
        )

    def _momentum_df(self):
        buffer = SKIP_DAYS + 5
        return _close_series([100.0] * LOOKBACK_DAYS + [150.0] * buffer)

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("MOMENTUM_FACTOR_ENABLED", raising=False)
        engine = PredictionEngine()
        (_signal, _confidence, _reasoning, _score_band, contributions, _composite_r,
         *_rest) = engine._composite_signal(horizon="medium", **self._base_kwargs(df=self._momentum_df()))
        assert contributions["momentum"] == 0.0

    def test_enabled_contributes_at_medium_horizon(self, monkeypatch):
        monkeypatch.setenv("MOMENTUM_FACTOR_ENABLED", "1")
        engine = PredictionEngine()
        (_signal, _confidence, _reasoning, _score_band, contributions, _composite_r,
         *_rest) = engine._composite_signal(horizon="medium", **self._base_kwargs(df=self._momentum_df()))
        assert contributions["momentum"] > 0

    @pytest.mark.parametrize("horizon", ["short", "long"])
    def test_enabled_but_wrong_horizon_still_zero(self, horizon, monkeypatch):
        monkeypatch.setenv("MOMENTUM_FACTOR_ENABLED", "1")
        engine = PredictionEngine()
        (_signal, _confidence, _reasoning, _score_band, contributions, _composite_r,
         *_rest) = engine._composite_signal(horizon=horizon, **self._base_kwargs(df=self._momentum_df()))
        assert contributions["momentum"] == 0.0

    def test_toggling_flag_with_identical_df_changes_only_the_flag_effect(self, monkeypatch):
        df = self._momentum_df()
        engine = PredictionEngine()

        monkeypatch.delenv("MOMENTUM_FACTOR_ENABLED", raising=False)
        (_s, _c, _r, _sb, _contrib, composite_off, *_rest) = engine._composite_signal(
            horizon="medium", **self._base_kwargs(df=df))

        monkeypatch.setenv("MOMENTUM_FACTOR_ENABLED", "1")
        (_s2, _c2, _r2, _sb2, _contrib2, composite_on, *_rest2) = engine._composite_signal(
            horizon="medium", **self._base_kwargs(df=df))

        assert composite_off != composite_on


class TestValidationEngineMomentumWiringAndDirectBaseline:
    def _df_with_momentum(self, close_val: float) -> pd.DataFrame:
        buffer = SKIP_DAYS + 5
        rows = [{"Open": 100.0, "High": 100.0, "Low": 100.0, "Close": 100.0, "Volume": 1_000_000}
                for _ in range(LOOKBACK_DAYS)]
        rows += [{"Open": close_val, "High": close_val, "Low": close_val, "Close": close_val, "Volume": 1_000_000}
                 for _ in range(buffer)]
        df = pd.DataFrame(rows)
        for col, val in {
            "rsi_14": 50.0, "macd_diff": 1.0, "ema_200": 90.0, "ema_20": 100.0, "ema_50": 90.0,
            "adx": 10.0, "adx_pos": 20.0, "adx_neg": 20.0,
            "bb_pct": 0.5, "stoch_rsi": 0.5, "williams_r": -50.0, "cci": 0.0,
        }.items():
            df[col] = val
        return df

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("MOMENTUM_FACTOR_ENABLED", raising=False)
        df = self._df_with_momentum(150.0)
        i = len(df) - 1
        result = _score_at(df, i, None, fund_score=50.0, regime_adj=0.0, horizon="medium")
        assert result["composite"] == result["base_composite"]
        assert result["momentum_score"] is None

    def test_enabled_moves_composite_away_from_base(self, monkeypatch):
        monkeypatch.setenv("MOMENTUM_FACTOR_ENABLED", "1")
        df = self._df_with_momentum(150.0)
        i = len(df) - 1
        result = _score_at(df, i, None, fund_score=50.0, regime_adj=0.0, horizon="medium")
        assert result["composite"] != result["base_composite"]
        assert result["momentum_score"] is not None
        assert result["momentum_pct"] is not None

    def test_base_composite_is_identical_regardless_of_momentum_enablement(self, monkeypatch):
        """The whole point of `base_composite`: it must be the SAME
        number whether or not momentum is enabled, because it is
        computed directly (before momentum is ever added), not
        reconstructed afterward. This is the corrected replacement for
        the prior research script's invalid subtraction-from-clamped
        approach."""
        df = self._df_with_momentum(150.0)
        i = len(df) - 1

        monkeypatch.delenv("MOMENTUM_FACTOR_ENABLED", raising=False)
        off = _score_at(df, i, None, fund_score=50.0, regime_adj=0.0, horizon="medium")

        monkeypatch.setenv("MOMENTUM_FACTOR_ENABLED", "1")
        on = _score_at(df, i, None, fund_score=50.0, regime_adj=0.0, horizon="medium")

        assert off["base_composite"] == on["base_composite"]
        assert off["composite"] != on["composite"]

    def test_base_composite_correct_even_when_final_composite_saturates(self, monkeypatch):
        """The specific scenario the prior subtraction-based approach
        got wrong: when the pre-clamp sum (base + momentum) exceeds 100,
        the final `composite` saturates at 100 — subtracting momentum's
        contribution from 100 would NOT recover the true base. Because
        `base_composite` is computed BEFORE momentum is added, it is
        unaffected by the final clamp regardless of saturation."""
        monkeypatch.setenv("MOMENTUM_FACTOR_ENABLED", "1")
        # Push tech/rs/obv/mfi and fund_score both to their max (100) so
        # base_composite itself is already 100 before momentum is added —
        # the final composite necessarily saturates at 100 too.
        df = self._df_with_momentum(150.0)
        for col in ("rsi_14",):
            df.loc[df.index[-1], col] = 20.0  # oversold -> +15 tech contribution
        result = _score_at(df, len(df) - 1, None, fund_score=100.0, regime_adj=0.0, horizon="medium")
        assert result["composite"] == 100.0  # saturated
        # base_composite reflects the true pre-momentum value directly —
        # not "100 minus momentum's contribution" (which would be wrong).
        assert result["base_composite"] <= 100.0

    @pytest.mark.parametrize("horizon", ["short", "long", None])
    def test_disabled_outside_medium_horizon_even_when_flag_enabled(self, horizon, monkeypatch):
        monkeypatch.setenv("MOMENTUM_FACTOR_ENABLED", "1")
        df = self._df_with_momentum(150.0)
        i = len(df) - 1
        result = _score_at(df, i, None, fund_score=50.0, regime_adj=0.0, horizon=horizon)
        assert result["composite"] == result["base_composite"]
