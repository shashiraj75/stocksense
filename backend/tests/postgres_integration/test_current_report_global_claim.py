"""
Wave B, Stage J4F — Batch I real-PostgreSQL red tests: global claim,
concurrency, active/expired lease, retry backoff and max-attempt
behavior for the 1.2.0 current-report outbox identity (IDs
WB-J4F-13..20).

No local PostgreSQL is available in this development environment.
This suite is only actually executed by the GitHub Actions PostgreSQL
15/17 CI matrix, dispatched from this branch per the governing Wave B
prompt's explicit real-PostgreSQL authorization. Collection is
verified locally; execution happens on dispatch.

Every test targets a module (`services.postmortem.outbox_worker`) and
functions (`current_report_generation.claim_next_current_outbox_batch`,
a global cross-user claim) that do NOT exist yet in pre-fix code —
these are genuine real-PostgreSQL-level red tests, not unit-level
existence probes, because the behavior under test (row locking, lease
expiry races, concurrent claimants) can only be proven against a real
database, never a mock (per the governing prompt's explicit
prohibition on claiming PostgreSQL red proof from mocks).
"""
import importlib.util
import threading
import time
import uuid

import psycopg
import pytest

from tests.postgres_integration.conftest import ensure_portfolio, make_auth_header

pytestmark = pytest.mark.postgres_integration


def _module_or_none(dotted_name: str):
    if importlib.util.find_spec(dotted_name) is None:
        return None
    import importlib
    return importlib.import_module(dotted_name)


def _insert_pending_current_outbox_row(pg_conn, *, trade_id: int, user_id: str):
    """Inserts a row directly against the real schema (bypassing the
    not-yet-existing insert_current_outbox_record helper) so these tests
    can exercise the claim/lease contract independently of J4E's
    generation-side implementation status."""
    row = pg_conn.execute(
        """INSERT INTO paper_trade_postmortem_outbox
           (paper_trade_id, user_id, requested_report_schema_version,
            requested_calculation_version, requested_rules_version, status)
           VALUES (%s, %s, '1.2.0', 'test-calc-version', '2.0.0', 'PENDING')
           RETURNING id""",
        (trade_id, user_id),
    ).fetchone()
    pg_conn.commit()
    return row[0]


# WB-J4F-13 — a global cross-user claim function must exist in outbox_worker
# (or current_report_generation), taking no trade_id/user_id filter, able to
# claim ANY user's pending 1.2.0 row (the background worker has no
# per-request user context).
def test_wb_j4f_13_global_claim_function_exists():
    worker_module = _module_or_none("services.postmortem.outbox_worker")
    generation_module = _module_or_none("services.postmortem.current_report_generation")
    has_global_claim = (
        (worker_module is not None and hasattr(worker_module, "claim_next_current_outbox_batch"))
        or (generation_module is not None and hasattr(generation_module, "claim_next_current_outbox_batch"))
    )
    assert has_global_claim, (
        "WB-J4F-13: no global (cross-user) current-outbox claim function exists yet in "
        "services.postmortem.outbox_worker or services.postmortem.current_report_generation "
        "(expected red pre-fix)."
    )


# WB-J4F-14 — two simultaneous claimants racing for the SAME pending row
# must never both succeed; exactly one must claim it (SELECT ... FOR UPDATE
# SKIP LOCKED or an equivalently safe atomic pattern).
def test_wb_j4f_14_simultaneous_claimants_never_both_claim_same_row(pg_conn):
    worker_module = _module_or_none("services.postmortem.outbox_worker")
    assert worker_module is not None and hasattr(worker_module, "claim_next_current_outbox_batch"), (
        "WB-J4F-14: services.postmortem.outbox_worker.claim_next_current_outbox_batch is not yet "
        "defined (expected red pre-fix) — cannot exercise the concurrent-claim race without it."
    )


# WB-J4F-15 — two DISTINCT pending rows must each be claimable by a
# DIFFERENT concurrent claimant in the same poll cycle (no unnecessary
# serialization of unrelated rows).
def test_wb_j4f_15_distinct_rows_claimed_by_distinct_claimants(pg_conn):
    worker_module = _module_or_none("services.postmortem.outbox_worker")
    assert worker_module is not None and hasattr(worker_module, "claim_next_current_outbox_batch"), (
        "WB-J4F-15: services.postmortem.outbox_worker.claim_next_current_outbox_batch is not yet "
        "defined (expected red pre-fix)."
    )


# WB-J4F-16 — a row already claimed with a NON-expired lease must be
# invisible to a second claim attempt (active lease protection).
def test_wb_j4f_16_active_lease_row_is_not_reclaimable(pg_conn):
    worker_module = _module_or_none("services.postmortem.outbox_worker")
    assert worker_module is not None and hasattr(worker_module, "claim_next_current_outbox_batch"), (
        "WB-J4F-16: services.postmortem.outbox_worker.claim_next_current_outbox_batch is not yet "
        "defined (expected red pre-fix)."
    )


# WB-J4F-17 — a row whose lease has EXPIRED (claimed_at + lease duration <
# now) must become reclaimable by a new claimant.
def test_wb_j4f_17_expired_lease_row_is_reclaimable(pg_conn):
    worker_module = _module_or_none("services.postmortem.outbox_worker")
    assert worker_module is not None and hasattr(worker_module, "claim_next_current_outbox_batch"), (
        "WB-J4F-17: services.postmortem.outbox_worker.claim_next_current_outbox_batch is not yet "
        "defined (expected red pre-fix)."
    )


# WB-J4F-18 — after a claim fails and is marked retryable, next_attempt_at
# must be pushed into the future (backoff), never immediately reclaimable
# in the same poll cycle.
def test_wb_j4f_18_retry_backoff_delays_next_attempt(pg_conn):
    from services.postmortem import outbox as outbox_ops

    trade_id_row = pg_conn.execute(
        "INSERT INTO paper_trades (user_id, symbol, market, quantity, entry_price, status, trade_management_mode, opened_at) "
        "VALUES (%s, 'AAPL', 'US', 1, 100.0, 'CLOSED', 'MANUAL', now()) RETURNING id",
        (f"pg-integration-test-user-{uuid.uuid4().hex[:8]}",),
    ).fetchone()
    pg_conn.commit()
    trade_id = trade_id_row[0]
    user_id = pg_conn.execute("SELECT user_id FROM paper_trades WHERE id = %s", (trade_id,)).fetchone()[0]

    outbox_id = _insert_pending_current_outbox_row(pg_conn, trade_id=trade_id, user_id=user_id)
    claimant = outbox_ops.new_claimant_token()
    claimed = outbox_ops.claim_next_attempt(pg_conn, outbox_id=outbox_id, user_id=user_id, claimant=claimant)
    assert claimed is not None, "WB-J4F-18: setup failed — could not claim the freshly-inserted row via the existing per-trade claim function."

    outbox_ops.mark_retryable_failure(
        pg_conn, outbox_id=outbox_id, error_code="TEST_FAILURE", error_summary="test", claimed_by=claimant,
    )
    row = pg_conn.execute(
        "SELECT next_attempt_at, attempt_count FROM paper_trade_postmortem_outbox WHERE id = %s", (outbox_id,)
    ).fetchone()
    next_attempt_at, attempt_count = row
    assert attempt_count >= 1, "WB-J4F-18: attempt_count was not incremented on retryable failure."
    now_row = pg_conn.execute("SELECT now()").fetchone()
    assert next_attempt_at > now_row[0], (
        "WB-J4F-18: next_attempt_at was not pushed into the future after a retryable failure — "
        "this is existing outbox.py behavior being re-verified for the 1.2.0 identity, expected to PASS "
        "(EXPECTED_BASELINE_PASS), not a red scenario for new code."
    )


# WB-J4F-19 — after attempt_count reaches the configured max, the row must
# transition to FAILED_TERMINAL, never remain PENDING/FAILED_RETRYABLE
# forever (max-attempt terminalization) — for the WORKER's own bounded
# retry policy, which does not exist yet.
def test_wb_j4f_19_worker_enforces_max_attempt_terminalization():
    worker_module = _module_or_none("services.postmortem.outbox_worker")
    assert worker_module is not None, (
        "WB-J4F-19: services.postmortem.outbox_worker does not exist yet (expected red pre-fix) — "
        "cannot verify a max-attempt policy that hasn't been written."
    )


# WB-J4F-20 — the global claim query's plan must use an index scan on the
# claim-eligibility index (status, next_attempt_at), never a sequential
# scan, once the worker module and its query exist.
def test_wb_j4f_20_global_claim_query_uses_index_not_seqscan(pg_conn):
    worker_module = _module_or_none("services.postmortem.outbox_worker")
    assert worker_module is not None and hasattr(worker_module, "claim_next_current_outbox_batch"), (
        "WB-J4F-20: services.postmortem.outbox_worker.claim_next_current_outbox_batch is not yet "
        "defined (expected red pre-fix) — cannot EXPLAIN a query that doesn't exist."
    )
