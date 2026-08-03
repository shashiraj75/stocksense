"""
Trade Postmortem Engine, Sprint 2 — unit tests for
services.postmortem.close_service.close_paper_trade, the sole
authoritative trade-close path.

Follows the exact in-memory _FakeConn pattern established in
tests/integration/test_paper_trading_idempotency.py: a hand-written fake
that implements just enough of paper_trades/paper_trade_exit_snapshot/
paper_trade_postmortem_outbox semantics (including SELECT ... FOR UPDATE
row-locking-by-convention and INSERT ... ON CONFLICT DO NOTHING
uniqueness) to prove close_paper_trade's own application-level logic is
internally consistent. This is NOT a substitute for real-PostgreSQL
concurrency proof (see tests/postgres_integration/ for that).
"""
import datetime as dt
import threading
from contextlib import contextmanager

import pytest

from services.postmortem.close_service import (
    CALCULATION_VERSION,
    CloseValidationError,
    TradeAlreadyClosedError,
    TradeNotFoundError,
    TradeNotOwnedError,
    UnsupportedMarketError,
    _EXIT_SNAPSHOT_COLUMNS,
    _build_exit_snapshot_insert,
    close_paper_trade,
)
from services.postmortem.exit_snapshot import build_exit_snapshot as _build_exit_snapshot_obj
from services.postmortem.evidence_attribution import ATTRIBUTION_RULES_VERSION
from services.postmortem.exit_snapshot import CloseExitMechanism


def _new_trade(**overrides) -> dict:
    base = dict(
        id=1, user_id="user-aaa", symbol="AAPL", quantity=10, entry_price=100.0,
        status="OPEN", market="US", stop_loss=90.0, target_price=130.0,
        trade_management_mode="manual",
        levels_modified_after_entry=None, level_history_contract_version=None,
        stop_modified_after_entry=None, target_modified_after_entry=None,
    )
    base.update(overrides)
    return base


class _FakeConn:
    """One shared in-memory `trades`/`exit_snapshots`/`outbox_rows` table
    set per test (or per set of concurrent "connections" in the race
    test), guarded by a single lock exactly like the idempotency test's
    own _FakeConn — modeling PostgreSQL's row lock as this lock's
    critical section around the read-modify-write UPDATE."""

    def __init__(self, shared: dict):
        self.shared = shared
        self.calls = []
        self._pending = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self._pending = self._process(sql, params or ())
        return self

    def fetchone(self):
        return self._pending

    def _process(self, sql, params):
        shared = self.shared
        stripped = sql.strip()

        if stripped.startswith("SELECT user_id, symbol, quantity, entry_price, status, market"):
            (trade_id,) = params
            with shared["lock"]:
                trade = shared["trades"].get(trade_id)
                if trade is None:
                    return None
                return (
                    trade["user_id"], trade["symbol"], trade["quantity"], trade["entry_price"],
                    trade["status"], trade["market"], trade["stop_loss"], trade["target_price"],
                    trade["trade_management_mode"],
                    trade["levels_modified_after_entry"], trade["level_history_contract_version"],
                    trade["stop_modified_after_entry"], trade["target_modified_after_entry"],
                )

        if stripped.startswith("UPDATE paper_trades SET exit_price"):
            exit_price, closed_at, exit_reason, trade_id = params
            with shared["lock"]:
                trade = shared["trades"].get(trade_id)
                if trade is None or trade["status"] != "OPEN":
                    return None
                trade["status"] = "CLOSED"
                trade["exit_price"] = exit_price
                trade["closed_at"] = closed_at
                trade["exit_reason"] = exit_reason
                return (trade_id,)

        if stripped.startswith("INSERT INTO paper_trade_exit_snapshot"):
            with shared["lock"]:
                shared["exit_snapshots"].append(params)
            return None

        if stripped.startswith("INSERT INTO paper_trade_postmortem_outbox"):
            trade_id, user_id, schema_v, calc_v, rules_v, source_request_id = params
            key = (trade_id, schema_v, calc_v, rules_v)
            with shared["lock"]:
                if key in shared["outbox_by_key"]:
                    return None  # ON CONFLICT DO NOTHING
                new_id = shared["next_outbox_id"]
                shared["next_outbox_id"] += 1
                shared["outbox_by_key"][key] = new_id
                shared["outbox_rows"][new_id] = {"trade_id": trade_id, "user_id": user_id}
                return (new_id,)

        if stripped.startswith("SELECT id FROM paper_trade_postmortem_outbox"):
            trade_id, schema_v, calc_v, rules_v = params
            key = (trade_id, schema_v, calc_v, rules_v)
            with shared["lock"]:
                existing_id = shared["outbox_by_key"].get(key)
            return (existing_id,) if existing_id is not None else None

        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @contextmanager
    def transaction(self):
        yield self


def _new_shared(trades: list[dict]) -> dict:
    return {
        "trades": {t["id"]: dict(t) for t in trades},
        "exit_snapshots": [],
        "outbox_by_key": {},
        "outbox_rows": {},
        "next_outbox_id": 1,
        "lock": threading.Lock(),
    }


@pytest.mark.unit
class TestClosePaperTradeHappyPaths:
    def test_profitable_manual_close(self):
        shared = _new_shared([_new_trade()])
        conn = _FakeConn(shared)
        result = close_paper_trade(
            conn, user_id="user-aaa", trade_id=1, exit_price=120.0,
            exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
        )
        assert result.realized_pnl_abs == pytest.approx(200.0)
        assert result.proceeds == pytest.approx(1200.0)
        assert shared["trades"][1]["status"] == "CLOSED"
        assert len(shared["exit_snapshots"]) == 1
        assert result.outbox_created is True

    def test_losing_stop_loss_close(self):
        shared = _new_shared([_new_trade()])
        conn = _FakeConn(shared)
        result = close_paper_trade(
            conn, user_id="user-aaa", trade_id=1, exit_price=90.0,
            exit_mechanism=CloseExitMechanism.STOP_LOSS, exit_mechanism_raw="STOP_LOSS",
        )
        assert result.realized_pnl_abs == pytest.approx(-100.0)

    def test_breakeven_close(self):
        shared = _new_shared([_new_trade()])
        conn = _FakeConn(shared)
        result = close_paper_trade(
            conn, user_id="user-aaa", trade_id=1, exit_price=100.0,
            exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
        )
        assert result.realized_pnl_abs == pytest.approx(0.0)
        assert result.exit_snapshot.financial_outcome == "BREAKEVEN"

    def test_outbox_row_requests_current_versions(self):
        shared = _new_shared([_new_trade()])
        conn = _FakeConn(shared)
        close_paper_trade(
            conn, user_id="user-aaa", trade_id=1, exit_price=120.0,
            exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
        )
        key = next(iter(shared["outbox_by_key"]))
        assert key == (1, "1.0.0", CALCULATION_VERSION, ATTRIBUTION_RULES_VERSION)


@pytest.mark.unit
class TestClosePaperTradeIndeterminatePnl:
    """Stage 7 correction — a legacy/corrupt stored entry_price must
    produce a genuinely indeterminate P&L, never a fabricated number, a
    crash, or a silently-substituted zero misread as real breakeven."""

    @pytest.mark.parametrize("bad_entry_price", [None, float("nan"), float("inf"), float("-inf")])
    def test_non_finite_or_missing_entry_price_yields_none_pnl_not_a_crash(self, bad_entry_price):
        shared = _new_shared([_new_trade(entry_price=bad_entry_price)])
        conn = _FakeConn(shared)
        result = close_paper_trade(
            conn, user_id="user-aaa", trade_id=1, exit_price=120.0,
            exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
        )
        assert result.realized_pnl_abs is None
        assert result.realized_pnl_pct is None

    def test_non_finite_entry_price_exit_snapshot_is_indeterminate_never_breakeven(self):
        shared = _new_shared([_new_trade(entry_price=float("nan"))])
        conn = _FakeConn(shared)
        result = close_paper_trade(
            conn, user_id="user-aaa", trade_id=1, exit_price=120.0,
            exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
        )
        assert result.exit_snapshot.financial_outcome == "INDETERMINATE"

    def test_valid_entry_price_still_computes_a_real_percentage_not_none(self):
        shared = _new_shared([_new_trade(entry_price=100.0)])
        conn = _FakeConn(shared)
        result = close_paper_trade(
            conn, user_id="user-aaa", trade_id=1, exit_price=120.0,
            exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
        )
        assert result.realized_pnl_pct == pytest.approx(20.0)

    def test_non_positive_legacy_entry_price_still_computes_abs_pnl_but_none_pct(self):
        """entry_price <= 0 is arithmetically defined for the absolute
        P&L (not indeterminate — it's real, if extreme, legacy data), but
        compute_realized_pnl_pct's own existing guard already returns
        None for it (a percentage against a non-positive base is
        meaningless) — this close_service correction doesn't touch that
        guard, it only stops None/NaN/Infinity from reaching it at all."""
        shared = _new_shared([_new_trade(entry_price=0.0)])
        conn = _FakeConn(shared)
        result = close_paper_trade(
            conn, user_id="user-aaa", trade_id=1, exit_price=120.0,
            exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
        )
        assert result.realized_pnl_abs == pytest.approx(1200.0)
        assert result.realized_pnl_pct is None


@pytest.mark.unit
class TestClosePaperTradeFailureModes:
    def test_trade_not_found(self):
        shared = _new_shared([])
        with pytest.raises(TradeNotFoundError):
            close_paper_trade(
                _FakeConn(shared), user_id="user-aaa", trade_id=999, exit_price=100.0,
                exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
            )

    def test_cross_user_close_rejected(self):
        shared = _new_shared([_new_trade(user_id="owner")])
        with pytest.raises(TradeNotOwnedError):
            close_paper_trade(
                _FakeConn(shared), user_id="attacker", trade_id=1, exit_price=100.0,
                exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
            )
        assert shared["trades"][1]["status"] == "OPEN"  # never mutated

    def test_already_closed_trade_rejected(self):
        shared = _new_shared([_new_trade(status="CLOSED")])
        with pytest.raises(TradeAlreadyClosedError):
            close_paper_trade(
                _FakeConn(shared), user_id="user-aaa", trade_id=1, exit_price=100.0,
                exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
            )

    def test_unsupported_market_rejected(self):
        shared = _new_shared([_new_trade(market="XX")])
        with pytest.raises(UnsupportedMarketError):
            close_paper_trade(
                _FakeConn(shared), user_id="user-aaa", trade_id=1, exit_price=100.0,
                exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
            )

    @pytest.mark.parametrize("bad_price", [0, -10.0, float("nan"), float("inf")])
    def test_invalid_exit_price_rejected_before_any_row_lock(self, bad_price):
        shared = _new_shared([_new_trade()])
        with pytest.raises(CloseValidationError):
            close_paper_trade(
                _FakeConn(shared), user_id="user-aaa", trade_id=1, exit_price=bad_price,
                exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
            )
        assert shared["trades"][1]["status"] == "OPEN"

    def test_duplicate_close_second_call_rejected_no_duplicate_pnl(self):
        """Two sequential close attempts on the same trade: the first
        succeeds, the second must fail cleanly rather than double-crediting
        or producing a second exit snapshot/outbox row."""
        shared = _new_shared([_new_trade()])
        close_paper_trade(
            _FakeConn(shared), user_id="user-aaa", trade_id=1, exit_price=120.0,
            exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
        )
        with pytest.raises(TradeAlreadyClosedError):
            close_paper_trade(
                _FakeConn(shared), user_id="user-aaa", trade_id=1, exit_price=125.0,
                exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
            )
        assert len(shared["exit_snapshots"]) == 1
        assert len(shared["outbox_rows"]) == 1

    def test_concurrent_close_race_only_one_winner(self):
        """Two threads racing to close the identical trade, each with its
        own _FakeConn (own 'pooled connection') against the SAME shared
        state/lock. Exactly one must succeed; the other must see
        TradeAlreadyClosedError; no double-write of the exit snapshot or
        outbox row."""
        shared = _new_shared([_new_trade()])
        results = {"ok": 0, "already_closed": 0}
        results_lock = threading.Lock()

        def _attempt(price):
            conn = _FakeConn(shared)
            try:
                close_paper_trade(
                    conn, user_id="user-aaa", trade_id=1, exit_price=price,
                    exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
                )
                with results_lock:
                    results["ok"] += 1
            except TradeAlreadyClosedError:
                with results_lock:
                    results["already_closed"] += 1

        threads = [threading.Thread(target=_attempt, args=(price,)) for price in (110.0, 115.0)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results["ok"] == 1
        assert results["already_closed"] == 1
        assert len(shared["exit_snapshots"]) == 1
        assert len(shared["outbox_rows"]) == 1


def _sample_snapshot(**overrides):
    kwargs = dict(
        paper_trade_id=1, user_id="user-aaa", symbol="AAPL", market="US",
        exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
        exit_price=120.0, exit_quantity=10,
        closed_at=dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),
        realized_pnl_abs=200.0, management_mode="manual",
    )
    kwargs.update(overrides)
    return _build_exit_snapshot_obj(**kwargs)


@pytest.mark.unit
class TestExitSnapshotInsertColumnValueParity:
    """Locks in the PR #33 real-PostgreSQL CI failure fix: the exit-
    snapshot INSERT's column list, value tuple, and placeholder count
    must always agree — a real server rejects a bind-parameter-count
    mismatch that no mocked-connection test previously caught."""

    def test_column_count_equals_value_count_for_null_optional_fields(self):
        snapshot = _sample_snapshot()  # every Optional[...] field left at its default (None)
        sql, values = _build_exit_snapshot_insert(snapshot)
        assert len(values) == len(_EXIT_SNAPSHOT_COLUMNS)

    def test_column_count_equals_value_count_with_every_optional_field_populated(self):
        snapshot = _sample_snapshot(
            final_stop_loss=90.0, final_target_price=130.0, trailing_stop_level=95.0,
            time_exit_rule="EOD", market_close_rule="16:00",
            levels_modified_after_entry=True,
            source_request_id="req-123", trigger_observation_timestamp=dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),
            trigger_observation_price=119.5, source_metadata={"note": "auto-detected"},
        )
        sql, values = _build_exit_snapshot_insert(snapshot)
        assert len(values) == len(_EXIT_SNAPSHOT_COLUMNS)

    def test_placeholder_count_equals_value_count(self):
        snapshot = _sample_snapshot()
        sql, values = _build_exit_snapshot_insert(snapshot)
        assert sql.count("%s") == len(values)

    def test_placeholder_count_equals_column_count(self):
        snapshot = _sample_snapshot()
        sql, values = _build_exit_snapshot_insert(snapshot)
        assert sql.count("%s") == len(_EXIT_SNAPSHOT_COLUMNS)

    def test_every_declared_column_name_appears_in_the_generated_sql(self):
        snapshot = _sample_snapshot()
        sql, _ = _build_exit_snapshot_insert(snapshot)
        for column in _EXIT_SNAPSHOT_COLUMNS:
            assert column in sql

    def test_source_metadata_none_serializes_to_none_not_json_null_string(self):
        snapshot = _sample_snapshot(source_metadata=None)
        _, values = _build_exit_snapshot_insert(snapshot)
        assert values[_EXIT_SNAPSHOT_COLUMNS.index("source_metadata")] is None

    def test_source_metadata_populated_serializes_to_json_string(self):
        snapshot = _sample_snapshot(source_metadata={"detected_by": "browser_useeffect"})
        _, values = _build_exit_snapshot_insert(snapshot)
        raw = values[_EXIT_SNAPSHOT_COLUMNS.index("source_metadata")]
        assert isinstance(raw, str)
        import json
        assert json.loads(raw) == {"detected_by": "browser_useeffect"}

    def test_values_are_positionally_ordered_to_match_columns(self):
        """paper_trade_id must be the first value (matching the first
        column), user_id the second, and so on — asserting a full
        positional round-trip against ExitSnapshot's own field access,
        not just a count."""
        snapshot = _sample_snapshot()
        _, values = _build_exit_snapshot_insert(snapshot)
        by_column = dict(zip(_EXIT_SNAPSHOT_COLUMNS, values))
        assert by_column["paper_trade_id"] == snapshot.paper_trade_id
        assert by_column["user_id"] == snapshot.user_id
        assert by_column["exit_price"] == snapshot.exit_price
        assert by_column["exit_quantity"] == snapshot.exit_quantity
        assert by_column["financial_outcome"] == snapshot.financial_outcome
        assert by_column["closure_classification"] == snapshot.closure_classification
        assert by_column["trigger_timing_verification"] == snapshot.trigger_timing_verification
