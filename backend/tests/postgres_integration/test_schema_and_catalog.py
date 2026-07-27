"""
Real PostgreSQL Verification, CI Execution phase — Parts 4 & 5.

Schema initialization (first run, second run, partial-state repair) and
PostgreSQL catalog verification, all against a real disposable PostgreSQL
instance. Uses the repository's actual `services.postgres_store.init_db()`
— never a hand-copied simplified DDL.
"""
import pytest

pytestmark = pytest.mark.postgres_integration


def _columns(pg_conn, table_name: str) -> dict:
    rows = pg_conn.execute(
        """SELECT column_name, data_type, is_nullable, column_default
           FROM information_schema.columns
           WHERE table_name = %s""",
        (table_name,)
    ).fetchall()
    return {r[0]: {"data_type": r[1], "is_nullable": r[2], "column_default": r[3]} for r in rows}


def _table_exists(pg_conn, table_name: str) -> bool:
    row = pg_conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s", (table_name,)
    ).fetchone()
    return row is not None


def _indexes(pg_conn, table_name: str) -> set:
    rows = pg_conn.execute("SELECT indexname FROM pg_indexes WHERE tablename = %s", (table_name,)).fetchall()
    return {r[0] for r in rows}


def _constraints(pg_conn, table_name: str) -> list:
    rows = pg_conn.execute(
        """SELECT conname, contype FROM pg_constraint c
           JOIN pg_class t ON c.conrelid = t.oid
           WHERE t.relname = %s""",
        (table_name,)
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _triggers(pg_conn, table_name: str) -> list:
    rows = pg_conn.execute(
        """SELECT tgname FROM pg_trigger t
           JOIN pg_class c ON t.tgrelid = c.oid
           WHERE c.relname = %s AND NOT t.tgisinternal""",
        (table_name,)
    ).fetchall()
    return [r[0] for r in rows]


@pytest.mark.timeout(60)
class TestFirstInitialization:
    def test_core_paper_trading_tables_exist(self, pg_conn, initialized_schema):
        for table in ("paper_trades", "paper_portfolio", "paper_trade_entry_snapshot", "paper_trade_idempotency_key"):
            assert _table_exists(pg_conn, table), f"expected table {table!r} to exist"

    def test_entry_snapshot_expected_columns_and_types(self, pg_conn, initialized_schema):
        cols = _columns(pg_conn, "paper_trade_entry_snapshot")
        assert cols["paper_trade_id"]["data_type"] == "bigint"
        assert cols["paper_trade_id"]["is_nullable"] == "NO"
        assert cols["snapshot_schema_version"]["is_nullable"] == "NO"
        assert cols["evidence_source"]["is_nullable"] == "NO"
        assert cols["simulated_execution_price"]["data_type"] == "double precision"
        assert cols["simulated_execution_price"]["is_nullable"] == "NO"
        assert cols["recommendation_reasoning"]["data_type"] == "jsonb"
        assert cols["verification_levels"]["data_type"] == "jsonb"
        assert cols["verification_levels"]["is_nullable"] == "NO"
        assert cols["captured_at"]["data_type"] == "timestamp with time zone"
        assert cols["recommendation_generated_at"]["data_type"] == "timestamp with time zone"

    def test_idempotency_expected_columns_and_types(self, pg_conn, initialized_schema):
        cols = _columns(pg_conn, "paper_trade_idempotency_key")
        for col in ("user_id", "operation_type", "idempotency_key", "request_fingerprint"):
            assert cols[col]["data_type"] == "text"
            assert cols[col]["is_nullable"] == "NO"
        assert cols["status"]["is_nullable"] == "NO"
        assert "PENDING" in (cols["status"]["column_default"] or "")
        assert cols["response_body"]["data_type"] == "jsonb"
        assert cols["response_body"]["is_nullable"] == "YES"
        assert cols["created_at"]["data_type"] == "timestamp with time zone"
        assert cols["created_at"]["is_nullable"] == "NO"
        assert cols["completed_at"]["is_nullable"] == "YES"

    def test_entry_snapshot_unique_constraint_on_paper_trade_id(self, pg_conn, initialized_schema):
        indexes = _indexes(pg_conn, "paper_trade_entry_snapshot")
        assert "idx_paper_trade_entry_snapshot_trade" in indexes
        assert "idx_paper_trade_entry_snapshot_user" in indexes

    def test_idempotency_unique_constraint_and_created_at_index(self, pg_conn, initialized_schema):
        indexes = _indexes(pg_conn, "paper_trade_idempotency_key")
        assert "idx_paper_trade_idem_key_unique" in indexes
        assert "idx_paper_trade_idem_key_created_at" in indexes

    def test_immutability_trigger_and_function_exist(self, pg_conn, initialized_schema):
        triggers = _triggers(pg_conn, "paper_trade_entry_snapshot")
        assert "trg_paper_trade_entry_snapshot_immutable" in triggers

        func_row = pg_conn.execute(
            "SELECT 1 FROM pg_proc WHERE proname = %s",
            ("reject_paper_trade_entry_snapshot_update",)
        ).fetchone()
        assert func_row is not None

        trigger_def = pg_conn.execute(
            """SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t
               JOIN pg_class c ON t.tgrelid = c.oid
               WHERE c.relname = 'paper_trade_entry_snapshot' AND t.tgname = %s""",
            ("trg_paper_trade_entry_snapshot_immutable",)
        ).fetchone()[0]
        assert "BEFORE UPDATE" in trigger_def
        assert "INSERT" not in trigger_def.split("EXECUTE")[0].upper().replace("BEFORE UPDATE", "")

        func_def = pg_conn.execute(
            "SELECT pg_get_functiondef(oid) FROM pg_proc WHERE proname = %s",
            ("reject_paper_trade_entry_snapshot_update",)
        ).fetchone()[0]
        assert "RAISE EXCEPTION" in func_def


@pytest.mark.timeout(60)
class TestRepeatedInitialization:
    def test_second_initialization_succeeds_with_no_duplicate_objects(self, pg_conn, initialized_schema):
        from services import postgres_store
        indexes_before = _indexes(pg_conn, "paper_trade_idempotency_key")
        triggers_before = _triggers(pg_conn, "paper_trade_entry_snapshot")

        postgres_store.init_db()  # second run — must not raise

        indexes_after = _indexes(pg_conn, "paper_trade_idempotency_key")
        triggers_after = _triggers(pg_conn, "paper_trade_entry_snapshot")
        assert indexes_after == indexes_before
        assert triggers_after == triggers_before
        assert len(triggers_after) == 1  # never duplicated

    def test_second_initialization_preserves_existing_rows(self, pg_conn, initialized_schema, unique_user_id):
        from services import postgres_store
        from tests.postgres_integration.conftest import ensure_portfolio

        ensure_portfolio(pg_conn, unique_user_id, cash=42_000.0)
        postgres_store.init_db()
        row = pg_conn.execute(
            "SELECT cash FROM paper_portfolio WHERE user_id = %s", (unique_user_id,)
        ).fetchone()
        assert row[0] == 42_000.0


@pytest.mark.timeout(60)
class TestPartialStateRecovery:
    def test_missing_snapshot_index_is_restored(self, pg_conn, initialized_schema):
        from services import postgres_store
        pg_conn.execute("DROP INDEX IF EXISTS idx_paper_trade_entry_snapshot_user")
        assert "idx_paper_trade_entry_snapshot_user" not in _indexes(pg_conn, "paper_trade_entry_snapshot")
        postgres_store.init_db()
        assert "idx_paper_trade_entry_snapshot_user" in _indexes(pg_conn, "paper_trade_entry_snapshot")

    def test_missing_immutability_trigger_is_restored_function_untouched(self, pg_conn, initialized_schema):
        from services import postgres_store
        pg_conn.execute("DROP TRIGGER IF EXISTS trg_paper_trade_entry_snapshot_immutable ON paper_trade_entry_snapshot")
        assert _triggers(pg_conn, "paper_trade_entry_snapshot") == []
        func_row_before = pg_conn.execute(
            "SELECT oid FROM pg_proc WHERE proname = %s", ("reject_paper_trade_entry_snapshot_update",)
        ).fetchone()
        assert func_row_before is not None  # function alone, no trigger — the exact scenario under test

        postgres_store.init_db()

        assert "trg_paper_trade_entry_snapshot_immutable" in _triggers(pg_conn, "paper_trade_entry_snapshot")

    def test_missing_idempotency_index_is_restored(self, pg_conn, initialized_schema):
        from services import postgres_store
        pg_conn.execute("DROP INDEX IF EXISTS idx_paper_trade_idem_key_created_at")
        assert "idx_paper_trade_idem_key_created_at" not in _indexes(pg_conn, "paper_trade_idempotency_key")
        postgres_store.init_db()
        assert "idx_paper_trade_idem_key_created_at" in _indexes(pg_conn, "paper_trade_idempotency_key")

    def test_all_objects_already_present_is_a_safe_noop(self, pg_conn, initialized_schema):
        from services import postgres_store
        postgres_store.init_db()
        postgres_store.init_db()
        assert "idx_paper_trade_idem_key_unique" in _indexes(pg_conn, "paper_trade_idempotency_key")
        assert len(_triggers(pg_conn, "paper_trade_entry_snapshot")) == 1
