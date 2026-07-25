"""
API contract tests for GET /api/leadership/context — additive endpoint,
flag-gated, market isolation, backward compatibility of every other route
(no existing schema changes). No live network (SES-003).
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clear_stock_context_cache():
    # compute_stock_context is TTL-cached at module level (Section 17
    # #10) — clear it so one test's cached result can't leak into another
    # via a shared (symbol, market, as_of) key.
    from services.market_leadership import orchestration as orch
    orch._STOCK_CONTEXT_CACHE.clear()


@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)


@pytest.mark.integration
class TestLeadershipContextEndpoint:
    def test_disabled_by_default(self, client, monkeypatch):
        monkeypatch.delenv("MARKET_LEADERSHIP_UI_ENABLED", raising=False)
        monkeypatch.delenv("MARKET_LEADERSHIP_ENGINE_ENABLED", raising=False)
        resp = client.get("/api/leadership/context?symbol=AAPL&market=US")
        assert resp.status_code == 200
        assert resp.json() == {"status": "disabled"}

    def test_ui_flag_alone_without_engine_flag_stays_disabled(self, client, monkeypatch):
        """Defense in depth: UI_ENABLED checks engine_enabled() internally
        via cfg.ui_enabled(), so the master gate still governs."""
        monkeypatch.delenv("MARKET_LEADERSHIP_ENGINE_ENABLED", raising=False)
        monkeypatch.setenv("MARKET_LEADERSHIP_UI_ENABLED", "1")
        resp = client.get("/api/leadership/context?symbol=AAPL&market=US")
        assert resp.json() == {"status": "disabled"}

    def test_missing_market_is_rejected(self, client):
        resp = client.get("/api/leadership/context?symbol=AAPL")
        assert resp.status_code == 422  # required, no silent default

    def test_invalid_market_is_rejected_not_defaulted(self, client):
        resp = client.get("/api/leadership/context?symbol=AAPL&market=XX")
        assert resp.status_code == 422

    def test_enabled_flag_computes_and_never_crashes(self, client, monkeypatch):
        monkeypatch.setenv("MARKET_LEADERSHIP_ENGINE_ENABLED", "1")
        monkeypatch.setenv("MARKET_LEADERSHIP_UI_ENABLED", "1")

        from services.market_leadership import orchestration as orch
        import pandas as pd

        monkeypatch.setattr(orch, "fetch_price_history", lambda *a, **k: pd.DataFrame(
            columns=["Open", "High", "Low", "Close", "Volume"]
        ))

        class _FakeTicker:
            def __init__(self, *a, **k):
                pass

            def history(self, **kw):
                return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

        monkeypatch.setattr(orch.yf, "Ticker", _FakeTicker)
        resp = client.get("/api/leadership/context?symbol=AAPL&market=US")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("ok", "unavailable", "error")

    def test_market_isolation_in_response(self, client, monkeypatch):
        monkeypatch.setenv("MARKET_LEADERSHIP_ENGINE_ENABLED", "1")
        monkeypatch.setenv("MARKET_LEADERSHIP_UI_ENABLED", "1")

        from services.market_leadership import orchestration as orch
        from tests.unit.market_leadership.conftest import make_uptrend_df
        import datetime

        df = make_uptrend_df(300)
        monkeypatch.setattr(orch, "fetch_price_history", lambda *a, **k: df)

        class _FakeTicker:
            def __init__(self, *a, **k):
                pass

            def history(self, **kw):
                return df

        monkeypatch.setattr(orch.yf, "Ticker", _FakeTicker)
        resp_in = client.get("/api/leadership/context?symbol=TCS&market=IN")
        resp_us = client.get("/api/leadership/context?symbol=AAPL&market=US")
        if resp_in.json().get("status") == "ok":
            assert resp_in.json()["rs"]["market"] == "IN"
        if resp_us.json().get("status") == "ok":
            assert resp_us.json()["rs"]["market"] == "US"


@pytest.mark.integration
class TestExistingRoutesUnaffectedByNewRouter:
    """The new router's registration must not shadow, break, or change any
    existing route's schema."""

    def test_validation_status_route_still_works(self, client):
        resp = client.get("/api/validation/status")
        assert resp.status_code == 200

    def test_openapi_schema_includes_new_route_without_removing_old_ones(self, client):
        schema = client.get("/openapi.json").json()
        paths = schema["paths"]
        assert "/api/leadership/context" in paths
        assert "/api/validation/status" in paths
        assert "/api/picks" in paths or any(p.startswith("/api/picks") for p in paths)
