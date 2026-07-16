"""
Sprint 011 — lightweight signal-only endpoint (Performance-Scalability-UX-
Sprint-011 spec §20.1). Tests GET /api/predictions/{symbol}/signal's pure
helpers and route behavior with no HTTP server, no DB, and no network: the
route coroutines are invoked directly, the shared prediction cache is seeded
via monkeypatch, and the background-compute spawn is stubbed so no real
prediction (and no daemon thread) ever runs.

What must hold, per the sprint's cache-correctness rules:
  1. A warm cache hit returns the SAME signal/confidence the full
     /{symbol} route serves — the two routes can never disagree.
  2. The cache key carries every required dimension (symbol, market,
     horizon) — no cross-market or cross-horizon read is possible.
  3. A miss returns the same 202 {status, retry_after} contract as the
     full route and starts the one shared compute path (never a second,
     parallel calculation path).
  4. Error-cached entries degrade to signal/confidence nulls — the safe
     "no signal" badge state, never a crash or a fabricated signal.
  5. The payload stays bounded to exactly its six documented fields no
     matter how large the cached full payload is (`sector` was added
     deliberately for Portfolio's sector-wise allocation view, reused from
     this same cached prediction's quality_factors — no new computation).
"""
import asyncio
import json
import time

import pytest

from api.routers import predictions
from services.prediction_engine import _pred_cache, _PRED_TTL


def _full_payload(signal="BUY", confidence=72.5, **extra):
    """A realistic multi-engine-shaped cached prediction result — far more
    fields than the signal endpoint may return."""
    payload = {
        "symbol": "AAPL",
        "market": "US",
        "horizon": "medium",
        "signal": signal,
        "confidence": confidence,
        "current_price": 210.0,
        "target_price": 240.0,
        "stop_loss": 195.0,
        "reasoning": [{"indicator": "RSI", "signal": "BUY", "reason": "oversold bounce"}],
        "technical": {"overall": "BUY", "rsi": 55.0, "macd_diff": 0.4},
        "fundamental_score": {"score": 71, "reasons": ["margins"]},
        "sentiment_score": {"score": 0.3, "label": "positive", "bullish": 4, "bearish": 1},
        "factor_contributions": {"technical": 12.0, "fundamental": 8.0},
    }
    payload.update(extra)
    return payload


def _body(response):
    return json.loads(response.body)


@pytest.fixture
def no_background_compute(monkeypatch):
    """Stub the shared compute spawn — records calls, starts nothing."""
    calls = []
    monkeypatch.setattr(
        predictions, "_start_background_compute",
        lambda sym, market, horizon, key: calls.append(key),
    )
    # Isolate the computing-registry so tests can't leak state into each other.
    monkeypatch.setattr(predictions, "_computing", set())
    return calls


@pytest.fixture(autouse=True)
def rci_disabled(monkeypatch):
    """Deterministic full-route behavior: RCI composition off (its default)."""
    monkeypatch.delenv("RCI_LIVE_STOCK_ANALYSIS_ENABLED", raising=False)


@pytest.mark.unit
class TestSignalSummaryHelper:
    def test_values_come_straight_from_the_cached_full_payload(self):
        out = predictions._signal_summary(_full_payload(), "AAPL", "US", "medium")
        assert out["signal"] == "BUY"
        assert out["confidence"] == 72.5
        assert out["symbol"] == "AAPL"
        assert out["market"] == "US"
        assert out["horizon"] == "medium"

    def test_payload_is_bounded_to_exactly_six_fields(self):
        out = predictions._signal_summary(_full_payload(), "AAPL", "US", "medium")
        assert set(out) == {"symbol", "market", "horizon", "signal", "confidence", "sector"}

    def test_sector_reused_from_quality_factors_not_recomputed(self):
        out = predictions._signal_summary(
            _full_payload(quality_factors={"score": 70, "sector": "Technology"}),
            "AAPL", "US", "medium",
        )
        assert out["sector"] == "Technology"

    def test_sector_is_none_when_quality_factors_absent(self):
        out = predictions._signal_summary(_full_payload(), "AAPL", "US", "medium")
        assert out["sector"] is None

    def test_error_cached_entry_degrades_to_nulls(self):
        out = predictions._signal_summary(
            {"error": "Prediction data is temporarily unavailable."},
            "AAPL", "US", "medium",
        )
        assert out["signal"] is None
        assert out["confidence"] is None

    def test_rejected_signal_passes_through_unchanged(self):
        out = predictions._signal_summary(
            _full_payload(signal="REJECTED", confidence=0), "AAPL", "US", "medium")
        assert out["signal"] == "REJECTED"
        assert out["confidence"] == 0


@pytest.mark.unit
class TestFreshCachedPrediction:
    def test_fresh_entry_is_returned(self, monkeypatch):
        payload = _full_payload()
        monkeypatch.setitem(_pred_cache, "AAPL:US:medium", (time.time(), payload))
        assert predictions._fresh_cached_prediction("AAPL:US:medium") is payload

    def test_stale_entry_is_a_miss(self, monkeypatch):
        monkeypatch.setitem(
            _pred_cache, "AAPL:US:medium",
            (time.time() - _PRED_TTL - 1, _full_payload()),
        )
        assert predictions._fresh_cached_prediction("AAPL:US:medium") is None

    def test_absent_key_is_a_miss(self):
        assert predictions._fresh_cached_prediction("NOSUCH:US:short") is None


@pytest.mark.unit
class TestSignalRoute:
    def test_cache_hit_matches_full_route_business_output(self, monkeypatch, no_background_compute):
        """The load-bearing equivalence: warm hit on /signal returns the exact
        signal/confidence the full /{symbol} route returns for the same key."""
        monkeypatch.setitem(_pred_cache, "AAPL:US:medium", (time.time(), _full_payload()))

        full = asyncio.run(predictions.get_prediction("AAPL", market="US", horizon="medium"))
        light = asyncio.run(predictions.get_signal_summary("AAPL", market="US", horizon="medium"))

        assert full.status_code == 200
        assert light.status_code == 200
        full_body, light_body = _body(full), _body(light)
        assert light_body["signal"] == full_body["signal"]
        assert light_body["confidence"] == full_body["confidence"]
        # No compute started on a warm hit.
        assert no_background_compute == []

    def test_cache_hit_payload_is_minimal(self, monkeypatch, no_background_compute):
        monkeypatch.setitem(_pred_cache, "AAPL:US:medium", (time.time(), _full_payload()))
        light = asyncio.run(predictions.get_signal_summary("AAPL", market="US", horizon="medium"))
        body = _body(light)
        assert set(body) == {"symbol", "market", "horizon", "signal", "confidence", "sector"}
        # None of the heavy engine internals may leak into the summary.
        assert "factor_contributions" not in body
        assert "reasoning" not in body

    def test_market_separation_us_entry_never_serves_in_request(self, monkeypatch, no_background_compute):
        monkeypatch.setitem(_pred_cache, "TCS:US:medium", (time.time(), _full_payload(signal="SELL")))
        resp = asyncio.run(predictions.get_signal_summary("TCS", market="IN", horizon="medium"))
        assert resp.status_code == 202
        assert no_background_compute == ["TCS:IN:medium"]

    def test_horizon_separation_medium_entry_never_serves_short_request(self, monkeypatch, no_background_compute):
        monkeypatch.setitem(_pred_cache, "AAPL:US:medium", (time.time(), _full_payload()))
        resp = asyncio.run(predictions.get_signal_summary("AAPL", market="US", horizon="short"))
        assert resp.status_code == 202
        assert no_background_compute == ["AAPL:US:short"]

    def test_cache_miss_returns_202_and_starts_the_one_shared_compute(self, no_background_compute):
        resp = asyncio.run(predictions.get_signal_summary("MSFT", market="US", horizon="long"))
        assert resp.status_code == 202
        assert _body(resp) == {"status": "computing", "retry_after": 5}
        assert no_background_compute == ["MSFT:US:long"]

    def test_already_computing_does_not_spawn_a_second_compute(self, monkeypatch, no_background_compute):
        monkeypatch.setattr(predictions, "_computing", {"MSFT:US:long"})
        resp = asyncio.run(predictions.get_signal_summary("MSFT", market="US", horizon="long"))
        assert resp.status_code == 202
        assert no_background_compute == []

    def test_symbol_is_uppercased_into_the_cache_key(self, monkeypatch, no_background_compute):
        monkeypatch.setitem(_pred_cache, "INFY:IN:medium", (time.time(), _full_payload(signal="HOLD", confidence=51.0)))
        resp = asyncio.run(predictions.get_signal_summary("infy", market="IN", horizon="medium"))
        assert resp.status_code == 200
        body = _body(resp)
        assert body["symbol"] == "INFY"
        assert body["signal"] == "HOLD"

    def test_error_cached_entry_is_translated_to_503_not_served_as_200(self, monkeypatch, no_background_compute):
        """Production hotfix: a cached {"error": ...} result must never be
        served as a plain HTTP 200 — that's indistinguishable at the
        transport level from a genuine successful prediction, which let the
        frontend's polling logic accept it as one and fabricate a
        BUY/HOLD/SELL signal from the missing fields (the NDSL incident).
        This supersedes the old "safe nulls at 200" contract."""
        monkeypatch.setitem(
            _pred_cache, "AAPL:US:medium",
            (time.time(), {"error": "Prediction data is temporarily unavailable.",
                           "code": "DATA_PROVIDER_UNAVAILABLE"}),
        )
        resp = asyncio.run(predictions.get_signal_summary("AAPL", market="US", horizon="medium"))
        assert resp.status_code == 503
        body = _body(resp)
        assert body["error"]["code"] == "DATA_PROVIDER_UNAVAILABLE"
        assert body["error"]["symbol"] == "AAPL"
        assert body["error"]["market"] == "US"


@pytest.mark.unit
class TestCachedSignalsBatchRoute:
    """GET /api/predictions/signals/cached-batch (Portfolio hotfix, Session
    12) — the whole point is that this NEVER starts a compute, unlike
    /{symbol}/signal above. Every test asserts `no_background_compute`
    stays empty, not just that the response looks right."""

    def test_warm_entries_report_cached_true_with_the_same_values_as_signal_route(self, monkeypatch, no_background_compute):
        monkeypatch.setitem(_pred_cache, "TCS:IN:medium", (time.time(), _full_payload(signal="BUY", confidence=80.0)))
        resp = asyncio.run(predictions.get_cached_signals_batch(symbols="TCS", market="IN", horizon="medium"))
        assert resp == {"signals": {"TCS": {"signal": "BUY", "confidence": 80.0, "cached": True}}}
        assert no_background_compute == []

    def test_cold_symbol_reports_cached_false_with_null_signal_and_never_starts_a_compute(self, no_background_compute):
        resp = asyncio.run(predictions.get_cached_signals_batch(symbols="NEVERSEEN", market="IN", horizon="medium"))
        assert resp == {"signals": {"NEVERSEEN": {"signal": None, "confidence": None, "cached": False}}}
        assert no_background_compute == []

    def test_batch_of_mixed_warm_and_cold_symbols_in_one_call(self, monkeypatch, no_background_compute):
        monkeypatch.setitem(_pred_cache, "TCS:IN:medium", (time.time(), _full_payload(signal="BUY", confidence=65.0)))
        resp = asyncio.run(predictions.get_cached_signals_batch(symbols="TCS,INFY,WIPRO", market="IN", horizon="medium"))
        signals = resp["signals"]
        assert signals["TCS"] == {"signal": "BUY", "confidence": 65.0, "cached": True}
        assert signals["INFY"] == {"signal": None, "confidence": None, "cached": False}
        assert signals["WIPRO"] == {"signal": None, "confidence": None, "cached": False}
        assert no_background_compute == []

    def test_stale_entry_reports_cached_false_not_a_stale_hit(self, monkeypatch, no_background_compute):
        monkeypatch.setitem(_pred_cache, "TCS:IN:medium", (time.time() - _PRED_TTL - 1, _full_payload()))
        resp = asyncio.run(predictions.get_cached_signals_batch(symbols="TCS", market="IN", horizon="medium"))
        assert resp["signals"]["TCS"]["cached"] is False
        assert no_background_compute == []

    def test_market_specific_a_us_entry_never_serves_an_in_request_for_the_same_symbol(self, monkeypatch, no_background_compute):
        monkeypatch.setitem(_pred_cache, "TCS:US:medium", (time.time(), _full_payload(signal="SELL")))
        resp = asyncio.run(predictions.get_cached_signals_batch(symbols="TCS", market="IN", horizon="medium"))
        assert resp["signals"]["TCS"] == {"signal": None, "confidence": None, "cached": False}
        assert no_background_compute == []

    def test_horizon_specific_a_medium_entry_never_serves_a_short_request(self, monkeypatch, no_background_compute):
        monkeypatch.setitem(_pred_cache, "TCS:IN:medium", (time.time(), _full_payload()))
        resp = asyncio.run(predictions.get_cached_signals_batch(symbols="TCS", market="IN", horizon="short"))
        assert resp["signals"]["TCS"]["cached"] is False
        assert no_background_compute == []

    def test_symbols_are_uppercased_into_the_cache_key_and_the_response_key(self, monkeypatch, no_background_compute):
        monkeypatch.setitem(_pred_cache, "INFY:IN:medium", (time.time(), _full_payload(signal="HOLD", confidence=51.0)))
        resp = asyncio.run(predictions.get_cached_signals_batch(symbols="infy", market="IN", horizon="medium"))
        assert resp["signals"]["INFY"] == {"signal": "HOLD", "confidence": 51.0, "cached": True}

    def test_empty_symbols_short_circuits_without_touching_the_cache(self, no_background_compute):
        resp = asyncio.run(predictions.get_cached_signals_batch(symbols="", market="IN", horizon="medium"))
        assert resp == {"signals": {}}
        assert no_background_compute == []

    def test_error_cached_entry_reports_cached_true_with_safe_nulls(self, monkeypatch, no_background_compute):
        """Matches /{symbol}/signal's own contract: an error-cached entry is
        still a genuine cache hit (no compute needed), just with null
        signal/confidence — not the same thing as "never resolved"."""
        monkeypatch.setitem(
            _pred_cache, "TCS:IN:medium",
            (time.time(), {"error": "Prediction data is temporarily unavailable."}),
        )
        resp = asyncio.run(predictions.get_cached_signals_batch(symbols="TCS", market="IN", horizon="medium"))
        assert resp["signals"]["TCS"] == {"signal": None, "confidence": None, "cached": True}
        assert no_background_compute == []

    def test_cold_symbol_with_no_postgres_configured_stays_not_cached(self, monkeypatch, no_background_compute):
        """USE_POSTGRES unset (or not '1') — the score_snapshots fallback
        must not even attempt a DB call, same behavior as before Session 13."""
        monkeypatch.delenv("USE_POSTGRES", raising=False)
        resp = asyncio.run(predictions.get_cached_signals_batch(symbols="NEVERSEEN", market="IN", horizon="medium"))
        assert resp["signals"]["NEVERSEEN"] == {"signal": None, "confidence": None, "cached": False}
        assert no_background_compute == []

    def test_never_invokes_prediction_engine_predict_even_with_every_symbol_cold(self, monkeypatch):
        """Direct proof, not just an inference from `_start_background_compute`
        never firing: patches PredictionEngine.predict to raise, so this
        test fails loudly if any code path in this route ever reaches it."""
        from services.prediction_engine import PredictionEngine
        monkeypatch.setattr(
            PredictionEngine, "predict",
            lambda self, *a, **kw: (_ for _ in ()).throw(AssertionError("cached-batch must never call PredictionEngine.predict")),
        )
        resp = asyncio.run(predictions.get_cached_signals_batch(symbols="TCS,INFY,WIPRO", market="IN", horizon="medium"))
        assert all(v["cached"] is False for v in resp["signals"].values())


@pytest.mark.unit
class TestCachedSignalsBatchScoreSnapshotsFallback:
    """Session 13 root-cause fix: _pred_cache's 15-minute TTL meant a
    symbol Daily Picks deep-scored last night showed "Not cached" again
    within 15 minutes of that run — for nearly every holding, nearly all
    day. These tests cover the score_snapshots (Postgres, no expiry)
    fallback that now runs for whatever's left uncached in _pred_cache."""

    def test_falls_back_to_score_snapshots_when_pred_cache_is_cold(self, monkeypatch, no_background_compute):
        monkeypatch.setenv("USE_POSTGRES", "1")
        monkeypatch.setattr(
            "services.postgres_store.get_latest_signals_batch",
            lambda symbols, market, horizon: {"TCS": {"signal": "BUY", "confidence": 72.0, "snapshot_date": "2026-07-10"}},
        )
        resp = asyncio.run(predictions.get_cached_signals_batch(symbols="TCS", market="IN", horizon="medium"))
        assert resp["signals"]["TCS"] == {"signal": "BUY", "confidence": 72.0, "cached": True}
        assert no_background_compute == []

    def test_pred_cache_hit_takes_priority_over_score_snapshots(self, monkeypatch, no_background_compute):
        """A fresh live prediction is always at least as current as last
        night's snapshot — _pred_cache must win, and score_snapshots must
        not even be consulted for a symbol already resolved from _pred_cache."""
        monkeypatch.setenv("USE_POSTGRES", "1")
        monkeypatch.setitem(_pred_cache, "TCS:IN:medium", (time.time(), _full_payload(signal="SELL", confidence=90.0)))

        def _fail_if_called(symbols, market, horizon):
            raise AssertionError("score_snapshots must not be queried for an already-fresh symbol")
        monkeypatch.setattr("services.postgres_store.get_latest_signals_batch", _fail_if_called)

        resp = asyncio.run(predictions.get_cached_signals_batch(symbols="TCS", market="IN", horizon="medium"))
        assert resp["signals"]["TCS"] == {"signal": "SELL", "confidence": 90.0, "cached": True}

    def test_symbol_with_no_snapshot_either_stays_not_cached(self, monkeypatch, no_background_compute):
        monkeypatch.setenv("USE_POSTGRES", "1")
        monkeypatch.setattr("services.postgres_store.get_latest_signals_batch", lambda symbols, horizon: {})
        resp = asyncio.run(predictions.get_cached_signals_batch(symbols="OBSCURESTOCK", market="IN", horizon="medium"))
        assert resp["signals"]["OBSCURESTOCK"] == {"signal": None, "confidence": None, "cached": False}
        assert no_background_compute == []

    def test_mixed_batch_some_from_pred_cache_some_from_snapshots_some_uncached(self, monkeypatch, no_background_compute):
        monkeypatch.setenv("USE_POSTGRES", "1")
        monkeypatch.setitem(_pred_cache, "TCS:IN:medium", (time.time(), _full_payload(signal="BUY", confidence=88.0)))
        monkeypatch.setattr(
            "services.postgres_store.get_latest_signals_batch",
            lambda symbols, market, horizon: {"INFY": {"signal": "HOLD", "confidence": 55.0, "snapshot_date": "2026-07-09"}},
        )
        resp = asyncio.run(predictions.get_cached_signals_batch(symbols="TCS,INFY,WIPRO", market="IN", horizon="medium"))
        signals = resp["signals"]
        assert signals["TCS"] == {"signal": "BUY", "confidence": 88.0, "cached": True}   # from _pred_cache
        assert signals["INFY"] == {"signal": "HOLD", "confidence": 55.0, "cached": True}  # from score_snapshots
        assert signals["WIPRO"] == {"signal": None, "confidence": None, "cached": False}  # neither has it
        assert no_background_compute == []

    def test_score_snapshots_lookup_failure_degrades_to_not_cached_not_a_500(self, monkeypatch, no_background_compute):
        monkeypatch.setenv("USE_POSTGRES", "1")

        def _raise(symbols, market, horizon):
            raise RuntimeError("connection refused")
        monkeypatch.setattr("services.postgres_store.get_latest_signals_batch", _raise)

        resp = asyncio.run(predictions.get_cached_signals_batch(symbols="TCS", market="IN", horizon="medium"))
        assert resp["signals"]["TCS"] == {"signal": None, "confidence": None, "cached": False}
        assert no_background_compute == []

    def test_never_invokes_prediction_engine_predict_via_the_snapshots_fallback_either(self, monkeypatch):
        monkeypatch.setenv("USE_POSTGRES", "1")
        monkeypatch.setattr("services.postgres_store.get_latest_signals_batch", lambda symbols, horizon: {})
        from services.prediction_engine import PredictionEngine
        monkeypatch.setattr(
            PredictionEngine, "predict",
            lambda self, *a, **kw: (_ for _ in ()).throw(AssertionError("must never call PredictionEngine.predict")),
        )
        resp = asyncio.run(predictions.get_cached_signals_batch(symbols="TCS", market="IN", horizon="medium"))
        assert resp["signals"]["TCS"]["cached"] is False
