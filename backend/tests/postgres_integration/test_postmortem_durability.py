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
import datetime as dt
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
class TestExitSnapshotInsertAgainstRealPostgres:
    """Locks in the PR #33 real-PostgreSQL CI failure fix (24 columns
    but only 23 bind placeholders — psycopg raises against a real
    server on a parameter-count mismatch that no mocked-connection test
    could ever catch). Exercises close_service.close_paper_trade
    directly against pg_conn so both the null-optional-fields path (via
    the HTTP endpoint, which never populates source_metadata/trigger_*
    fields today) and the fully-populated path (direct service call,
    proving the INSERT itself handles every column when a caller does
    supply them) both succeed against a real server."""

    def test_close_via_http_endpoint_succeeds_with_all_optional_fields_null(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        row = pg_conn.execute(
            "SELECT source_metadata, trigger_observation_timestamp, trailing_stop_level "
            "FROM paper_trade_exit_snapshot WHERE paper_trade_id = %s",
            (trade_id,)
        ).fetchone()
        assert row == (None, None, None)

    def test_direct_close_service_call_succeeds_with_every_optional_field_populated(self, pg_conn, unique_user_id):
        from services.postmortem.close_service import close_paper_trade
        from services.postmortem.exit_snapshot import CloseExitMechanism

        ensure_portfolio(pg_conn, unique_user_id, cash_usd=1_000_000.0)
        trade_row = pg_conn.execute(
            """INSERT INTO paper_trades (user_id, symbol, market, quantity, entry_price, status,
                                          stop_loss, target_price, trade_management_mode)
               VALUES (%s, 'AAPL', 'US', 10, 100.0, 'OPEN', 90.0, 130.0, 'auto') RETURNING id""",
            (unique_user_id,)
        ).fetchone()
        trade_id = trade_row[0]

        with pg_conn.transaction():
            result = close_paper_trade(
                pg_conn, user_id=unique_user_id, trade_id=trade_id, exit_price=95.0,
                exit_mechanism=CloseExitMechanism.STOP_LOSS, exit_mechanism_raw="STOP_LOSS",
                source_request_id="close-req-abc123",
                exit_metadata={"detected_by": "browser_useeffect", "note": "auto stop-loss trigger"},
            )
        assert result.trade_id == trade_id

        row = pg_conn.execute(
            """SELECT financial_outcome, closure_classification, exit_mechanism, exit_mechanism_raw,
                      exit_price, exit_quantity, final_stop_loss, final_target_price, management_mode,
                      source_request_id, trigger_timing_verification, source_metadata
               FROM paper_trade_exit_snapshot WHERE paper_trade_id = %s""",
            (trade_id,)
        ).fetchone()
        (outcome, closure, mechanism, mechanism_raw, exit_price, exit_qty, stop_loss, target,
         mgmt_mode, source_request_id, trigger_verification, source_metadata) = row

        assert outcome == "LOSS"
        assert closure == "SYSTEM_EXIT"
        assert mechanism == "STOP_LOSS"
        assert mechanism_raw == "STOP_LOSS"
        assert exit_price == 95.0
        assert exit_qty == 10
        assert stop_loss == 90.0
        assert target == 130.0
        assert mgmt_mode == "auto"
        assert source_request_id == "close-req-abc123"
        assert trigger_verification == "CLIENT_REPORTED_UNVERIFIED"
        assert source_metadata == {"detected_by": "browser_useeffect", "note": "auto stop-loss trigger"}


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

    def test_report_never_updated_via_persist_application_level(self, client, pg_conn, unique_user_id):
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

    def test_report_update_rejected_by_database_trigger(self, client, pg_conn, unique_user_id):
        """Stage 8 correction — database-level immutability, not merely
        an application-code convention: a real UPDATE against a
        persisted report row is rejected by trg_paper_trade_postmortem_
        report_immutable, exactly like the entry/exit snapshot tables."""
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        client.post(f"/api/paper-trading/postmortem/{trade_id}/generate", headers=make_auth_header(unique_user_id))
        with pytest.raises(psycopg.errors.RaiseException):
            pg_conn.execute(
                "UPDATE paper_trade_postmortem_report SET status = 'FAILED_TERMINAL' WHERE paper_trade_id = %s",
                (trade_id,)
            )

    def test_report_delete_still_permitted_for_reset(self, pg_conn, unique_user_id):
        """The immutability trigger only rejects UPDATE — DELETE remains
        permitted, since reset_portfolio's own DELETE statements must
        still work (see TestResetLeavesNoOrphanRows below)."""
        ensure_portfolio(pg_conn, unique_user_id)
        pg_conn.execute(
            """INSERT INTO paper_trade_postmortem_report (
                paper_trade_id, user_id, market, report_trading_date, market_timezone,
                report_schema_version, calculation_version, attribution_rules_version, evidence_bundle_version,
                evidence_hash, status, structured_report, evidence_items, claims, source_manifest
            ) VALUES (999999, %s, 'US', CURRENT_DATE, 'America/New_York', '1.0.0', '1.0.0', '1.0.0', '1.0.0',
                      'deadbeef', 'COMPLETE', '{}'::jsonb, '[]'::jsonb, '[]'::jsonb, '{}'::jsonb)""",
            (unique_user_id,)
        )
        pg_conn.execute("DELETE FROM paper_trade_postmortem_report WHERE user_id = %s", (unique_user_id,))
        count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report WHERE user_id = %s", (unique_user_id,)
        ).fetchone()[0]
        assert count == 0


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


@pytest.mark.timeout(30)
class TestCrossUserSellPreflightPrivacyAgainstRealPG:
    """Stage 5 correction, real-PostgreSQL proof: the preflight probe's
    WHERE clause (id = %s AND user_id = %s) genuinely returns no row for
    a trade owned by another user, against a real server — not just the
    mocked regression suite's own simulated filtering."""

    @pytest.mark.parametrize("market,market_open", [
        ("IN", True), ("IN", False), ("US", True), ("US", False),
    ])
    def test_cross_user_trade_indistinguishable_from_nonexistent(
        self, client, pg_conn, unique_user_id, market, market_open, monkeypatch
    ):
        monkeypatch.setattr("api.routers.paper_trading._is_market_open", lambda m: market_open)
        victim_id = f"{unique_user_id}-victim"
        ensure_portfolio(pg_conn, victim_id, cash_usd=1_000_000.0)
        symbol = "TCS" if market == "IN" else "AAPL"
        victim_trade_id = _buy(client, victim_id, market=market, symbol=symbol).json()["trade_id"]

        ensure_portfolio(pg_conn, unique_user_id, cash_usd=1_000_000.0)
        not_found = client.post(
            "/api/paper-trading/sell/999999999", json={"price": 100.0, "exit_reason": "MANUAL"},
            headers=make_auth_header(unique_user_id),
        )
        cross_user = client.post(
            f"/api/paper-trading/sell/{victim_trade_id}", json={"price": 100.0, "exit_reason": "MANUAL"},
            headers=make_auth_header(unique_user_id),
        )

        assert cross_user.status_code == 404
        assert cross_user.json() == not_found.json()

        victim_status = pg_conn.execute(
            "SELECT status FROM paper_trades WHERE id = %s", (victim_trade_id,)
        ).fetchone()[0]
        assert victim_status == "OPEN"

        # Cleanup: this test's own victim_id isn't under the standard
        # SYNTHETIC_USER_PREFIX%-scoped autouse cleanup fixture unless it
        # shares that prefix — it does, since it's derived from
        # unique_user_id (itself prefixed) — asserted here defensively.
        assert victim_id.startswith(unique_user_id)


@pytest.mark.timeout(30)
class TestReportOutboxConsistencyAgainstRealPG:
    """Stage 3/8 correction — the exact database postcondition query a
    future monitoring job would run to detect the two contradictions
    this sprint's design must never produce: a completed/limited-evidence
    outbox row with no corresponding report, or a persisted report whose
    outbox row was never marked terminal. Run here against real close +
    generate flows as a direct proof, not just unit-level trust."""

    def test_no_terminal_outbox_without_a_report(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        client.post(f"/api/paper-trading/postmortem/{trade_id}/generate", headers=make_auth_header(unique_user_id))

        contradictions = pg_conn.execute(
            """SELECT o.id FROM paper_trade_postmortem_outbox o
               WHERE o.status IN ('COMPLETE', 'LIMITED_EVIDENCE')
                 AND NOT EXISTS (
                     SELECT 1 FROM paper_trade_postmortem_report r
                     WHERE r.paper_trade_id = o.paper_trade_id
                       AND r.report_schema_version = o.requested_report_schema_version
                       AND r.calculation_version = o.requested_calculation_version
                       AND r.attribution_rules_version = o.requested_rules_version
                 )
               AND o.paper_trade_id = %s""",
            (trade_id,)
        ).fetchall()
        assert contradictions == []

    def test_no_report_without_a_terminal_outbox(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        client.post(f"/api/paper-trading/postmortem/{trade_id}/generate", headers=make_auth_header(unique_user_id))

        contradictions = pg_conn.execute(
            """SELECT r.id FROM paper_trade_postmortem_report r
               WHERE NOT EXISTS (
                   SELECT 1 FROM paper_trade_postmortem_outbox o
                   WHERE o.paper_trade_id = r.paper_trade_id
                     AND o.requested_report_schema_version = r.report_schema_version
                     AND o.requested_calculation_version = r.calculation_version
                     AND o.requested_rules_version = r.attribution_rules_version
                     AND o.status IN ('COMPLETE', 'LIMITED_EVIDENCE')
               )
               AND r.paper_trade_id = %s""",
            (trade_id,)
        ).fetchall()
        assert contradictions == []
