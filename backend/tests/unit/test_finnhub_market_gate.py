"""
Finnhub IN market gate.

Railway production logs showed bursts of NSE:* quote requests hitting
Finnhub's free-tier 60 req/min limit and getting 429'd (e.g. NSE:UNICHEMLAB,
NSE:GMDCLTD, NSE:ATLANTAA, NSE:JSLL, NSE:RPOWER). Root cause: the price-alert
(services/price_alert_notifier.py, every 90s) and paper-trade-notifier
(services/trade_notifier.py, every 15min) polling loops call
MarketDataService.get_quote() once per tracked symbol/row with no market
gate, so any IN symbol whose NSE lookup failed (or lagged) fell straight
through to Finnhub — for however many alerts/trades happened to be active.

Fix: Finnhub is now gated for market="IN" behind ENABLE_FINNHUB_FOR_IN
(default off/unset) in services/finnhub_client.is_enabled_for_market().
NSE (services/nse_client.py) remains IN's primary/only allowed quote
provider unless the flag opts back in explicitly. market="US" is
unconditional and unaffected — Finnhub was and remains its primary/fallback
provider there.

These tests run with no network, no DB (matches this repo's existing
no-network unit-test convention, see test_predictions_signal_endpoint.py):
yfinance, NSE, and Finnhub calls are stubbed via monkeypatch.
"""
import asyncio
import inspect

import pytest

from services import finnhub_client, market_data, screener_service


@pytest.fixture(autouse=True)
def _clear_finnhub_flag(monkeypatch):
    monkeypatch.delenv("ENABLE_FINNHUB_FOR_IN", raising=False)


@pytest.mark.unit
class TestIsEnabledForMarket:
    def test_in_defaults_to_disabled(self):
        assert finnhub_client.is_enabled_for_market("IN") is False

    def test_us_defaults_to_enabled(self):
        assert finnhub_client.is_enabled_for_market("US") is True

    def test_in_can_be_explicitly_enabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_FINNHUB_FOR_IN", "true")
        assert finnhub_client.is_enabled_for_market("IN") is True

    def test_in_stays_disabled_for_explicit_false(self, monkeypatch):
        monkeypatch.setenv("ENABLE_FINNHUB_FOR_IN", "false")
        assert finnhub_client.is_enabled_for_market("IN") is False

    def test_us_unaffected_by_the_in_flag(self, monkeypatch):
        monkeypatch.setenv("ENABLE_FINNHUB_FOR_IN", "true")
        assert finnhub_client.is_enabled_for_market("US") is True


def _finnhub_quote_spy(monkeypatch, target=market_data):
    calls = []

    def _fake(symbol, market):
        calls.append((symbol, market))
        return {
            "symbol": symbol, "market": market, "price": 100.0, "prev_close": 99.0,
            "change": 1.0, "change_pct": 1.01, "quote_source": "finnhub",
        }

    monkeypatch.setattr(target.finnhub_client, "get_quote", _fake)
    return calls


@pytest.mark.unit
class TestMarketDataServiceQuoteGate:
    def _stub_nse_fail(self, monkeypatch):
        monkeypatch.setattr(market_data.nse_client, "get_quote", lambda symbol: None)

    def _stub_fast_info_fail(self, monkeypatch):
        # Requirement 1/2 don't need a real signal — force every path after
        # the gate check to fail cleanly so the test proves absence, not luck.
        monkeypatch.setattr(
            market_data.MarketDataService, "_fetch_fast_info",
            lambda self, symbol, market: None,
        )

    def test_in_quote_does_not_call_finnhub_when_flag_unset(self, monkeypatch):
        self._stub_nse_fail(monkeypatch)
        calls = _finnhub_quote_spy(monkeypatch)
        self._stub_fast_info_fail(monkeypatch)
        svc = market_data.MarketDataService()
        result = asyncio.run(svc.get_quote("UNICHEMLAB", "IN"))
        assert calls == []
        assert result is None

    def test_in_quote_does_not_call_finnhub_when_flag_explicitly_false(self, monkeypatch):
        monkeypatch.setenv("ENABLE_FINNHUB_FOR_IN", "false")
        self._stub_nse_fail(monkeypatch)
        calls = _finnhub_quote_spy(monkeypatch)
        self._stub_fast_info_fail(monkeypatch)
        svc = market_data.MarketDataService()
        asyncio.run(svc.get_quote("GMDCLTD", "IN"))
        assert calls == []

    def test_in_quote_calls_finnhub_only_when_flag_enabled_and_nse_failed(self, monkeypatch):
        monkeypatch.setenv("ENABLE_FINNHUB_FOR_IN", "true")
        self._stub_nse_fail(monkeypatch)
        calls = _finnhub_quote_spy(monkeypatch)
        svc = market_data.MarketDataService()
        result = asyncio.run(svc.get_quote("RPOWER", "IN"))
        assert calls == [("RPOWER", "IN")]
        assert result["quote_source"] == "finnhub"

    def test_in_quote_prefers_nse_over_finnhub_even_when_flag_enabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_FINNHUB_FOR_IN", "true")
        monkeypatch.setattr(
            market_data.nse_client, "get_quote",
            lambda symbol: {
                "symbol": symbol, "market": "IN", "price": 500.0, "prev_close": 490.0,
                "change": 10.0, "change_pct": 2.04, "quote_source": "nse_official",
            },
        )
        calls = _finnhub_quote_spy(monkeypatch)
        svc = market_data.MarketDataService()
        result = asyncio.run(svc.get_quote("JSLL", "IN"))
        assert calls == []
        assert result["quote_source"] == "nse_official"

    def test_us_quote_still_calls_finnhub_unconditionally(self, monkeypatch):
        calls = _finnhub_quote_spy(monkeypatch)
        svc = market_data.MarketDataService()
        result = asyncio.run(svc.get_quote("AAPL", "US"))
        assert calls == [("AAPL", "US")]
        assert result["quote_source"] == "finnhub"


@pytest.mark.unit
class TestPredictionAndDailyPicksNeverReferenceFinnhub:
    """Structural proof (no live network, matches this repo's convention):
    the /predictions/{symbol}/signal and /predictions/{symbol} compute path
    (PredictionEngine.predict, including the cache warm-up job in
    api/main.py which calls the same _bg_thread/predict path) and the Daily
    Picks generation path never import or call finnhub_client for any
    market, so no runtime code path there can reach Finnhub regardless of
    ENABLE_FINNHUB_FOR_IN."""

    def test_prediction_engine_module_never_references_finnhub(self):
        import services.prediction_engine as pe
        assert "finnhub" not in inspect.getsource(pe).lower()

    def test_daily_picks_module_never_references_finnhub(self):
        import services.daily_picks as dp
        assert "finnhub" not in inspect.getsource(dp).lower()

    def test_predictions_router_never_references_finnhub(self):
        from api.routers import predictions
        assert "finnhub" not in inspect.getsource(predictions).lower()


@pytest.mark.unit
class TestScreenerMoversGate:
    @pytest.fixture(autouse=True)
    def _clear_movers_cache(self):
        for m in ("IN", "US"):
            screener_service._movers_cache.pop(m, None)
            screener_service._last_good_movers.pop(m, None)
        yield
        for m in ("IN", "US"):
            screener_service._movers_cache.pop(m, None)
            screener_service._last_good_movers.pop(m, None)

    def test_in_movers_skips_finnhub_when_flag_unset(self, monkeypatch):
        monkeypatch.setattr(screener_service.nse_client, "get_gainers_losers", lambda idx: ([], []))
        monkeypatch.setattr(screener_service, "_is_market_open", lambda market: False)
        monkeypatch.setattr(
            screener_service, "_closed_gainers_losers",
            lambda universe: ([{"symbol": "RELIANCE", "price": 2500.0, "change_pct": 1.5, "name": "Reliance"}], []),
        )
        calls = []
        monkeypatch.setattr(
            screener_service, "_finnhub_gainers_losers",
            lambda symbols, market: (calls.append(market) or ([], [])),
        )
        svc = screener_service.ScreenerService()
        result = asyncio.run(svc.get_top_movers("IN"))
        assert calls == []
        assert result["gainers"]

    def test_us_movers_still_uses_finnhub_when_no_gainers(self, monkeypatch):
        monkeypatch.setattr(screener_service, "_is_market_open", lambda market: False)
        monkeypatch.setattr(screener_service.finnhub_client, "FINNHUB_KEY", "test-key")
        calls = []
        monkeypatch.setattr(
            screener_service, "_finnhub_gainers_losers",
            lambda symbols, market: (calls.append(market) or ([{"symbol": "AAPL", "price": 200.0, "change_pct": 1.0}], [])),
        )
        svc = screener_service.ScreenerService()
        result = asyncio.run(svc.get_top_movers("US"))
        assert calls == ["US"]
        assert result["gainers"]
