"""
Trade Postmortem Sprint 3A, Pre-Stage-H Correction 3 — MOCK-LEVEL
transaction-boundary proof only.

This file is explicitly NOT sufficient proof on its own — Correction 3
requires a real-PostgreSQL integration test that begins a close
request, commits it, and observes provider invocation ordering across a
SECOND connection. That test does not exist yet: this sandbox has no
`psql`/`pg_isready` and no reachable PostgreSQL instance (checked at
the start of this correction), so the real-PostgreSQL leg of
Correction 3 is environment-blocked, not implemented, and honestly
reported as such rather than faked with a mock standing in for it.

What THIS file proves, at the unit level: a spy provider correctly
records whether it was invoked while a simulated transaction context
was still open, and the generation-flow ordering this sprint intends
(commit-then-acquire) is exercised end-to-end against a fake connection
that raises if any operation occurs after "commit" without an explicit
new transaction. This gives fast, deterministic regression coverage for
the ordering CONTRACT — it is a mock, and is only a complement to, never
a replacement for, real-PostgreSQL verification.
"""
import datetime as dt

import pytest

from services.market_hours import ET
from services.postmortem.price_path_acquisition import PriceProviderAcquisitionError, acquire_price_path_evidence


class _FakeTransactionalConn:
    """Simulates a close transaction: begin() opens a window during
    which `in_transaction` is True; commit() closes it. A provider spy
    can inspect `conn.in_transaction` at call time to assert it was
    invoked strictly after commit."""

    def __init__(self):
        self.in_transaction = False
        self.committed_state = {}
        self.lock_held = False

    def begin(self):
        self.in_transaction = True
        self.lock_held = True

    def commit(self, **state):
        self.committed_state.update(state)
        self.in_transaction = False
        self.lock_held = False


@pytest.mark.unit
class TestMockTransactionBoundaryOrdering:
    def test_provider_spy_sees_transaction_already_committed(self):
        conn = _FakeTransactionalConn()
        conn.begin()
        conn.commit(trade_status="CLOSED", cash_credited=True, exit_snapshot_id=1, outbox_id=1)

        observed = {}

        def spy_bars(symbol, start, end):
            observed["in_transaction_at_call"] = conn.in_transaction
            observed["lock_held_at_call"] = conn.lock_held
            observed["committed_state_at_call"] = dict(conn.committed_state)
            return []

        def spy_splits(symbol, start, end):
            return []

        acquire_price_path_evidence(
            paper_trade_id=1, user_id="u", symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET),
            exit_timestamp=dt.datetime(2026, 6, 4, tzinfo=ET),
            fetch_bars_fn=spy_bars, fetch_splits_fn=spy_splits,
        )

        assert observed["in_transaction_at_call"] is False
        assert observed["lock_held_at_call"] is False
        assert observed["committed_state_at_call"] == {
            "trade_status": "CLOSED", "cash_credited": True, "exit_snapshot_id": 1, "outbox_id": 1,
        }

    def test_provider_never_receives_the_connection_object(self):
        """No amount of care inside a spy can prove this, but the
        function signature itself is checked — no positional or keyword
        slot exists for a connection, so the caller has nothing to pass
        even if it wanted to."""
        conn = _FakeTransactionalConn()

        def spy_bars(symbol, start, end):
            assert conn not in (symbol, start, end)
            return []

        acquire_price_path_evidence(
            paper_trade_id=1, user_id="u", symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET),
            exit_timestamp=dt.datetime(2026, 6, 4, tzinfo=ET),
            fetch_bars_fn=spy_bars, fetch_splits_fn=lambda *a: [],
        )

    def test_provider_failure_after_commit_does_not_touch_committed_state(self):
        conn = _FakeTransactionalConn()
        conn.begin()
        conn.commit(trade_status="CLOSED", cash_credited=True)
        snapshot_before = dict(conn.committed_state)

        def failing_bars(symbol, start, end):
            raise PriceProviderAcquisitionError("PROVIDER_FETCH_FAILED", "simulated outage")

        with pytest.raises(PriceProviderAcquisitionError):
            acquire_price_path_evidence(
                paper_trade_id=1, user_id="u", symbol="AAPL", market="US",
                market_timezone_name="America/New_York", market_tzinfo=ET,
                entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET),
                exit_timestamp=dt.datetime(2026, 6, 4, tzinfo=ET),
                fetch_bars_fn=failing_bars, fetch_splits_fn=lambda *a: [],
            )
        assert conn.committed_state == snapshot_before
        assert conn.in_transaction is False


@pytest.mark.unit
class TestCorrection3EnvironmentStatusHonesty:
    def test_this_suite_does_not_claim_to_be_real_postgres_proof(self):
        """A meta-test making the limitation impossible to silently lose
        track of: this module's own docstring must keep stating that a
        real-PostgreSQL leg is required and currently blocked."""
        import tests.unit.test_price_path_transaction_boundary_mock as mod
        assert "environment-blocked" in mod.__doc__
        assert "real-PostgreSQL" in mod.__doc__
