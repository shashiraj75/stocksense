"""
services.postgres_store.get_latest_signals_batch — Session 13 root-cause
fix for Portfolio's Signal column showing "Not cached" for nearly every
holding. See its own docstring for the full root-cause explanation;
these tests are fully mocked at the connection-pool boundary (no real
Postgres) and check: batch shape, missing-symbol handling, and that the
query is the single most-recent snapshot per symbol (not every historical
row).
"""
from unittest.mock import patch, MagicMock

import pytest

from services import postgres_store


def _mock_pool_returning(rows):
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = rows
    conn_ctx = MagicMock()
    conn_ctx.__enter__.return_value = conn
    conn_ctx.__exit__.return_value = False
    pool = MagicMock()
    pool.connection.return_value = conn_ctx
    return pool


@pytest.mark.unit
class TestGetLatestSignalsBatch:
    def test_returns_signal_and_confidence_per_symbol(self, monkeypatch):
        monkeypatch.setattr(
            postgres_store, "_get_pool",
            lambda: _mock_pool_returning([
                ("TCS", "BUY", 72.5, "2026-07-10"),
                ("INFY", "HOLD", 51.0, "2026-07-10"),
            ]),
        )
        result = postgres_store.get_latest_signals_batch(["TCS", "INFY"], "medium")
        assert result == {
            "TCS": {"signal": "BUY", "confidence": 72.5, "snapshot_date": "2026-07-10"},
            "INFY": {"signal": "HOLD", "confidence": 51.0, "snapshot_date": "2026-07-10"},
        }

    def test_symbol_with_no_snapshot_is_simply_absent_from_the_result(self, monkeypatch):
        """Callers must treat a missing key as "no persisted signal
        either" — this function never fabricates an entry for a symbol
        Daily Picks has never scored."""
        monkeypatch.setattr(
            postgres_store, "_get_pool",
            lambda: _mock_pool_returning([("TCS", "BUY", 72.5, "2026-07-10")]),
        )
        result = postgres_store.get_latest_signals_batch(["TCS", "NEVERSCORED"], "medium")
        assert "TCS" in result
        assert "NEVERSCORED" not in result

    def test_empty_symbol_list_returns_empty_dict_without_querying(self, monkeypatch):
        pool = MagicMock()
        monkeypatch.setattr(postgres_store, "_get_pool", lambda: pool)
        result = postgres_store.get_latest_signals_batch([], "medium")
        assert result == {}
        pool.connection.assert_not_called()

    def test_query_selects_one_row_per_symbol_ordered_by_most_recent_snapshot(self, monkeypatch):
        """Asserts the actual SQL shape — DISTINCT ON (symbol) ordered by
        snapshot_date DESC — which is what makes this "latest known
        signal" rather than an arbitrary historical row."""
        captured = {}

        def _fake_pool():
            pool = _mock_pool_returning([("TCS", "BUY", 72.5, "2026-07-10")])
            real_conn = pool.connection.return_value.__enter__.return_value
            original_execute = real_conn.execute

            def _execute(sql, params):
                captured["sql"] = sql
                captured["params"] = params
                return original_execute(sql, params)
            real_conn.execute = _execute
            return pool

        monkeypatch.setattr(postgres_store, "_get_pool", _fake_pool)
        postgres_store.get_latest_signals_batch(["TCS"], "medium")
        assert "DISTINCT ON (symbol)" in captured["sql"]
        assert "ORDER BY symbol, snapshot_date DESC" in captured["sql"]
        assert captured["params"] == (["TCS"], "medium")

    def test_symbols_are_uppercased(self, monkeypatch):
        captured = {}

        def _fake_pool():
            pool = _mock_pool_returning([])
            real_conn = pool.connection.return_value.__enter__.return_value
            fetch_result = real_conn.execute.return_value

            def _execute(sql, params):
                captured["params"] = params
                return fetch_result
            real_conn.execute = _execute
            return pool

        monkeypatch.setattr(postgres_store, "_get_pool", _fake_pool)
        postgres_store.get_latest_signals_batch(["tcs", "infy"], "medium")
        assert captured["params"][0] == ["TCS", "INFY"]
