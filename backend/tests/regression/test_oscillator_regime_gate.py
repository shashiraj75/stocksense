"""
2026-08-24 methodology remediation — oscillator/trend regime gate.
Isolated PR B: this change ONLY suppresses mean-reversion oscillator
contributions when ADX > 25 (a real trend is in force). It does NOT
touch SELL/BUY/HOLD classification thresholds — see the separate
fix/equity-sell-containment branch for that independently reviewable
change.

Production evidence (validation_engine.py backtest, 2.9M+ signals):
`services/technical_indicators.py::get_signal_summary` unconditionally
summed two contradictory rule families every call — mean-reversion
oscillators (RSI/Bollinger/StochRSI/Williams-%R/CCI: "buy oversold,
sell overbought") and trend-following rules (MACD/EMA/ADX: "buy
strength, sell weakness"). In a trending market, "overbought" from an
oscillator usually just means "still strong," not "about to reverse."

Fix: every mean-reversion oscillator's score contribution is suppressed
(0, not applied) when ADX > 25 (this codebase's own existing
trend-strength threshold) — active at full weight only when ADX <= 25
(range-bound/choppy, where mean-reversion has a legitimate edge).
Applied identically in get_signal_summary (live) and
validation_engine.py::_score_at (backtest).
"""
import pandas as pd
import pytest

from services.technical_indicators import get_signal_summary
from services.validation_engine import _score_at


def _flat_ohlc_row(close=100.0):
    return {"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1_000_000}


def _build_df(overrides: dict, n_rows: int = 5) -> pd.DataFrame:
    rows = [_flat_ohlc_row() for _ in range(n_rows)]
    df = pd.DataFrame(rows)
    # Deliberately unambiguous (not tied) trend-following baseline — the
    # formula's comparisons are all strict `>`, so a tie (e.g. close ==
    # ema_200) silently falls into the bearish "else" branch.
    base_indicators = {
        "rsi_14": 50.0, "macd_diff": 1.0,
        "ema_200": 90.0, "ema_20": 100.0, "ema_50": 90.0,
        "adx": 10.0, "adx_pos": 20.0, "adx_neg": 20.0,
        "bb_pct": 0.5, "stoch_rsi": 0.5, "williams_r": -50.0, "cci": 0.0,
    }
    for col, val in base_indicators.items():
        df[col] = val
    for col, val in overrides.items():
        df.loc[df.index[-1], col] = val
    return df


class TestOscillatorRegimeGate:
    def test_overbought_rsi_suppressed_when_trending(self):
        df = _build_df({"rsi_14": 75.0, "adx": 40.0, "adx_pos": 40.0, "adx_neg": 5.0})
        result = get_signal_summary(df)
        rsi_entries = [s for s in result["breakdown"] if s["indicator"] == "RSI"]
        assert rsi_entries[0]["signal"] == "HOLD"
        assert "suppressed" in rsi_entries[0]["reason"]
        assert result["score"] == 90  # baseline 80 + ADX's own +10 for confirmed uptrend

    def test_overbought_rsi_still_applies_when_not_trending(self):
        df = _build_df({"rsi_14": 75.0, "adx": 10.0})
        result = get_signal_summary(df)
        rsi_entries = [s for s in result["breakdown"] if s["indicator"] == "RSI"]
        assert rsi_entries[0]["signal"] == "SELL"  # unsuppressed, individual-indicator tag
        assert result["score"] == 65

    def test_adx_exactly_25_is_not_trending(self):
        """Strict '> 25', not '>= 25' — the boundary itself unsuppressed."""
        df = _build_df({"rsi_14": 75.0, "adx": 25.0})
        result = get_signal_summary(df)
        rsi_entries = [s for s in result["breakdown"] if s["indicator"] == "RSI"]
        assert rsi_entries[0]["signal"] == "SELL"

    def test_adx_just_above_25_is_trending(self):
        df = _build_df({"rsi_14": 75.0, "adx": 25.01, "adx_pos": 40.0, "adx_neg": 5.0})
        result = get_signal_summary(df)
        rsi_entries = [s for s in result["breakdown"] if s["indicator"] == "RSI"]
        assert rsi_entries[0]["signal"] == "HOLD"

    def test_missing_adx_defaults_to_not_trending(self):
        df = _build_df({"rsi_14": 75.0}).drop(columns=["adx"])
        result = get_signal_summary(df)
        rsi_entries = [s for s in result["breakdown"] if s["indicator"] == "RSI"]
        assert rsi_entries[0]["signal"] == "SELL"

    def test_nan_adx_defaults_to_not_trending(self):
        df = _build_df({"rsi_14": 75.0, "adx": float("nan")})
        result = get_signal_summary(df)
        rsi_entries = [s for s in result["breakdown"] if s["indicator"] == "RSI"]
        assert rsi_entries[0]["signal"] == "SELL"

    @pytest.mark.parametrize("col,val", [
        ("bb_pct", 0.95), ("stoch_rsi", 0.9), ("williams_r", -10), ("cci", 150),
    ])
    def test_each_oscillator_suppressed_only_when_trending(self, col, val):
        trending_df = _build_df({col: val, "adx": 40.0, "adx_pos": 40.0, "adx_neg": 5.0})
        ranging_df = _build_df({col: val, "adx": 10.0})
        trending_result = get_signal_summary(trending_df)
        ranging_result = get_signal_summary(ranging_df)
        assert trending_result["score"] > ranging_result["score"]
        assert trending_result["score"] >= 80
        assert ranging_result["score"] < 80

    def test_sell_and_buy_classification_thresholds_completely_unchanged(self):
        """PR B does not touch classification at all — SELL still fires
        at score<=42 in the raw scoring function (unlike the separate
        SELL-containment PR)."""
        df = _build_df({
            "rsi_14": 80.0, "macd_diff": -5.0,
            "ema_200": 200.0, "ema_20": 90.0, "ema_50": 100.0,
            "adx": 10.0,
            "bb_pct": 0.95, "stoch_rsi": 0.95, "williams_r": -5.0, "cci": 150.0,
        })
        result = get_signal_summary(df)
        assert result["score"] <= 10
        assert result["overall"] == "SELL"  # PR B alone still permits SELL


class TestValidationEngineOscillatorGateParity:
    def _validation_df(self, overrides: dict, n_rows: int = 10) -> pd.DataFrame:
        rows = [_flat_ohlc_row() for _ in range(n_rows)]
        df = pd.DataFrame(rows)
        base = {
            "rsi_14": 50.0, "macd_diff": 1.0,
            "ema_200": 90.0, "ema_20": 100.0, "ema_50": 90.0,
            "adx": 10.0, "adx_pos": 20.0, "adx_neg": 20.0,
            "bb_pct": 0.5, "stoch_rsi": 0.5, "williams_r": -50.0, "cci": 0.0,
        }
        for col, val in base.items():
            df[col] = val
        for col, val in overrides.items():
            df.loc[df.index[-1], col] = val
        return df

    def test_score_at_suppresses_oscillators_when_trending(self):
        trending_df = self._validation_df({"rsi_14": 75.0, "adx": 40.0, "adx_pos": 40.0, "adx_neg": 5.0})
        ranging_df = self._validation_df({"rsi_14": 75.0, "adx": 10.0})
        trending = _score_at(trending_df, len(trending_df) - 1, None, fund_score=50.0, regime_adj=0.0)
        ranging = _score_at(ranging_df, len(ranging_df) - 1, None, fund_score=50.0, regime_adj=0.0)
        assert trending["tech"] > ranging["tech"]
        assert trending["composite"] > ranging["composite"]

    def test_sell_classification_thresholds_unchanged(self):
        from services.validation_engine import BUY_THRESHOLD, SELL_THRESHOLD
        assert BUY_THRESHOLD == {"short": 60, "medium": 60, "long": 60}
        assert SELL_THRESHOLD == {"short": 45, "medium": 45, "long": 45}
