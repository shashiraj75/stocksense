"""
Trade Postmortem Engine, Sprint 2 correction phase — unit tests for
services.postmortem.outbox's lease-based claim/mark contract.

Uses an in-memory fake that models the real UPDATE's actual WHERE-clause
semantics (claimable statuses, retry backoff via next_attempt_at, lease
expiry via lease_expires_at, attempt-limit settling to FAILED_TERMINAL,
and claimed_by ownership matching for every mark_* call) — sufficient to
prove outbox.py's own application-level contract without a real
database. Real-PostgreSQL row-lock concurrency proof (including the
WITH-CTE claim statement actually executing against a live server) lives
in tests/postgres_integration/.
"""
import datetime as dt
import threading

import pytest

from services.postmortem.outbox import (
    LEASE_DURATION_SECONDS,
    MAX_ATTEMPTS_BEFORE_TERMINAL,
    claim_next_attempt,
    find_claimable_outbox_for_trade,
    mark_retryable_failure,
    mark_terminal,
    mark_terminal_failure,
    new_claimant_token,
)


def _now():
    return dt.datetime.now(dt.timezone.utc)


class _FakeConn:
    def __init__(self, rows: dict, lock: threading.Lock):
        self.rows = rows
        self.lock = lock

    def execute(self, sql, params):
        stripped = sql.strip()
        if "UPDATE paper_trade_postmortem_outbox o" in sql and "GENERATING" in sql:
            max_attempts, lease_seconds = params["max_attempts"], params["lease_seconds"]
            claimant, outbox_id, user_id = params["claimant"], params["outbox_id"], params["user_id"]
            now = _now()
            with self.lock:
                row = self.rows.get(outbox_id)
                if row is None or row["user_id"] != user_id:
                    self._pending = None
                    return self
                claimable = (
                    row["status"] == "PENDING"
                    or (row["status"] == "FAILED_RETRYABLE"
                        and (row["next_attempt_at"] is None or row["next_attempt_at"] <= now))
                    or (row["status"] == "GENERATING"
                        and row["lease_expires_at"] is not None and row["lease_expires_at"] <= now)
                )
                if not claimable:
                    self._pending = None
                    return self
                exceeds_limit = (row["attempt_count"] + 1) > max_attempts
                row["attempt_count"] += 1
                row["last_attempt_at"] = now
                if exceeds_limit:
                    row["status"] = "FAILED_TERMINAL"
                    row["lease_expires_at"] = None
                    row["claimed_by"] = None
                    row["completed_at"] = now
                    row["last_error_code"] = "MAX_ATTEMPTS_EXCEEDED"
                    row["last_error_summary"] = "exceeded max attempts before terminal"
                else:
                    row["status"] = "GENERATING"
                    row["claimed_at"] = now
                    row["lease_expires_at"] = now + dt.timedelta(seconds=lease_seconds)
                    row["claimed_by"] = claimant
                self._pending = (
                    row["id"], row["paper_trade_id"], row["user_id"],
                    row["requested_report_schema_version"], row["requested_calculation_version"],
                    row["requested_rules_version"], row["status"], row["attempt_count"],
                    row["source_request_id"], row["claimed_by"],
                )
            return self
        if "SET status = %s, completed_at = now()" in sql:
            status, outbox_id, claimed_by = params
            with self.lock:
                row = self.rows.get(outbox_id)
                if row is None or row["status"] != "GENERATING" or row["claimed_by"] != claimed_by:
                    self._pending = None
                    return self
                row["status"] = status
                row["lease_expires_at"] = None
                row["claimed_by"] = None
            self._pending = (outbox_id,)
            return self
        if "SET status = 'FAILED_RETRYABLE'" in sql:
            error_code, error_summary, backoff, outbox_id, claimed_by = params
            with self.lock:
                row = self.rows.get(outbox_id)
                if row is None or row["status"] != "GENERATING" or row["claimed_by"] != claimed_by:
                    self._pending = None
                    return self
                row["status"] = "FAILED_RETRYABLE"
                row["last_error_code"] = error_code
                row["last_error_summary"] = error_summary
                row["next_attempt_at"] = _now() + dt.timedelta(seconds=backoff)
                row["lease_expires_at"] = None
                row["claimed_by"] = None
            self._pending = (outbox_id,)
            return self
        if "SET status = 'FAILED_TERMINAL'" in sql:
            error_code, error_summary, outbox_id, claimed_by = params
            with self.lock:
                row = self.rows.get(outbox_id)
                if row is None or row["status"] != "GENERATING" or row["claimed_by"] != claimed_by:
                    self._pending = None
                    return self
                row["status"] = "FAILED_TERMINAL"
                row["last_error_code"] = error_code
                row["last_error_summary"] = error_summary
                row["lease_expires_at"] = None
                row["claimed_by"] = None
            self._pending = (outbox_id,)
            return self
        if stripped.startswith("SELECT id, paper_trade_id, user_id"):
            trade_id, user_id = params
            with self.lock:
                matches = [r for r in self.rows.values() if r["paper_trade_id"] == trade_id and r["user_id"] == user_id]
            if not matches:
                self._pending = None
            else:
                row = matches[-1]
                self._pending = (
                    row["id"], row["paper_trade_id"], row["user_id"],
                    row["requested_report_schema_version"], row["requested_calculation_version"],
                    row["requested_rules_version"], row["status"], row["attempt_count"],
                    row["source_request_id"], row["claimed_by"],
                )
            return self
        raise AssertionError(f"unexpected SQL in fake outbox conn: {sql!r}")

    def fetchone(self):
        return self._pending


def _row(id=1, paper_trade_id=1, user_id="user-aaa", status="PENDING", attempt_count=0,
         next_attempt_at=None, lease_expires_at=None, claimed_by=None):
    return {
        "id": id, "paper_trade_id": paper_trade_id, "user_id": user_id,
        "requested_report_schema_version": "1.0.0", "requested_calculation_version": "1.0.0",
        "requested_rules_version": "2.0.0", "status": status, "attempt_count": attempt_count,
        "source_request_id": None, "last_error_code": None, "last_error_summary": None,
        "next_attempt_at": next_attempt_at, "lease_expires_at": lease_expires_at, "claimed_by": claimed_by,
        "claimed_at": None, "completed_at": None,
    }


@pytest.mark.unit
class TestClaimNextAttempt:
    def test_pending_row_is_claimable(self):
        rows = {1: _row(status="PENDING")}
        conn = _FakeConn(rows, threading.Lock())
        claimed = claim_next_attempt(conn, outbox_id=1, user_id="user-aaa", claimant="tok-a")
        assert claimed is not None
        assert claimed.status == "GENERATING"
        assert claimed.attempt_count == 1
        assert claimed.claimed_by == "tok-a"

    def test_eligible_failed_retryable_row_is_claimable(self):
        rows = {1: _row(status="FAILED_RETRYABLE", next_attempt_at=_now() - dt.timedelta(seconds=1))}
        conn = _FakeConn(rows, threading.Lock())
        claimed = claim_next_attempt(conn, outbox_id=1, user_id="user-aaa", claimant="tok-a")
        assert claimed is not None
        assert claimed.status == "GENERATING"

    def test_failed_retryable_before_backoff_window_is_blocked(self):
        """Retry backoff enforced: a FAILED_RETRYABLE row whose
        next_attempt_at is still in the future must not be claimable."""
        rows = {1: _row(status="FAILED_RETRYABLE", next_attempt_at=_now() + dt.timedelta(seconds=30))}
        conn = _FakeConn(rows, threading.Lock())
        assert claim_next_attempt(conn, outbox_id=1, user_id="user-aaa", claimant="tok-a") is None
        assert rows[1]["status"] == "FAILED_RETRYABLE"

    def test_failed_retryable_after_backoff_window_succeeds(self):
        rows = {1: _row(status="FAILED_RETRYABLE", next_attempt_at=_now() - dt.timedelta(milliseconds=1))}
        conn = _FakeConn(rows, threading.Lock())
        claimed = claim_next_attempt(conn, outbox_id=1, user_id="user-aaa", claimant="tok-a")
        assert claimed is not None

    def test_expired_lease_generating_row_is_reclaimable(self):
        """Stale-GENERATING recovery: a row claimed by a prior attempt
        whose lease has since expired (the prior worker crashed and never
        marked it terminal/retryable) is genuinely reclaimable by a new
        claimant."""
        rows = {1: _row(status="GENERATING", lease_expires_at=_now() - dt.timedelta(seconds=1), claimed_by="stale-tok")}
        conn = _FakeConn(rows, threading.Lock())
        claimed = claim_next_attempt(conn, outbox_id=1, user_id="user-aaa", claimant="fresh-tok")
        assert claimed is not None
        assert claimed.status == "GENERATING"
        assert claimed.claimed_by == "fresh-tok"
        assert rows[1]["claimed_by"] == "fresh-tok"

    def test_non_expired_lease_generating_row_cannot_be_reclaimed(self):
        rows = {1: _row(status="GENERATING", lease_expires_at=_now() + dt.timedelta(seconds=60), claimed_by="active-tok")}
        conn = _FakeConn(rows, threading.Lock())
        assert claim_next_attempt(conn, outbox_id=1, user_id="user-aaa", claimant="attacker-tok") is None
        assert rows[1]["claimed_by"] == "active-tok"

    @pytest.mark.parametrize("status", ["COMPLETE", "LIMITED_EVIDENCE", "FAILED_TERMINAL"])
    def test_terminal_statuses_are_never_reclaimable(self, status):
        rows = {1: _row(status=status)}
        conn = _FakeConn(rows, threading.Lock())
        assert claim_next_attempt(conn, outbox_id=1, user_id="user-aaa", claimant="tok-a") is None
        assert rows[1]["status"] == status  # untouched

    def test_wrong_user_cannot_claim(self):
        rows = {1: _row(user_id="owner", status="PENDING")}
        conn = _FakeConn(rows, threading.Lock())
        assert claim_next_attempt(conn, outbox_id=1, user_id="attacker", claimant="tok-a") is None
        assert rows[1]["status"] == "PENDING"

    def test_nonexistent_row_returns_none(self):
        conn = _FakeConn({}, threading.Lock())
        assert claim_next_attempt(conn, outbox_id=999, user_id="user-aaa", claimant="tok-a") is None

    def test_attempt_limit_settles_failed_terminal(self):
        """Attempt-limit enforcement: once the next attempt would exceed
        MAX_ATTEMPTS_BEFORE_TERMINAL, the SAME atomic claim call settles
        the row FAILED_TERMINAL instead of claiming it — no infinite
        retry."""
        rows = {1: _row(status="FAILED_RETRYABLE", attempt_count=MAX_ATTEMPTS_BEFORE_TERMINAL,
                         next_attempt_at=_now() - dt.timedelta(seconds=1))}
        conn = _FakeConn(rows, threading.Lock())
        claimed = claim_next_attempt(conn, outbox_id=1, user_id="user-aaa", claimant="tok-a")
        assert claimed is not None
        assert claimed.status == "FAILED_TERMINAL"
        assert rows[1]["status"] == "FAILED_TERMINAL"
        assert rows[1]["claimed_by"] is None

    def test_concurrent_claims_only_one_winner(self):
        rows = {1: _row(status="PENDING")}
        lock = threading.Lock()
        results = {"claimed": 0, "none": 0}
        results_lock = threading.Lock()

        def _attempt(token):
            claimed = claim_next_attempt(_FakeConn(rows, lock), outbox_id=1, user_id="user-aaa", claimant=token)
            with results_lock:
                results["claimed" if claimed is not None else "none"] += 1

        threads = [threading.Thread(target=_attempt, args=(f"tok-{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results["claimed"] == 1
        assert results["none"] == 4

    def test_concurrent_claims_for_expired_lease_only_one_winner(self):
        """Two claimers racing for the SAME expired lease — exactly one
        must win, matching the fresh-PENDING race above."""
        rows = {1: _row(status="GENERATING", lease_expires_at=_now() - dt.timedelta(seconds=1), claimed_by="stale-tok")}
        lock = threading.Lock()
        results = {"claimed": 0, "none": 0}
        results_lock = threading.Lock()

        def _attempt(token):
            claimed = claim_next_attempt(_FakeConn(rows, lock), outbox_id=1, user_id="user-aaa", claimant=token)
            with results_lock:
                results["claimed" if claimed is not None else "none"] += 1

        threads = [threading.Thread(target=_attempt, args=(f"tok-{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results["claimed"] == 1
        assert results["none"] == 3


@pytest.mark.unit
class TestMarkTerminal:
    @pytest.mark.parametrize("status", ["COMPLETE", "LIMITED_EVIDENCE", "FAILED_TERMINAL"])
    def test_accepts_valid_terminal_statuses_with_matching_lease(self, status):
        rows = {1: _row(status="GENERATING", claimed_by="tok-a")}
        conn = _FakeConn(rows, threading.Lock())
        ok = mark_terminal(conn, outbox_id=1, status=status, claimed_by="tok-a")
        assert ok is True
        assert rows[1]["status"] == status
        assert rows[1]["claimed_by"] is None

    def test_rejects_non_terminal_status(self):
        rows = {1: _row(status="GENERATING", claimed_by="tok-a")}
        conn = _FakeConn(rows, threading.Lock())
        with pytest.raises(ValueError):
            mark_terminal(conn, outbox_id=1, status="PENDING", claimed_by="tok-a")

    def test_rejects_arbitrary_string(self):
        rows = {1: _row(status="GENERATING", claimed_by="tok-a")}
        conn = _FakeConn(rows, threading.Lock())
        with pytest.raises(ValueError):
            mark_terminal(conn, outbox_id=1, status="not_a_real_status", claimed_by="tok-a")

    def test_stale_worker_cannot_mark_terminal_after_lease_reclaimed(self):
        """A stale worker whose lease already expired and was reclaimed
        by a fresh claimant (different claimed_by token) must fail to
        mark the row — never silently overwriting the fresh claimant's
        in-flight or completed work."""
        rows = {1: _row(status="GENERATING", claimed_by="fresh-tok")}  # reclaimed by someone else
        conn = _FakeConn(rows, threading.Lock())
        ok = mark_terminal(conn, outbox_id=1, status="COMPLETE", claimed_by="stale-tok")
        assert ok is False
        assert rows[1]["status"] == "GENERATING"  # untouched — fresh claimant's work stands
        assert rows[1]["claimed_by"] == "fresh-tok"


@pytest.mark.unit
class TestMarkFailures:
    def test_mark_retryable_failure_never_stores_raw_message(self):
        """error_summary must be the caller-supplied sanitized string
        (e.g. an exception class name), never asserted to equal a raw
        message — this test only proves the function stores exactly what
        it's given, the privacy discipline itself lives at the call site
        (paper_trading.py logs type(exc).__name__, never str(exc))."""
        rows = {1: _row(status="GENERATING", claimed_by="tok-a")}
        conn = _FakeConn(rows, threading.Lock())
        ok = mark_retryable_failure(
            conn, outbox_id=1, error_code="GENERATION_ERROR", error_summary="ValueError", claimed_by="tok-a"
        )
        assert ok is True
        assert rows[1]["status"] == "FAILED_RETRYABLE"
        assert rows[1]["last_error_code"] == "GENERATION_ERROR"
        assert rows[1]["last_error_summary"] == "ValueError"
        assert rows[1]["claimed_by"] is None

    def test_mark_retryable_failure_stale_worker_no_op(self):
        rows = {1: _row(status="GENERATING", claimed_by="fresh-tok")}
        conn = _FakeConn(rows, threading.Lock())
        ok = mark_retryable_failure(
            conn, outbox_id=1, error_code="X", error_summary="Y", claimed_by="stale-tok"
        )
        assert ok is False
        assert rows[1]["status"] == "GENERATING"

    def test_mark_terminal_failure(self):
        rows = {1: _row(status="GENERATING", claimed_by="tok-a")}
        conn = _FakeConn(rows, threading.Lock())
        ok = mark_terminal_failure(
            conn, outbox_id=1, error_code="TRADE_MISSING", error_summary="trade row not found", claimed_by="tok-a"
        )
        assert ok is True
        assert rows[1]["status"] == "FAILED_TERMINAL"

    def test_mark_terminal_failure_stale_worker_no_op(self):
        rows = {1: _row(status="GENERATING", claimed_by="fresh-tok")}
        conn = _FakeConn(rows, threading.Lock())
        ok = mark_terminal_failure(
            conn, outbox_id=1, error_code="X", error_summary="Y", claimed_by="stale-tok"
        )
        assert ok is False
        assert rows[1]["status"] == "GENERATING"


@pytest.mark.unit
class TestFindClaimableOutboxForTrade:
    def test_finds_existing_row_for_trade_and_user(self):
        rows = {1: _row(paper_trade_id=42, user_id="user-aaa")}
        conn = _FakeConn(rows, threading.Lock())
        found = find_claimable_outbox_for_trade(conn, trade_id=42, user_id="user-aaa")
        assert found is not None
        assert found.id == 1

    def test_returns_none_for_other_user(self):
        rows = {1: _row(paper_trade_id=42, user_id="owner")}
        conn = _FakeConn(rows, threading.Lock())
        assert find_claimable_outbox_for_trade(conn, trade_id=42, user_id="attacker") is None

    def test_returns_none_when_no_row_exists(self):
        conn = _FakeConn({}, threading.Lock())
        assert find_claimable_outbox_for_trade(conn, trade_id=999, user_id="user-aaa") is None


@pytest.mark.unit
class TestConstants:
    def test_max_attempts_constant_is_positive(self):
        assert MAX_ATTEMPTS_BEFORE_TERMINAL > 0

    def test_lease_duration_constant_is_positive(self):
        assert LEASE_DURATION_SECONDS > 0

    def test_claimant_tokens_are_unique_and_non_empty(self):
        tokens = {new_claimant_token() for _ in range(100)}
        assert len(tokens) == 100
        assert all(t for t in tokens)
