"""
DP-033 second readiness pass (2026-07-22) — database-enforced append-only
proof for sec_pit_facts (SQLite path; the Postgres trigger SQL is
identical in intent, syntax-reviewed but NOT executable against a real
Postgres instance in this environment — no production database access
was available this session, documented honestly rather than assumed
passing).

Uses the accurate term "database-enforced append-only" per this
session's own instruction -- not claimed cryptographic, administrator-
proof, or absolute immutability (a superuser/table-owner with DROP
TRIGGER privilege could still remove the trigger; this defends against
the application's normal connection role performing an ordinary
UPDATE/DELETE, not against a privileged administrator).
"""
import os
import sqlite3
import tempfile

import pytest

import services.sec_pit_store as store


@pytest.fixture(autouse=True)
def _isolated_sqlite(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    monkeypatch.setattr(store, "_SQLITE_PATH", path)
    monkeypatch.setattr(store, "_USE_POSTGRES", False)
    monkeypatch.setattr(store, "_initialized", False)
    yield path
    store._initialized = False
    if os.path.exists(path):
        os.unlink(path)


def _seed_one_fact():
    store.ingest_symbol(
        "AAPL", "run_1", "manifest_v1",
        resolve_cik_fn=lambda sym: 320193,
        fetch_company_facts_fn=lambda cik: {
            "cik": 320193, "entityName": "AAPL",
            "facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
                {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY",
                 "form": "10-K", "filed": "2025-10-31", "accn": "A-1"},
            ]}}}},
        },
    )


@pytest.mark.unit
class TestAppendOnlyEnforcement:
    def test_valid_insert_succeeds(self):
        _seed_one_fact()
        with sqlite3.connect(store._SQLITE_PATH) as conn:
            n = conn.execute("SELECT COUNT(*) FROM sec_pit_facts").fetchone()[0]
        assert n == 1

    def test_update_is_rejected_by_the_database(self):
        _seed_one_fact()
        with sqlite3.connect(store._SQLITE_PATH) as conn:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute("UPDATE sec_pit_facts SET value = 999.0 WHERE accession = 'A-1'")

    def test_delete_is_rejected_by_the_database(self):
        _seed_one_fact()
        with sqlite3.connect(store._SQLITE_PATH) as conn:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute("DELETE FROM sec_pit_facts WHERE accession = 'A-1'")

    def test_facts_remain_readable_after_a_rejected_mutation_attempt(self):
        _seed_one_fact()
        with sqlite3.connect(store._SQLITE_PATH) as conn:
            try:
                conn.execute("UPDATE sec_pit_facts SET value = 999.0")
            except sqlite3.IntegrityError:
                pass
            row = conn.execute("SELECT value FROM sec_pit_facts WHERE accession = 'A-1'").fetchone()
        assert row[0] == 100.0  # unchanged — the rejected UPDATE had zero effect

    def test_amendment_still_inserts_as_a_new_row_append_only_does_not_block_legitimate_inserts(self):
        _seed_one_fact()
        store.ingest_symbol(
            "AAPL", "run_2", "manifest_v1",
            resolve_cik_fn=lambda sym: 320193,
            fetch_company_facts_fn=lambda cik: {
                "cik": 320193, "entityName": "AAPL",
                "facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
                    {"end": "2025-09-27", "val": 999.0, "fy": 2025, "fp": "FY",
                     "form": "10-K/A", "filed": "2026-01-15", "accn": "A-2"},
                ]}}}},
            },
        )
        with sqlite3.connect(store._SQLITE_PATH) as conn:
            n = conn.execute("SELECT COUNT(*) FROM sec_pit_facts").fetchone()[0]
        assert n == 2  # both the original and the amendment persist

    def test_schema_initialization_is_safely_rerunnable(self):
        store.init_db()
        store._initialized = False  # force a second real re-run, not the in-process guard
        store.init_db()  # must not raise
        _seed_one_fact()
        with sqlite3.connect(store._SQLITE_PATH) as conn:
            n = conn.execute("SELECT COUNT(*) FROM sec_pit_facts").fetchone()[0]
        assert n == 1


@pytest.mark.unit
class TestPostgresTriggerSqlReviewed:
    """The Postgres trigger SQL cannot be executed against a real
    Postgres instance in this environment (no production database access
    this session) -- reviewed for syntactic/semantic correctness instead,
    documented honestly as review-only, not execution-verified."""

    def test_pg_schema_contains_reject_mutation_function_and_both_triggers(self):
        assert "CREATE OR REPLACE FUNCTION sec_pit_facts_reject_mutation" in store._PG_SCHEMA
        assert "BEFORE UPDATE ON sec_pit_facts" in store._PG_SCHEMA
        assert "BEFORE DELETE ON sec_pit_facts" in store._PG_SCHEMA
        assert "RAISE EXCEPTION" in store._PG_SCHEMA

    def test_pg_schema_does_not_rely_on_row_level_security_alone(self):
        # RLS is present (matches this repo's existing convention for
        # closing the "RLS disabled" Supabase finding on every public
        # table) but the append-only guarantee itself comes from the
        # trigger, which is effective even for a BYPASSRLS/owner role --
        # confirmed by inspecting the schema: the trigger function exists
        # independently of the ENABLE ROW LEVEL SECURITY statements.
        assert "ENABLE ROW LEVEL SECURITY" in store._PG_SCHEMA
        # Both mechanisms are present and independently defined (the
        # trigger function/attachment statements exist as their own
        # standalone SQL objects, not expressed as an RLS policy) — the
        # append-only guarantee does not depend on RLS being respected by
        # the connecting role.
        assert "sec_pit_facts_reject_mutation" in store._PG_SCHEMA
