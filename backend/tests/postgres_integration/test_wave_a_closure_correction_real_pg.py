"""
Trade Postmortem Sprint 3A, Wave A closure correction — real
PostgreSQL verification for corrections A (entry-snapshot governed
evidence), C (explicit transaction / deterministic concurrency), D
(exact governed tuple invariant), and E (privacy-safe authorization).
Never mocks _conn.
"""
import threading
import time

import pytest

from tests.postgres_integration.conftest import ensure_portfolio, make_auth_header

pytestmark = pytest.mark.postgres_integration


def _insert_governed_trade(pg_conn, user_id, **overrides):
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


def _buy(client, user_id, **body_overrides):
    body = {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 100.0,
            "stop_loss": 90.0, "target_price": 120.0}
    body.update(body_overrides)
    return client.post("/api/paper-trading/buy", json=body, headers=make_auth_header(user_id))


# ============================= WA-C01 — entry-snapshot governed fields =============================

class TestEntrySnapshotGovernedFields:
    def test_new_governed_buy_stores_four_governed_entry_fields(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        resp = _buy(client, unique_user_id)
        assert resp.status_code == 200
        trade_id = resp.json()["trade_id"]

        row = pg_conn.execute(
            "SELECT snapshot_schema_version, level_history_contract_version, "
            "initial_stop_modified_after_entry, initial_target_modified_after_entry, "
            "initial_levels_modified_after_entry FROM paper_trade_entry_snapshot WHERE paper_trade_id = %s",
            (trade_id,),
        ).fetchone()
        assert row is not None
        schema_version, version, initial_stop, initial_target, initial_agg = row
        assert schema_version == "1.1.0"
        assert version == "1"
        assert initial_stop is False
        assert initial_target is False
        assert initial_agg is False

    def test_legacy_entry_snapshot_remains_null_for_new_fields(self, pg_conn, unique_user_id):
        # A legacy-shaped snapshot is one whose linked trade has no
        # governed invariant version -- directly inserted here since no
        # production writer creates this shape anymore (buy_trade always
        # writes governed fields), matching how a pre-Wave-A row would
        # read back untouched.
        trade_id = _insert_governed_trade(
            pg_conn, unique_user_id, level_history_contract_version=None,
            stop_modified_after_entry=None, target_modified_after_entry=None, levels_modified_after_entry=None,
        )
        pg_conn.execute(
            """INSERT INTO paper_trade_entry_snapshot
               (paper_trade_id, user_id, symbol, market, snapshot_schema_version, evidence_source,
                simulated_execution_price, execution_range_position, user_overrode_recommendation,
                verification_levels)
               VALUES (%s, %s, 'AAPL', 'US', '1.0.0', 'MANUAL', 100.0, 'UNKNOWN', NULL, '{}'::jsonb)""",
            (trade_id, unique_user_id),
        )
        row = pg_conn.execute(
            "SELECT level_history_contract_version, initial_stop_modified_after_entry, "
            "initial_target_modified_after_entry, initial_levels_modified_after_entry "
            "FROM paper_trade_entry_snapshot WHERE paper_trade_id = %s",
            (trade_id,),
        ).fetchone()
        assert row == (None, None, None, None)

    def test_entry_snapshot_cannot_be_updated(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        resp = _buy(client, unique_user_id)
        trade_id = resp.json()["trade_id"]
        with pytest.raises(Exception, match="immutable"):
            pg_conn.execute(
                "UPDATE paper_trade_entry_snapshot SET level_history_contract_version = NULL WHERE paper_trade_id = %s",
                (trade_id,),
            )

    def test_schema_reinitialization_idempotent_after_entry_snapshot_columns(self, pg_conn, initialized_schema):
        from services import postgres_store
        postgres_store.init_db()

    def test_no_existing_row_backfilled(self, pg_conn, unique_user_id):
        # A row written before this Wave (simulated: legacy shape, no
        # governed fields) is never touched by re-running schema init.
        trade_id = _insert_governed_trade(
            pg_conn, unique_user_id, level_history_contract_version=None,
            stop_modified_after_entry=None, target_modified_after_entry=None, levels_modified_after_entry=None,
        )
        pg_conn.execute(
            """INSERT INTO paper_trade_entry_snapshot
               (paper_trade_id, user_id, symbol, market, snapshot_schema_version, evidence_source,
                simulated_execution_price, execution_range_position, user_overrode_recommendation,
                verification_levels)
               VALUES (%s, %s, 'AAPL', 'US', '1.0.0', 'MANUAL', 100.0, 'UNKNOWN', NULL, '{}'::jsonb)""",
            (trade_id, unique_user_id),
        )
        from services import postgres_store
        postgres_store.init_db()
        row = pg_conn.execute(
            "SELECT snapshot_schema_version, level_history_contract_version FROM paper_trade_entry_snapshot WHERE paper_trade_id = %s",
            (trade_id,),
        ).fetchone()
        assert row == ("1.0.0", None)


# ============================= WA-C02 — snapshot schema-version coexistence =============================

class TestSnapshotVersionCoexistence:
    def test_new_entry_and_exit_snapshots_stamp_1_1_0(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        buy_resp = _buy(client, unique_user_id)
        trade_id = buy_resp.json()["trade_id"]
        sell_resp = client.post(
            f"/api/paper-trading/sell/{trade_id}", json={"price": 110.0}, headers=make_auth_header(unique_user_id),
        )
        assert sell_resp.status_code == 200

        entry_version = pg_conn.execute(
            "SELECT snapshot_schema_version FROM paper_trade_entry_snapshot WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        exit_version = pg_conn.execute(
            "SELECT exit_snapshot_schema_version FROM paper_trade_exit_snapshot WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert entry_version == "1.1.0"
        assert exit_version == "1.1.0"

    def test_1_0_0_and_1_1_0_snapshots_coexist(self, client, pg_conn, unique_user_id):
        # A legacy 1.0.0 row (simulated, since no writer produces it
        # anymore) and a genuine new 1.1.0 row for a DIFFERENT trade both
        # remain independently readable side by side.
        ensure_portfolio(pg_conn, unique_user_id)
        legacy_trade_id = _insert_governed_trade(
            pg_conn, unique_user_id, level_history_contract_version=None,
            stop_modified_after_entry=None, target_modified_after_entry=None, levels_modified_after_entry=None,
        )
        pg_conn.execute(
            """INSERT INTO paper_trade_entry_snapshot
               (paper_trade_id, user_id, symbol, market, snapshot_schema_version, evidence_source,
                simulated_execution_price, execution_range_position, user_overrode_recommendation,
                verification_levels)
               VALUES (%s, %s, 'AAPL', 'US', '1.0.0', 'MANUAL', 100.0, 'UNKNOWN', NULL, '{}'::jsonb)""",
            (legacy_trade_id, unique_user_id),
        )
        buy_resp = _buy(client, unique_user_id)
        new_trade_id = buy_resp.json()["trade_id"]

        legacy_version = pg_conn.execute(
            "SELECT snapshot_schema_version FROM paper_trade_entry_snapshot WHERE paper_trade_id = %s", (legacy_trade_id,)
        ).fetchone()[0]
        new_version = pg_conn.execute(
            "SELECT snapshot_schema_version FROM paper_trade_entry_snapshot WHERE paper_trade_id = %s", (new_trade_id,)
        ).fetchone()[0]
        assert legacy_version == "1.0.0"
        assert new_version == "1.1.0"


# ============================= WA-C03/C04/C05 — transaction, concurrency, rollback =============================

class TestExplicitTransactionAndConcurrency:
    @pytest.mark.timeout(30)
    def test_wa_c04_deterministic_concurrent_stop_and_target_preserved(self, client, pg_conn, unique_user_id):
        """Forces genuine overlap: a held row lock (via an explicit
        transaction on a SEPARATE real connection) blocks both concurrent
        editors until released, proving the lock — not mere thread timing
        — is what serializes them."""
        ensure_portfolio(pg_conn, unique_user_id)
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)

        lock_acquired = threading.Event()
        release_lock = threading.Event()

        def _hold_lock():
            with pg_conn.transaction():
                pg_conn.execute("SELECT 1 FROM paper_trades WHERE id = %s FOR UPDATE", (trade_id,))
                lock_acquired.set()
                release_lock.wait(timeout=15)

        holder = threading.Thread(target=_hold_lock)
        holder.start()
        assert lock_acquired.wait(timeout=10), "lock-holder thread never acquired the row lock"

        results = []
        results_lock = threading.Lock()

        def _edit_stop():
            resp = client.patch(
                f"/api/paper-trading/trade/{trade_id}", json={"stop_loss": 85.0},
                headers=make_auth_header(unique_user_id),
            )
            with results_lock:
                results.append(("stop", resp.status_code))

        def _edit_target():
            resp = client.patch(
                f"/api/paper-trading/trade/{trade_id}", json={"target_price": 160.0},
                headers=make_auth_header(unique_user_id),
            )
            with results_lock:
                results.append(("target", resp.status_code))

        t1 = threading.Thread(target=_edit_stop)
        t2 = threading.Thread(target=_edit_target)
        t1.start()
        t2.start()
        # Both editors must now be blocked on the held row lock — give
        # them a moment to reach that blocked state before releasing it.
        time.sleep(0.5)
        release_lock.set()
        holder.join(timeout=15)
        t1.join(timeout=20)
        t2.join(timeout=20)

        assert len(results) == 2
        assert all(code == 200 for _, code in results), results

        flags = _fetch_flags(pg_conn, trade_id)
        assert flags["stop_loss"] == 85.0
        assert flags["target_price"] == 160.0
        assert flags["stop_modified"] is True
        assert flags["target_modified"] is True
        assert flags["aggregate"] is True

    @pytest.mark.timeout(20)
    def test_wa_c04_concurrent_identical_edits_remain_idempotent(self, client, pg_conn, unique_user_id):
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
            t.join(timeout=15)

        assert len(results) == N and all(c == 200 for c in results)
        flags = _fetch_flags(pg_conn, trade_id)
        assert flags["stop_loss"] == 85.0
        assert flags["stop_modified"] is True
        assert flags["aggregate"] is True

    def test_wa_c05_rollback_on_cash_adjustment_failure_preserves_trade_and_cash(self, client, pg_conn, unique_user_id):
        """Forces the entry-price cash-adjustment UPDATE (the LAST
        statement in edit_trade's transaction) to hit a genuine
        PostgreSQL error, proving the earlier stop/target UPDATE in the
        SAME transaction is rolled back too -- not partially committed.
        TestClient's default raise_server_exceptions=True means an
        unhandled backend exception propagates to the test rather than
        becoming an HTTP 500 response — matching this suite's existing
        test_early_failure_before_trade_insert_leaves_no_effect pattern."""
        import psycopg
        from unittest.mock import patch as _patch
        ensure_portfolio(pg_conn, unique_user_id, cash_usd=1_000_000.0)
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        cash_before = pg_conn.execute(
            "SELECT cash_usd FROM paper_portfolio WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0]

        with _patch.dict("api.routers.paper_trading._CASH_COL", {"US": "not_a_real_column"}):
            with pytest.raises(psycopg.errors.UndefinedColumn):
                client.patch(
                    f"/api/paper-trading/trade/{trade_id}",
                    json={"stop_loss": 85.0, "target_price": 160.0, "entry_price": 105.0},
                    headers=make_auth_header(unique_user_id),
                )

        flags = _fetch_flags(pg_conn, trade_id)
        assert flags["stop_loss"] == 90.0
        assert flags["target_price"] == 120.0
        assert flags["stop_modified"] is False
        assert flags["target_modified"] is False
        assert flags["aggregate"] is False
        cash_after = pg_conn.execute(
            "SELECT cash_usd FROM paper_portfolio WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0]
        assert cash_after == pytest.approx(cash_before)


# ============================= WA-C06/C07/C08 — exact governed tuple invariant =============================

class TestExactGovernedTupleMatrix:
    def _assert_insert_rejected(self, pg_conn, unique_user_id, **overrides):
        with pytest.raises(Exception):
            _insert_governed_trade(pg_conn, unique_user_id, **overrides)

    def test_insert_false_false_true_rejected(self, pg_conn, unique_user_id):
        self._assert_insert_rejected(
            pg_conn, unique_user_id,
            stop_modified_after_entry=False, target_modified_after_entry=False, levels_modified_after_entry=True,
        )

    def test_insert_false_false_null_rejected(self, pg_conn, unique_user_id):
        self._assert_insert_rejected(
            pg_conn, unique_user_id,
            stop_modified_after_entry=False, target_modified_after_entry=False, levels_modified_after_entry=None,
        )

    def test_insert_true_false_false_rejected(self, pg_conn, unique_user_id):
        self._assert_insert_rejected(
            pg_conn, unique_user_id,
            stop_modified_after_entry=True, target_modified_after_entry=False, levels_modified_after_entry=False,
        )

    def test_insert_false_true_false_rejected(self, pg_conn, unique_user_id):
        self._assert_insert_rejected(
            pg_conn, unique_user_id,
            stop_modified_after_entry=False, target_modified_after_entry=True, levels_modified_after_entry=False,
        )

    def test_insert_null_false_false_rejected(self, pg_conn, unique_user_id):
        self._assert_insert_rejected(
            pg_conn, unique_user_id,
            stop_modified_after_entry=None, target_modified_after_entry=False, levels_modified_after_entry=False,
        )

    def test_insert_false_null_false_rejected(self, pg_conn, unique_user_id):
        self._assert_insert_rejected(
            pg_conn, unique_user_id,
            stop_modified_after_entry=False, target_modified_after_entry=None, levels_modified_after_entry=False,
        )

    def test_insert_partial_null_rejected(self, pg_conn, unique_user_id):
        self._assert_insert_rejected(
            pg_conn, unique_user_id,
            stop_modified_after_entry=True, target_modified_after_entry=None, levels_modified_after_entry=True,
        )

    def test_insert_unsupported_version_rejected(self, pg_conn, unique_user_id):
        self._assert_insert_rejected(pg_conn, unique_user_id, level_history_contract_version="2")

    def test_update_to_false_false_true_rejected(self, pg_conn, unique_user_id):
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        with pytest.raises(Exception):
            pg_conn.execute(
                "UPDATE paper_trades SET levels_modified_after_entry = TRUE WHERE id = %s", (trade_id,)
            )

    def test_update_removing_aggregate_while_per_level_true_rejected(self, pg_conn, unique_user_id):
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        pg_conn.execute(
            "UPDATE paper_trades SET stop_loss = 95.0, stop_modified_after_entry = TRUE, "
            "levels_modified_after_entry = TRUE WHERE id = %s", (trade_id,)
        )
        with pytest.raises(Exception):
            pg_conn.execute(
                "UPDATE paper_trades SET levels_modified_after_entry = FALSE WHERE id = %s", (trade_id,)
            )

    def test_update_mutating_supported_version_rejected(self, pg_conn, unique_user_id):
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        with pytest.raises(Exception):
            pg_conn.execute(
                "UPDATE paper_trades SET level_history_contract_version = NULL WHERE id = %s", (trade_id,)
            )


# ============================= WA-C09 — cross-user/nonexistent indistinguishability =============================

class TestPrivacySafeAuthorizationMatrix:
    def test_owner_existing_trade_succeeds(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        resp = client.patch(
            f"/api/paper-trading/trade/{trade_id}", json={"stop_loss": 85.0}, headers=make_auth_header(unique_user_id),
        )
        assert resp.status_code == 200

    def test_owner_nonexistent_trade_returns_stable_not_found(self, client, unique_user_id):
        resp = client.patch(
            "/api/paper-trading/trade/999999999", json={"stop_loss": 85.0}, headers=make_auth_header(unique_user_id),
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Trade not found"

    def test_other_user_existing_trade_returns_identical_result(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        other_user = unique_user_id + "-other"
        resp = client.patch(
            f"/api/paper-trading/trade/{trade_id}", json={"stop_loss": 85.0}, headers=make_auth_header(other_user),
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Trade not found"

    def test_market_and_status_differences_do_not_create_existence_oracle(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        us_trade_id = _insert_governed_trade(pg_conn, unique_user_id, market="US")
        closed_trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        pg_conn.execute(
            "UPDATE paper_trades SET status = 'CLOSED', exit_price = 100.0 WHERE id = %s", (closed_trade_id,)
        )
        other_user = unique_user_id + "-other"

        us_resp = client.patch(
            f"/api/paper-trading/trade/{us_trade_id}", json={"stop_loss": 85.0}, headers=make_auth_header(other_user),
        )
        closed_resp = client.patch(
            f"/api/paper-trading/trade/{closed_trade_id}", json={"stop_loss": 85.0}, headers=make_auth_header(other_user),
        )
        assert us_resp.status_code == closed_resp.status_code == 404
        assert us_resp.json()["detail"] == closed_resp.json()["detail"] == "Trade not found"

    def test_rejected_edit_does_not_mutate_trade_snapshot_or_portfolio(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id, cash_usd=1_000_000.0)
        trade_id = _insert_governed_trade(pg_conn, unique_user_id)
        other_user = unique_user_id + "-other"
        cash_before = pg_conn.execute(
            "SELECT cash_usd FROM paper_portfolio WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0]

        resp = client.patch(
            f"/api/paper-trading/trade/{trade_id}", json={"stop_loss": 85.0, "entry_price": 999.0},
            headers=make_auth_header(other_user),
        )
        assert resp.status_code == 404

        flags = _fetch_flags(pg_conn, trade_id)
        assert flags["stop_loss"] == 90.0
        assert flags["stop_modified"] is False
        cash_after = pg_conn.execute(
            "SELECT cash_usd FROM paper_portfolio WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0]
        assert cash_after == pytest.approx(cash_before)
