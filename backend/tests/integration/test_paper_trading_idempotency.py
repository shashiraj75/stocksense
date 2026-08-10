"""
Migration-verification hardening gate, Part 7 — Buy idempotency
integration tests.

No live PostgreSQL is available in this environment (confirmed absent:
docker, psql, pg_ctl, initdb; nothing on :5432 — see the hardening gate
report). These tests instead exercise the REAL reservation/replay/
conflict/reclaim logic in api/routers/paper_trading.py end-to-end through
the actual HTTP endpoint, against a hand-written in-memory fake that
implements just enough of `paper_portfolio`/`paper_trades`/
`paper_trade_entry_snapshot`/`paper_trade_idempotency_key` semantics
(including the INSERT-ON-CONFLICT-DO-NOTHING uniqueness behavior a real
unique index would enforce) to prove the application-level state machine
is correct. This is NOT a substitute for a live-Postgres concurrency proof
— it proves this module's own logic is internally consistent, not that
PostgreSQL's actual unique-index/MVCC behavior matches these assumptions.
"""
import datetime as dt
import json as _json
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


def _new_shared_db(cash: float = 1_000_000.0, cash_usd: float = 100_000.0) -> dict:
    # BASE_BODY uses market="US", which debits the cash_usd column — tests
    # that need to simulate insufficient funds for BASE_BODY must set
    # cash_usd, not cash (the IN-side ledger, unused by these tests).
    return {
        "cash": cash, "cash_usd": cash_usd,
        "idem_rows": [], "next_idem_id": 1, "next_trade_id": 1,
        "lock": threading.Lock(),
    }


class _FakeConn:
    """Simulates just enough of the real schema to exercise
    _resolve_idempotency_reservation and paper_buy's transaction body
    end-to-end. Multiple _FakeConn instances share one `shared` dict +
    lock, mimicking multiple pooled connections against one real database
    — this is what makes the threading concurrency test below meaningful:
    each thread gets its own _FakeConn (like a real pooled connection) but
    all mutate the same underlying state under the same lock a real
    UNIQUE INDEX would enforce atomically.
    """

    def __init__(self, shared: dict):
        self.shared = shared
        self.calls = []
        self._pending_result = None

    def execute(self, sql, params=None):
        # Side effects happen HERE, not in fetchone() — the real router
        # code calls .execute(...) without chaining .fetchone() for
        # statements with no RETURNING clause (e.g. the COMPLETED/FAILED
        # status updates), so any state change gated behind fetchone()
        # would silently never happen for those statements.
        self.calls.append((sql, params))
        self._pending_result = self._process(sql, params or ())
        return self

    def fetchone(self):
        return self._pending_result

    def _process(self, sql, params):
        shared = self.shared
        stripped = sql.strip()

        if "SELECT cash, cash_usd, email_notifications_enabled" in sql:
            return (shared["cash"], shared["cash_usd"], True)
        if stripped.startswith("INSERT INTO paper_portfolio"):
            return None
        if stripped.startswith("UPDATE paper_portfolio SET cash"):
            cost = params[0]
            col = "cash" if "SET cash =" in sql else "cash_usd"
            with shared["lock"]:
                if shared[col] >= cost:
                    shared[col] -= cost
                    return (shared[col],)
                return None
        if stripped.startswith("SELECT cash FROM paper_portfolio") or stripped.startswith("SELECT cash_usd FROM paper_portfolio"):
            col = "cash" if "cash FROM" in sql else "cash_usd"
            return (shared[col],)

        if stripped.startswith("INSERT INTO paper_trade_idempotency_key"):
            user_id, op_type, key, fingerprint = params
            with shared["lock"]:
                for row in shared["idem_rows"]:
                    if (row["user_id"], row["operation_type"], row["idempotency_key"]) == (user_id, op_type, key):
                        return None  # ON CONFLICT DO NOTHING
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
            # Real CAS now pins all three of id/status/created_at (Real
            # PostgreSQL Verification, CI Execution phase — a status-only
            # match is a no-op compare for the stale-PENDING case, since
            # the reclaimed-to value is the same as the reclaimed-from
            # value, letting two concurrent reclaimers both win).
            row_id, expected_status, expected_created_at = params
            with shared["lock"]:
                for row in shared["idem_rows"]:
                    if (
                        row["id"] == row_id
                        and row["status"] == expected_status
                        and row["created_at"] == expected_created_at
                    ):
                        row["status"] = "PENDING"
                        row["created_at"] = dt.datetime.now(dt.timezone.utc)
                        return (row_id,)
            return None

        if "SET status = 'COMPLETED'" in sql:
            trade_id, response_json, schema_version, row_id = params
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

        if stripped.startswith("INSERT INTO paper_trades"):
            with shared["lock"]:
                new_trade_id = shared["next_trade_id"]
                shared["next_trade_id"] += 1
            return (new_trade_id,)

        if stripped.startswith("INSERT INTO paper_trade_entry_snapshot"):
            return None

        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @contextmanager
    def transaction(self):
        yield self


def _conn_factory(shared: dict):
    def factory():
        return _FakeConn(shared)
    return factory


def _buy(client, body, shared, auth_sub="user-aaa"):
    with patch.object(
        __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _conn_factory(shared)
    ), patch("api.routers.paper_trading._is_market_open", return_value=True):
        return client.post("/api/paper-trading/buy", json=body, headers=_auth(auth_sub))


BASE_BODY = {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0}


@pytest.mark.integration
class TestIdempotencyBasics:
    def test_missing_key_is_rejected_deterministically_no_trade_no_debit(self, client):
        # Owner-authorized hardening (post-incident) — idempotency_key was
        # previously optional and this test asserted the old fail-open
        # behavior ("no key behaves exactly as before": 200,
        # idempotency_enforced=False, no dedup guarantee at all). That
        # design is exactly what let the TCS double-Buy incident happen: a
        # remounted modal could fire a second Buy with no key and no
        # server-side protection. The contract is now the opposite: a Buy
        # request missing idempotency_key is rejected at the Pydantic
        # request-boundary (422) BEFORE paper_buy() runs at all — zero
        # trade row inserted, zero cash debited, zero idempotency
        # reservation row created.
        shared = _new_shared_db()
        cash_before = shared["cash_usd"]
        resp = _buy(client, BASE_BODY, shared)
        assert resp.status_code == 422
        assert shared["next_trade_id"] == 1  # no trade row was ever inserted
        assert len(shared["idem_rows"]) == 0
        assert shared["cash_usd"] == cash_before  # no debit

    def test_first_request_with_key_succeeds_and_reserves(self, client):
        shared = _new_shared_db()
        resp = _buy(client, {**BASE_BODY, "idempotency_key": "key-aaaaaaaaaaaa"}, shared)
        assert resp.status_code == 200
        body = resp.json()
        assert body["idempotency_enforced"] is True
        assert len(shared["idem_rows"]) == 1
        assert shared["idem_rows"][0]["status"] == "COMPLETED"

    def test_sequential_identical_replay_returns_original_trade_no_new_debit(self, client):
        shared = _new_shared_db()
        body = {**BASE_BODY, "idempotency_key": "key-bbbbbbbbbbbb"}
        first = _buy(client, body, shared).json()
        cash_after_first = shared["cash_usd"]  # BASE_BODY is market="US" — debits cash_usd, not cash
        second = _buy(client, body, shared).json()
        assert second == first  # byte-identical replay
        assert shared["cash_usd"] == cash_after_first  # no second debit
        assert len(shared["idem_rows"]) == 1  # no second row

    def test_different_keys_create_separate_trades(self, client):
        shared = _new_shared_db()
        r1 = _buy(client, {**BASE_BODY, "idempotency_key": "key-cccccccccccc"}, shared).json()
        r2 = _buy(client, {**BASE_BODY, "idempotency_key": "key-dddddddddddd"}, shared).json()
        assert r1["trade_id"] != r2["trade_id"]
        assert len(shared["idem_rows"]) == 2

    def test_same_key_different_user_is_independent(self, client):
        shared = _new_shared_db()
        key = "key-eeeeeeeeeeee"
        r1 = _buy(client, {**BASE_BODY, "idempotency_key": key}, shared, auth_sub="user-aaa").json()
        r2 = _buy(client, {**BASE_BODY, "idempotency_key": key}, shared, auth_sub="user-bbb").json()
        assert r1["trade_id"] != r2["trade_id"]
        assert len(shared["idem_rows"]) == 2

    def test_same_key_changed_quantity_is_conflict(self, client):
        shared = _new_shared_db()
        key = "key-ffffffffffff"
        _buy(client, {**BASE_BODY, "idempotency_key": key}, shared)
        resp = _buy(client, {**BASE_BODY, "quantity": 2, "idempotency_key": key}, shared)
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST"

    def test_same_key_changed_symbol_is_conflict(self, client):
        shared = _new_shared_db()
        key = "key-gggggggggggg"
        _buy(client, {**BASE_BODY, "idempotency_key": key}, shared)
        resp = _buy(client, {**BASE_BODY, "symbol": "MSFT", "idempotency_key": key}, shared)
        assert resp.status_code == 409

    def test_same_key_changed_stop_loss_is_conflict(self, client):
        shared = _new_shared_db()
        key = "key-hhhhhhhhhhhh"
        _buy(client, {**BASE_BODY, "idempotency_key": key}, shared)
        resp = _buy(client, {**BASE_BODY, "stop_loss": 90.0, "idempotency_key": key}, shared)
        assert resp.status_code == 409

    def test_conflict_does_not_create_a_second_trade_or_debit(self, client):
        shared = _new_shared_db()
        key = "key-iiiiiiiiiiii"
        _buy(client, {**BASE_BODY, "idempotency_key": key}, shared)
        cash_after_first = shared["cash_usd"]
        _buy(client, {**BASE_BODY, "quantity": 99, "idempotency_key": key}, shared)
        assert shared["cash_usd"] == cash_after_first
        assert shared["next_trade_id"] == 2  # only one trade was ever created


@pytest.mark.integration
class TestIdempotencyFailureRecovery:
    def test_insufficient_funds_marks_failed_and_is_retryable(self, client):
        shared = _new_shared_db(cash_usd=50.0)  # not enough for the $101 buy
        key = "key-jjjjjjjjjjjj"
        body = {**BASE_BODY, "idempotency_key": key}
        first = _buy(client, body, shared)
        assert first.status_code == 400
        assert shared["idem_rows"][0]["status"] == "FAILED"

        # Retry with the SAME key after funds become available — must
        # succeed, not be permanently blocked by the earlier failure.
        shared["cash_usd"] = 1_000_000.0
        second = _buy(client, body, shared)
        assert second.status_code == 200
        assert shared["idem_rows"][0]["status"] == "COMPLETED"
        assert len(shared["idem_rows"]) == 1  # same row reclaimed, not a new one

    def test_stale_pending_row_is_reclaimed_not_stuck_forever(self, client):
        """Simulates a crashed process that reserved a key and never
        reached the FAILED/COMPLETED update — the row is stuck PENDING.
        A retry after the staleness window must reclaim it, not report
        BUY_ALREADY_IN_PROGRESS forever."""
        from services.postmortem.idempotency import STALE_PENDING_THRESHOLD

        shared = _new_shared_db()
        key = "key-kkkkkkkkkkkk"
        shared["idem_rows"].append({
            "id": 1, "user_id": "user-aaa", "operation_type": "PAPER_BUY",
            "idempotency_key": key,
            "request_fingerprint": "will-be-overwritten-by-real-computation",
            "status": "PENDING", "response_body": None,
            "created_at": dt.datetime.now(dt.timezone.utc) - STALE_PENDING_THRESHOLD - dt.timedelta(seconds=5),
        })
        shared["next_idem_id"] = 2
        # Compute the real fingerprint the request will produce, so the
        # stale row's fingerprint matches (this test is about staleness
        # reclaim, not fingerprint-mismatch conflict).
        from services.postmortem.idempotency import compute_request_fingerprint
        from services.postmortem.entry_snapshot import SNAPSHOT_SCHEMA_VERSION
        shared["idem_rows"][0]["request_fingerprint"] = compute_request_fingerprint(
            market="US", symbol="AAPL", quantity=1, price=101.0, stop_loss=None,
            target_price=None, trade_management_mode="manual", evidence_source="MANUAL",
            entry_evidence_schema_version=SNAPSHOT_SCHEMA_VERSION,
        )

        resp = _buy(client, {**BASE_BODY, "idempotency_key": key}, shared)
        assert resp.status_code == 200
        assert shared["idem_rows"][0]["status"] == "COMPLETED"
        assert len(shared["idem_rows"]) == 1  # reclaimed, not duplicated

    def test_fresh_pending_row_returns_stable_in_progress_conflict(self, client):
        """A genuinely fresh (not stale) PENDING row — simulating a request
        that's still actually processing — must return the documented
        stable in-progress response, never let a second request proceed to
        the financial transaction."""
        from services.postmortem.idempotency import compute_request_fingerprint
        from services.postmortem.entry_snapshot import SNAPSHOT_SCHEMA_VERSION

        shared = _new_shared_db()
        key = "key-llllllllllll"
        fingerprint = compute_request_fingerprint(
            market="US", symbol="AAPL", quantity=1, price=101.0, stop_loss=None,
            target_price=None, trade_management_mode="manual", evidence_source="MANUAL",
            entry_evidence_schema_version=SNAPSHOT_SCHEMA_VERSION,
        )
        shared["idem_rows"].append({
            "id": 1, "user_id": "user-aaa", "operation_type": "PAPER_BUY",
            "idempotency_key": key, "request_fingerprint": fingerprint,
            "status": "PENDING", "response_body": None,
            "created_at": dt.datetime.now(dt.timezone.utc),  # fresh, not stale
        })
        shared["next_idem_id"] = 2

        resp = _buy(client, {**BASE_BODY, "idempotency_key": key}, shared)
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "BUY_ALREADY_IN_PROGRESS"
        assert shared["next_trade_id"] == 1  # no trade was ever created


@pytest.mark.integration
class TestIdempotencyConcurrency:
    def test_concurrent_identical_requests_create_exactly_one_trade_and_one_debit(self, client):
        """Real Python threads, sharing one in-memory fake 'database' under
        one lock (mimicking a real UNIQUE INDEX's atomicity) — genuinely
        exercises the reservation race, not just sequential calls."""
        shared = _new_shared_db()
        key = "key-mmmmmmmmmmmm"
        body = {**BASE_BODY, "idempotency_key": key}
        results = []
        results_lock = threading.Lock()

        def _worker():
            resp = _buy(client, body, shared)
            with results_lock:
                results.append(resp)

        threads = [threading.Thread(target=_worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        status_codes = sorted(r.status_code for r in results)
        # Every request either got the successful (replayed or original)
        # result, or the stable in-progress conflict if it lost the poll
        # window entirely — never a second 200 with a different trade_id.
        successes = [r for r in results if r.status_code == 200]
        assert len(successes) >= 1
        trade_ids = {r.json()["trade_id"] for r in successes}
        assert trade_ids == {list(trade_ids)[0]}  # every success is the SAME trade_id
        assert shared["next_trade_id"] == 2  # exactly one trade was ever created
        assert len(shared["idem_rows"]) == 1
