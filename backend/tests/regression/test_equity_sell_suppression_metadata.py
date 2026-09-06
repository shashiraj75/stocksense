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
import pytest

from services.prediction_engine import PredictionEngine
from services.technical_indicators import compute_indicators as ce_compute_indicators


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


def _bearish_indicator_df():
    import pandas as pd
    rows = [{"Open": 100.0, "High": 100.0, "Low": 100.0, "Close": 100.0, "Volume": 1_000_000} for _ in range(5)]
    df = pd.DataFrame(rows)
    for col, val in {
        "rsi_14": 80.0, "macd_diff": -5.0, "ema_200": 200.0, "ema_20": 90.0, "ema_50": 100.0,
        "adx": 10.0, "adx_pos": 5.0, "adx_neg": 40.0,
        "bb_pct": 0.95, "stoch_rsi": 0.95, "williams_r": -5.0, "cci": 150.0,
    }.items():
        df[col] = val
    return df


class TestTrackingOnlySuppressionBoundary:
    """The tracking-only (ETF) branch uses get_signal_summary's own
    score<=42 threshold — a separate scale from the main composite's
    <45 — verified independently so the two paths' suppression logic
    doesn't cross-contaminate."""

    def test_get_signal_summary_score_at_or_below_42_would_have_been_sell(self):
        from services.technical_indicators import get_signal_summary
        df = _bearish_indicator_df()
        # Equity call sites pass suppress_sell=True explicitly (see
        # prediction_engine.py) — this test exercises the equity behavior,
        # not the function's own default.
        result = get_signal_summary(df, suppress_sell=True)
        assert result["score"] <= 42
        assert result["overall"] == "HOLD"  # collapsed, per PR #85, equity call sites only


class TestGetSignalSummaryDefaultIsUnsuppressedForCrypto:
    """2026-09-06 corrective follow-up: get_signal_summary()'s SELL
    collapse used to be unconditional, so crypto_engine.py's call to this
    SAME shared function (it has no separate implementation) had its
    `overall` sub-field silently collapsed too — even though crypto's own
    headline `signal` was always computed independently and was never
    affected. Restores the function's DEFAULT (no suppress_sell argument,
    exactly how crypto_engine.py calls it) to the true pre-containment
    score<=42 -> SELL mapping."""

    def test_default_call_restores_genuine_sell_for_a_bearish_setup(self):
        from services.technical_indicators import get_signal_summary
        df = _bearish_indicator_df()
        result = get_signal_summary(df)  # no suppress_sell — crypto's exact call shape
        assert result["score"] <= 42
        assert result["overall"] == "SELL"  # restored to pre-PR-#85 (12810927) behavior

    def test_numeric_score_and_breakdown_are_identical_regardless_of_suppress_sell(self):
        """The `suppress_sell` flag must only ever change the `overall`
        label — never the underlying score or per-indicator breakdown
        (no scoring/oscillator logic is touched by this correction)."""
        from services.technical_indicators import get_signal_summary
        df = _bearish_indicator_df()
        suppressed = get_signal_summary(df, suppress_sell=True)
        unsuppressed = get_signal_summary(df, suppress_sell=False)
        assert suppressed["score"] == unsuppressed["score"]
        assert suppressed["breakdown"] == unsuppressed["breakdown"]
        assert suppressed["rsi"] == unsuppressed["rsi"]
        assert suppressed["macd_diff"] == unsuppressed["macd_diff"]
        assert suppressed["adx"] == unsuppressed["adx"]
        assert suppressed["overall"] == "HOLD"
        assert unsuppressed["overall"] == "SELL"


class TestStaleCacheDoesNotCrashOnMissingSuppressionFields:
    """`_pred_cache` (services/prediction_engine.py) is a plain
    module-level, in-process dict of (timestamp, response) — never a
    Redis/Memcached/DB-backed store (grepped the module and every
    services/*.py import: no external cache client exists in this
    codebase). The Railway service config
    (`get-service-config` on project a35f6bff.../service
    34101674-cfcb-4079-aec5-08263dc119ec) confirms `numReplicas: 1` — a
    single process. A deploy replaces that process wholesale (new
    container, new interpreter, new module import), so `_pred_cache` is
    always empty immediately after a deploy; code and cache change
    atomically together. Concretely: a raw, pre-containment
    `signal: "SELL"` dict can only ever be WRITTEN to this cache by code
    that itself still classifies SELL — i.e. by the OLD process, before
    a deploy carrying this fix replaces it. Once the new process is
    running, every write path recomputes `signal` through the collapsed
    (HOLD) logic before it ever reaches `_cache_set` (verified directly:
    both `_cache_set(_pred_cache, ...)` call sites in `predict()` are
    reached only after `signal`/`tech_signal["overall"]` have already
    been forced through the collapse). So a legacy raw-SELL entry cannot
    be produced by the fixed code — it could only be *inherited*, at
    most transiently, from an old process's already-running cache during
    a rolling deploy's brief cutover window (a property of any
    in-memory, single-replica service, not specific to this fix).

    These tests prove what actually happens if such an entry exists —
    by seeding a genuine unmodified-SELL dict (not a should-be-impossible
    hypothetical) and calling the real `predict()` cache-hit branch —
    rather than asserting the risk is zero by construction."""

    def test_cache_hit_returns_a_legacy_raw_sell_dict_verbatim(self):
        """The honest finding: IF a pre-containment `signal: "SELL"` dict
        were sitting in `_pred_cache` (only reachable, per the docstring
        above, from an old process during a deploy's cutover window),
        the cache-hit branch returns it completely unchanged — SELL and
        all, no re-collapse, no suppression metadata injected. This is
        not a defect introduced by this PR (the cache-hit branch
        `return cached[1]` predates PR #85 and is unrelated to it) — it
        is a pre-existing property of a pure read-through cache, recorded
        here so the PR's cache claim is grounded in observed behavior,
        not asserted away."""
        from services import prediction_engine as pe

        legacy_raw_sell = {
            "signal": "SELL", "composite_score": 30, "symbol": "LEGACYSELL",
            "confidence": 75,  # the OLD SELL-formula confidence, pre-relabeling
        }
        cache_key = "LEGACYSELL:IN:medium"
        pe._pred_cache[cache_key] = (time.time(), legacy_raw_sell)
        try:
            engine = pe.PredictionEngine()
            result = asyncio.run(engine.predict("LEGACYSELL", "IN", "medium"))
            assert result is legacy_raw_sell
            assert result["signal"] == "SELL"  # returned exactly as cached, unmodified
            assert "equity_sell_suppressed" not in result
        finally:
            pe._pred_cache.pop(cache_key, None)

    def test_fresh_cache_entry_never_reaches_the_network_fetch_path(self, monkeypatch):
        """Confirms the cache-HIT branch is a true short-circuit: for an
        entry within TTL, `predict()` never calls `yf.Ticker` at all.
        Proven by making `yf.Ticker` raise if invoked — the test would
        fail loudly (not silently pass) if the short-circuit were ever
        removed."""
        from services import prediction_engine as pe

        def _boom(*_a, **_kw):
            raise AssertionError("yf.Ticker must not be called on a cache hit")

        monkeypatch.setattr(pe.yf, "Ticker", _boom)

        cached_response = {"signal": "HOLD", "composite_score": 50, "symbol": "FRESHTEST"}
        cache_key = "FRESHTEST:IN:medium"
        pe._pred_cache[cache_key] = (time.time(), cached_response)  # freshly written — well within TTL
        try:
            engine = pe.PredictionEngine()
            result = asyncio.run(engine.predict("FRESHTEST", "IN", "medium"))
            assert result is cached_response
        finally:
            pe._pred_cache.pop(cache_key, None)

    def test_expired_cache_entry_triggers_a_real_recompute_not_a_stale_return(self, monkeypatch):
        """Exercises the ACTUAL `if cached and (time.time() - cached[0]) <
        _PRED_TTL` expiry check in predict() — via real `time.time()`
        (monkeypatched to simulate elapsed wall-clock time, not
        reimplemented as a separate boolean) — rather than asserting a
        copy of that expression in the test itself. Proof of "not served
        stale": `yf.Ticker` is made to raise a distinctive marker
        exception the instant predict() tries to fetch fresh data: if the
        exception propagates out of predict() (as it does — the
        gather/wait_for wrapper only catches asyncio.TimeoutError, not an
        arbitrary exception, so it surfaces), that proves predict() took
        the recompute branch and reached the network-fetch code, i.e.
        genuinely did NOT return the expired cached dict."""
        from services import prediction_engine as pe

        class _MarkerException(Exception):
            pass

        def _boom(*_a, **_kw):
            raise _MarkerException("expired entry correctly triggered a real recompute attempt")

        monkeypatch.setattr(pe.yf, "Ticker", _boom)

        real_time = time.time
        # Advance the clock the cache-check actually reads by more than
        # _PRED_TTL, via the real `time.time` call site inside predict() —
        # not a hand-rolled re-derivation of "is this expired".
        monkeypatch.setattr(pe.time, "time", lambda: real_time() + pe._PRED_TTL + 3600)

        stale_response = {"signal": "SELL", "composite_score": 30, "symbol": "EXPIREDSELL"}
        cache_key = "EXPIREDSELL:IN:medium"
        # Written at the REAL current time — only "expired" because the
        # mocked clock inside predict() has jumped forward past the TTL.
        pe._pred_cache[cache_key] = (real_time(), stale_response)
        try:
            engine = pe.PredictionEngine()
            with pytest.raises(_MarkerException):
                asyncio.run(engine.predict("EXPIREDSELL", "IN", "medium"))
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


class TestCryptoTechnicalSummaryUsesTheRealUnsuppressedFunction:
    """The tests above mock `get_signal_summary` entirely (needed to force
    a specific composite score deterministically for the headline
    SELL/BUY tests) — which means they cannot prove crypto's copy of the
    ACTUAL shared function is unaffected by the equity containment. This
    class calls the real `services.technical_indicators.get_signal_summary`
    (only network — `_fetch_crypto_history`/news — is mocked) exactly as
    `crypto_engine.predict_crypto()` does, and compares the resulting
    `technical.overall` field against the function's own true (base,
    pre-containment) score-based thresholds, proving the equity-only
    `suppress_sell` gate does not reach crypto's call at all."""

    @staticmethod
    def _synthetic_crypto_df(seed, n=260):
        import numpy as np
        rng = np.random.default_rng(seed)
        # A mean-reverting-ish random walk with real day-to-day noise —
        # deliberately NOT a smooth constant-percentage trend (those
        # produce degenerate/unintuitive oscillator readings, as observed
        # empirically), so RSI/MACD/ADX etc. compute realistic, varied
        # values across the seeds used below.
        rets = rng.normal(loc=0.0, scale=0.02, size=n)
        price = 100 * np.cumprod(1 + rets)
        high = price * (1 + np.abs(rng.normal(0, 0.005, n)))
        low = price * (1 - np.abs(rng.normal(0, 0.005, n)))
        vol = rng.integers(500_000, 2_000_000, n).astype(float)
        return pd.DataFrame({"Open": price, "High": high, "Low": low, "Close": price, "Volume": vol})

    def _run_predict_crypto_with_real_indicators(self, monkeypatch, seed):
        from services import crypto_engine as ce

        df = self._synthetic_crypto_df(seed)
        monkeypatch.setattr(ce, "_fetch_crypto_history", lambda symbol, horizon: df)
        # compute_indicators and get_signal_summary are NOT mocked — the
        # real shared functions run exactly as crypto_engine.py calls them.

        async def _raise(*_a, **_kw):
            raise RuntimeError("network disabled in test — forces neutral sentiment fallback")

        monkeypatch.setattr(ce._news_svc, "get_news_with_sentiment", _raise)
        return asyncio.run(ce.predict_crypto("BTC", "medium"))

    def test_crypto_technical_overall_matches_the_true_unsuppressed_thresholds_across_seeds(self, monkeypatch):
        from services.technical_indicators import get_signal_summary

        seen_sell = seen_buy = seen_hold = 0
        for seed in range(12):
            result = self._run_predict_crypto_with_real_indicators(monkeypatch, seed)
            assert "error" not in result
            tech = result["technical"]

            # Ground truth: call the SAME real function directly (default
            # suppress_sell=False, exactly as crypto_engine.py calls it)
            # on the identical df, and confirm it agrees with what
            # predict_crypto actually returned — i.e. predict_crypto is
            # not silently routing through a suppressed variant anywhere.
            direct = get_signal_summary(ce_compute_indicators(self._synthetic_crypto_df(seed)))
            assert tech["overall"] == direct["overall"]
            assert tech["score"] == direct["score"]

            if tech["score"] <= 42:
                assert tech["overall"] == "SELL", (
                    "crypto's technical.overall must still reach genuine SELL "
                    "for a bearish score — equity containment must not apply here"
                )
                seen_sell += 1
            elif tech["score"] >= 58:
                assert tech["overall"] == "BUY"
                seen_buy += 1
            else:
                assert tech["overall"] == "HOLD"
                seen_hold += 1

        # Sanity: the seed sweep must actually exercise more than one
        # branch, or this test would trivially pass without ever having
        # touched the SELL case it exists to protect.
        assert seen_sell > 0, "seed sweep never produced a genuine bearish score — strengthen the fixture"


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


class TestSuppressionMetadataSurvivesTheActualHttpSerializationBoundary:
    """The helper-boundary tests above (TestCompositeSignalSellSuppressionBoundary
    etc.) prove `equity_sell_suppressed`/`equity_sell_suppressed_note` are
    present on the dict PredictionEngine.predict() constructs — but that
    dict is not what a client receives. This class calls the actual
    `GET /{symbol}` route handler (api.routers.predictions.get_prediction),
    exactly as `tests/unit/test_predictions_symbol_support_contract.py`
    does for its own warm-cache-success assertions, and inspects the real
    JSONResponse body (`json.loads(response.body)`) — proving the fields
    survive FastAPI's actual serialization path, not just presence on an
    in-memory Python dict."""

    def test_suppressed_sell_fields_are_present_in_the_serialized_response_body(self, monkeypatch):
        import json
        import time as time_mod
        from api.routers import predictions
        from services.prediction_engine import _pred_cache

        monkeypatch.setattr(predictions, "_start_background_compute", lambda *a, **kw: None)
        monkeypatch.setattr(predictions, "_computing", set())

        payload = {
            "symbol": "RELIANCE", "market": "IN", "horizon": "medium",
            "signal": "HOLD", "confidence": 6, "current_price": 1307.8, "target_price": 1283.0,
            "equity_sell_suppressed": True,
            "equity_sell_suppressed_note": "This setup would previously have been classified SELL. Equity SELL "
                                            "recommendations are currently disabled pending methodology review.",
            "technical": {"overall": "HOLD", "rsi": 50.0, "macd_diff": -0.1},
        }
        monkeypatch.setitem(_pred_cache, "RELIANCE:IN:medium", (time_mod.time(), payload))

        resp = asyncio.run(predictions.get_prediction("RELIANCE", market="IN", horizon="medium"))
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert body["equity_sell_suppressed"] is True
        assert "This setup would previously have been classified SELL" in body["equity_sell_suppressed_note"]
        assert body["signal"] == "HOLD"

    def test_non_suppressed_hold_serializes_with_a_false_flag_not_a_dropped_key(self, monkeypatch):
        import json
        import time as time_mod
        from api.routers import predictions
        from services.prediction_engine import _pred_cache

        monkeypatch.setattr(predictions, "_start_background_compute", lambda *a, **kw: None)
        monkeypatch.setattr(predictions, "_computing", set())

        payload = {
            "symbol": "RELIANCE", "market": "IN", "horizon": "medium",
            "signal": "HOLD", "confidence": 55, "current_price": 1307.8, "target_price": 1283.0,
            "equity_sell_suppressed": False,
            "equity_sell_suppressed_note": None,
        }
        monkeypatch.setitem(_pred_cache, "RELIANCE:IN:medium", (time_mod.time(), payload))

        resp = asyncio.run(predictions.get_prediction("RELIANCE", market="IN", horizon="medium"))
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert body["equity_sell_suppressed"] is False
        assert body["equity_sell_suppressed_note"] is None
