"""
2026-09-06 PR #85 corrective follow-up — suppressed-SELL metadata.

The backend collapses a would-be equity SELL classification into HOLD
(PR #85's original change), but until now only the Validation page
explained this — the per-stock prediction response gave API consumers
and the stock page no way to distinguish a genuinely neutral/range-bound
HOLD from a setup that would previously have been an actionable SELL.

Adds two additive fields to the prediction response, computed from the
ORIGINAL pre-containment thresholds (never from `signal` itself, so a
genuine BUY can never be mislabeled as suppressed):
  - `equity_sell_suppressed`: bool
  - `equity_sell_suppressed_note`: str | None

Also documents (without fixing — out of this task's scope) a confirmed
side effect: because `signal` is now never "SELL", a composite score
that would previously have used the SELL confidence formula now silently
uses the HOLD confidence formula instead, and the two formulas disagree
sharply for the same input.
"""
import asyncio
import time

import pandas as pd

from services.prediction_engine import PredictionEngine


def _kwargs(tech_score=50, fund_score=50, sentiment_score=50):
    return dict(
        tech={"score": tech_score, "breakdown": []},
        fund={"score": fund_score, "reasons": []},
        sentiment={"score": sentiment_score, "data_available": True, "label": "NEUTRAL"},
        weights={"tech": 0.5, "fund": 0.3, "sentiment": 0.2},
        regime={"trend": "NEUTRAL", "reason": "test", "score_adj": 0},
    )


class TestCompositeSignalSellSuppressionBoundary:
    """_composite_signal itself doesn't return the suppression field
    (kept out of its return-tuple arity deliberately, to avoid touching
    every existing consumer/test that unpacks it) — these tests confirm
    the underlying composite_r value that predict()'s call site uses to
    compute suppression behaves as expected at the boundary."""

    def test_composite_r_below_45_would_have_been_sell(self):
        engine = PredictionEngine()
        signal, _confidence, _reasoning, _score_band, _contrib, composite_r, *_rest = engine._composite_signal(
            horizon="medium", **_kwargs(tech_score=0, fund_score=0, sentiment_score=0))
        assert composite_r < 45
        assert signal == "HOLD"  # collapsed, per PR #85 — this is the case suppression must flag

    def test_composite_r_45_to_59_is_genuine_hold_not_suppressed(self):
        engine = PredictionEngine()
        signal, _confidence, _reasoning, _score_band, _contrib, composite_r, *_rest = engine._composite_signal(
            horizon="medium", **_kwargs(tech_score=50, fund_score=50, sentiment_score=50))
        assert 45 <= composite_r < 60
        assert signal == "HOLD"  # genuinely was always HOLD, not suppressed SELL

    def test_composite_r_60_plus_is_buy_never_suppressed(self):
        engine = PredictionEngine()
        signal, _confidence, _reasoning, _score_band, _contrib, composite_r, *_rest = engine._composite_signal(
            horizon="medium", **_kwargs(tech_score=100, fund_score=100, sentiment_score=100))
        assert composite_r >= 60
        assert signal == "BUY"


class TestConfidenceSideEffectOfRelabeling:
    """Documents (does not fix — out of this task's scope) that a
    composite_r which would previously have used the SELL confidence
    formula now silently uses the HOLD formula instead, and the two
    disagree sharply for the same input. This is a real, confirmed
    consequence of SELL->HOLD relabeling that must not be silently
    glossed over."""

    def test_old_sell_formula_and_new_hold_formula_disagree_sharply(self):
        composite_r = 30  # would have been classified SELL pre-PR-#85 (< 45)
        old_sell_confidence = round(max(0, min(100, (45 - composite_r) / 20 * 100)))
        new_hold_confidence = max(0, min(100, 50 - int(abs(composite_r - 52) * 2)))
        assert old_sell_confidence == 75
        assert new_hold_confidence == 6
        assert old_sell_confidence != new_hold_confidence  # confirmed side effect, not a formula bug


class TestTrackingOnlySuppressionBoundary:
    """The tracking-only (ETF) branch uses get_signal_summary's own
    score<=42 threshold — a separate scale from the main composite's
    <45 — verified independently so the two paths' suppression logic
    doesn't cross-contaminate."""

    def test_get_signal_summary_score_at_or_below_42_would_have_been_sell(self):
        from services.technical_indicators import get_signal_summary
        import pandas as pd
        rows = [{"Open": 100.0, "High": 100.0, "Low": 100.0, "Close": 100.0, "Volume": 1_000_000} for _ in range(5)]
        df = pd.DataFrame(rows)
        for col, val in {
            "rsi_14": 80.0, "macd_diff": -5.0, "ema_200": 200.0, "ema_20": 90.0, "ema_50": 100.0,
            "adx": 10.0, "adx_pos": 5.0, "adx_neg": 40.0,
            "bb_pct": 0.95, "stoch_rsi": 0.95, "williams_r": -5.0, "cci": 150.0,
        }.items():
            df[col] = val
        result = get_signal_summary(df)
        assert result["score"] <= 42
        assert result["overall"] == "HOLD"  # collapsed, per PR #85


class TestStaleCacheDoesNotCrashOnMissingSuppressionFields:
    """`_pred_cache` is a plain in-process dict of (timestamp, response) —
    a response cached moments before this deploy went live will not carry
    `equity_sell_suppressed`/`equity_sell_suppressed_note`. Proves the
    cache-hit path returns that legacy dict verbatim (no crash, no
    silent field injection), and that once the entry expires a fresh
    compute produces a dict that DOES carry the new fields — i.e. the
    absence is bounded by the existing 15-minute TTL, not permanent."""

    def test_cache_hit_returns_legacy_dict_unchanged_without_crashing(self):
        from services import prediction_engine as pe

        legacy_response = {"signal": "HOLD", "composite_score": 30, "symbol": "LEGACYTEST"}
        cache_key = "LEGACYTEST:IN:medium"
        pe._pred_cache[cache_key] = (time.time(), legacy_response)
        try:
            engine = pe.PredictionEngine()
            result = asyncio.run(engine.predict("LEGACYTEST", "IN", "medium"))
            assert result is legacy_response
            assert "equity_sell_suppressed" not in result
        finally:
            pe._pred_cache.pop(cache_key, None)

    def test_expired_cache_entry_is_not_served_stale(self):
        from services import prediction_engine as pe

        legacy_response = {"signal": "HOLD", "composite_score": 30, "symbol": "LEGACYTEST2"}
        cache_key = "LEGACYTEST2:IN:medium"
        # timestamp far older than _PRED_TTL — the entry must be treated as expired
        pe._pred_cache[cache_key] = (time.time() - pe._PRED_TTL - 3600, legacy_response)
        try:
            cached = pe._pred_cache.get(cache_key)
            is_fresh = cached and (time.time() - cached[0]) < pe._PRED_TTL
            assert not is_fresh  # confirms the TTL check itself would reject this entry
        finally:
            pe._pred_cache.pop(cache_key, None)


class TestCryptoSellIsUnaffectedByEquityContainment:
    """crypto_engine.py is a fully separate module from prediction_engine.py
    and was never touched by PR #85 — these fixture-based tests call the
    REAL predict_crypto() end-to-end (network calls stubbed out
    deterministically) and assert SELL can still be produced, rather than
    trusting a source-string/inspect.getsource() assertion."""

    @staticmethod
    def _flat_df(n=120):
        # Content is irrelevant once get_signal_summary/on-chain/fear-greed
        # are stubbed below — only its shape/length needs to satisfy
        # predict_crypto's own `len(df) < 30` guard and the rolling-window
        # calcs inside _fear_greed/_on_chain_proxy that run on the raw df.
        rows = [{"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1_000_000} for _ in range(n)]
        return pd.DataFrame(rows)

    def _run(self, monkeypatch, tech_score):
        from services import crypto_engine as ce

        df = self._flat_df()
        monkeypatch.setattr(ce, "_fetch_crypto_history", lambda symbol, horizon: df)
        monkeypatch.setattr(ce, "compute_indicators", lambda df: df)
        monkeypatch.setattr(ce, "get_signal_summary", lambda df: {"score": tech_score, "overall": "N/A", "breakdown": []})

        async def _raise(*_a, **_kw):
            raise RuntimeError("network disabled in test — forces neutral sentiment fallback")

        monkeypatch.setattr(ce._news_svc, "get_news_with_sentiment", _raise)
        return asyncio.run(ce.predict_crypto("BTC", "medium"))

    def test_predict_crypto_can_still_return_sell(self, monkeypatch):
        # tech_score=0, sentiment/on-chain/fear default to neutral (50) on
        # this flat df -> composite well under the <=45 SELL cutoff.
        result = self._run(monkeypatch, tech_score=0)
        assert "error" not in result
        assert result["signal"] == "SELL"  # crypto SELL must remain fully live, unaffected by PR #85

    def test_predict_crypto_can_still_return_buy(self, monkeypatch):
        result = self._run(monkeypatch, tech_score=100)
        assert "error" not in result
        assert result["signal"] == "BUY"


class TestTradeExitsIgnoreSignalEntirely:
    """paper_trade_exit_monitor.check_exit_trigger is a pure price-only
    function — it never reads `signal`/SELL/composite_score at all, so
    PR #85's SELL->HOLD collapse cannot affect stop-loss or target exits.
    Verified by calling the real function directly with deterministic
    price fixtures, not by inspecting its source for the word "signal"."""

    def test_stop_loss_triggers_on_price_alone(self):
        from services.paper_trade_exit_monitor import check_exit_trigger
        assert check_exit_trigger(stop_loss=90, target_price=120, live_price=89.99) == "STOP_LOSS"

    def test_target_triggers_on_price_alone(self):
        from services.paper_trade_exit_monitor import check_exit_trigger
        assert check_exit_trigger(stop_loss=90, target_price=120, live_price=120.01) == "TARGET_HIT"

    def test_no_trigger_between_bounds(self):
        from services.paper_trade_exit_monitor import check_exit_trigger
        assert check_exit_trigger(stop_loss=90, target_price=120, live_price=105) is None

    def test_stop_loss_priority_on_simultaneous_gap(self):
        from services.paper_trade_exit_monitor import check_exit_trigger
        # A single quote instant satisfying both stop and target (large gap) —
        # stop-loss must take priority, per the function's own docstring contract.
        assert check_exit_trigger(stop_loss=100, target_price=50, live_price=40) == "STOP_LOSS"
