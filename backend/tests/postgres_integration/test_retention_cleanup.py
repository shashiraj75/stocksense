"""
Real PostgreSQL Verification, CI Execution phase — Part 13.

Runs the actual `services.postmortem.idempotency_cleanup.run_cleanup`
against a real disposable PostgreSQL instance.
"""
import datetime as dt
import json

import pytest

from services.postmortem.idempotency import STALE_PENDING_THRESHOLD
from services.postmortem.idempotency_cleanup import run_cleanup

pytestmark = pytest.mark.postgres_integration


def _seed(pg_conn, user_id, status, age: dt.timedelta, key_suffix, response_body=None):
    created_at = dt.datetime.now(dt.timezone.utc) - age
    pg_conn.execute(
        """INSERT INTO paper_trade_idempotency_key
           (user_id, operation_type, idempotency_key, request_fingerprint, status, created_at, response_body)
           VALUES (%s, 'PAPER_BUY', %s, 'fp', %s, %s, %s)""",
        (user_id, f"key-{key_suffix}", status, created_at, json.dumps(response_body) if response_body else None)
    )


def _count(pg_conn, user_id):
    return pg_conn.execute(
        "SELECT count(*) FROM paper_trade_idempotency_key WHERE user_id = %s", (user_id,)
    ).fetchone()[0]


@pytest.mark.timeout(30)
class TestRetentionCleanupAgainstRealPostgres:
    def test_old_completed_removed_recent_completed_kept(self, pg_conn, unique_user_id, monkeypatch):
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_COMPLETED_RETENTION_DAYS", "30")
        _seed(pg_conn, unique_user_id, "COMPLETED", dt.timedelta(days=31), "old")
        _seed(pg_conn, unique_user_id, "COMPLETED", dt.timedelta(days=1), "recent")

        result = run_cleanup(pg_conn)

        assert result.completed_deleted == 1
        remaining = pg_conn.execute(
            "SELECT idempotency_key FROM paper_trade_idempotency_key WHERE user_id = %s", (unique_user_id,)
        ).fetchall()
        assert [r[0] for r in remaining] == ["key-recent"]

    def test_old_failed_removed_recent_failed_kept(self, pg_conn, unique_user_id, monkeypatch):
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_FAILED_RETENTION_HOURS", "24")
        _seed(pg_conn, unique_user_id, "FAILED", dt.timedelta(hours=25), "old")
        _seed(pg_conn, unique_user_id, "FAILED", dt.timedelta(hours=1), "recent")

        result = run_cleanup(pg_conn)

        assert result.failed_deleted == 1
        assert _count(pg_conn, unique_user_id) == 1

    def test_active_pending_row_is_never_removed(self, pg_conn, unique_user_id):
        _seed(pg_conn, unique_user_id, "PENDING", dt.timedelta(seconds=1), "active")
        result = run_cleanup(pg_conn)
        assert result.stale_pending_deleted == 0
        assert _count(pg_conn, unique_user_id) == 1

    def test_stale_reclaimable_but_not_yet_beyond_safety_margin_is_kept(self, pg_conn, unique_user_id, monkeypatch):
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_STALE_PENDING_SAFETY_HOURS", "1")
        age = STALE_PENDING_THRESHOLD + dt.timedelta(minutes=5)
        _seed(pg_conn, unique_user_id, "PENDING", age, "stale-recoverable")
        result = run_cleanup(pg_conn)
        assert result.stale_pending_deleted == 0
        assert _count(pg_conn, unique_user_id) == 1

    def test_truly_abandoned_pending_removed(self, pg_conn, unique_user_id, monkeypatch):
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_STALE_PENDING_SAFETY_HOURS", "1")
        age = STALE_PENDING_THRESHOLD + dt.timedelta(hours=2)
        _seed(pg_conn, unique_user_id, "PENDING", age, "abandoned")
        result = run_cleanup(pg_conn)
        assert result.stale_pending_deleted == 1

    def test_batch_size_respected_against_real_postgres(self, pg_conn, unique_user_id, monkeypatch):
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_COMPLETED_RETENTION_DAYS", "30")
        for i in range(8):
            _seed(pg_conn, unique_user_id, "COMPLETED", dt.timedelta(days=31), f"batch-{i}")
        result = run_cleanup(pg_conn, batch_size=3)
        assert result.completed_deleted == 3
        assert _count(pg_conn, unique_user_id) == 5

    def test_repeated_cleanup_is_safe(self, pg_conn, unique_user_id, monkeypatch):
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_COMPLETED_RETENTION_DAYS", "30")
        _seed(pg_conn, unique_user_id, "COMPLETED", dt.timedelta(days=31), "one")
        run_cleanup(pg_conn)
        second = run_cleanup(pg_conn)
        assert second.completed_deleted == 0
        assert second.errors == []

    def test_one_category_failure_does_not_incorrectly_delete_another(self, pg_conn, unique_user_id, monkeypatch):
        """Corrupt only the FAILED category's eligibility (impossible
        interval string) while COMPLETED/PENDING remain seedable and
        eligible — confirms category isolation for real."""
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_COMPLETED_RETENTION_DAYS", "30")
        _seed(pg_conn, unique_user_id, "COMPLETED", dt.timedelta(days=31), "completed-old")

        import services.postmortem.idempotency_cleanup as cleanup_mod
        original = cleanup_mod.get_failed_retention

        def _broken_retention():
            raise RuntimeError("simulated config error")
        cleanup_mod.get_failed_retention = _broken_retention
        try:
            result = run_cleanup(pg_conn)
        finally:
            cleanup_mod.get_failed_retention = original

        assert result.completed_deleted == 1
        assert len(result.errors) == 1

    def test_trades_and_snapshots_are_never_touched_by_cleanup(self, client, pg_conn, unique_user_id, monkeypatch):
        from tests.postgres_integration.conftest import ensure_portfolio, make_auth_header
        ensure_portfolio(pg_conn, unique_user_id)
        resp = client.post(
            "/api/paper-trading/buy",
            json={"symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0, "idempotency_key": "cleanup-safety-key"},
            headers=make_auth_header(unique_user_id),
        )
        assert resp.status_code == 200
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_COMPLETED_RETENTION_DAYS", "0")  # make it eligible immediately
        run_cleanup(pg_conn)
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trades WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0] == 1
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trade_entry_snapshot WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0] == 1

    def test_empty_table_handled(self, pg_conn, unique_user_id):
        result = run_cleanup(pg_conn)
        assert result.total_deleted == 0

    def test_malformed_legacy_status_defensively_ignored(self, pg_conn, unique_user_id):
        _seed(pg_conn, unique_user_id, "UNKNOWN_LEGACY_STATUS", dt.timedelta(days=365), "legacy")
        result = run_cleanup(pg_conn)
        assert result.total_deleted == 0
        assert _count(pg_conn, unique_user_id) == 1

    def test_cleanup_does_not_block_a_concurrent_buy_beyond_a_couple_seconds(self, client, pg_conn, unique_user_id):
        import threading
        import time
        from tests.postgres_integration.conftest import ensure_portfolio, make_auth_header

        ensure_portfolio(pg_conn, unique_user_id)
        cleanup_done = threading.Event()

        def _cleanup_worker():
            run_cleanup(pg_conn)
            cleanup_done.set()

        t = threading.Thread(target=_cleanup_worker)
        start = time.monotonic()
        t.start()
        resp = client.post(
            "/api/paper-trading/buy",
            json={"symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0},
            headers=make_auth_header(unique_user_id),
        )
        elapsed = time.monotonic() - start
        t.join(timeout=10)
        assert resp.status_code == 200
        assert elapsed < 10
