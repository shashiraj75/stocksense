"""
Trade Postmortem Engine, Sprint 2, Stage 13 — regression tests for
POST /api/paper-trading/postmortem/{trade_id}/generate.

Mirrors test_paper_trading_postmortem_endpoint.py's _RecordingConn
pattern for the identical-404 ownership convention, plus a minimal
generation-aware fake for the idempotent-persistence assertions this
endpoint's own contract requires.
"""
import datetime as dt
import json
import time as _time
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
        {"sub": sub, "aud": "authenticated", "iss": TEST_ISSUER, "exp": _time.time() + 3600},
        TEST_SECRET, algorithm="HS256",
    )


def _auth(sub: str = "user-aaa") -> dict:
    return {"Authorization": f"Bearer {_token(sub)}"}


def _row(
    *, trade_id=1, owner="user-aaa", status="CLOSED", symbol="AAPL", market="US",
    quantity=10, entry_price=100.0, exit_price=120.0, stop_loss=90.0, target_price=130.0,
    opened_at=dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),
    closed_at=dt.datetime(2026, 6, 2, tzinfo=dt.timezone.utc),
    trade_management_mode="manual", exit_reason="MANUAL",
):
    return (
        trade_id, owner, status, symbol, market, quantity, entry_price, exit_price,
        stop_loss, target_price, opened_at, closed_at, trade_management_mode, exit_reason,
        None, None, None, None, None, None, None, None, None, None, None,
    )


class _FakeConn:
    """Answers the trade-row SELECT (from the queued row), the entry-
    snapshot/exit-snapshot-presence lookups (always 'none exist'), and
    persists reports in-memory keyed by version triple — enough to prove
    the endpoint's own idempotency and status-mapping contract without a
    real database."""

    _report_rows: dict = {}
    _next_id = [1]

    def __init__(self, fetchone_results):
        self.calls = []
        self._queue = list(fetchone_results)

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        stripped = (sql or "").strip()
        if stripped.startswith("INSERT INTO paper_trade_postmortem_report"):
            (paper_trade_id, user_id, market, trading_date, tz, schema_v, calc_v, rules_v, bundle_v,
             ev_hash, status, structured, ev_items, claims, manifest, gaps, warnings) = params
            key = (paper_trade_id, schema_v, calc_v, rules_v)
            for row in type(self)._report_rows.values():
                if (row[1], row[6], row[7], row[8]) == key:
                    self._pending = None
                    return self
            new_id = type(self)._next_id[0]
            type(self)._next_id[0] += 1
            row = (new_id, paper_trade_id, user_id, market, trading_date, tz, schema_v, calc_v, rules_v,
                   bundle_v, ev_hash, status, json.loads(structured), json.loads(ev_items),
                   json.loads(claims), json.loads(manifest), json.loads(gaps), json.loads(warnings))
            type(self)._report_rows[new_id] = row
            self._pending = row
            return self
        if stripped.startswith("SELECT") and "WHERE paper_trade_id = %s AND report_schema_version" in sql:
            paper_trade_id, schema_v, calc_v, rules_v = params
            key = (paper_trade_id, schema_v, calc_v, rules_v)
            for row in type(self)._report_rows.values():
                if (row[1], row[6], row[7], row[8]) == key:
                    self._pending = row
                    return self
            self._pending = None
            return self
        # Trade-row SELECT and entry/exit-snapshot lookups come off the
        # queued results in call order (mirrors _RecordingConn's own
        # convention from test_paper_trading_postmortem_endpoint.py).
        self._pending = self._queue.pop(0) if self._queue else None
        return self

    def fetchone(self):
        return self._pending

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @contextmanager
    def transaction(self):
        yield self


def _generate(client, trade_id, fetchone_results, auth_sub="user-aaa"):
    _FakeConn._report_rows = {}
    conn = _FakeConn(fetchone_results)

    @contextmanager
    def _fake_conn():
        yield conn

    with patch.object(
        __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _fake_conn
    ):
        resp = client.post(f"/api/paper-trading/postmortem/{trade_id}/generate", headers=_auth(auth_sub))
    return resp, conn


@pytest.mark.regression
class TestGenerateEndpoint:
    def test_closed_trade_generates_report(self, client):
        # queue: trade row, entry-snapshot lookup (None), exit-snapshot-exists lookup (None)
        resp, _ = _generate(client, 1, [_row(), None, None])
        assert resp.status_code == 200
        body = resp.json()
        assert body["trade_id"] == 1
        assert body["generated"] is True
        assert body["status"] in ("COMPLETE", "LIMITED_EVIDENCE")

    def test_trade_not_found_404(self, client):
        resp, _ = _generate(client, 999, [None])
        assert resp.status_code == 404

    def test_trade_belonging_to_another_user_identical_404(self, client):
        not_found, _ = _generate(client, 999, [None])
        other_user, _ = _generate(client, 1, [_row(owner="someone-else")])
        assert other_user.status_code == 404
        assert other_user.json() == not_found.json()

    def test_open_trade_returns_409(self, client):
        resp, _ = _generate(client, 1, [_row(status="OPEN")])
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "TRADE_NOT_CLOSED"

    def test_missing_auth_401(self, client):
        resp = client.post("/api/paper-trading/postmortem/1/generate")
        assert resp.status_code == 401

    def test_never_alters_the_trade_row(self, client):
        """No UPDATE to paper_trades anywhere in this call path."""
        resp, conn = _generate(client, 1, [_row(), None, None])
        assert resp.status_code == 200
        for sql, _ in conn.calls:
            assert "UPDATE paper_trades" not in sql
