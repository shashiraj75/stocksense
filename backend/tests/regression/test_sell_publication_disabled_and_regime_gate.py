"""
2026-08-24 stock-picking methodology remediation.

Production evidence (validation_engine.py backtest, 2.9M+ signals at true
scale, not the small paper-trading sample): SELL calls at US long/medium
horizon were statistically significant AND WRONG-SIGNED — stocks flagged
SELL subsequently beat the benchmark by +3.98%/+1.91% on average
(t=8.24/t=20.79). Traced to services/technical_indicators.py::
get_signal_summary (the live, shared scoring function used by
prediction_engine.py, which feeds Daily Picks and the per-stock page)
unconditionally summing two contradictory rule families every call:

  - mean-reversion oscillators (RSI, Bollinger %B, StochRSI, Williams %R,
    CCI): "buy oversold, sell overbought"
  - trend-following rules (MACD, EMA200, EMA20/50 cross, ADX+DI):
    "buy strength, sell weakness"

In a trending market, "overbought" from an oscillator usually just means
"still strong," not "about to reverse" — so the oscillator block was
firing SELL on stocks that kept running.

Two changes, both covered here:
  1. Regime gate: every mean-reversion oscillator's score contribution is
     suppressed (0, not applied) when ADX > 25 (a real, developed trend is
     in force, per this codebase's own existing ADX>25 threshold for its
     trend sub-signal) — active only when ADX <= 25 (range-bound/choppy,
     where mean-reversion has a legitimate edge). Applied identically in
     get_signal_summary (live) and validation_engine.py::_score_at
     (backtest), so future validation runs measure the corrected
     methodology.
  2. SELL publication disabled at both live choke points
     (get_signal_summary's own "overall" mapping, and
     PredictionEngine._composite_signal's final signal decision) — a low
     score is presented as HOLD, never an actionable SELL, until the
     corrected methodology has its own validated track record.
     validation_engine.py's `predicted` classification deliberately still
     includes SELL — it is the measurement instrument used to prove
     whether this fix corrects SELL's alpha in a future validation run;
     nothing acts on a SELL classification produced there.

See Documentation/Engineering-Handbook/Daily-Picks/
DAILY-PICKS-IMPLEMENTATION-REGISTER.md for the full forensic evidence.
"""
import numpy as np
import pandas as pd
import pytest

from services.prediction_engine import PredictionEngine
from services.technical_indicators import get_signal_summary


def _flat_ohlc_row(close=100.0):
    """A zero-body, zero-wick candle — never triggers a candlestick
    pattern (full_range == 0 fails every pattern's range/body checks),
    keeping the candlestick term at its neutral default of 0 so tests can
    isolate the oscillator/trend terms under test."""
    return {"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1_000_000}


def _build_df(overrides: dict, n_rows: int = 5) -> pd.DataFrame:
    """Builds a minimal DataFrame get_signal_summary can score: `n_rows`
    flat, identical rows (n_rows < 21 so get_volume_signal short-circuits
    to not-confirmed — see its own `len(df) < 21` guard — keeping the
    volume term neutral too), with the LAST row's indicator columns set
    to the values under test via `overrides`."""
    rows = [_flat_ohlc_row() for _ in range(n_rows)]
    df = pd.DataFrame(rows)
    # Deliberately unambiguous (not tied) trend-following baseline: the
    # formula's comparisons are all strict `>`, so a tie (e.g. close ==
    # ema_200) silently falls into the bearish "else" branch — using equal
    # defaults would make the "neutral" baseline secretly bearish. RSI and
    # every oscillator sit in their genuine 45-60/neutral zone (no branch
    # fires), and ADX is non-trending by default (10 <= 25, HOLD branch,
    # no score change) so tests only need to override what they're testing.
    base_indicators = {
        "rsi_14": 50.0, "macd_diff": 1.0,
        "ema_200": 90.0, "ema_20": 100.0, "ema_50": 90.0,  # close(100) above both; ema20 above ema50
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
        """RSI > 70 (overbought) must NOT apply its -15 score penalty when
        ADX > 25 (a real trend is in force) — the exact contradiction that
        produced statistically backwards SELL calls in production."""
        df = _build_df({"rsi_14": 75.0, "adx": 40.0, "adx_pos": 40.0, "adx_neg": 5.0})
        result = get_signal_summary(df)
        rsi_entries = [s for s in result["breakdown"] if s["indicator"] == "RSI"]
        assert len(rsi_entries) == 1
        assert rsi_entries[0]["signal"] == "HOLD"
        assert "suppressed" in rsi_entries[0]["reason"]
        # Score must not carry the -15 penalty a non-trending overbought
        # reading would apply: baseline 80 (trend-following) + ADX's own
        # +10 for a confirmed uptrend = 90, RSI contributing 0.
        assert result["score"] == 90

    def test_overbought_rsi_still_applies_when_not_trending(self):
        """The same overbought RSI reading, in a genuinely range-bound
        market (ADX <= 25), must still apply its full -15 penalty —
        mean-reversion logic is only suppressed during real trends, not
        removed altogether."""
        df = _build_df({"rsi_14": 75.0, "adx": 10.0})
        result = get_signal_summary(df)
        rsi_entries = [s for s in result["breakdown"] if s["indicator"] == "RSI"]
        assert rsi_entries[0]["signal"] == "SELL"
        assert result["score"] == 65  # baseline 80 (trend-following only) - 15, unsuppressed

    @pytest.mark.parametrize("col,trending_val,ranging_val", [
        ("bb_pct", 0.95, 0.95),
        ("stoch_rsi", 0.9, 0.9),
        ("williams_r", -10, -10),
        ("cci", 150, 150),
    ])
    def test_each_oscillator_suppressed_only_when_trending(self, col, trending_val, ranging_val):
        trending_df = _build_df({col: trending_val, "adx": 40.0, "adx_pos": 40.0, "adx_neg": 5.0})
        ranging_df = _build_df({col: ranging_val, "adx": 10.0})
        trending_result = get_signal_summary(trending_df)
        ranging_result = get_signal_summary(ranging_df)
        # The overbought/bearish reading must be neutralized while trending
        # (score stays at/near the 80 trend-following baseline)...
        assert trending_result["score"] > ranging_result["score"]
        assert trending_result["score"] >= 80
        # ...and still fire its full penalty when genuinely range-bound.
        assert ranging_result["score"] < 80


class TestSellPublicationDisabled:
    def test_get_signal_summary_never_returns_sell_overall(self):
        """Every mean-reversion AND trend-following rule pushed maximally
        bearish, in a non-trending regime (so oscillators are NOT
        suppressed) — the worst-case score the function can produce.
        overall must still be HOLD, never SELL."""
        df = _build_df({
            "rsi_14": 80.0, "macd_diff": -5.0,
            "ema_200": 200.0, "ema_20": 90.0, "ema_50": 100.0,  # close(100) < ema200; ema20 < ema50
            "adx": 10.0,  # non-trending: oscillators fully active
            "bb_pct": 0.95, "stoch_rsi": 0.95, "williams_r": -5.0, "cci": 150.0,
        })
        result = get_signal_summary(df)
        assert result["score"] <= 10  # deeply bearish score is still possible...
        assert result["overall"] == "HOLD"  # ...but never surfaced as SELL

    def test_get_signal_summary_still_returns_buy_for_strong_bullish_setup(self):
        """The SELL removal must not have collapsed BUY too — a strongly
        bullish, non-trending setup (so oscillators are active) must still
        clear the >=58 BUY threshold."""
        df = _build_df({
            "rsi_14": 20.0, "macd_diff": 5.0,
            "ema_200": 50.0, "ema_20": 110.0, "ema_50": 100.0,
            "adx": 10.0,
            "bb_pct": 0.05, "stoch_rsi": 0.05, "williams_r": -90.0, "cci": -150.0,
        })
        result = get_signal_summary(df)
        assert result["overall"] == "BUY"

    def test_composite_signal_never_returns_sell(self):
        """PredictionEngine._composite_signal (the main production
        composite feeding Daily Picks and the per-stock page) must never
        emit SELL, even for a maximally bearish tech+fundamental input."""
        engine = PredictionEngine()
        signal, *_rest = engine._composite_signal(
            tech={"score": 0, "breakdown": []},
            fund={"score": 0, "reasons": []},
            sentiment={"score": 0, "data_available": True, "label": "NEUTRAL"},
            horizon="medium",
            weights={"tech": 0.5, "fund": 0.3, "sentiment": 0.2},
            regime={"trend": "NEUTRAL", "reason": "test", "score_adj": 0},
        )
        assert signal == "HOLD"

    def test_composite_signal_still_returns_buy_for_strong_input(self):
        engine = PredictionEngine()
        signal, *_rest = engine._composite_signal(
            tech={"score": 100, "breakdown": []},
            fund={"score": 100, "reasons": []},
            sentiment={"score": 100, "data_available": True, "label": "NEUTRAL"},
            horizon="medium",
            weights={"tech": 0.5, "fund": 0.3, "sentiment": 0.2},
            regime={"trend": "NEUTRAL", "reason": "test", "score_adj": 0},
        )
        assert signal == "BUY"


class TestValidationEngineRegimeGateAndMeasurementPreserved:
    """validation_engine.py::_score_at mirrors get_signal_summary's trend
    gate (so a future validation run measures the corrected methodology),
    but — unlike the live path — deliberately does NOT collapse SELL: it
    is the measurement instrument used to prove whether this fix corrects
    SELL's alpha, and nothing acts on a SELL classification produced by
    the backtest."""

    def _validation_df(self, overrides: dict, n_rows: int = 10) -> pd.DataFrame:
        from tests.regression.test_sell_publication_disabled_and_regime_gate import _flat_ohlc_row
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
        from services.validation_engine import _score_at

        trending_df = self._validation_df({"rsi_14": 75.0, "adx": 40.0, "adx_pos": 40.0, "adx_neg": 5.0})
        ranging_df = self._validation_df({"rsi_14": 75.0, "adx": 10.0})
        # Short df (< 20 rows) and benchmark_close=None keep rs/obv/mfi at
        # their neutral 50 defaults, isolating the tech term under test.
        trending = _score_at(trending_df, len(trending_df) - 1, None, fund_score=50.0, regime_adj=0.0)
        ranging = _score_at(ranging_df, len(ranging_df) - 1, None, fund_score=50.0, regime_adj=0.0)
        assert trending["tech"] > ranging["tech"]
        assert trending["composite"] > ranging["composite"]

    def test_sell_classification_thresholds_unchanged(self):
        """This fix must not touch the classification thresholds
        themselves — only the tech sub-score inputs feeding them. SELL
        classification stays available in the backtest as the measurement
        instrument for this fix's own future validation."""
        from services.validation_engine import BUY_THRESHOLD, SELL_THRESHOLD

        assert BUY_THRESHOLD == {"short": 60, "medium": 60, "long": 60}
        assert SELL_THRESHOLD == {"short": 45, "medium": 45, "long": 45}
