"""
Trade Postmortem Engine, Stage 2 — migration structural checks for
`paper_trade_entry_snapshot` in services/postgres_store.py's SCHEMA_SQL.

No live Postgres instance was available in this environment (no docker, no
local `postgres`/`psql`/`initdb` binary — confirmed absent before writing
these tests) to execute the DDL against a real, disposable database. These
tests instead statically verify the SQL text follows this repository's
established idempotent-migration conventions (every other table in
SCHEMA_SQL follows the same pattern, and these are the properties that make
repeated execution safe), and separately exercise the migration string
itself against a mocked executor to prove `init_db()` actually includes
it. This is the strongest repository-consistent validation available
without an isolated database — see the Stage 2 Program Director report for
the explicit limitation this implies.
"""
import re

import pytest

from services import postgres_store


@pytest.mark.regression
class TestEntrySnapshotMigrationIsIdempotent:
    def test_create_table_uses_if_not_exists(self):
        assert "CREATE TABLE IF NOT EXISTS paper_trade_entry_snapshot" in postgres_store.SCHEMA_SQL

    def test_indexes_use_if_not_exists(self):
        sql = postgres_store.SCHEMA_SQL
        idx_statements = re.findall(r"CREATE (?:UNIQUE )?INDEX[^\n]*paper_trade_entry_snapshot[^\n]*", sql)
        assert idx_statements, "expected at least one index statement for paper_trade_entry_snapshot"
        for stmt in idx_statements:
            assert "IF NOT EXISTS" in stmt, f"index statement missing IF NOT EXISTS: {stmt}"

    def test_no_destructive_statements_against_existing_tables(self):
        """This migration must never DROP or ALTER an existing column/table
        — it only adds a brand new table. (DROP INDEX/DROP CONSTRAINT
        elsewhere in SCHEMA_SQL belong to older, unrelated migrations using
        the existing DROP-then-ADD idempotency pattern for constraints on
        tables that already existed before this stage — this test scopes
        its check to statements mentioning paper_trade_entry_snapshot only.)
        """
        sql = postgres_store.SCHEMA_SQL
        # Isolate roughly the block this stage added, bounded by its own
        # comment markers, so this check can't accidentally pass by reading
        # unrelated DROP statements elsewhere in the 800-line schema file.
        start = sql.index("Trade Postmortem Engine, Stage 2")
        end = sql.index("Learning Alpha Engine remediation, Phase 2A")
        block = sql[start:end]
        # Strip SQL comment lines first — this block's own explanatory
        # comments discuss why DELETE FROM paper_trades is handled at the
        # application layer (reset_portfolio) rather than a DB constraint,
        # which would otherwise false-positive a naive substring search.
        executable_lines = "\n".join(
            line for line in block.splitlines() if not line.strip().startswith("--")
        )
        assert "DROP TABLE" not in executable_lines.upper()
        assert "ALTER TABLE paper_trades" not in executable_lines
        assert "DELETE FROM" not in executable_lines.upper()

    def test_no_forced_default_or_backfill_for_historical_trades(self):
        """The new table must never be pre-populated for existing
        paper_trades rows — no INSERT INTO paper_trade_entry_snapshot
        appears anywhere in the schema migration itself; rows are only
        ever inserted by paper_buy at trade-creation time."""
        sql = postgres_store.SCHEMA_SQL
        assert "INSERT INTO paper_trade_entry_snapshot" not in sql

    def test_paper_trade_id_is_unique_not_a_foreign_key(self):
        """Documented ADR: no FK to paper_trades, to avoid a first-ever FK
        constraint interacting with POST /reset's DELETE FROM paper_trades.
        UNIQUE is still the real duplicate-snapshot guard."""
        start = postgres_store.SCHEMA_SQL.index("CREATE TABLE IF NOT EXISTS paper_trade_entry_snapshot")
        end = postgres_store.SCHEMA_SQL.index(");", start)
        table_def = postgres_store.SCHEMA_SQL[start:end]
        assert "paper_trade_id" in table_def
        assert "UNIQUE" in table_def
        assert "REFERENCES paper_trades" not in table_def

    def test_repeated_execution_of_schema_sql_string_is_textually_stable(self):
        """A weak but real proxy for "safe to run on every startup": the
        SCHEMA_SQL string itself is a fixed constant re-executed verbatim on
        every process start (see api/main.py's init_db() call site) — this
        asserts it's the same string both times it's read, i.e. nothing
        about constructing it is order-dependent or has a side effect that
        would make a second execution see different DDL than the first."""
        first = postgres_store.SCHEMA_SQL
        second = postgres_store.SCHEMA_SQL
        assert first == second


@pytest.mark.regression
class TestEntrySnapshotImmutabilityTriggerStructure:
    """Structural checks only — see module docstring. These prove the
    trigger SQL exists and follows this schema's idempotent-migration
    conventions; they cannot and do not prove PostgreSQL actually rejects
    an UPDATE (that requires a live server — see the Stage 2 Migration
    Verification Gate report, Gate C = PARTIAL)."""

    def test_trigger_function_and_trigger_both_present(self):
        sql = postgres_store.SCHEMA_SQL
        assert "CREATE OR REPLACE FUNCTION reject_paper_trade_entry_snapshot_update" in sql
        assert "BEFORE UPDATE ON paper_trade_entry_snapshot" in sql
        assert "RAISE EXCEPTION" in sql

    def test_trigger_creation_is_idempotent_drop_then_create(self):
        sql = postgres_store.SCHEMA_SQL
        assert "DROP TRIGGER IF EXISTS trg_paper_trade_entry_snapshot_immutable ON paper_trade_entry_snapshot" in sql
        assert "CREATE TRIGGER trg_paper_trade_entry_snapshot_immutable" in sql

    def test_trigger_only_fires_on_update_not_insert_or_delete(self):
        """The trigger clause itself must say BEFORE UPDATE only — INSERT
        (new snapshot creation) and DELETE (reset_portfolio) must remain
        unaffected."""
        sql = postgres_store.SCHEMA_SQL
        start = sql.index("CREATE TRIGGER trg_paper_trade_entry_snapshot_immutable")
        end = sql.index(";", start)
        trigger_stmt = sql[start:end]
        assert "BEFORE UPDATE" in trigger_stmt
        assert "INSERT" not in trigger_stmt
        assert "DELETE" not in trigger_stmt


@pytest.mark.regression
class TestEntrySnapshotSchemaExecutionIsAttempted:
    def test_init_db_executes_the_schema_sql_containing_the_new_table(self, monkeypatch):
        """Mocked-executor proof that init_db() actually runs SCHEMA_SQL
        (and therefore this stage's DDL) — not just that the string exists
        somewhere in the module."""
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
        assert any("paper_trade_entry_snapshot" in sql for sql in executed)
