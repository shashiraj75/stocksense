"""
Trade Postmortem Sprint 3A — direct, isolated real-PostgreSQL regression
coverage for services.postmortem.outbox.claim_next_attempt (and its
mark_terminal/mark_retryable_failure siblings).

This is the DIRECT primitive-level regression for the double-claim
defect fixed in commit 5c524b6: claim_next_attempt used to compute its
claimable-row guard inside a `WITH claimable AS (...)` CTE, which
Postgres evaluates once from the pre-lock-wait snapshot — under genuine
simultaneous contention on the SAME row, a second transaction unblocking
from a lock wait could still win a claim the guard should have excluded,
because EvalPlanQual only re-verifies the join key, never re-executes
the CTE. The fix moved every guard condition onto the target row
directly in a single UPDATE.

Deliberately calls services.postmortem.outbox functions DIRECTLY —
never through FastAPI, TestClient, provider acquisition, report
construction, or price_path_generation/report_store. The existing
endpoint-level true-concurrency test
(test_price_path_endpoint_lifecycle.py::TestTrueConcurrentGenerate)
remains the symptom-level reproducer; this file is the primitive-level
proof the shared durability building block itself is correct,
independent of any one caller.

No paper_trades FK exists on paper_trade_postmortem_outbox.paper_trade_id
(a plain BIGINT NOT NULL) — every test here uses a synthetic sentinel
trade id and inserts its own disposable outbox row directly, cleaned up
by conftest's autouse `_cleanup_synthetic_rows` (keyed on the
SYNTHETIC_USER_PREFIX user_id).
"""
import datetime as dt
import threading

import psycopg
import pytest

from services.postmortem.outbox import (
    LEASE_DURATION_SECONDS,
    MAX_ATTEMPTS_BEFORE_TERMINAL,
    claim_next_attempt,
    mark_retryable_failure,
    mark_terminal,
    new_claimant_token,
)

pytestmark = pytest.mark.postgres_integration


def _insert_outbox_row(
    conn, *, trade_id: int, user_id: str, status: str = "PENDING", attempt_count: int = 0,
    next_attempt_at=None, lease_expires_at=None, claimed_by=None,
) -> int:
    # next_attempt_at is NOT NULL DEFAULT now() in the real schema —
    # passing an explicit NULL parameter overrides the column default
    # rather than falling back to it, so a caller not specifying one
    # gets `now()` here instead of a literal NULL.
    if next_attempt_at is None:
        next_attempt_at = dt.datetime.now(dt.timezone.utc)
    row = conn.execute(
        """INSERT INTO paper_trade_postmortem_outbox
               (paper_trade_id, user_id, requested_report_schema_version,
                requested_calculation_version, requested_rules_version,
                status, attempt_count, next_attempt_at, lease_expires_at, claimed_by)
           VALUES (%s, %s, '1.0.0', '1.0.0', '1.0.0', %s, %s, %s, %s, %s)
           RETURNING id""",
        (trade_id, user_id, status, attempt_count, next_attempt_at, lease_expires_at, claimed_by),
    ).fetchone()
    return row[0]


def _fetch_row(conn, outbox_id: int):
    return conn.execute(
        """SELECT status, attempt_count, claimed_by, claimed_at, lease_expires_at,
                  completed_at, last_error_code, next_attempt_at
           FROM paper_trade_postmortem_outbox WHERE id = %s""",
        (outbox_id,),
    ).fetchone()


def _run_two_workers(pg_database_url, *, outbox_id, user_id, claimant_a, claimant_b):
    """Two INDEPENDENT real connections, two real threads, released
    together by a barrier — exactly the shape the fixed defect requires
    to reproduce. Returns (result_a, result_b, exceptions)."""
    barrier = threading.Barrier(2, timeout=15)
    results = {}
    exceptions = []

    def _worker(key, claimant):
        try:
            with psycopg.connect(pg_database_url, autocommit=True) as conn:
                barrier.wait()
                results[key] = claim_next_attempt(conn, outbox_id=outbox_id, user_id=user_id, claimant=claimant)
        except Exception as exc:  # pragma: no cover - surfaced via exceptions below
            exceptions.append(exc)

    threads = [
        threading.Thread(target=_worker, args=("a", claimant_a)),
        threading.Thread(target=_worker, args=("b", claimant_b)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not any(t.is_alive() for t in threads), "a worker thread did not finish within the join timeout"
    assert exceptions == [], f"worker thread(s) raised: {exceptions}"
    assert len(results) == 2, f"expected exactly two captured results, got {results}"
    return results["a"], results["b"]


@pytest.mark.timeout(30)
class TestPendingRowDoubleClaimRace:
    """Stage 2 — the direct regression for the 5c524b6 defect: one
    PENDING row, two real connections/threads released simultaneously
    via a barrier, distinct claimant tokens."""

    def test_exactly_one_winner_under_true_simultaneous_contention(self, pg_database_url, pg_conn, unique_user_id):
        trade_id = 900_000_001
        outbox_id = _insert_outbox_row(pg_conn, trade_id=trade_id, user_id=unique_user_id, status="PENDING")
        claimant_a, claimant_b = new_claimant_token(), new_claimant_token()

        result_a, result_b = _run_two_workers(
            pg_database_url, outbox_id=outbox_id, user_id=unique_user_id,
            claimant_a=claimant_a, claimant_b=claimant_b,
        )

        winners = [r for r in (result_a, result_b) if r is not None]
        losers = [r for r in (result_a, result_b) if r is None]
        assert len(winners) == 1, f"expected exactly one non-None claim result, got {(result_a, result_b)}"
        assert len(losers) == 1

        won = winners[0]
        assert won.status == "GENERATING"
        assert won.attempt_count == 1
        assert won.claimed_by in (claimant_a, claimant_b)

        # No second claimant token ever appears as a successful claim —
        # the winning token in the RETURNING row must be internally
        # consistent with whichever result object actually came back
        # non-None (never the OTHER thread's token).
        if result_a is not None:
            assert result_a.claimed_by == claimant_a
        if result_b is not None:
            assert result_b.claimed_by == claimant_b

        status, attempt_count, claimed_by, claimed_at, lease_expires_at, completed_at, error_code, _ = _fetch_row(
            pg_conn, outbox_id
        )
        assert status == "GENERATING"
        assert attempt_count == 1
        assert claimed_by == won.claimed_by
        assert claimed_at is not None
        assert lease_expires_at is not None
        assert completed_at is None
        assert error_code is None


@pytest.mark.timeout(30)
class TestExpiredLeaseReclaimRace:
    """Stage 3 — a GENERATING row whose lease has already expired is a
    legitimately reclaimable row; two simultaneous reclaimers must still
    produce exactly one winner, never two."""

    def test_exactly_one_reclaimer_wins(self, pg_database_url, pg_conn, unique_user_id):
        trade_id = 900_000_002
        prior_claimant = new_claimant_token()
        expired = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=5)
        outbox_id = _insert_outbox_row(
            pg_conn, trade_id=trade_id, user_id=unique_user_id, status="GENERATING",
            attempt_count=1, lease_expires_at=expired, claimed_by=prior_claimant,
        )
        claimant_a, claimant_b = new_claimant_token(), new_claimant_token()

        result_a, result_b = _run_two_workers(
            pg_database_url, outbox_id=outbox_id, user_id=unique_user_id,
            claimant_a=claimant_a, claimant_b=claimant_b,
        )

        winners = [r for r in (result_a, result_b) if r is not None]
        assert len(winners) == 1
        won = winners[0]
        assert won.status == "GENERATING"
        assert won.attempt_count == 2  # incremented exactly once from 1
        assert won.claimed_by != prior_claimant
        assert won.claimed_by in (claimant_a, claimant_b)

        status, attempt_count, claimed_by, claimed_at, lease_expires_at, completed_at, error_code, _ = _fetch_row(
            pg_conn, outbox_id
        )
        assert attempt_count == 2
        assert claimed_by == won.claimed_by
        assert lease_expires_at > expired  # lease moved forward

        # The prior (expired) claimant can no longer settle the row —
        # its own token no longer matches the current lease holder.
        assert mark_terminal(pg_conn, outbox_id=outbox_id, status="COMPLETE", claimed_by=prior_claimant) is False
        status_after, *_ = _fetch_row(pg_conn, outbox_id)
        assert status_after == "GENERATING"  # unchanged by the stale attempt


@pytest.mark.timeout(15)
class TestNonExpiredLeaseNeverReclaimed:
    """Stage 4 — an actively-held, non-expired lease must reject both
    concurrent claimants, not just serialize a second winner."""

    def test_both_claimants_get_none(self, pg_database_url, pg_conn, unique_user_id):
        trade_id = 900_000_003
        current_claimant = new_claimant_token()
        future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=90)
        outbox_id = _insert_outbox_row(
            pg_conn, trade_id=trade_id, user_id=unique_user_id, status="GENERATING",
            attempt_count=1, lease_expires_at=future, claimed_by=current_claimant,
        )

        result_a, result_b = _run_two_workers(
            pg_database_url, outbox_id=outbox_id, user_id=unique_user_id,
            claimant_a=new_claimant_token(), claimant_b=new_claimant_token(),
        )

        assert result_a is None
        assert result_b is None

        status, attempt_count, claimed_by, claimed_at, lease_expires_at, completed_at, error_code, _ = _fetch_row(
            pg_conn, outbox_id
        )
        assert status == "GENERATING"
        assert attempt_count == 1
        assert claimed_by == current_claimant
        assert lease_expires_at == future


@pytest.mark.timeout(15)
class TestRetryableBackoff:
    """Stage 5 — FAILED_RETRYABLE rows are only claimable once
    next_attempt_at has passed; the backoff window itself is enforced by
    the claim UPDATE's own WHERE clause, and a claimable retryable row
    still resolves to exactly one winner under contention."""

    def test_before_backoff_window_is_not_claimable(self, pg_conn, unique_user_id):
        trade_id = 900_000_004
        future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60)
        outbox_id = _insert_outbox_row(
            pg_conn, trade_id=trade_id, user_id=unique_user_id, status="FAILED_RETRYABLE",
            attempt_count=1, next_attempt_at=future,
        )
        result = claim_next_attempt(pg_conn, outbox_id=outbox_id, user_id=unique_user_id, claimant=new_claimant_token())
        assert result is None
        status, attempt_count, *_ = _fetch_row(pg_conn, outbox_id)
        assert status == "FAILED_RETRYABLE"
        assert attempt_count == 1

    def test_after_backoff_window_exactly_one_simultaneous_claimant_wins(self, pg_database_url, pg_conn, unique_user_id):
        trade_id = 900_000_005
        past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=5)
        outbox_id = _insert_outbox_row(
            pg_conn, trade_id=trade_id, user_id=unique_user_id, status="FAILED_RETRYABLE",
            attempt_count=1, next_attempt_at=past,
        )
        claimant_a, claimant_b = new_claimant_token(), new_claimant_token()
        result_a, result_b = _run_two_workers(
            pg_database_url, outbox_id=outbox_id, user_id=unique_user_id,
            claimant_a=claimant_a, claimant_b=claimant_b,
        )
        winners = [r for r in (result_a, result_b) if r is not None]
        losers = [r for r in (result_a, result_b) if r is None]
        assert len(winners) == 1
        assert len(losers) == 1
        assert winners[0].attempt_count == 2
        status, attempt_count, claimed_by, *_ = _fetch_row(pg_conn, outbox_id)
        assert attempt_count == 2
        assert claimed_by == winners[0].claimed_by


@pytest.mark.timeout(15)
class TestAttemptLimitSettlement:
    """Stage 6 — a row whose next attempt would exceed
    MAX_ATTEMPTS_BEFORE_TERMINAL settles FAILED_TERMINAL in the SAME
    atomic claim call, with no provider or report involvement, and a
    simultaneous competing caller must not settle or increment it a
    second time."""

    def test_single_call_settles_failed_terminal(self, pg_conn, unique_user_id):
        trade_id = 900_000_006
        outbox_id = _insert_outbox_row(
            pg_conn, trade_id=trade_id, user_id=unique_user_id, status="PENDING",
            attempt_count=MAX_ATTEMPTS_BEFORE_TERMINAL,
        )
        result = claim_next_attempt(pg_conn, outbox_id=outbox_id, user_id=unique_user_id, claimant=new_claimant_token())
        assert result is not None
        assert result.status == "FAILED_TERMINAL"
        assert result.attempt_count == MAX_ATTEMPTS_BEFORE_TERMINAL + 1

        status, attempt_count, claimed_by, claimed_at, lease_expires_at, completed_at, error_code, _ = _fetch_row(
            pg_conn, outbox_id
        )
        assert status == "FAILED_TERMINAL"
        assert attempt_count == MAX_ATTEMPTS_BEFORE_TERMINAL + 1
        assert claimed_by is None
        assert lease_expires_at is None
        assert completed_at is not None
        assert error_code == "MAX_ATTEMPTS_EXCEEDED"

    def test_simultaneous_competing_caller_does_not_settle_twice(self, pg_database_url, pg_conn, unique_user_id):
        trade_id = 900_000_007
        outbox_id = _insert_outbox_row(
            pg_conn, trade_id=trade_id, user_id=unique_user_id, status="PENDING",
            attempt_count=MAX_ATTEMPTS_BEFORE_TERMINAL,
        )
        claimant_a, claimant_b = new_claimant_token(), new_claimant_token()
        result_a, result_b = _run_two_workers(
            pg_database_url, outbox_id=outbox_id, user_id=unique_user_id,
            claimant_a=claimant_a, claimant_b=claimant_b,
        )
        settled = [r for r in (result_a, result_b) if r is not None]
        assert len(settled) == 1, f"exactly one caller may observe/settle the terminal transition, got {(result_a, result_b)}"
        assert settled[0].status == "FAILED_TERMINAL"
        assert settled[0].attempt_count == MAX_ATTEMPTS_BEFORE_TERMINAL + 1

        status, attempt_count, *_ = _fetch_row(pg_conn, outbox_id)
        assert status == "FAILED_TERMINAL"
        assert attempt_count == MAX_ATTEMPTS_BEFORE_TERMINAL + 1  # incremented exactly once


@pytest.mark.timeout(15)
class TestOwnershipAndStaleClaimant:
    """Stage 7 — user scoping and claimant-token ownership guards,
    exercised directly against real Postgres."""

    def test_wrong_user_cannot_claim(self, pg_conn, unique_user_id):
        trade_id = 900_000_008
        outbox_id = _insert_outbox_row(pg_conn, trade_id=trade_id, user_id=unique_user_id, status="PENDING")
        result = claim_next_attempt(pg_conn, outbox_id=outbox_id, user_id="attacker-user", claimant=new_claimant_token())
        assert result is None
        status, attempt_count, *_ = _fetch_row(pg_conn, outbox_id)
        assert status == "PENDING"
        assert attempt_count == 0

    def test_stale_claimant_cannot_mark_terminal(self, pg_conn, unique_user_id):
        trade_id = 900_000_009
        outbox_id = _insert_outbox_row(pg_conn, trade_id=trade_id, user_id=unique_user_id, status="PENDING")
        real_claimant = new_claimant_token()
        claim_next_attempt(pg_conn, outbox_id=outbox_id, user_id=unique_user_id, claimant=real_claimant)

        assert mark_terminal(pg_conn, outbox_id=outbox_id, status="COMPLETE", claimed_by="a-stale-token") is False
        status, *_ = _fetch_row(pg_conn, outbox_id)
        assert status == "GENERATING"  # unchanged

    def test_stale_claimant_cannot_mark_retryable(self, pg_conn, unique_user_id):
        trade_id = 900_000_010
        outbox_id = _insert_outbox_row(pg_conn, trade_id=trade_id, user_id=unique_user_id, status="PENDING")
        real_claimant = new_claimant_token()
        claim_next_attempt(pg_conn, outbox_id=outbox_id, user_id=unique_user_id, claimant=real_claimant)

        assert mark_retryable_failure(
            pg_conn, outbox_id=outbox_id, error_code="X", error_summary="stale", claimed_by="a-stale-token"
        ) is False
        status, *_ = _fetch_row(pg_conn, outbox_id)
        assert status == "GENERATING"  # unchanged

    def test_current_claimant_marks_terminal_exactly_once(self, pg_conn, unique_user_id):
        trade_id = 900_000_011
        outbox_id = _insert_outbox_row(pg_conn, trade_id=trade_id, user_id=unique_user_id, status="PENDING")
        claimant = new_claimant_token()
        claim_next_attempt(pg_conn, outbox_id=outbox_id, user_id=unique_user_id, claimant=claimant)

        assert mark_terminal(pg_conn, outbox_id=outbox_id, status="COMPLETE", claimed_by=claimant) is True
        status, *_ = _fetch_row(pg_conn, outbox_id)
        assert status == "COMPLETE"

        # Once terminal, the row cannot be reclaimed by anyone.
        result = claim_next_attempt(pg_conn, outbox_id=outbox_id, user_id=unique_user_id, claimant=new_claimant_token())
        assert result is None

        # A second mark_terminal by the same (now-stale-again) claimed_by
        # also fails, since claimed_by was cleared on settlement.
        assert mark_terminal(pg_conn, outbox_id=outbox_id, status="COMPLETE", claimed_by=claimant) is False

    def test_current_claimant_marks_retryable_exactly_once_and_backoff_is_enforced(self, pg_conn, unique_user_id):
        trade_id = 900_000_012
        outbox_id = _insert_outbox_row(pg_conn, trade_id=trade_id, user_id=unique_user_id, status="PENDING")
        claimant = new_claimant_token()
        claim_next_attempt(pg_conn, outbox_id=outbox_id, user_id=unique_user_id, claimant=claimant)

        assert mark_retryable_failure(
            pg_conn, outbox_id=outbox_id, error_code="PROVIDER_TIMEOUT", error_summary="timeout", claimed_by=claimant
        ) is True
        status, attempt_count, claimed_by, claimed_at, lease_expires_at, completed_at, error_code, next_attempt_at = (
            _fetch_row(pg_conn, outbox_id)
        )
        assert status == "FAILED_RETRYABLE"
        assert claimed_by is None
        assert lease_expires_at is None
        assert error_code == "PROVIDER_TIMEOUT"
        assert next_attempt_at is not None and next_attempt_at > dt.datetime.now(dt.timezone.utc)

        # Backoff window not yet elapsed — not immediately reclaimable.
        assert claim_next_attempt(pg_conn, outbox_id=outbox_id, user_id=unique_user_id, claimant=new_claimant_token()) is None

        # A second mark_retryable_failure using the now-stale claimant fails.
        assert mark_retryable_failure(
            pg_conn, outbox_id=outbox_id, error_code="X", error_summary="stale", claimed_by=claimant
        ) is False
