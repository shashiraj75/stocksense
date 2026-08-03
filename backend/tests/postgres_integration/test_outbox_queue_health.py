"""
Wave C, WC-O (corrected) §O6 — compact real-PostgreSQL proof for
services.postmortem.outbox_queue_health.read_queue_health_snapshot.

One shared setup seeding every governed status plus a different-version-
identity row and multiple users/trades, then a small number of
behavioral tests (not one per output field) proving: exact status
counts, exact version isolation, active-vs-expired lease classification,
age fields use the documented timestamps, the >120s processing count,
null ages for empty categories, and that the read causes no database
mutation.

paper_trade_postmortem_outbox has no foreign-key constraint on
paper_trade_id (confirmed by schema inspection) — these tests use
synthetic trade IDs directly, never creating real paper_trades rows,
since the snapshot query never joins to that table.
"""
import uuid

import pytest

from services.postmortem.outbox_queue_health import read_queue_health_snapshot
from tests.postgres_integration.conftest import SYNTHETIC_USER_PREFIX

pytestmark = pytest.mark.postgres_integration

_SCHEMA_V = "1.2.0"


def _insert_outbox_row(
    pg_conn, *, trade_id: int, user_id: str, status: str, calc_version: str, rules_version: str,
    created_at_offset: str | None = None,
    claimed_at_offset: str | None = None,
    lease_expires_offset: str | None = None,
    last_attempt_at_offset: str | None = None,
):
    """`*_offset` are SQL interval literals like "'-200 seconds'" applied
    via `now() + interval ...` — None means the column stays at its
    table default (NULL for claimed_at/lease_expires_at/last_attempt_at,
    now() for created_at)."""
    created_at_expr = f"now() + interval {created_at_offset}" if created_at_offset else "now()"
    claimed_at_expr = f"now() + interval {claimed_at_offset}" if claimed_at_offset else "NULL"
    lease_expr = f"now() + interval {lease_expires_offset}" if lease_expires_offset else "NULL"
    last_attempt_expr = f"now() + interval {last_attempt_at_offset}" if last_attempt_at_offset else "NULL"
    pg_conn.execute(
        f"""INSERT INTO paper_trade_postmortem_outbox
           (paper_trade_id, user_id, requested_report_schema_version,
            requested_calculation_version, requested_rules_version, status,
            created_at, claimed_at, lease_expires_at, last_attempt_at)
           VALUES (%s, %s, %s, %s, %s, %s, {created_at_expr}, {claimed_at_expr}, {lease_expr}, {last_attempt_expr})""",
        (trade_id, user_id, _SCHEMA_V, calc_version, rules_version, status),
    )


@pytest.fixture
def seeded_queue(pg_conn):
    """Seeds one row per governed status under a per-invocation-UNIQUE
    identity (never a fixed literal — a fixed calc/rules version
    collided with leftover rows across CI reruns, since these synthetic
    user IDs must use SYNTHETIC_USER_PREFIX for the suite's own
    autouse cleanup to actually delete them; the version identity is
    still randomized per invocation as defense in depth), plus a row
    for a DIFFERENT calculation_version (must never be counted), across
    multiple synthetic users/trades. Trade IDs are also randomized —
    the unique index is (paper_trade_id, schema, calc, rules), so even
    reusing trade IDs across test runs is only safe when at least one
    of those four is guaranteed unique per invocation."""
    suffix = uuid.uuid4().hex[:12]
    calc_version = f"queue-health-test-calc-{suffix}"
    other_calc_version = f"queue-health-test-calc-{suffix}-DIFFERENT"
    rules_version = f"queue-health-test-rules-{suffix}"
    user_a = f"{SYNTHETIC_USER_PREFIX}qh-a-{suffix}"
    user_b = f"{SYNTHETIC_USER_PREFIX}qh-b-{suffix}"
    base_trade_id = int(suffix[:8], 16) % 1_000_000_000

    _insert_outbox_row(
        pg_conn, trade_id=base_trade_id + 1, user_id=user_a, status="PENDING",
        calc_version=calc_version, rules_version=rules_version, created_at_offset="'-30 seconds'",
    )
    _insert_outbox_row(
        pg_conn, trade_id=base_trade_id + 2, user_id=user_a, status="GENERATING",
        calc_version=calc_version, rules_version=rules_version,
        claimed_at_offset="'-10 seconds'", lease_expires_offset="'+110 seconds'",
    )
    _insert_outbox_row(
        pg_conn, trade_id=base_trade_id + 3, user_id=user_b, status="GENERATING",
        calc_version=calc_version, rules_version=rules_version,
        claimed_at_offset="'-200 seconds'", lease_expires_offset="'-80 seconds'",
    )
    _insert_outbox_row(
        pg_conn, trade_id=base_trade_id + 4, user_id=user_b, status="FAILED_RETRYABLE",
        calc_version=calc_version, rules_version=rules_version, last_attempt_at_offset="'-45 seconds'",
    )
    _insert_outbox_row(
        pg_conn, trade_id=base_trade_id + 5, user_id=user_a, status="FAILED_TERMINAL",
        calc_version=calc_version, rules_version=rules_version,
    )
    # A GENERATING row older than the 120s frontend poll window but whose
    # lease has NOT yet expired — proves the two counts are independent.
    _insert_outbox_row(
        pg_conn, trade_id=base_trade_id + 6, user_id=user_b, status="GENERATING",
        calc_version=calc_version, rules_version=rules_version,
        claimed_at_offset="'-150 seconds'", lease_expires_offset="'+30 seconds'",
    )
    # A row under a DIFFERENT version identity — must never be counted.
    _insert_outbox_row(
        pg_conn, trade_id=base_trade_id + 7, user_id=user_a, status="PENDING",
        calc_version=other_calc_version, rules_version=rules_version,
    )
    return {
        "user_a": user_a, "user_b": user_b,
        "calc_version": calc_version, "other_calc_version": other_calc_version, "rules_version": rules_version,
    }


def test_exact_status_counts_and_version_isolation(pg_conn, seeded_queue):
    snapshot = read_queue_health_snapshot(
        pg_conn, schema_version=_SCHEMA_V, calculation_version=seeded_queue["calc_version"],
        rules_version=seeded_queue["rules_version"],
    )
    assert snapshot.pending == 1
    # 2 GENERATING rows have a non-expired lease (base+2, base+6); base+3 has an expired lease.
    assert snapshot.generating_active == 2
    assert snapshot.generating_expired_lease == 1
    assert snapshot.failed_retryable == 1
    assert snapshot.failed_terminal == 1

    # The different-identity row (base+7) must never be counted here.
    other_snapshot = read_queue_health_snapshot(
        pg_conn, schema_version=_SCHEMA_V, calculation_version=seeded_queue["other_calc_version"],
        rules_version=seeded_queue["rules_version"],
    )
    assert other_snapshot.pending == 1
    assert other_snapshot.generating_active == 0
    assert other_snapshot.failed_terminal == 0


def test_active_vs_expired_lease_classification_and_over_poll_window_count(pg_conn, seeded_queue):
    snapshot = read_queue_health_snapshot(
        pg_conn, schema_version=_SCHEMA_V, calculation_version=seeded_queue["calc_version"],
        rules_version=seeded_queue["rules_version"],
    )
    # base+3 (claimed 200s ago, expired) and base+6 (claimed 150s ago,
    # still has an active lease) are BOTH over the 120s frontend poll
    # window — this count is independent of lease-expiry classification.
    assert snapshot.generating_over_poll_window_count == 2


def test_age_fields_use_the_documented_timestamps(pg_conn, seeded_queue):
    snapshot = read_queue_health_snapshot(
        pg_conn, schema_version=_SCHEMA_V, calculation_version=seeded_queue["calc_version"],
        rules_version=seeded_queue["rules_version"],
    )
    # Pending age derives from created_at (base+1 was created ~30s ago).
    assert snapshot.oldest_pending_age_seconds is not None
    assert 25 <= snapshot.oldest_pending_age_seconds <= 40
    # Generating age derives from claimed_at — the OLDEST (largest) age
    # among GENERATING rows is base+3's ~200s.
    assert snapshot.oldest_generating_age_seconds is not None
    assert 190 <= snapshot.oldest_generating_age_seconds <= 215
    # Failed-retryable age derives from last_attempt_at (base+4, ~45s ago)
    # — never from next_attempt_at (future eligibility).
    assert snapshot.oldest_failed_retryable_age_seconds is not None
    assert 40 <= snapshot.oldest_failed_retryable_age_seconds <= 55


def test_null_ages_and_zero_counts_when_a_category_is_empty(pg_conn):
    """An identity with NO rows at all — every count is exactly zero and
    every age is None, never a fabricated zero-age."""
    empty_rules_version = f"empty-{uuid.uuid4().hex[:12]}"
    snapshot = read_queue_health_snapshot(
        pg_conn, schema_version=_SCHEMA_V, calculation_version=empty_rules_version, rules_version=empty_rules_version,
    )
    assert snapshot.pending == 0
    assert snapshot.generating_active == 0
    assert snapshot.generating_expired_lease == 0
    assert snapshot.failed_retryable == 0
    assert snapshot.failed_terminal == 0
    assert snapshot.oldest_pending_age_seconds is None
    assert snapshot.oldest_generating_age_seconds is None
    assert snapshot.oldest_failed_retryable_age_seconds is None
    assert snapshot.generating_over_poll_window_count == 0


def test_snapshot_read_causes_no_database_mutation(pg_conn, seeded_queue):
    calc_version, rules_version = seeded_queue["calc_version"], seeded_queue["rules_version"]
    before = pg_conn.execute(
        """SELECT id, status, attempt_count, claimed_at, lease_expires_at, claimed_by, completed_at
           FROM paper_trade_postmortem_outbox
           WHERE requested_report_schema_version = %s AND requested_calculation_version = %s
             AND requested_rules_version = %s
           ORDER BY id""",
        (_SCHEMA_V, calc_version, rules_version),
    ).fetchall()

    for _ in range(3):
        read_queue_health_snapshot(
            pg_conn, schema_version=_SCHEMA_V, calculation_version=calc_version, rules_version=rules_version,
        )

    after = pg_conn.execute(
        """SELECT id, status, attempt_count, claimed_at, lease_expires_at, claimed_by, completed_at
           FROM paper_trade_postmortem_outbox
           WHERE requested_report_schema_version = %s AND requested_calculation_version = %s
             AND requested_rules_version = %s
           ORDER BY id""",
        (_SCHEMA_V, calc_version, rules_version),
    ).fetchall()
    assert after == before, "read_queue_health_snapshot must never mutate, claim, lease, settle or repair any row."
