"""
Trade Postmortem Engine, Stage 2 — integration tests for POST /buy's
atomic trade + entry-snapshot persistence.

Follows the exact mocking pattern established in
tests/regression/test_paper_trading_provenance.py: a fake psycopg-shaped
connection recorder patched over api.routers.paper_trading._conn, so these
tests never touch a real database or network.
"""
import datetime as dt
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


def _token(sub: str = "user-aaa") -> str:
    return jwt.encode(
        {"sub": sub, "aud": "authenticated", "iss": TEST_ISSUER, "exp": time.time() + 3600},
        TEST_SECRET, algorithm="HS256",
    )


def _auth(sub: str = "user-aaa") -> dict:
    return {"Authorization": f"Bearer {_token(sub)}"}


class _RecordingConn:
    """Matches test_paper_trading_provenance.py's fake exactly, plus a real
    (non-no-op) `.transaction()` that actually tracks whether the block
    raised — so rollback-on-exception can be genuinely asserted, not just
    assumed."""

    def __init__(self, fetchone_results=None):
        self.calls = []
        self._fetchone_results = list(fetchone_results or [])
        self.committed_statement_count = None
        self.rolled_back = False
        self._pending_reservation_insert = False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        # Owner-authorized hardening — idempotency_key is now REQUIRED, so
        # every Buy now ALSO issues the reservation
        # "INSERT INTO paper_trade_idempotency_key ... RETURNING id" before
        # the debit/trade-insert statements this fake was originally built
        # to simulate. Rather than editing every call site's
        # `fetchone_results` list to prepend a reservation row (this file
        # alone has ~20), the reservation's own `.fetchone()` is answered
        # synthetically here — it never consumes from `_fetchone_results`,
        # which stays reserved for the financial statements exactly as
        # before this change.
        self._pending_reservation_insert = (
            "INSERT INTO paper_trade_idempotency_key" in sql and "RETURNING id" in sql
        )
        return self

    def fetchone(self):
        if self._pending_reservation_insert:
            self._pending_reservation_insert = False
            return (777001,)  # synthetic fresh idempotency-reservation row id
        return self._fetchone_results.pop(0) if self._fetchone_results else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @contextmanager
    def transaction(self):
        statements_before = len(self.calls)
        try:
            yield self
        except Exception:
            # Real psycopg3 rolls back everything executed inside the
            # `with conn.transaction():` block on an exception propagating
            # out of it. This fake can't roll back a real DB, but it proves
            # the code path lets the exception propagate (rather than
            # swallowing it and returning 200 with a half-created trade) by
            # re-raising — the assertion is on the HTTP response code the
            # caller sees, not on this fake's internal state.
            self.rolled_back = True
            raise
        else:
            self.committed_statement_count = len(self.calls) - statements_before


def _buy_conn_factory(fetchone_results_seq):
    made = []

    def factory():
        conn = fetchone_results_seq.pop(0)
        made.append(conn)
        return conn

    return factory, made


def _insert_calls(made_conns, table_fragment):
    return [
        (sql, params)
        for conn in made_conns
        for sql, params in conn.calls
        if f"INSERT INTO {table_fragment}" in sql
    ]


def _buy(client, body, conns):
    # Owner-authorized hardening — idempotency_key is now REQUIRED by
    # POST /buy. This suite's bodies were written before that change and
    # mostly don't set one; injecting a default here (only when the caller
    # hasn't already set one) keeps every existing test's intent (atomic
    # trade+snapshot persistence under a mocked connection) unchanged
    # without editing every one of this file's ~20 call sites individually.
    body = {"idempotency_key": f"ptbuy-test-{uuid.uuid4()}", **body}
    factory, made = _buy_conn_factory(list(conns))
    with patch.object(
        __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", factory
    ), patch("api.routers.paper_trading._is_market_open", return_value=True):
        resp = client.post("/api/paper-trading/buy", json=body, headers=_auth())
    return resp, made


def _buy_raw_json(client, body, conns):
    """Bypasses httpx's `json=` encoder, which enforces `allow_nan=False`
    and can never construct a request containing a literal NaN/Infinity
    token client-side. A real attacker isn't bound by httpx's encoder —
    Python's own `json.loads` (which FastAPI/Pydantic parses request bodies
    with) accepts bare NaN/Infinity/-Infinity tokens as a well-known
    extension, so a hand-crafted raw request CAN contain them; this sends
    exactly that, via stdlib `json.dumps` (allow_nan=True, the default),
    to prove the backend's own `math.isfinite` validation — not httpx's
    encoder — is what actually rejects them."""
    body = {"idempotency_key": f"ptbuy-test-{uuid.uuid4()}", **body}
    factory, made = _buy_conn_factory(list(conns))
    raw = _json.dumps(body)
    with patch.object(
        __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", factory
    ), patch("api.routers.paper_trading._is_market_open", return_value=True):
        resp = client.post(
            "/api/paper-trading/buy", content=raw,
            headers={**_auth(), "Content-Type": "application/json"},
        )
    return resp, made


def _default_conns():
    return [
        _RecordingConn(fetchone_results=[(1_000_000.0, 100_000.0, True)]),  # _ensure_portfolio SELECT
        _RecordingConn(fetchone_results=[(999_900.0,), (1,)]),               # debit UPDATE, trade INSERT
    ]


@pytest.mark.integration
class TestAtomicEntrySnapshotPersistence:
    def test_successful_buy_creates_exactly_one_snapshot_row(self, client):
        body = {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0}
        resp, made = _buy(client, body, _default_conns())
        assert resp.status_code == 200
        snapshot_inserts = _insert_calls(made, "paper_trade_entry_snapshot")
        assert len(snapshot_inserts) == 1

    def test_snapshot_and_trade_insert_happen_inside_the_same_transaction_block(self, client):
        body = {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0}
        resp, made = _buy(client, body, _default_conns())
        assert resp.status_code == 200
        buy_conn = made[1]
        # All three writes recorded on the buy transaction's own
        # connection, and the transaction() context committed (didn't roll
        # back). Migration-verification hardening gate, Part 7 — the debit
        # UPDATE now participates in this same transaction too (previously
        # a standalone autocommit statement before it), so the count is 3,
        # not 2.
        assert any("INSERT INTO paper_trades" in sql for sql, _ in buy_conn.calls)
        assert any("INSERT INTO paper_trade_entry_snapshot" in sql for sql, _ in buy_conn.calls)
        assert any("UPDATE paper_portfolio" in sql for sql, _ in buy_conn.calls)
        assert buy_conn.rolled_back is False
        # Owner-authorized hardening — idempotency_key is now REQUIRED, so
        # idempotency_enforced is always True and the idempotency-completion
        # UPDATE (SET status='COMPLETED' ...) always runs as a 4th statement
        # inside this same transaction block, alongside the debit UPDATE +
        # trade INSERT + snapshot INSERT. (The idempotency RESERVATION
        # INSERT itself runs BEFORE this transaction block opens — see
        # paper_buy — so it is correctly NOT one of these 4.)
        assert buy_conn.committed_statement_count == 4

    def test_snapshot_insert_failure_rolls_back_and_returns_500_not_200(self, client):
        """If the snapshot INSERT raises, the whole request must fail loudly
        — never a 200 with a trade created and its evidence decision
        silently dropped. TestClient re-raises unhandled server exceptions
        by default (debugging convenience) rather than turning them into a
        500 response, so the propagating exception itself — not a 200 — is
        the observable proof here."""
        conns = _default_conns()
        buy_conn = conns[1]
        real_execute = buy_conn.execute

        def failing_execute(sql, params=None):
            if "INSERT INTO paper_trade_entry_snapshot" in sql:
                raise RuntimeError("simulated snapshot insert failure")
            return real_execute(sql, params)

        buy_conn.execute = failing_execute
        with pytest.raises(RuntimeError, match="simulated snapshot insert failure"):
            _buy(client, {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0}, conns)
        assert buy_conn.rolled_back is True
        # Confirm the trade INSERT itself did happen before the failure —
        # i.e. this is a genuine "second statement failed, first must roll
        # back too" case, not a trivial "nothing ran yet".
        assert any("INSERT INTO paper_trades" in sql for sql, _ in buy_conn.calls)

    def test_response_reports_evidence_metadata(self, client):
        body = {
            "symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0,
            "evidence_source": "DAILY_PICK",
            "entry_evidence": {"confidence_score": 82.0, "recommendation_reference_price": 100.0},
        }
        resp, _ = _buy(client, body, _default_conns())
        assert resp.status_code == 200
        data = resp.json()
        assert data["entry_evidence_captured"] is True
        assert data["evidence_source"] == "DAILY_PICK"
        assert data["evidence_completeness"] == "PARTIAL"
        assert "confidence_score" in data["available_evidence_fields"]
        assert "snapshot_schema_version" in data

    def test_manual_trade_with_no_entry_evidence_still_succeeds(self, client):
        """Part 8 — a manual trade must not fail solely because no
        recommendation exists."""
        body = {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0}
        resp, _ = _buy(client, body, _default_conns())
        assert resp.status_code == 200
        data = resp.json()
        assert data["evidence_source"] == "MANUAL"
        assert data["evidence_completeness"] == "LIMITED"

    def test_old_client_omitting_new_fields_entirely_still_succeeds(self, client):
        """Backward compatibility — the exact old request shape (Phase 1's
        provenance fields, no evidence_source/entry_evidence at all)."""
        body = {
            "symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0,
            "recommendation_source": "daily_pick", "recommendation_reference_price": 99.0,
        }
        resp, made = _buy(client, body, _default_conns())
        assert resp.status_code == 200
        # Old flat provenance fields still land in paper_trades unchanged.
        trade_insert = _insert_calls(made, "paper_trades")[0]
        assert "daily_pick" in trade_insert[1]

    @pytest.mark.parametrize("bad_evidence", [
        {"confidence_score": 150.0},                                  # out of [0,100]
        {"recommendation_entry_low": 110.0, "recommendation_entry_high": 90.0},  # low > high
    ])
    def test_malformed_entry_evidence_is_rejected_not_silently_accepted(self, client, bad_evidence):
        body = {
            "symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0,
            "entry_evidence": bad_evidence,
        }
        resp, made = _buy(client, body, _default_conns())
        assert resp.status_code == 422
        assert made == []  # rejected before any connection was ever opened

    @pytest.mark.parametrize("bad_evidence", [
        {"confidence_score": float("nan")},
        {"recommendation_reference_price": float("inf")},
        {"recommendation_reference_price": float("-inf")},
    ])
    def test_non_finite_entry_evidence_is_rejected(self, client, bad_evidence):
        """Sent as raw JSON (see _buy_raw_json) since httpx's own `json=`
        encoder can't construct a NaN/Infinity request body at all — a real
        attacker isn't bound by that; Python's own `json.loads` (which
        FastAPI/Pydantic parses request bodies with) accepts bare
        NaN/Infinity/-Infinity tokens by default.

        Migration-verification gate fix: this previously crashed with an
        unhandled 500 (Starlette's JSONResponse enforces `allow_nan=False`
        on *output*, and FastAPI's default validation-error handler echoes
        the rejected raw value back in the error body) — see
        api/main.py's `_stable_validation_error_handler`. Now returns a
        stable 422, the non-finite value is never echoed verbatim, and
        malformed evidence is never persisted."""
        body = {
            "symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0,
            "entry_evidence": bad_evidence,
        }
        factory, made = _buy_conn_factory(list(_default_conns()))
        raw = _json.dumps(body)
        with patch.object(
            __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", factory
        ), patch("api.routers.paper_trading._is_market_open", return_value=True):
            resp = client.post(
                "/api/paper-trading/buy", content=raw,
                headers={**_auth(), "Content-Type": "application/json"},
            )
        assert resp.status_code == 422
        body_text = resp.text
        assert "NaN" not in body_text and "Infinity" not in body_text
        # The response itself must be valid, re-parseable JSON — proves no
        # non-finite literal leaked into the body.
        import json as _json_check
        _json_check.loads(body_text)
        assert made == []

    def test_impossibly_future_timestamp_is_rejected(self, client):
        future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat()
        body = {
            "symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0,
            "entry_evidence": {"recommendation_generated_at": future},
        }
        resp, _ = _buy(client, body, _default_conns())
        assert resp.status_code == 422

    def test_stale_timestamp_beyond_freshness_window_is_rejected(self, client):
        stale = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=200)).isoformat()
        body = {
            "symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0,
            "entry_evidence": {"recommendation_generated_at": stale},
        }
        resp, _ = _buy(client, body, _default_conns())
        assert resp.status_code == 422

    def test_fresh_timestamp_within_window_is_accepted(self, client):
        fresh = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()
        body = {
            "symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0,
            "entry_evidence": {"recommendation_generated_at": fresh},
        }
        resp, _ = _buy(client, body, _default_conns())
        assert resp.status_code == 200

    def test_screener_and_research_evidence_sources_accepted(self, client):
        for source in ("SCREENER", "RESEARCH"):
            resp, _ = _buy(
                client, {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0, "evidence_source": source},
                _default_conns(),
            )
            assert resp.status_code == 200
            assert resp.json()["evidence_source"] == source

    def test_unknown_evidence_source_is_rejected(self, client):
        resp, made = _buy(
            client,
            {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0, "evidence_source": "BOGUS"},
            _default_conns(),
        )
        assert resp.status_code == 422
        assert made == []

    def test_two_buys_of_the_same_recommendation_get_independent_snapshots(self, client):
        """Part 6 — identical recommendation evidence used for two distinct
        trades: each Buy call creates its own trade_id and its own snapshot
        row, no shared/deduplicated evidence record."""
        shared_evidence = {
            "evidence_source": "DAILY_PICK",
            "entry_evidence": {"confidence_score": 75.0, "daily_pick_run_id": "run-42"},
        }
        for _ in range(2):
            body = {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0, **shared_evidence}
            resp, made = _buy(client, body, _default_conns())
            assert resp.status_code == 200
            assert len(_insert_calls(made, "paper_trade_entry_snapshot")) == 1
