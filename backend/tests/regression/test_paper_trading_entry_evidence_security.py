"""
Migration-verification hardening gate, Part 10 — focused security tests for
EntryEvidenceRequest's field-size bounds. No live DB needed: all of these
are rejected at the Pydantic validation layer, before any connection is
opened.
"""
import json as _json
import time
import uuid
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


def _auth() -> dict:
    token = jwt.encode(
        {"sub": "user-aaa", "aud": "authenticated", "iss": TEST_ISSUER, "exp": time.time() + 3600},
        TEST_SECRET, algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _buy_body(**entry_evidence_overrides):
    # Owner-authorized hardening — idempotency_key is now REQUIRED by
    # POST /buy; every body built here gets a fresh default key.
    return {
        "symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0,
        "idempotency_key": f"ptbuy-test-{uuid.uuid4()}",
        "entry_evidence": entry_evidence_overrides,
    }


class _RecordingConn:
    def __init__(self, fetchone_results=None):
        self.calls = []
        self._fetchone_results = list(fetchone_results or [])
        self._pending_reservation_insert = False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        # Owner-authorized hardening — idempotency_key is now REQUIRED, so
        # every Buy now also issues the reservation
        # "INSERT INTO paper_trade_idempotency_key ... RETURNING id" before
        # the statements this fake was built to simulate. Answered
        # synthetically here (never consumes `_fetchone_results`).
        self._pending_reservation_insert = (
            "INSERT INTO paper_trade_idempotency_key" in sql and "RETURNING id" in sql
        )
        return self

    def fetchone(self):
        if self._pending_reservation_insert:
            self._pending_reservation_insert = False
            return (777001,)
        return self._fetchone_results.pop(0) if self._fetchone_results else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @contextmanager
    def transaction(self):
        yield self


def _default_conns():
    return [
        _RecordingConn(fetchone_results=[(1_000_000.0, 100_000.0, True)]),
        _RecordingConn(fetchone_results=[(999_900.0,), (1,)]),
    ]


def _mocked_buy(client, body, conns=None):
    # Belt-and-braces: also inject a default key for any body constructed
    # inline rather than via `_buy_body` above.
    body = {"idempotency_key": f"ptbuy-test-{uuid.uuid4()}", **body}
    pending = list(conns if conns is not None else _default_conns())
    made = []

    def factory():
        conn = pending.pop(0)
        made.append(conn)
        return conn

    with patch.object(
        __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", factory
    ), patch("api.routers.paper_trading._is_market_open", return_value=True):
        resp = client.post("/api/paper-trading/buy", json=body, headers=_auth())
    return resp, made


@pytest.mark.regression
class TestEntryEvidenceSizeBounds:
    def test_oversized_string_field_rejected(self, client):
        resp, made = _mocked_buy(client, _buy_body(recommendation_signal="X" * 200))
        assert resp.status_code == 422
        assert made == []  # rejected before any connection was ever opened

    def test_oversized_reasoning_array_rejected(self, client):
        oversized_reasoning = [{"indicator": "RSI", "signal": "BUY", "reason": "x"} for _ in range(200)]
        resp, made = _mocked_buy(client, _buy_body(recommendation_reasoning=oversized_reasoning))
        assert resp.status_code == 422
        assert made == []

    def test_oversized_individual_reasoning_item_rejected(self, client):
        huge_reasoning = [{"indicator": "RSI", "signal": "BUY", "reason": "x" * 25_000}]
        resp, made = _mocked_buy(client, _buy_body(recommendation_reasoning=huge_reasoning))
        assert resp.status_code == 422
        assert made == []

    def test_reasonable_reasoning_array_accepted(self, client):
        """Confirms the bound doesn't reject legitimate, ordinary-sized
        evidence — only pathological inputs."""
        normal_reasoning = [
            {"indicator": "RSI", "signal": "BUY", "reason": "oversold"},
            {"indicator": "MACD", "signal": "BUY", "reason": "bullish crossover"},
        ]
        resp, _ = _mocked_buy(client, _buy_body(recommendation_reasoning=normal_reasoning, confidence_score=75.0))
        assert resp.status_code == 200

    def test_sql_injection_shaped_symbol_does_not_crash_and_is_parameterized(self, client):
        """Confirms the symbol field is passed as a bound parameter, not
        interpolated into SQL text — a malicious-shaped string must not
        cause a 500, and the raw injection text must appear only as a bound
        parameter value, never concatenated into the executed SQL string."""
        body = {
            "symbol": "AAPL'; DROP TABLE paper_trades; --",
            "market": "US", "quantity": 1, "price": 101.0,
        }
        resp, made = _mocked_buy(client, body)
        assert resp.status_code == 200
        for conn in made:
            for sql, params in conn.calls:
                assert "DROP TABLE" not in sql
                assert "--" not in sql
