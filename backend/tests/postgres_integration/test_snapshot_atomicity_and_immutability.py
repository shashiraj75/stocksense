"""
Real PostgreSQL Verification, CI Execution phase — Parts 6 & 7.

Real Buy transaction atomicity (success, snapshot-insert failure, trade-
insert failure) and real entry-snapshot immutability, against a real
disposable PostgreSQL instance. Goes through the actual HTTP router
(`api.routers.paper_trading`) — no mocked connection anywhere.
"""
from unittest.mock import patch

import psycopg
import pytest

from tests.postgres_integration.conftest import ensure_portfolio, get_portfolio_cash, make_auth_header

pytestmark = pytest.mark.postgres_integration


def _buy(client, user_id, **overrides):
    body = {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0}
    body.update(overrides)
    return client.post("/api/paper-trading/buy", json=body, headers=make_auth_header(user_id))


@pytest.mark.timeout(30)
class TestSuccessfulBuyAtomicity:
    def test_cash_trade_and_snapshot_all_created_exactly_once(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id, cash_usd=1_000_000.0)

        resp = _buy(client, unique_user_id)
        assert resp.status_code == 200
        body = resp.json()

        assert get_portfolio_cash(pg_conn, unique_user_id, "US") == pytest.approx(1_000_000.0 - 101.0)

        trades = pg_conn.execute(
            "SELECT id, symbol, market, quantity, entry_price FROM paper_trades WHERE user_id = %s",
            (unique_user_id,)
        ).fetchall()
        assert len(trades) == 1
        trade_id, symbol, market, quantity, entry_price = trades[0]
        assert (symbol, market, quantity, entry_price) == ("AAPL", "US", 1, 101.0)
        assert trade_id == body["trade_id"]

        snapshots = pg_conn.execute(
            "SELECT paper_trade_id, user_id, symbol, market, verification_levels FROM paper_trade_entry_snapshot WHERE user_id = %s",
            (unique_user_id,)
        ).fetchall()
        assert len(snapshots) == 1
        snap_trade_id, snap_user_id, snap_symbol, snap_market, verification_levels = snapshots[0]
        assert snap_trade_id == trade_id
        assert (snap_user_id, snap_symbol, snap_market) == (unique_user_id, "AAPL", "US")
        assert isinstance(verification_levels, dict)
        assert body["evidence_completeness"] == "LIMITED"


@pytest.mark.timeout(30)
class TestSnapshotInsertFailureRollsBack:
    def test_snapshot_not_null_violation_rolls_back_trade_and_cash(self, client, pg_conn, unique_user_id):
        """A real PostgreSQL NOT NULL violation on simulated_execution_price
        — induced by patching the snapshot-construction step (not the DB
        layer) so the actual bound SQL sent to real Postgres is what fails.
        Proves the whole transaction (debit + trade INSERT + snapshot
        INSERT) rolls back together."""
        ensure_portfolio(pg_conn, unique_user_id, cash_usd=1_000_000.0)

        with patch("api.routers.paper_trading._build_snapshot_for_buy") as mock_build:
            from services.postmortem.entry_snapshot import EntrySnapshot

            def _broken_snapshot(*, trade_id, user_id, symbol, market, req):
                return EntrySnapshot(
                    paper_trade_id=trade_id, user_id=user_id, symbol=symbol, market=market,
                    snapshot_schema_version="1.0.0", evidence_source="MANUAL",
                    daily_pick_run_id=None, daily_pick_rank=None, recommendation_signal=None,
                    recommendation_generated_at=None, recommendation_reference_price=None,
                    recommendation_entry_low=None, recommendation_entry_high=None,
                    simulated_execution_price=None,  # NOT NULL column — real constraint violation
                    execution_slippage_pct=None, execution_range_position="UNKNOWN",
                    recommended_stop_loss=None, recommended_target_price=None,
                    user_selected_stop_loss=None, user_selected_target_price=None,
                    user_overrode_recommendation=None, reward_to_risk_ratio=None,
                    confidence_score=None, technical_signal=None, technical_rsi=None,
                    technical_macd_diff=None, fundamental_score=None, sentiment_score=None,
                    sentiment_label=None, market_regime_trend=None, market_regime_score_adj=None,
                    market_regime_reason=None, recommendation_reasoning=None, model_version=None,
                    verification_levels={},
                )
            mock_build.side_effect = _broken_snapshot

            with pytest.raises(psycopg.errors.NotNullViolation):
                _buy(client, unique_user_id)

        assert get_portfolio_cash(pg_conn, unique_user_id, "US") == pytest.approx(1_000_000.0)
        assert pg_conn.execute("SELECT count(*) FROM paper_trades WHERE user_id = %s", (unique_user_id,)).fetchone()[0] == 0
        assert pg_conn.execute("SELECT count(*) FROM paper_trade_entry_snapshot WHERE user_id = %s", (unique_user_id,)).fetchone()[0] == 0

    def test_snapshot_failure_with_idempotency_key_marks_failed_not_completed(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id, cash_usd=1_000_000.0)
        key = "aaaaaaaa-0000-0000-0000-000000000001"

        with patch("api.routers.paper_trading._build_snapshot_for_buy") as mock_build:
            from services.postmortem.entry_snapshot import EntrySnapshot
            mock_build.side_effect = lambda **kw: EntrySnapshot(
                paper_trade_id=kw["trade_id"], user_id=kw["user_id"], symbol=kw["symbol"], market=kw["market"],
                snapshot_schema_version="1.0.0", evidence_source="MANUAL", daily_pick_run_id=None,
                daily_pick_rank=None, recommendation_signal=None, recommendation_generated_at=None,
                recommendation_reference_price=None, recommendation_entry_low=None, recommendation_entry_high=None,
                simulated_execution_price=None, execution_slippage_pct=None, execution_range_position="UNKNOWN",
                recommended_stop_loss=None, recommended_target_price=None, user_selected_stop_loss=None,
                user_selected_target_price=None, user_overrode_recommendation=None, reward_to_risk_ratio=None,
                confidence_score=None, technical_signal=None, technical_rsi=None, technical_macd_diff=None,
                fundamental_score=None, sentiment_score=None, sentiment_label=None, market_regime_trend=None,
                market_regime_score_adj=None, market_regime_reason=None, recommendation_reasoning=None,
                model_version=None, verification_levels={},
                level_history_contract_version=None, initial_stop_modified_after_entry=None,
                initial_target_modified_after_entry=None, initial_levels_modified_after_entry=None,
            )
            with pytest.raises(psycopg.errors.NotNullViolation):
                _buy(client, unique_user_id, idempotency_key=key)

        row = pg_conn.execute(
            "SELECT status FROM paper_trade_idempotency_key WHERE user_id = %s AND idempotency_key = %s",
            (unique_user_id, key)
        ).fetchone()
        assert row is not None
        assert row[0] == "FAILED"


@pytest.mark.timeout(30)
class TestTradeInsertFailureRollsBack:
    def test_early_failure_before_trade_insert_leaves_no_effect(self, client, pg_conn, unique_user_id):
        """Forces the FIRST statement in the transaction (the cash debit)
        to hit a genuine PostgreSQL error — an undefined column, via a
        temporarily-corrupted cash-column mapping — proving nothing
        downstream (trade, snapshot) is ever created when the earliest
        possible failure point fires."""
        ensure_portfolio(pg_conn, unique_user_id, cash_usd=1_000_000.0)

        with patch.dict("api.routers.paper_trading._CASH_COL", {"US": "not_a_real_column"}):
            with pytest.raises(psycopg.errors.UndefinedColumn):
                _buy(client, unique_user_id)

        assert get_portfolio_cash(pg_conn, unique_user_id, "US") == pytest.approx(1_000_000.0)
        assert pg_conn.execute("SELECT count(*) FROM paper_trades WHERE user_id = %s", (unique_user_id,)).fetchone()[0] == 0
        assert pg_conn.execute("SELECT count(*) FROM paper_trade_entry_snapshot WHERE user_id = %s", (unique_user_id,)).fetchone()[0] == 0


@pytest.mark.timeout(30)
class TestSnapshotImmutability:
    def test_direct_update_is_rejected_by_postgres(self, client, pg_conn, unique_user_id):
        resp = _buy(client, unique_user_id)
        trade_id = resp.json()["trade_id"]

        with pytest.raises(psycopg.errors.RaiseException) as exc_info:
            with pg_conn.transaction():
                pg_conn.execute(
                    "UPDATE paper_trade_entry_snapshot SET symbol = 'MUTATED' WHERE paper_trade_id = %s",
                    (trade_id,)
                )
        # Sanitized, safe-to-log evidence: the SQLSTATE and the exception's
        # own message (no row data, no connection string).
        assert exc_info.value.sqlstate is not None
        assert "immutable" in str(exc_info.value).lower()

        row = pg_conn.execute(
            "SELECT symbol FROM paper_trade_entry_snapshot WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()
        assert row[0] == "AAPL"  # unchanged

    def test_insert_still_works(self, client, pg_conn, unique_user_id):
        resp = _buy(client, unique_user_id)
        assert resp.status_code == 200
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trade_entry_snapshot WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0] == 1

    def test_select_still_works(self, client, pg_conn, unique_user_id):
        resp = _buy(client, unique_user_id)
        trade_id = resp.json()["trade_id"]
        row = pg_conn.execute(
            "SELECT paper_trade_id FROM paper_trade_entry_snapshot WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()
        assert row is not None

    def test_reset_delete_still_works_despite_trigger(self, client, pg_conn, unique_user_id):
        resp = _buy(client, unique_user_id)
        assert resp.status_code == 200
        del_resp = client.post("/api/paper-trading/reset?market=ALL", headers=make_auth_header(unique_user_id))
        assert del_resp.status_code == 200
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trade_entry_snapshot WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0] == 0

    def test_repeated_initialization_does_not_disable_the_trigger(self, client, pg_conn, unique_user_id):
        from services import postgres_store
        postgres_store.init_db()
        postgres_store.init_db()

        resp = _buy(client, unique_user_id)
        trade_id = resp.json()["trade_id"]
        with pytest.raises(psycopg.errors.RaiseException):
            with pg_conn.transaction():
                pg_conn.execute(
                    "UPDATE paper_trade_entry_snapshot SET symbol = 'MUTATED' WHERE paper_trade_id = %s",
                    (trade_id,)
                )
