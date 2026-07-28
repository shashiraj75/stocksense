"""
Trade Postmortem Engine, Sprint 2 — regression tests proving
POST /api/paper-trading/sell/{trade_id} now delegates to
services.postmortem.close_service.close_paper_trade for the trade UPDATE,
exit snapshot, and outbox record, atomically with the cash credit, and
maps close_service's typed exceptions to the exact HTTP contract the
endpoint already had before this sprint (404/403/400 with the same
messages).

Uses the same _conn-patching + in-memory fake-connection pattern as
test_paper_trading_idempotency.py. Best-effort post-commit report
generation (_attempt_best_effort_generation) is exercised for real but
any SQL it issues beyond what this fake models is a harmless no-op —
that function already catches every exception itself (Stage 8's own
"generation failure must never affect this endpoint's response"
contract), so an unmodeled generation-side query never fails these tests;
it only proves generation doesn't corrupt the close response.
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
        self.calls = []
        self._pending = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self._pending = self._process(sql, params or ())
        return self

    def fetchone(self):
        return self._pending

    def _process(self, sql, params):
        shared = self.shared
        stripped = sql.strip()

        if stripped.startswith("SELECT market FROM paper_trades WHERE id = %s"):
            # Trade Postmortem Engine, Sprint 2 correction phase — this
            # probe is now user-scoped (WHERE id = %s AND user_id = %s);
            # mirror that filter here so a cross-user probe genuinely
            # returns no row, matching real-database behavior, rather
            # than leaking the trade's market to a non-owning caller.
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
                trade["exit_price"] = exit_price
                trade["closed_at"] = closed_at
                trade["exit_reason"] = exit_reason
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
                shared["outbox_rows"][new_id] = {"trade_id": trade_id, "user_id": user_id, "status": "PENDING"}
                return (new_id,)

        if stripped.startswith("SELECT id FROM paper_trade_postmortem_outbox"):
            trade_id, schema_v, calc_v, rules_v = params
            key = (trade_id, schema_v, calc_v, rules_v)
            existing_id = shared["outbox_by_key"].get(key)
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

        # Best-effort post-commit generation issues further SELECTs this
        # fake doesn't model (entry snapshot, exit snapshot presence,
        # persisted report insert) — returning None for all of them is
        # harmless: _attempt_best_effort_generation already treats any
        # exception in that path as non-fatal to the close response.
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @contextmanager
    def transaction(self):
        yield self


def _new_shared(trades: list[dict], cash_usd: float = 100_000.0) -> dict:
    return {
        "trades": {t["id"]: dict(t) for t in trades},
        "exit_snapshots": [], "outbox_by_key": {}, "outbox_rows": {}, "next_outbox_id": 1,
        "cash_usd": cash_usd, "cash": 1_000_000.0,
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


@pytest.mark.regression
class TestSellDelegatesToCloseService:
    def test_profitable_manual_sell(self, client):
        shared = _new_shared([_new_trade()])
        resp = _sell(client, 1, {"price": 120.0, "exit_reason": "MANUAL"}, shared)
        assert resp.status_code == 200
        body = resp.json()
        assert body["pnl"] == pytest.approx(200.0)
        assert body["proceeds"] == pytest.approx(1200.0)
        assert shared["trades"][1]["status"] == "CLOSED"
        assert len(shared["exit_snapshots"]) == 1
        assert len(shared["outbox_rows"]) == 1

    def test_cash_credited_atomically_with_close(self, client):
        shared = _new_shared([_new_trade()], cash_usd=1000.0)
        resp = _sell(client, 1, {"price": 120.0, "exit_reason": "MANUAL"}, shared)
        assert resp.status_code == 200
        assert shared["cash_usd"] == pytest.approx(1000.0 + 1200.0)

    def test_trade_not_found_404(self, client):
        shared = _new_shared([])
        resp = _sell(client, 999, {"price": 100.0, "exit_reason": "MANUAL"}, shared)
        assert resp.status_code == 404

    def test_cross_user_close_identical_404(self, client):
        """Trade Postmortem Engine, Sprint 2 correction phase — the
        market-open preflight is now user-scoped, so a trade owned by
        another user returns the SAME 404 as a nonexistent trade_id,
        never a 403 that would confirm the ID exists. This intentionally
        changed this endpoint's prior contract (a distinguishable 403
        'Not your trade') in favor of not leaking trade existence."""
        shared = _new_shared([_new_trade(user_id="owner")])
        not_found = _sell(client, 999, {"price": 100.0, "exit_reason": "MANUAL"}, shared)
        cross_user = _sell(client, 1, {"price": 100.0, "exit_reason": "MANUAL"}, shared, auth_sub="attacker")
        assert cross_user.status_code == 404
        assert cross_user.json() == not_found.json()

    def test_already_closed_trade_400(self, client):
        shared = _new_shared([_new_trade(status="CLOSED")])
        resp = _sell(client, 1, {"price": 100.0, "exit_reason": "MANUAL"}, shared)
        assert resp.status_code == 400
        assert "already closed" in resp.json()["detail"].lower()

    def test_invalid_price_400_before_any_close_service_call(self, client):
        shared = _new_shared([_new_trade()])
        resp = _sell(client, 1, {"price": 0, "exit_reason": "MANUAL"}, shared)
        assert resp.status_code == 400
        assert shared["trades"][1]["status"] == "OPEN"

    def test_market_closed_400(self, client):
        shared = _new_shared([_new_trade()])
        with patch.object(
            __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _conn_factory(shared)
        ), patch("api.routers.paper_trading._is_market_open", return_value=False):
            resp = client.post(
                "/api/paper-trading/sell/1", json={"price": 100.0, "exit_reason": "MANUAL"}, headers=_auth()
            )
        assert resp.status_code == 400
        assert shared["trades"][1]["status"] == "OPEN"

    def test_duplicate_sell_second_call_400_no_double_credit(self, client):
        shared = _new_shared([_new_trade()], cash_usd=1000.0)
        first = _sell(client, 1, {"price": 120.0, "exit_reason": "MANUAL"}, shared)
        assert first.status_code == 200
        cash_after_first = shared["cash_usd"]
        second = _sell(client, 1, {"price": 125.0, "exit_reason": "MANUAL"}, shared)
        assert second.status_code == 400
        assert shared["cash_usd"] == cash_after_first
        assert len(shared["exit_snapshots"]) == 1
        assert len(shared["outbox_rows"]) == 1

    def test_stop_loss_and_target_hit_exit_reasons_accepted(self, client):
        shared = _new_shared([_new_trade()])
        resp = _sell(client, 1, {"price": 90.0, "exit_reason": "STOP_LOSS"}, shared)
        assert resp.status_code == 200

    def test_missing_legacy_entry_price_yields_null_pnl_not_a_crash_or_fabricated_zero(self, client):
        """Stage 7 correction — a legacy trade with a NULL stored
        entry_price must close successfully with pnl=null in the response
        (a genuinely indeterminate value, surfaced honestly), never a 500
        crash and never a silently fabricated 0. (A literal NaN/Infinity
        in the entry_price column is covered at the close_service unit
        level — see test_postmortem_close_service.py — rather than here,
        since a NaN would also fail this endpoint's own unrelated JSON
        response encoding of the raw entry_price field, a separate,
        narrower gap this correction does not extend to.)"""
        shared = _new_shared([_new_trade(entry_price=None)])
        resp = _sell(client, 1, {"price": 120.0, "exit_reason": "MANUAL"}, shared)
        assert resp.status_code == 200
        body = resp.json()
        assert body["pnl"] is None
        # pnl_pct keeps this endpoint's own pre-existing legacy "0"
        # response contract (applied only at this HTTP boundary, per
        # test_paper_trading_postmortem_pnl_parity.py's locked-in
        # formula) — never confused with pnl itself, which stays null.
        assert body["pnl_pct"] == 0


@pytest.mark.regression
class TestCrossUserSellPreflightPrivacyMatrix:
    """Stage 5 correction — a cross-user sell attempt must be
    indistinguishable from a nonexistent trade_id regardless of the
    victim's market or that market's current open/closed state. Before
    the fix, the preflight probe queried by trade_id alone, so an
    attacker could learn a target trade's market and whether it was
    tradeable right now, purely from the HTTP status/detail returned for
    an ID they don't own."""

    @pytest.mark.parametrize("market,market_open", [
        ("IN", True), ("IN", False), ("US", True), ("US", False),
    ])
    def test_cross_user_trade_indistinguishable_from_nonexistent(self, client, market, market_open):
        shared = _new_shared([_new_trade(user_id="owner", market=market)])

        with patch.object(
            __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _conn_factory(shared)
        ), patch("api.routers.paper_trading._is_market_open", return_value=market_open):
            not_found = client.post(
                "/api/paper-trading/sell/999", json={"price": 100.0, "exit_reason": "MANUAL"},
                headers=_auth("attacker"),
            )
            cross_user = client.post(
                "/api/paper-trading/sell/1", json={"price": 100.0, "exit_reason": "MANUAL"},
                headers=_auth("attacker"),
            )

        assert cross_user.status_code == 404
        assert cross_user.json() == not_found.json()
        assert shared["trades"][1]["status"] == "OPEN"  # never touched
