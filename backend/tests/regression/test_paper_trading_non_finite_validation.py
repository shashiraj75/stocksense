"""
Migration-verification hardening gate — regression tests proving:

1. A non-finite `price`/`stop_loss`/`target_price`/`entry_price` on
   BuyRequest/SellRequest/EditRequest is rejected (422), not silently
   accepted. Before this fix, `price=NaN` passed Pydantic's default float
   validation AND the router's own `if req.price <= 0` guard (`float('nan')
   <= 0` is `False` in Python) and reached the SQL execute call with a
   literal NaN bound parameter — reproduced directly against a fake
   connection below.
2. FastAPI's default RequestValidationError handler crashes with an
   unhandled 500 when echoing a non-finite `input` value back in the error
   body (Starlette's JSONResponse enforces `allow_nan=False` on output) —
   api/main.py's `_stable_validation_error_handler` fixes this globally.

Sent via raw JSON content (see `_raw_json_post`) since httpx's own `json=`
encoder cannot construct a NaN/Infinity request body at all — a real
attacker isn't bound by that; Python's own `json.loads` (which FastAPI/
Pydantic parses request bodies with) accepts bare NaN/Infinity/-Infinity
tokens by default.
"""
import json as _json
import time
from contextlib import contextmanager
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

TEST_SECRET = "regression-test-jwt-secret-at-least-32-bytes-long"
TEST_SUPABASE_URL = "https://test-project.supabase.co"
TEST_ISSUER = f"{TEST_SUPABASE_URL}/auth/v1"


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)


@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)


def _token(sub: str = "user-aaa") -> str:
    return jwt.encode(
        {"sub": sub, "aud": "authenticated", "iss": TEST_ISSUER, "exp": time.time() + 3600},
        TEST_SECRET, algorithm="HS256",
    )


def _auth() -> dict:
    return {"Authorization": f"Bearer {_token()}"}


class _RecordingConn:
    def __init__(self, fetchone_results=None):
        self.calls = []
        self._fetchone_results = list(fetchone_results or [])

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchone(self):
        return self._fetchone_results.pop(0) if self._fetchone_results else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @contextmanager
    def transaction(self):
        yield self


def _raw_json_post(client, path, body_dict, headers=None):
    raw = _json.dumps(body_dict)  # stdlib json allows NaN/Infinity by default, unlike httpx's encoder
    return client.post(path, content=raw, headers={**(headers or {}), "Content-Type": "application/json"})


@pytest.mark.regression
class TestNonFiniteBuyRequestRejected:
    @pytest.mark.parametrize("bad_price", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_price_rejected_before_any_db_call(self, client, bad_price):
        conn = _RecordingConn(fetchone_results=[(1_000_000.0, 100_000.0, True)])

        @contextmanager
        def _fake_conn():
            yield conn

        body_raw = f'{{"symbol": "AAPL", "market": "US", "quantity": 1, "price": {bad_price}, "idempotency_key": "nonfinite-test-key"}}'
        with patch.object(
            __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _fake_conn
        ), patch("api.routers.paper_trading._is_market_open", return_value=True):
            resp = client.post(
                "/api/paper-trading/buy", content=body_raw,
                headers={**_auth(), "Content-Type": "application/json"},
            )
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["msg"] == "Value error, must be a finite number"
        # The critical assertion: no non-finite value ever reached a bound
        # SQL parameter — before the fix, this list would contain a call
        # with `nan` in its params (the debit UPDATE).
        assert conn.calls == []

    def test_non_finite_stop_loss_rejected(self, client):
        body_raw = '{"symbol": "AAPL", "market": "US", "quantity": 1, "price": 100.0, "stop_loss": Infinity, "idempotency_key": "nonfinite-test-key"}'
        resp = client.post(
            "/api/paper-trading/buy", content=body_raw,
            headers={**_auth(), "Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    def test_non_finite_sell_price_rejected(self, client):
        body_raw = '{"price": NaN}'
        resp = client.post(
            "/api/paper-trading/sell/1", content=body_raw,
            headers={**_auth(), "Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    def test_non_finite_edit_stop_loss_rejected(self, client):
        body_raw = '{"stop_loss": Infinity}'
        resp = client.patch(
            "/api/paper-trading/trade/1", content=body_raw,
            headers={**_auth(), "Content-Type": "application/json"},
        )
        assert resp.status_code == 422


@pytest.mark.regression
class TestValidationErrorResponseIsAlwaysStableJson:
    """Proves api/main.py's _stable_validation_error_handler: the response
    itself must never contain a raw NaN/Infinity token (which would make it
    invalid JSON) and must never be a 500."""

    @pytest.mark.parametrize("bad_value", ["NaN", "Infinity", "-Infinity"])
    def test_response_is_valid_reparseable_json_never_500(self, client, bad_value):
        body_raw = f'{{"symbol": "AAPL", "market": "US", "quantity": 1, "price": {bad_value}, "idempotency_key": "nonfinite-test-key"}}'
        resp = client.post(
            "/api/paper-trading/buy", content=body_raw,
            headers={**_auth(), "Content-Type": "application/json"},
        )
        assert resp.status_code == 422
        assert resp.status_code != 500
        # Must re-parse cleanly — proves no bare NaN/Infinity token leaked
        # into the response body text.
        parsed = _json.loads(resp.text)
        assert "NaN" not in resp.text
        assert "Infinity" not in resp.text
        echoed_input = parsed["detail"][0]["input"]
        if isinstance(echoed_input, dict):
            assert echoed_input.get("price") == "<rejected: non-finite number>"
        else:
            assert echoed_input == "<rejected: non-finite number>"

    def test_ordinary_validation_error_shape_is_unchanged(self, client):
        """A normal (finite-value) validation error must still look exactly
        like a standard FastAPI 422 — this handler only changes behavior
        for non-finite floats, nothing else."""
        resp = client.post(
            "/api/paper-trading/buy",
            json={"symbol": "AAPL", "market": "NOT_A_MARKET", "quantity": 1, "price": 100.0, "idempotency_key": "nonfinite-test-key"},
            headers=_auth(),
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert isinstance(detail, list)
        assert detail[0]["loc"] == ["body", "market"]

    def test_unrelated_exception_types_are_not_swallowed(self, client):
        """The handler is scoped to RequestValidationError only — an
        actually-nonexistent route must still 404, not be silently
        absorbed into a validation-error shape."""
        resp = client.post("/api/paper-trading/this-route-does-not-exist", json={})
        assert resp.status_code == 404
