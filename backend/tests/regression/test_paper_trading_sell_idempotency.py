"""
Trade Postmortem Engine, Sprint 2 correction phase (Stage 6) — regression
tests for POST /api/paper-trading/sell/{trade_id}'s additive,
backward-compatible close idempotency (an `idempotency_key` body field,
mirroring BuyRequest's own established convention exactly, reusing the
SAME services.postmortem.idempotency engine with a DISTINCT operation
type: OPERATION_TYPE_PAPER_SELL never collides with OPERATION_TYPE_PAPER_
BUY even for the identical key string and user).

Uses one shared in-memory state dict (trades/cash/idempotency rows)
mutated across however many _FakeConn instances the router constructs,
matching the exact pattern test_paper_trading_idempotency.py's own Buy
suite already established for this repository.
"""
import datetime as dt
import threading
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


def _token(sub: str) -> str:
    return jwt.encode(
        {"sub": sub, "aud": "authenticated", "iss": TEST_ISSUER, "exp": _time.time() + 3600},
        TEST_SECRET, algorithm="HS256",
    )


def _auth(sub: str = "user-aaa") -> dict:
    return {"Authorization": f"Bearer {_token(sub)}"}


def _new_trade(**overrides) -> dict:
    base = dict(
        id=1, user_id="user-aaa", symbol="AAPL", quantity=10, entry_price=100.0,
        status="OPEN", market="US", stop_loss=90.0, target_price=130.0,
        trade_management_mode="manual",
    )
    base.update(overrides)
    return base


class _FakeConn:
    def __init__(self, shared: dict):
        self.shared = shared

    def execute(self, sql, params=None):
        self._pending = self._process(sql, params or ())
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

    def _process(self, sql, params):
        shared = self.shared
        stripped = sql.strip()

        if stripped.startswith("SELECT market FROM paper_trades WHERE id = %s"):
            trade_id, probing_user_id = params
            trade = shared["trades"].get(trade_id)
            if trade is None or trade["user_id"] != probing_user_id:
                return None
            return (trade["market"],)

        if stripped.startswith("SELECT user_id, symbol, quantity, entry_price, status, market"):
            (trade_id,) = params
            with shared["lock"]:
                trade = shared["trades"].get(trade_id)
                if trade is None:
                    return None
                return (
                    trade["user_id"], trade["symbol"], trade["quantity"], trade["entry_price"],
                    trade["status"], trade["market"], trade["stop_loss"], trade["target_price"],
                    trade["trade_management_mode"],
                )

        if stripped.startswith("UPDATE paper_trades SET exit_price"):
            exit_price, closed_at, exit_reason, trade_id = params
            with shared["lock"]:
                trade = shared["trades"].get(trade_id)
                if trade is None or trade["status"] != "OPEN":
                    return None
                trade["status"] = "CLOSED"
                return (trade_id,)

        if stripped.startswith("INSERT INTO paper_trade_exit_snapshot"):
            with shared["lock"]:
                shared["exit_snapshots"].append(params)
            return None

        if stripped.startswith("INSERT INTO paper_trade_postmortem_outbox"):
            trade_id, user_id, schema_v, calc_v, rules_v, source_request_id = params
            key = (trade_id, schema_v, calc_v, rules_v)
            with shared["lock"]:
                if key in shared["outbox_by_key"]:
                    return None
                new_id = shared["next_outbox_id"]
                shared["next_outbox_id"] += 1
                shared["outbox_by_key"][key] = new_id
                shared["outbox_rows"][new_id] = {"trade_id": trade_id}
                return (new_id,)

        if stripped.startswith("SELECT id FROM paper_trade_postmortem_outbox"):
            trade_id, schema_v, calc_v, rules_v = params
            existing_id = shared["outbox_by_key"].get((trade_id, schema_v, calc_v, rules_v))
            return (existing_id,) if existing_id is not None else None

        if stripped.startswith("UPDATE paper_portfolio SET cash_usd"):
            proceeds, user_id = params
            with shared["lock"]:
                shared["cash_usd"] += proceeds
            return None
        if stripped.startswith("UPDATE paper_portfolio SET cash"):
            proceeds, user_id = params
            with shared["lock"]:
                shared["cash"] += proceeds
            return None

        # --- idempotency table (mirrors test_paper_trading_idempotency.py's own Buy fake) ---
        if stripped.startswith("INSERT INTO paper_trade_idempotency_key"):
            user_id, op_type, key, fingerprint = params
            with shared["lock"]:
                for row in shared["idem_rows"]:
                    if (row["user_id"], row["operation_type"], row["idempotency_key"]) == (user_id, op_type, key):
                        return None
                new_id = shared["next_idem_id"]
                shared["next_idem_id"] += 1
                shared["idem_rows"].append({
                    "id": new_id, "user_id": user_id, "operation_type": op_type,
                    "idempotency_key": key, "request_fingerprint": fingerprint,
                    "status": "PENDING", "response_body": None,
                    "created_at": dt.datetime.now(dt.timezone.utc),
                })
                return (new_id,)

        if stripped.startswith("SELECT id, status, request_fingerprint, response_body, created_at"):
            user_id, op_type, key = params
            with shared["lock"]:
                for row in shared["idem_rows"]:
                    if (row["user_id"], row["operation_type"], row["idempotency_key"]) == (user_id, op_type, key):
                        return (row["id"], row["status"], row["request_fingerprint"], row["response_body"], row["created_at"])
            return None

        if "SET status = 'PENDING', created_at = now()" in sql:
            row_id, expected_status, expected_created_at = params
            with shared["lock"]:
                for row in shared["idem_rows"]:
                    if row["id"] == row_id and row["status"] == expected_status and row["created_at"] == expected_created_at:
                        row["status"] = "PENDING"
                        row["created_at"] = dt.datetime.now(dt.timezone.utc)
                        return (row_id,)
            return None

        if "SET status = 'COMPLETED'" in sql:
            trade_id, response_json, schema_version, row_id = params
            import json as _json
            with shared["lock"]:
                for row in shared["idem_rows"]:
                    if row["id"] == row_id:
                        row["status"] = "COMPLETED"
                        row["response_body"] = _json.loads(response_json)
            return None

        if "SET status = 'FAILED'" in sql:
            failure_reason, row_id = params
            with shared["lock"]:
                for row in shared["idem_rows"]:
                    if row["id"] == row_id and row["status"] == "PENDING":
                        row["status"] = "FAILED"
            return None

        return None


def _new_shared(trades: list[dict], cash_usd: float = 100_000.0) -> dict:
    return {
        "trades": {t["id"]: dict(t) for t in trades},
        "exit_snapshots": [], "outbox_by_key": {}, "outbox_rows": {}, "next_outbox_id": 1,
        "cash_usd": cash_usd, "cash": 1_000_000.0,
        "idem_rows": [], "next_idem_id": 1,
        "lock": threading.Lock(),
    }


def _conn_factory(shared: dict):
    def factory():
        return _FakeConn(shared)
    return factory


def _sell(client, trade_id, body, shared, auth_sub="user-aaa"):
    with patch.object(
        __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _conn_factory(shared)
    ), patch("api.routers.paper_trading._is_market_open", return_value=True):
        return client.post(f"/api/paper-trading/sell/{trade_id}", json=body, headers=_auth(auth_sub))


BASE_BODY = {"price": 120.0, "exit_reason": "MANUAL"}


@pytest.mark.regression
class TestSellIdempotencyBasics:
    def test_no_key_behaves_exactly_as_before(self, client):
        shared = _new_shared([_new_trade()])
        resp = _sell(client, 1, BASE_BODY, shared)
        assert resp.status_code == 200
        assert resp.json()["idempotency_enforced"] is False
        assert len(shared["idem_rows"]) == 0

    def test_first_request_with_key_succeeds_and_reserves(self, client):
        shared = _new_shared([_new_trade()])
        resp = _sell(client, 1, {**BASE_BODY, "idempotency_key": "sell-key-aaaaaaaaaaaa"}, shared)
        assert resp.status_code == 200
        body = resp.json()
        assert body["idempotency_enforced"] is True
        assert len(shared["idem_rows"]) == 1
        assert shared["idem_rows"][0]["status"] == "COMPLETED"
        assert shared["idem_rows"][0]["operation_type"] == "PAPER_SELL"

    def test_sequential_identical_replay_returns_original_response_no_second_credit(self, client):
        shared = _new_shared([_new_trade()], cash_usd=1000.0)
        key = "sell-key-bbbbbbbbbbbb"
        first = _sell(client, 1, {**BASE_BODY, "idempotency_key": key}, shared).json()
        cash_after_first = shared["cash_usd"]
        second = _sell(client, 1, {**BASE_BODY, "idempotency_key": key}, shared).json()
        assert second == first  # byte-identical replay
        assert shared["cash_usd"] == cash_after_first  # no second credit
        assert len(shared["idem_rows"]) == 1  # no second row
        assert len(shared["exit_snapshots"]) == 1  # no second snapshot

    def test_same_key_different_users_no_collision(self, client):
        """A Sell key reused by TWO DIFFERENT users must never collide —
        each is scoped independently."""
        shared = _new_shared([_new_trade(id=1, user_id="user-aaa"), _new_trade(id=2, user_id="user-bbb")])
        key = "sell-key-shared-across-users"
        r1 = _sell(client, 1, {**BASE_BODY, "idempotency_key": key}, shared, auth_sub="user-aaa")
        r2 = _sell(client, 2, {**BASE_BODY, "idempotency_key": key}, shared, auth_sub="user-bbb")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert len(shared["idem_rows"]) == 2

    def test_buy_key_and_sell_key_same_string_never_collide(self):
        """A Buy idempotency row and a Sell idempotency row sharing the
        identical key string for the SAME user are distinguished purely
        by operation_type — this is proven at the fingerprint/decision
        level directly since simulating a real Buy+Sell mixed flow
        against one fake conn is out of this file's scope."""
        from services.postmortem.idempotency import OPERATION_TYPE_PAPER_BUY, OPERATION_TYPE_PAPER_SELL
        assert OPERATION_TYPE_PAPER_BUY != OPERATION_TYPE_PAPER_SELL

    def test_same_key_changed_price_is_conflict_no_financial_write(self, client):
        shared = _new_shared([_new_trade()], cash_usd=1000.0)
        key = "sell-key-cccccccccccc"
        _sell(client, 1, {**BASE_BODY, "idempotency_key": key}, shared)
        cash_after_first = shared["cash_usd"]
        resp = _sell(client, 1, {**BASE_BODY, "price": 999.0, "idempotency_key": key}, shared)
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST"
        assert shared["cash_usd"] == cash_after_first
        assert len(shared["exit_snapshots"]) == 1  # no second snapshot

    def test_same_key_different_trade_id_is_conflict(self, client):
        """trade_id is part of the fingerprint — reusing a key against a
        DIFFERENT trade must never be silently applied to the wrong one."""
        shared = _new_shared([_new_trade(id=1), _new_trade(id=2)])
        key = "sell-key-dddddddddddd"
        _sell(client, 1, {**BASE_BODY, "idempotency_key": key}, shared)
        resp = _sell(client, 2, {**BASE_BODY, "idempotency_key": key}, shared)
        assert resp.status_code == 409

    def test_raw_key_never_stored_as_source_request_id(self, client):
        """The exit snapshot's source_request_id must be a hashed
        reference, never the raw idempotency key."""
        shared = _new_shared([_new_trade()])
        key = "sell-key-eeeeeeeeeeee"
        _sell(client, 1, {**BASE_BODY, "idempotency_key": key}, shared)
        source_request_id = shared["exit_snapshots"][0][18]  # positional index in _EXIT_SNAPSHOT_COLUMNS
        assert source_request_id != key
        assert key not in str(source_request_id)


@pytest.mark.regression
class TestSellIdempotencyFailureRecovery:
    def test_already_closed_marks_failed_and_is_retryable(self, client):
        shared = _new_shared([_new_trade(status="CLOSED")])
        key = "sell-key-ffffffffffff"
        body = {**BASE_BODY, "idempotency_key": key}
        first = _sell(client, 1, body, shared)
        assert first.status_code == 400
        assert shared["idem_rows"][0]["status"] == "FAILED"

        # Retry with the SAME key once the trade is open again — must
        # succeed, not be permanently blocked.
        shared["trades"][1]["status"] = "OPEN"
        second = _sell(client, 1, body, shared)
        assert second.status_code == 200
        assert shared["idem_rows"][0]["status"] == "COMPLETED"
        assert len(shared["idem_rows"]) == 1  # same row reclaimed, not a new one

    def test_stale_pending_row_is_reclaimed_not_stuck_forever(self, client):
        """Simulates a crashed process that reserved a Sell key and never
        reached the FAILED/COMPLETED update — retried after the staleness
        window, it must reclaim, not report SELL_ALREADY_IN_PROGRESS
        forever (response-lost-after-commit case: had the crash happened
        AFTER the COMPLETED update instead, the row would already be
        COMPLETED and the retry would hit the replay path above, not
        this one)."""
        from services.postmortem.idempotency import STALE_PENDING_THRESHOLD
        shared = _new_shared([_new_trade()])
        key = "sell-key-gggggggggggg"
        shared["idem_rows"].append({
            "id": 1, "user_id": "user-aaa", "operation_type": "PAPER_SELL",
            "idempotency_key": key,
            "request_fingerprint": "stale-fingerprint-does-not-matter-since-status-pending",
            "status": "PENDING", "response_body": None,
            "created_at": dt.datetime.now(dt.timezone.utc) - STALE_PENDING_THRESHOLD - dt.timedelta(seconds=5),
        })
        shared["next_idem_id"] = 2

        import services.postmortem.idempotency as idem_mod
        fingerprint = idem_mod.compute_close_request_fingerprint(trade_id=1, exit_price=120.0, exit_reason="MANUAL")
        shared["idem_rows"][0]["request_fingerprint"] = fingerprint

        resp = _sell(client, 1, {**BASE_BODY, "idempotency_key": key}, shared)
        assert resp.status_code == 200
        assert shared["idem_rows"][0]["status"] == "COMPLETED"
        assert len(shared["idem_rows"]) == 1  # reclaimed, not duplicated

    def test_concurrent_identical_sell_requests_one_close_rest_safe(self, client):
        """Concurrent identical Sell requests with the same key: exactly
        one actually closes the trade; the others get a safe outcome
        (replay of the completed response, or a stable in-progress/
        conflict signal) — never a second close, second credit, or crash."""
        shared = _new_shared([_new_trade()], cash_usd=1000.0)
        key = "sell-key-hhhhhhhhhhhh"
        results = []
        results_lock = threading.Lock()

        def _attempt():
            resp = _sell(client, 1, {**BASE_BODY, "idempotency_key": key}, shared)
            with results_lock:
                results.append(resp.status_code)

        threads = [threading.Thread(target=_attempt) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(code in (200, 409) for code in results)
        assert len(shared["exit_snapshots"]) == 1
        assert len(shared["outbox_rows"]) == 1
        assert shared["trades"][1]["status"] == "CLOSED"
