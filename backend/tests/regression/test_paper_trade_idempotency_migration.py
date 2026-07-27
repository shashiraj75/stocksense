"""
Migration-verification hardening gate, Part 7 — structural checks for
`paper_trade_idempotency_key` in services/postgres_store.py's SCHEMA_SQL.

Same limitation as test_paper_trade_entry_snapshot_migration.py: no live
Postgres instance is available in this environment, so these are static
text/idempotency-convention checks, not a real-server proof.
"""
import pytest

from services import postgres_store


@pytest.mark.regression
class TestIdempotencyKeyMigrationIsIdempotent:
    def test_create_table_uses_if_not_exists(self):
        assert "CREATE TABLE IF NOT EXISTS paper_trade_idempotency_key" in postgres_store.SCHEMA_SQL

    def test_unique_index_on_user_operation_key_exists(self):
        sql = postgres_store.SCHEMA_SQL
        assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_trade_idem_key_unique" in sql
        assert "ON paper_trade_idempotency_key(user_id, operation_type, idempotency_key)" in sql

    def test_created_at_index_uses_if_not_exists(self):
        assert "CREATE INDEX IF NOT EXISTS idx_paper_trade_idem_key_created_at" in postgres_store.SCHEMA_SQL

    def test_no_destructive_statements_in_this_blocks_executable_lines(self):
        sql = postgres_store.SCHEMA_SQL
        start = sql.index("CREATE TABLE IF NOT EXISTS paper_trade_idempotency_key")
        end = sql.index("Learning Alpha Engine remediation, Phase 2A")
        block = sql[start:end]
        executable_lines = "\n".join(
            line for line in block.splitlines() if not line.strip().startswith("--")
        )
        assert "DROP TABLE" not in executable_lines.upper()
        assert "DELETE FROM" not in executable_lines.upper()
        assert "ALTER TABLE paper_trades " not in executable_lines

    def test_no_foreign_key_to_paper_trades(self):
        """Same ADR as paper_trade_entry_snapshot — no FK, to avoid the
        first-ever FK constraint interacting with POST /reset's DELETEs."""
        start = postgres_store.SCHEMA_SQL.index("CREATE TABLE IF NOT EXISTS paper_trade_idempotency_key")
        end = postgres_store.SCHEMA_SQL.index(");", start)
        table_def = postgres_store.SCHEMA_SQL[start:end]
        assert "REFERENCES paper_trades" not in table_def

    def test_status_defaults_to_pending(self):
        start = postgres_store.SCHEMA_SQL.index("CREATE TABLE IF NOT EXISTS paper_trade_idempotency_key")
        end = postgres_store.SCHEMA_SQL.index(");", start)
        table_def = postgres_store.SCHEMA_SQL[start:end]
        assert "status" in table_def
        assert "DEFAULT 'PENDING'" in table_def


@pytest.mark.regression
class TestIdempotencyKeySchemaExecutionIsAttempted:
    def test_init_db_executes_schema_sql_containing_the_new_table(self, monkeypatch):
        executed = []

        class _FakeConn:
            def execute(self, sql, params=None):
                executed.append(sql)
                return self

            def fetchone(self):
                return None

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(postgres_store, "_get_pool", lambda: type(
            "P", (), {"connection": lambda self: _FakeConn()}
        )())
        postgres_store.init_db()
        assert any("paper_trade_idempotency_key" in sql for sql in executed)
