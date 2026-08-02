"""
Wave B closure, Section 8K — real-PostgreSQL behavioral proof for
reset/delete safety and cross-user isolation around the 1.2.0 report/
outbox identity.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import psycopg
import pytest

pytestmark = pytest.mark.postgres_integration

_ET = ZoneInfo("America/New_York")


def _fake_none(*a, **k):
    return []


@pytest.fixture(autouse=True)
def _patch_price_path_provider(monkeypatch):
    from services.postmortem import price_path_generation
    from services.postmortem.price_path_acquisition import acquire_price_path_evidence as _real_acquire

    def _fake_acquire(*, fetch_bars_fn=None, fetch_splits_fn=None, fetch_dividends_fn=None, **kwargs):
        return _real_acquire(fetch_bars_fn=_fake_none, fetch_splits_fn=_fake_none, fetch_dividends_fn=_fake_none, **kwargs)

    monkeypatch.setattr(price_path_generation, "acquire_price_path_evidence", _fake_acquire)


def _make_conn_factory(pg_database_url):
    @contextmanager
    def _factory():
        with psycopg.connect(pg_database_url, autocommit=True) as conn:
            yield conn
    return _factory


def _seed_closed_trade(pg_conn, *, user_id, closed_at):
    trade_row = pg_conn.execute(
        """INSERT INTO paper_trades
           (session_id, user_id, symbol, market, quantity, entry_price, exit_price, stop_loss, target_price,
            status, trade_management_mode, opened_at, closed_at, exit_reason)
           VALUES (%s, %s, 'AAPL', 'US', 10, 100.0, 108.0, 95.0, 110.0, 'CLOSED', 'MANUAL', %s, %s, 'MANUAL')
           RETURNING id""",
        (user_id, user_id, closed_at, closed_at),
    ).fetchone()
    trade_id = trade_row[0]
    pg_conn.execute(
        """INSERT INTO paper_trade_entry_snapshot
           (paper_trade_id, user_id, symbol, market, snapshot_schema_version, evidence_source,
            simulated_execution_price, execution_range_position, user_selected_stop_loss, user_selected_target_price,
            user_overrode_recommendation, verification_levels, level_history_contract_version,
            initial_stop_modified_after_entry, initial_target_modified_after_entry,
            initial_levels_modified_after_entry)
           VALUES (%s, %s, 'AAPL', 'US', '1.0.0', 'DAILY_PICK', 100.0, 'WITHIN_RANGE', 95.0, 110.0, false,
                   '{}'::jsonb, '1.0.0', false, false, false)""",
        (trade_id, user_id),
    )
    pg_conn.execute(
        """INSERT INTO paper_trade_exit_snapshot
           (paper_trade_id, user_id, symbol, market, exit_snapshot_schema_version, financial_outcome,
            closure_classification, exit_mechanism, exit_mechanism_raw, exit_price, exit_quantity, closed_at,
            final_stop_loss, final_target_price, management_mode, levels_modified_after_entry,
            level_history_contract_version, final_stop_modified_after_entry, final_target_modified_after_entry)
           VALUES (%s, %s, 'AAPL', 'US', '1.0.0', 'PROFIT', 'MANUAL', 'MANUAL', 'MANUAL', 108.0, 10, %s,
                   95.0, 110.0, 'MANUAL', false, '1.0.0', false, false)""",
        (trade_id, user_id, closed_at),
    )
    return trade_id


def _reset_user(pg_conn, user_id):
    """Mirrors reset_portfolio's own generic user_id-scoped DELETE
    statements for the postmortem tables — proving THOSE statements,
    not a hand-rolled test-only equivalent, actually remove the 1.2.0
    rows."""
    pg_conn.execute("DELETE FROM paper_trade_price_path_evidence WHERE user_id = %s", (user_id,))
    pg_conn.execute("DELETE FROM paper_trade_postmortem_report WHERE user_id = %s", (user_id,))
    pg_conn.execute("DELETE FROM paper_trade_postmortem_outbox WHERE user_id = %s", (user_id,))
    pg_conn.execute("DELETE FROM paper_trade_exit_snapshot WHERE user_id = %s", (user_id,))
    pg_conn.execute("DELETE FROM paper_trade_entry_snapshot WHERE user_id = %s", (user_id,))
    pg_conn.execute("DELETE FROM paper_trades WHERE user_id = %s", (user_id,))


def test_8k_reset_after_1_2_0_report_exists_removes_it_without_orphans(pg_conn, pg_database_url, unique_user_id):
    from services.postmortem.current_report_generation import process_current_report

    closed_at = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    trade_id = _seed_closed_trade(pg_conn, user_id=unique_user_id, closed_at=closed_at)
    report, outcome = process_current_report(
        _make_conn_factory(pg_database_url), trade_id=trade_id, user_id=unique_user_id,
        market_tzinfo=_ET, market_timezone_name="America/New_York",
    )
    assert outcome == "CURRENT_REPORT_GENERATED"

    _reset_user(pg_conn, unique_user_id)

    remaining_report = pg_conn.execute(
        "SELECT count(*) FROM paper_trade_postmortem_report WHERE user_id = %s", (unique_user_id,),
    ).fetchone()[0]
    remaining_trade = pg_conn.execute(
        "SELECT count(*) FROM paper_trades WHERE user_id = %s", (unique_user_id,),
    ).fetchone()[0]
    assert remaining_report == 0, "8K: reset must remove the persisted 1.2.0 report row."
    assert remaining_trade == 0, "8K: reset must remove the trade row too."


def test_8k_cross_user_get_current_report_never_returns_another_users_report(pg_conn, pg_database_url, unique_user_id):
    from services.postmortem.current_report_generation import process_current_report
    from services.postmortem import report_store

    closed_at = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    trade_id = _seed_closed_trade(pg_conn, user_id=unique_user_id, closed_at=closed_at)
    report, outcome = process_current_report(
        _make_conn_factory(pg_database_url), trade_id=trade_id, user_id=unique_user_id,
        market_tzinfo=_ET, market_timezone_name="America/New_York",
    )
    assert outcome == "CURRENT_REPORT_GENERATED"

    attacker_id = f"{unique_user_id}-attacker"
    looked_up = report_store.get_current_report(
        pg_conn, paper_trade_id=trade_id, user_id=attacker_id,
        report_schema_version=report.report_schema_version, calculation_version=report.calculation_version,
        attribution_rules_version=report.attribution_rules_version,
    )
    assert looked_up is None, "8K: a different user_id must never retrieve another user's persisted report."

    genuine = report_store.get_current_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id,
        report_schema_version=report.report_schema_version, calculation_version=report.calculation_version,
        attribution_rules_version=report.attribution_rules_version,
    )
    assert genuine is not None and genuine.id == report.id


def test_8k_reset_does_not_touch_a_different_users_report(pg_conn, pg_database_url, unique_user_id):
    from services.postmortem.current_report_generation import process_current_report

    closed_at = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    victim_id = f"{unique_user_id}-victim"
    attacker_id = f"{unique_user_id}-attacker"

    victim_trade_id = _seed_closed_trade(pg_conn, user_id=victim_id, closed_at=closed_at)
    victim_report, victim_outcome = process_current_report(
        _make_conn_factory(pg_database_url), trade_id=victim_trade_id, user_id=victim_id,
        market_tzinfo=_ET, market_timezone_name="America/New_York",
    )
    assert victim_outcome == "CURRENT_REPORT_GENERATED"

    attacker_trade_id = _seed_closed_trade(pg_conn, user_id=attacker_id, closed_at=closed_at)
    attacker_report, attacker_outcome = process_current_report(
        _make_conn_factory(pg_database_url), trade_id=attacker_trade_id, user_id=attacker_id,
        market_tzinfo=_ET, market_timezone_name="America/New_York",
    )
    assert attacker_outcome == "CURRENT_REPORT_GENERATED"

    _reset_user(pg_conn, attacker_id)

    victim_report_still_exists = pg_conn.execute(
        "SELECT count(*) FROM paper_trade_postmortem_report WHERE id = %s", (victim_report.id,),
    ).fetchone()[0]
    assert victim_report_still_exists == 1, "8K: resetting the attacker's data must not touch the victim's report."
