"""
Trade Postmortem Sprint 3A, Stage J10 — dedicated real-PostgreSQL
historical-compatibility tests.

These specifically exercise the Stage J2/J3 snapshot-validity and
completeness-ceiling governance against REAL entry/exit snapshot rows
(deleted after a real buy/sell to simulate a historical trade that
predates Sprint 2's durability layer, or mutated to simulate a
present-but-invalid row) — not the fake-conn simulations in
tests/regression/test_paper_trading_price_path_lease_lifecycle.py,
and not merely re-asserting what the Stage A endpoint suite in
test_price_path_endpoint_lifecycle.py already covers (cross-user
isolation, idempotent replay, reset cleanup are already proven there
and are not duplicated here).
"""
import datetime as dt

import pytest

from tests.postgres_integration.conftest import make_auth_header

pytestmark = pytest.mark.postgres_integration


def _fake_bars(*a, **k):
    return [
        {"date": dt.date(2026, 6, 2), "open": 100.0, "high": 115.0, "low": 99.0, "close": 105.0, "volume": 500, "adj_close": None, "dividend": 0.0},
        {"date": dt.date(2026, 6, 3), "open": 105.0, "high": 112.0, "low": 90.0, "close": 95.0, "volume": 500, "adj_close": None, "dividend": 0.0},
    ]


def _fake_none(*a, **k):
    return []


@pytest.fixture(autouse=True)
def _patch_price_path_provider(monkeypatch):
    from services.postmortem import price_path_acquisition
    monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _fake_bars)
    monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _fake_none)
    monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _fake_none)


@pytest.fixture(autouse=True)
def _enable_price_path_flag(monkeypatch):
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")


def _buy(client, user_id, **overrides):
    body = {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 100.0}
    body.update(overrides)
    return client.post("/api/paper-trading/buy", json=body, headers=make_auth_header(user_id))


def _sell(client, user_id, trade_id, **overrides):
    body = {"price": 110.0, "exit_reason": "MANUAL"}
    body.update(overrides)
    return client.post(f"/api/paper-trading/sell/{trade_id}", json=body, headers=make_auth_header(user_id))


def _generate(client, user_id, trade_id):
    return client.post(f"/api/paper-trading/postmortem/{trade_id}/generate", headers=make_auth_header(user_id))


def _open_and_close(client, pg_conn, user_id, exit_price=110.0):
    from tests.postgres_integration.conftest import ensure_portfolio
    ensure_portfolio(pg_conn, user_id, cash_usd=1_000_000.0)
    trade_id = _buy(client, user_id).json()["trade_id"]
    resp = _sell(client, user_id, trade_id, price=exit_price)
    assert resp.status_code == 200
    return trade_id


@pytest.mark.timeout(30)
class TestMissingSnapshotCapsReportAtLimitedEvidence:
    def test_missing_entry_snapshot_produces_limited_evidence(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        pg_conn.execute("DELETE FROM paper_trade_entry_snapshot WHERE paper_trade_id = %s", (trade_id,))

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "LIMITED_EVIDENCE"

    def test_missing_exit_snapshot_produces_limited_evidence(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        pg_conn.execute("DELETE FROM paper_trade_exit_snapshot WHERE paper_trade_id = %s", (trade_id,))

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["status"] == "LIMITED_EVIDENCE"

    def test_invalid_entry_snapshot_market_mismatch_produces_limited_evidence(self, client, pg_conn, unique_user_id):
        """A present-but-invalid row (wrong market — simulating a
        corrupted or cross-context row) must never be used, and must
        still cap the ceiling exactly like a missing row.

        paper_trade_entry_snapshot rows are immutable at the database
        level (a genuine Sprint 2 hardening trigger rejects UPDATE) --
        the only way to substitute an invalid row is DELETE + re-INSERT
        with the same column values except market, using the real row's
        own data so every NOT NULL column stays populated."""
        trade_id = _open_and_close(client, pg_conn, unique_user_id)

        from psycopg.types.json import Jsonb

        with pg_conn.cursor() as cur:
            cur.execute("SELECT * FROM paper_trade_entry_snapshot WHERE paper_trade_id = %s", (trade_id,))
            columns = [d.name for d in cur.description]
            row = dict(zip(columns, cur.fetchone()))
        row["market"] = "IN" if row["market"] == "US" else "US"
        insert_columns = [c for c in columns if c not in ("id", "created_at")]
        # verification_levels/recommendation_reasoning are JSONB and come
        # back from psycopg as plain dicts — re-inserting a bare dict
        # against a %s placeholder has no adapter; Jsonb() wraps it.
        values = tuple(
            Jsonb(row[c]) if isinstance(row[c], dict) else row[c] for c in insert_columns
        )
        pg_conn.execute("DELETE FROM paper_trade_entry_snapshot WHERE paper_trade_id = %s", (trade_id,))
        pg_conn.execute(
            f"INSERT INTO paper_trade_entry_snapshot ({', '.join(insert_columns)}) "
            f"VALUES ({', '.join('%s' for _ in insert_columns)})",
            values,
        )

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["status"] == "LIMITED_EVIDENCE"


@pytest.mark.timeout(30)
class TestMissingExitPriceCapsCompleteness:
    def test_missing_exit_price_caps_report_at_limited_evidence(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        pg_conn.execute("UPDATE paper_trades SET exit_price = NULL WHERE id = %s", (trade_id,))

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["status"] == "LIMITED_EVIDENCE"


@pytest.mark.timeout(30)
class TestHistoricalGenerationNeverMutatesSourceRows:
    def test_generation_does_not_modify_trade_entry_or_exit_snapshot_rows(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)

        trade_before = pg_conn.execute(
            "SELECT symbol, market, entry_price, exit_price, status, opened_at, closed_at "
            "FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()
        entry_before = pg_conn.execute(
            "SELECT simulated_execution_price, captured_at FROM paper_trade_entry_snapshot "
            "WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()
        exit_before = pg_conn.execute(
            "SELECT * FROM paper_trade_exit_snapshot WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200

        trade_after = pg_conn.execute(
            "SELECT symbol, market, entry_price, exit_price, status, opened_at, closed_at "
            "FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()
        entry_after = pg_conn.execute(
            "SELECT simulated_execution_price, captured_at FROM paper_trade_entry_snapshot "
            "WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()
        exit_after = pg_conn.execute(
            "SELECT * FROM paper_trade_exit_snapshot WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()

        assert trade_before == trade_after
        assert entry_before == entry_after
        assert exit_before == exit_after


@pytest.mark.timeout(30)
class TestZeroBarResultNeverFabricatesAnalytics:
    """Stage J1B-Fail-Closed-Hardening, Stage 4/10 -- CALCULATION_
    UNAVAILABLE must prevent the actual MFE/MAE/touch calculator from
    ever running, not merely be a field computed and then ignored. A
    zero-bar provider response still produces a real, persisted,
    LIMITED_EVIDENCE report (an honest manifest-only outcome), but every
    analytic field must be null -- never zero, never fabricated."""

    def test_zero_bars_produces_limited_report_with_all_null_analytics(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        from services.postmortem import price_path_acquisition
        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _fake_none)

        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "LIMITED_EVIDENCE"

        structured_report = pg_conn.execute(
            "SELECT structured_report FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        section = structured_report["price_path"]
        assert section["price_path_status"] == "SOURCE_UNAVAILABLE"
        for field in ("mfe_abs", "mfe_pct", "mae_signed_abs", "mae_signed_pct",
                      "mae_magnitude_abs", "mae_magnitude_pct", "target_touch", "stop_touch", "touch_order"):
            assert section[field] is None, f"{field} must be null, never a fabricated zero/value"
        assert structured_report["price_path"]["evidence_quality_decision"]["calculation_status"] == "CALCULATION_UNAVAILABLE"

        # The zero-bar bundle is still an honest, immutable evidence
        # record (never fabricated bars) -- Stage 3's own explicit
        # persistence-permitted decision for SOURCE_UNAVAILABLE.
        evidence_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert evidence_count == 1

        outbox_status = pg_conn.execute(
            "SELECT status FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert outbox_status == "LIMITED_EVIDENCE"


@pytest.mark.timeout(30)
class TestTrueEvidenceReplayWithoutAnyReport:
    """Stage J1B/Stage 8 — the load-bearing proof that PERSISTED
    EVIDENCE (not an already-complete REPORT) can be replayed to
    CONSTRUCT a missing report. TestCompatibleReplaySurvivesProviderOutage
    below proves report replay (an immutable report already exists);
    this test proves the materially different evidence-replay path: a
    compatible evidence bundle exists, NO price-path report exists yet,
    and the live /generate endpoint must still construct one without
    ever touching the provider."""

    def test_generate_constructs_report_from_persisted_evidence_with_zero_provider_calls(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        from services.market_hours import ET
        from services.postmortem import price_path_acquisition, price_path_generation, price_path_store

        # Price-path disabled during /sell -- the trade closes under
        # Sprint 2 only, exactly the "historical trade predating the
        # price-path evidence layer" shape Stage J exists to handle.
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)

        entry_ts, exit_ts = pg_conn.execute(
            "SELECT opened_at, closed_at FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()

        # Build one real evidence bundle via the actual acquisition
        # function and persist it directly -- simulating evidence that
        # was captured by a prior attempt, independent of any report
        # ever being constructed from it. Bars must be dated within the
        # REAL entry/exit window (module-level _fake_bars is hardcoded
        # to 2026-06-02/03 and would be filtered out entirely by
        # build_price_path_evidence's own entry_date/exit_date bounds
        # check, silently downgrading the bundle to SOURCE_UNAVAILABLE
        # -- caught by real PG CI when the calculation_status assertion
        # below was added).
        entry_date, exit_date = entry_ts.astimezone(ET).date(), exit_ts.astimezone(ET).date()

        def _bars_in_window(*a, **k):
            return [
                {"date": entry_date, "open": 100.0, "high": 115.0, "low": 99.0, "close": 105.0,
                 "volume": 500, "adj_close": None, "dividend": 0.0},
                {"date": exit_date, "open": 105.0, "high": 112.0, "low": 90.0, "close": 95.0,
                 "volume": 500, "adj_close": None, "dividend": 0.0},
            ]

        bundle = price_path_acquisition.acquire_price_path_evidence(
            paper_trade_id=trade_id, user_id=unique_user_id, symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=entry_ts, exit_timestamp=exit_ts,
            fetch_bars_fn=_bars_in_window, fetch_splits_fn=_fake_none, fetch_dividends_fn=_fake_none,
        )
        persisted_evidence, created = price_path_store.persist_evidence(pg_conn, bundle)
        assert created is True

        evidence_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert evidence_count == 1
        report_count_before = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert report_count_before == 0

        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        def _raises(*a, **k):
            raise AssertionError("provider function must not be called on a genuine evidence-replay path")

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _raises)

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        body = resp.json()
        assert body["price_path_generation_status"] == "PRICE_PATH_GENERATED"

        evidence_count_after = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert evidence_count_after == 1  # no second evidence row created

        report_row = pg_conn.execute(
            "SELECT id, status, structured_report FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()
        assert report_row is not None
        report_id, report_status, structured_report = report_row
        assert structured_report["price_path"]["price_path_evidence_id"] == persisted_evidence.id

        # Stage J1B-Fail-Closed-Hardening — acquisition provenance must
        # truthfully record that NO provider call occurred for this
        # report, not merely that the endpoint happened to succeed.
        acquisition = structured_report["price_path"]["acquisition_decision"]
        assert acquisition["acquisition_status"] == "COMPATIBLE_REPLAY"
        assert acquisition["provider_call_expected"] is False
        assert acquisition["reused_evidence_id"] == persisted_evidence.id
        assert structured_report["price_path"]["evidence_quality_decision"]["calculation_status"] == "CALCULATION_ELIGIBLE"

        outbox_status = pg_conn.execute(
            "SELECT status FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert outbox_status == report_status  # Stage J8 -- report/outbox consistency

        # Repeated generate is idempotent -- no duplicate report/supersession.
        second = _generate(client, unique_user_id, trade_id)
        assert second.status_code == 200
        assert second.json()["price_path_generation_status"] == "PRICE_PATH_ALREADY_COMPLETE"
        report_count_final = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert report_count_final == 1


@pytest.mark.timeout(30)
class TestCompatibleReplaySurvivesProviderOutage:
    def test_second_generate_succeeds_even_when_every_provider_function_raises(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        """Stage J5 — a provider outage must never prevent a compatible
        (already-settled) outcome from being returned. A report is
        settled while the provider is still faked (either during
        _open_and_close's own /sell call, per Stage H2A, or by this
        first explicit /generate call — both are a legitimate settled
        outcome); every provider function is then monkeypatched to
        raise, and a second /generate call must still succeed by never
        touching the provider at all."""
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        first = _generate(client, unique_user_id, trade_id)
        assert first.status_code == 200
        assert first.json()["price_path_generation_status"] in (
            "PRICE_PATH_GENERATED", "PRICE_PATH_ALREADY_COMPLETE",
        )

        from services.postmortem import price_path_acquisition

        def _raises(*a, **k):
            raise AssertionError("provider function must not be called on a compatible-replay path")

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _raises)

        second = _generate(client, unique_user_id, trade_id)
        assert second.status_code == 200
        assert second.json()["price_path_generation_status"] == "PRICE_PATH_ALREADY_COMPLETE"


@pytest.mark.timeout(30)
class TestTransientProviderFailureMarksRetryable:
    def test_provider_exception_marks_outbox_failed_retryable_not_terminal(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        # _open_and_close's own /sell call ALSO synchronously attempts
        # price-path enhancement (Stage H2A) with whatever provider is
        # active at that moment. With the file's autouse fake still
        # active, that call would succeed and settle a COMPLETE report
        # during setup -- this explicit /generate call would then hit
        # the ALREADY_COMPLETE short-circuit and never touch the
        # (raising) provider at all, never exercising the failure this
        # test targets. Disabled only for setup, re-enabled with the
        # raising provider immediately before the explicit call, same
        # pattern as TestTrueConcurrentGenerate in
        # test_price_path_endpoint_lifecycle.py.
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        from services.postmortem import price_path_acquisition

        def _raises(*a, **k):
            raise price_path_acquisition.PriceProviderAcquisitionError("PROVIDER_FETCH_FAILED", "synthetic outage")

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _raises)

        pre_existing = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert pre_existing == 0, "setup itself must not have already generated the price-path report"

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_FAILED_RETRYABLE"

        outbox_status, error_code = pg_conn.execute(
            "SELECT status, last_error_code FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()
        assert outbox_status == "FAILED_RETRYABLE"
        assert error_code == "PROVIDER_FETCH_FAILED"

        report_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert report_count == 0  # no partial/fabricated evidence persisted
