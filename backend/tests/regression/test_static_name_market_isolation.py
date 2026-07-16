"""
Static company-name cache: market isolation for colliding tickers.

Production bug (2026-07-17): the stock detail page for NSE symbol "CUB"
(City Union Bank Limited) showed the company name "Lionheart Holdings -
Class A Ordinary Shares" — a different, US-listed entity that happens to
share the same bare ticker. Root cause: services/market_data.py's
_get_static_names() flattened IN_STOCKS and US_STOCKS into a single dict
keyed only by the bare symbol (suffix stripped), so any ticker present in
both universes collided and whichever list was appended last (US) always
won, regardless of which market the caller actually asked about. 55 such
collisions exist in the current universe lists, including INFY.

Fix: key the static-name cache by (symbol, market) instead of bare symbol.

These tests run with no network, no DB (matches this repo's existing
no-network unit-test convention, see test_finnhub_market_gate.py): NSE,
Finnhub, and yfinance fast_info calls are stubbed via monkeypatch.
"""
import asyncio

import pytest

from services import market_data


@pytest.fixture(autouse=True)
def _clear_static_names_cache():
    market_data._STATIC_NAMES = {}
    yield
    market_data._STATIC_NAMES = {}


def _stub_no_name_providers(monkeypatch):
    """Force every company-name source ahead of the static-list fallback
    (NSE, Finnhub) to come back empty, and stub fast_info with a plain
    price quote (market_cap present, so the enrich-pass is skipped) so
    execution reaches the static-universe-list lookup deterministically."""
    monkeypatch.setattr(market_data.nse_client, "get_quote", lambda symbol: None)
    monkeypatch.setattr(market_data.finnhub_client, "get_quote", lambda symbol, market: None)
    monkeypatch.setattr(
        market_data.MarketDataService, "_fetch_fast_info",
        lambda self, symbol, market: {
            "price": 220.88, "prev_close": 220.21, "day_high": 225.44,
            "day_low": 220.01, "open": 222.37, "volume": 100000,
            "market_cap": 218850000000, "year_high": 243.075, "year_low": 145.125,
        },
    )


@pytest.mark.regression
class TestStaticNameMarketIsolation:
    def test_get_static_names_keys_by_symbol_and_market(self):
        names = market_data._get_static_names()
        assert names["CUB:IN"] == "City Union Bank Limited"
        assert names["CUB:US"] == "Lionheart Holdings - Class A Ordinary Shares"

    def test_in_quote_for_colliding_symbol_gets_the_indian_company_name(self, monkeypatch):
        _stub_no_name_providers(monkeypatch)
        svc = market_data.MarketDataService()
        result = asyncio.run(svc.get_quote("CUB", "IN"))
        assert result["company_name"] == "City Union Bank Limited"

    def test_us_quote_for_colliding_symbol_gets_the_us_company_name(self, monkeypatch):
        _stub_no_name_providers(monkeypatch)
        svc = market_data.MarketDataService()
        result = asyncio.run(svc.get_quote("CUB", "US"))
        assert result["company_name"] == "Lionheart Holdings - Class A Ordinary Shares"

    def test_no_bare_symbol_keys_leak_into_the_static_cache(self):
        names = market_data._get_static_names()
        assert "CUB" not in names
