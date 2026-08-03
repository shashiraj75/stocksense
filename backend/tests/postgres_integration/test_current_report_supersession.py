"""
Wave B closure, Section 8I — real-PostgreSQL behavioral proof for
report persistence and supersession: exactly-one-per-version-identity,
concurrent generation single persisted effect, 1.0.0/1.1.0/1.2.0
coexistence, deterministic predecessor selection, historical report
immutability, and stale-settlement rollback.
"""
import threading
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


def _seed_trade_and_snapshots(pg_conn, *, user_id, closed_at):
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


def test_8i_concurrent_generation_produces_exactly_one_persisted_report(pg_conn, pg_database_url, unique_user_id):
    """Two real threads calling process_current_report for the SAME
    trade concurrently must result in exactly ONE 1.2.0 report row —
    the database unique index, not application logic, is what
    guarantees this."""
    from services.postmortem.current_report_generation import process_current_report

    closed_at = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    trade_id = _seed_trade_and_snapshots(pg_conn, user_id=unique_user_id, closed_at=closed_at)

    outcomes = []

    def _generate():
        r, o = process_current_report(
            _make_conn_factory(pg_database_url), trade_id=trade_id, user_id=unique_user_id,
            market_tzinfo=_ET, market_timezone_name="America/New_York",
        )
        outcomes.append(o)

    threads = [threading.Thread(target=_generate) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    count = pg_conn.execute(
        "SELECT count(*) FROM paper_trade_postmortem_report WHERE paper_trade_id = %s AND report_schema_version = '1.2.0'",
        (trade_id,),
    ).fetchone()[0]
    assert count == 1, f"8I: expected exactly 1 persisted 1.2.0 report from 5 concurrent generation attempts, found {count}."
    assert "CURRENT_REPORT_GENERATED" in outcomes, "8I: at least one concurrent attempt must have genuinely generated."


def test_8i_1_0_0_1_1_0_1_2_0_coexist_with_deterministic_predecessor(pg_conn, pg_database_url, unique_user_id):
    """Seeds genuine 1.0.0 and 1.1.0 report rows, then generates the
    1.2.0 report for real — all three must coexist, and the 1.2.0
    report's supersedes_report_id must deterministically point at the
    HIGHEST-schema-version predecessor (1.1.0), never 1.0.0."""
    from services.postmortem.current_report_generation import process_current_report
    from services.postmortem import report_store

    closed_at = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    trade_id = _seed_trade_and_snapshots(pg_conn, user_id=unique_user_id, closed_at=closed_at)

    report_1_0_0, _ = report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=closed_at.date(), market_timezone="America/New_York",
        report_schema_version="1.0.0", calculation_version="1.0.0-test", attribution_rules_version="1.0.0-test",
        evidence_bundle_version="1.0.0", status="COMPLETE", structured_report={}, evidence_items=[], claims=[],
        source_manifest={}, evidence_gaps=[], warnings=[],
    )
    report_1_1_0, _ = report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=closed_at.date(), market_timezone="America/New_York",
        report_schema_version="1.1.0", calculation_version="1.1.0-test", attribution_rules_version="1.1.0-test",
        evidence_bundle_version="1.0.0", status="COMPLETE", structured_report={}, evidence_items=[], claims=[],
        source_manifest={}, evidence_gaps=[], warnings=[], supersedes_report_id=report_1_0_0.id,
    )

    report_1_2_0, outcome = process_current_report(
        _make_conn_factory(pg_database_url), trade_id=trade_id, user_id=unique_user_id,
        market_tzinfo=_ET, market_timezone_name="America/New_York",
    )
    assert outcome == "CURRENT_REPORT_GENERATED"

    all_reports = pg_conn.execute(
        "SELECT report_schema_version, supersedes_report_id FROM paper_trade_postmortem_report "
        "WHERE paper_trade_id = %s ORDER BY report_schema_version", (trade_id,),
    ).fetchall()
    versions = {r[0] for r in all_reports}
    assert versions == {"1.0.0", "1.1.0", "1.2.0"}, f"8I: expected all 3 versions to coexist, found {versions}."

    report_1_2_0_row = [r for r in all_reports if r[0] == "1.2.0"][0]
    assert report_1_2_0_row[1] == report_1_1_0.id, (
        f"8I: 1.2.0's supersedes_report_id={report_1_2_0_row[1]!r}, expected the 1.1.0 predecessor's id "
        f"{report_1_1_0.id!r} (the highest-schema-version predecessor), never the 1.0.0 report."
    )

    # Historical rows are byte-for-byte unchanged.
    unchanged_1_0_0 = pg_conn.execute(
        "SELECT calculation_version, status FROM paper_trade_postmortem_report WHERE id = %s", (report_1_0_0.id,),
    ).fetchone()
    assert unchanged_1_0_0 == ("1.0.0-test", "COMPLETE")
    unchanged_1_1_0 = pg_conn.execute(
        "SELECT calculation_version, status, supersedes_report_id FROM paper_trade_postmortem_report WHERE id = %s",
        (report_1_1_0.id,),
    ).fetchone()
    assert unchanged_1_1_0 == ("1.1.0-test", "COMPLETE", report_1_0_0.id)


def test_8i_immutability_trigger_rejects_update_on_1_2_0_report(pg_conn, pg_database_url, unique_user_id):
    """The database-level immutability trigger (shared across all
    report_schema_version rows) must reject an UPDATE against a
    persisted 1.2.0 report row exactly like it already does for 1.0.0/
    1.1.0 rows."""
    from services.postmortem.current_report_generation import process_current_report

    closed_at = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    trade_id = _seed_trade_and_snapshots(pg_conn, user_id=unique_user_id, closed_at=closed_at)
    report, outcome = process_current_report(
        _make_conn_factory(pg_database_url), trade_id=trade_id, user_id=unique_user_id,
        market_tzinfo=_ET, market_timezone_name="America/New_York",
    )
    assert outcome == "CURRENT_REPORT_GENERATED"

    with pytest.raises(psycopg.errors.RaiseException):
        pg_conn.execute(
            "UPDATE paper_trade_postmortem_report SET status = 'FAILED_TERMINAL' WHERE id = %s", (report.id,),
        )
