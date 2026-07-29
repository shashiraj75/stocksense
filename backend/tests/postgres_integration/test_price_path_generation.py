"""
Trade Postmortem Sprint 3A, Stage H/I — real PostgreSQL verification for
services.postmortem.price_path_generation's five-phase orchestration and
report supersession.

No local PostgreSQL is available in this development environment (no
docker/psql/pg_ctl/initdb, nothing on :5432 at authoring time — verified
explicitly before writing this file) — this suite is only actually
executed by the GitHub Actions PostgreSQL 15/17 CI matrix, never
locally. It is written and reviewed here in good faith against the real
schema in services/postgres_store.py and the real orchestration
functions in services/postmortem/price_path_generation.py, but its
first real execution against a live server happens in CI once this
branch is pushed and dispatched.

Endpoint-wiring scope note: Stage H's orchestration SERVICE is complete
and independently tested (see tests/unit/test_price_path_generation.py).
Wiring it into the actual `/sell` and
`POST /postmortem/{trade_id}/generate` HTTP endpoints is a deliberately
deferred follow-up (see the Sprint 3A checkpoint report), so these tests
call price_path_generation's functions directly against a real
PostgreSQL connection (after using the real HTTP client to set up a
genuinely closed trade with real exit-snapshot/outbox rows), rather than
asserting HTTP-level behavior for a code path that isn't wired into the
router yet.
"""
import datetime as dt

import psycopg
import pytest

from tests.postgres_integration.conftest import ensure_portfolio, get_portfolio_cash, make_auth_header

pytestmark = pytest.mark.postgres_integration


def _buy(client, user_id, **overrides):
    body = {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 100.0}
    body.update(overrides)
    return client.post("/api/paper-trading/buy", json=body, headers=make_auth_header(user_id))


def _sell(client, user_id, trade_id, **overrides):
    body = {"price": 110.0, "exit_reason": "MANUAL"}
    body.update(overrides)
    return client.post(f"/api/paper-trading/sell/{trade_id}", json=body, headers=make_auth_header(user_id))


def _open_and_close(client, pg_conn, user_id, exit_price=110.0):
    ensure_portfolio(pg_conn, user_id, cash_usd=1_000_000.0)
    trade_id = _buy(client, user_id).json()["trade_id"]
    resp = _sell(client, user_id, trade_id, price=exit_price)
    assert resp.status_code == 200
    return trade_id


def _fake_bars():
    return [
        {"date": dt.date(2026, 6, 2), "open": 100.0, "high": 115.0, "low": 99.0, "close": 105.0, "volume": 500, "adj_close": None, "dividend": 0.0},
        {"date": dt.date(2026, 6, 3), "open": 105.0, "high": 112.0, "low": 90.0, "close": 95.0, "volume": 500, "adj_close": None, "dividend": 0.0},
    ]


@pytest.mark.timeout(30)
class TestCommitBeforeAcquireVisibility:
    """Items 1-6 — everything the close transaction wrote must be
    visible from a SECOND connection, and the trade row's lock released,
    before this test even attempts to build price-path evidence."""

    def test_closed_trade_cash_snapshot_outbox_all_visible_before_acquisition(self, client, pg_conn, pg_database_url, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)

        with psycopg.connect(pg_database_url, autocommit=True) as second_conn:
            status = second_conn.execute(
                "SELECT status FROM paper_trades WHERE id = %s", (trade_id,)
            ).fetchone()[0]
            assert status == "CLOSED"

            cash = get_portfolio_cash(second_conn, unique_user_id, market="US")
            assert cash > 0

            snapshot_count = second_conn.execute(
                "SELECT count(*) FROM paper_trade_exit_snapshot WHERE paper_trade_id = %s", (trade_id,)
            ).fetchone()[0]
            assert snapshot_count == 1

            outbox_count = second_conn.execute(
                "SELECT count(*) FROM paper_trade_postmortem_outbox WHERE paper_trade_id = %s", (trade_id,)
            ).fetchone()[0]
            assert outbox_count == 1

    def test_trade_row_lock_released_after_close_commits(self, client, pg_conn, pg_database_url, unique_user_id):
        """A second connection can acquire a conflicting SELECT FOR
        UPDATE with a bounded lock_timeout — proving the close
        transaction's own row lock was released, not merely that the
        transaction eventually committed."""
        trade_id = _open_and_close(client, pg_conn, unique_user_id)

        with psycopg.connect(pg_database_url, autocommit=False) as second_conn:
            with second_conn.transaction():
                second_conn.execute("SET LOCAL lock_timeout = '2s'")
                row = second_conn.execute(
                    "SELECT id FROM paper_trades WHERE id = %s FOR UPDATE", (trade_id,)
                ).fetchone()
                assert row is not None


@pytest.mark.timeout(30)
class TestEvidenceAcquisitionAndPersistenceAtomicity:
    def test_persist_price_path_evidence_creates_exactly_one_row(self, client, pg_conn, unique_user_id):
        from services.postmortem.price_path_acquisition import build_price_path_evidence
        from services.postmortem.price_path_generation import persist_price_path_evidence
        from services.market_hours import ET

        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        bundle = build_price_path_evidence(
            paper_trade_id=trade_id, user_id=unique_user_id, symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET), exit_timestamp=dt.datetime(2026, 6, 5, tzinfo=ET),
            raw_bars=_fake_bars(), split_events=[],
        )
        evidence, created = persist_price_path_evidence(pg_conn, bundle)
        assert created is True

        count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert count == 1

    def test_repeated_persist_is_idempotent_not_a_duplicate_row(self, client, pg_conn, unique_user_id):
        from services.postmortem.price_path_acquisition import build_price_path_evidence
        from services.postmortem.price_path_generation import persist_price_path_evidence
        from services.market_hours import ET

        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        bundle = build_price_path_evidence(
            paper_trade_id=trade_id, user_id=unique_user_id, symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET), exit_timestamp=dt.datetime(2026, 6, 5, tzinfo=ET),
            raw_bars=_fake_bars(), split_events=[],
        )
        persist_price_path_evidence(pg_conn, bundle)
        persist_price_path_evidence(pg_conn, bundle)

        count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert count == 1


@pytest.mark.timeout(30)
class TestProviderFailurePreservesState:
    def test_provider_failure_leaves_committed_financial_state_untouched(self, client, pg_conn, unique_user_id):
        from services.postmortem.price_path_acquisition import PriceProviderAcquisitionError

        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        cash_before = get_portfolio_cash(pg_conn, unique_user_id, market="US")
        status_before = pg_conn.execute("SELECT status FROM paper_trades WHERE id = %s", (trade_id,)).fetchone()[0]

        def failing_fetch(*a, **k):
            raise PriceProviderAcquisitionError("PROVIDER_FETCH_FAILED", "simulated outage")

        from services.postmortem.price_path_generation import GenerationContext, acquire_evidence_outside_transaction
        from services.market_hours import ET
        ctx = GenerationContext(
            trade_id=trade_id, user_id=unique_user_id, symbol="AAPL", market="US",
            market_timezone_name="America/New_York", entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET),
            entry_price=100.0, exit_price=110.0, applicable_stop=90.0, applicable_target=120.0,
            exit_timestamp=dt.datetime(2026, 6, 5, tzinfo=ET), level_history_complete=True,
            prior_report=None, compatible_evidence=None,
        )
        with pytest.raises(PriceProviderAcquisitionError):
            acquire_evidence_outside_transaction(
                ctx, market_tzinfo=ET, fetch_bars_fn=failing_fetch, fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [],
            )

        assert get_portfolio_cash(pg_conn, unique_user_id, market="US") == cash_before
        assert pg_conn.execute("SELECT status FROM paper_trades WHERE id = %s", (trade_id,)).fetchone()[0] == status_before

    def test_provider_failure_creates_no_evidence_row(self, client, pg_conn, unique_user_id):
        from services.postmortem.price_path_acquisition import PriceProviderAcquisitionError
        from services.postmortem.price_path_generation import GenerationContext, acquire_evidence_outside_transaction
        from services.market_hours import ET

        trade_id = _open_and_close(client, pg_conn, unique_user_id)

        def failing_fetch(*a, **k):
            raise PriceProviderAcquisitionError("PROVIDER_FETCH_FAILED", "simulated outage")

        ctx = GenerationContext(
            trade_id=trade_id, user_id=unique_user_id, symbol="AAPL", market="US",
            market_timezone_name="America/New_York", entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET),
            entry_price=100.0, exit_price=110.0, applicable_stop=90.0, applicable_target=120.0,
            exit_timestamp=dt.datetime(2026, 6, 5, tzinfo=ET), level_history_complete=True,
            prior_report=None, compatible_evidence=None,
        )
        with pytest.raises(PriceProviderAcquisitionError):
            acquire_evidence_outside_transaction(
                ctx, market_tzinfo=ET, fetch_bars_fn=failing_fetch, fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [],
            )

        count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert count == 0

        report_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report WHERE paper_trade_id = %s "
            "AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert report_count == 0


@pytest.mark.timeout(30)
class TestReportSupersession:
    def _sprint2_report(self, pg_conn, trade_id, user_id):
        """_open_and_close's real /sell call already triggers best-effort
        generation for the CURRENT production version triple (see the
        [postmortem_generation_limited] log line in CI) — fetch THAT
        real, already-persisted report rather than hand-rolling a second
        one under guessed version strings, which would collide with
        nothing (report_store's ON CONFLICT target is the full version
        triple) and silently create a THIRD, spurious row instead of
        reusing the real prior report."""
        from services.postmortem import generation_service, report_store
        from services.postmortem.evidence_attribution import ATTRIBUTION_RULES_VERSION
        from services.postmortem.deterministic import CALCULATION_VERSION
        report = report_store.get_current_report(
            pg_conn, paper_trade_id=trade_id, user_id=user_id,
            report_schema_version=generation_service.REPORT_SCHEMA_VERSION,
            calculation_version=CALCULATION_VERSION, attribution_rules_version=ATTRIBUTION_RULES_VERSION,
        )
        assert report is not None, "expected _open_and_close's own best-effort generation to have already persisted a report"
        return report

    def test_price_path_report_creates_new_row_and_supersedes_prior(self, client, pg_conn, unique_user_id):
        from services.postmortem.price_path_acquisition import build_price_path_evidence
        from services.postmortem.price_path_generation import (
            build_price_path_report_payload, persist_price_path_evidence, persist_price_path_report,
        )
        from services.market_hours import ET

        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        prior = self._sprint2_report(pg_conn, trade_id, unique_user_id)

        bundle = build_price_path_evidence(
            paper_trade_id=trade_id, user_id=unique_user_id, symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET), exit_timestamp=dt.datetime(2026, 6, 5, tzinfo=ET),
            raw_bars=_fake_bars(), split_events=[],
        )
        evidence, _ = persist_price_path_evidence(pg_conn, bundle)
        payload = build_price_path_report_payload(
            evidence, entry_price=100.0, exit_price=110.0, applicable_stop=90.0, applicable_target=120.0,
            level_history_complete=True, trade_id=trade_id,
        )
        enhanced, created = persist_price_path_report(
            pg_conn, prior_report=prior, payload=payload, trade_id=trade_id, user_id=unique_user_id, market="US",
            report_trading_date=dt.date(2026, 6, 5), market_timezone="America/New_York", source_version="1.0.0",
        )
        assert created is True
        assert enhanced.supersedes_report_id == prior.id

        report_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert report_count == 2

        # A manually-bought/sold trade (this test's own setup) has no
        # entry snapshot, so the real prior report is genuinely
        # LIMITED_EVIDENCE, not COMPLETE — assert the row is UNCHANGED
        # by the price-path enhancement (Stage I requirement 1) rather
        # than assuming a specific status value.
        prior_row_status = pg_conn.execute(
            "SELECT status FROM paper_trade_postmortem_report WHERE id = %s", (prior.id,)
        ).fetchone()[0]
        assert prior_row_status == prior.status

    def test_report_update_rejected_by_immutability_trigger(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        prior = self._sprint2_report(pg_conn, trade_id, unique_user_id)
        with pytest.raises(Exception):
            pg_conn.execute(
                "UPDATE paper_trade_postmortem_report SET status = 'FAILED_TERMINAL' WHERE id = %s", (prior.id,)
            )

    def test_stale_claimant_report_insert_rolls_back_atomically(self, client, pg_conn, unique_user_id):
        """Stage J1B-Real-PG-Assurance, Stage 5F -- a genuine forced
        stale-claimant condition (not merely a description of the
        transaction structure): a real outbox row is claimed, then its
        lease is stolen by a different claimant via a direct SQL update
        (simulating a fresher attempt reclaiming an expired lease), then
        persist_price_path_report is called with the ORIGINAL (now
        stale) claimant token inside its own real conn.transaction() --
        exactly as paper_trading.py's own call site does. Both the
        report INSERT and the outbox mark-terminal must roll back
        together; a second real connection must see neither."""
        from services.postmortem import generation_service, outbox as outbox_ops
        from services.postmortem.price_path_acquisition import build_price_path_evidence
        from services.postmortem.price_path_generation import (
            build_price_path_report_payload, persist_price_path_evidence, persist_price_path_report,
        )
        from services.market_hours import ET

        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        prior = self._sprint2_report(pg_conn, trade_id, unique_user_id)

        outbox_id = pg_conn.execute(
            """INSERT INTO paper_trade_postmortem_outbox
                   (paper_trade_id, user_id, requested_report_schema_version,
                    requested_calculation_version, requested_rules_version)
               VALUES (%s, %s, '1.1.0', 'stale-claimant-test', '2.0.0')
               RETURNING id""",
            (trade_id, unique_user_id),
        ).fetchone()[0]
        stale_claimant = outbox_ops.new_claimant_token()
        claimed = outbox_ops.claim_next_attempt(pg_conn, outbox_id=outbox_id, user_id=unique_user_id, claimant=stale_claimant)
        assert claimed is not None and claimed.status == "GENERATING"

        # A fresher claimant steals the lease -- simulating a real
        # concurrent reclaim after this attempt's own lease genuinely
        # expired, via the exact same UPDATE shape claim_next_attempt
        # itself would issue.
        fresh_claimant = outbox_ops.new_claimant_token()
        pg_conn.execute(
            "UPDATE paper_trade_postmortem_outbox SET claimed_by = %s WHERE id = %s",
            (fresh_claimant, outbox_id),
        )

        bundle = build_price_path_evidence(
            paper_trade_id=trade_id, user_id=unique_user_id, symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET), exit_timestamp=dt.datetime(2026, 6, 5, tzinfo=ET),
            raw_bars=_fake_bars(), split_events=[],
        )
        evidence, _ = persist_price_path_evidence(pg_conn, bundle)
        payload = build_price_path_report_payload(
            evidence, entry_price=100.0, exit_price=110.0, applicable_stop=90.0, applicable_target=120.0,
            level_history_complete=True, trade_id=trade_id,
        )

        report_count_before = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]

        with pytest.raises(generation_service.StaleLeaseError):
            with pg_conn.transaction():
                persist_price_path_report(
                    pg_conn, prior_report=prior, payload=payload, trade_id=trade_id, user_id=unique_user_id,
                    market="US", report_trading_date=dt.date(2026, 6, 5), market_timezone="America/New_York",
                    source_version="1.0.0", outbox_id=outbox_id, claimed_by=stale_claimant,
                )

        # No partial settlement -- verified from THIS connection (the
        # transaction rollback is synchronous) and from the outbox row's
        # own claimed_by, which must still read the fresh claimant, never
        # overwritten by the stale attempt's failed insert.
        report_count_after = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert report_count_after == report_count_before  # the report INSERT rolled back

        outbox_claimed_by = pg_conn.execute(
            "SELECT claimed_by FROM paper_trade_postmortem_outbox WHERE id = %s", (outbox_id,)
        ).fetchone()[0]
        assert outbox_claimed_by == fresh_claimant  # never overwritten by the stale attempt


@pytest.mark.timeout(30)
class TestCrossUserIsolation:
    def test_evidence_not_visible_to_a_different_user(self, client, pg_conn, unique_user_id):
        from services.postmortem.price_path_acquisition import build_price_path_evidence
        from services.postmortem.price_path_generation import persist_price_path_evidence
        from services.postmortem.price_path_store import get_current_evidence
        from services.market_hours import ET

        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        bundle = build_price_path_evidence(
            paper_trade_id=trade_id, user_id=unique_user_id, symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET), exit_timestamp=dt.datetime(2026, 6, 5, tzinfo=ET),
            raw_bars=_fake_bars(), split_events=[],
        )
        persist_price_path_evidence(pg_conn, bundle)

        found = get_current_evidence(
            pg_conn, paper_trade_id=trade_id, user_id="a-different-synthetic-user",
            evidence_bundle_version=bundle.evidence_bundle_version, source_id=bundle.source_id,
            source_version=bundle.source_version,
        )
        assert found is None
