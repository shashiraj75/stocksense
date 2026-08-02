"""
Wave B closure, Section 8A/8B/8C/8D — real-PostgreSQL behavioral proof
for current_report_generation.process_current_report, calling the REAL
production function against a real database (never a mock), directly
seeding real trade/entry-snapshot/exit-snapshot/outbox rows via SQL,
exactly the way production data would look.

No local PostgreSQL is available in this development environment; this
suite is executed by the GitHub Actions PostgreSQL 15/17 CI matrix.
Collection is verified locally; execution happens on dispatch.
"""
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import psycopg
import pytest

pytestmark = pytest.mark.postgres_integration

_ET = ZoneInfo("America/New_York")
_IST = ZoneInfo("Asia/Kolkata")


def _make_conn_factory(pg_database_url):
    @contextmanager
    def _factory():
        with psycopg.connect(pg_database_url, autocommit=True) as conn:
            yield conn
    return _factory


def _seed_trade(pg_conn, *, user_id, market="US", entry_price=100.0, exit_price=108.0,
                 stop_loss=95.0, target_price=110.0, closed_at):
    row = pg_conn.execute(
        """INSERT INTO paper_trades
           (session_id, user_id, symbol, market, quantity, entry_price, exit_price, stop_loss, target_price,
            status, trade_management_mode, opened_at, closed_at, exit_reason)
           VALUES (%s, %s, 'AAPL', %s, 10, %s, %s, %s, %s, 'CLOSED', 'MANUAL', %s, %s, 'MANUAL')
           RETURNING id""",
        (user_id, user_id, market, entry_price, exit_price, stop_loss, target_price,
         closed_at, closed_at),
    ).fetchone()
    return row[0]


def _seed_entry_snapshot(pg_conn, *, trade_id, user_id, market="US",
                          user_selected_stop_loss, user_selected_target_price):
    pg_conn.execute(
        """INSERT INTO paper_trade_entry_snapshot
           (paper_trade_id, user_id, symbol, market, snapshot_schema_version, evidence_source,
            simulated_execution_price, user_selected_stop_loss, user_selected_target_price,
            user_overrode_recommendation, verification_levels, level_history_contract_version,
            initial_stop_modified_after_entry, initial_target_modified_after_entry,
            initial_levels_modified_after_entry)
           VALUES (%s, %s, 'AAPL', %s, '1.0.0', 'DAILY_PICK', 100.0, %s, %s, false, '{}'::jsonb, '1.0.0',
                   false, false, false)""",
        (trade_id, user_id, market, user_selected_stop_loss, user_selected_target_price),
    )


def _seed_exit_snapshot(pg_conn, *, trade_id, user_id, market="US", closed_at,
                         final_stop_loss, final_target_price, levels_modified=True):
    pg_conn.execute(
        """INSERT INTO paper_trade_exit_snapshot
           (paper_trade_id, user_id, symbol, market, exit_snapshot_schema_version, financial_outcome,
            closure_classification, exit_mechanism, exit_mechanism_raw, exit_price, exit_quantity, closed_at,
            final_stop_loss, final_target_price, management_mode, levels_modified_after_entry,
            level_history_contract_version, final_stop_modified_after_entry, final_target_modified_after_entry)
           VALUES (%s, %s, 'AAPL', %s, '1.0.0', 'PROFIT', 'MANUAL', 'MANUAL', 'MANUAL', 108.0, 10, %s,
                   %s, %s, 'MANUAL', %s, '1.0.0', %s, %s)""",
        (trade_id, user_id, market, closed_at, final_stop_loss, final_target_price,
         levels_modified, levels_modified, levels_modified),
    )


def _current_report_row(pg_conn, trade_id):
    return pg_conn.execute(
        "SELECT id, status, structured_report, report_trading_date FROM paper_trade_postmortem_report "
        "WHERE paper_trade_id = %s AND report_schema_version = '1.2.0'",
        (trade_id,),
    ).fetchone()


# ============================= Section 8A — immutable endpoint provenance ============================= #

def test_8a_entry_and_exit_endpoint_values_survive_real_persistence(pg_conn, pg_database_url, unique_user_id):
    from services.postmortem.current_report_generation import process_current_report

    closed_at = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    trade_id = _seed_trade(
        pg_conn, user_id=unique_user_id, entry_price=100.0, exit_price=108.0,
        stop_loss=97.0, target_price=108.0, closed_at=closed_at,
    )
    _seed_entry_snapshot(pg_conn, trade_id=trade_id, user_id=unique_user_id,
                          user_selected_stop_loss=95.0, user_selected_target_price=110.0)
    _seed_exit_snapshot(pg_conn, trade_id=trade_id, user_id=unique_user_id, closed_at=closed_at,
                         final_stop_loss=97.0, final_target_price=108.0, levels_modified=True)

    report, outcome = process_current_report(
        _make_conn_factory(pg_database_url), trade_id=trade_id, user_id=unique_user_id,
        market_tzinfo=_ET, market_timezone_name="America/New_York",
    )
    assert outcome == "CURRENT_REPORT_GENERATED", f"expected CURRENT_REPORT_GENERATED, got {outcome!r}"
    price_path = report.structured_report["price_path"]
    target_section = price_path["level_history"]["target"]
    stop_section = price_path["level_history"]["stop"]

    assert target_section["entry_endpoint_value"] == 110.0, (
        f"8A: persisted target entry_endpoint_value={target_section['entry_endpoint_value']!r}, expected 110.0 "
        "(the immutable entry snapshot's own value, never the modified exit value)."
    )
    assert target_section["exit_endpoint_value"] == 108.0
    assert stop_section["entry_endpoint_value"] == 95.0
    assert stop_section["exit_endpoint_value"] == 97.0
    assert target_section["entry_endpoint_value"] != target_section["exit_endpoint_value"]
    assert stop_section["entry_endpoint_value"] != stop_section["exit_endpoint_value"]


# ============================= Section 8B — six-state reconciliation ============================= #

def test_8b_state1_no_outbox_no_report_generates(pg_conn, pg_database_url, unique_user_id):
    from services.postmortem.current_report_generation import process_current_report
    closed_at = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    trade_id = _seed_trade(pg_conn, user_id=unique_user_id, closed_at=closed_at)
    _seed_entry_snapshot(pg_conn, trade_id=trade_id, user_id=unique_user_id,
                          user_selected_stop_loss=95.0, user_selected_target_price=110.0)
    _seed_exit_snapshot(pg_conn, trade_id=trade_id, user_id=unique_user_id, closed_at=closed_at,
                         final_stop_loss=95.0, final_target_price=110.0, levels_modified=False)

    report, outcome = process_current_report(
        _make_conn_factory(pg_database_url), trade_id=trade_id, user_id=unique_user_id,
        market_tzinfo=_ET, market_timezone_name="America/New_York",
    )
    assert outcome == "CURRENT_REPORT_GENERATED"
    assert report is not None
    assert _current_report_row(pg_conn, trade_id) is not None


def test_8b_state5_terminal_success_with_matching_report_returns_existing_no_second_row(pg_conn, pg_database_url, unique_user_id):
    from services.postmortem.current_report_generation import process_current_report
    closed_at = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    trade_id = _seed_trade(pg_conn, user_id=unique_user_id, closed_at=closed_at)
    _seed_entry_snapshot(pg_conn, trade_id=trade_id, user_id=unique_user_id,
                          user_selected_stop_loss=95.0, user_selected_target_price=110.0)
    _seed_exit_snapshot(pg_conn, trade_id=trade_id, user_id=unique_user_id, closed_at=closed_at,
                         final_stop_loss=95.0, final_target_price=110.0, levels_modified=False)

    conn_factory = _make_conn_factory(pg_database_url)
    first_report, first_outcome = process_current_report(
        conn_factory, trade_id=trade_id, user_id=unique_user_id,
        market_tzinfo=_ET, market_timezone_name="America/New_York",
    )
    assert first_outcome == "CURRENT_REPORT_GENERATED"

    second_report, second_outcome = process_current_report(
        conn_factory, trade_id=trade_id, user_id=unique_user_id,
        market_tzinfo=_ET, market_timezone_name="America/New_York",
    )
    assert second_outcome == "CURRENT_REPORT_ALREADY_COMPLETE"
    assert second_report.id == first_report.id, "8B state 5: a second call must return the SAME report row, never insert a duplicate."

    count = pg_conn.execute(
        "SELECT count(*) FROM paper_trade_postmortem_report WHERE paper_trade_id = %s AND report_schema_version = '1.2.0'",
        (trade_id,),
    ).fetchone()[0]
    assert count == 1, f"8B state 5: expected exactly 1 persisted 1.2.0 report row, found {count}."


def test_8b_state6_terminal_outbox_missing_report_is_integrity_contradiction(pg_conn, pg_database_url, unique_user_id):
    """8B state 6 — a terminal-success (COMPLETE) outbox row whose report
    is genuinely missing must be detected as an integrity contradiction,
    never silently regenerated."""
    from services.postmortem.current_report_generation import (
        current_target_identity, process_current_report,
    )
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION

    closed_at = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    trade_id = _seed_trade(pg_conn, user_id=unique_user_id, closed_at=closed_at)
    _seed_entry_snapshot(pg_conn, trade_id=trade_id, user_id=unique_user_id,
                          user_selected_stop_loss=95.0, user_selected_target_price=110.0)
    _seed_exit_snapshot(pg_conn, trade_id=trade_id, user_id=unique_user_id, closed_at=closed_at,
                         final_stop_loss=95.0, final_target_price=110.0, levels_modified=False)

    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    outbox_row = pg_conn.execute(
        """INSERT INTO paper_trade_postmortem_outbox
           (paper_trade_id, user_id, requested_report_schema_version, requested_calculation_version,
            requested_rules_version, status, completed_at)
           VALUES (%s, %s, %s, %s, %s, 'COMPLETE', now())
           RETURNING id""",
        (trade_id, unique_user_id, schema_v, calc_v, rules_v),
    ).fetchone()
    outbox_id = outbox_row[0]

    report, outcome = process_current_report(
        _make_conn_factory(pg_database_url), trade_id=trade_id, user_id=unique_user_id,
        market_tzinfo=_ET, market_timezone_name="America/New_York", outbox_id=outbox_id,
        claimed_by="fake-claimant-token",
    )
    assert outcome == "CURRENT_REPORT_INTEGRITY_CONTRADICTION", (
        f"8B state 6: expected CURRENT_REPORT_INTEGRITY_CONTRADICTION, got {outcome!r}."
    )
    assert report is None

    assert _current_report_row(pg_conn, trade_id) is None, "8B state 6: no report row must be fabricated."
    outbox_after = pg_conn.execute(
        "SELECT status FROM paper_trade_postmortem_outbox WHERE id = %s", (outbox_id,),
    ).fetchone()
    assert outbox_after[0] == "COMPLETE", "8B state 6: the terminal outbox row must not be rewritten."


# ============================= Section 8C — existing-report settlement ============================= #

def test_8c_existing_report_settles_outbox_with_its_own_status(pg_conn, pg_database_url, unique_user_id):
    from services.postmortem import outbox as outbox_ops
    from services.postmortem.current_report_generation import (
        current_target_identity, process_current_report,
    )
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION

    closed_at = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    trade_id = _seed_trade(pg_conn, user_id=unique_user_id, closed_at=closed_at)
    _seed_entry_snapshot(pg_conn, trade_id=trade_id, user_id=unique_user_id,
                          user_selected_stop_loss=95.0, user_selected_target_price=110.0)
    _seed_exit_snapshot(pg_conn, trade_id=trade_id, user_id=unique_user_id, closed_at=closed_at,
                         final_stop_loss=95.0, final_target_price=110.0, levels_modified=False)

    conn_factory = _make_conn_factory(pg_database_url)
    first_report, first_outcome = process_current_report(
        conn_factory, trade_id=trade_id, user_id=unique_user_id,
        market_tzinfo=_ET, market_timezone_name="America/New_York",
    )
    assert first_outcome == "CURRENT_REPORT_GENERATED"

    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    outbox_row = pg_conn.execute(
        """INSERT INTO paper_trade_postmortem_outbox
           (paper_trade_id, user_id, requested_report_schema_version, requested_calculation_version,
            requested_rules_version, status)
           VALUES (%s, %s, %s, %s, %s, 'PENDING')
           RETURNING id""",
        (trade_id, unique_user_id, schema_v, calc_v, rules_v),
    ).fetchone()
    outbox_id = outbox_row[0]
    claimant = outbox_ops.new_claimant_token()
    claimed = outbox_ops.claim_next_attempt(pg_conn, outbox_id=outbox_id, user_id=unique_user_id, claimant=claimant)
    assert claimed is not None

    report, outcome = process_current_report(
        conn_factory, trade_id=trade_id, user_id=unique_user_id,
        market_tzinfo=_ET, market_timezone_name="America/New_York", outbox_id=outbox_id, claimed_by=claimant,
    )
    assert outcome == "CURRENT_REPORT_ALREADY_COMPLETE"
    assert report.id == first_report.id

    outbox_after = pg_conn.execute(
        "SELECT status, claimed_by, lease_expires_at FROM paper_trade_postmortem_outbox WHERE id = %s", (outbox_id,),
    ).fetchone()
    assert outbox_after[0] == first_report.status, (
        f"8C: settled outbox status={outbox_after[0]!r}, expected the report's own status {first_report.status!r}, "
        "never a hardcoded/invalid value."
    )
    assert outbox_after[1] is None, "8C: claimed_by must be cleared on successful settlement."
    assert outbox_after[2] is None, "8C: lease_expires_at must be cleared on successful settlement."


# ============================= Section 8D — market-local report date ============================= #

def test_8d_market_local_date_india_boundary(pg_conn, pg_database_url, unique_user_id):
    from services.postmortem.current_report_generation import process_current_report

    # 2026-01-02 23:30 UTC is already 2026-01-03 05:00 in Asia/Kolkata.
    closed_at = datetime(2026, 1, 2, 23, 30, tzinfo=timezone.utc)
    trade_id = _seed_trade(pg_conn, user_id=unique_user_id, market="IN", closed_at=closed_at)
    _seed_entry_snapshot(pg_conn, trade_id=trade_id, user_id=unique_user_id, market="IN",
                          user_selected_stop_loss=95.0, user_selected_target_price=110.0)
    _seed_exit_snapshot(pg_conn, trade_id=trade_id, user_id=unique_user_id, market="IN", closed_at=closed_at,
                         final_stop_loss=95.0, final_target_price=110.0, levels_modified=False)

    report, outcome = process_current_report(
        _make_conn_factory(pg_database_url), trade_id=trade_id, user_id=unique_user_id,
        market_tzinfo=_IST, market_timezone_name="Asia/Kolkata",
    )
    assert outcome == "CURRENT_REPORT_GENERATED"
    row = _current_report_row(pg_conn, trade_id)
    assert row is not None
    persisted_date = row[3]
    assert persisted_date == closed_at.astimezone(_IST).date(), (
        f"8D: persisted report_trading_date={persisted_date!r}, expected the Asia/Kolkata LOCAL date "
        f"{closed_at.astimezone(_IST).date()!r}, not the bare UTC date {closed_at.date()!r}."
    )
    assert persisted_date != closed_at.date(), "8D: setup sanity check — UTC and IST-local dates must genuinely differ here."
