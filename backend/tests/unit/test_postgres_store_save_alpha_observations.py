"""
Product Integrity Workstream #025 — save_alpha_observations write bug.

Root cause: postgres_store.save_alpha_observations() called
conn.executemany(...) directly on a psycopg3 Connection object. psycopg3's
Connection has no executemany method — only Cursor does (Connection does
have a convenience execute() since psycopg 3.1, but never executemany()).
Every call raised AttributeError, always caught by the function's own
try/except and logged as a non-fatal warning ("shadow telemetry, must
never interrupt the Daily Picks job") — so the failure never surfaced
anywhere visible. Confirmed via direct reproduction against the real
driver: the `alpha_observations` table had zero rows in production since
this feature was written, despite running (and failing) on every single
Daily Picks generation for both markets.

The existing test_alpha_observations.py regression suite never caught
this because every persistence test explicitly forces the SQLite
fallback path (monkeypatch.setattr(ao, "USE_POSTGRES", False)) — the real
Postgres branch in this file had zero test coverage. This is also why a
MagicMock()-based test wouldn't have caught it either: MagicMock happily
fabricates any attribute you ask for, including a fake `.executemany`
that doesn't exist on the real driver. `_FakePsycopg3Connection` below
deliberately mimics psycopg3's real, narrower attribute surface instead
(has `cursor()`, does not have `executemany`) so a regression back to
`conn.executemany(...)` fails this test with the same AttributeError it
would raise against the real database, not a false green from an
over-permissive mock.
"""

from unittest.mock import MagicMock

import pytest

from services import postgres_store


class _FakePsycopg3Cursor:
    """Records executemany calls; usable as a context manager, matching
    `with conn.cursor() as cur:`."""

    def __init__(self):
        self.executemany_calls: list[tuple[str, list]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def executemany(self, sql: str, rows: list) -> None:
        self.executemany_calls.append((sql, rows))


class _FakePsycopg3Connection:
    """Deliberately mimics psycopg3's real Connection attribute surface for
    this test's purpose: has `cursor()`, does NOT have `executemany` —
    accessing `.executemany` on an instance of this class raises
    AttributeError exactly like the real driver, since this class simply
    never defines it (no __getattr__ fallback, unlike MagicMock)."""

    def __init__(self, cursor: _FakePsycopg3Cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _mock_pool_with(conn: _FakePsycopg3Connection):
    conn_ctx = MagicMock()
    conn_ctx.__enter__.return_value = conn
    conn_ctx.__exit__.return_value = False
    pool = MagicMock()
    pool.connection.return_value = conn_ctx
    return pool


_SAMPLE_ROW = {
    "observation_id": "obs-1", "run_id": "run-1", "market": "IN", "horizon": "short",
    "symbol": "TCS", "run_generated_at": "2026-07-16T00:00:00+00:00",
    "run_session_date": "2026-07-16", "reference_session_date": "2026-07-15",
    "reference_price": 3500.0, "reference_price_source": "yahoo_daily_history",
    "reference_price_basis": "adjusted_close",
    "technical_raw_score": 60, "fundamental_raw_score": 55,
    "sentiment_raw_score": None, "quality_raw_score": 70,
    "sentiment_available": False, "quality_available": True,
    "technical_zscore": 0.5, "fundamental_zscore": 0.3,
    "sentiment_zscore": 0.0, "quality_zscore": 0.2,
    "composite_score": 65, "ic_combined_alpha": 0.4, "meta_alpha": None,
    "ranking_alpha": 0.4, "signal": "BUY", "signal_confidence": 60,
    "canonical_regime_id": 1, "canonical_regime_label": "BULL_VOLATILE",
    "feature_schema_version": 1, "regime_schema_version": 1,
    "is_daily_pick": 0, "pick_rank": None, "portfolio_weight": None,
}


class TestSaveAlphaObservationsUsesCursorExecutemany:
    def test_returns_true_and_writes_via_the_cursor_not_the_connection(self, monkeypatch):
        cursor = _FakePsycopg3Cursor()
        conn = _FakePsycopg3Connection(cursor)
        monkeypatch.setattr(postgres_store, "_get_pool", lambda: _mock_pool_with(conn))

        result = postgres_store.save_alpha_observations([_SAMPLE_ROW])

        assert result is True, (
            "save_alpha_observations must return True on a genuinely successful "
            "write — a regression to conn.executemany(...) would hit "
            "AttributeError against this fake (which mimics the real driver's "
            "attribute surface), get caught by the function's own except "
            "block, and return False here instead."
        )
        assert len(cursor.executemany_calls) == 1
        sql, rows = cursor.executemany_calls[0]
        assert "INSERT INTO alpha_observations" in sql
        assert rows == [_SAMPLE_ROW]

    def test_empty_rows_short_circuits_without_touching_the_pool_at_all(self, monkeypatch):
        # No pool/connection should be requested for an empty batch — a
        # stricter check than just "returns True", since it also confirms
        # the function's early-return guard still exists after this fix.
        def _fail_if_called():
            raise AssertionError("_get_pool() must not be called for an empty rows list")

        monkeypatch.setattr(postgres_store, "_get_pool", _fail_if_called)
        assert postgres_store.save_alpha_observations([]) is True

    def test_a_true_attributeerror_regression_is_caught_and_returns_false_not_raised(self, monkeypatch):
        """Directly proves the exact historical bug's failure mode: if the
        code ever regresses to calling `.executemany` on the connection
        itself, the function must degrade to `False` (matching this
        table's "shadow telemetry, never break the real job" contract),
        never raise up into the Daily Picks job lifecycle."""

        class _ConnWithoutExecutemany:
            def cursor(self):
                # Simulate what would happen if a future edit re-introduced
                # conn.executemany(...) — accessing the attribute directly
                # on this bare object raises AttributeError, same as the
                # real driver.
                raise AttributeError("'Connection' object has no attribute 'executemany'")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(
            postgres_store, "_get_pool",
            lambda: _mock_pool_with(_ConnWithoutExecutemany()),
        )
        result = postgres_store.save_alpha_observations([_SAMPLE_ROW])
        assert result is False
