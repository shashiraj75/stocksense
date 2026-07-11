"""
Portfolio performance/UX hotfix — sector allocation must not depend on the
Prediction Engine's per-symbol signal computation just to learn which
sector a holding belongs to. This adds a lightweight, read-only batch
sector lookup (GET /api/stocks/sectors) sourced from the nightly-refreshed
stock_fundamentals_cache table, and services.fundamentals_cache.get_sectors_batch()
underneath it.

Session 15: get_sectors_batch now returns both `sector` and `industry`
(previously `sector` alone) per symbol — needed so Portfolio's display
layer (frontend/src/utils/sectorDisplay.ts) can notice cases like
Wellness/Hospitals/Diagnostics that screener.in files under a broad
"Consumer Services" sector (e.g. JSLL/Jeena Sikho Lifecare) and show a
more useful "Healthcare" label, without this cache ever rewriting
screener.in's own raw classification.

These tests are fully mocked at the DB boundary (no real Postgres
connection) — they verify: batch lookup shape, missing-symbol handling,
market scoping, and — the constraint this whole hotfix must respect —
that this path never touches the Prediction Engine, RCI, or any scoring
logic.
"""
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


# ─────────────────────────────────────────────────────────────────────────────
# services.fundamentals_cache.get_sectors_batch — unit level, mocked _conn()
# ─────────────────────────────────────────────────────────────────────────────

def _mock_conn_returning(rows):
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = rows
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx


def test_get_sectors_batch_returns_sector_and_industry_per_symbol():
    from services import fundamentals_cache

    with patch.object(fundamentals_cache, "_conn", return_value=_mock_conn_returning(
        [("TCS", "IT", "IT Services"), ("WIPRO", "IT", "IT Services"), ("ONGC", "Energy", "Oil & Gas")]
    )):
        result = fundamentals_cache.get_sectors_batch(["TCS", "WIPRO", "ONGC"], market="IN")

    assert result == {
        "TCS": {"sector": "IT", "industry": "IT Services"},
        "WIPRO": {"sector": "IT", "industry": "IT Services"},
        "ONGC": {"sector": "Energy", "industry": "Oil & Gas"},
    }


def test_get_sectors_batch_raw_passthrough_for_jsll_like_case():
    """The exact reported case: screener.in's raw sector for JSLL is
    "Consumer Services" with industry "Wellness" — this cache must return
    both fields completely unmodified. Normalizing "Wellness" into
    "Healthcare" is the frontend display layer's job, not this cache's."""
    from services import fundamentals_cache

    with patch.object(fundamentals_cache, "_conn", return_value=_mock_conn_returning(
        [("JSLL", "Consumer Services", "Wellness")]
    )):
        result = fundamentals_cache.get_sectors_batch(["JSLL"], market="IN")

    assert result == {"JSLL": {"sector": "Consumer Services", "industry": "Wellness"}}


def test_get_sectors_batch_missing_symbol_maps_to_none_none():
    """A symbol outside the cache (or not refreshed yet) must still appear
    in the result — as nulls, not omitted — so callers can index by their
    original symbol list without a membership check."""
    from services import fundamentals_cache

    with patch.object(fundamentals_cache, "_conn", return_value=_mock_conn_returning(
        [("TCS", "IT", "IT Services")]
    )):
        result = fundamentals_cache.get_sectors_batch(["TCS", "UNKNOWNSTOCK"], market="IN")

    assert result == {
        "TCS": {"sector": "IT", "industry": "IT Services"},
        "UNKNOWNSTOCK": {"sector": None, "industry": None},
    }


def test_get_sectors_batch_empty_input_returns_empty_dict_without_querying():
    from services import fundamentals_cache

    with patch.object(fundamentals_cache, "_conn") as mock_conn:
        result = fundamentals_cache.get_sectors_batch([], market="IN")

    assert result == {}
    mock_conn.assert_not_called()


def test_get_sectors_batch_is_market_scoped():
    """Same symbol string in two markets must not bleed sectors across —
    the query always filters on market, never symbol alone."""
    from services import fundamentals_cache

    captured_params = {}

    def _conn_capturing():
        conn = MagicMock()

        def _execute(sql, params):
            captured_params["sql"] = sql
            captured_params["params"] = params
            m = MagicMock()
            m.fetchall.return_value = [("TATAMOTORS", "Auto", "Auto Ancillaries")]
            return m
        conn.execute.side_effect = _execute
        ctx = MagicMock()
        ctx.__enter__.return_value = conn
        ctx.__exit__.return_value = False
        return ctx

    with patch.object(fundamentals_cache, "_conn", side_effect=_conn_capturing):
        fundamentals_cache.get_sectors_batch(["TATAMOTORS"], market="US")

    assert captured_params["params"][1] == "US"
    assert "market = %s" in captured_params["sql"]


def test_get_sectors_batch_swallows_db_errors_returns_none_for_all():
    """Same fail-safe contract as the existing single-symbol get_sector() —
    a DB error must degrade to "no sector known" for every requested
    symbol, never raise into the caller (Portfolio's allocation view)."""
    from services import fundamentals_cache

    with patch.object(fundamentals_cache, "_conn", side_effect=RuntimeError("connection refused")):
        result = fundamentals_cache.get_sectors_batch(["TCS", "INFY"], market="IN")

    assert result == {
        "TCS": {"sector": None, "industry": None},
        "INFY": {"sector": None, "industry": None},
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/stocks/sectors — router level
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)


def test_sectors_endpoint_returns_batch_shape(client):
    with patch("services.fundamentals_cache.get_sectors_batch", return_value={
        "TCS": {"sector": "IT", "industry": "IT Services"},
        "WIPRO": {"sector": "IT", "industry": "IT Services"},
    }):
        resp = client.get("/api/stocks/sectors", params={"symbols": "TCS,WIPRO", "market": "IN"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"sectors": {
        "TCS": {"sector": "IT", "industry": "IT Services"},
        "WIPRO": {"sector": "IT", "industry": "IT Services"},
    }}


def test_sectors_endpoint_market_specific_lookup_passes_market_through(client):
    captured = {}

    def _fake_batch(symbols, market):
        captured["symbols"] = symbols
        captured["market"] = market
        return {s: {"sector": None, "industry": None} for s in symbols}

    with patch("services.fundamentals_cache.get_sectors_batch", side_effect=_fake_batch):
        resp = client.get("/api/stocks/sectors", params={"symbols": "AAPL,MSFT", "market": "US"})

    assert resp.status_code == 200
    assert captured["market"] == "US"
    assert captured["symbols"] == ["AAPL", "MSFT"]


def test_sectors_endpoint_missing_sectors_return_null_not_error(client):
    with patch("services.fundamentals_cache.get_sectors_batch", return_value={"OBSCURESTOCK": {"sector": None, "industry": None}}):
        resp = client.get("/api/stocks/sectors", params={"symbols": "OBSCURESTOCK", "market": "IN"})
    assert resp.status_code == 200
    assert resp.json() == {"sectors": {"OBSCURESTOCK": {"sector": None, "industry": None}}}


def test_sectors_endpoint_empty_symbols_short_circuits(client):
    with patch("services.fundamentals_cache.get_sectors_batch") as mock_batch:
        resp = client.get("/api/stocks/sectors", params={"symbols": "", "market": "IN"})
    assert resp.status_code == 200
    assert resp.json() == {"sectors": {}}
    mock_batch.assert_not_called()


def test_sectors_endpoint_never_touches_prediction_engine(client):
    """The whole point of this endpoint: sector allocation must resolve
    without running any prediction/scoring. Patch PredictionEngine.predict
    to raise — if the sectors endpoint ever calls it, this test fails."""
    with patch("services.fundamentals_cache.get_sectors_batch", return_value={"TCS": {"sector": "IT", "industry": "IT Services"}}), \
         patch("services.prediction_engine.PredictionEngine.predict",
               side_effect=AssertionError("sectors endpoint must never invoke the Prediction Engine")):
        resp = client.get("/api/stocks/sectors", params={"symbols": "TCS", "market": "IN"})
    assert resp.status_code == 200
    assert resp.json() == {"sectors": {"TCS": {"sector": "IT", "industry": "IT Services"}}}
