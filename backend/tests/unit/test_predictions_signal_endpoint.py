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

    def test_error_cached_entry_serves_safe_nulls_not_an_error(self, monkeypatch, no_background_compute):
        monkeypatch.setitem(
            _pred_cache, "AAPL:US:medium",
            (time.time(), {"error": "Prediction data is temporarily unavailable."}),
        )
        resp = asyncio.run(predictions.get_signal_summary("AAPL", market="US", horizon="medium"))
        assert resp.status_code == 200
        body = _body(resp)
        assert body["signal"] is None
        assert body["confidence"] is None
