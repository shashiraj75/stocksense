"""
Trade Postmortem Engine, Sprint 2, Stage 11 — regression tests proving
POST /reset deletes rows from the three new durability tables
(paper_trade_exit_snapshot, paper_trade_postmortem_outbox,
paper_trade_postmortem_report) so a reset never leaves an orphaned
exit-snapshot/outbox/report row behind, for both the market=ALL branch
(scoped by user_id alone) and the market-specific branch (scoped by
user_id + market, with the outbox delete going through a paper_trades
subquery since that table has no market column of its own).

Mirrors test_paper_trading_reset_idempotency_cleanup.py's minimal
call-recording fake — this suite only needs to prove the right DELETE
statements are issued with the right parameters, not simulate full
table semantics.
"""
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


def _auth(sub: str = "user-aaa") -> dict:
    token = jwt.encode(
        {"sub": sub, "aud": "authenticated", "iss": TEST_ISSUER, "exp": time.time() + 3600},
        TEST_SECRET, algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


class _RecordingConn:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql.strip(), params))
        return self

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @contextmanager
    def transaction(self):
        yield self


def _reset(client, market):
    conn = _RecordingConn()

    def factory():
        return conn

    with patch.object(__import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", factory):
        resp = client.post(f"/api/paper-trading/reset?market={market}", headers=_auth())
    return resp, conn


def _delete_calls_for(conn, table):
    return [(sql, params) for sql, params in conn.calls if sql.startswith(f"DELETE FROM {table}")]


@pytest.mark.regression
class TestResetOrphanCleanup:
    def test_all_markets_deletes_report_scoped_by_user_only(self, client):
        resp, conn = _reset(client, "ALL")
        assert resp.status_code == 200
        calls = _delete_calls_for(conn, "paper_trade_postmortem_report")
        assert len(calls) == 1
        assert calls[0][1] == ("user-aaa",)

    def test_all_markets_deletes_outbox_scoped_by_user_only(self, client):
        resp, conn = _reset(client, "ALL")
        assert resp.status_code == 200
        calls = _delete_calls_for(conn, "paper_trade_postmortem_outbox")
        assert len(calls) == 1
        assert calls[0][1] == ("user-aaa",)

    def test_all_markets_deletes_exit_snapshot_scoped_by_user_only(self, client):
        resp, conn = _reset(client, "ALL")
        assert resp.status_code == 200
        calls = _delete_calls_for(conn, "paper_trade_exit_snapshot")
        assert len(calls) == 1
        assert calls[0][1] == ("user-aaa",)

    def test_market_specific_deletes_report_scoped_by_user_and_market(self, client):
        resp, conn = _reset(client, "US")
        assert resp.status_code == 200
        calls = _delete_calls_for(conn, "paper_trade_postmortem_report")
        assert len(calls) == 1
        assert calls[0][1] == ("user-aaa", "US")

    def test_market_specific_deletes_exit_snapshot_scoped_by_user_and_market(self, client):
        resp, conn = _reset(client, "IN")
        assert resp.status_code == 200
        calls = _delete_calls_for(conn, "paper_trade_exit_snapshot")
        assert len(calls) == 1
        assert calls[0][1] == ("user-aaa", "IN")

    def test_market_specific_outbox_delete_uses_subquery_scoped_by_market(self, client):
        """paper_trade_postmortem_outbox has no market column — the
        market-specific branch must filter it via a subquery against
        paper_trades, not a direct market column reference."""
        resp, conn = _reset(client, "US")
        assert resp.status_code == 200
        calls = _delete_calls_for(conn, "paper_trade_postmortem_outbox")
        assert len(calls) == 1
        sql, params = calls[0]
        assert "SELECT id FROM paper_trades" in sql
        assert params == ("user-aaa", "user-aaa", "US")

    def test_market_specific_outbox_delete_runs_before_trades_delete(self, client):
        """The outbox subquery depends on paper_trades rows for this
        market still existing — it must execute before the paper_trades
        DELETE removes them."""
        resp, conn = _reset(client, "US")
        assert resp.status_code == 200
        sqls = [sql for sql, _ in conn.calls]
        outbox_idx = next(i for i, s in enumerate(sqls) if s.startswith("DELETE FROM paper_trade_postmortem_outbox"))
        trades_idx = next(i for i, s in enumerate(sqls) if s.startswith("DELETE FROM paper_trades"))
        assert outbox_idx < trades_idx

    def test_all_markets_new_table_deletes_run_before_trades_delete(self, client):
        resp, conn = _reset(client, "ALL")
        assert resp.status_code == 200
        sqls = [sql for sql, _ in conn.calls]
        trades_idx = next(i for i, s in enumerate(sqls) if s.startswith("DELETE FROM paper_trades"))
        for table in ("paper_trade_postmortem_report", "paper_trade_postmortem_outbox", "paper_trade_exit_snapshot"):
            table_idx = next(i for i, s in enumerate(sqls) if s.startswith(f"DELETE FROM {table}"))
            assert table_idx < trades_idx
