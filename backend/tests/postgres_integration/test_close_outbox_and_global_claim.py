"""
Wave B closure, Sections 8G/8H — real-PostgreSQL behavioral proof for
atomic close-to-current-outbox insertion (close_service.close_paper_trade)
and the global cross-user claim (outbox_worker.claim_next_current_outbox_batch).
"""
import threading
import uuid
from datetime import datetime, timezone

import psycopg
import pytest

pytestmark = pytest.mark.postgres_integration


def _open_trade(pg_conn, *, user_id, market="US"):
    row = pg_conn.execute(
        """INSERT INTO paper_trades
           (session_id, user_id, symbol, market, quantity, entry_price, stop_loss, target_price,
            status, trade_management_mode, opened_at)
           VALUES (%s, %s, 'AAPL', %s, 10, 100.0, 95.0, 110.0, 'OPEN', 'MANUAL', now())
           RETURNING id""",
        (user_id, user_id, market),
    ).fetchone()
    return row[0]


# ============================= Section 8G — close-to-outbox atomicity ============================= #

def test_8g_flag_enabled_inserts_current_outbox_in_close_transaction(pg_conn, pg_database_url, unique_user_id):
    from services.postmortem.close_service import CloseExitMechanism, close_paper_trade
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION

    trade_id = _open_trade(pg_conn, user_id=unique_user_id)
    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )

    with pg_conn.transaction():
        result = close_paper_trade(
            pg_conn, user_id=unique_user_id, trade_id=trade_id, exit_price=105.0,
            exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
            request_current_report_outbox=True,
            current_report_schema_version=schema_v, current_calculation_version=calc_v, current_rules_version=rules_v,
        )

    row = pg_conn.execute(
        """SELECT status FROM paper_trade_postmortem_outbox
           WHERE paper_trade_id = %s AND requested_report_schema_version = %s
             AND requested_calculation_version = %s AND requested_rules_version = %s""",
        (trade_id, schema_v, calc_v, rules_v),
    ).fetchone()
    assert row is not None, "8G: no current (1.2.0) outbox row was inserted despite request_current_report_outbox=True."
    assert row[0] == "PENDING"


def test_8g_flag_disabled_legacy_behavior_unchanged_no_current_outbox(pg_conn, pg_database_url, unique_user_id):
    from services.postmortem.close_service import CloseExitMechanism, close_paper_trade

    trade_id = _open_trade(pg_conn, user_id=unique_user_id)
    with pg_conn.transaction():
        result = close_paper_trade(
            pg_conn, user_id=unique_user_id, trade_id=trade_id, exit_price=105.0,
            exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
        )

    count_1_2_0 = pg_conn.execute(
        "SELECT count(*) FROM paper_trade_postmortem_outbox WHERE paper_trade_id = %s AND requested_report_schema_version = '1.2.0'",
        (trade_id,),
    ).fetchone()[0]
    assert count_1_2_0 == 0, "8G: no 1.2.0 outbox row should exist when request_current_report_outbox is not passed."

    count_legacy = pg_conn.execute(
        "SELECT count(*) FROM paper_trade_postmortem_outbox WHERE paper_trade_id = %s AND requested_report_schema_version = '1.0.0'",
        (trade_id,),
    ).fetchone()[0]
    assert count_legacy == 1, "8G: the legacy 1.0.0 outbox row must still be created (unchanged behavior)."


def test_8g_close_rollback_removes_current_outbox(pg_conn, pg_database_url, unique_user_id):
    from services.postmortem.close_service import CloseExitMechanism, close_paper_trade
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION

    trade_id = _open_trade(pg_conn, user_id=unique_user_id)
    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )

    try:
        with pg_conn.transaction():
            close_paper_trade(
                pg_conn, user_id=unique_user_id, trade_id=trade_id, exit_price=105.0,
                exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
                request_current_report_outbox=True,
                current_report_schema_version=schema_v, current_calculation_version=calc_v, current_rules_version=rules_v,
            )
            raise RuntimeError("forced rollback after close+outbox-insert, before commit")
    except RuntimeError:
        pass

    row = pg_conn.execute(
        "SELECT status FROM paper_trades WHERE id = %s", (trade_id,),
    ).fetchone()
    assert row[0] == "OPEN", "8G: the trade close itself must have rolled back too (atomicity)."

    outbox_row = pg_conn.execute(
        "SELECT id FROM paper_trade_postmortem_outbox WHERE paper_trade_id = %s AND requested_report_schema_version = %s",
        (trade_id, schema_v),
    ).fetchone()
    assert outbox_row is None, "8G: the current-outbox insert must have rolled back along with the close."


# ============================= Section 8H — global claim and lease ============================= #

def test_8h_simultaneous_claimants_never_claim_the_same_row(pg_database_url):
    from services.postmortem import outbox_worker

    schema_v, calc_v, rules_v = "1.2.0", "test-calc-8h", "2.0.0"
    with psycopg.connect(pg_database_url, autocommit=True) as setup_conn:
        user_id = f"pg-integration-test-user-{uuid.uuid4().hex[:8]}"
        trade_id_row = setup_conn.execute(
            """INSERT INTO paper_trades (session_id, user_id, symbol, market, quantity, entry_price, status,
               trade_management_mode, opened_at, closed_at)
               VALUES (%s, %s, 'AAPL', 'US', 1, 100.0, 'CLOSED', 'MANUAL', now(), now()) RETURNING id""",
            (user_id, user_id),
        ).fetchone()
        trade_id = trade_id_row[0]
        outbox_row = setup_conn.execute(
            """INSERT INTO paper_trade_postmortem_outbox
               (paper_trade_id, user_id, requested_report_schema_version, requested_calculation_version,
                requested_rules_version, status)
               VALUES (%s, %s, %s, %s, %s, 'PENDING') RETURNING id""",
            (trade_id, user_id, schema_v, calc_v, rules_v),
        ).fetchone()
        outbox_id = outbox_row[0]

    results = []

    def _claim_attempt():
        with psycopg.connect(pg_database_url, autocommit=True) as conn:
            claimant = outbox_worker.outbox_ops.new_claimant_token()
            batch = outbox_worker.claim_next_current_outbox_batch(
                conn, claimant=claimant, limit=10,
                schema_version=schema_v, calculation_version=calc_v, rules_version=rules_v,
            )
            results.append((claimant, [r["outbox_id"] for r in batch]))

    threads = [threading.Thread(target=_claim_attempt) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    winners = [r for r in results if outbox_id in r[1]]
    assert len(winners) == 1, (
        f"8H: expected exactly ONE claimant to win outbox_id={outbox_id}, got {len(winners)} — "
        f"results: {results}"
    )


def test_8h_expired_lease_is_reclaimable_by_a_new_claimant(pg_conn, pg_database_url, unique_user_id):
    from services.postmortem import outbox_worker

    schema_v, calc_v, rules_v = "1.2.0", "test-calc-8h-expired", "2.0.0"
    trade_id_row = pg_conn.execute(
        """INSERT INTO paper_trades (session_id, user_id, symbol, market, quantity, entry_price, status,
           trade_management_mode, opened_at, closed_at)
           VALUES (%s, %s, 'AAPL', 'US', 1, 100.0, 'CLOSED', 'MANUAL', now(), now()) RETURNING id""",
        (unique_user_id, unique_user_id),
    ).fetchone()
    trade_id = trade_id_row[0]
    outbox_row = pg_conn.execute(
        """INSERT INTO paper_trade_postmortem_outbox
           (paper_trade_id, user_id, requested_report_schema_version, requested_calculation_version,
            requested_rules_version, status, claimed_by, claimed_at, lease_expires_at, attempt_count)
           VALUES (%s, %s, %s, %s, %s, 'GENERATING', 'stale-claimant', now() - interval '10 minutes',
                   now() - interval '5 minutes', 1)
           RETURNING id""",
        (trade_id, unique_user_id, schema_v, calc_v, rules_v),
    ).fetchone()
    outbox_id = outbox_row[0]

    new_claimant = outbox_worker.outbox_ops.new_claimant_token()
    batch = outbox_worker.claim_next_current_outbox_batch(
        pg_conn, claimant=new_claimant, limit=10,
        schema_version=schema_v, calculation_version=calc_v, rules_version=rules_v,
    )
    claimed_ids = [r["outbox_id"] for r in batch]
    assert outbox_id in claimed_ids, "8H: an expired-lease GENERATING row must be reclaimable by a new claimant."

    row = pg_conn.execute(
        "SELECT status, claimed_by FROM paper_trade_postmortem_outbox WHERE id = %s", (outbox_id,),
    ).fetchone()
    assert row[0] == "GENERATING"
    assert row[1] == new_claimant, "8H: the reclaimed row's claimed_by must be the NEW claimant, not the stale one."
