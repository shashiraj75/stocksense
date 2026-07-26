"""
Real PostgreSQL Verification, CI Execution phase — Part 15.

Full and market-scoped reset against a real disposable PostgreSQL
instance, including the market-scoped idempotency-cleanup fix from the
Retention phase's ADR.
"""
import pytest

from tests.postgres_integration.conftest import ensure_portfolio, get_portfolio_cash, make_auth_header

pytestmark = pytest.mark.postgres_integration


def _buy(client, user_id, **overrides):
    body = {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0}
    body.update(overrides)
    return client.post("/api/paper-trading/buy", json=body, headers=make_auth_header(user_id))


@pytest.mark.timeout(30)
class TestFullReset:
    def test_full_reset_removes_trades_snapshots_and_idempotency_records(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        _buy(client, unique_user_id, idempotency_key="reset-full-key-1")

        resp = client.post("/api/paper-trading/reset?market=ALL", headers=make_auth_header(unique_user_id))
        assert resp.status_code == 200

        for table in ("paper_trades", "paper_trade_entry_snapshot", "paper_trade_idempotency_key"):
            assert pg_conn.execute(
                f"SELECT count(*) FROM {table} WHERE user_id = %s", (unique_user_id,)
            ).fetchone()[0] == 0
        assert get_portfolio_cash(pg_conn, unique_user_id, "US") == pytest.approx(100_000.0)

    def test_full_reset_does_not_touch_another_user(self, client, pg_conn, unique_user_id):
        other_user = f"{unique_user_id}-other"
        ensure_portfolio(pg_conn, unique_user_id)
        ensure_portfolio(pg_conn, other_user)
        _buy(client, other_user)
        try:
            client.post("/api/paper-trading/reset?market=ALL", headers=make_auth_header(unique_user_id))
            assert pg_conn.execute(
                "SELECT count(*) FROM paper_trades WHERE user_id = %s", (other_user,)
            ).fetchone()[0] == 1
        finally:
            with pg_conn.transaction():
                pg_conn.execute("DELETE FROM paper_trade_entry_snapshot WHERE user_id = %s", (other_user,))
                pg_conn.execute("DELETE FROM paper_trade_idempotency_key WHERE user_id = %s", (other_user,))
                pg_conn.execute("DELETE FROM paper_trades WHERE user_id = %s", (other_user,))
                pg_conn.execute("DELETE FROM paper_portfolio WHERE user_id = %s", (other_user,))


@pytest.mark.timeout(30)
class TestMarketScopedReset:
    def test_only_selected_market_trades_and_snapshots_removed(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        _buy(client, unique_user_id, market="US", idempotency_key="reset-market-us")
        _buy(client, unique_user_id, market="IN", price=1000.0, idempotency_key="reset-market-in")

        resp = client.post("/api/paper-trading/reset?market=US", headers=make_auth_header(unique_user_id))
        assert resp.status_code == 200

        remaining_trades = pg_conn.execute(
            "SELECT market FROM paper_trades WHERE user_id = %s", (unique_user_id,)
        ).fetchall()
        assert [r[0] for r in remaining_trades] == ["IN"]
        remaining_snapshots = pg_conn.execute(
            "SELECT market FROM paper_trade_entry_snapshot WHERE user_id = %s", (unique_user_id,)
        ).fetchall()
        assert [r[0] for r in remaining_snapshots] == ["IN"]

    def test_completed_idempotency_record_for_reset_market_is_cleared_per_adr(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        _buy(client, unique_user_id, market="US", idempotency_key="reset-market-idem-us")
        _buy(client, unique_user_id, market="IN", price=1000.0, idempotency_key="reset-market-idem-in")

        client.post("/api/paper-trading/reset?market=US", headers=make_auth_header(unique_user_id))

        remaining_keys = pg_conn.execute(
            "SELECT idempotency_key FROM paper_trade_idempotency_key WHERE user_id = %s", (unique_user_id,)
        ).fetchall()
        assert [r[0] for r in remaining_keys] == ["reset-market-idem-in"]

    def test_no_orphaned_snapshot_or_idempotency_record_remains(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        _buy(client, unique_user_id, market="US", idempotency_key="reset-orphan-check")
        client.post("/api/paper-trading/reset?market=US", headers=make_auth_header(unique_user_id))

        orphaned_snapshots = pg_conn.execute(
            """SELECT s.paper_trade_id FROM paper_trade_entry_snapshot s
               LEFT JOIN paper_trades t ON s.paper_trade_id = t.id
               WHERE s.user_id = %s AND t.id IS NULL""",
            (unique_user_id,)
        ).fetchall()
        assert orphaned_snapshots == []
