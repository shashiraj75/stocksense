"""
Trade Postmortem Engine, Sprint 2 — real PostgreSQL verification for the
durability layer: paper_trade_exit_snapshot, paper_trade_postmortem_outbox,
paper_trade_postmortem_report. Goes through the actual HTTP router
(`api.routers.paper_trading`) end-to-end — `_conn` is never mocked
anywhere in this directory, exactly like every other postgres_integration
test file.

No local PostgreSQL is available in this development environment (no
docker/psql/pg_ctl/initdb, nothing on :5432 at authoring time) — this
suite is only actually executed by the GitHub Actions PostgreSQL 15/17 CI
matrix, never locally. It is written and reviewed here in good faith
against the real schema in services/postgres_store.py, but its first real
execution against a live server happens in CI once this branch is pushed.
"""
import threading

import psycopg
import pytest

from tests.postgres_integration.conftest import ensure_portfolio, get_portfolio_cash, make_auth_header

pytestmark = pytest.mark.postgres_integration


def _buy(client, user_id, **overrides):
    body = {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0}
    body.update(overrides)
    return client.post("/api/paper-trading/buy", json=body, headers=make_auth_header(user_id))


def _sell(client, user_id, trade_id, **overrides):
    body = {"price": 120.0, "exit_reason": "MANUAL"}
    body.update(overrides)
    return client.post(f"/api/paper-trading/sell/{trade_id}", json=body, headers=make_auth_header(user_id))


def _open_and_close(client, pg_conn, user_id, exit_price=120.0):
    ensure_portfolio(pg_conn, user_id, cash_usd=1_000_000.0)
    trade_id = _buy(client, user_id).json()["trade_id"]
    resp = _sell(client, user_id, trade_id, price=exit_price)
    assert resp.status_code == 200
    return trade_id


@pytest.mark.timeout(30)
class TestExitSnapshotWrittenAtomicallyWithClose:
    def test_close_writes_exactly_one_exit_snapshot_and_outbox_row(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)

        snapshots = pg_conn.execute(
            "SELECT paper_trade_id, financial_outcome, closure_classification, exit_mechanism "
            "FROM paper_trade_exit_snapshot WHERE user_id = %s",
            (unique_user_id,)
        ).fetchall()
        assert len(snapshots) == 1
        assert snapshots[0] == (trade_id, "WIN", "TRADING_EXIT", "MANUAL")

        outbox_rows = pg_conn.execute(
            "SELECT paper_trade_id, status FROM paper_trade_postmortem_outbox WHERE user_id = %s",
            (unique_user_id,)
        ).fetchall()
        assert len(outbox_rows) == 1
        assert outbox_rows[0][0] == trade_id
        assert outbox_rows[0][1] in ("PENDING", "GENERATING", "COMPLETE", "LIMITED_EVIDENCE", "FAILED_RETRYABLE")

    def test_trade_and_snapshot_close_atomically_survive_together(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        trade_status = pg_conn.execute(
            "SELECT status FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()[0]
        snapshot_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_exit_snapshot WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert trade_status == "CLOSED"
        assert snapshot_count == 1


@pytest.mark.timeout(30)
class TestExitSnapshotImmutability:
    def test_update_after_insert_is_rejected_by_trigger(self, client, pg_conn, unique_user_id):
        _open_and_close(client, pg_conn, unique_user_id)
        with pytest.raises(psycopg.errors.RaiseException):
            pg_conn.execute(
                "UPDATE paper_trade_exit_snapshot SET exit_price = 999.0 WHERE user_id = %s",
                (unique_user_id,)
            )

    def test_check_constraint_rejects_non_positive_exit_price(self, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        with pytest.raises(psycopg.errors.CheckViolation):
            pg_conn.execute(
                """INSERT INTO paper_trade_exit_snapshot (
                    paper_trade_id, user_id, symbol, market, exit_snapshot_schema_version,
                    financial_outcome, closure_classification, exit_mechanism, exit_mechanism_raw,
                    exit_price, exit_quantity, closed_at, management_mode
                ) VALUES (999999, %s, 'AAPL', 'US', '1.0.0', 'WIN', 'TRADING_EXIT', 'MANUAL', 'MANUAL',
                          0, 1, now(), 'manual')""",
                (unique_user_id,)
            )


@pytest.mark.timeout(30)
class TestDuplicateCloseRejectedAgainstRealPG:
    def test_second_sell_rejected_no_second_snapshot_or_outbox_row(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        second = _sell(client, unique_user_id, trade_id, price=125.0)
        assert second.status_code == 400

        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trade_exit_snapshot WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0] == 1
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_outbox WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0] == 1


@pytest.mark.timeout(30)
class TestConcurrentCloseRaceAgainstRealPG:
    def test_concurrent_sells_only_one_winner(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id, cash_usd=1_000_000.0)
        trade_id = _buy(client, unique_user_id).json()["trade_id"]

        results = []
        results_lock = threading.Lock()

        def _attempt(price):
            resp = _sell(client, unique_user_id, trade_id, price=price)
            with results_lock:
                results.append(resp.status_code)

        threads = [threading.Thread(target=_attempt, args=(p,)) for p in (110.0, 115.0, 120.0)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count(200) == 1
        assert results.count(400) == 2
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trade_exit_snapshot WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0] == 1
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_outbox WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0] == 1


@pytest.mark.timeout(30)
class TestReportGenerationAgainstRealPG:
    def test_generate_endpoint_persists_a_report(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        resp = client.post(
            f"/api/paper-trading/postmortem/{trade_id}/generate", headers=make_auth_header(unique_user_id)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["generated"] is True

        rows = pg_conn.execute(
            "SELECT paper_trade_id, user_id, status FROM paper_trade_postmortem_report WHERE paper_trade_id = %s",
            (trade_id,)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0] == (trade_id, unique_user_id, body["status"])

    def test_repeated_generate_calls_are_idempotent_no_duplicate_row(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        first = client.post(
            f"/api/paper-trading/postmortem/{trade_id}/generate", headers=make_auth_header(unique_user_id)
        ).json()
        second = client.post(
            f"/api/paper-trading/postmortem/{trade_id}/generate", headers=make_auth_header(unique_user_id)
        ).json()
        assert first["generated"] is True
        assert second["generated"] is False
        count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert count == 1

    def test_report_immutability_trigger_not_required_but_update_via_persist_never_happens(self, client, pg_conn, unique_user_id):
        """persist_report itself never issues an UPDATE — proven at the
        application level by re-generating and confirming the row's
        `generated_at`/id are unchanged (report_store has no code path
        that updates an existing row)."""
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        client.post(f"/api/paper-trading/postmortem/{trade_id}/generate", headers=make_auth_header(unique_user_id))
        row_before = pg_conn.execute(
            "SELECT id, generated_at FROM paper_trade_postmortem_report WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()
        client.post(f"/api/paper-trading/postmortem/{trade_id}/generate", headers=make_auth_header(unique_user_id))
        row_after = pg_conn.execute(
            "SELECT id, generated_at FROM paper_trade_postmortem_report WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()
        assert row_before == row_after


@pytest.mark.timeout(30)
class TestResetLeavesNoOrphanRows:
    def test_full_reset_removes_exit_snapshot_outbox_and_report_rows(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        client.post(f"/api/paper-trading/postmortem/{trade_id}/generate", headers=make_auth_header(unique_user_id))

        resp = client.post("/api/paper-trading/reset?market=ALL", headers=make_auth_header(unique_user_id))
        assert resp.status_code == 200

        for table in ("paper_trade_exit_snapshot", "paper_trade_postmortem_outbox", "paper_trade_postmortem_report"):
            count = pg_conn.execute(f"SELECT count(*) FROM {table} WHERE user_id = %s", (unique_user_id,)).fetchone()[0]
            assert count == 0, f"{table} left an orphan row after reset"

    def test_market_specific_reset_removes_only_that_markets_rows(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id, cash=1_000_000.0, cash_usd=1_000_000.0)
        us_trade_id = _buy(client, unique_user_id, market="US").json()["trade_id"]
        _sell(client, unique_user_id, us_trade_id, price=110.0)

        in_trade_id = _buy(client, unique_user_id, market="IN", symbol="TCS").json()["trade_id"]
        _sell(client, unique_user_id, in_trade_id, price=110.0)

        resp = client.post("/api/paper-trading/reset?market=US", headers=make_auth_header(unique_user_id))
        assert resp.status_code == 200

        us_remaining = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_exit_snapshot WHERE user_id = %s AND market = 'US'",
            (unique_user_id,)
        ).fetchone()[0]
        in_remaining = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_exit_snapshot WHERE user_id = %s AND market = 'IN'",
            (unique_user_id,)
        ).fetchone()[0]
        assert us_remaining == 0
        assert in_remaining == 1

        us_outbox_remaining = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_outbox WHERE user_id = %s AND paper_trade_id = %s",
            (unique_user_id, us_trade_id)
        ).fetchone()[0]
        in_outbox_remaining = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_outbox WHERE user_id = %s AND paper_trade_id = %s",
            (unique_user_id, in_trade_id)
        ).fetchone()[0]
        assert us_outbox_remaining == 0
        assert in_outbox_remaining == 1
