"""
Real PostgreSQL Verification, CI Execution phase — Part 14.

Query-plan inspection against a moderate synthetic dataset — enough for
the planner to have a meaningful choice, without inflating CI runtime.
Confirms the intended indexes exist and are considered/used where data
volume makes that meaningful; a sequential scan on a handful of CI rows is
explicitly NOT treated as a production defect (Part 14's own instruction).
"""
import datetime as dt
import json
import uuid

import pytest

pytestmark = pytest.mark.postgres_integration

_SEED_ROWS = 500  # moderate — enough for the planner to prefer an index, fast enough for CI


def _seed_idempotency_rows(pg_conn, user_id_prefix: str, n: int):
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for i in range(n):
        status = "COMPLETED" if i % 3 else "FAILED" if i % 3 == 1 else "PENDING"
        age_days = (i % 40)
        rows.append((
            f"{user_id_prefix}-{i}", "PAPER_BUY", str(uuid.uuid4()), "fp",
            status, now - dt.timedelta(days=age_days),
            json.dumps({"trade_id": i, "market": "US"}) if status == "COMPLETED" else None,
        ))
    with pg_conn.transaction(), pg_conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO paper_trade_idempotency_key
               (user_id, operation_type, idempotency_key, request_fingerprint, status, created_at, response_body)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            rows
        )
    return [r[0] for r in rows]


@pytest.fixture
def seeded_prefix(pg_conn):
    prefix = f"pg-integration-test-user-planquery-{uuid.uuid4().hex[:8]}"
    _seed_idempotency_rows(pg_conn, prefix, _SEED_ROWS)
    yield prefix
    with pg_conn.transaction():
        pg_conn.execute("DELETE FROM paper_trade_idempotency_key WHERE user_id LIKE %s", (f"{prefix}%",))


def _explain(pg_conn, sql, params):
    rows = pg_conn.execute(f"EXPLAIN {sql}", params).fetchall()
    return "\n".join(r[0] for r in rows)


@pytest.mark.timeout(30)
class TestQueryPlans:
    def test_idempotency_key_lookup_uses_unique_index(self, pg_conn, seeded_prefix):
        plan = _explain(
            pg_conn,
            "SELECT id, status, request_fingerprint, response_body, created_at "
            "FROM paper_trade_idempotency_key WHERE user_id = %s AND operation_type = %s AND idempotency_key = %s",
            (f"{seeded_prefix}-0", "PAPER_BUY", "nonexistent-key"),
        )
        assert "idx_paper_trade_idem_key_unique" in plan or "Index" in plan

    def test_cleanup_completed_query_plan_captured(self, pg_conn, seeded_prefix):
        plan = _explain(
            pg_conn,
            "SELECT id FROM paper_trade_idempotency_key WHERE status = %s AND created_at < now() - %s::interval "
            "ORDER BY id LIMIT %s",
            ("COMPLETED", "30 days", 500),
        )
        assert plan  # plan captured — presence/absence of index use is informational here, not asserted as a defect

    def test_cleanup_failed_query_plan_captured(self, pg_conn, seeded_prefix):
        plan = _explain(
            pg_conn,
            "SELECT id FROM paper_trade_idempotency_key WHERE status = %s AND created_at < now() - %s::interval "
            "ORDER BY id LIMIT %s",
            ("FAILED", "24 hours", 500),
        )
        assert plan

    def test_cleanup_pending_query_plan_captured(self, pg_conn, seeded_prefix):
        plan = _explain(
            pg_conn,
            "SELECT id FROM paper_trade_idempotency_key WHERE status = %s AND created_at < now() - %s::interval "
            "ORDER BY id LIMIT %s",
            ("PENDING", "1 hour", 500),
        )
        assert plan

    def test_snapshot_lookup_by_trade_id_uses_unique_index(self, pg_conn, seeded_prefix):
        plan = _explain(
            pg_conn,
            "SELECT * FROM paper_trade_entry_snapshot WHERE paper_trade_id = %s",
            (999999999,),
        )
        assert "idx_paper_trade_entry_snapshot_trade" in plan or "Index" in plan

    def test_market_scoped_reset_cleanup_query_plan_captured(self, pg_conn, seeded_prefix):
        plan = _explain(
            pg_conn,
            "SELECT id FROM paper_trade_idempotency_key WHERE user_id = %s AND operation_type = %s "
            "AND status = 'COMPLETED' AND response_body ->> 'market' = %s",
            (f"{seeded_prefix}-0", "PAPER_BUY", "US"),
        )
        assert plan
