"""
DP-033 second readiness pass (2026-07-22) — database-enforced append-only
proof for sec_pit_facts (SQLite path, run in this automated suite; the
Postgres trigger SQL below was ALSO execution-verified this session
against a real, disposable Railway Postgres instance (`Postgres-aOTX`,
provisioned and torn down for this purpose — not the SQLite-derived
claim the prior pass had to accept as a documented gap). That live run
found and fixed a real Postgres-only bug: the `period_start` NULL-safety
sentinel was `""`, valid for SQLite's dynamically-typed TEXT column but
rejected by Postgres's typed `DATE` column
(`psycopg.errors.InvalidDatetimeFormat`); fixed by using a real sentinel
date (`0001-01-01`) plus a second, independent bug the same live run
surfaced — the replay-read path built `entry["start"]` from a bare
truthiness check instead of calling `_period_start_or_none()`, which
only "worked" by coincidence while the sentinel was empty-string
(falsy); fixed to call `_period_start_or_none()` explicitly. Both fixes
are in `sec_pit_store.py`; the live Postgres run was re-verified green
after both fixes, including UPDATE/DELETE rejection, idempotent
re-ingest, and instant-fact (`period_start IS NULL` on read) dedup. The
live-run script and disposable database are not part of this repo or
CI — this docstring records what was verified and how, since it cannot
be reproduced without live Railway/Postgres credentials.

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
    """These specific static-content checks run in CI without live
    Postgres access, so they stay review-only by nature (asserting on
    the SQL string). The trigger SQL they check has SEPARATELY been
    execution-verified this session against a real, disposable Railway
    Postgres instance — see this file's module docstring for the two
    real bugs that live run found and fixed (period_start sentinel type
    mismatch; a replay-read path that bypassed the sentinel normalizer).
    Do not read "review-only" below as "never executed" — it describes
    only what these particular assertions do."""

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
