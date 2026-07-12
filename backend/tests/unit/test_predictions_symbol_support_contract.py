"""
Production truthfulness hotfix — NDSL incident.

Root cause: an unsupported/unknown symbol (e.g. "NDSL" — either a typo, or
a reference to the real depository "NSDL," which listed on the BSE on
6 August 2025 and is not an NSE-listed equity; either way it is genuinely
outside stock_universe.py's NSE-sourced IN_STOCKS, an exchange-coverage
gap rather than a stale-universe-refresh problem) was
never validated before a prediction was started. yfinance returned an
empty price-history DataFrame (indistinguishable from a genuine Yahoo
rate-limit/outage), prediction_engine.py cached a generic
`{"error": "..."}` dict, and predictions.py served that dict as a plain
HTTP 200 — indistinguishable at the transport level from a real
successful prediction. The frontend's polling logic then accepted it as
one, and Stock Detail's `signal === "BUY" ? ... : signal === "SELL" ?
... : "— HOLD"` ternary defaulted the missing `signal` field to a
fabricated "HOLD," with a blank confidence bar and an unrestricted Paper
Trade button.

This file locks in the corrected contract:
  1. An unsupported symbol is rejected with a structured 404
     (SYMBOL_NOT_SUPPORTED) BEFORE any cache/compute logic runs — it can
     never produce a cached error entry, and never starts a background
     compute.
  2. A cached error result for a *valid* symbol (provider outage,
     insufficient history, timeout) is translated to HTTP 503
     (DATA_PROVIDER_UNAVAILABLE) — never served as a plain 200.
  3. A valid symbol's existing 202 (computing) -> poll -> 200 (success)
     contract is completely unchanged.
Both /{symbol} and /{symbol}/signal are covered identically — the two
routes must never disagree about a symbol's support status or error
translation, exactly like their existing cache-value equivalence rule.
"""
import asyncio
import json
import time

import pytest

from api.routers import predictions
from services.prediction_engine import _pred_cache, _PRED_TTL
from services.stock_universe import is_known_symbol, IN_SYMBOLS, US_SYMBOLS


def _body(response):
    return json.loads(response.body)


def _full_payload(signal="BUY", confidence=72.5, current_price=210.0, **extra):
    payload = {
        "symbol": "RELIANCE", "market": "IN", "horizon": "medium",
        "signal": signal, "confidence": confidence, "current_price": current_price,
        "target_price": 240.0,
        "reasoning": [{"indicator": "RSI", "signal": "BUY", "reason": "oversold bounce"}],
        "technical": {"overall": "BUY", "rsi": 55.0, "macd_diff": 0.4},
        "fundamental_score": {"score": 71, "reasons": ["margins"]},
        "sentiment_score": {"score": 0.3, "label": "positive", "bullish": 4, "bearish": 1},
    }
    payload.update(extra)
    return payload


@pytest.fixture
def no_background_compute(monkeypatch):
    calls = []
    monkeypatch.setattr(
        predictions, "_start_background_compute",
        lambda sym, market, horizon, key: calls.append(key),
    )
    monkeypatch.setattr(predictions, "_computing", set())
    return calls


@pytest.fixture(autouse=True)
def rci_disabled(monkeypatch):
    monkeypatch.delenv("RCI_LIVE_STOCK_ANALYSIS_ENABLED", raising=False)


@pytest.mark.unit
class TestIsKnownSymbol:
    def test_reliance_is_known_in_the_indian_universe(self):
        assert is_known_symbol("RELIANCE", "IN") is True

    def test_ndsl_is_not_known_in_the_indian_universe(self):
        """The exact symbol from the production incident — confirms it is
        genuinely absent, not a stale assumption."""
        assert "NDSL" not in IN_SYMBOLS
        assert is_known_symbol("NDSL", "IN") is False

    def test_case_insensitive(self):
        assert is_known_symbol("reliance", "IN") is True

    def test_us_universe_checked_independently(self):
        assert is_known_symbol("AAPL", "US") is True
        assert "NDSL" not in US_SYMBOLS
        assert is_known_symbol("NDSL", "US") is False

    def test_markets_without_a_static_universe_are_not_gated(self):
        """CRYPTO (and anything else) has no allow-list here — callers must
        only use this for markets that actually have one (IN/US)."""
        assert is_known_symbol("SOMEUNKNOWNCOIN", "CRYPTO") is True


@pytest.mark.unit
class TestUnsupportedSymbolRejectedBeforeAnyCompute:
    def test_get_prediction_returns_structured_404_for_ndsl(self, no_background_compute):
        resp = asyncio.run(predictions.get_prediction("NDSL", market="IN", horizon="short"))
        assert resp.status_code == 404
        body = _body(resp)
        assert body["error"]["code"] == "SYMBOL_NOT_SUPPORTED"
        assert body["error"]["symbol"] == "NDSL"
        assert body["error"]["market"] == "IN"
        # The whole point: an unsupported symbol must never start a background compute.
        assert no_background_compute == []

    def test_get_signal_summary_returns_structured_404_for_ndsl(self, no_background_compute):
        resp = asyncio.run(predictions.get_signal_summary("NDSL", market="IN", horizon="short"))
        assert resp.status_code == 404
        body = _body(resp)
        assert body["error"]["code"] == "SYMBOL_NOT_SUPPORTED"
        assert no_background_compute == []

    def test_unsupported_symbol_never_reaches_the_cache_even_if_pre_seeded(self, monkeypatch, no_background_compute):
        """Even a maliciously/accidentally pre-seeded cache entry for an
        unsupported symbol must not leak through — the validation check
        runs unconditionally before any cache read."""
        monkeypatch.setitem(_pred_cache, "NDSL:IN:short", (time.time(), _full_payload(symbol="NDSL")))
        resp = asyncio.run(predictions.get_prediction("NDSL", market="IN", horizon="short"))
        assert resp.status_code == 404
        assert _body(resp)["error"]["code"] == "SYMBOL_NOT_SUPPORTED"

    def test_symbol_uppercased_before_the_universe_check(self, no_background_compute):
        resp = asyncio.run(predictions.get_prediction("ndsl", market="IN", horizon="short"))
        assert resp.status_code == 404
        assert _body(resp)["error"]["symbol"] == "NDSL"

    def test_crypto_market_is_not_gated_by_the_static_universe_check(self, monkeypatch):
        """CRYPTO goes through predict_crypto entirely, before is_known_symbol
        would even be consulted — confirms this hotfix didn't accidentally
        block crypto symbols outside IN_STOCKS/US_STOCKS."""
        async def _fake_predict_crypto(sym, horizon):
            return {"symbol": sym, "signal": "HOLD", "confidence": 50.0}
        monkeypatch.setattr(predictions, "predict_crypto", _fake_predict_crypto)
        resp = asyncio.run(predictions.get_prediction("SOMEOBSCURECOIN", market="CRYPTO", horizon="short"))
        assert resp.status_code == 200


@pytest.mark.unit
class TestCachedErrorTranslatedToNonTwoHundred:
    def test_get_prediction_translates_cached_error_to_503(self, monkeypatch, no_background_compute):
        monkeypatch.setitem(
            _pred_cache, "RELIANCE:IN:short",
            (time.time(), {"error": "No price data returned — Yahoo Finance may be rate-limiting. Try again in a moment.",
                           "code": "DATA_PROVIDER_UNAVAILABLE"}),
        )
        resp = asyncio.run(predictions.get_prediction("RELIANCE", market="IN", horizon="short"))
        assert resp.status_code == 503
        body = _body(resp)
        assert body["error"]["code"] == "DATA_PROVIDER_UNAVAILABLE"
        assert body["error"]["symbol"] == "RELIANCE"
        assert body["error"]["market"] == "IN"
        # No compute re-triggered on a warm (even if erroring) cache entry.
        assert no_background_compute == []

    def test_get_signal_summary_translates_cached_error_to_503(self, monkeypatch, no_background_compute):
        monkeypatch.setitem(
            _pred_cache, "RELIANCE:IN:short",
            (time.time(), {"error": "Insufficient price history (need at least 20 days)",
                           "code": "DATA_PROVIDER_UNAVAILABLE"}),
        )
        resp = asyncio.run(predictions.get_signal_summary("RELIANCE", market="IN", horizon="short"))
        assert resp.status_code == 503
        assert _body(resp)["error"]["code"] == "DATA_PROVIDER_UNAVAILABLE"

    def test_cached_error_without_a_code_defaults_to_data_provider_unavailable(self, monkeypatch, no_background_compute):
        """Defensive: an older-shaped cache entry (no `code` key, e.g. from
        before this hotfix, or the bg_thread's own generic exception
        fallback) must still translate to a real error status, not 200."""
        monkeypatch.setitem(
            _pred_cache, "RELIANCE:IN:short",
            (time.time(), {"error": "Prediction data is temporarily unavailable. Please try again."}),
        )
        resp = asyncio.run(predictions.get_prediction("RELIANCE", market="IN", horizon="short"))
        assert resp.status_code == 503
        assert _body(resp)["error"]["code"] == "DATA_PROVIDER_UNAVAILABLE"

    def test_a_valid_supported_symbol_never_gets_symbol_not_supported_for_a_data_error(self, monkeypatch, no_background_compute):
        """A provider outage for a real, supported stock must render as
        DATA_PROVIDER_UNAVAILABLE (temporary), never SYMBOL_NOT_SUPPORTED —
        the two are different states with different user-facing meaning."""
        monkeypatch.setitem(
            _pred_cache, "RELIANCE:IN:short",
            (time.time(), {"error": "Data fetch timed out — Yahoo Finance took too long. Try again in a moment.",
                           "code": "DATA_PROVIDER_UNAVAILABLE"}),
        )
        resp = asyncio.run(predictions.get_prediction("RELIANCE", market="IN", horizon="short"))
        assert resp.status_code == 503
        assert _body(resp)["error"]["code"] != "SYMBOL_NOT_SUPPORTED"


@pytest.mark.unit
class TestExistingValidSymbolContractUnchanged:
    """The 202 (computing) -> poll -> 200 (success) contract for a real,
    supported symbol must be completely unaffected by this hotfix."""

    def test_cold_valid_symbol_returns_202_and_starts_the_shared_compute(self, no_background_compute):
        resp = asyncio.run(predictions.get_prediction("RELIANCE", market="IN", horizon="short"))
        assert resp.status_code == 202
        assert _body(resp) == {"status": "computing", "retry_after": 5}
        assert no_background_compute == ["RELIANCE:IN:short"]

    def test_already_computing_valid_symbol_returns_202_without_a_second_compute(self, monkeypatch, no_background_compute):
        monkeypatch.setattr(predictions, "_computing", {"RELIANCE:IN:short"})
        resp = asyncio.run(predictions.get_prediction("RELIANCE", market="IN", horizon="short"))
        assert resp.status_code == 202
        assert no_background_compute == []

    def test_warm_valid_success_entry_still_returns_200_with_the_full_payload(self, monkeypatch, no_background_compute):
        monkeypatch.setitem(_pred_cache, "RELIANCE:IN:short", (time.time(), _full_payload()))
        resp = asyncio.run(predictions.get_prediction("RELIANCE", market="IN", horizon="short"))
        assert resp.status_code == 200
        body = _body(resp)
        assert body["signal"] == "BUY"
        assert body["confidence"] == 72.5
        assert body["current_price"] == 210.0

    def test_warm_valid_success_entry_signal_route_still_returns_200(self, monkeypatch, no_background_compute):
        monkeypatch.setitem(_pred_cache, "RELIANCE:IN:short", (time.time(), _full_payload()))
        resp = asyncio.run(predictions.get_signal_summary("RELIANCE", market="IN", horizon="short"))
        assert resp.status_code == 200
        body = _body(resp)
        assert body["signal"] == "BUY"
        assert body["confidence"] == 72.5

    def test_us_valid_symbol_also_passes_the_universe_check(self, no_background_compute):
        resp = asyncio.run(predictions.get_prediction("AAPL", market="US", horizon="short"))
        assert resp.status_code == 202
        assert no_background_compute == ["AAPL:US:short"]
