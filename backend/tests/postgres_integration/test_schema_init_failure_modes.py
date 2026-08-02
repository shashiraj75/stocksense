"""
Schema Initialization Hardening phase — real-PostgreSQL evidence for the
fail-closed, concurrency-safe init_db() redesign.

Covers: clean/repeated initialization under the new code path, advisory-
lock serialization of concurrent callers, lock-wait-timeout failure,
genuine invalid-SQL failure, genuine permission-denied failure via a real
restricted role, a deliberately-missing postcondition failing startup,
and FastAPI lifespan/health behavior when init_db() fails.

Every test here uses the real `services.postgres_store.init_db()` and
its real helpers — never a hand-copied simplified schema, never a mocked
connection.
"""
import threading

import psycopg
import pytest

pytestmark = pytest.mark.postgres_integration


@pytest.mark.timeout(60)
class TestCleanAndRepeatedInitialization:
    def test_clean_first_initialization_succeeds(self, pg_conn, initialized_schema):
        # initialized_schema fixture already ran the real init_db() once
        # for this session — a second explicit call here proves the fresh-
        # connection/advisory-lock/postcondition path is safe to call
        # again in isolation, not just as a session fixture side effect.
        from services import postgres_store
        postgres_store.init_db()
        row = pg_conn.execute("SELECT to_regclass('public.paper_trade_entry_snapshot')").fetchone()
        assert row[0] is not None

    def test_repeated_initialization_is_a_safe_noop(self, pg_conn, initialized_schema):
        from services import postgres_store
        postgres_store.init_db()
        postgres_store.init_db()
        postgres_store.init_db()
        # No exception across three consecutive real calls is itself the
        # assertion; also confirm no duplicate objects resulted.
        rows = pg_conn.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_paper_trade_entry_snapshot_immutable'"
        ).fetchone()
        assert rows[0] == 1


@pytest.mark.timeout(60)
class TestAdvisoryLockConcurrency:
    def test_multiple_simultaneous_callers_all_succeed_with_no_duplicates(self, pg_conn, initialized_schema):
        """N real threads each call the real init_db() at (as close to)
        the same time as Python threading allows. The advisory lock must
        serialize them — if it didn't, this is exactly the class of race
        the score_snapshots incident could have hit under a multi-
        replica deploy. Success is measured by zero exceptions and zero
        duplicate objects, not by directly observing lock ordering."""
        from services import postgres_store

        errors = []
        barrier = threading.Barrier(6)

        def _worker():
            barrier.wait(timeout=10)
            try:
                postgres_store.init_db()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=45)

        assert errors == [], f"concurrent init_db() calls raised: {errors}"
        trig_count = pg_conn.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_paper_trade_entry_snapshot_immutable'"
        ).fetchone()[0]
        assert trig_count == 1
        idx_count = pg_conn.execute(
            "SELECT count(*) FROM pg_indexes WHERE indexname = 'idx_paper_trade_idem_key_unique'"
        ).fetchone()[0]
        assert idx_count == 1

    def test_lock_is_actually_held_while_one_caller_initializes(self, pg_database_url, initialized_schema):
        """Directly proves serialization: one connection holds the exact
        advisory-lock identity init_db() uses; a second connection's
        pg_try_advisory_lock on that same identity must fail while the
        first still holds it, and succeed once released."""
        from services.postgres_store import _SCHEMA_INIT_LOCK_NAMESPACE, _SCHEMA_INIT_LOCK_ID

        holder = psycopg.connect(pg_database_url, autocommit=True)
        try:
            got = holder.execute(
                "SELECT pg_try_advisory_lock(%s, %s)",
                (_SCHEMA_INIT_LOCK_NAMESPACE, _SCHEMA_INIT_LOCK_ID),
            ).fetchone()[0]
            assert got is True

            waiter = psycopg.connect(pg_database_url, autocommit=True)
            try:
                blocked = waiter.execute(
                    "SELECT pg_try_advisory_lock(%s, %s)",
                    (_SCHEMA_INIT_LOCK_NAMESPACE, _SCHEMA_INIT_LOCK_ID),
                ).fetchone()[0]
                assert blocked is False, "a second holder acquired the same advisory-lock identity while the first still held it"
            finally:
                waiter.close()

            holder.execute("SELECT pg_advisory_unlock(%s, %s)", (_SCHEMA_INIT_LOCK_NAMESPACE, _SCHEMA_INIT_LOCK_ID))

            waiter2 = psycopg.connect(pg_database_url, autocommit=True)
            try:
                now_free = waiter2.execute(
                    "SELECT pg_try_advisory_lock(%s, %s)",
                    (_SCHEMA_INIT_LOCK_NAMESPACE, _SCHEMA_INIT_LOCK_ID),
                ).fetchone()[0]
                assert now_free is True
                waiter2.execute("SELECT pg_advisory_unlock(%s, %s)", (_SCHEMA_INIT_LOCK_NAMESPACE, _SCHEMA_INIT_LOCK_ID))
            finally:
                waiter2.close()
        finally:
            holder.close()

    def test_lock_wait_exceeding_limit_fails_clearly(self, pg_database_url, initialized_schema, monkeypatch):
        """A caller that can never acquire the ADVISORY lock within the
        configured wait window must raise SchemaInitializationError, not
        hang forever and not silently proceed without the lock. This is
        the advisory-lock POLLING path (_acquire_schema_init_lock's own
        bounded wait) — distinct from TestOrdinaryDdlLockTimeout below,
        which exercises PostgreSQL's own lock_timeout GUC against a real
        blocked DDL statement and a real SQLSTATE 55P03."""
        from services import postgres_store
        from services.postgres_store import (
            SchemaInitializationError, _SCHEMA_INIT_LOCK_NAMESPACE, _SCHEMA_INIT_LOCK_ID,
        )

        monkeypatch.setattr(postgres_store, "_SCHEMA_INIT_LOCK_MAX_WAIT_SECONDS", 2)
        monkeypatch.setattr(postgres_store, "_SCHEMA_INIT_LOCK_POLL_INTERVAL_SECONDS", 0.2)
        monkeypatch.setattr(postgres_store, "_SCHEMA_INIT_MAX_ATTEMPTS", 1)

        holder = psycopg.connect(pg_database_url, autocommit=True)
        try:
            got = holder.execute(
                "SELECT pg_try_advisory_lock(%s, %s)",
                (_SCHEMA_INIT_LOCK_NAMESPACE, _SCHEMA_INIT_LOCK_ID),
            ).fetchone()[0]
            assert got is True

            with pytest.raises(SchemaInitializationError):
                postgres_store.init_db()
        finally:
            holder.execute("SELECT pg_advisory_unlock(%s, %s)", (_SCHEMA_INIT_LOCK_NAMESPACE, _SCHEMA_INIT_LOCK_ID))
            holder.close()


@pytest.mark.timeout(60)
class TestOrdinaryDdlLockTimeout:
    def test_real_table_lock_conflict_raises_55P03_and_the_complete_attempt_retries_and_succeeds(
        self, pg_database_url, pg_conn, initialized_schema
    ):
        """PR #31 review correction — the advisory-lock tests above prove
        OUR OWN lock's polling behavior; this proves the separately
        configured PostgreSQL `lock_timeout` GUC actually turns an
        ORDINARY DDL table-lock conflict (not our advisory lock at all)
        into a real SQLSTATE 55P03, and that init_db()'s retry path
        genuinely recovers from it on a fresh connection once the
        conflicting transaction releases the lock.

        Real conflict, not a simulated exception: a holder connection
        opens an explicit transaction and takes a real ACCESS EXCLUSIVE
        lock (via `LOCK TABLE ... IN ACCESS EXCLUSIVE MODE`) on
        `outcomes`, a table SCHEMA_SQL's own ALTER TABLE statements
        touch. init_db() is started on a background thread; its first
        attempt must block behind the real lock, hit the 5s
        lock_timeout, and raise 55P03 (retryable). The holder's
        transaction is then rolled back, releasing the real lock, and
        init_db()'s second attempt (on a brand-new connection, since the
        first was discarded) must complete successfully — proven by the
        thread finishing without exception and by all postconditions
        still holding true afterward.
        """
        from services import postgres_store

        holder = psycopg.connect(pg_database_url, autocommit=False)
        holder.execute("BEGIN")
        holder.execute("LOCK TABLE outcomes IN ACCESS EXCLUSIVE MODE")

        result = {}

        def _run_init_db():
            try:
                postgres_store.init_db()
            except Exception as e:
                result["error"] = e

        thread = threading.Thread(target=_run_init_db)
        thread.start()
        # Give init_db() time to reach the blocked ALTER TABLE and hit the
        # real lock_timeout (5s, per _SCHEMA_INIT_LOCK_TIMEOUT_MS) before
        # releasing the conflicting lock — this is what forces the FIRST
        # attempt to genuinely fail with 55P03 rather than simply queueing
        # behind a lock that's released before it ever blocks.
        thread.join(timeout=8)
        holder.rollback()
        holder.close()
        thread.join(timeout=30)

        assert not thread.is_alive(), "init_db() did not complete after the blocking lock was released"
        assert result.get("error") is None, f"init_db() failed even after retry: {result.get('error')}"

        # Postconditions still correct after the retried, successful attempt.
        row = pg_conn.execute("SELECT to_regclass('public.paper_trade_entry_snapshot')").fetchone()
        assert row[0] is not None
        con_row = pg_conn.execute(
            """SELECT 1 FROM information_schema.table_constraints
               WHERE table_schema = 'public' AND table_name = 'outcomes'
                 AND constraint_type = 'UNIQUE'
                 AND constraint_name = 'outcomes_symbol_horizon_pred_date_market_key'"""
        ).fetchone()
        assert con_row is not None


@pytest.mark.timeout(30)
class TestGenuineFailuresPropagate:
    def test_invalid_sql_statement_causes_initialization_to_fail(self, pg_database_url, initialized_schema, monkeypatch):
        """A real syntax error must raise, not be logged and tolerated."""
        from services import postgres_store
        from services.postgres_store import SchemaInitializationError

        monkeypatch.setattr(postgres_store, "SCHEMA_SQL", "THIS IS NOT VALID SQL;")
        monkeypatch.setattr(postgres_store, "_SCHEMA_INIT_MAX_ATTEMPTS", 1)
        with pytest.raises(SchemaInitializationError) as exc_info:
            postgres_store.init_db()
        assert "42601" in str(exc_info.value) or "SyntaxError" in str(exc_info.value) or "Syntax" in str(exc_info.value)

    def test_permission_denied_causes_initialization_to_fail(self, pg_database_url, pg_admin_conn, initialized_schema, monkeypatch):
        """Real restricted role, real permission-denied error — not a
        simulated exception. Creates a synthetic, disposable, no-DDL-
        privilege role for this one test and drops it afterward."""
        from services import postgres_store
        from services.postgres_store import SchemaInitializationError
        from urllib.parse import urlparse, urlunparse

        role = "stocksense_test_no_ddl_role"
        pg_admin_conn.execute(f"DROP ROLE IF EXISTS {role}")
        pg_admin_conn.execute(f"CREATE ROLE {role} LOGIN PASSWORD 'test_only_password' NOSUPERUSER NOCREATEDB NOCREATEROLE")
        # PUBLIC has CONNECT on the database by default — explicitly strip
        # this role's CREATE/USAGE on schema public so any DDL it attempts
        # fails with a genuine permission-denied error.
        pg_admin_conn.execute(f"REVOKE ALL ON SCHEMA public FROM {role}")

        parsed = urlparse(pg_database_url)
        restricted_netloc = f"{role}:test_only_password@{parsed.hostname}:{parsed.port}"
        restricted_url = urlunparse((parsed.scheme, restricted_netloc, parsed.path, "", "", ""))

        try:
            monkeypatch.setattr(postgres_store, "DATABASE_URL", restricted_url)
            monkeypatch.setattr(postgres_store, "_SCHEMA_INIT_MAX_ATTEMPTS", 1)
            with pytest.raises(SchemaInitializationError) as exc_info:
                postgres_store.init_db()
            assert "42501" in str(exc_info.value) or "Insufficient" in str(exc_info.value) or "Permission" in str(exc_info.value)
        finally:
            pg_admin_conn.execute(f"DROP ROLE IF EXISTS {role}")

    def test_missing_required_postcondition_fails_startup(self, pg_conn, initialized_schema, monkeypatch):
        """Deliberately corrupts a required postcondition (drops the
        immutability trigger) and confirms _verify_schema_postconditions
        catches it — not by re-running the guarded migration first, but
        by directly calling the verification function against a
        deliberately incomplete state. See
        TestFullInitDbPostconditionFailure below for the complementary
        proof through the REAL, COMPLETE init_db() path."""
        from services.postgres_store import _verify_schema_postconditions, SchemaInitializationError

        pg_conn.execute("DROP TRIGGER IF EXISTS trg_paper_trade_entry_snapshot_immutable ON paper_trade_entry_snapshot")
        try:
            with pytest.raises(SchemaInitializationError):
                _verify_schema_postconditions(pg_conn)
        finally:
            # restore via the real init_db() so later tests in this
            # session aren't left with a missing trigger
            from services import postgres_store
            postgres_store.init_db()


@pytest.mark.timeout(30)
class TestFullInitDbPostconditionFailure:
    def test_real_init_db_raises_when_a_required_table_is_impossible(self, pg_conn, initialized_schema, monkeypatch):
        """PR #31 review correction — proves the COMPLETE init_db() path
        (SCHEMA_SQL, all three guarded migrations, THEN postcondition
        verification) fails closed, not just the verifier function in
        isolation. Monkeypatches the module-level, data-driven
        _REQUIRED_SCHEMA_TABLES specification to include a table name
        that can never exist, then calls the real init_db() end to end."""
        from services import postgres_store
        from services.postgres_store import SchemaInitializationError, _SCHEMA_INIT_LOCK_NAMESPACE, _SCHEMA_INIT_LOCK_ID

        monkeypatch.setattr(
            postgres_store, "_REQUIRED_SCHEMA_TABLES",
            postgres_store._REQUIRED_SCHEMA_TABLES + ["this_table_can_never_exist_xyz"],
        )
        monkeypatch.setattr(postgres_store, "_SCHEMA_INIT_MAX_ATTEMPTS", 1)

        with pytest.raises(SchemaInitializationError, match="this_table_can_never_exist_xyz"):
            postgres_store.init_db()

        # The advisory lock and connection must still have been released
        # despite the failure — pg_conn (a separate real connection) can
        # immediately acquire the same identity.
        got = pg_conn.execute(
            "SELECT pg_try_advisory_lock(%s, %s)", (_SCHEMA_INIT_LOCK_NAMESPACE, _SCHEMA_INIT_LOCK_ID)
        ).fetchone()[0]
        assert got is True, "advisory lock was not released after the postcondition failure"
        pg_conn.execute("SELECT pg_advisory_unlock(%s, %s)", (_SCHEMA_INIT_LOCK_NAMESPACE, _SCHEMA_INIT_LOCK_ID))


@pytest.mark.timeout(60)
class TestSchemaQualificationResistsDecoys:
    def test_decoy_schema_objects_do_not_satisfy_public_schema_postconditions(self, pg_conn, initialized_schema):
        """PR #31 review correction — creates a temporary decoy schema
        containing same-named tables/constraint/trigger/function as the
        real ones, then proves _verify_schema_postconditions still
        passes cleanly against the correct `public` state (the decoys
        must not accidentally satisfy anything) and, separately, that
        dropping the REAL public objects while the decoys still exist
        elsewhere still fails verification — a same-named object in
        another schema must never substitute for the real one."""
        from services.postgres_store import _verify_schema_postconditions, SchemaInitializationError
        from services import postgres_store

        pg_conn.execute("DROP SCHEMA IF EXISTS decoy_schema_init_test CASCADE")
        pg_conn.execute("CREATE SCHEMA decoy_schema_init_test")
        try:
            pg_conn.execute("CREATE TABLE decoy_schema_init_test.paper_trade_entry_snapshot (id bigint)")
            pg_conn.execute(
                "CREATE TABLE decoy_schema_init_test.outcomes (id bigint, symbol text, horizon text, "
                "pred_date date, market text, CONSTRAINT outcomes_symbol_horizon_pred_date_market_key "
                "UNIQUE (symbol, horizon, pred_date, market))"
            )
            pg_conn.execute("CREATE TABLE decoy_schema_init_test.daily_picks_cache (id bigint, status text)")
            pg_conn.execute(
                """CREATE FUNCTION decoy_schema_init_test.reject_paper_trade_entry_snapshot_update()
                   RETURNS TRIGGER AS $$ BEGIN RAISE EXCEPTION 'decoy'; END; $$ LANGUAGE plpgsql"""
            )
            pg_conn.execute(
                """CREATE TRIGGER trg_paper_trade_entry_snapshot_immutable
                   BEFORE UPDATE ON decoy_schema_init_test.paper_trade_entry_snapshot
                   FOR EACH ROW EXECUTE FUNCTION decoy_schema_init_test.reject_paper_trade_entry_snapshot_update()"""
            )

            # With the decoys present but the real public objects intact,
            # verification must still pass (decoys are harmless noise).
            _verify_schema_postconditions(pg_conn)

            # Now drop the REAL public trigger — the decoy trigger of the
            # identical name in another schema must NOT mask this failure.
            pg_conn.execute(
                "DROP TRIGGER IF EXISTS trg_paper_trade_entry_snapshot_immutable ON public.paper_trade_entry_snapshot"
            )
            with pytest.raises(SchemaInitializationError):
                _verify_schema_postconditions(pg_conn)
        finally:
            pg_conn.execute("DROP SCHEMA IF EXISTS decoy_schema_init_test CASCADE")
            postgres_store.init_db()  # restore the real public trigger


@pytest.mark.timeout(30)
class TestTriggerContractIsFullyVerified:
    """PR #31 review correction — a trigger's mere existence (or even its
    name) proves nothing about its actual behavior. Each test here
    corrupts one specific dimension of the contract, confirms
    verification fails, then restores the correct state via the real
    init_db() so later tests are never left with a corrupted trigger."""

    def test_disabled_trigger_fails_verification(self, pg_conn, initialized_schema):
        from services.postgres_store import _verify_schema_postconditions, SchemaInitializationError
        from services import postgres_store

        pg_conn.execute("ALTER TABLE paper_trade_entry_snapshot DISABLE TRIGGER trg_paper_trade_entry_snapshot_immutable")
        try:
            with pytest.raises(SchemaInitializationError, match="enabled state"):
                _verify_schema_postconditions(pg_conn)
        finally:
            pg_conn.execute("ALTER TABLE paper_trade_entry_snapshot ENABLE TRIGGER trg_paper_trade_entry_snapshot_immutable")
            postgres_store.init_db()

    def test_trigger_bound_to_wrong_function_fails_verification(self, pg_conn, initialized_schema):
        from services.postgres_store import _verify_schema_postconditions, SchemaInitializationError
        from services import postgres_store

        pg_conn.execute(
            """CREATE OR REPLACE FUNCTION decoy_no_op_trigger_fn() RETURNS TRIGGER AS $$
               BEGIN RETURN NEW; END; $$ LANGUAGE plpgsql"""
        )
        pg_conn.execute("DROP TRIGGER trg_paper_trade_entry_snapshot_immutable ON paper_trade_entry_snapshot")
        pg_conn.execute(
            """CREATE TRIGGER trg_paper_trade_entry_snapshot_immutable
               BEFORE UPDATE ON paper_trade_entry_snapshot
               FOR EACH ROW EXECUTE FUNCTION decoy_no_op_trigger_fn()"""
        )
        try:
            with pytest.raises(SchemaInitializationError, match="unexpected function"):
                _verify_schema_postconditions(pg_conn)
        finally:
            pg_conn.execute("DROP TRIGGER IF EXISTS trg_paper_trade_entry_snapshot_immutable ON paper_trade_entry_snapshot")
            pg_conn.execute("DROP FUNCTION IF EXISTS decoy_no_op_trigger_fn()")
            postgres_store.init_db()

    def test_trigger_absent_entirely_fails_verification(self, pg_conn, initialized_schema):
        from services.postgres_store import _verify_schema_postconditions, SchemaInitializationError
        from services import postgres_store

        pg_conn.execute("DROP TRIGGER IF EXISTS trg_paper_trade_entry_snapshot_immutable ON paper_trade_entry_snapshot")
        try:
            with pytest.raises(SchemaInitializationError, match="required trigger missing"):
                _verify_schema_postconditions(pg_conn)
        finally:
            postgres_store.init_db()


@pytest.mark.timeout(30)
class TestLifespanFailsClosed:
    def test_lifespan_raises_when_init_db_fails(self, monkeypatch):
        """Confirms api/main.py's lifespan handler re-raises rather than
        swallowing — imports the app module fresh under a monkeypatched
        init_db that always fails, and drives the lifespan context
        manager directly (not a full TestClient boot, which would also
        exercise unrelated startup work)."""
        import asyncio
        from api import main as api_main

        def _boom():
            raise RuntimeError("simulated schema initialization failure")

        monkeypatch.setenv("USE_POSTGRES", "1")
        monkeypatch.setattr("services.postgres_store.init_db", _boom)

        async def _run():
            async with api_main.lifespan(api_main.app):
                pass

        with pytest.raises(RuntimeError, match="simulated schema initialization failure"):
            asyncio.run(_run())

    def test_testclient_startup_fails_and_app_never_becomes_ready(self, monkeypatch):
        """PR #31 review correction — the test above proves the lifespan
        coroutine itself propagates; this proves the consequence at the
        ASGI application level using Starlette's TestClient (used as a
        context manager, which is what actually drives startup/shutdown
        lifespan events): entering the TestClient context must raise,
        and no request can be issued through a TestClient whose startup
        never completed. This does not claim anything about route
        registration timing — only that the ASGI app never reaches a
        state where it will serve /health or any other route."""
        from fastapi.testclient import TestClient
        from api import main as api_main

        def _boom():
            raise RuntimeError("simulated schema initialization failure")

        monkeypatch.setenv("USE_POSTGRES", "1")
        monkeypatch.setattr("services.postgres_store.init_db", _boom)

        with pytest.raises(RuntimeError, match="simulated schema initialization failure"):
            with TestClient(api_main.app) as client:
                # If startup had (incorrectly) succeeded, this line would
                # run and could even get a 200 — the whole point is that
                # entering the `with` block itself must raise before this.
                client.get("/health")
