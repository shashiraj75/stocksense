"""
Learning Alpha Engine integrity remediation — Phase 1 (market-labeling fix).

Confirmed defect: PredictionEngine._composite_signal() has a fully-resolved
`market` parameter in scope at its `log_prediction()` call site, but never
forwarded it — so every call fell through to
services.alpha_engine.store.log_prediction()'s default `market="IN"`,
regardless of the actual market the prediction was for. US Stock Detail
predictions and US Daily Picks Phase-1 candidate predictions were silently
logged as market="IN".

Fix: prediction_engine.py's log_prediction() call now passes market=market
explicitly (the one already-correct value threaded in from predict()).

These tests prove the fix without any network/DB access: log_prediction is
mocked at its import site (services.alpha_engine.store.log_prediction), so
no real SQLite/Postgres write occurs and no yfinance/screener call is made.
"""
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

import services.daily_picks as dp
import services.prediction_engine as pe


def _make_composite_signal_args(symbol: str, market: str):
    """Minimal, valid inputs for PredictionEngine._composite_signal —
    covers every field the method actually reads (see the method body),
    with `_confidence_engine` patched out separately since its internals
    are unrelated to this fix."""
    tech = {"score": 55, "breakdown": [], "candlestick": {}}
    fund = {"score": 60, "reasons": []}
    sentiment = {"score": 50, "label": "NEUTRAL", "bullish": 0, "bearish": 0, "data_available": True}
    horizon = "short"
    weights = {"tech": 0.4, "fund": 0.3, "sentiment": 0.3}
    regime = {"trend": "BULLISH", "reason": "test regime", "score_adj": 0}
    df = pd.DataFrame({"Close": [100.0]})
    info = {"currentPrice": 100.0}
    return dict(
        tech=tech, fund=fund, sentiment=sentiment, horizon=horizon,
        weights=weights, regime=regime, quality=None, df=df, info=info,
        symbol=symbol, market=market,
    )


@pytest.fixture(autouse=True)
def _stub_confidence_engine(monkeypatch):
    """_confidence_engine's own internals are unrelated to this fix and
    unnecessary to exercise here — stub it to a fixed return so
    _composite_signal can run to completion with minimal fixtures."""
    monkeypatch.setattr(
        pe.PredictionEngine, "_confidence_engine",
        lambda self, **kwargs: (70, "high", {}),
    )


@pytest.mark.regression
class TestCompositeSignalMarketForwarding:
    """Direct proof of the fix: _composite_signal's log_prediction call
    now carries the exact `market` it was invoked with."""

    def test_in_prediction_logs_market_in(self):
        with patch("services.alpha_engine.store.log_prediction") as mock_log:
            engine = pe.PredictionEngine()
            engine._composite_signal(**_make_composite_signal_args("RELIANCE", "IN"))
        assert mock_log.call_count == 1
        assert mock_log.call_args.kwargs["market"] == "IN"

    def test_us_prediction_logs_market_us(self):
        """The exact regression this fix addresses: before the fix, this
        call site never passed `market` at all, so store.log_prediction's
        default of "IN" silently mislabeled every US prediction."""
        with patch("services.alpha_engine.store.log_prediction") as mock_log:
            engine = pe.PredictionEngine()
            engine._composite_signal(**_make_composite_signal_args("AAPL", "US"))
        assert mock_log.call_count == 1
        assert mock_log.call_args.kwargs["market"] == "US"

    def test_market_comes_from_the_parameter_not_symbol_inspection(self):
        """No hardcoded IN/US symbol logic was added — market must reflect
        whatever the caller passed, even when the symbol "looks like" the
        other market. An Indian-sounding symbol under market="US" must
        still log market="US", and a US-sounding symbol under market="IN"
        must still log market="IN"."""
        with patch("services.alpha_engine.store.log_prediction") as mock_log:
            engine = pe.PredictionEngine()
            engine._composite_signal(**_make_composite_signal_args("TCS", "US"))
        assert mock_log.call_args.kwargs["market"] == "US"

        with patch("services.alpha_engine.store.log_prediction") as mock_log2:
            engine = pe.PredictionEngine()
            engine._composite_signal(**_make_composite_signal_args("AAPL", "IN"))
        assert mock_log2.call_args.kwargs["market"] == "IN"

    def test_fix_changes_only_the_market_argument_not_prediction_output_or_other_logged_fields(self):
        """The primary objective is scoped to the market label alone —
        confirm the returned prediction tuple and every other field passed
        to log_prediction are identical to what the same inputs already
        produced before this fix (factor values, combined_alpha, signal,
        price, regime_label, confidence, composite score are untouched)."""
        args_in = _make_composite_signal_args("RELIANCE", "IN")
        args_us = dict(args_in, symbol="RELIANCE", market="US")  # only market differs

        with patch("services.alpha_engine.store.log_prediction") as mock_in:
            engine = pe.PredictionEngine()
            result_in = engine._composite_signal(**args_in)
        with patch("services.alpha_engine.store.log_prediction") as mock_us:
            engine = pe.PredictionEngine()
            result_us = engine._composite_signal(**args_us)

        # Prediction output (signal, confidence, reasoning, score_band,
        # contributions, composite_r, confidence_score/band/components) is
        # entirely independent of the `market` label passed to logging —
        # same inputs otherwise, so results must match exactly.
        assert result_in == result_us

        call_in, call_us = mock_in.call_args.kwargs, mock_us.call_args.kwargs
        for field in ("symbol", "horizon", "factor_zscores", "combined_alpha",
                      "meta_alpha", "signal", "price", "regime_label"):
            assert call_in[field] == call_us[field], f"{field} differs — only `market` should"
        assert call_in["market"] == "IN"
        assert call_us["market"] == "US"


@pytest.mark.regression
class TestDailyPicksPhase1MarketForwarding:
    """Daily Picks Phase 1 (_predict_stock) reaches PredictionEngine.predict
    (and therefore _composite_signal's log_prediction call) with the real
    market — proving the fix closes the loop for Daily Picks candidate
    scoring, not just the standalone Stock Detail path."""

    def test_predict_stock_forwards_market_to_predict_for_us(self):
        captured = {}

        async def fake_predict(self, symbol, market, horizon):
            captured["symbol"], captured["market"], captured["horizon"] = symbol, market, horizon
            return {"signal": "BUY", "confidence": 90}

        with patch.object(pe.PredictionEngine, "predict", fake_predict):
            dp._predict_stock("AAPL", "short", "US")

        assert captured["market"] == "US"
        assert captured["symbol"] == "AAPL"

    def test_predict_stock_forwards_market_to_predict_for_in(self):
        captured = {}

        async def fake_predict(self, symbol, market, horizon):
            captured["market"] = market
            return {"signal": "BUY", "confidence": 90}

        with patch.object(pe.PredictionEngine, "predict", fake_predict):
            dp._predict_stock("RELIANCE", "short", "IN")

        assert captured["market"] == "IN"


_FAKE_REGIME = {"regime_id": 0, "label": "neutral", "description": "", "trend": "BULLISH",
                 "score_adj": 0, "weight_multipliers": {}}


def _fake_ranked_buy_pick(symbol: str) -> dict:
    """A single, minimal, quality-gate-passing BUY candidate — enough for
    Phase 5 selection to include it in `top_buy`, so Phase 7 actually logs
    something to assert on."""
    return {
        "symbol": symbol,
        "signal": "BUY",
        "confidence": 90,
        "ranking_alpha": 0.5,
        "combined_alpha": 0.5,
        "meta_alpha": None,
        "factor_zscores": {"tech": 0.1, "fund": 0.1, "sentiment": 0.1, "quality": 0.1},
        "price": 100.0,
        "reasoning": [],
    }


@pytest.mark.regression
class TestDailyPicksPhase7MarketForwarding:
    """Phase 7's own log_prediction call already passed market=market
    correctly before this fix — locking that in explicitly so any future
    change can't silently regress it back to relying on the (now-fixed)
    default."""

    def test_phase7_final_pick_logs_the_generation_market(self):
        with patch("services.daily_picks._bulk_screen",
                    return_value=(["AAPL"], 5, "screener", False, 10,
                                  {"universe_candidate_count": 1, "attempts": 1,
                                   "reason": "healthy_screener_universe", "error_category": "none"})), \
             patch("services.daily_picks._write_score_snapshots"), \
             patch("services.daily_picks._zscore_and_rank",
                   return_value=[_fake_ranked_buy_pick("AAPL")]), \
             patch("services.daily_picks._predict_stock",
                   return_value={"symbol": "AAPL", "horizon": "short", "signal": "BUY"}), \
             patch("services.alpha_engine.outcome_logger.resolve_pending_outcomes"), \
             patch("services.alpha_engine.regime_cluster.detect_regime", return_value=_FAKE_REGIME), \
             patch("services.alpha_engine.ic_engine.get_ic_weights", return_value={}), \
             patch("services.alpha_engine.store.log_prediction") as mock_log, \
             patch("services.global_context.get_global_context", return_value={}), \
             patch("services.daily_picks._fetch_returns_matrix", return_value=None), \
             patch("services.daily_picks.yf.download", return_value=pd.DataFrame()), \
             patch("os.getenv", return_value=None), \
             patch("builtins.open", MagicMock()), \
             patch("json.dump"):
            try:
                dp._generate_picks_inner("US", job_id=None)
            except Exception:
                # Only Phase 7's log_prediction call matters for this test —
                # any downstream persistence/telemetry failure in this heavily
                # mocked harness is irrelevant to what we're asserting.
                pass

        assert mock_log.call_count >= 1
        for call in mock_log.call_args_list:
            assert call.kwargs["market"] == "US"
            assert call.kwargs["is_daily_pick"] is True
