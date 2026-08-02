"""
Trade Postmortem Sprint 3A, Pre-Stage-H Correction 3 — MOCK-LEVEL
transaction-boundary proof.

This file is fast, deterministic complementary unit coverage for the
provider-acquisition ordering CONTRACT (commit-then-acquire, no open
connection during provider I/O) — it is a mock and is never a
replacement for real-PostgreSQL verification. The real-PostgreSQL leg
of Correction 3 is no longer blocked: it is proven by
tests/postgres_integration/test_current_report_generation_lifecycle.py
::test_8e_no_connection_open_during_provider_acquisition_phase and by
tests/postgres_integration/test_price_path_generation.py::
TestCloseTransactionFinancialIsolation
(test_closed_trade_cash_snapshot_outbox_all_visible_before_acquisition,
test_provider_failure_leaves_committed_financial_state_untouched,
test_provider_failure_creates_no_evidence_row), all of which run
against a real ephemeral PostgreSQL 15/17 instance in CI. THIS file
never substitutes for those — it only gives sub-second regression
coverage for the same ordering contract between full PostgreSQL runs.

Every call to the acquisition entry point below MUST inject all three
fetch-callback keyword arguments explicitly — omitting one lets the
call fall through to its production default, which for the dividends
callback reaches the live yfinance provider. Note that those defaults
are ordinary Python default-parameter values bound once at function-
definition time, NOT a live module-attribute lookup on every call — so
monkeypatching the module-level fetch_* functions does NOT intercept a
call that omits a keyword (the defect this file previously had): the
only reliable guard is TestNoLiveNetworkAccess's static source scan
below, which fails the build the moment any call site in this module
stops explicitly naming all three keywords.
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
            fetch_bars_fn=spy_bars, fetch_splits_fn=spy_splits, fetch_dividends_fn=lambda *a: [],
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
            fetch_bars_fn=spy_bars, fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [],
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
                fetch_bars_fn=failing_bars, fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [],
            )
        assert conn.committed_state == snapshot_before
        assert conn.in_transaction is False


@pytest.mark.unit
class TestNoLiveNetworkAccess:
    """Guards against exactly the defect this module previously had: a
    call to the acquisition entry point omitting fetch_dividends_fn
    silently fell through to the production default, which calls
    yfinance — a real, nondeterministic network dependency inside what
    is supposed to be a fast offline unit test.

    A dynamic monkeypatch-and-call guard is deliberately NOT used here:
    fetch_bars_fn/fetch_splits_fn/fetch_dividends_fn are ordinary
    Python default-parameter values bound once at function-definition
    time, not a live module-attribute lookup performed on every call —
    monkeypatching the module-level fetch_* functions would silently
    fail to intercept a call that omits a keyword, giving false
    confidence while the call still reaches the network. A static
    source scan is the only reliable guard against this class of
    defect for this file."""

    def test_every_call_site_in_this_module_names_all_three_offline_fetchers(self):
        import inspect
        import re
        import tests.unit.test_price_path_transaction_boundary_mock as mod

        source = inspect.getsource(mod)
        # Match only real call sites (an open paren immediately followed
        # by a newline, the shape every call in this file uses) — never
        # the bare mention of the function name in prose/docstrings.
        call_sites = re.findall(r"acquire_price_path_evidence\(\s*\n", source)
        assert len(call_sites) >= 3, (
            f"expected at least 3 acquire_price_path_evidence(...) call sites in this module, found {len(call_sites)}"
        )
        for keyword in ("fetch_bars_fn=", "fetch_splits_fn=", "fetch_dividends_fn="):
            count = source.count(keyword)
            assert count >= len(call_sites), (
                f"{keyword} appears {count} times but there are {len(call_sites)} call sites — "
                "a call site is missing an explicit offline override and may reach the live network"
            )
