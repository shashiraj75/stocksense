"""
Wave C, WC-O final correction — §4: real EXPLAIN (ANALYZE, BUFFERS,
FORMAT JSON) evidence for outbox_queue_health.read_queue_health_snapshot's
query, captured at a representative beta-scale row count rather than
asserted as a brittle exact plan shape or a hardcoded millisecond
threshold.

Seeds rows via one INSERT ... SELECT generate_series (never a Python
loop of individual INSERTs, and never seeding production) under a
throwaway synthetic version identity, runs EXPLAIN on the exact query
text `read_queue_health_snapshot` executes, and prints the captured
evidence (PostgreSQL version, seeded row count, top plan node, planning
time, execution time, shared-buffer hits/reads) to stdout so it appears
in the JUnit system-out for this test and can be copied into the
observability review document's evidence section.
"""
import json
import uuid

import pytest

from tests.postgres_integration.conftest import SYNTHETIC_USER_PREFIX

pytestmark = pytest.mark.postgres_integration

# A representative beta-scale row count — matches the documented "global,
# low-traffic activation... low thousands of outbox rows" boundary in
# Trade-Postmortem-Wave-C-WC-O-Observability-And-Capacity-Review.md §3.
_SEEDED_ROW_COUNT = 5000

# The exact query text read_queue_health_snapshot executes (duplicated
# here deliberately — this test's job is to gather EXPLAIN evidence for
# THIS literal SQL, not to exercise the function's own Python wrapping).
_QUERY_SQL = """SELECT
     count(*) FILTER (WHERE status = 'PENDING') AS pending,
     count(*) FILTER (WHERE status = 'GENERATING' AND (lease_expires_at IS NULL OR lease_expires_at > now())) AS generating_active,
     count(*) FILTER (WHERE status = 'GENERATING' AND lease_expires_at IS NOT NULL AND lease_expires_at <= now()) AS generating_expired_lease,
     count(*) FILTER (WHERE status = 'FAILED_RETRYABLE') AS failed_retryable,
     count(*) FILTER (WHERE status = 'FAILED_TERMINAL') AS failed_terminal,
     EXTRACT(EPOCH FROM (now() - min(created_at) FILTER (WHERE status = 'PENDING'))) AS oldest_pending_age_seconds,
     EXTRACT(EPOCH FROM (now() - min(claimed_at) FILTER (WHERE status = 'GENERATING' AND claimed_at IS NOT NULL))) AS oldest_generating_age_seconds,
     EXTRACT(EPOCH FROM (now() - min(last_attempt_at) FILTER (WHERE status = 'FAILED_RETRYABLE' AND last_attempt_at IS NOT NULL))) AS oldest_failed_retryable_age_seconds,
     count(*) FILTER (
       WHERE status = 'GENERATING' AND claimed_at IS NOT NULL
         AND claimed_at <= now() - make_interval(secs => %(poll_window)s)
     ) AS generating_over_poll_window
   FROM paper_trade_postmortem_outbox
   WHERE requested_report_schema_version = %(schema_version)s
     AND requested_calculation_version = %(calculation_version)s
     AND requested_rules_version = %(rules_version)s"""


def test_explain_evidence_at_beta_scale_row_count(pg_conn):
    schema_version = "1.2.0"
    calculation_version = f"explain-evidence-calc-{uuid.uuid4().hex[:12]}"
    rules_version = f"explain-evidence-rules-{uuid.uuid4().hex[:12]}"
    synthetic_user = f"{SYNTHETIC_USER_PREFIX}explain-{uuid.uuid4().hex[:8]}"

    # One INSERT ... SELECT generate_series — never a Python-side loop,
    # never seeding production (this connection targets the CI-only
    # PostgreSQL instance this suite always runs against).
    pg_conn.execute(
        """INSERT INTO paper_trade_postmortem_outbox
           (paper_trade_id, user_id, requested_report_schema_version,
            requested_calculation_version, requested_rules_version, status,
            created_at, claimed_at, lease_expires_at, last_attempt_at)
         SELECT
           1_000_000_000 + gs,
           %(user_id)s,
           %(schema_version)s, %(calculation_version)s, %(rules_version)s,
           (ARRAY['PENDING','GENERATING','FAILED_RETRYABLE','FAILED_TERMINAL'])[1 + (gs %% 4)],
           now() - make_interval(secs => (gs %% 300)),
           CASE WHEN gs %% 4 = 1 THEN now() - make_interval(secs => (gs %% 300)) ELSE NULL END,
           CASE WHEN gs %% 4 = 1 THEN now() + make_interval(secs => (60 - (gs %% 200))) ELSE NULL END,
           CASE WHEN gs %% 4 = 2 THEN now() - make_interval(secs => (gs %% 300)) ELSE NULL END
         FROM generate_series(1, %(row_count)s) AS gs""",
        {
            "user_id": synthetic_user, "schema_version": schema_version, "calculation_version": calculation_version,
            "rules_version": rules_version, "row_count": _SEEDED_ROW_COUNT,
        },
    )

    pg_version_row = pg_conn.execute("SELECT version()").fetchone()
    pg_version = pg_version_row[0] if pg_version_row else "unknown"

    explain_row = pg_conn.execute(
        f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {_QUERY_SQL}",
        {"poll_window": 120, "schema_version": schema_version, "calculation_version": calculation_version, "rules_version": rules_version},
    ).fetchone()
    plan_json = explain_row[0]
    plan = plan_json[0] if isinstance(plan_json, list) else json.loads(plan_json)[0]

    top_node = plan["Plan"]
    planning_time_ms = plan.get("Planning Time")
    execution_time_ms = plan.get("Execution Time")
    shared_hit = top_node.get("Shared Hit Blocks")
    shared_read = top_node.get("Shared Read Blocks")

    evidence_lines = [
        "=== WC-O §4 EXPLAIN evidence (read_queue_health_snapshot query) ===",
        f"PostgreSQL version: {pg_version}",
        f"Seeded row count (this identity): {_SEEDED_ROW_COUNT}",
        f"Top plan node: {top_node.get('Node Type')}",
        f"Planning Time (ms): {planning_time_ms}",
        f"Execution Time (ms): {execution_time_ms}",
        f"Shared Hit Blocks: {shared_hit}",
        f"Shared Read Blocks: {shared_read}",
        "=== end evidence ===",
    ]
    # Printed (not asserted) so it surfaces in this test's JUnit
    # system-out for copying into the observability review document —
    # deliberately no assertion on the plan node type or a millisecond
    # threshold, per the explicit instruction not to pin a brittle plan
    # shape or timing number.
    print("\n".join(evidence_lines))

    # The only real assertions: the EXPLAIN command executed successfully
    # and returned a well-formed plan — proving the query is valid and
    # runs to completion at this row count, without pinning its shape.
    assert top_node.get("Node Type") is not None
    assert execution_time_ms is not None and execution_time_ms >= 0


def test_explain_analyze_causes_no_database_mutation(pg_conn):
    """EXPLAIN ANALYZE actually executes the underlying query — since
    read_queue_health_snapshot's query is a pure read (no INSERT/UPDATE/
    DELETE anywhere in it), running EXPLAIN ANALYZE on it must cause zero
    mutation, exactly like calling the function directly does."""
    schema_version = "1.2.0"
    calculation_version = f"explain-mutation-calc-{uuid.uuid4().hex[:12]}"
    rules_version = f"explain-mutation-rules-{uuid.uuid4().hex[:12]}"
    synthetic_user = f"{SYNTHETIC_USER_PREFIX}explain-mut-{uuid.uuid4().hex[:8]}"

    pg_conn.execute(
        """INSERT INTO paper_trade_postmortem_outbox
           (paper_trade_id, user_id, requested_report_schema_version,
            requested_calculation_version, requested_rules_version, status)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (2_000_000_001, synthetic_user, schema_version, calculation_version, rules_version, "PENDING"),
    )

    before = pg_conn.execute(
        """SELECT id, status, attempt_count, claimed_at, lease_expires_at, claimed_by, completed_at
           FROM paper_trade_postmortem_outbox
           WHERE requested_calculation_version = %s AND requested_rules_version = %s
           ORDER BY id""",
        (calculation_version, rules_version),
    ).fetchall()

    for _ in range(2):
        pg_conn.execute(
            f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {_QUERY_SQL}",
            {
                "poll_window": 120, "schema_version": schema_version,
                "calculation_version": calculation_version, "rules_version": rules_version,
            },
        ).fetchone()

    after = pg_conn.execute(
        """SELECT id, status, attempt_count, claimed_at, lease_expires_at, claimed_by, completed_at
           FROM paper_trade_postmortem_outbox
           WHERE requested_calculation_version = %s AND requested_rules_version = %s
           ORDER BY id""",
        (calculation_version, rules_version),
    ).fetchall()
    assert after == before, "EXPLAIN ANALYZE on this query must cause zero mutation."
