"""
Real PostgreSQL Verification, Idempotency Retention, and Operational
Observability phase, Stage 8 — cleanup tests.

No live Postgres available in this environment (confirmed absent this
session — no docker/psql/pg_ctl/initdb, nothing on :5432). These tests run
against an in-memory fake that implements exactly the one DELETE shape
`idempotency_cleanup._delete_batch` issues, parsing the real SQL's
status/interval/limit parameters — this proves the module's own batching,
retention-window, and status-selection logic is correct, NOT that
PostgreSQL's real `DELETE ... RETURNING` semantics, locking, or interval
arithmetic behave identically. That live-server proof remains BLOCKED —
see the phase report.
"""
import datetime as dt

import pytest

from services.postmortem.idempotency_cleanup import (
    CleanupResult,
    get_cleanup_batch_size,
    get_completed_retention,
    get_failed_retention,
    get_stale_pending_retention,
    run_cleanup,
)
from services.postmortem.idempotency import STALE_PENDING_THRESHOLD

UTC = dt.timezone.utc


class _FakeCleanupConn:
    """Rows are plain dicts: {id, user_id, status, created_at}. Implements
    only the exact DELETE shape _delete_batch issues."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self._last_params = params
        return self

    def fetchall(self):
        status, interval_str, limit = self._last_params
        seconds = float(interval_str.split()[0])
        cutoff = dt.datetime.now(UTC) - dt.timedelta(seconds=seconds)
        eligible = sorted(
            (r for r in self.rows if r["status"] == status and r["created_at"] < cutoff),
            key=lambda r: r["id"],
        )[:limit]
        eligible_ids = {r["id"] for r in eligible}
        self.rows = [r for r in self.rows if r["id"] not in eligible_ids]
        return [(r["id"],) for r in eligible]


def _row(id, status, age: dt.timedelta, user_id="user-aaa"):
    return {"id": id, "user_id": user_id, "status": status, "created_at": dt.datetime.now(UTC) - age}


@pytest.mark.unit
class TestRetentionConfig:
    def test_defaults_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("PAPER_TRADE_IDEMPOTENCY_COMPLETED_RETENTION_DAYS", raising=False)
        monkeypatch.delenv("PAPER_TRADE_IDEMPOTENCY_FAILED_RETENTION_HOURS", raising=False)
        monkeypatch.delenv("PAPER_TRADE_IDEMPOTENCY_CLEANUP_BATCH_SIZE", raising=False)
        assert get_completed_retention() == dt.timedelta(days=30)
        assert get_failed_retention() == dt.timedelta(hours=24)
        assert get_cleanup_batch_size() == 500

    def test_env_override_respected(self, monkeypatch):
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_COMPLETED_RETENTION_DAYS", "7")
        assert get_completed_retention() == dt.timedelta(days=7)

    def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_CLEANUP_BATCH_SIZE", "not-a-number")
        assert get_cleanup_batch_size() == 500

    def test_non_positive_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_CLEANUP_BATCH_SIZE", "0")
        assert get_cleanup_batch_size() == 500

    def test_stale_pending_retention_includes_reclaim_threshold_plus_safety_margin(self, monkeypatch):
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_STALE_PENDING_SAFETY_HOURS", "2")
        assert get_stale_pending_retention() == STALE_PENDING_THRESHOLD + dt.timedelta(hours=2)


@pytest.mark.unit
class TestRunCleanup:
    def test_old_completed_row_is_removed(self, monkeypatch):
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_COMPLETED_RETENTION_DAYS", "30")
        conn = _FakeCleanupConn([_row(1, "COMPLETED", dt.timedelta(days=31))])
        result = run_cleanup(conn)
        assert result.completed_deleted == 1
        assert conn.rows == []

    def test_recent_completed_row_remains(self, monkeypatch):
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_COMPLETED_RETENTION_DAYS", "30")
        conn = _FakeCleanupConn([_row(1, "COMPLETED", dt.timedelta(days=1))])
        result = run_cleanup(conn)
        assert result.completed_deleted == 0
        assert len(conn.rows) == 1

    def test_old_failed_row_is_removed(self, monkeypatch):
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_FAILED_RETENTION_HOURS", "24")
        conn = _FakeCleanupConn([_row(1, "FAILED", dt.timedelta(hours=25))])
        result = run_cleanup(conn)
        assert result.failed_deleted == 1

    def test_recent_failed_row_remains(self, monkeypatch):
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_FAILED_RETENTION_HOURS", "24")
        conn = _FakeCleanupConn([_row(1, "FAILED", dt.timedelta(hours=1))])
        result = run_cleanup(conn)
        assert result.failed_deleted == 0
        assert len(conn.rows) == 1

    def test_active_pending_row_remains(self):
        """A genuinely fresh PENDING row (well within the reclaim
        threshold) must never be touched by cleanup."""
        conn = _FakeCleanupConn([_row(1, "PENDING", dt.timedelta(seconds=1))])
        result = run_cleanup(conn)
        assert result.stale_pending_deleted == 0
        assert len(conn.rows) == 1

    def test_stale_but_reclaimable_pending_row_remains_until_safety_margin_passes(self, monkeypatch):
        """Older than STALE_PENDING_THRESHOLD (reclaimable by the next
        retry) but NOT yet older than threshold+safety margin — cleanup
        must not delete it; a legitimate retry might still reclaim it."""
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_STALE_PENDING_SAFETY_HOURS", "1")
        age = STALE_PENDING_THRESHOLD + dt.timedelta(minutes=5)  # reclaimable, but well under +1h safety margin
        conn = _FakeCleanupConn([_row(1, "PENDING", age)])
        result = run_cleanup(conn)
        assert result.stale_pending_deleted == 0
        assert len(conn.rows) == 1

    def test_truly_abandoned_pending_row_beyond_safety_margin_is_removed(self, monkeypatch):
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_STALE_PENDING_SAFETY_HOURS", "1")
        age = STALE_PENDING_THRESHOLD + dt.timedelta(hours=2)
        conn = _FakeCleanupConn([_row(1, "PENDING", age)])
        result = run_cleanup(conn)
        assert result.stale_pending_deleted == 1

    def test_batch_size_limit_is_respected(self, monkeypatch):
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_COMPLETED_RETENTION_DAYS", "30")
        rows = [_row(i, "COMPLETED", dt.timedelta(days=31)) for i in range(10)]
        conn = _FakeCleanupConn(rows)
        result = run_cleanup(conn, batch_size=3)
        assert result.completed_deleted == 3
        assert len(conn.rows) == 7  # only one batch's worth removed

    def test_repeated_cleanup_is_safe_and_eventually_empties_backlog(self, monkeypatch):
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_COMPLETED_RETENTION_DAYS", "30")
        rows = [_row(i, "COMPLETED", dt.timedelta(days=31)) for i in range(7)]
        conn = _FakeCleanupConn(rows)
        first = run_cleanup(conn, batch_size=5)
        second = run_cleanup(conn, batch_size=5)
        third = run_cleanup(conn, batch_size=5)
        assert first.completed_deleted == 5
        assert second.completed_deleted == 2
        assert third.completed_deleted == 0  # already clean — safe to call again
        assert conn.rows == []

    def test_does_not_delete_another_users_valid_recent_rows(self, monkeypatch):
        """Cleanup has no user-scoping at all by design — retention is
        purely time/status-based, applying identically across all users.
        This test confirms a RECENT row survives regardless of whose it
        is, which is the property that actually protects other users'
        valid data (there is no per-user exemption to get wrong)."""
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_COMPLETED_RETENTION_DAYS", "30")
        conn = _FakeCleanupConn([
            _row(1, "COMPLETED", dt.timedelta(days=31), user_id="user-aaa"),
            _row(2, "COMPLETED", dt.timedelta(days=1), user_id="user-bbb"),
        ])
        run_cleanup(conn)
        assert [r["id"] for r in conn.rows] == [2]

    def test_empty_table_is_handled(self):
        conn = _FakeCleanupConn([])
        result = run_cleanup(conn)
        assert result.total_deleted == 0
        assert result.errors == []

    def test_malformed_legacy_status_is_never_deleted_by_any_category(self):
        """A row with a status value outside {PENDING, COMPLETED, FAILED}
        (e.g. a future/unknown status from a schema evolution) is not
        matched by any of the three category deletes — defensively left
        alone rather than guessed into a category."""
        conn = _FakeCleanupConn([_row(1, "SOME_FUTURE_STATUS", dt.timedelta(days=365))])
        result = run_cleanup(conn)
        assert result.total_deleted == 0
        assert len(conn.rows) == 1

    def test_cleanup_result_and_logs_never_reference_response_body_or_fingerprint(self):
        """Static guard: the module's own source never formats a log
        message with response_body/request_fingerprint/idempotency_key —
        only counts."""
        import pathlib
        import services.postmortem.idempotency_cleanup as mod
        source = pathlib.Path(mod.__file__).read_text()
        for forbidden in ("response_body", "request_fingerprint", "idempotency_key"):
            # Allowed to appear in comments/docstrings explaining the rule
            # itself, but never inside a log.info/log.error(...) call.
            for line in source.splitlines():
                if "log." in line and forbidden in line:
                    pytest.fail(f"log call appears to reference {forbidden!r}: {line}")

    def test_a_failure_in_one_category_does_not_prevent_the_others(self, monkeypatch):
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_COMPLETED_RETENTION_DAYS", "30")
        monkeypatch.setenv("PAPER_TRADE_IDEMPOTENCY_FAILED_RETENTION_HOURS", "24")

        class _PartiallyBrokenConn(_FakeCleanupConn):
            def execute(self, sql, params=None):
                if params and params[0] == "FAILED":
                    raise RuntimeError("simulated transient failure")
                return super().execute(sql, params)

        conn = _PartiallyBrokenConn([
            _row(1, "COMPLETED", dt.timedelta(days=31)),
            _row(2, "FAILED", dt.timedelta(hours=25)),
        ])
        result = run_cleanup(conn)
        assert result.completed_deleted == 1
        assert result.failed_deleted == 0
        assert len(result.errors) == 1
        assert "FAILED" in result.errors[0]


@pytest.mark.unit
class TestCleanupResult:
    def test_total_deleted_sums_all_categories(self):
        result = CleanupResult(completed_deleted=3, failed_deleted=2, stale_pending_deleted=1)
        assert result.total_deleted == 6
