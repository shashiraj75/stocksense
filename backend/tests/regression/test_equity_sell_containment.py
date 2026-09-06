"""
2026-08-24 SELL publication containment (equities only). Isolated PR A:
this change ONLY collapses the live/actionable SELL classification into
HOLD, at the two choke points that decide what actually reaches a user
or the Daily Picks product. It does NOT touch the oscillator/trend
scoring logic — see the separate fix/adx-oscillator-regime-gate branch
for that independently reviewable change.

Production evidence (validation_engine.py backtest, 2.9M+ signals): SELL
calls at US long/medium horizon were statistically significant and
backwards (t=8.24/t=20.79 — SELL calls beat the benchmark afterward, not
underperformed it). Live exposure confirmed before acting: daily_picks.py
already only ever publishes signal=="BUY" (Daily Picks itself never
showed SELL); 0 of 194 real paper trades were ever opened on a SELL
signal. The actual live exposure was the individual stock page's
"SELL" recommendation tag and the public Validation page's "SELL Hit
Rate" stat (frontend concern, covered separately in this PR's frontend
changes).

Scope: EQUITIES ONLY. services/crypto_engine.py has its own, fully
independent composite formula and SELL threshold and is deliberately
NOT touched — it is a separate product path with no shared presentation
surface with the equity Validation page (verified 2026-09-06). This PR
must never be used to justify disabling crypto SELL recommendations.

validation_engine.py's `predicted` classification (the historical
research/backtest record) is DELIBERATELY UNCHANGED by this PR — SELL
classifications already persisted, and newly computed by future
validation runs, remain available and correctly labeled as research
evidence, not deleted or silently relabeled.
"""
from services.technical_indicators import get_signal_summary
from services.prediction_engine import PredictionEngine
from services.crypto_engine import predict_crypto
import inspect

import pandas as pd
import pytest


def _flat_ohlc_row(close=100.0):
    return {"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1_000_000}


def _bearish_df(n_rows: int = 5) -> pd.DataFrame:
    """Every rule pushed maximally bearish — the worst-case score the
    live scoring function can produce, with NO oscillator gating in
    play (PR A alone does not suppress anything based on ADX)."""
    rows = [_flat_ohlc_row() for _ in range(n_rows)]
    df = pd.DataFrame(rows)
    indicators = {
        "rsi_14": 80.0, "macd_diff": -5.0,
        "ema_200": 200.0, "ema_20": 90.0, "ema_50": 100.0,
        "adx": 10.0, "adx_pos": 5.0, "adx_neg": 40.0,
        "bb_pct": 0.95, "stoch_rsi": 0.95, "williams_r": -5.0, "cci": 150.0,
    }
    for col, val in indicators.items():
        df[col] = val
    return df


class TestEquitySellPublicationDisabled:
    def test_get_signal_summary_never_returns_sell_overall(self):
        """2026-09-06 correction: get_signal_summary()'s SELL collapse is
        now gated on `suppress_sell` (default False), because the
        function is shared with crypto_engine.py, which must keep seeing
        genuine SELL. The two equity call sites in prediction_engine.py
        pass suppress_sell=True explicitly — this test exercises that
        exact equity-scoped call shape, not the function's bare default."""
        df = _bearish_df()
        result = get_signal_summary(df, suppress_sell=True)
        assert result["score"] <= 10  # deeply bearish score is still possible...
        assert result["overall"] == "HOLD"  # ...but never surfaced as SELL, for equities

    def test_get_signal_summary_default_restores_genuine_sell_for_crypto(self):
        """The function's own default (no suppress_sell) — exactly how
        crypto_engine.py calls it — must NOT collapse SELL. This is the
        equity-only boundary this PR is required to preserve."""
        df = _bearish_df()
        result = get_signal_summary(df)
        assert result["score"] <= 10
        assert result["overall"] == "SELL"

    def test_get_signal_summary_still_returns_buy_for_strong_bullish_setup(self):
        rows = [_flat_ohlc_row() for _ in range(5)]
        df = pd.DataFrame(rows)
        for col, val in {
            "rsi_14": 20.0, "macd_diff": 5.0,
            "ema_200": 50.0, "ema_20": 110.0, "ema_50": 100.0,
            "adx": 10.0, "adx_pos": 40.0, "adx_neg": 5.0,
            "bb_pct": 0.05, "stoch_rsi": 0.05, "williams_r": -90.0, "cci": -150.0,
        }.items():
            df[col] = val
        result = get_signal_summary(df)
        assert result["overall"] == "BUY"

    def test_composite_signal_never_returns_sell(self):
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


class TestValidationHistoricalRecordPreserved:
    def test_validation_engine_sell_classification_thresholds_unchanged(self):
        """The backtest/research record must be untouched by this PR —
        validation_engine.py continues to classify and persist SELL so
        it remains available as historical/research evidence, never
        silently deleted or relabeled."""
        from services.validation_engine import BUY_THRESHOLD, SELL_THRESHOLD
        assert BUY_THRESHOLD == {"short": 60, "medium": 60, "long": 60}
        assert SELL_THRESHOLD == {"short": 45, "medium": 45, "long": 45}

    def test_score_at_can_still_classify_sell_for_research(self):
        from services.validation_engine import _score_at
        rows = [_flat_ohlc_row() for _ in range(10)]
        df = pd.DataFrame(rows)
        for col, val in {
            "rsi_14": 80.0, "macd_diff": -5.0,
            "ema_200": 200.0, "ema_20": 90.0, "ema_50": 100.0,
            "adx": 10.0, "adx_pos": 5.0, "adx_neg": 40.0,
            "bb_pct": 0.95, "stoch_rsi": 0.95, "williams_r": -5.0, "cci": 150.0,
        }.items():
            df[col] = val
        result = _score_at(df, len(df) - 1, None, fund_score=0.0, regime_adj=0.0)
        assert result["composite"] < 45  # would classify SELL under SELL_THRESHOLD


class TestCryptoScopeUntouched:
    def test_crypto_engine_does_not_import_equity_sell_containment(self):
        """crypto_engine.py must retain its own, fully independent
        composite/SELL logic — this PR must never be interpreted as
        having disabled crypto SELL. Verified by inspecting predict_crypto's
        source for its own literal SELL branch, independent of
        get_signal_summary's now-collapsed 'overall' field."""
        import services.crypto_engine as ce
        source = inspect.getsource(ce)
        assert 'signal = "BUY" if composite >= 55 else "SELL" if composite <= 45 else "HOLD"' in source, (
            "crypto_engine.py's own independent SELL classification must remain "
            "present and unmodified — equity SELL containment must not silently "
            "extend to crypto"
        )
