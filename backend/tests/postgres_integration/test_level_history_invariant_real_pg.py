"""
Trade Postmortem Sprint 3A, Stage J4D — real PostgreSQL verification of
the durable level-history write invariant (Gate 3C batches E, F, G, H,
I, J). Never mocks _conn — every assertion here is against a real
disposable PostgreSQL instance's trigger/constraint/transaction
behavior, which a hand-written fake connection cannot prove.
"""
import threading
import uuid

import pytest

from tests.postgres_integration.conftest import ensure_portfolio, make_auth_header

pytestmark = pytest.mark.postgres_integration


def _insert_governed_trade(pg_conn, user_id, **overrides):
    """Direct-SQL setup helper mirroring exactly what buy_trade's own
    INSERT now writes for a governed trade — used so trigger-focused
    tests don't need to go through the full HTTP /buy path."""
    values = dict(
        session_id=user_id, user_id=user_id, symbol="AAPL", market="US", quantity=1,
        entry_price=100.0, stop_loss=90.0, target_price=120.0,
        trade_management_mode="manual",
        level_history_contract_version="1",
        stop_modified_after_entry=False, target_modified_after_entry=False,
        levels_modified_after_entry=False,
    )
    values.update(overrides)
    row = pg_conn.execute(
        """INSERT INTO paper_trades
           (session_id, user_id, symbol, market, quantity, entry_price, stop_loss, target_price,
            trade_management_mode, level_history_contract_version, stop_modified_after_entry,
            target_modified_after_entry, levels_modified_after_entry)
           VALUES (%(session_id)s, %(user_id)s, %(symbol)s, %(market)s, %(quantity)s, %(entry_price)s,
                   %(stop_loss)s, %(target_price)s, %(trade_management_mode)s,
                   %(level_history_contract_version)s, %(stop_modified_after_entry)s,
                   %(target_modified_after_entry)s, %(levels_modified_after_entry)s)
           RETURNING id""",
        values,
    ).fetchone()
    return row[0]


def _insert_legacy_trade(pg_conn, user_id, **overrides):
    return _insert_governed_trade(
        pg_conn, user_id,
        level_history_contract_version=None, stop_modified_after_entry=None,
        target_modified_after_entry=None, levels_modified_after_entry=None,
        **overrides,
    )


def _fetch_flags(pg_conn, trade_id):
    row = pg_conn.execute(
        "SELECT level_history_contract_version, stop_modified_after_entry, "
        "target_modified_after_entry, levels_modified_after_entry, stop_loss, target_price "
        "FROM paper_trades WHERE id = %s",
        (trade_id,),
    ).fetchone()
    return dict(
        version=row[0], stop_modified=row[1], target_modified=row[2],
        aggregate=row[3], stop_loss=row[4], target_price=row[5],
    )


class TestSchemaIdempotency:
    def test_schema_initialization_idempotent(self, pg_conn, initialized_schema):
        # initialized_schema already ran the real init_db() once at session
        # scope; calling it again here proves every ALTER/DO block/CREATE
        # OR REPLACE FUNCTION/CREATE TRIGGER this Wave added is safe to run
        # again against an already-migrated database.
        from services import postgres_store
        postgres_store.init_db()
        # No exception — schema application is idempotent by construction.


class TestNewTradeInitialization:
    def test_new_governed_trade_initializes_quadruple(self, pg_conn, unique_user_id):
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        flags = _fetch_flags(pg_conn, trade_id)
        assert flags["version"] == "1"
        assert flags["stop_modified"] is False
        assert flags["target_modified"] is False
        assert flags["aggregate"] is False

    def test_legacy_fixture_remains_all_null(self, pg_conn, unique_user_id):
        trade_id = _insert_legacy_trade(pg_conn, unique_user_id)
        flags = _fetch_flags(pg_conn, trade_id)
        assert flags["version"] is None
        assert flags["stop_modified"] is None
        assert flags["target_modified"] is None
        assert flags["aggregate"] is None


class TestDatabaseEnforcement:
    def test_version_immutable_once_set(self, pg_conn, unique_user_id):
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        with pytest.raises(Exception, match="level_history_contract_version is immutable"):
            pg_conn.execute(
                "UPDATE paper_trades SET level_history_contract_version = NULL WHERE id = %s", (trade_id,)
            )

    def test_unsupported_invariant_version_rejected_on_insert(self, pg_conn, unique_user_id):
        with pytest.raises(Exception):
            _insert_governed_trade(pg_conn, unique_user_id, level_history_contract_version="999")

    def test_stop_true_cannot_revert(self, pg_conn, unique_user_id):
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        pg_conn.execute(
            "UPDATE paper_trades SET stop_loss = 95.0, stop_modified_after_entry = TRUE, "
            "levels_modified_after_entry = TRUE WHERE id = %s", (trade_id,)
        )
        with pytest.raises(Exception, match="stop_modified_after_entry cannot revert"):
            pg_conn.execute(
                "UPDATE paper_trades SET stop_modified_after_entry = FALSE WHERE id = %s", (trade_id,)
            )

    def test_target_true_cannot_revert(self, pg_conn, unique_user_id):
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        pg_conn.execute(
            "UPDATE paper_trades SET target_price = 150.0, target_modified_after_entry = TRUE, "
            "levels_modified_after_entry = TRUE WHERE id = %s", (trade_id,)
        )
        with pytest.raises(Exception, match="target_modified_after_entry cannot revert"):
            pg_conn.execute(
                "UPDATE paper_trades SET target_modified_after_entry = FALSE WHERE id = %s", (trade_id,)
            )

    def test_governed_stop_change_without_matching_flag_rejected(self, pg_conn, unique_user_id):
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        with pytest.raises(Exception, match="governed stop_loss change requires"):
            pg_conn.execute("UPDATE paper_trades SET stop_loss = 95.0 WHERE id = %s", (trade_id,))

    def test_governed_target_change_without_matching_flag_rejected(self, pg_conn, unique_user_id):
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        with pytest.raises(Exception, match="governed target_price change requires"):
            pg_conn.execute("UPDATE paper_trades SET target_price = 150.0 WHERE id = %s", (trade_id,))

    def test_per_level_flag_true_without_aggregate_true_rejected(self, pg_conn, unique_user_id):
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        with pytest.raises(Exception, match="levels_modified_after_entry must be TRUE"):
            pg_conn.execute(
                "UPDATE paper_trades SET stop_loss = 95.0, stop_modified_after_entry = TRUE WHERE id = %s",
                (trade_id,)
            )

    def test_correctly_shaped_governed_change_succeeds(self, pg_conn, unique_user_id):
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        pg_conn.execute(
            "UPDATE paper_trades SET stop_loss = 95.0, stop_modified_after_entry = TRUE, "
            "levels_modified_after_entry = TRUE WHERE id = %s", (trade_id,)
        )
        flags = _fetch_flags(pg_conn, trade_id)
        assert flags["stop_loss"] == 95.0
        assert flags["stop_modified"] is True
        assert flags["aggregate"] is True

    def test_unrelated_update_never_misclassified_as_level_modification(self, pg_conn, unique_user_id):
        """trade_notifier.py-style UPDATE that never touches stop_loss/
        target_price must pass through the trigger untouched."""
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        pg_conn.execute("UPDATE paper_trades SET stop_notified_at = now() WHERE id = %s", (trade_id,))
        flags = _fetch_flags(pg_conn, trade_id)
        assert flags["stop_modified"] is False
        assert flags["aggregate"] is False

    def test_legacy_row_stop_edit_may_set_true_without_version(self, pg_conn, unique_user_id):
        trade_id = _insert_legacy_trade(pg_conn, unique_user_id)
        pg_conn.execute(
            "UPDATE paper_trades SET stop_loss = 95.0, stop_modified_after_entry = TRUE, "
            "levels_modified_after_entry = TRUE WHERE id = %s", (trade_id,)
        )
        flags = _fetch_flags(pg_conn, trade_id)
        assert flags["version"] is None
        assert flags["stop_modified"] is True
        assert flags["aggregate"] is True

    def test_legacy_row_cannot_silently_acquire_a_version_via_edit(self, pg_conn, unique_user_id):
        trade_id = _insert_legacy_trade(pg_conn, unique_user_id)
        with pytest.raises(Exception, match="level_history_contract_version is immutable"):
            pg_conn.execute(
                "UPDATE paper_trades SET level_history_contract_version = '1' WHERE id = %s", (trade_id,)
            )


class TestConcurrency:
    @pytest.mark.timeout(30)
    def test_concurrent_stop_and_target_edits_via_endpoint_preserve_both_true(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        results = []
        lock = threading.Lock()

        def _edit_stop():
            resp = client.patch(
                f"/api/paper-trading/trade/{trade_id}", json={"stop_loss": 85.0},
                headers=make_auth_header(unique_user_id),
            )
            with lock:
                results.append(("stop", resp.status_code))

        def _edit_target():
            resp = client.patch(
                f"/api/paper-trading/trade/{trade_id}", json={"target_price": 160.0},
                headers=make_auth_header(unique_user_id),
            )
            with lock:
                results.append(("target", resp.status_code))

        threads = [threading.Thread(target=_edit_stop), threading.Thread(target=_edit_target)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

        assert len(results) == 2
        assert all(code == 200 for _, code in results)
        flags = _fetch_flags(pg_conn, trade_id)
        assert flags["stop_modified"] is True
        assert flags["target_modified"] is True
        assert flags["aggregate"] is True

    @pytest.mark.timeout(30)
    def test_concurrent_identical_stop_edits_cannot_lose_true(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        N = 5
        results = []
        lock = threading.Lock()

        def _edit():
            resp = client.patch(
                f"/api/paper-trading/trade/{trade_id}", json={"stop_loss": 85.0},
                headers=make_auth_header(unique_user_id),
            )
            with lock:
                results.append(resp.status_code)

        threads = [threading.Thread(target=_edit) for _ in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

        assert len(results) == N
        assert all(code == 200 for code in results)
        flags = _fetch_flags(pg_conn, trade_id)
        assert flags["stop_modified"] is True
        assert flags["stop_loss"] == 85.0


class TestAuthorization:
    def test_cross_user_stop_edit_rejected(self, client, pg_conn, unique_user_id):
        # Wave A closure correction — edit_trade's lookup is now scoped
        # by id AND user_id together, so a cross-user edit returns the
        # SAME stable 404 as a nonexistent trade (see the dedicated
        # WA-C09 matrix in test_wave_a_closure_correction_real_pg.py for
        # the full indistinguishability proof).
        ensure_portfolio(pg_conn, unique_user_id)
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        other_user = unique_user_id + "-other"
        resp = client.patch(
            f"/api/paper-trading/trade/{trade_id}", json={"stop_loss": 85.0},
            headers=make_auth_header(other_user),
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Trade not found"
        flags = _fetch_flags(pg_conn, trade_id)
        assert flags["stop_modified"] is False
        assert flags["stop_loss"] == 90.0

    def test_nonexistent_trade_edit_indistinguishable_from_cross_user(self, client, unique_user_id):
        resp = client.patch(
            "/api/paper-trading/trade/999999999", json={"stop_loss": 85.0},
            headers=make_auth_header(unique_user_id),
        )
        assert resp.status_code == 404


class TestSnapshotsCloseAndReset:
    @pytest.mark.timeout(20)
    def test_buy_then_close_populates_exit_snapshot_governed_fields(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        buy_resp = client.post(
            "/api/paper-trading/buy",
            # Owner-authorized hardening — idempotency_key is now REQUIRED.
            json={"symbol": "AAPL", "market": "US", "quantity": 1, "price": 100.0,
                  "stop_loss": 90.0, "target_price": 120.0,
                  "idempotency_key": f"ptbuy-test-{uuid.uuid4()}"},
            headers=make_auth_header(unique_user_id),
        )
        assert buy_resp.status_code == 200
        trade_id = buy_resp.json()["trade_id"]

        edit_resp = client.patch(
            f"/api/paper-trading/trade/{trade_id}", json={"stop_loss": 85.0},
            headers=make_auth_header(unique_user_id),
        )
        assert edit_resp.status_code == 200

        sell_resp = client.post(
            f"/api/paper-trading/sell/{trade_id}", json={"price": 110.0},
            headers=make_auth_header(unique_user_id),
        )
        assert sell_resp.status_code == 200

        row = pg_conn.execute(
            "SELECT level_history_contract_version, final_stop_modified_after_entry, "
            "final_target_modified_after_entry, levels_modified_after_entry "
            "FROM paper_trade_exit_snapshot WHERE paper_trade_id = %s",
            (trade_id,),
        ).fetchone()
        assert row is not None
        version, final_stop_modified, final_target_modified, aggregate = row
        assert version == "1"
        assert final_stop_modified is True
        assert final_target_modified is False
        assert aggregate is True

    def test_reset_removes_governed_trade_and_snapshot_for_owner_only(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        other_user = unique_user_id + "-other"
        ensure_portfolio(pg_conn, other_user)
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        other_trade_id = _insert_governed_trade(pg_conn, other_user)

        resp = client.post("/api/paper-trading/reset", headers=make_auth_header(unique_user_id))
        assert resp.status_code == 200

        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()[0] == 0
        assert pg_conn.execute(
            "SELECT count(*) FROM paper_trades WHERE id = %s", (other_trade_id,)
        ).fetchone()[0] == 1
