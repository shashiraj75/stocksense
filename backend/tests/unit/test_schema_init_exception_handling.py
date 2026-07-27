"""
Schema Initialization Hardening phase — mocked-connection unit tests.

Complements, never substitutes for, the real-PostgreSQL evidence in
tests/postgres_integration/test_schema_init_failure_modes.py. These
tests isolate narrow branching logic (SQLSTATE classification, retry
bounding, fresh-connection-per-attempt, lock release on every path,
logging fields, secret redaction, migration ordering, postcondition
propagation) that's awkward or slow to exercise against a real database
for every edge case.
"""
import logging
from unittest.mock import MagicMock, call

import psycopg
import pytest

from services import postgres_store


def _pg_error(sqlstate: str, message: str = "simulated") -> psycopg.Error:
    exc = psycopg.OperationalError(message)
    exc.sqlstate = sqlstate
    return exc


class TestSqlstateClassification:
    @pytest.mark.parametrize("sqlstate", ["55P03", "40P01", "40001"])
    def test_approved_transient_sqlstates_are_retryable(self, sqlstate):
        assert postgres_store._is_retryable(_pg_error(sqlstate)) is True

    @pytest.mark.parametrize("sqlstate", [
        "42601",  # syntax_error
        "42501",  # insufficient_privilege
        "28000",  # invalid_authorization_specification
        "42P01",  # undefined_table
        "42804",  # datatype_mismatch
        "42710",  # duplicate_object
        "08006",  # connection_failure
        None,
    ])
    def test_everything_else_is_non_retryable(self, sqlstate):
        assert postgres_store._is_retryable(_pg_error(sqlstate) if sqlstate else RuntimeError("no sqlstate")) is False

    def test_sqlstate_extraction_from_plain_exception_without_sqlstate_attr(self):
        assert postgres_store._sqlstate_of(RuntimeError("no sqlstate here")) is None

    def test_sqlstate_extraction_falls_back_to_cause_diag(self):
        underlying = _pg_error("40001")
        wrapper = RuntimeError("wrapped")
        wrapper.__cause__ = underlying
        # _sqlstate_of only checks direct .sqlstate / .diag.sqlstate on the
        # passed exception itself, not __cause__ — this documents that
        # boundary rather than assuming deeper unwrapping.
        assert postgres_store._sqlstate_of(wrapper) is None
        assert postgres_store._sqlstate_of(underlying) == "40001"


class TestRetryBounding:
    def test_non_retryable_failure_raises_after_first_attempt(self, monkeypatch):
        attempts = []

        def _fake_attempt(n):
            attempts.append(n)
            raise _pg_error("42601", "syntax error at or near")

        monkeypatch.setattr(postgres_store, "_attempt_schema_initialization", _fake_attempt)
        monkeypatch.setattr(postgres_store.time, "sleep", lambda *_: None)

        with pytest.raises(postgres_store.SchemaInitializationError):
            postgres_store.init_db()
        assert attempts == [1]

    def test_retryable_failure_retries_up_to_max_attempts_then_raises(self, monkeypatch):
        attempts = []

        def _fake_attempt(n):
            attempts.append(n)
            raise _pg_error("55P03", "lock not available")

        monkeypatch.setattr(postgres_store, "_attempt_schema_initialization", _fake_attempt)
        monkeypatch.setattr(postgres_store, "_SCHEMA_INIT_MAX_ATTEMPTS", 3)
        monkeypatch.setattr(postgres_store.time, "sleep", lambda *_: None)

        with pytest.raises(postgres_store.SchemaInitializationError):
            postgres_store.init_db()
        assert attempts == [1, 2, 3]

    def test_retryable_failure_succeeding_on_a_later_attempt_returns_normally(self, monkeypatch):
        attempts = []

        def _fake_attempt(n):
            attempts.append(n)
            if n < 2:
                raise _pg_error("40001", "serialization failure")
            return None

        monkeypatch.setattr(postgres_store, "_attempt_schema_initialization", _fake_attempt)
        monkeypatch.setattr(postgres_store, "_SCHEMA_INIT_MAX_ATTEMPTS", 3)
        monkeypatch.setattr(postgres_store.time, "sleep", lambda *_: None)

        postgres_store.init_db()
        assert attempts == [1, 2]

    def test_backoff_is_invoked_between_retryable_attempts(self, monkeypatch):
        sleeps = []

        def _fake_attempt(n):
            if n == 1:
                raise _pg_error("40P01", "deadlock")
            return None

        monkeypatch.setattr(postgres_store, "_attempt_schema_initialization", _fake_attempt)
        monkeypatch.setattr(postgres_store, "_SCHEMA_INIT_MAX_ATTEMPTS", 3)
        monkeypatch.setattr(postgres_store.time, "sleep", lambda s: sleeps.append(s))

        postgres_store.init_db()
        assert sleeps == [postgres_store._SCHEMA_INIT_RETRY_BACKOFF_SECONDS]


class TestFreshConnectionPerAttempt:
    def test_each_attempt_opens_a_new_psycopg_connection(self, monkeypatch):
        made_connections = []

        class _FakeConn:
            def __init__(self):
                made_connections.append(self)
                self.executed = []
                self.closed = False

            def execute(self, sql, params=None):
                self.executed.append(sql)
                if "pg_try_advisory_lock" in sql:
                    return MagicMock(fetchone=lambda: (True,))
                return MagicMock(fetchone=lambda: (True,))

            def close(self):
                self.closed = True

        monkeypatch.setattr(postgres_store.psycopg, "connect", lambda *a, **k: _FakeConn())
        postgres_store.init_db()

        assert len(made_connections) == 1  # single successful attempt = one connection
        assert made_connections[0].closed is True


class TestAdvisoryLockReleaseOnEveryPath:
    def test_lock_released_on_success(self, monkeypatch):
        released = []

        class _FakeConn:
            def execute(self, sql, params=None):
                if "pg_try_advisory_lock" in sql:
                    return MagicMock(fetchone=lambda: (True,))
                if "pg_advisory_unlock" in sql:
                    released.append(True)
                    return MagicMock(fetchone=lambda: (True,))
                return MagicMock(fetchone=lambda: (True,))

            def close(self):
                pass

        monkeypatch.setattr(postgres_store.psycopg, "connect", lambda *a, **k: _FakeConn())
        postgres_store.init_db()
        assert released == [True]

    def test_lock_released_even_when_a_later_step_fails(self, monkeypatch):
        released = []

        class _FakeConn:
            def execute(self, sql, params=None):
                if "pg_try_advisory_lock" in sql:
                    return MagicMock(fetchone=lambda: (True,))
                if "pg_advisory_unlock" in sql:
                    released.append(True)
                    return MagicMock(fetchone=lambda: (True,))
                if sql.strip().startswith("CREATE TABLE"):
                    raise _pg_error("42601", "syntax error")
                return MagicMock(fetchone=lambda: (True,))

            def close(self):
                pass

        monkeypatch.setattr(postgres_store.psycopg, "connect", lambda *a, **k: _FakeConn())
        monkeypatch.setattr(postgres_store, "_SCHEMA_INIT_MAX_ATTEMPTS", 1)
        with pytest.raises(postgres_store.SchemaInitializationError):
            postgres_store.init_db()
        assert released == [True]

    def test_no_pooled_connection_is_used_for_schema_init(self, monkeypatch):
        """init_db() must not touch _get_pool() at all — it opens its own
        connection so it never returns a pooled connection to the pool
        while still holding the advisory lock."""
        pool_calls = []
        monkeypatch.setattr(postgres_store, "_get_pool", lambda: pool_calls.append(1))

        class _FakeConn:
            def execute(self, sql, params=None):
                return MagicMock(fetchone=lambda: (True,))

            def close(self):
                pass

        monkeypatch.setattr(postgres_store.psycopg, "connect", lambda *a, **k: _FakeConn())
        postgres_store.init_db()
        assert pool_calls == []


class TestMigrationOrderingAndPostconditionPropagation:
    def test_migrations_run_in_documented_order(self, monkeypatch):
        order = []
        monkeypatch.setattr(postgres_store, "_migrate_outcomes_market_constraint", lambda c: order.append("outcomes"))
        monkeypatch.setattr(postgres_store, "_migrate_score_snapshots_market_constraint", lambda c: order.append("score_snapshots"))
        monkeypatch.setattr(postgres_store, "_migrate_daily_picks_cache_status_column", lambda c: order.append("daily_picks_cache"))
        monkeypatch.setattr(postgres_store, "_verify_schema_postconditions", lambda c: None)

        class _FakeConn:
            def execute(self, sql, params=None):
                return MagicMock(fetchone=lambda: (True,))

            def close(self):
                pass

        monkeypatch.setattr(postgres_store.psycopg, "connect", lambda *a, **k: _FakeConn())
        postgres_store.init_db()
        assert order == ["outcomes", "score_snapshots", "daily_picks_cache"]

    def test_postcondition_failure_propagates_as_schema_initialization_error(self, monkeypatch):
        monkeypatch.setattr(postgres_store, "_migrate_outcomes_market_constraint", lambda c: None)
        monkeypatch.setattr(postgres_store, "_migrate_score_snapshots_market_constraint", lambda c: None)
        monkeypatch.setattr(postgres_store, "_migrate_daily_picks_cache_status_column", lambda c: None)

        def _fail_postcondition(c):
            raise postgres_store.SchemaInitializationError("required table missing after init: paper_trades")

        monkeypatch.setattr(postgres_store, "_verify_schema_postconditions", _fail_postcondition)

        class _FakeConn:
            def execute(self, sql, params=None):
                return MagicMock(fetchone=lambda: (True,))

            def close(self):
                pass

        monkeypatch.setattr(postgres_store.psycopg, "connect", lambda *a, **k: _FakeConn())
        monkeypatch.setattr(postgres_store, "_SCHEMA_INIT_MAX_ATTEMPTS", 1)
        with pytest.raises(postgres_store.SchemaInitializationError, match="paper_trades"):
            postgres_store.init_db()


class TestLoggingAndRedaction:
    def test_critical_log_emitted_on_terminal_failure(self, monkeypatch, caplog):
        class _FakeConn:
            def execute(self, sql, params=None):
                if "pg_try_advisory_lock" in sql:
                    return MagicMock(fetchone=lambda: (True,))
                raise _pg_error("42601", "syntax error near CREATE")

            def close(self):
                pass

        monkeypatch.setattr(postgres_store.psycopg, "connect", lambda *a, **k: _FakeConn())
        monkeypatch.setattr(postgres_store, "_SCHEMA_INIT_MAX_ATTEMPTS", 1)

        with caplog.at_level(logging.CRITICAL, logger="services.postgres_store"):
            with pytest.raises(postgres_store.SchemaInitializationError):
                postgres_store.init_db()

        critical_records = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
        assert critical_records, "expected a CRITICAL log on terminal schema-init failure"
        assert "42601" in critical_records[0].getMessage()

    def test_no_database_url_or_credentials_appear_in_any_log_record(self, monkeypatch, caplog):
        fake_url = "postgresql://realuser:realsecretpassword@internal-host:5432/realdb"
        monkeypatch.setattr(postgres_store, "DATABASE_URL", fake_url)

        class _FakeConn:
            def execute(self, sql, params=None):
                if "pg_try_advisory_lock" in sql:
                    return MagicMock(fetchone=lambda: (True,))
                raise _pg_error("42601", "syntax error")

            def close(self):
                pass

        monkeypatch.setattr(postgres_store.psycopg, "connect", lambda *a, **k: _FakeConn())
        monkeypatch.setattr(postgres_store, "_SCHEMA_INIT_MAX_ATTEMPTS", 1)

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(postgres_store.SchemaInitializationError):
                postgres_store.init_db()

        for record in caplog.records:
            msg = record.getMessage()
            assert "realsecretpassword" not in msg
            assert "internal-host" not in msg
            assert fake_url not in msg
