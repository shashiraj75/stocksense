"""
Trade Postmortem Engine, Sprint 2 — unit tests for
services.postmortem.outbox: atomic claim, terminal/retryable marking.

Uses a minimal in-memory fake matching only the WHERE-clause conditions
outbox.py's own UPDATE statements depend on (status membership, ownership)
— sufficient to prove the atomic-claim contract ("only a row currently in
a claimable status, owned by this user, can be claimed") without a real
database. Real-PostgreSQL row-lock concurrency proof lives in
tests/postgres_integration/.
"""
import threading

import pytest

from services.postmortem.outbox import (
    MAX_ATTEMPTS_BEFORE_TERMINAL,
    claim_next_attempt,
    find_claimable_outbox_for_trade,
    mark_retryable_failure,
    mark_terminal,
    mark_terminal_failure,
)


class _FakeConn:
    def __init__(self, rows: dict, lock: threading.Lock):
        self.rows = rows
        self.lock = lock

    def execute(self, sql, params):
        stripped = sql.strip()
        if stripped.startswith("UPDATE paper_trade_postmortem_outbox\n           SET status = 'GENERATING'") or \
           "SET status = 'GENERATING'" in sql:
            outbox_id, user_id, claimable = params
            with self.lock:
                row = self.rows.get(outbox_id)
                if row is None or row["user_id"] != user_id or row["status"] not in claimable:
                    self._pending = None
                else:
                    row["status"] = "GENERATING"
                    row["attempt_count"] += 1
                    self._pending = (
                        row["id"], row["paper_trade_id"], row["user_id"],
                        row["requested_report_schema_version"], row["requested_calculation_version"],
                        row["requested_rules_version"], row["status"], row["attempt_count"],
                        row["source_request_id"],
                    )
            return self
        if "SET status = %s, completed_at = now()" in sql:
            status, outbox_id = params
            self.rows[outbox_id]["status"] = status
            self._pending = None
            return self
        if "SET status = 'FAILED_RETRYABLE'" in sql:
            error_code, error_summary, backoff, outbox_id = params
            row = self.rows[outbox_id]
            row["status"] = "FAILED_RETRYABLE"
            row["last_error_code"] = error_code
            row["last_error_summary"] = error_summary
            self._pending = None
            return self
        if "SET status = 'FAILED_TERMINAL'" in sql:
            error_code, error_summary, outbox_id = params
            row = self.rows[outbox_id]
            row["status"] = "FAILED_TERMINAL"
            row["last_error_code"] = error_code
            row["last_error_summary"] = error_summary
            self._pending = None
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
                    row["source_request_id"],
                )
            return self
        raise AssertionError(f"unexpected SQL in fake outbox conn: {sql!r}")

    def fetchone(self):
        return self._pending


def _row(id=1, paper_trade_id=1, user_id="user-aaa", status="PENDING"):
    return {
        "id": id, "paper_trade_id": paper_trade_id, "user_id": user_id,
        "requested_report_schema_version": "1.0.0", "requested_calculation_version": "1.0.0",
        "requested_rules_version": "2.0.0", "status": status, "attempt_count": 0,
        "source_request_id": None, "last_error_code": None, "last_error_summary": None,
    }


@pytest.mark.unit
class TestClaimNextAttempt:
    def test_pending_row_is_claimable(self):
        rows = {1: _row(status="PENDING")}
        conn = _FakeConn(rows, threading.Lock())
        claimed = claim_next_attempt(conn, outbox_id=1, user_id="user-aaa")
        assert claimed is not None
        assert claimed.status == "GENERATING"
        assert claimed.attempt_count == 1

    def test_failed_retryable_row_is_claimable(self):
        rows = {1: _row(status="FAILED_RETRYABLE")}
        conn = _FakeConn(rows, threading.Lock())
        claimed = claim_next_attempt(conn, outbox_id=1, user_id="user-aaa")
        assert claimed is not None

    @pytest.mark.parametrize("status", ["GENERATING", "COMPLETE", "LIMITED_EVIDENCE", "FAILED_TERMINAL"])
    def test_non_claimable_statuses_return_none(self, status):
        rows = {1: _row(status=status)}
        conn = _FakeConn(rows, threading.Lock())
        assert claim_next_attempt(conn, outbox_id=1, user_id="user-aaa") is None
        assert rows[1]["status"] == status  # untouched

    def test_wrong_user_cannot_claim(self):
        rows = {1: _row(user_id="owner", status="PENDING")}
        conn = _FakeConn(rows, threading.Lock())
        assert claim_next_attempt(conn, outbox_id=1, user_id="attacker") is None
        assert rows[1]["status"] == "PENDING"

    def test_nonexistent_row_returns_none(self):
        conn = _FakeConn({}, threading.Lock())
        assert claim_next_attempt(conn, outbox_id=999, user_id="user-aaa") is None

    def test_concurrent_claims_only_one_winner(self):
        rows = {1: _row(status="PENDING")}
        lock = threading.Lock()
        results = {"claimed": 0, "none": 0}
        results_lock = threading.Lock()

        def _attempt():
            claimed = claim_next_attempt(_FakeConn(rows, lock), outbox_id=1, user_id="user-aaa")
            with results_lock:
                results["claimed" if claimed is not None else "none"] += 1

        threads = [threading.Thread(target=_attempt) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results["claimed"] == 1
        assert results["none"] == 4


@pytest.mark.unit
class TestMarkTerminal:
    @pytest.mark.parametrize("status", ["COMPLETE", "LIMITED_EVIDENCE", "FAILED_TERMINAL"])
    def test_accepts_valid_terminal_statuses(self, status):
        rows = {1: _row(status="GENERATING")}
        conn = _FakeConn(rows, threading.Lock())
        mark_terminal(conn, outbox_id=1, status=status)
        assert rows[1]["status"] == status

    def test_rejects_non_terminal_status(self):
        rows = {1: _row(status="GENERATING")}
        conn = _FakeConn(rows, threading.Lock())
        with pytest.raises(ValueError):
            mark_terminal(conn, outbox_id=1, status="PENDING")

    def test_rejects_arbitrary_string(self):
        rows = {1: _row(status="GENERATING")}
        conn = _FakeConn(rows, threading.Lock())
        with pytest.raises(ValueError):
            mark_terminal(conn, outbox_id=1, status="not_a_real_status")


@pytest.mark.unit
class TestMarkFailures:
    def test_mark_retryable_failure_never_stores_raw_message(self):
        """error_summary must be the caller-supplied sanitized string
        (e.g. an exception class name), never asserted to equal a raw
        message — this test only proves the function stores exactly what
        it's given, the privacy discipline itself lives at the call site
        (paper_trading.py logs type(exc).__name__, never str(exc))."""
        rows = {1: _row(status="GENERATING")}
        conn = _FakeConn(rows, threading.Lock())
        mark_retryable_failure(conn, outbox_id=1, error_code="GENERATION_ERROR", error_summary="ValueError")
        assert rows[1]["status"] == "FAILED_RETRYABLE"
        assert rows[1]["last_error_code"] == "GENERATION_ERROR"
        assert rows[1]["last_error_summary"] == "ValueError"

    def test_mark_terminal_failure(self):
        rows = {1: _row(status="GENERATING")}
        conn = _FakeConn(rows, threading.Lock())
        mark_terminal_failure(conn, outbox_id=1, error_code="TRADE_MISSING", error_summary="trade row not found")
        assert rows[1]["status"] == "FAILED_TERMINAL"


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
def test_max_attempts_constant_is_positive():
    assert MAX_ATTEMPTS_BEFORE_TERMINAL > 0
