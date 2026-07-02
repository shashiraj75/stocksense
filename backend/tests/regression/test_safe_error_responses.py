"""
UI/UX Truthfulness Correction Program — Wave 0A: shared safe-error responses
and authentication privacy.

Root cause this addresses: alerts, portfolio, screener (heatmap), validation,
auth (accept-terms), and predictions routes all returned raw Python
exception text (`str(e)`, `type(e).__name__`, or `traceback.format_exc()`)
directly in API responses on unexpected failure — exposing internal
implementation details to end users and making a genuine computation
failure indistinguishable from a successful empty result in several cases
(validation's per-stock/history/single-stock endpoints had no discriminator
field at all before this change).

All tests are fully mocked — no DB, no network, no external providers, no
real Supabase calls (Supabase behavior is frontend-only and out of scope
for backend tests).
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# ─────────────────────────────────────────────────────────────────────────────
# Shared helper unit tests
# ─────────────────────────────────────────────────────────────────────────────

def test_safe_error_message_returns_fixed_message_not_exception_text():
    import logging
    from services.safe_errors import safe_error_message

    log = logging.getLogger("test.safe_errors")
    exc = ValueError("connection string: postgres://user:secret@host/db")
    result = safe_error_message(log, "test.context", exc, "Unable to load data right now. Please try again.")

    assert result == "Unable to load data right now. Please try again."
    assert "secret" not in result
    assert "postgres://" not in result
    assert "ValueError" not in result


def test_safe_error_message_logs_the_real_exception(caplog):
    import logging
    from services.safe_errors import safe_error_message

    log = logging.getLogger("test.safe_errors.logging")
    exc = RuntimeError("actual diagnostic detail")

    with caplog.at_level(logging.ERROR, logger="test.safe_errors.logging"):
        safe_error_message(log, "test.context", exc, "safe message")

    assert any("actual diagnostic detail" in r.getMessage() or "test.context" in r.getMessage()
               for r in caplog.records)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from services.auth import get_current_user_id
    from api.main import app

    app.dependency_overrides[get_current_user_id] = lambda: "test-user-id"
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user_id, None)


# ─────────────────────────────────────────────────────────────────────────────
# 1-3. Alerts and Portfolio — raw exception text no longer returned
# ─────────────────────────────────────────────────────────────────────────────

def test_alerts_get_failure_returns_safe_message_not_raw_exception(client):
    with patch("api.routers.alerts._ensure_table",
               side_effect=RuntimeError("psycopg.OperationalError: could not connect to server")):
        resp = client.get("/api/alerts/test-user-id")
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail == "Unable to load alerts right now. Please try again."
    assert "psycopg" not in detail
    assert "OperationalError" not in detail


def test_alerts_create_failure_returns_safe_message(client):
    with patch("api.routers.alerts._ensure_table",
               side_effect=RuntimeError("SQL syntax error near INSERT")):
        resp = client.post("/api/alerts/test-user-id", json={
            "symbol": "AAPL", "market": "US", "target_price": 100.0, "direction": "above",
        })
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail == "Unable to create the alert right now. Please try again."
    assert "SQL" not in detail


def test_portfolio_get_failure_returns_safe_message_not_raw_exception(client):
    with patch("api.routers.portfolio._ensure_table",
               side_effect=RuntimeError("psycopg.OperationalError: could not connect to server")):
        resp = client.get("/api/portfolio/test-user-id")
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail == "Unable to load your portfolio right now. Please try again."
    assert "psycopg" not in detail


def test_portfolio_add_holding_failure_returns_safe_message(client):
    with patch("api.routers.portfolio._ensure_table",
               side_effect=RuntimeError("duplicate key value violates unique constraint")):
        resp = client.post("/api/portfolio/test-user-id", json={
            "symbol": "AAPL", "market": "US", "qty": 10, "avg_price": 150.0,
        })
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail == "Unable to add the holding right now. Please try again."
    assert "constraint" not in detail


# ─────────────────────────────────────────────────────────────────────────────
# 4. Screener (heatmap) and predictions — safe messages
# ─────────────────────────────────────────────────────────────────────────────

def test_screener_heatmap_failure_returns_safe_message_not_raw_exception():
    from api.main import app
    client = TestClient(app)
    with patch("api.routers.screener.get_heatmap",
               side_effect=RuntimeError("yfinance.exceptions.YFRateLimitError: 429 Too Many Requests")):
        resp = client.get("/api/screener/heatmap", params={"market": "IN"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["sectors"] == []
    assert body["error"] == "Heatmap data is temporarily unavailable."
    assert "YFRateLimitError" not in body["error"]
    assert "429" not in body["error"]


def test_prediction_background_failure_never_exposes_exception_type_name():
    """
    _bg_thread's except clause previously wrote
    f"Prediction failed: {type(e).__name__}. Please retry." into the cache,
    which the GET /{symbol} cache-hit path then serves verbatim.
    """
    import api.routers.predictions as predictions_router

    async def _raise(*args, **kwargs):
        raise KeyError("unexpected_field_name")

    fake_cache: dict = {}
    with patch.object(predictions_router, "_pred_cache", fake_cache), \
         patch.object(predictions_router.engine, "predict", side_effect=_raise), \
         patch("services.prediction_engine._cache_set") as mock_cache_set:
        predictions_router._bg_thread("AAPL", "US", "short", "AAPL:US:short")

    assert mock_cache_set.called
    _, key, cached_value = mock_cache_set.call_args[0]
    err_payload = cached_value[1]
    assert "KeyError" not in err_payload["error"]
    assert "unexpected_field_name" not in err_payload["error"]
    assert err_payload["error"] == "Prediction data is temporarily unavailable. Please try again."


# ─────────────────────────────────────────────────────────────────────────────
# 2. Validation — no traceback text, and failure is distinguishable from
#    a genuine empty/zero result
# ─────────────────────────────────────────────────────────────────────────────

def test_validation_results_failure_returns_safe_message_no_traceback():
    from api.main import app
    client = TestClient(app)
    with patch("services.validation_engine.get_latest_results",
               side_effect=RuntimeError("sqlite3.OperationalError: no such table: val_runs")):
        resp = client.get("/api/validation/results", params={"horizon": "medium", "universe": "nifty100"})
    body = resp.json()
    assert body["available"] is False
    assert "sqlite3" not in body["error"]
    assert "OperationalError" not in body["error"]
    assert "trace" not in body
    assert body["error"] == "Validation data is temporarily unavailable."


def test_validation_engine_get_latest_results_failure_no_raw_traceback():
    """Root-cause fix: get_latest_results() itself must not leak str(e)/traceback,
    since the router's own try/except never fires when this function catches
    internally and returns a normal dict."""
    from services.validation_engine import get_latest_results

    with patch("services.validation_engine._init_db",
               side_effect=RuntimeError("disk I/O error at /var/lib/postgresql/data")):
        result = get_latest_results(horizon="medium", universe="nifty100")

    assert result["available"] is False
    assert "disk I/O error" not in result["error"]
    assert "trace" not in result
    assert result["error"] == "Validation data is temporarily unavailable."


def test_validation_stock_results_failure_is_distinguishable_from_zero_signals():
    """A genuine failure must set available=False — a caller must never be
    able to mistake this for '0 stocks have BUY signals today' (available
    absent/omitted previously made these visually identical: {"stocks": []})."""
    from api.main import app
    client = TestClient(app)
    with patch("services.validation_engine.get_per_stock_results",
               side_effect=RuntimeError("connection reset by peer")):
        resp = client.get("/api/validation/results/stocks", params={"horizon": "medium", "universe": "nifty100"})
    body = resp.json()
    assert body["available"] is False
    assert body["stocks"] == []
    assert "connection reset" not in body["error"]
    assert body["error"] == "Validation data is temporarily unavailable."


def test_validation_stock_results_genuine_empty_is_available_true():
    from api.main import app
    client = TestClient(app)
    with patch("services.validation_engine.get_per_stock_results", return_value=[]):
        resp = client.get("/api/validation/results/stocks", params={"horizon": "medium", "universe": "nifty100"})
    body = resp.json()
    assert body["available"] is True
    assert body["stocks"] == []
    assert "error" not in body


def test_validation_single_stock_failure_returns_safe_message():
    from api.main import app
    client = TestClient(app)
    with patch("services.validation_engine.get_per_stock_results",
               side_effect=RuntimeError("upstream provider timeout")):
        resp = client.get("/api/validation/results/stock/AAPL", params={"horizon": "medium", "universe": "nifty100"})
    body = resp.json()
    assert body["available"] is False
    assert "upstream provider timeout" not in body["error"]


def test_validation_history_failure_returns_safe_message():
    from api.main import app
    client = TestClient(app)
    with patch("services.validation_engine.get_all_run_summaries",
               side_effect=RuntimeError("relation val_runs does not exist")):
        resp = client.get("/api/validation/results/history")
    body = resp.json()
    assert body["available"] is False
    assert body["runs"] == []
    assert "relation" not in body["error"]


# ─────────────────────────────────────────────────────────────────────────────
# 6. Genuine successful zero-result vs unavailable — Multibagger already
#    covered in test_multibagger_decimal_handling.py; this file adds the
#    equivalent coverage for the newly-touched routes above.
# ─────────────────────────────────────────────────────────────────────────────

def test_validation_results_genuine_no_run_is_available_false_but_not_an_error():
    """"No run yet" and "computation failed" are both available=False today
    (pre-existing behavior, unchanged by this fix) — this test locks in that
    a 'no run found' message is never confused with the new safe-error text."""
    from services.validation_engine import get_latest_results

    with patch("services.validation_engine._fetchone", return_value=None):
        result = get_latest_results(horizon="medium", universe="nifty100")

    assert result["available"] is False
    assert result.get("message") == "No validation run found. Run /api/validation/run first."
    assert "error" not in result


# ─────────────────────────────────────────────────────────────────────────────
# Auth — accept-terms safe error (backend-testable route only; Supabase
# sign-in/reset/set-password privacy behavior lives in the frontend and
# cannot be unit-tested here without calling Supabase, which is out of scope)
# ─────────────────────────────────────────────────────────────────────────────

def test_accept_terms_failure_returns_safe_message(client, monkeypatch):
    monkeypatch.setenv("USE_POSTGRES", "1")
    with patch("services.postgres_store._get_pool", side_effect=RuntimeError("FATAL: password authentication failed")):
        resp = client.post("/api/auth/accept-terms", json={
            "user_id": "test-user-id", "email": "test@example.com",
        })
    body = resp.json()
    assert body["status"] == "error"
    assert "password authentication failed" not in body["detail"]
    assert body["detail"] == "Unable to record acceptance right now. Please try again."
