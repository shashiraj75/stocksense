"""
Integration tests for market_leadership/persistence.py against a real
(temp-file) SQLite database — fresh schema, duplicate prevention, partial-
vs-complete promotion rules, repeated init idempotency. No live network,
no Postgres (SES-003 integration convention: multiple in-process modules
together, still no live external calls).
"""
import sqlite3

import pytest

from services.market_leadership import persistence as p


@pytest.fixture(autouse=True)
def _fresh_sqlite(tmp_path, monkeypatch):
    db = tmp_path / "ml.db"
    monkeypatch.setattr(p, "_USE_POSTGRES", False)
    monkeypatch.setattr(p, "_DB_PATH", str(db))
    monkeypatch.setattr(p, "_db_initialised", False)
    yield db


@pytest.mark.integration
class TestSchemaInitialization:
    def test_fresh_schema_creates_all_three_tables(self, _fresh_sqlite):
        p.init_leadership_db()
        with sqlite3.connect(_fresh_sqlite) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        assert {"ml_rs_snapshots", "ml_group_snapshots", "ml_breadth_snapshots"} <= tables

    def test_repeated_init_is_idempotent(self, _fresh_sqlite):
        p.init_leadership_db()
        p.init_leadership_db()  # must not raise, must not duplicate anything
        with sqlite3.connect(_fresh_sqlite) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='ml_rs_snapshots'"
            ).fetchone()[0]
        assert count == 1

    def test_repeated_process_guarded_init_does_not_reopen_connection(self, _fresh_sqlite, monkeypatch):
        calls = {"n": 0}
        real_conn = p._sqlite_conn

        def counting_conn():
            calls["n"] += 1
            return real_conn()

        monkeypatch.setattr(p, "_sqlite_conn", counting_conn)
        p.init_leadership_db()
        p.init_leadership_db()
        p.init_leadership_db()
        assert calls["n"] == 1  # guarded — only the first call actually opens/inits


@pytest.mark.integration
class TestRsSnapshotRoundTrip:
    def test_save_and_read_back(self, _fresh_sqlite):
        payload = {"symbol": "ABC", "stock_rs_score": 80}
        p.save_rs_snapshot("IN", "ABC", "2026-07-20", "u1", "v1", "m1", payload)
        got = p.get_latest_rs_snapshot("IN", "ABC", "u1")
        assert got == payload

    def test_no_snapshot_returns_none_not_a_fabricated_default(self, _fresh_sqlite):
        assert p.get_latest_rs_snapshot("IN", "NOPE", "u1") is None

    def test_partial_snapshot_never_returned_by_read_path(self, _fresh_sqlite):
        p.save_rs_snapshot("IN", "ABC", "2026-07-20", "u1", "v1", "m1", {"x": 1}, status="PARTIAL")
        assert p.get_latest_rs_snapshot("IN", "ABC", "u1") is None

    def test_partial_promotes_to_complete_on_reinsert(self, _fresh_sqlite):
        p.save_rs_snapshot("IN", "ABC", "2026-07-20", "u1", "v1", "m1", {"x": 1}, status="PARTIAL")
        p.save_rs_snapshot("IN", "ABC", "2026-07-20", "u1", "v1", "m1", {"x": 2}, status="COMPLETE")
        assert p.get_latest_rs_snapshot("IN", "ABC", "u1") == {"x": 2}

    def test_complete_snapshot_is_immutable_reinsert_is_a_noop(self, _fresh_sqlite):
        p.save_rs_snapshot("IN", "ABC", "2026-07-20", "u1", "v1", "m1", {"x": 1}, status="COMPLETE")
        p.save_rs_snapshot("IN", "ABC", "2026-07-20", "u1", "v1", "m1", {"x": 999}, status="COMPLETE")
        assert p.get_latest_rs_snapshot("IN", "ABC", "u1") == {"x": 1}  # first write wins

    def test_uniqueness_scoped_by_methodology_version(self, _fresh_sqlite):
        """A methodology bump must not collide with the prior version's row."""
        p.save_rs_snapshot("IN", "ABC", "2026-07-20", "u1", "v1", "m1", {"v": "old"})
        p.save_rs_snapshot("IN", "ABC", "2026-07-20", "u1", "v1", "m2", {"v": "new"})
        with sqlite3.connect(_fresh_sqlite) as conn:
            count = conn.execute("SELECT COUNT(*) FROM ml_rs_snapshots").fetchone()[0]
        assert count == 2

    def test_market_isolation_same_symbol_different_market_distinct_rows(self, _fresh_sqlite):
        p.save_rs_snapshot("IN", "V", "2026-07-20", "u1", "v1", "m1", {"m": "IN"})
        p.save_rs_snapshot("US", "V", "2026-07-20", "u1", "v1", "m1", {"m": "US"})
        assert p.get_latest_rs_snapshot("IN", "V", "u1") == {"m": "IN"}
        assert p.get_latest_rs_snapshot("US", "V", "u1") == {"m": "US"}


@pytest.mark.integration
class TestGroupAndBreadthSnapshots:
    def test_group_snapshot_uniqueness_includes_group_kind(self, _fresh_sqlite):
        p.save_group_snapshot("IN", "IT", "sector", "2026-07-20", "u1", "v1", "m1", {"a": 1})
        p.save_group_snapshot("IN", "IT", "industry", "2026-07-20", "u1", "v1", "m1", {"a": 2})
        with sqlite3.connect(_fresh_sqlite) as conn:
            count = conn.execute("SELECT COUNT(*) FROM ml_group_snapshots").fetchone()[0]
        assert count == 2

    def test_breadth_snapshot_round_trip(self, _fresh_sqlite):
        p.save_breadth_snapshot("US", "market", "2026-07-20", "u1", "v1", "m1", {"state": "HEALTHY"})
        with sqlite3.connect(_fresh_sqlite) as conn:
            row = conn.execute(
                "SELECT status FROM ml_breadth_snapshots WHERE market='US' AND scope='market'"
            ).fetchone()
        assert row[0] == "COMPLETE"


@pytest.mark.integration
class TestPartiallyMigratedAndLegacyState:
    def test_init_leadership_db_never_touches_unrelated_tables(self, _fresh_sqlite):
        with sqlite3.connect(_fresh_sqlite) as conn:
            conn.execute("CREATE TABLE unrelated_legacy (id INTEGER PRIMARY KEY)")
        p.init_leadership_db()
        with sqlite3.connect(_fresh_sqlite) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        assert "unrelated_legacy" in tables
        assert "ml_rs_snapshots" in tables
