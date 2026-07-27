"""
Real PostgreSQL Verification, CI Execution phase — Parts 8-12.

Real API-level Buy requests backed by a real disposable PostgreSQL
instance, including genuine multi-threaded concurrency (each thread makes
its own HTTP request through the app's real connection pool — independent
pooled connections, real PostgreSQL unique-index enforcement, no shared
client lock serializing anything).
"""
import datetime as dt
import threading
import time

import pytest

from tests.postgres_integration.conftest import ensure_portfolio, get_portfolio_cash, make_auth_header

pytestmark = pytest.mark.postgres_integration

BASE_BODY = {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0}


def _buy(client, user_id, **overrides):
    body = {**BASE_BODY, **overrides}
    return client.post("/api/paper-trading/buy", json=body, headers=make_auth_header(user_id))


@pytest.mark.timeout(30)
class TestFirstRequestAndSequentialReplay:
    def test_first_request_completes_and_is_stored(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        key = "11111111-1111-1111-1111-111111111111"
        resp = _buy(client, unique_user_id, idempotency_key=key)
        assert resp.status_code == 200
        assert resp.json()["idempotency_enforced"] is True

        row = pg_conn.execute(
            "SELECT status, response_body FROM paper_trade_idempotency_key WHERE user_id = %s AND idempotency_key = %s",
            (unique_user_id, key)
        ).fetchone()
        assert row[0] == "COMPLETED"
        assert row[1] is not None

    def test_sequential_replay_no_second_debit_trade_or_snapshot(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        key = "22222222-2222-2222-2222-222222222222"
        first = _buy(client, unique_user_id, idempotency_key=key)
        cash_after_first = get_portfolio_cash(pg_conn, unique_user_id, "US")

        second = _buy(client, unique_user_id, idempotency_key=key)

        assert second.status_code == 200
        assert second.json() == first.json()
        assert get_portfolio_cash(pg_conn, unique_user_id, "US") == pytest.approx(cash_after_first)
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trades WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0] == 1
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trade_entry_snapshot WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0] == 1
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trade_idempotency_key WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0] == 1


@pytest.mark.timeout(60)
class TestRealConcurrency:
    def test_ten_concurrent_identical_requests_produce_exactly_one_of_everything(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        key = "33333333-3333-3333-3333-333333333333"
        results = []
        results_lock = threading.Lock()
        N = 10

        def _worker():
            resp = _buy(client, unique_user_id, idempotency_key=key)
            with results_lock:
                results.append(resp)

        start = time.monotonic()
        threads = [threading.Thread(target=_worker) for _ in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=25)
        elapsed = time.monotonic() - start

        assert len(results) == N, "every thread must complete (no hang/deadlock)"
        assert elapsed < 25, f"concurrency resolution took too long: {elapsed:.1f}s"

        status_codes = [r.status_code for r in results]
        # No leaked raw SQL/unique-constraint exception (that would surface
        # as a 500) and no deadlock (would also surface as 500/timeout).
        assert 500 not in status_codes, f"a request leaked an unhandled exception: {status_codes}"

        successes = [r for r in results if r.status_code == 200]
        assert len(successes) >= 1
        trade_ids = {r.json()["trade_id"] for r in successes}
        assert len(trade_ids) == 1, f"all successful responses must refer to the same trade, got {trade_ids}"

        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trades WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0] == 1
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trade_entry_snapshot WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0] == 1
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trade_idempotency_key WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0] == 1

        cash = get_portfolio_cash(pg_conn, unique_user_id, "US")
        # market="US" debits cash_usd (default 100_000.0 per ensure_portfolio),
        # never cash (the IN-market ledger, default 1_000_000.0) — a wrong
        # ledger constant here would have masked a real debit-to-wrong-column
        # bug instead of proving the correct one.
        assert cash == pytest.approx(100_000.0 - 101.0)  # exactly one debit, not zero or negative/inconsistent


@pytest.mark.timeout(30)
class TestPayloadConflict:
    @pytest.mark.parametrize("field,value", [
        ("quantity", 2), ("symbol", "MSFT"), ("market", "IN"), ("price", 202.0),
        ("stop_loss", 90.0), ("target_price", 130.0),
        ("trade_management_mode", "auto"), ("evidence_source", "DAILY_PICK"),
    ])
    def test_changed_field_produces_stable_conflict_and_no_financial_write(
        self, client, pg_conn, unique_user_id, field, value
    ):
        ensure_portfolio(pg_conn, unique_user_id)
        key = "44444444-4444-4444-4444-444444444444"
        first = _buy(client, unique_user_id, idempotency_key=key)
        cash_after_first = get_portfolio_cash(pg_conn, unique_user_id, "US")
        row_before = pg_conn.execute(
            "SELECT request_fingerprint, response_body FROM paper_trade_idempotency_key WHERE user_id = %s AND idempotency_key = %s",
            (unique_user_id, key)
        ).fetchone()

        resp = _buy(client, unique_user_id, idempotency_key=key, **{field: value})

        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST"
        assert get_portfolio_cash(pg_conn, unique_user_id, "US") == pytest.approx(cash_after_first)
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trades WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0] == 1
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trade_entry_snapshot WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0] == 1
        row_after = pg_conn.execute(
            "SELECT request_fingerprint, response_body FROM paper_trade_idempotency_key WHERE user_id = %s AND idempotency_key = %s",
            (unique_user_id, key)
        ).fetchone()
        assert row_after == row_before  # untouched


@pytest.mark.timeout(30)
class TestCrossUserAndDifferentKeys:
    def test_same_key_different_users_no_collision(self, client, pg_conn, unique_user_id):
        other_user = f"{unique_user_id}-peer"
        ensure_portfolio(pg_conn, unique_user_id)
        ensure_portfolio(pg_conn, other_user)
        key = "55555555-5555-5555-5555-555555555555"
        try:
            r1 = _buy(client, unique_user_id, idempotency_key=key)
            r2 = _buy(client, other_user, idempotency_key=key)
            assert r1.status_code == 200 and r2.status_code == 200
            assert r1.json()["trade_id"] != r2.json()["trade_id"]
        finally:
            with pg_conn.transaction():
                pg_conn.execute("DELETE FROM paper_trade_entry_snapshot WHERE user_id = %s", (other_user,))
                pg_conn.execute("DELETE FROM paper_trade_idempotency_key WHERE user_id = %s", (other_user,))
                pg_conn.execute("DELETE FROM paper_trades WHERE user_id = %s", (other_user,))
                pg_conn.execute("DELETE FROM paper_portfolio WHERE user_id = %s", (other_user,))

    def test_different_keys_same_payload_creates_two_genuine_trades(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        r1 = _buy(client, unique_user_id, idempotency_key="66666666-6666-6666-6666-666666666661")
        r2 = _buy(client, unique_user_id, idempotency_key="66666666-6666-6666-6666-666666666662")
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["trade_id"] != r2.json()["trade_id"]
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trades WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0] == 2
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trade_entry_snapshot WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0] == 2
        assert get_portfolio_cash(pg_conn, unique_user_id, "US") == pytest.approx(100_000.0 - 202.0)


@pytest.mark.timeout(30)
class TestResponseLossReplay:
    def test_retry_after_simulated_lost_response_returns_committed_result(self, client, pg_conn, unique_user_id):
        """Honest simulation method: the first request is allowed to
        complete and commit normally (the server-side commit is real and
        unaffected); "the client never saw the response" is simulated by
        simply discarding the first HTTP Response object and acting only
        on the retry — this is exactly what a real client experiences on a
        dropped connection after the server has already committed, since
        nothing server-side depends on the client having read the
        response."""
        ensure_portfolio(pg_conn, unique_user_id)
        key = "77777777-7777-7777-7777-777777777777"
        first = _buy(client, unique_user_id, idempotency_key=key)
        assert first.status_code == 200
        _ = first  # simulate: client discards this, as if the response never arrived

        retry = _buy(client, unique_user_id, idempotency_key=key)
        assert retry.status_code == 200
        assert retry.json()["trade_id"] == first.json()["trade_id"]
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trades WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0] == 1
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trade_entry_snapshot WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0] == 1


@pytest.mark.timeout(30)
class TestStaleAndFailedReclaim:
    def test_recent_pending_row_returns_in_progress(self, client, pg_conn, unique_user_id):
        from services.postmortem.idempotency import compute_request_fingerprint
        from services.postmortem.entry_snapshot import SNAPSHOT_SCHEMA_VERSION

        ensure_portfolio(pg_conn, unique_user_id)
        key = "88888888-8888-8888-8888-888888888881"
        fingerprint = compute_request_fingerprint(
            market="US", symbol="AAPL", quantity=1, price=101.0, stop_loss=None,
            target_price=None, trade_management_mode="manual", evidence_source="MANUAL",
            entry_evidence_schema_version=SNAPSHOT_SCHEMA_VERSION,
        )
        pg_conn.execute(
            """INSERT INTO paper_trade_idempotency_key
               (user_id, operation_type, idempotency_key, request_fingerprint, status)
               VALUES (%s, 'PAPER_BUY', %s, %s, 'PENDING')""",
            (unique_user_id, key, fingerprint)
        )
        resp = _buy(client, unique_user_id, idempotency_key=key)
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "BUY_ALREADY_IN_PROGRESS"
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trades WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0] == 0

    def test_stale_pending_row_is_reclaimed_exactly_once_under_concurrency(self, client, pg_conn, unique_user_id):
        """Multiple simultaneous reclaim attempts against one stale PENDING
        row — real compare-and-swap under real concurrent connections."""
        from services.postmortem.idempotency import compute_request_fingerprint, STALE_PENDING_THRESHOLD
        from services.postmortem.entry_snapshot import SNAPSHOT_SCHEMA_VERSION

        ensure_portfolio(pg_conn, unique_user_id)
        key = "88888888-8888-8888-8888-888888888882"
        fingerprint = compute_request_fingerprint(
            market="US", symbol="AAPL", quantity=1, price=101.0, stop_loss=None,
            target_price=None, trade_management_mode="manual", evidence_source="MANUAL",
            entry_evidence_schema_version=SNAPSHOT_SCHEMA_VERSION,
        )
        stale_created_at = dt.datetime.now(dt.timezone.utc) - STALE_PENDING_THRESHOLD - dt.timedelta(seconds=5)
        pg_conn.execute(
            """INSERT INTO paper_trade_idempotency_key
               (user_id, operation_type, idempotency_key, request_fingerprint, status, created_at)
               VALUES (%s, 'PAPER_BUY', %s, %s, 'PENDING', %s)""",
            (unique_user_id, key, fingerprint, stale_created_at)
        )

        results = []
        results_lock = threading.Lock()

        def _worker():
            resp = _buy(client, unique_user_id, idempotency_key=key)
            with results_lock:
                results.append(resp)

        threads = [threading.Thread(target=_worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

        assert len(results) == 5
        successes = [r for r in results if r.status_code == 200]
        assert len(successes) >= 1
        trade_ids = {r.json()["trade_id"] for r in successes}
        assert len(trade_ids) == 1
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trades WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0] == 1

    def test_failed_row_is_reclaimed_and_fingerprint_mismatch_still_conflicts(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id, cash_usd=50.0)  # too little for the $101 buy -> FAILED
        key = "88888888-8888-8888-8888-888888888883"
        first = _buy(client, unique_user_id, idempotency_key=key)
        assert first.status_code == 400
        row = pg_conn.execute(
            "SELECT status FROM paper_trade_idempotency_key WHERE user_id = %s AND idempotency_key = %s",
            (unique_user_id, key)
        ).fetchone()
        assert row[0] == "FAILED"

        # Fingerprint mismatch must still conflict even though prior state is FAILED.
        conflict = _buy(client, unique_user_id, idempotency_key=key, quantity=99)
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["error_code"] == "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST"

        # Same payload, now with sufficient funds -> reclaimed and succeeds.
        pg_conn.execute("UPDATE paper_portfolio SET cash_usd = 1000000.0 WHERE user_id = %s", (unique_user_id,))
        retry = _buy(client, unique_user_id, idempotency_key=key)
        assert retry.status_code == 200
        row_after = pg_conn.execute(
            "SELECT status FROM paper_trade_idempotency_key WHERE user_id = %s AND idempotency_key = %s",
            (unique_user_id, key)
        ).fetchone()
        assert row_after[0] == "COMPLETED"
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trade_idempotency_key WHERE user_id = %s AND idempotency_key = %s",
            (unique_user_id, key)
        ).fetchone()[0] == 1  # same row reclaimed, not duplicated
