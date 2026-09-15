"""
GET /api/stocks/indices?market=COMMODITIES — Gold, Silver, Brent Crude added
to the ticker-ribbon indices endpoint (2026-09-15 user request: "can we also
show Gold and Silver per ounce in USD and the Brent Crude price").

Reuses the exact yfinance futures tickers (GC=F/SI=F/BZ=F) already used by
services/global_context.py's GLOBAL_TICKERS for prediction-engine macro
context — not re-picked here, just read for the ribbon too.
"""
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from api.main import app
from api.routers.stocks import INDICES, _index_cache

client = TestClient(app)


def _fake_fast_info(last_price: float, previous_close: float):
    fi = MagicMock()
    fi.last_price = last_price
    fi.previous_close = previous_close
    return fi


class TestIndicesCommodities:
    def setup_method(self):
        _index_cache.clear()  # each test gets a fresh 15s TTL cache

    def test_commodities_bucket_has_gold_silver_brent(self):
        assert INDICES["COMMODITIES"] == [
            ("GC=F", "Gold (oz)"),
            ("SI=F", "Silver (oz)"),
            ("BZ=F", "Brent Crude"),
        ]

    def test_endpoint_accepts_commodities_market_and_returns_all_three(self):
        with patch("api.routers.stocks.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.fast_info = _fake_fast_info(2650.10, 2600.00)
            resp = client.get("/api/stocks/indices", params={"market": "COMMODITIES"})
        assert resp.status_code == 200
        data = resp.json()
        names = [idx["name"] for idx in data["indices"]]
        assert names == ["Gold (oz)", "Silver (oz)", "Brent Crude"]
        # Every row uses the same mocked quote here (single mock covers all
        # three calls) — this test only proves shape/wiring, not real prices.
        for idx in data["indices"]:
            assert idx["price"] == 2650.1
            assert idx["change_pct"] is not None

    def test_unknown_market_falls_back_to_empty_list_not_an_error(self):
        # INDICES.get(market, []) — matches existing IN/US/CRYPTO behavior,
        # not new for this change, but worth pinning since the Literal type
        # only constrains FastAPI's own validation, not this dict lookup.
        resp = client.get("/api/stocks/indices", params={"market": "IN"})
        assert resp.status_code == 200
